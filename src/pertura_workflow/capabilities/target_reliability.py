from __future__ import annotations

import csv
import json
import math
import random
from collections import Counter
from importlib import resources
from pathlib import Path
from statistics import median
from typing import Any

import yaml

from pertura_core import CapabilityRunRequest, CapabilitySpec, DatasetContract, DiagnosticStatus, ResultEnvelope
from pertura_core.hashing import file_sha256
from pertura_workflow.capabilities.dependency_inputs import (
    apply_retained_cells,
    dependency_grounding_metadata,
    retained_cells_for_request,
)


def run_target_reliability_v2(
    spec: CapabilitySpec,
    request: CapabilityRunRequest,
    contract: DatasetContract,
    staging: Path,
) -> ResultEnvelope:
    params = request.parameters
    expression_path = _resolve_input(contract, params.get("expression_path"))
    metadata_path = _resolve_input(contract, params.get("metadata_path"))
    target_uid = str(params.get("target_uid") or "")
    control_uid = str(params.get("control_uid") or "")
    target_gene = str(params.get("target_gene") or "")
    condition_column = str(params.get("condition_column") or "perturbation_uid")
    cell_column = str(params.get("cell_column") or "cell_id")
    guide_column = str(params.get("guide_column") or "guide")
    batch_column = str(params.get("batch_column") or "batch")
    replicate_column = str(params.get("replicate_column") or "replicate")
    expected = str(params.get("expected_direction") or "down").lower()
    layer_scale = str(params.get("layer_scale") or "log_normalized")
    profile_name = str(params.get("profile") or "dev_unvalidated_v0")
    profile = _load_profile(profile_name)
    if not target_uid or not control_uid or not target_gene:
        raise ValueError("target_uid, control_uid and target_gene are required")
    if expected not in {"down", "up"}:
        raise ValueError("expected_direction must be down or up")

    expression = _read_expression(expression_path, cell_column)
    metadata = _read_metadata(metadata_path, cell_column)
    retained = retained_cells_for_request(staging, request)
    expression_cells = [
        cell for cell in metadata if cell in expression["rows"]
    ]
    analysis_cells = apply_retained_cells(expression_cells, retained)
    analysis_cell_set = set(analysis_cells)
    grounding = dependency_grounding_metadata(retained, analysis_cells)
    if target_gene not in expression["genes"]:
        return _blocked(
            spec,
            request,
            contract,
            (f"target gene is absent from expression matrix: {target_gene}",),
            profile_name,
            metadata=grounding,
        )
    target_cells = [
        cell
        for cell, row in metadata.items()
        if row.get(condition_column) == target_uid and cell in analysis_cell_set
    ]
    control_cells = [
        cell
        for cell, row in metadata.items()
        if row.get(condition_column) == control_uid and cell in analysis_cell_set
    ]
    target_values = [expression["rows"][cell][target_gene] for cell in target_cells]
    control_values = [expression["rows"][cell][target_gene] for cell in control_cells]
    effect = _effect(target_values, control_values, layer_scale)
    ci = _bootstrap_effect(target_values, control_values, layer_scale, int(params.get("bootstrap_iterations", 1000)), seed=0)
    control_detection = _detection(control_values)
    target_detection = _detection(target_values)

    guide_groups: dict[str, list[str]] = {}
    if guide_column in next(iter(metadata.values()), {}):
        for cell in target_cells:
            guide = metadata[cell].get(guide_column, "").strip()
            if guide:
                guide_groups.setdefault(guide, []).append(cell)
    per_guide: dict[str, Any] = {}
    for guide, cells in sorted(guide_groups.items()):
        values = [expression["rows"][cell][target_gene] for cell in cells]
        guide_effect = _effect(values, control_values, layer_scale)
        per_guide[guide] = {
            "n_cells": len(values),
            "effect": guide_effect,
            "direction_supported": _direction_supported(guide_effect, expected, profile["minimum_abs_effect"]),
            "bootstrap_ci": _bootstrap_effect(values, control_values, layer_scale, min(500, int(params.get("bootstrap_iterations", 1000))), seed=_stable_seed(guide)),
        }
    eligible_guides = [item for item in per_guide.values() if item["n_cells"] >= profile["minimum_cells_per_guide"]]
    concordance = sum(item["direction_supported"] for item in eligible_guides) / len(eligible_guides) if eligible_guides else None
    guide_effect_values = [item["effect"] for item in eligible_guides]
    heterogeneity = _heterogeneity(guide_effect_values)
    loo = {}
    for left_out in sorted(guide_groups):
        cells = [cell for guide, members in guide_groups.items() if guide != left_out for cell in members]
        values = [expression["rows"][cell][target_gene] for cell in cells]
        loo[left_out] = _effect(values, control_values, layer_scale) if values else None

    signature_genes = [str(item) for item in params.get("signature_genes") or [] if str(item) in expression["genes"]]
    signature = _signature_efficacy(expression["rows"], target_cells, control_cells, signature_genes, layer_scale)
    responder = _responder_summary(metadata, target_cells, params)
    batch_overlap = _axis_overlap(metadata, target_cells, control_cells, batch_column)
    replicate_overlap = _axis_overlap(metadata, target_cells, control_cells, replicate_column)

    blockers: list[str] = []
    cautions: list[str] = []
    if retained is not None and not analysis_cells:
        blockers.append("retained-cell manifest has no overlap with expression and metadata")
    if len(target_cells) < profile["minimum_cells"] or len(control_cells) < profile["minimum_cells"]:
        blockers.append("target or control cell coverage is below the profile minimum")
    if control_detection < profile["minimum_control_detection"] and not signature["available"]:
        blockers.append("target gene has low control detectability and no signature-level efficacy is available")
    if batch_overlap["available"] and not batch_overlap["shared_levels"]:
        blockers.append("target and control have no shared batch levels")
    if not _direction_supported(effect, expected, profile["minimum_abs_effect"]):
        cautions.append("pooled target-gene effect does not support the expected direction")
    if len(eligible_guides) < profile["minimum_guides"]:
        cautions.append("too few powered guides support target-level pooling")
    if concordance is None or concordance < profile["minimum_guide_concordance"]:
        cautions.append("guide direction concordance is unresolved or below profile minimum")
    if replicate_overlap["available"] and len(replicate_overlap["shared_levels"]) < 2:
        cautions.append("fewer than two shared replicate levels support the target-control comparison")
    if responder["status"] == "unresolved":
        cautions.append("escape/responder fraction is unresolved; provide a Mixscape/Mixscale classification")
    production_profile = (
        bool(profile.get("validated"))
        and profile.get("validation_class") == "expert_adjudicated"
        and bool(profile.get("benchmark_hash"))
        and bool(profile.get("evaluation_metrics_hash"))
        and bool(profile.get("adjudication_manifest_hash"))
    )
    if not production_profile:
        cautions.append(
            f"threshold profile {profile_name} is not benchmark-validated by expert adjudication and cannot issue production screen_passed"
        )

    if blockers:
        status = DiagnosticStatus.blocked
    elif cautions:
        status = DiagnosticStatus.caution
    else:
        status = DiagnosticStatus.screen_passed
    payload = {
        "schema_version": "pertura-target-reliability-v2",
        "target_uid": target_uid,
        "control_uid": control_uid,
        "target_gene": target_gene,
        "status": status.value,
        "profile": profile,
        "n_target_cells": len(target_cells),
        "n_control_cells": len(control_cells),
        "target_gene_efficacy": {
            "effect": effect,
            "bootstrap_ci": ci,
            "expected_direction": expected,
            "direction_supported": _direction_supported(effect, expected, profile["minimum_abs_effect"]),
            "target_detection": target_detection,
            "control_detection": control_detection,
        },
        "guide_effects": per_guide,
        "guide_concordance": concordance,
        "heterogeneity": heterogeneity,
        "leave_one_guide_out": loo,
        "signature_efficacy": signature,
        "escape_responder": responder,
        "batch_overlap": batch_overlap,
        "replicate_overlap": replicate_overlap,
        "blockers": blockers,
        "cautions": list(dict.fromkeys(cautions)),
        "dependency_grounding": grounding,
    }
    output = staging / "target_reliability_v2.json"
    output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return ResultEnvelope(
        run_id=request.run_id,
        request_id=request.request_id,
        capability_id=spec.capability_id,
        capability_version=spec.version,
        capability_trust=spec.trust_level,
        contract_id=contract.contract_id,
        contract_hash=contract.canonical_hash,
        scope=request.scope,
        status=status,
        result_kind=spec.output_kind,
        source_class=spec.source_class,
        summary=f"Target reliability evaluated for {target_gene}: {status.value}.",
        blockers=tuple(blockers),
        cautions=tuple(dict.fromkeys(cautions)),
        metrics={
            "effect": effect,
            "bootstrap_ci_low": ci["low"],
            "bootstrap_ci_high": ci["high"],
            "control_detection": control_detection,
            "guide_concordance": concordance,
            "n_guides": len(guide_groups),
            "responder_fraction": responder.get("responder_fraction"),
            "n_shared_replicates": len(replicate_overlap["shared_levels"]),
            "profile": profile_name,
            "profile_validated": production_profile,
            **grounding,
        },
        output_paths=(output.name,),
        output_hashes={output.name: file_sha256(output)},
        dependencies=request.dependencies,
        metadata={"profile_hash": profile["profile_hash"], **grounding},
    )


