from __future__ import annotations

import csv
import json
import math
import random
from pathlib import Path
from statistics import median
from typing import Any

from pertura_core import AnalysisStatus, CapabilityRunRequest, CapabilitySpec, DatasetContract, ResultEnvelope
from pertura_core.hashing import file_sha256


def run_replicate_null_calibration(
    spec: CapabilitySpec,
    request: CapabilityRunRequest,
    contract: DatasetContract,
    staging: Path,
) -> ResultEnvelope:
    params = request.parameters
    counts_path = _resolve_input(contract, params.get("counts_path"))
    metadata_path = _resolve_input(contract, params.get("metadata_path"))
    gene_column = str(params.get("gene_column") or "gene")
    cell_column = str(params.get("cell_column") or "cell_id")
    condition_column = str(params.get("condition_column") or "condition")
    replicate_column = str(params.get("replicate_column") or "replicate")
    target = str(params.get("target_condition") or "")
    baseline = str(params.get("baseline_condition") or "")
    ntc_conditions = [str(item) for item in params.get("negative_control_conditions") or []]
    permutations = int(params.get("permutations", 200))
    if not target or not baseline:
        raise ValueError("target_condition and baseline_condition are required")
    if permutations < 20:
        raise ValueError("at least 20 replicate-label permutations are required")
    cells, genes, counts = _read_counts(counts_path, gene_column)
    metadata = _read_metadata(metadata_path, cell_column)
    if set(cells) - set(metadata):
        raise ValueError("count matrix contains cells absent from metadata")
    sample_vectors: dict[tuple[str, str], list[int]] = {}
    cell_index = {cell: index for index, cell in enumerate(cells)}
    for cell in cells:
        condition = metadata[cell].get(condition_column, "")
        replicate = metadata[cell].get(replicate_column, "")
        if not condition or not replicate:
            continue
        key = (replicate, condition)
        vector = sample_vectors.setdefault(key, [0] * len(genes))
        index = cell_index[cell]
        for gene_index in range(len(genes)):
            vector[gene_index] += counts[gene_index][index]
    normalized = {key: _log_cpm(vector) for key, vector in sample_vectors.items()}
    target_units = sorted(replicate for replicate, condition in normalized if condition == target)
    baseline_units = sorted(replicate for replicate, condition in normalized if condition == baseline)
    if len(target_units) < 2 or len(baseline_units) < 2:
        return _blocked(spec, request, contract, ("calibration needs at least two replicate units in both contrast arms",))

    observed_effects = _contrast_effects(normalized, target_units, target, baseline_units, baseline, len(genes))
    unit_rows = [(replicate, target, normalized[(replicate, target)]) for replicate in target_units]
    unit_rows += [(replicate, baseline, normalized[(replicate, baseline)]) for replicate in baseline_units]
    rng = random.Random(1729)
    permuted_median_abs = []
    labels = [row[1] for row in unit_rows]
    for _ in range(permutations):
        shuffled = list(labels)
        rng.shuffle(shuffled)
        left = [row[2] for row, label in zip(unit_rows, shuffled) if label == target]
        right = [row[2] for row, label in zip(unit_rows, shuffled) if label == baseline]
        if not left or not right:
            continue
        effects = [_mean([row[index] for row in left]) - _mean([row[index] for row in right]) for index in range(len(genes))]
        permuted_median_abs.append(median(abs(value) for value in effects))

    ntc_summary: dict[str, Any]
    if len(ntc_conditions) >= 2:
        left_label, right_label = ntc_conditions[:2]
        left_units = sorted(replicate for replicate, condition in normalized if condition == left_label)
        right_units = sorted(replicate for replicate, condition in normalized if condition == right_label)
        if len(left_units) >= 2 and len(right_units) >= 2:
            ntc_effects = _contrast_effects(normalized, left_units, left_label, right_units, right_label, len(genes))
            ntc_summary = {
                "status": "estimated",
                "conditions": [left_label, right_label],
                "n_units": [len(left_units), len(right_units)],
                "median_absolute_effect": median(abs(value) for value in ntc_effects),
                "p95_absolute_effect": _quantile([abs(value) for value in ntc_effects], 0.95),
            }
        else:
            ntc_summary = {"status": "unresolved", "reason": "negative-control labels lack two replicate units each"}
    else:
        ntc_summary = {"status": "unresolved", "reason": "two negative-control condition labels were not provided"}

    observed_median = median(abs(value) for value in observed_effects)
    permutation_p = (1 + sum(value >= observed_median for value in permuted_median_abs)) / (1 + len(permuted_median_abs))
    permutation_summary = {
        "status": "estimated",
        "permutation_unit": "replicate_label",
        "n_permutations": len(permuted_median_abs),
        "observed_median_absolute_effect": observed_median,
        "null_median_absolute_effect_p95": _quantile(permuted_median_abs, 0.95),
        "empirical_p": permutation_p,
    }
    cautions = []
    if ntc_summary["status"] == "unresolved":
        cautions.append("NTC-vs-NTC calibration is unresolved")
    passed = ntc_summary["status"] == "estimated" and permutation_summary["status"] == "estimated"
    status = AnalysisStatus.completed if passed else AnalysisStatus.completed_with_caution
    payload = {
        "schema_version": "pertura-replicate-null-calibration-v1",
        "status": status.value,
        "label_permutation": permutation_summary,
        "ntc_vs_ntc": ntc_summary,
        "replicate_label_permutation_only": True,
        "cell_label_permutation_performed": False,
        "passed": passed,
        "cautions": cautions,
    }
    output = staging / "replicate_null_calibration.json"
    output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return ResultEnvelope(
        run_id=request.run_id, request_id=request.request_id, capability_id=spec.capability_id,
        capability_version=spec.version, capability_trust=spec.trust_level,
        contract_id=contract.contract_id, contract_hash=contract.canonical_hash, scope=request.scope,
        status=status, result_kind=spec.output_kind, source_class=spec.source_class,
        summary="Replicate-level NTC and label-permutation calibration completed.",
        cautions=tuple(cautions), metrics={"passed": passed, "permutation_unit": "replicate_label", "n_permutations": len(permuted_median_abs), "ntc_status": ntc_summary["status"]},
        output_paths=(output.name,), output_hashes={output.name: file_sha256(output)},
        dependencies=request.dependencies, metadata={"cell_level_permutation": False},
    )


