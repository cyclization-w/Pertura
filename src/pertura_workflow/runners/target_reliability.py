from __future__ import annotations

import csv
import json
import math
from collections import Counter
from pathlib import Path
from statistics import median
from typing import Any

from pertura_gate.evidence.execution_ledger import canonical_execution_hash, file_sha256
from pertura_workflow.trusted_run import record_trusted_run

METHOD = "target_reliability_audit"
RUNNER_NAME = "target_reliability_audit"
RUNNER_VERSION = "target_reliability_audit_v1"


def run_target_reliability_audit(
    workspace: str | Path,
    *,
    expression_csv: str | Path,
    metadata_csv: str | Path,
    target_uid: str,
    control_uid: str,
    target: str,
    target_gene: str,
    layer: str,
    output_path: str | Path | None = None,
    cell_id_column: str = "cell_id",
    condition_column: str = "perturbation_uid",
    guide_column: str | None = None,
    batch_column: str | None = None,
    replicate_column: str | None = None,
    expected_direction: str = "down",
    layer_scale: str = "log_normalized",
    perturbation_modality: str = "crispri",
    minimum_cells: int = 20,
    minimum_guides: int = 2,
    minimum_cells_per_guide: int = 5,
    minimum_control_detection: float = 0.10,
    minimum_abs_effect: float = 0.10,
    minimum_guide_concordance: float = 0.75,
) -> dict[str, Any]:
    """Audit whether one target is reliable enough for downstream analysis.

    Assignment coverage, target-gene detectability, expected direction, guide
    agreement, and batch/replicate overlap remain separate diagnostics. This
    runner does not turn them into a mechanism claim or significance test.
    """

    if expected_direction not in {"down", "up", "either"}:
        raise ValueError("expected_direction must be down, up, or either")
    if layer_scale not in {"counts", "log_normalized"}:
        raise ValueError("layer_scale must be counts or log_normalized")
    if not all((target_uid, control_uid, target, target_gene, layer)):
        raise ValueError("target_uid, control_uid, target, target_gene, and layer are required")

    root = Path(workspace).resolve()
    expression_path = _workspace_file(root, expression_csv)
    metadata_path = _workspace_file(root, metadata_csv)
    metadata_fields, metadata = _read_metadata(metadata_path, cell_id_column)
    required = {condition_column, *(column for column in (guide_column, batch_column, replicate_column) if column)}
    missing = sorted(required - set(metadata_fields))
    if missing:
        raise ValueError("metadata_csv is missing columns: " + ", ".join(missing))
    expression = _read_expression(expression_path, cell_id_column, target_gene)

    target_cells = [cell for cell, row in metadata.items() if row.get(condition_column) == target_uid]
    control_cells = [cell for cell, row in metadata.items() if row.get(condition_column) == control_uid]
    target_values = [expression[cell] for cell in target_cells if cell in expression]
    control_values = [expression[cell] for cell in control_cells if cell in expression]
    effect = _effect(target_values, control_values, layer_scale)
    direction_supported = _direction_supported(effect, expected_direction, minimum_abs_effect)
    control_detection = _detection_rate(control_values)
    target_detection = _detection_rate(target_values)
    dropout_risk = "high" if control_detection < minimum_control_detection else ("moderate" if control_detection < 0.30 else "low")

    cells_per_guide: Counter[str] = Counter()
    guide_effects: dict[str, dict[str, Any]] = {}
    if guide_column:
        for cell in target_cells:
            guide = str(metadata[cell].get(guide_column) or "").strip()
            if guide:
                cells_per_guide[guide] += 1
        for guide, count in sorted(cells_per_guide.items()):
            values = [expression[cell] for cell in target_cells if cell in expression and str(metadata[cell].get(guide_column) or "").strip() == guide]
            guide_effect = _effect(values, control_values, layer_scale)
            guide_effects[guide] = {
                "n_cells": count,
                "n_expression_cells": len(values),
                "effect": guide_effect,
                "direction_supported": _direction_supported(guide_effect, expected_direction, minimum_abs_effect),
            }
    eligible_guide_effects = [item for item in guide_effects.values() if item["n_expression_cells"] >= minimum_cells_per_guide]
    concordance = sum(bool(item["direction_supported"]) for item in eligible_guide_effects) / len(eligible_guide_effects) if eligible_guide_effects else None
    guide_consistency = _guide_consistency(len(cells_per_guide), concordance, minimum_guide_concordance)
    batch_coverage = _axis_coverage(metadata, target_cells, control_cells, batch_column)
    replicate_coverage = _axis_coverage(metadata, target_cells, control_cells, replicate_column)

    findings: list[dict[str, str]] = []
    if len(target_cells) < minimum_cells or len(control_cells) < minimum_cells:
        _finding(findings, "insufficient_cells", "block", f"Need at least {minimum_cells} target and control cells.", "Increase coverage or pool only scientifically compatible replicates.")
    if not target_values or not control_values:
        _finding(findings, "missing_target_expression", "block", "Target-gene expression is absent for one contrast arm.", "Verify gene identifiers, matrix orientation, and the declared expression layer.")
    if control_detection < minimum_control_detection:
        _finding(findings, "target_gene_low_detectability", "block", f"Control detection rate is {control_detection:.3f}, below {minimum_control_detection:.3f}.", "Do not interpret zeros as knockdown; use orthogonal efficacy evidence or redesign the readout.")
    elif dropout_risk == "moderate":
        _finding(findings, "dropout_risk", "caution", f"Control detection rate is only {control_detection:.3f}.", "Treat target-expression efficacy as uncertain and inspect signature-level response.")
    if not direction_supported:
        _finding(findings, "expected_direction_not_supported", "caution", f"Observed {layer_scale} effect ({effect:.4g}) does not support expected direction {expected_direction}.", "Check target identity, perturbation modality, escape cells, and alternative efficacy readouts.")
    if guide_column:
        if len(cells_per_guide) < minimum_guides:
            _finding(findings, "too_few_guides", "caution", f"Observed {len(cells_per_guide)} guides; policy requests {minimum_guides}.", "Add or retain an independent guide before target-level interpretation.")
        if concordance is None:
            _finding(findings, "guide_concordance_unresolved", "caution", "No guide had enough expression-observed cells for concordance.", "Increase cells per guide or inspect assignment/dropout separately.")
        elif concordance < minimum_guide_concordance:
            _finding(findings, "guide_disagreement", "caution", f"Expected-direction guide concordance is {concordance:.3f}.", "Report guide-level effects and avoid a pooled target claim until disagreement is resolved.")
    elif _is_guide_based(perturbation_modality):
        _finding(findings, "guide_identity_unavailable", "caution", "Guide-level consistency was not assessed for a guide-based experiment.", "Provide the guide column and guide-to-target map.")
    if batch_coverage and not batch_coverage["has_shared_levels"]:
        _finding(findings, "batch_perturbation_confounding", "block", "Target and control have no shared batch levels.", "Do not estimate a target effect without within-batch support.")
    elif batch_coverage and batch_coverage["total_variation"] > 0.50:
        _finding(findings, "batch_imbalance", "caution", f"Batch distribution total variation is {batch_coverage['total_variation']:.3f}.", "Use a batch-aware contrast and sensitivity analysis.")
    if replicate_column and (not replicate_coverage or len(replicate_coverage["shared_levels"]) < 2):
        _finding(findings, "insufficient_replicate_overlap", "caution", "Fewer than two shared replicate levels support this contrast.", "Acquire replicate support or keep the conclusion exploratory.")

    blockers = [item for item in findings if item["severity"] == "block"]
    cautions = [item for item in findings if item["severity"] == "caution"]
    status = "blocked" if blockers else ("caution" if cautions else "eligible")
    guide_ready = not guide_column or (len(cells_per_guide) >= minimum_guides and concordance is not None and concordance >= minimum_guide_concordance)
    payload: dict[str, Any] = {
        "schema_version": "pertura-target-reliability-v1", "method": METHOD,
        "target": target, "target_gene": target_gene, "target_uid": target_uid,
        "control_uid": control_uid, "layer": layer, "layer_scale": layer_scale,
        "expected_direction": expected_direction, "status": status,
        "n_target_cells": len(target_cells), "n_control_cells": len(control_cells),
        "n_target_expression_cells": len(target_values), "n_control_expression_cells": len(control_values),
        "target_expression": _summary(target_values), "control_expression": _summary(control_values),
        "effect": effect, "effect_metric": "log2_mean_ratio" if layer_scale == "counts" else "mean_difference",
        "direction_supported": direction_supported, "target_detection_rate": target_detection,
        "control_detection_rate": control_detection, "dropout_risk": dropout_risk,
        "guides_per_target": len(cells_per_guide) if guide_column else None,
        "cells_per_guide": dict(sorted(cells_per_guide.items())), "guide_effects": guide_effects,
        "guide_concordance": concordance, "guide_consistency": guide_consistency,
        "batch_coverage": batch_coverage or {}, "replicate_coverage": replicate_coverage or {},
        "findings": findings,
        "eligibility": {"measured_effect_analysis": not blockers, "target_engagement_interpretation": not blockers and direction_supported and guide_ready, "status": status},
        "policy": {"minimum_cells": minimum_cells, "minimum_guides": minimum_guides, "minimum_cells_per_guide": minimum_cells_per_guide, "minimum_control_detection": minimum_control_detection, "minimum_abs_effect": minimum_abs_effect, "minimum_guide_concordance": minimum_guide_concordance},
    }
    return _persist(root, expression_path, metadata_path, output_path, payload)