def _blocked(
    spec: CapabilitySpec,
    request: CapabilityRunRequest,
    contract: DatasetContract,
    blockers: tuple[str, ...],
    profile: str,
    *,
    metadata: dict[str, Any] | None = None,
) -> ResultEnvelope:
    return ResultEnvelope(
        run_id=request.run_id, request_id=request.request_id, capability_id=spec.capability_id,
        capability_version=spec.version, capability_trust=spec.trust_level,
        contract_id=contract.contract_id, contract_hash=contract.canonical_hash, scope=request.scope,
        status=DiagnosticStatus.blocked, result_kind=spec.output_kind, source_class=spec.source_class,
        summary="Target reliability was blocked before evaluation.", blockers=blockers,
        dependencies=request.dependencies, metadata={"profile": profile, **(metadata or {})},
    )


def _load_profile(name: str) -> dict[str, Any]:
    path = resources.files("pertura_workflow.capabilities").joinpath("profiles", f"{name}.yaml")
    if not path.is_file():
        raise ValueError(f"unknown target reliability profile: {name}")
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if payload.get("validated") and payload.get("validation_class") != "expert_adjudicated":
        raise ValueError("only expert-adjudicated target reliability profiles can be production validated")
    if payload.get("validated"):
        required = ("benchmark_hash", "evaluation_metrics_hash", "adjudication_manifest_hash")
        if any(not payload.get(field) for field in required):
            raise ValueError("validated target reliability profile is missing provenance hashes")
    from pertura_core.hashing import canonical_hash

    payload["profile_hash"] = canonical_hash(payload)
    return payload


