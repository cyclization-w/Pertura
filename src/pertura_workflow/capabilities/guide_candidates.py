from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any

from pertura_core import CapabilityRunRequest, CapabilitySpec, DatasetContract, DiagnosticStatus

from pertura_workflow.capabilities.candidate_common import (
    blocked,
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


def run_guide_integrity(
    spec: CapabilitySpec,
    request: CapabilityRunRequest,
    contract: DatasetContract,
    staging: Path,
):
    counts_path = resolve_input(contract, request.parameters.get("guide_counts_path"), label="guide_counts_path")
    rna_path = resolve_input(contract, request.parameters.get("rna_barcodes_path"), label="rna_barcodes_path")
    map_path = resolve_input(contract, request.parameters.get("guide_map_path"), label="guide_map_path")
    barcodes, guides, _ = _read_count_matrix(counts_path, request.parameters.get("barcode_column"))
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
    )


def run_guide_nb_mixture(
    spec: CapabilitySpec,
    request: CapabilityRunRequest,
    contract: DatasetContract,
    staging: Path,
):
    max_memory_gb, _ = resource_budget(request.parameters)
    threshold = float(request.parameters.get("posterior_threshold", 0.90))
    if not 0.5 <= threshold < 1:
        raise ValueError("posterior_threshold must be in [0.5, 1)")
    counts_path = resolve_input(contract, request.parameters.get("guide_counts_path"), label="guide_counts_path")
    barcodes, guides, counts = _read_count_matrix(counts_path, request.parameters.get("barcode_column"))
    estimate_gb = len(barcodes) * len(guides) * 8 / 1024**3
    if estimate_gb > max_memory_gb:
        return blocked(
            spec,
            request,
            contract,
            f"posterior matrix estimate {estimate_gb:.3f} GB exceeds max_memory_gb={max_memory_gb}",
        )
    try:
        import pandas as pd
        from scipy import sparse
    except ModuleNotFoundError as exc:
        return blocked(
            spec,
            request,
            contract,
            f"guide assignment dependency is missing: {exc.name}",
            metadata={"setup_command": "pertura env setup python-science-v1"},
        )

    posteriors: list[list[float]] = [[0.0 for _ in guides] for _ in barcodes]
    fitted: dict[str, dict[str, float]] = {}
    for column, guide in enumerate(guides):
        vector = [row[column] for row in counts]
        posterior, parameters = _fit_nb_mixture(
            vector,
            max_iterations=int(request.parameters.get("max_iterations", 200)),
            tolerance=float(request.parameters.get("tolerance", 1e-6)),
        )
        fitted[guide] = parameters
        for row_index, value in enumerate(posterior):
            posteriors[row_index][column] = value

    assignments = []
    moi_counts: Counter[int] = Counter()
    for row_index, barcode in enumerate(barcodes):
        assigned = [
            guide
            for column, guide in enumerate(guides)
            if posteriors[row_index][column] >= threshold and counts[row_index][column] > 0
        ]
        moi_counts[len(assigned)] += 1
        assignments.append(
            {
                "raw_barcode": barcode,
                "normalized_barcode": _normalize_one(barcode),
                "assigned_guides": assigned,
                "assigned_guide_count": len(assigned),
                "classification": (
                    "no_guide" if not assigned else "singlet" if len(assigned) == 1 else "multi_guide"
                ),
            }
        )

    posterior_path = staging / "guide_posterior.npz"
    sparse.save_npz(posterior_path, sparse.csr_matrix(posteriors))
    row_path = staging / "guide_posterior_rows.parquet"
    column_path = staging / "guide_posterior_columns.parquet"
    pd.DataFrame(
        {
            "row_index": range(len(barcodes)),
            "raw_barcode": barcodes,
            "normalized_barcode": [_normalize_one(item) for item in barcodes],
        }
    ).to_parquet(row_path, index=False)
    pd.DataFrame({"column_index": range(len(guides)), "guide_id": guides}).to_parquet(column_path, index=False)
    assignment_path = write_json(
        staging,
        "guide_assignments.json",
        {
            "schema_version": "pertura-guide-assignment-nb-v1",
            "posterior_threshold": threshold,
            "seed": 1729,
            "max_iterations": int(request.parameters.get("max_iterations", 200)),
            "tolerance": float(request.parameters.get("tolerance", 1e-6)),
            "mixture_parameters": fitted,
            "assignments": assignments,
        },
    )
    caution = []
    if any(item.get("signal_weight", 0) >= 0.999 for item in fitted.values()):
        caution.append("one or more guide mixtures were nearly degenerate")
    return envelope(
        spec,
        request,
        contract,
        status=DiagnosticStatus.caution if caution else DiagnosticStatus.screen_passed,
        summary=f"Assigned {len(barcodes)} cells across {len(guides)} guides using NB mixture posteriors.",
        cautions=caution,
        metrics={
            "n_cells": len(barcodes),
            "n_guides": len(guides),
            "moi_counts": {str(key): value for key, value in sorted(moi_counts.items())},
            "posterior_threshold": threshold,
        },
        outputs=(posterior_path, row_path, column_path, assignment_path),
        metadata={"seed": 1729, "max_memory_gb": max_memory_gb},
    )