def _persist(root: Path, expression_path: Path, metadata_path: Path, output_path: str | Path | None, payload: dict[str, Any]) -> dict[str, Any]:
    out_path = _output_path(root, output_path, str(payload["target"])); out_path.parent.mkdir(parents=True, exist_ok=True)
    input_hashes = {"expression_csv": file_sha256(expression_path), "metadata_csv": file_sha256(metadata_path)}
    parameters = {key: payload[key] for key in ("target_uid", "control_uid", "target", "target_gene", "layer", "layer_scale", "expected_direction", "policy")}
    execution_hash = canonical_execution_hash({"runner_name": RUNNER_NAME, "runner_version": RUNNER_VERSION, "method": METHOD, "input_hashes": input_hashes, "parameters": parameters})
    payload["execution_hash"] = execution_hash; payload["input_hashes"] = input_hashes
    out_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    output_hashes = {"target_reliability": file_sha256(out_path)}
    ledger = record_trusted_run(root, execution_hash=execution_hash, runner_name=RUNNER_NAME, runner_version=RUNNER_VERSION, method=METHOD, input_hashes=input_hashes, output_hashes=output_hashes, parameters=parameters)
    return {**payload, "path": str(out_path), "relative_path": str(out_path.relative_to(root)), "output_hashes": output_hashes, "execution_ledger_path": ledger["execution_ledger_path"], "execution_ledger_relative_path": ledger["execution_ledger_relative_path"]}


