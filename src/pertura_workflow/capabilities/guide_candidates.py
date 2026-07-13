from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any

from pertura_core import CapabilityRunRequest, CapabilitySpec, DatasetContract, DiagnosticStatus
from pertura_core.hashing import path_sha256

from pertura_workflow.capabilities.candidate_common import (
    blocked,
    dependency_results,
    envelope,
    read_rows,
    resolve_input,
    resource_budget,
    write_json,
)
from pertura_workflow.capabilities.guide_assignment import (
    _fit_nb_mixture,
    _normalize_barcodes,
    _normalize_one,
    _read_barcodes,
    _read_count_matrix,
    _read_guide_map,
    _reverse_complement,
)
from pertura_workflow.capabilities.guide_counts import open_guide_count_source


def run_guide_integrity(
    spec: CapabilitySpec,
    request: CapabilityRunRequest,
    contract: DatasetContract,
    staging: Path,
):
    budget = resource_budget(request.parameters)
    counts_path = resolve_input(contract, request.parameters.get("guide_counts_path"), label="guide_counts_path")
    rna_path = resolve_input(contract, request.parameters.get("rna_barcodes_path"), label="rna_barcodes_path")
    map_path = resolve_input(contract, request.parameters.get("guide_map_path"), label="guide_map_path")
    try:
        source = open_guide_count_source(
            counts_path,
            barcode_column=request.parameters.get("barcode_column"),
            row_manifest_path=request.parameters.get("row_manifest_path"),
            column_manifest_path=request.parameters.get("column_manifest_path"),
            modality=request.parameters.get("modality"),
            layer=request.parameters.get("layer"),
            max_memory_gb=budget.max_memory_gb,
            chunk_rows=budget.chunk_rows,
        )
    except (MemoryError, ValueError) as exc:
        return blocked(spec, request, contract, str(exc))
    try:
        barcodes, guides = list(source.cell_ids), list(source.guide_ids)
    finally:
        source.close()
    rna_barcodes = _read_barcodes(rna_path, request.parameters.get("rna_barcode_column"))
    guide_map, map_issues = _read_guide_map(map_path)
    normalized_rna, rna_collision = _normalize_barcodes(rna_barcodes)
    normalized_guide, guide_collision = _normalize_barcodes(barcodes)
    reverse = [_reverse_complement(item) for item in normalized_guide]
    direct_overlap = len(set(normalized_rna) & set(normalized_guide))
    reverse_overlap = len(set(normalized_rna) & set(reverse))
    orientation = "reverse_complement" if reverse_overlap > direct_overlap else "forward"
    missing_guides = sorted(set(guides) - set(guide_map))
    blockers = list(map_issues)
    cautions: list[str] = []
    if rna_collision or guide_collision:
        blockers.append("barcode suffix removal would create collisions")
    if max(direct_overlap, reverse_overlap) == 0:
        blockers.append("RNA and guide barcode sets do not overlap in either orientation")
    if missing_guides:
        blockers.append(f"guide map is missing {len(missing_guides)} observed guides")
    if orientation == "reverse_complement":
        cautions.append("guide barcodes match after reverse complement")
    payload = {
        "schema_version": "pertura-guide-integrity-v1",
        "orientation": orientation,
        "direct_overlap": direct_overlap,
        "reverse_complement_overlap": reverse_overlap,
        "suffix_collision": rna_collision or guide_collision,
        "n_rna_barcodes": len(rna_barcodes),
        "n_guide_barcodes": len(barcodes),
        "missing_guides": missing_guides,
        "guide_map_issues": map_issues,
        "blockers": blockers,
        "cautions": cautions,
    }
    output = write_json(staging, "guide_integrity.json", payload)
    status = DiagnosticStatus.blocked if blockers else (
        DiagnosticStatus.caution if cautions else DiagnosticStatus.screen_passed
    )
    return envelope(
        spec,
        request,
        contract,
        status=status,
        summary=f"Guide integrity selected {orientation} orientation with {max(direct_overlap, reverse_overlap)} overlapping barcodes.",
        blockers=blockers,
        cautions=cautions,
        metrics={
            "orientation": orientation,
            "selected_overlap": max(direct_overlap, reverse_overlap),
            "suffix_collision": rna_collision or guide_collision,
            "missing_guide_count": len(missing_guides),
        },
        outputs=(output,),
        metadata={"guide_count_source": "sparse_or_backed"},
    )