def run_guide_ambient(
    spec: CapabilitySpec,
    request: CapabilityRunRequest,
    contract: DatasetContract,
    staging: Path,
):
    raw_value = request.parameters.get("raw_guide_counts_path")
    if not raw_value:
        output = write_json(
            staging,
            "guide_ambient.json",
            {
                "schema_version": "pertura-guide-ambient-v1",
                "status": "unresolved",
                "reason": "raw droplets were not provided",
            },
        )
        return envelope(
            spec,
            request,
            contract,
            status=DiagnosticStatus.unresolved,
            summary="Ambient guide contamination is unresolved because raw droplets were not provided.",
            cautions=("raw droplets are required for ambient guide estimation",),
            metrics={"ambient_status": "unresolved"},
            outputs=(output,),
        )
    raw_path = resolve_input(contract, raw_value, label="raw_guide_counts_path")
    filtered_path = resolve_input(
        contract,
        request.parameters.get("filtered_guide_counts_path") or request.parameters.get("guide_counts_path"),
        label="filtered_guide_counts_path",
    )
    raw_barcodes, raw_guides, raw_counts = _read_count_matrix(raw_path, request.parameters.get("barcode_column"))
    filtered_barcodes, filtered_guides, _ = _read_count_matrix(filtered_path, request.parameters.get("barcode_column"))
    if raw_guides != filtered_guides:
        return blocked(spec, request, contract, "raw and filtered guide matrices have different guide columns")
    cell_set = {_normalize_one(item) for item in filtered_barcodes}
    empty = [
        row for barcode, row in zip(raw_barcodes, raw_counts)
        if _normalize_one(barcode) not in cell_set
    ]
    cautions: list[str] = []
    if not empty:
        cautions.append("raw matrix contained no identifiable empty droplets")
    mean = {
        guide: (sum(row[index] for row in empty) / len(empty) if empty else None)
        for index, guide in enumerate(raw_guides)
    }
    payload = {
        "schema_version": "pertura-guide-ambient-v1",
        "status": "estimated" if empty else "unresolved",
        "n_empty_droplets": len(empty),
        "mean_guide_umi": mean,
        "counts_modified": False,
        "cautions": cautions,
    }
    output = write_json(staging, "guide_ambient.json", payload)
    status = DiagnosticStatus.caution if cautions else DiagnosticStatus.screen_passed
    return envelope(
        spec,
        request,
        contract,
        status=status,
        summary=f"Estimated ambient guide background from {len(empty)} non-cell droplets.",
        cautions=cautions,
        metrics={"ambient_status": payload["status"], "n_empty_droplets": len(empty)},
        outputs=(output,),
    )


def run_moi_doublet(
    spec: CapabilitySpec,
    request: CapabilityRunRequest,
    contract: DatasetContract,
    staging: Path,
):
    assignment_path = resolve_input(
        contract,
        request.parameters.get("assignment_path"),
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

                data = ad.read_h5ad(h5ad_path)
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
    assignment_path = resolve_input(contract, request.parameters.get("assignment_path"), label="assignment_path")
    moi_path = resolve_input(contract, request.parameters.get("moi_doublet_path"), label="moi_doublet_path")
    assignments_payload = json.loads(assignment_path.read_text(encoding="utf-8"))
    assignments = assignments_payload.get("assignments")
    if assignments is None and isinstance(assignments_payload, list):
        assignments = assignments_payload
    assignments = list(assignments or [])
    moi_payload = json.loads(moi_path.read_text(encoding="utf-8"))
    predicted_doublet = dict(moi_payload.get("predicted_doublet") or {})
    design_moi = str(request.parameters.get("design_moi") or "low").lower()
    high_moi = design_moi in {"high", "multi", "combinatorial", "pooled_high"}
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
        if not high_moi and guide_count > 1:
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
            "high_moi_multi_guide_preserved": high_moi,
        },
    )
    caution = []
    if not predicted_doublet:
        caution.append("doublet exclusion was unresolved")
    return envelope(
        spec,
        request,
        contract,
        status=DiagnosticStatus.caution if caution else DiagnosticStatus.screen_passed,
        summary=f"Retained {retained_count} of {len(rows)} cells for a {design_moi}-MOI design.",
        cautions=caution,
        metrics={
            "n_cells": len(rows),
            "n_retained": retained_count,
            "reason_counts": dict(reason_counts),
            "design_moi": design_moi,
        },
        outputs=(manifest_path, summary_path),
        metadata={"retained_cell_manifest_hash_bound": True},
    )
