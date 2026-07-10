from __future__ import annotations

import csv
import hashlib
import json
import math
import random
from pathlib import Path
from typing import Any

from pertura_gate.evidence.execution_ledger import canonical_execution_hash, file_sha256
from pertura_workflow.trusted_run import record_trusted_run

RUNNER_NAME = "control_calibration"
RUNNER_VERSION = "control_calibration_v1"
NTC_METHOD = "basic_ntc_vs_ntc_v1"
LABEL_PERMUTATION_METHOD = "basic_label_permutation_null_v1"


def run_ntc_vs_ntc_calibration(
    workspace: str | Path,
    *,
    expression_csv: str | Path,
    metadata_csv: str | Path,
    control_uid: str,
    layer: str,
    output_path: str | Path | None = None,
    cell_id_column: str = "cell_id",
    condition_column: str = "perturbation_uid",
    gene_columns: list[str] | None = None,
    alpha: float = 0.05,
    seed: int = 0,
    max_features: int | None = None,
) -> dict[str, Any]:
    """Run a narrow NTC-vs-NTC calibration check for an explicit control UID.

    The runner does not infer controls, cell types, normalization, confounders, or
    biological interpretation. It only compares two deterministic random splits
    of cells with the provided control UID and writes structured calibration JSON.
    """

    if not control_uid:
        raise ValueError("control_uid is required")
    if not layer:
        raise ValueError("layer declaration is required")

    root = Path(workspace).resolve()
    expression_path = _resolve_workspace_file(root, expression_csv)
    metadata_path = _resolve_workspace_file(root, metadata_csv)
    out_path = _resolve_output_path(root, output_path, "ntc_vs_ntc")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    condition_by_cell = _read_metadata(metadata_path, cell_id_column=cell_id_column, condition_column=condition_column)
    expression_rows, inferred_genes = _read_expression(expression_path, cell_id_column=cell_id_column, gene_columns=gene_columns)
    genes = list(gene_columns or inferred_genes)
    if max_features is not None:
        genes = genes[:max_features]
    if not genes:
        raise ValueError("expression_csv must contain at least one numeric gene column")

    control_cells = [(cell_id, values) for cell_id, values in expression_rows if condition_by_cell.get(cell_id) == control_uid]
    if len(control_cells) < 2:
        raise ValueError("NTC-vs-NTC calibration requires at least two control cells")
    shuffled = list(control_cells)
    random.Random(seed).shuffle(shuffled)
    midpoint = len(shuffled) // 2
    left_cells = shuffled[:midpoint]
    right_cells = shuffled[midpoint:]
    if not left_cells or not right_cells:
        raise ValueError("NTC-vs-NTC split produced an empty group")

    rows = _test_rows(genes, left_cells, right_cells)
    _add_padj(rows)
    n_significant = sum(1 for row in rows if row["padj"] <= alpha)
    check = {
        "passed": n_significant == 0,
        "status": "passed" if n_significant == 0 else "failed",
        "method": NTC_METHOD,
        "alpha": alpha,
        "n_features_tested": len(rows),
        "n_significant": n_significant,
        "n_control_cells": len(control_cells),
        "n_split_a": len(left_cells),
        "n_split_b": len(right_cells),
        "pvalue_summary": _pvalue_summary([row["pvalue"] for row in rows]),
        "padj_summary": _pvalue_summary([row["padj"] for row in rows]),
    }
    payload = {
        "schema_version": "pertura-control-calibration-v1",
        "calibration_type": "ntc_vs_ntc_check",
        "method": NTC_METHOD,
        "layer": layer,
        "control_uid": control_uid,
        "alpha": alpha,
        "seed": seed,
        "n_features_tested": len(rows),
        "n_significant": n_significant,
        "ntc_vs_ntc_check": check,
        "label_permutation_check": {},
        "quality": {"runner": NTC_METHOD, "diagnostic_only": True},
        "input_hashes": _input_hashes(expression_path, metadata_path),
    }
    return _persist_trusted_calibration(root, out_path, payload)