def run_guide_nb_mixture(
    spec: CapabilitySpec,
    request: CapabilityRunRequest,
    contract: DatasetContract,
    staging: Path,
):
    budget = resource_budget(request.parameters)
    threshold = float(request.parameters.get("posterior_threshold", 0.90))
    if not 0.5 <= threshold < 1:
        raise ValueError("posterior_threshold must be in [0.5, 1)")
    counts_path = resolve_input(contract, request.parameters.get("guide_counts_path"), label="guide_counts_path")
    try:
        import numpy as np
        import pandas as pd
        from scipy import sparse
        source = open_guide_count_source(
            counts_path,
            barcode_column=request.parameters.get("barcode_column"),
            row_manifest_path=request.parameters.get("row_manifest_path"),
            column_manifest_path=request.parameters.get("column_manifest_path"),
            modality=request.parameters.get("modality"),
            layer=request.parameters.get("layer"),
            max_memory_gb=budget.max_memory_gb,
            chunk_rows=budget.chunk_rows,
        )
    except ModuleNotFoundError as exc:
        return blocked(
            spec, request, contract,
            f"guide assignment dependency is missing: {exc.name}",
            metadata={"setup_command": "pertura env setup python-science-v1"},
        )
    except (MemoryError, ValueError) as exc:
        return blocked(spec, request, contract, str(exc))

    barcodes, guides = list(source.cell_ids), list(source.guide_ids)
    working_bytes = source.estimated_peak_memory(chunk_rows=budget.chunk_rows) + len(barcodes) * 24
    if working_bytes > budget.max_bytes:
        source.close()
        return blocked(
            spec, request, contract,
            f"guide assignment working-set estimate {working_bytes / 1024**3:.3f} GB exceeds max_memory_gb={budget.max_memory_gb}",
        )
    fitted: dict[str, dict[str, float]] = {}
    posterior_rows: list[int] = []
    posterior_columns: list[int] = []
    posterior_values: list[float] = []
    assigned_by_row: list[list[str]] = [[] for _ in barcodes]
    try:
        for column, guide in enumerate(guides):
            vector = source.column_values(column, chunk_rows=budget.chunk_rows)
            posterior, parameters = _fit_nb_mixture(
                vector.tolist(),
                max_iterations=int(request.parameters.get("max_iterations", 200)),
                tolerance=float(request.parameters.get("tolerance", 1e-6)),
            )
            fitted[guide] = parameters
            nonzero = np.flatnonzero(vector > 0)
            for row_index in nonzero:
                value = float(posterior[int(row_index)])
                posterior_rows.append(int(row_index))
                posterior_columns.append(column)
                posterior_values.append(value)
                if value >= threshold:
                    assigned_by_row[int(row_index)].append(guide)
    finally:
        source.close()

    posterior_matrix = sparse.csr_matrix(
        (posterior_values, (posterior_rows, posterior_columns)),
        shape=(len(barcodes), len(guides)),
        dtype=float,
    )
    posterior_bytes = (
        posterior_matrix.data.nbytes
        + posterior_matrix.indices.nbytes
        + posterior_matrix.indptr.nbytes
    )
    if posterior_bytes + len(barcodes) * 16 > budget.max_bytes:
        return blocked(spec, request, contract, "sparse posterior output exceeds resource budget")

    assignments = []
    moi_counts: Counter[int] = Counter()
    for row_index, barcode in enumerate(barcodes):
        assigned = assigned_by_row[row_index]
        moi_counts[len(assigned)] += 1
        assignments.append({
            "raw_barcode": barcode,
            "normalized_barcode": _normalize_one(barcode),
            "assigned_guides": assigned,
            "assigned_guide_count": len(assigned),
            "classification": (
                "no_guide" if not assigned else "singlet" if len(assigned) == 1 else "multi_guide"
            ),
        })

    posterior_path = staging / "guide_posterior.npz"
    sparse.save_npz(posterior_path, posterior_matrix)
    row_path = staging / "guide_posterior_rows.parquet"
    column_path = staging / "guide_posterior_columns.parquet"
    pd.DataFrame({
        "row_index": range(len(barcodes)),
        "raw_barcode": barcodes,
        "normalized_barcode": [_normalize_one(item) for item in barcodes],
    }).to_parquet(row_path, index=False)
    pd.DataFrame({"column_index": range(len(guides)), "guide_id": guides}).to_parquet(column_path, index=False)
    assignment_path = write_json(staging, "guide_assignments.json", {
        "schema_version": "pertura-guide-assignment-nb-v1",
        "posterior_threshold": threshold,
        "seed": 1729,
        "max_iterations": int(request.parameters.get("max_iterations", 200)),
        "tolerance": float(request.parameters.get("tolerance", 1e-6)),
        "mixture_parameters": fitted,
        "assignments": assignments,
    })
    caution = []
    if any(item.get("signal_weight", 0) >= 0.999 for item in fitted.values()):
        caution.append("one or more guide mixtures were nearly degenerate")
    return envelope(
        spec, request, contract,
        status=DiagnosticStatus.caution if caution else DiagnosticStatus.screen_passed,
        summary=f"Assigned {len(barcodes)} cells across {len(guides)} guides using sparse NB mixture posteriors.",
        cautions=caution,
        metrics={
            "n_cells": len(barcodes),
            "n_guides": len(guides),
            "posterior_nnz": int(posterior_matrix.nnz),
            "moi_counts": {str(key): value for key, value in sorted(moi_counts.items())},
            "posterior_threshold": threshold,
        },
        outputs=(posterior_path, row_path, column_path, assignment_path),
        metadata={
            "seed": 1729,
            "max_memory_gb": budget.max_memory_gb,
            "posterior_storage": "csr_nonzero_candidates",
        },
    )


