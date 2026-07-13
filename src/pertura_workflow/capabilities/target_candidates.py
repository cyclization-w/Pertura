from __future__ import annotations

import json
import random
from collections import Counter
from importlib import metadata as package_metadata
from pathlib import Path
from typing import Any

from pertura_core import CapabilityRunRequest, CapabilitySpec, DatasetContract, DiagnosticStatus

from pertura_workflow.capabilities.candidate_common import (
    blocked,
    dependency_results,
    envelope,
    resolve_input,
    write_json,
)
from pertura_workflow.environment import doctor_environment
from pertura_workflow.capabilities.dependency_inputs import retained_cells_for_request
from pertura_workflow.capabilities.candidate_common import resource_budget
from pertura_workflow.capabilities.target_reliability import (
    _axis_overlap,
    _bootstrap_effect,
    _detection,
    _direction_supported,
    _effect,
    _heterogeneity,
    _load_profile,
    _read_expression,
    _read_metadata,
    _signature_efficacy,
    _stable_seed,
)


def run_mixscape_responder(
    spec: CapabilitySpec,
    request: CapabilityRunRequest,
    contract: DatasetContract,
    staging: Path,
):
    environment = doctor_environment("perturbseq-python-v1")
    if not environment["ok"]:
        return blocked(
            spec,
            request,
            contract,
            *environment["problems"],
            metadata={"setup_command": "pertura env setup perturbseq-python-v1"},
        )
    try:
        import anndata as ad
        import pandas as pd
        import pertpy as pt
    except ModuleNotFoundError as exc:
        return blocked(
            spec,
            request,
            contract,
            f"Mixscape dependency is missing: {exc.name}",
            metadata={"setup_command": "pertura env setup perturbseq-python-v1"},
        )
    h5ad_path = resolve_input(contract, request.parameters.get("h5ad_path"), label="h5ad_path")
    pert_key = str(request.parameters.get("pert_key") or "")
    control = str(request.parameters.get("control") or "")
    if not pert_key or not control:
        return blocked(spec, request, contract, "pert_key and control are required for Mixscape")
    budget = resource_budget(request.parameters)
    retained = retained_cells_for_request(staging, request, required=True)
    inspection = ad.read_h5ad(h5ad_path, backed="r")
    try:
        retained_mask = (
            inspection.obs_names.astype(str).isin(retained)
            if retained is not None
            else [True] * int(inspection.n_obs)
        )
        retained_count = int(retained_mask.sum())
        estimated = budget.dense_bytes(
            retained_count,
            int(inspection.n_vars),
            arrays=3,
            itemsize=8,
        )
        if estimated > budget.max_bytes:
            return blocked(
                spec,
                request,
                contract,
                f"Mixscape working-set estimate {estimated / 1024**3:.3f} GB exceeds max_memory_gb={budget.max_memory_gb}",
            )
    finally:
        if getattr(inspection, "file", None):
            inspection.file.close()
    inspection = ad.read_h5ad(h5ad_path, backed="r")
    try:
        selected = (
            inspection.obs_names.astype(str).isin(retained)
            if retained is not None
            else [True] * int(inspection.n_obs)
        )
        data = inspection[selected].to_memory()
    finally:
        if getattr(inspection, "file", None):
            inspection.file.close()
    if data.n_obs == 0:
        return blocked(spec, request, contract, "retained-cell manifest has no overlap with Mixscape input")
    if pert_key not in data.obs.columns:
        return blocked(spec, request, contract, f"perturbation column is missing: {pert_key}")
    split_by = request.parameters.get("split_by")
    new_class_name = str(request.parameters.get("new_class_name") or "mixscape_class")
    mixscape = pt.tl.Mixscape()
    mixscape.perturbation_signature(
        data,
        pert_key=pert_key,
        control=control,
        ref_selection_mode=str(request.parameters.get("ref_selection_mode") or "nn"),
        split_by=str(split_by) if split_by else None,
        n_neighbors=int(request.parameters.get("n_neighbors", 20)),
        use_rep=str(request.parameters.get("use_rep") or "X_pca"),
        n_dims=int(request.parameters.get("n_dims", 15)),
        copy=False,
    )
    mixscape.mixscape(
        data,
        pert_key=pert_key,
        control=control,
        new_class_name=new_class_name,
        layer="X_pert",
        min_de_genes=int(request.parameters.get("min_de_genes", 5)),
        logfc_threshold=float(request.parameters.get("logfc_threshold", 0.25)),
        iter_num=int(request.parameters.get("iter_num", 10)),
        scale=bool(request.parameters.get("scale", True)),
        split_by=str(split_by) if split_by else None,
        pval_cutoff=float(request.parameters.get("pval_cutoff", 0.05)),
        perturbation_type=str(request.parameters.get("perturbation_type") or "KO"),
        random_state=1729,
    )
    if new_class_name not in data.obs.columns:
        alternatives = [name for name in data.obs.columns if name.startswith(new_class_name)]
        if not alternatives:
            return blocked(spec, request, contract, "Mixscape did not produce the requested class column")
        class_column = alternatives[0]
    else:
        class_column = new_class_name
    labels = data.obs[class_column].astype(str)
    control_mask = data.obs[pert_key].astype(str) == control
    candidate_cells = ~control_mask
    candidate_labels = labels[candidate_cells]
    lower = candidate_labels.str.lower()
    responder_mask = lower.str.contains("ko|responder|perturbed", regex=True)
    escape_mask = lower.str.contains("np|escape|non.perturbed", regex=True)
    table = pd.DataFrame(
        {
            "cell_id": data.obs_names.astype(str),
            "perturbation": data.obs[pert_key].astype(str).to_numpy(),
            "mixscape_class": labels.to_numpy(),
            "is_control": control_mask.to_numpy(),
        }
    )
    score_columns = [
        name for name in data.obs.columns
        if "mixscape" in name.lower() and "score" in name.lower()
    ]
    if score_columns:
        table["perturbation_score"] = data.obs[score_columns[0]].to_numpy()
    cells_path = staging / "mixscape_cells.parquet"
    table.to_parquet(cells_path, index=False)
    summary = {
        "schema_version": "pertura-mixscape-responder-v1",
        "class_column": class_column,
        "class_counts": dict(Counter(candidate_labels)),
        "n_candidate_cells": int(candidate_cells.sum()),
        "responder_fraction": float(responder_mask.mean()) if len(responder_mask) else None,
        "escape_fraction": float(escape_mask.mean()) if len(escape_mask) else None,
        "parameters": {
            "pert_key": pert_key,
            "control": control,
            "split_by": split_by,
            "seed": 1729,
            "signature_layer": "X_pert",
        },
        "package_versions": {
            "pertpy": package_metadata.version("pertpy"),
            "anndata": package_metadata.version("anndata"),
        },
    }
    summary_path = write_json(staging, "mixscape_summary.json", summary)
    caution = []
    if summary["responder_fraction"] is None:
        caution.append("Mixscape produced no non-control candidate cells")
    return envelope(
        spec,
        request,
        contract,
        status=DiagnosticStatus.caution if caution else DiagnosticStatus.screen_passed,
        summary=f"Mixscape classified {summary['n_candidate_cells']} non-control cells.",
        cautions=caution,
        metrics={
            "n_candidate_cells": summary["n_candidate_cells"],
            "responder_fraction": summary["responder_fraction"],
            "escape_fraction": summary["escape_fraction"],
        },
        outputs=(cells_path, summary_path),
        metadata={"method": "pertpy_mixscape", "seed": 1729},
    )