def _resolve_input(contract: DatasetContract, value: Any) -> Path:
    if value in (None, ""):
        raise ValueError("target reliability capability is missing a required input path")
    candidate = Path(str(value)).expanduser()
    roots = [Path(item).expanduser().resolve() for item in contract.source_paths]
    if not candidate.is_absolute():
        directories = [item for item in roots if item.is_dir()]
        if not directories:
            raise ValueError("relative input requires a directory DatasetContract source")
        candidate = directories[0] / candidate
    resolved = candidate.resolve()
    if not any(resolved == root or (root.is_dir() and root in resolved.parents) for root in roots):
        raise ValueError("target reliability input is not bound to DatasetContract")
    return resolved


def _read_expression(path: Path, cell_column: str) -> dict[str, Any]:
    delimiter = "\t" if path.suffix.lower() in {".tsv", ".txt"} else ","
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter=delimiter)
        if not reader.fieldnames or cell_column not in reader.fieldnames:
            raise ValueError(f"expression table must contain {cell_column}")
        genes = [item for item in reader.fieldnames if item != cell_column]
        rows = {}
        for row in reader:
            cell = str(row.get(cell_column) or "").strip()
            if not cell:
                continue
            rows[cell] = {gene: float(row.get(gene) or 0) for gene in genes}
    return {"genes": genes, "rows": rows}