def run_guide_ambient(
    spec: CapabilitySpec,
    request: CapabilityRunRequest,
    contract: DatasetContract,
    staging: Path,
):
    raw_value = request.parameters.get("raw_guide_counts_path")
    if not raw_value:
        output = write_json(staging, "guide_ambient.json", {
            "schema_version": "pertura-guide-ambient-v1",
            "status": "unresolved",
            "reason": "raw droplets were not provided",
        })
        return envelope(
            spec, request, contract,
            status=DiagnosticStatus.unresolved,
            summary="Ambient guide contamination is unresolved because raw droplets were not provided.",
            cautions=("raw droplets are required for ambient guide estimation",),
            metrics={"ambient_status": "unresolved"},
            outputs=(output,),
        )
    budget = resource_budget(request.parameters)
    raw_path = resolve_input(contract, raw_value, label="raw_guide_counts_path")
    filtered_path = resolve_input(
        contract,
        request.parameters.get("filtered_guide_counts_path") or request.parameters.get("guide_counts_path"),
        label="filtered_guide_counts_path",
    )
    kwargs = {
        "barcode_column": request.parameters.get("barcode_column"),
        "row_manifest_path": request.parameters.get("row_manifest_path"),
        "column_manifest_path": request.parameters.get("column_manifest_path"),
        "modality": request.parameters.get("modality"),
        "layer": request.parameters.get("layer"),
        "max_memory_gb": budget.max_memory_gb,
        "chunk_rows": budget.chunk_rows,
    }
    try:
        raw_source = open_guide_count_source(raw_path, **kwargs)
        filtered_source = open_guide_count_source(filtered_path, **kwargs)
    except (MemoryError, ValueError) as exc:
        return blocked(spec, request, contract, str(exc))
    try:
        if raw_source.guide_ids != filtered_source.guide_ids:
            return blocked(spec, request, contract, "raw and filtered guide matrices have different guide columns")
        cell_set = {_normalize_one(item) for item in filtered_source.cell_ids}
        import numpy as np
        total = np.zeros(len(raw_source.guide_ids), dtype=float)
        n_empty = 0
        for start, chunk in raw_source.iter_row_chunks(budget.chunk_rows):
            labels = raw_source.cell_ids[start:start + chunk.shape[0]]
            mask = np.asarray([_normalize_one(item) not in cell_set for item in labels], dtype=bool)
            if mask.any():
                total += np.asarray(chunk[mask].sum(axis=0)).ravel()
                n_empty += int(mask.sum())
        mean = {
            guide: (float(total[index] / n_empty) if n_empty else None)
            for index, guide in enumerate(raw_source.guide_ids)
        }
    finally:
        raw_source.close()
        filtered_source.close()
    cautions: list[str] = []
    if not n_empty:
        cautions.append("raw matrix contained no identifiable empty droplets")
    payload = {
        "schema_version": "pertura-guide-ambient-v1",
        "status": "estimated" if n_empty else "unresolved",
        "n_empty_droplets": n_empty,
        "mean_guide_umi": mean,
        "counts_modified": False,
        "cautions": cautions,
    }
    output = write_json(staging, "guide_ambient.json", payload)
    return envelope(
        spec, request, contract,
        status=DiagnosticStatus.caution if cautions else DiagnosticStatus.screen_passed,
        summary=f"Estimated ambient guide background from {n_empty} non-cell droplets.",
        cautions=cautions,
        metrics={"ambient_status": payload["status"], "n_empty_droplets": n_empty},
        outputs=(output,),
        metadata={"guide_count_source": "sparse_or_backed"},
    )