def _blocked(spec: CapabilitySpec, request: CapabilityRunRequest, contract: DatasetContract, blockers: tuple[str, ...]) -> ResultEnvelope:
    return ResultEnvelope(
        run_id=request.run_id, request_id=request.request_id, capability_id=spec.capability_id,
        capability_version=spec.version, capability_trust=spec.trust_level,
        contract_id=contract.contract_id, contract_hash=contract.canonical_hash, scope=request.scope,
        status=AnalysisStatus.blocked, result_kind=spec.output_kind, source_class=spec.source_class,
        summary="Replicate-level null calibration was blocked.", blockers=blockers, dependencies=request.dependencies,
    )


def _resolve_input(contract: DatasetContract, value: Any) -> Path:
    if value in (None, ""):
        raise ValueError("calibration capability is missing a required input path")
    candidate = Path(str(value)).expanduser()
    roots = [Path(item).expanduser().resolve() for item in contract.source_paths]
    if not candidate.is_absolute():
        directories = [item for item in roots if item.is_dir()]
        if not directories:
            raise ValueError("relative calibration input requires a directory DatasetContract source")
        candidate = directories[0] / candidate
    resolved = candidate.resolve()
    if not any(resolved == root or (root.is_dir() and root in resolved.parents) for root in roots):
        raise ValueError("calibration input is not bound to DatasetContract")
    return resolved


def _read_counts(path: Path, gene_column: str) -> tuple[list[str], list[str], list[list[int]]]:
    delimiter = "\t" if path.suffix.lower() in {".tsv", ".txt"} else ","
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter=delimiter)
        if not reader.fieldnames or gene_column not in reader.fieldnames:
            raise ValueError(f"count matrix must contain {gene_column}")
        cells = [item for item in reader.fieldnames if item != gene_column]
        genes, matrix = [], []
        for row in reader:
            genes.append(str(row[gene_column]))
            values = []
            for cell in cells:
                number = float(row.get(cell) or 0)
                if number < 0 or not number.is_integer():
                    raise ValueError("calibration requires nonnegative integer counts")
                values.append(int(number))
            matrix.append(values)
    return cells, genes, matrix


def _read_metadata(path: Path, cell_column: str) -> dict[str, dict[str, str]]:
    delimiter = "\t" if path.suffix.lower() in {".tsv", ".txt"} else ","
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter=delimiter)
        if not reader.fieldnames or cell_column not in reader.fieldnames:
            raise ValueError(f"metadata must contain {cell_column}")
        return {str(row[cell_column]): {key: str(value or "") for key, value in row.items()} for row in reader}


def _log_cpm(vector: list[int]) -> list[float]:
    library = sum(vector)
    return [math.log2(value / library * 1e6 + 0.5) if library else 0.0 for value in vector]


def _contrast_effects(normalized: dict[tuple[str, str], list[float]], left_units: list[str], left_label: str, right_units: list[str], right_label: str, n_genes: int) -> list[float]:
    return [
        _mean([normalized[(unit, left_label)][index] for unit in left_units])
        - _mean([normalized[(unit, right_label)][index] for unit in right_units])
        for index in range(n_genes)
    ]


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _quantile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[int(quantile * (len(ordered) - 1))]
