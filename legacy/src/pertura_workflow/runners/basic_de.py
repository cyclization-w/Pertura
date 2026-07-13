from __future__ import annotations

import csv
import math
from pathlib import Path
from typing import Any


DEFAULT_METHOD = "basic_mean_difference_v1"


def run_basic_de_for_registered_contrast(
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
    pseudocount: float = 1e-9,
) -> dict[str, Any]:
    """Run a narrow, explicit DE helper for an already registered contrast.

    This runner is intentionally small: it does not infer biology, cell type,
    normalization, confounders, or scope. Callers must pass UID-linked contrast
    identity and a declared input layer. The output is a table plus structured
    metadata suitable for evidence registration.
    """

    if not contrast_uid or not left_uid or not baseline_uid:
        raise ValueError("contrast_uid, left_uid, and baseline_uid are required")
    if not layer:
        raise ValueError("layer declaration is required")

    root = Path(workspace).resolve()
    expression_path = _resolve_workspace_file(root, expression_csv)
    metadata_path = _resolve_workspace_file(root, metadata_csv)
    out_path = _resolve_output_path(root, output_path, contrast_uid)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    condition_by_cell = _read_metadata(metadata_path, cell_id_column=cell_id_column, condition_column=condition_column)
    expression_rows, inferred_genes = _read_expression(expression_path, cell_id_column=cell_id_column, gene_columns=gene_columns)
    genes = gene_columns or inferred_genes
    if not genes:
        raise ValueError("expression_csv must contain at least one numeric gene column")

    left_values: dict[str, list[float]] = {gene: [] for gene in genes}
    baseline_values: dict[str, list[float]] = {gene: [] for gene in genes}
    skipped_cells = 0

    for cell_id, values in expression_rows:
        condition = condition_by_cell.get(cell_id)
        if condition == left_uid:
            target = left_values
        elif condition == baseline_uid:
            target = baseline_values
        else:
            skipped_cells += 1
            continue
        for gene in genes:
            value = values.get(gene)
            if value is not None and math.isfinite(value):
                target[gene].append(value)

    n_left = _group_n(left_values)
    n_baseline = _group_n(baseline_values)
    if n_left <= 0 or n_baseline <= 0:
        raise ValueError("metadata/expression inputs must contain cells for both left_uid and baseline_uid")

    rows = []
    pvalues = []
    for gene in genes:
        left = left_values[gene]
        baseline = baseline_values[gene]
        mean_left = _mean(left)
        mean_baseline = _mean(baseline)
        effect = mean_left - mean_baseline
        logfc = math.log2((mean_left + pseudocount) / (mean_baseline + pseudocount)) if mean_left + pseudocount > 0 and mean_baseline + pseudocount > 0 else effect
        pvalue = _normal_approx_pvalue(left, baseline)
        pvalues.append(pvalue)
        rows.append(
            {
                "gene": gene,
                "mean_left": mean_left,
                "mean_baseline": mean_baseline,
                "logfc": logfc,
                "effect_size": effect,
                "pvalue": pvalue,
                "n_left": len(left),
                "n_baseline": len(baseline),
            }
        )

    padj = _benjamini_hochberg(pvalues)
    for row, adjusted in zip(rows, padj):
        row["padj"] = adjusted

    rows.sort(key=lambda item: (item["padj"], -abs(item["effect_size"]), item["gene"]))
    fieldnames = ["gene", "mean_left", "mean_baseline", "logfc", "effect_size", "pvalue", "padj", "n_left", "n_baseline"]
    with out_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: _format_number(row[key]) if isinstance(row[key], float) else row[key] for key in fieldnames})

    return {
        "path": str(out_path),
        "relative_path": str(out_path.relative_to(root)) if _is_relative_to(out_path, root) else str(out_path),
        "method": DEFAULT_METHOD,
        "layer": layer,
        "contrast_uid": contrast_uid,
        "contrast_left": left_uid,
        "contrast_baseline": baseline_uid,
        "n_left": n_left,
        "n_baseline": n_baseline,
        "multiple_testing": "benjamini-hochberg",
        "has_padj": True,
        "columns": fieldnames,
        "skipped_cells": skipped_cells,
    }


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


def _resolve_output_path(root: Path, output_path: str | Path | None, contrast_uid: str) -> Path:
    if output_path is None:
        safe = "".join(ch if ch.isalnum() else "_" for ch in contrast_uid).strip("_") or "contrast"
        return (root / "outputs" / f"basic_de_{safe}.csv").resolve()
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


def _read_expression(
    path: Path,
    *,
    cell_id_column: str,
    gene_columns: list[str] | None,
) -> tuple[list[tuple[str, dict[str, float]]], list[str]]:
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


def _group_n(values_by_gene: dict[str, list[float]]) -> int:
    if not values_by_gene:
        return 0
    return max((len(values) for values in values_by_gene.values()), default=0)


def _format_number(value: float) -> str:
    return f"{value:.12g}"


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False