def run_moi_doublet(
    spec: CapabilitySpec,
    request: CapabilityRunRequest,
    contract: DatasetContract,
    staging: Path,
):
    dependencies = dependency_results(staging)
    assignment_path = _parameter_or_dependency_output(
        contract,
        request.parameters.get("assignment_path"),
        dependencies,
        capability_id="guide.assignment.nb_mixture.v1",
        names=("guide_assignments.json",),
        label="assignment_path",
    )
    assignment_payload = json.loads(assignment_path.read_text(encoding="utf-8"))
    assignments = assignment_payload.get("assignments")
    if assignments is None and isinstance(assignment_payload, list):
        assignments = assignment_payload
    assignments = list(assignments or [])
    moi = Counter(int(item.get("assigned_guide_count", 0)) for item in assignments)
    multi_guide = {
        str(item.get("raw_barcode")): int(item.get("assigned_guide_count", 0)) > 1
        for item in assignments
    }

    doublet_scores: dict[str, float] = {}
    threshold = float(request.parameters.get("doublet_threshold", 0.25))
    metadata_value = request.parameters.get("metadata_path")
    if metadata_value:
        metadata_path = resolve_input(contract, metadata_value, label="metadata_path")
        fields, rows = read_rows(metadata_path)
        barcode_column = str(request.parameters.get("metadata_barcode_column") or "barcode")
        score_column = str(request.parameters.get("doublet_score_column") or "scrublet_score")
        if barcode_column in fields and score_column in fields:
            doublet_scores = {
                row[barcode_column]: float(row[score_column])
                for row in rows
                if row.get(barcode_column) and row.get(score_column) not in {"", None}
            }

    cautions: list[str] = []
    if not doublet_scores:
        h5ad_value = request.parameters.get("h5ad_path")
        if h5ad_value:
            h5ad_path = resolve_input(contract, h5ad_value, label="h5ad_path")
            try:
                import anndata as ad
                import scanpy as sc

                budget = resource_budget(request.parameters)
                inspection = ad.read_h5ad(h5ad_path, backed="r")
                try:
                    estimated = budget.dense_bytes(
                        int(inspection.n_obs), int(inspection.n_vars), arrays=3
                    )
                    if estimated > budget.max_bytes:
                        return blocked(
                            spec,
                            request,
                            contract,
                            f"Scrublet working-set estimate {estimated / 1024**3:.3f} GB exceeds max_memory_gb={budget.max_memory_gb}",
                        )
                    data = inspection.to_memory()
                finally:
                    if getattr(inspection, "file", None):
                        inspection.file.close()
                sc.pp.scrublet(data, random_state=1729)
                doublet_scores = {
                    str(cell): float(score)
                    for cell, score in zip(data.obs_names, data.obs["doublet_score"])
                }
            except ModuleNotFoundError:
                cautions.append("Scrublet environment is unavailable; doublet status is unresolved")
        else:
            cautions.append("no doublet scores or expression input were provided")
    predicted = {
        cell: score >= threshold for cell, score in doublet_scores.items()
    }
    payload = {
        "schema_version": "pertura-moi-doublet-v1",
        "moi_counts": {str(key): value for key, value in sorted(moi.items())},
        "multi_guide": multi_guide,
        "doublet_scores": doublet_scores,
        "predicted_doublet": predicted,
        "doublet_threshold": threshold,
        "multi_guide_is_doublet": False,
        "cautions": cautions,
    }
    output = write_json(staging, "moi_doublet.json", payload)
    return envelope(
        spec,
        request,
        contract,
        status=DiagnosticStatus.caution if cautions else DiagnosticStatus.screen_passed,
        summary=f"Profiled MOI for {len(assignments)} cells; doublet status is kept separate from multi-guide status.",
        cautions=cautions,
        metrics={
            "moi_counts": payload["moi_counts"],
            "n_multi_guide": sum(multi_guide.values()),
            "n_predicted_doublet": sum(predicted.values()),
            "doublet_status": "estimated" if doublet_scores else "unresolved",
        },
        outputs=(output,),
    )