def _read_metadata(path: Path, cell_id_column: str) -> tuple[list[str], dict[str, dict[str, str]]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle); fields = list(reader.fieldnames or [])
        if cell_id_column not in fields: raise ValueError(f"metadata_csv must include {cell_id_column!r}")
        rows = {str(row.get(cell_id_column) or "").strip(): dict(row) for row in reader if str(row.get(cell_id_column) or "").strip()}
    return fields, rows


def _read_expression(path: Path, cell_id_column: str, target_gene: str) -> dict[str, float]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle); fields = list(reader.fieldnames or [])
        if cell_id_column not in fields or target_gene not in fields: raise ValueError(f"expression_csv must include {cell_id_column!r} and target gene {target_gene!r}")
        result = {}
        for row in reader:
            cell = str(row.get(cell_id_column) or "").strip()
            try: value = float(str(row.get(target_gene) or "").strip())
            except ValueError: continue
            if cell and math.isfinite(value): result[cell] = value
        return result


def _effect(left: list[float], baseline: list[float], layer_scale: str) -> float:
    if not left or not baseline: return 0.0
    left_mean, baseline_mean = sum(left) / len(left), sum(baseline) / len(baseline)
    if layer_scale == "counts":
        positive = [value for value in left + baseline if value > 0]; pseudocount = max(min(positive) / 2, 1e-9) if positive else 1.0
        return math.log2((left_mean + pseudocount) / (baseline_mean + pseudocount))
    return left_mean - baseline_mean