def run_label_permutation_null(
    workspace: str | Path,
    *,
    expression_csv: str | Path,
    metadata_csv: str | Path,
    contrast_uid: str,
    left_uid: str,
    baseline_uid: str,
    layer: str,
    output_path: str | Path | None = None,
    cell_id_column: str = "cell_id",
    condition_column: str = "perturbation_uid",
    gene_columns: list[str] | None = None,
    alpha: float = 0.05,
    seed: int = 0,
    max_features: int | None = None,
) -> dict[str, Any]:
    """Run a narrow label-permutation null for an explicit registered contrast."""

    if not contrast_uid or not left_uid or not baseline_uid:
        raise ValueError("contrast_uid, left_uid, and baseline_uid are required")
    if not layer:
        raise ValueError("layer declaration is required")

    root = Path(workspace).resolve()
    expression_path = _resolve_workspace_file(root, expression_csv)
    metadata_path = _resolve_workspace_file(root, metadata_csv)
    out_path = _resolve_output_path(root, output_path, "label_permutation")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    condition_by_cell = _read_metadata(metadata_path, cell_id_column=cell_id_column, condition_column=condition_column)
    expression_rows, inferred_genes = _read_expression(expression_path, cell_id_column=cell_id_column, gene_columns=gene_columns)
    genes = list(gene_columns or inferred_genes)
    if max_features is not None:
        genes = genes[:max_features]
    if not genes:
        raise ValueError("expression_csv must contain at least one numeric gene column")

    selected = [(cell_id, values, condition_by_cell.get(cell_id)) for cell_id, values in expression_rows if condition_by_cell.get(cell_id) in {left_uid, baseline_uid}]
    n_left = sum(1 for _, _, condition in selected if condition == left_uid)
    n_baseline = sum(1 for _, _, condition in selected if condition == baseline_uid)
    if n_left <= 0 or n_baseline <= 0:
        raise ValueError("label-permutation calibration requires cells for both left_uid and baseline_uid")

    permuted = list(selected)
    random.Random(seed).shuffle(permuted)
    left_cells = [(cell_id, values) for cell_id, values, _ in permuted[:n_left]]
    baseline_cells = [(cell_id, values) for cell_id, values, _ in permuted[n_left:]]

    rows = _test_rows(genes, left_cells, baseline_cells)
    _add_padj(rows)
    n_significant = sum(1 for row in rows if row["padj"] <= alpha)
    check = {
        "passed": n_significant == 0,
        "status": "passed" if n_significant == 0 else "failed",
        "method": LABEL_PERMUTATION_METHOD,
        "alpha": alpha,
        "n_features_tested": len(rows),
        "n_significant": n_significant,
        "n_left": n_left,
        "n_baseline": n_baseline,
        "seed": seed,
        "pvalue_summary": _pvalue_summary([row["pvalue"] for row in rows]),
        "padj_summary": _pvalue_summary([row["padj"] for row in rows]),
    }
    payload = {
        "schema_version": "pertura-control-calibration-v1",
        "calibration_type": "label_permutation_check",
        "method": LABEL_PERMUTATION_METHOD,
        "layer": layer,
        "contrast_uid": contrast_uid,
        "left_uid": left_uid,
        "baseline_uid": baseline_uid,
        "alpha": alpha,
        "seed": seed,
        "n_features_tested": len(rows),
        "n_significant": n_significant,
        "ntc_vs_ntc_check": {},
        "label_permutation_check": check,
        "quality": {"runner": LABEL_PERMUTATION_METHOD, "diagnostic_only": True},
        "input_hashes": _input_hashes(expression_path, metadata_path),
    }
    return _persist_trusted_calibration(root, out_path, payload)