def run_guide_efficacy(
    spec: CapabilitySpec,
    request: CapabilityRunRequest,
    contract: DatasetContract,
    staging: Path,
):
    expression_path = resolve_input(
        contract,
        request.parameters.get("expression_path"),
        label="expression_path",
    )
    metadata_path = resolve_input(
        contract,
        request.parameters.get("metadata_path"),
        label="metadata_path",
    )
    target_uid = str(request.parameters.get("target_uid") or "")
    control_uid = str(request.parameters.get("control_uid") or "")
    target_gene = str(request.parameters.get("target_gene") or "")
    if not target_uid or not control_uid or not target_gene:
        return blocked(spec, request, contract, "target_uid, control_uid and target_gene are required")
    condition_column = str(request.parameters.get("condition_column") or "perturbation_uid")
    cell_column = str(request.parameters.get("cell_column") or "cell_id")
    guide_column = str(request.parameters.get("guide_column") or "guide")
    replicate_column = str(request.parameters.get("replicate_column") or "replicate")
    batch_column = str(request.parameters.get("batch_column") or "batch")
    expected = str(request.parameters.get("expected_direction") or "down").lower()
    layer_scale = str(request.parameters.get("layer_scale") or "log_normalized")
    profile_name = str(request.parameters.get("profile") or "dev_unvalidated_v0")
    profile = _load_profile(profile_name)
    retained = retained_cells_for_request(staging, request, required=True)
    expression = _read_expression(expression_path, cell_column)
    metadata = {
        cell: row
        for cell, row in _read_metadata(metadata_path, cell_column).items()
        if retained is None or cell in retained
    }
    if target_gene not in expression["genes"]:
        return blocked(spec, request, contract, f"target gene is absent: {target_gene}")
    target_cells = [
        cell for cell, row in metadata.items()
        if row.get(condition_column) == target_uid and cell in expression["rows"]
    ]
    control_cells = [
        cell for cell, row in metadata.items()
        if row.get(condition_column) == control_uid and cell in expression["rows"]
    ]
    target_values = [expression["rows"][cell][target_gene] for cell in target_cells]
    control_values = [expression["rows"][cell][target_gene] for cell in control_cells]
    pooled_effect = _effect(target_values, control_values, layer_scale)
    guide_groups: dict[str, list[str]] = {}
    for cell in target_cells:
        guide = metadata[cell].get(guide_column, "")
        if guide:
            guide_groups.setdefault(guide, []).append(cell)
    guide_results = {}
    for guide, cells in sorted(guide_groups.items()):
        values = [expression["rows"][cell][target_gene] for cell in cells]
        effect = _effect(values, control_values, layer_scale)
        guide_results[guide] = {
            "n_cells": len(cells),
            "effect": effect,
            "bootstrap_ci": _bootstrap_effect(
                values,
                control_values,
                layer_scale,
                int(request.parameters.get("guide_bootstrap_iterations", 250)),
                _stable_seed(guide),
            ),
            "direction_supported": _direction_supported(
                effect,
                expected,
                profile["minimum_abs_effect"],
            ),
        }
    eligible = [
        value for value in guide_results.values()
        if value["n_cells"] >= profile["minimum_cells_per_guide"]
    ]
    concordance = (
        sum(item["direction_supported"] for item in eligible) / len(eligible)
        if eligible
        else None
    )
    replicate_levels = sorted(
        {
            metadata[cell].get(replicate_column, "")
            for cell in target_cells + control_cells
            if metadata[cell].get(replicate_column, "")
        }
    )
    if len(replicate_levels) >= 3:
        pooled_ci = _replicate_bootstrap(
            expression["rows"],
            metadata,
            target_cells,
            control_cells,
            target_gene,
            replicate_column,
            layer_scale,
            iterations=int(request.parameters.get("bootstrap_iterations", 1000)),
        )
        bootstrap_unit = "replicate"
    else:
        pooled_ci = _bootstrap_effect(
            target_values,
            control_values,
            layer_scale,
            int(request.parameters.get("bootstrap_iterations", 1000)),
            seed=1729,
        )
        bootstrap_unit = "cell_within_design_caution"
    loo = {}
    for excluded in sorted(guide_groups):
        cells = [
            cell for guide, members in guide_groups.items()
            if guide != excluded
            for cell in members
        ]
        loo[excluded] = _effect(
            [expression["rows"][cell][target_gene] for cell in cells],
            control_values,
            layer_scale,
        ) if cells else None
    signature_genes = [
        str(item) for item in request.parameters.get("signature_genes") or []
        if str(item) in expression["genes"]
    ]
    leakage = bool(
        request.parameters.get("signature_learned_from_same_perturbation")
        or request.parameters.get("signature_test_split_used")
    )
    signature = _signature_efficacy(
        expression["rows"],
        target_cells,
        control_cells,
        signature_genes,
        layer_scale,
    )
    signature["leakage_detected"] = leakage
    signature["confirmation_allowed"] = bool(signature.get("available")) and not leakage
    control_detection = _detection(control_values)
    blockers: list[str] = []
    cautions: list[str] = []
    if len(target_cells) < profile["minimum_cells"] or len(control_cells) < profile["minimum_cells"]:
        blockers.append("target or control cell coverage is below the profile minimum")
    if control_detection < profile["minimum_control_detection"] and not signature["confirmation_allowed"]:
        blockers.append("target gene detectability is low and no leakage-safe signature fallback is available")
    if len(replicate_levels) < 3:
        cautions.append("replicate-stratified bootstrap was unavailable; cell bootstrap is exploratory")
    if not _direction_supported(pooled_effect, expected, profile["minimum_abs_effect"]):
        cautions.append("pooled target-gene effect does not support the expected direction")
    if concordance is None or concordance < profile["minimum_guide_concordance"]:
        cautions.append("guide direction concordance is unresolved or below the development threshold")
    if leakage:
        cautions.append("signature-level efficacy is leakage-affected and cannot confirm target efficacy")
    batch_overlap = _axis_overlap(metadata, target_cells, control_cells, batch_column)
    replicate_overlap = _axis_overlap(metadata, target_cells, control_cells, replicate_column)
    payload = {
        "schema_version": "pertura-target-guide-efficacy-v1",
        "target_uid": target_uid,
        "control_uid": control_uid,
        "target_gene": target_gene,
        "expected_direction": expected,
        "profile": profile_name,
        "profile_validation": "dev_unvalidated",
        "target_gene_efficacy": {
            "effect": pooled_effect,
            "bootstrap_ci": pooled_ci,
            "bootstrap_unit": bootstrap_unit,
            "control_detection": control_detection,
            "target_detection": _detection(target_values),
        },
        "guide_effects": guide_results,
        "guide_concordance": concordance,
        "heterogeneity": _heterogeneity([item["effect"] for item in eligible]),
        "leave_one_guide_out": loo,
        "signature_efficacy": signature,
        "batch_overlap": batch_overlap,
        "replicate_overlap": replicate_overlap,
        "blockers": blockers,
        "cautions": cautions,
    }
    output = write_json(staging, "target_guide_efficacy.json", payload)
    status = DiagnosticStatus.blocked if blockers else (
        DiagnosticStatus.caution if cautions else DiagnosticStatus.screen_passed
    )
    return envelope(
        spec,
        request,
        contract,
        status=status,
        summary=f"Evaluated direct and guide-level efficacy for {target_gene}.",
        blockers=blockers,
        cautions=cautions,
        metrics={
            "effect": pooled_effect,
            "control_detection": control_detection,
            "n_guides": len(guide_groups),
            "guide_concordance": concordance,
            "n_shared_replicates": len(replicate_overlap["shared_levels"]),
            "signature_confirmation_allowed": signature["confirmation_allowed"],
        },
        outputs=(output,),
        metadata={
            "profile": profile_name,
            "profile_validation": "dev_unvalidated",
            "retained_manifest_applied": True,
        },
    )