def run_retained_cells(
    spec: CapabilitySpec,
    request: CapabilityRunRequest,
    contract: DatasetContract,
    staging: Path,
):
    dependencies = dependency_results(staging)
    assignment_path = _parameter_or_dependency_output(
        contract,
        request.parameters.get("assignment_path"),
        dependencies,
        capability_id="guide.assignment.nb_mixture.v1",
        names=("guide_assignments.json",),
        label="assignment_path",
    )
    moi_path = _parameter_or_dependency_output(
        contract,
        request.parameters.get("moi_doublet_path"),
        dependencies,
        capability_id="screen.moi_doublet.v1",
        names=("moi_doublet.json",),
        label="moi_doublet_path",
    )
    assignments_payload = json.loads(assignment_path.read_text(encoding="utf-8"))
    assignments = assignments_payload.get("assignments")
    if assignments is None and isinstance(assignments_payload, list):
        assignments = assignments_payload
    assignments = list(assignments or [])
    moi_payload = json.loads(moi_path.read_text(encoding="utf-8"))
    predicted_doublet = dict(moi_payload.get("predicted_doublet") or {})
    design_moi = _confirmed_design_fact(contract, "design_moi", {"low", "high"})
    guide_design = _confirmed_design_fact(
        contract,
        "guide_design",
        {"single", "combinatorial", "mixed"},
    )
    preserve_multi_guide = design_moi == "high" or guide_design in {
        "combinatorial",
        "mixed",
    }
    rows = []
    retained_count = 0
    reason_counts: Counter[str] = Counter()
    for item in assignments:
        barcode = str(item.get("raw_barcode") or "")
        guide_count = int(item.get("assigned_guide_count", 0))
        reasons: list[str] = []
        if predicted_doublet.get(barcode, False):
            reasons.append("transcriptomic_doublet")
        if guide_count == 0:
            reasons.append("no_high_posterior_guide")
        if design_moi == "low" and guide_design == "single" and guide_count > 1:
            reasons.append("multi_guide_low_moi_design")
        retained = not reasons
        retained_count += int(retained)
        reason_counts.update(reasons)
        rows.append(
            {
                "raw_barcode": barcode,
                "retained": retained,
                "assigned_guide_count": guide_count,
                "multi_guide": guide_count > 1,
                "transcriptomic_doublet": bool(predicted_doublet.get(barcode, False)),
                "exclusion_reasons": ";".join(reasons),
            }
        )
    manifest_path = staging / "retained_cells.csv"
    with manifest_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]) if rows else [
            "raw_barcode", "retained", "assigned_guide_count", "multi_guide",
            "transcriptomic_doublet", "exclusion_reasons",
        ])
        writer.writeheader()
        writer.writerows(rows)
    summary_path = write_json(
        staging,
        "retained_cells_summary.json",
        {
            "schema_version": "pertura-retained-cells-v1",
            "design_moi": design_moi,
            "n_cells": len(rows),
            "n_retained": retained_count,
            "reason_counts": dict(reason_counts),
            "guide_design": guide_design,
            "high_moi_multi_guide_preserved": preserve_multi_guide,
        },
    )
    caution = []
    if not predicted_doublet:
        caution.append("doublet exclusion was unresolved")
    if design_moi == "unknown":
        caution.append("design MOI is unconfirmed; multi-guide cells were not excluded by MOI")
    blockers = []
    if retained_count == 0:
        blockers.append("retained-cell manifest contains no retained cells")
    status = (
        DiagnosticStatus.blocked
        if blockers
        else DiagnosticStatus.caution
        if caution
        else DiagnosticStatus.screen_passed
    )
    return envelope(
        spec,
        request,
        contract,
        status=status,
        summary=f"Retained {retained_count} of {len(rows)} cells for a {design_moi}-MOI design.",
        blockers=blockers,
        cautions=caution,
        metrics={
            "n_cells": len(rows),
            "n_retained": retained_count,
            "reason_counts": dict(reason_counts),
            "design_moi": design_moi,
            "guide_design": guide_design,
        },
        outputs=(manifest_path, summary_path),
        metadata={
            "retained_cell_manifest_hash_bound": True,
            "design_moi_basis": (
                "contract_confirmation" if design_moi != "unknown" else "unresolved"
            ),
        },
    )