def _direction_supported(effect: float, expected: str, minimum: float) -> bool:
    return abs(effect) >= minimum if expected == "either" else (effect <= -minimum if expected == "down" else effect >= minimum)


def _detection_rate(values: list[float]) -> float: return sum(value > 0 for value in values) / len(values) if values else 0.0


def _summary(values: list[float]) -> dict[str, float | int | None]:
    return {"n": len(values), "mean": sum(values) / len(values) if values else None, "median": median(values) if values else None, "minimum": min(values) if values else None, "maximum": max(values) if values else None}


def _axis_coverage(metadata: dict[str, dict[str, str]], target_cells: list[str], control_cells: list[str], column: str | None) -> dict[str, Any] | None:
    if not column: return None
    target = Counter(str(metadata[cell].get(column) or "").strip() for cell in target_cells); control = Counter(str(metadata[cell].get(column) or "").strip() for cell in control_cells)
    target.pop("", None); control.pop("", None); levels = sorted(set(target) | set(control)); shared = sorted(set(target) & set(control)); t_total, c_total = sum(target.values()), sum(control.values())
    tv = 0.5 * sum(abs((target[level] / t_total if t_total else 0) - (control[level] / c_total if c_total else 0)) for level in levels)
    return {"axis": column, "target_counts": dict(sorted(target.items())), "control_counts": dict(sorted(control.items())), "shared_levels": shared, "has_shared_levels": bool(shared), "total_variation": tv}


def _guide_consistency(n_guides: int, concordance: float | None, threshold: float) -> str | None:
    if n_guides == 0: return None
    if n_guides == 1: return "single_guide_observed"
    if concordance is None: return "unresolved"
    return "direction_consistent" if concordance >= threshold else "direction_inconsistent"


def _finding(items: list[dict[str, str]], code: str, severity: str, message: str, action: str) -> None: items.append({"code": code, "severity": severity, "message": message, "recommended_action": action})
def _is_guide_based(modality: str) -> bool: return str(modality).strip().lower().replace("-", "") in {"crispr", "crispri", "crispra", "crisprko", "guidecapture", "perturbseq"}


def _workspace_file(root: Path, path: str | Path) -> Path:
    candidate = Path(path); candidate = candidate if candidate.is_absolute() else root / candidate; resolved = candidate.resolve()
    if not _is_relative_to(resolved, root): raise ValueError(f"path is outside workspace: {path}")
    if not resolved.exists(): raise FileNotFoundError(resolved)
    return resolved


def _output_path(root: Path, path: str | Path | None, target: str) -> Path:
    safe = "".join(ch if ch.isalnum() else "_" for ch in target).strip("_") or "target"; candidate = Path(path) if path else Path("outputs") / f"target_reliability_{safe}.json"; candidate = candidate if candidate.is_absolute() else root / candidate; resolved = candidate.resolve()
    if not _is_relative_to(resolved, root): raise ValueError(f"output_path is outside workspace: {path}")
    return resolved


def _is_relative_to(path: Path, root: Path) -> bool:
    try: path.relative_to(root); return True
    except ValueError: return False