def run_target_reliability_aggregate(
    spec: CapabilitySpec,
    request: CapabilityRunRequest,
    contract: DatasetContract,
    staging: Path,
):
    results = dependency_results(staging)
    required = {
        "screen.retained_cells.v1",
        "diagnostic.design_balance.v1",
        "target.guide_efficacy.v1",
        "target.responder.mixscape.v1",
    }
    by_capability = {item.get("capability_id"): item for item in results}
    missing = sorted(required - set(by_capability))
    if missing:
        return blocked(
            spec,
            request,
            contract,
            "target reliability is missing explicit dependencies: " + ", ".join(missing),
        )
    blockers: list[str] = []
    cautions: list[str] = []
    trace = []
    for capability_id in sorted(required):
        result = by_capability[capability_id]
        trace.append(
            {
                "capability_id": capability_id,
                "result_id": result["result_id"],
                "result_hash": result["canonical_hash"],
                "status": result["status"],
            }
        )
        blockers.extend(
            f"{capability_id}: {reason}"
            for reason in result.get("blockers") or []
        )
        cautions.extend(
            f"{capability_id}: {reason}"
            for reason in result.get("cautions") or []
        )
    cautions.append("aggregate uses dev_unvalidated_v0 thresholds and is not a production screen certification")
    status = DiagnosticStatus.blocked if blockers else DiagnosticStatus.caution
    payload = {
        "schema_version": "pertura-target-reliability-aggregate-v1",
        "status": status.value,
        "profile": "dev_unvalidated_v0",
        "validation_status": "synthetic_only",
        "dependency_trace": trace,
        "blockers": blockers,
        "cautions": list(dict.fromkeys(cautions)),
        "raw_data_recomputed": False,
    }
    output = write_json(staging, "target_reliability_aggregate.json", payload)
    return envelope(
        spec,
        request,
        contract,
        status=status,
        summary=f"Aggregated target reliability from {len(trace)} explicit committed dependencies.",
        blockers=blockers,
        cautions=cautions,
        metrics={
            "dependency_count": len(trace),
            "profile_validated": False,
            "raw_data_recomputed": False,
        },
        outputs=(output,),
        metadata={"profile": "dev_unvalidated_v0", "raw_data_recomputed": False},
    )