def _test_rows(genes: list[str], left_cells: list[tuple[str, dict[str, float]]], baseline_cells: list[tuple[str, dict[str, float]]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for gene in genes:
        left = [values[gene] for _, values in left_cells if gene in values and math.isfinite(values[gene])]
        baseline = [values[gene] for _, values in baseline_cells if gene in values and math.isfinite(values[gene])]
        effect = _mean(left) - _mean(baseline)
        rows.append({
            "feature": gene,
            "mean_left": _mean(left),
            "mean_baseline": _mean(baseline),
            "effect_size": effect,
            "pvalue": _normal_approx_pvalue(left, baseline),
            "n_left": len(left),
            "n_baseline": len(baseline),
        })
    return rows


def _add_padj(rows: list[dict[str, Any]]) -> None:
    padj = _benjamini_hochberg([float(row["pvalue"]) for row in rows])
    for row, adjusted in zip(rows, padj):
        row["padj"] = adjusted


def _resolve_workspace_file(root: Path, path: str | Path) -> Path:
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = candidate.resolve()
    if not _is_relative_to(resolved, root):
        raise ValueError(f"path is outside workspace: {path}")
    if not resolved.exists():
        raise FileNotFoundError(resolved)
    return resolved


def _resolve_output_path(root: Path, output_path: str | Path | None, label: str) -> Path:
    if output_path is None:
        return (root / "outputs" / f"control_calibration_{label}.json").resolve()
    candidate = Path(output_path)
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = candidate.resolve()
    if not _is_relative_to(resolved, root):
        raise ValueError(f"output_path is outside workspace: {output_path}")
    return resolved


def _read_metadata(path: Path, *, cell_id_column: str, condition_column: str) -> dict[str, str]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames or cell_id_column not in reader.fieldnames or condition_column not in reader.fieldnames:
            raise ValueError(f"metadata_csv must include {cell_id_column!r} and {condition_column!r}")
        mapping = {}
        for row in reader:
            cell_id = str(row.get(cell_id_column) or "").strip()
            condition = str(row.get(condition_column) or "").strip()
            if cell_id:
                mapping[cell_id] = condition
    return mapping


def _read_expression(path: Path, *, cell_id_column: str, gene_columns: list[str] | None) -> tuple[list[tuple[str, dict[str, float]]], list[str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames or cell_id_column not in reader.fieldnames:
            raise ValueError(f"expression_csv must include {cell_id_column!r}")
        genes = list(gene_columns or [column for column in reader.fieldnames if column != cell_id_column])
        rows: list[tuple[str, dict[str, float]]] = []
        for row in reader:
            cell_id = str(row.get(cell_id_column) or "").strip()
            if not cell_id:
                continue
            values = {}
            for gene in genes:
                text = str(row.get(gene) or "").strip()
                if text == "":
                    continue
                try:
                    values[gene] = float(text)
                except ValueError:
                    continue
            rows.append((cell_id, values))
    return rows, genes


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _variance(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = _mean(values)
    return sum((value - mean) ** 2 for value in values) / (len(values) - 1)


def _normal_approx_pvalue(left: list[float], baseline: list[float]) -> float:
    if not left or not baseline:
        return 1.0
    diff = _mean(left) - _mean(baseline)
    se = math.sqrt((_variance(left) / len(left)) + (_variance(baseline) / len(baseline)))
    if se <= 0:
        return 0.0 if diff != 0 else 1.0
    z = abs(diff / se)
    return max(0.0, min(1.0, math.erfc(z / math.sqrt(2))))


def _benjamini_hochberg(pvalues: list[float]) -> list[float]:
    m = len(pvalues)
    if m == 0:
        return []
    indexed = sorted(enumerate(pvalues), key=lambda item: item[1], reverse=True)
    adjusted = [1.0] * m
    running = 1.0
    for rank_from_end, (index, pvalue) in enumerate(indexed, start=1):
        rank = m - rank_from_end + 1
        running = min(running, pvalue * m / rank)
        adjusted[index] = max(0.0, min(1.0, running))
    return adjusted


def _pvalue_summary(values: list[float]) -> dict[str, float | int | None]:
    clean = sorted(value for value in values if math.isfinite(value))
    if not clean:
        return {"count": 0, "min": None, "median": None, "max": None}
    return {
        "count": len(clean),
        "min": clean[0],
        "median": clean[len(clean) // 2],
        "max": clean[-1],
    }


def _input_hashes(*paths: Path) -> dict[str, str]:
    return {path.name: _file_hash(path) for path in paths}


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _persist_trusted_calibration(root: Path, out_path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    method = str(payload["method"])
    input_hashes = dict(payload.get("input_hashes") or {})
    parameter_keys = (
        "calibration_type",
        "layer",
        "control_uid",
        "contrast_uid",
        "left_uid",
        "baseline_uid",
        "alpha",
        "seed",
        "n_features_tested",
    )
    parameters = {key: payload[key] for key in parameter_keys if key in payload}
    execution_hash = canonical_execution_hash(
        {
            "runner_name": RUNNER_NAME,
            "runner_version": RUNNER_VERSION,
            "method": method,
            "input_hashes": input_hashes,
            "parameters": parameters,
        }
    )
    payload["execution_hash"] = execution_hash
    out_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    output_hashes = {"calibration": file_sha256(out_path)}
    ledger_record = record_trusted_run(
        root,
        execution_hash=execution_hash,
        runner_name=RUNNER_NAME,
        runner_version=RUNNER_VERSION,
        method=method,
        input_hashes=input_hashes,
        output_hashes=output_hashes,
        parameters=parameters,
    )
    return {
        **payload,
        "path": str(out_path),
        "relative_path": str(out_path.relative_to(root)) if _is_relative_to(out_path, root) else str(out_path),
        "output_hashes": output_hashes,
        "execution_ledger_path": ledger_record["execution_ledger_path"],
        "execution_ledger_relative_path": ledger_record["execution_ledger_relative_path"],
    }


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False
