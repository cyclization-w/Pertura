from __future__ import annotations

import csv
import math
from pathlib import Path
from typing import Any

from pertura_gate.evidence.execution_ledger import canonical_execution_hash, file_sha256
from pertura_workflow.trusted_run import record_trusted_run

METHOD = "exploratory_normal_approximation"
RUNNER_NAME = "legacy_pseudobulk_normal_approximation"
RUNNER_VERSION = "legacy_pseudobulk_normal_approximation_v2"


def run_pseudobulk_de_for_registered_contrast(
    workspace: str | Path,
    *,
    expression_csv: str | Path,
    metadata_csv: str | Path,
    contrast_uid: str,
    left_uid: str,
    baseline_uid: str,
    replicate_column: str,
    layer: str,
    output_path: str | Path | None = None,
    cell_id_column: str = "cell_id",
    condition_column: str = "perturbation_uid",
    gene_columns: list[str] | None = None,
    pseudocount: float = 1e-9,
) -> dict[str, Any]:
    """Run the legacy exploratory replicate-mean normal approximation.

    This compatibility runner is deliberately *not* a trusted pseudobulk method.
    It averages cells within replicate units and applies a normal approximation;
    it is retained so historical recipes remain readable while the trusted edgeR
    capability is introduced. Its ledger entry is provenance only and cannot
    authorize a strong measured claim.
    """

    if not contrast_uid or not left_uid or not baseline_uid:
        raise ValueError("contrast_uid, left_uid, and baseline_uid are required")
    if not replicate_column:
        raise ValueError("replicate_column is required")
    if not layer:
        raise ValueError("layer declaration is required")

    root = Path(workspace).resolve()
    expression_path = _resolve_workspace_file(root, expression_csv)
    metadata_path = _resolve_workspace_file(root, metadata_csv)
    out_path = _resolve_output_path(root, output_path, contrast_uid)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    metadata = _read_metadata(
        metadata_path,
        cell_id_column=cell_id_column,
        condition_column=condition_column,
        replicate_column=replicate_column,
    )
    expression_rows, inferred_genes = _read_expression(expression_path, cell_id_column=cell_id_column, gene_columns=gene_columns)
    genes = list(gene_columns or inferred_genes)
    if not genes:
        raise ValueError("expression_csv must contain at least one numeric gene column")

    grouped: dict[tuple[str, str], dict[str, list[float]]] = {}
    cell_counts = {left_uid: 0, baseline_uid: 0}
    skipped_cells = 0
    for cell_id, values in expression_rows:
        info = metadata.get(cell_id)
        if info is None:
            skipped_cells += 1
            continue
        condition = info["condition"]
        if condition not in {left_uid, baseline_uid}:
            skipped_cells += 1
            continue
        replicate = info["replicate"]
        if not replicate:
            raise ValueError("replicate_column contains an empty replicate value")
        cell_counts[condition] += 1
        bucket = grouped.setdefault((condition, replicate), {gene: [] for gene in genes})
        for gene in genes:
            value = values.get(gene)
            if value is not None and math.isfinite(value):
                bucket[gene].append(value)

    left_reps = sorted({replicate for condition, replicate in grouped if condition == left_uid})
    baseline_reps = sorted({replicate for condition, replicate in grouped if condition == baseline_uid})
    if not left_reps or not baseline_reps:
        raise ValueError("pseudobulk DE requires replicate units for both left_uid and baseline_uid")
    if cell_counts[left_uid] <= 0 or cell_counts[baseline_uid] <= 0:
        raise ValueError("metadata/expression inputs must contain cells for both left_uid and baseline_uid")

    rows = []
    pvalues = []
    for gene in genes:
        left_values = [_mean(grouped[(left_uid, replicate)].get(gene, [])) for replicate in left_reps]
        baseline_values = [_mean(grouped[(baseline_uid, replicate)].get(gene, [])) for replicate in baseline_reps]
        mean_left = _mean(left_values)
        mean_baseline = _mean(baseline_values)
        effect = mean_left - mean_baseline
        logfc = math.log2((mean_left + pseudocount) / (mean_baseline + pseudocount)) if mean_left + pseudocount > 0 and mean_baseline + pseudocount > 0 else effect
        pvalue = _normal_approx_pvalue(left_values, baseline_values)
        pvalues.append(pvalue)
        rows.append({
            "gene": gene,
            "mean_left": mean_left,
            "mean_baseline": mean_baseline,
            "logfc": logfc,
            "effect_size": effect,
            "pvalue": pvalue,
            "n_left_replicates": len(left_values),
            "n_baseline_replicates": len(baseline_values),
            "n_left_cells": cell_counts[left_uid],
            "n_baseline_cells": cell_counts[baseline_uid],
        })

    padj = _benjamini_hochberg(pvalues)
    for row, adjusted in zip(rows, padj):
        row["padj"] = adjusted

    rows.sort(key=lambda item: (item["padj"], -abs(item["effect_size"]), item["gene"]))
    fieldnames = [
        "gene",
        "mean_left",
        "mean_baseline",
        "logfc",
        "effect_size",
        "pvalue",
        "padj",
        "n_left_replicates",
        "n_baseline_replicates",
        "n_left_cells",
        "n_baseline_cells",
    ]
    with out_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: _format_number(row[key]) if isinstance(row[key], float) else row[key] for key in fieldnames})

    input_hashes = {
        "expression_csv": file_sha256(expression_path),
        "metadata_csv": file_sha256(metadata_path),
    }
    parameters = {
        "contrast_uid": contrast_uid,
        "left_uid": left_uid,
        "baseline_uid": baseline_uid,
        "replicate_column": replicate_column,
        "layer": layer,
        "cell_id_column": cell_id_column,
        "condition_column": condition_column,
        "gene_columns": genes,
    }
    execution_hash = canonical_execution_hash({
        "runner_name": RUNNER_NAME,
        "runner_version": RUNNER_VERSION,
        "method": METHOD,
        "trust_level": "exploratory",
        "legacy": True,
        "limitations": [
            "replicate means are analysed with a normal approximation",
            "this result is not a trusted measured-association backend",
        ],
        "input_hashes": input_hashes,
        "parameters": parameters,
    })
    output_hashes = {"de_table": file_sha256(out_path)}
    ledger_record = record_trusted_run(
        root,
        execution_hash=execution_hash,
        runner_name=RUNNER_NAME,
        runner_version=RUNNER_VERSION,
        method=METHOD,
        input_hashes=input_hashes,
        output_hashes=output_hashes,
        parameters=parameters,
    )

    return {
        "path": str(out_path),
        "relative_path": str(out_path.relative_to(root)) if _is_relative_to(out_path, root) else str(out_path),
        "method": METHOD,
        "trust_level": "exploratory",
        "legacy": True,
        "limitations": [
            "replicate means are analysed with a normal approximation",
            "this result is not a trusted measured-association backend",
        ],
        "layer": layer,
        "contrast_uid": contrast_uid,
        "contrast_left": left_uid,
        "contrast_baseline": baseline_uid,
        "n_left": cell_counts[left_uid],
        "n_baseline": cell_counts[baseline_uid],
        "n_left_replicates": len(left_reps),
        "n_baseline_replicates": len(baseline_reps),
        "replicate_axis": replicate_column,
        "multiple_testing": "benjamini-hochberg",
        "has_padj": True,
        "columns": fieldnames,
        "skipped_cells": skipped_cells,
        "execution_hash": execution_hash,
        "execution_ledger_path": ledger_record["execution_ledger_path"],
        "execution_ledger_relative_path": ledger_record["execution_ledger_relative_path"],
        "input_hashes": input_hashes,
        "output_hashes": output_hashes,
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
        return (root / "outputs" / f"pseudobulk_de_{safe}.csv").resolve()
    candidate = Path(output_path)
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = candidate.resolve()
    if not _is_relative_to(resolved, root):
        raise ValueError(f"output_path is outside workspace: {output_path}")
    return resolved


def _read_metadata(path: Path, *, cell_id_column: str, condition_column: str, replicate_column: str) -> dict[str, dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        required = {cell_id_column, condition_column, replicate_column}
        if not reader.fieldnames or not required.issubset(set(reader.fieldnames)):
            raise ValueError(f"metadata_csv must include {cell_id_column!r}, {condition_column!r}, and {replicate_column!r}")
        mapping = {}
        for row in reader:
            cell_id = str(row.get(cell_id_column) or "").strip()
            if cell_id:
                mapping[cell_id] = {
                    "condition": str(row.get(condition_column) or "").strip(),
                    "replicate": str(row.get(replicate_column) or "").strip(),
                }
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


def _format_number(value: float) -> str:
    return f"{value:.12g}"


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False