def _replicate_bootstrap(
    rows: dict[str, dict[str, float]],
    metadata: dict[str, dict[str, str]],
    target_cells: list[str],
    control_cells: list[str],
    gene: str,
    replicate_column: str,
    scale: str,
    *,
    iterations: int,
) -> dict[str, Any]:
    target_by_rep: dict[str, list[str]] = {}
    control_by_rep: dict[str, list[str]] = {}
    for cell in target_cells:
        target_by_rep.setdefault(metadata[cell].get(replicate_column, ""), []).append(cell)
    for cell in control_cells:
        control_by_rep.setdefault(metadata[cell].get(replicate_column, ""), []).append(cell)
    levels = sorted((set(target_by_rep) & set(control_by_rep)) - {""})
    if not levels or iterations <= 0:
        return {"low": None, "high": None, "iterations": 0}
    rng = random.Random(1729)
    values = []
    for _ in range(iterations):
        sampled = [levels[rng.randrange(len(levels))] for _ in levels]
        left = [
            rows[cell][gene]
            for level in sampled
            for cell in target_by_rep[level]
        ]
        control = [
            rows[cell][gene]
            for level in sampled
            for cell in control_by_rep[level]
        ]
        values.append(_effect(left, control, scale))
    values.sort()
    return {
        "low": values[int(0.025 * (len(values) - 1))],
        "high": values[int(0.975 * (len(values) - 1))],
        "iterations": iterations,
        "unit": "replicate",
    }