def _read_metadata(path: Path, cell_column: str) -> dict[str, dict[str, str]]:
    delimiter = "\t" if path.suffix.lower() in {".tsv", ".txt"} else ","
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter=delimiter)
        if not reader.fieldnames or cell_column not in reader.fieldnames:
            raise ValueError(f"metadata must contain {cell_column}")
        return {str(row[cell_column]).strip(): {key: str(value or "").strip() for key, value in row.items()} for row in reader if str(row.get(cell_column) or "").strip()}


def _effect(left: list[float], control: list[float], scale: str) -> float:
    if not left or not control:
        return 0.0
    left_mean = sum(left) / len(left)
    control_mean = sum(control) / len(control)
    if scale == "counts":
        positive = [value for value in left + control if value > 0]
        pseudocount = min(positive) / 2 if positive else 1.0
        return math.log2((left_mean + pseudocount) / (control_mean + pseudocount))
    return left_mean - control_mean


def _bootstrap_effect(left: list[float], control: list[float], scale: str, iterations: int, seed: int) -> dict[str, float | int | None]:
    if not left or not control or iterations <= 0:
        return {"low": None, "high": None, "iterations": 0}
    rng = random.Random(seed)
    values = []
    for _ in range(iterations):
        left_sample = [left[rng.randrange(len(left))] for _ in left]
        control_sample = [control[rng.randrange(len(control))] for _ in control]
        values.append(_effect(left_sample, control_sample, scale))
    values.sort()
    return {"low": values[int(0.025 * (len(values) - 1))], "high": values[int(0.975 * (len(values) - 1))], "iterations": iterations}


def _direction_supported(effect: float, expected: str, minimum: float) -> bool:
    return effect <= -minimum if expected == "down" else effect >= minimum


def _detection(values: list[float]) -> float:
    return sum(value > 0 for value in values) / len(values) if values else 0.0


def _heterogeneity(effects: list[float]) -> dict[str, Any]:
    if len(effects) < 2:
        return {"status": "unresolved", "n_guides": len(effects)}
    mean = sum(effects) / len(effects)
    variance = sum((value - mean) ** 2 for value in effects) / (len(effects) - 1)
    signs = {value > 0 for value in effects if value != 0}
    return {"status": "estimated", "variance": variance, "range": [min(effects), max(effects)], "direction_disagreement": len(signs) > 1}


def _signature_efficacy(rows: dict[str, dict[str, float]], target_cells: list[str], control_cells: list[str], genes: list[str], scale: str) -> dict[str, Any]:
    if not genes:
        return {"available": False, "reason": "no signature genes were supplied"}
    effects = []
    for gene in genes:
        effects.append(_effect([rows[cell][gene] for cell in target_cells], [rows[cell][gene] for cell in control_cells], scale))
    return {"available": True, "genes": genes, "mean_effect": sum(effects) / len(effects), "per_gene_effect": dict(zip(genes, effects))}


def _responder_summary(metadata: dict[str, dict[str, str]], target_cells: list[str], params: dict[str, Any]) -> dict[str, Any]:
    column = str(params.get("mixscape_class_column") or "")
    if not column or column not in next(iter(metadata.values()), {}):
        return {"status": "unresolved", "method": None}
    labels = [metadata[cell].get(column, "").lower() for cell in target_cells]
    responder_labels = {str(item).lower() for item in params.get("responder_labels") or ["ko", "responder", "perturbed"]}
    responder = sum(label in responder_labels for label in labels)
    return {"status": "observed", "method": "imported_mixscape_or_mixscale_classification", "column": column, "n_cells": len(labels), "responder_fraction": responder / len(labels) if labels else None}


def _axis_overlap(metadata: dict[str, dict[str, str]], target_cells: list[str], control_cells: list[str], column: str) -> dict[str, Any]:
    if column not in next(iter(metadata.values()), {}):
        return {"available": False, "shared_levels": [], "target_counts": {}, "control_counts": {}}
    target = Counter(metadata[cell].get(column, "") for cell in target_cells)
    control = Counter(metadata[cell].get(column, "") for cell in control_cells)
    target.pop("", None)
    control.pop("", None)
    return {"available": True, "shared_levels": sorted(set(target) & set(control)), "target_counts": dict(target), "control_counts": dict(control)}


def _stable_seed(text: str) -> int:
    import hashlib

    return int(hashlib.sha256(text.encode("utf-8")).hexdigest()[:8], 16)