def _parameter_or_dependency_output(
    contract: DatasetContract,
    value: Any,
    dependencies: list[dict[str, Any]],
    *,
    capability_id: str,
    names: tuple[str, ...],
    label: str,
) -> Path:
    if value not in (None, ""):
        resolved = resolve_input(contract, value, label=label)
        assert resolved is not None
        from pertura_workflow.capabilities.execution_context import execution_context
        if (
            not dependencies
            and not execution_context().get("enforce_dependency_consumption")
        ):
            return resolved
        bound = []
        for result in dependencies:
            if result.get("capability_id") != capability_id:
                continue
            for output in result.get("local_output_paths") or ():
                candidate = Path(output).resolve()
                expected_hash = (result.get("output_hashes") or {}).get(candidate.name)
                if (
                    candidate.name == resolved.name
                    and expected_hash
                    and path_sha256(resolved) == expected_hash
                ):
                    bound.append(candidate)
        if len(bound) != 1:
            raise ValueError(
                f"explicit {label} must resolve to exactly one {capability_id} output"
            )
        return resolved
    matches = []
    for result in dependencies:
        if result.get("capability_id") != capability_id:
            continue
        for output in result.get("local_output_paths") or ():
            candidate = Path(output)
            if candidate.name in names and candidate.is_file():
                matches.append(candidate)
    if len(matches) != 1:
        raise ValueError(
            f"{capability_id} must expose exactly one of {', '.join(names)}"
        )
    return matches[0]


def _confirmed_design_fact(
    contract: DatasetContract,
    field: str,
    allowed: set[str],
) -> str:
    payload = contract.identity_fields.get(field) or {}
    if str(payload.get("status") or "") != "confirmed":
        return "unknown"
    value = str(payload.get("value") or "").strip().lower()
    return value if value in allowed else "unknown"
