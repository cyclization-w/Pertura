from __future__ import annotations

import csv
import json
import os
import subprocess
from collections import Counter
from importlib import resources
from pathlib import Path
from typing import Any

from pertura_core import AnalysisStatus, CapabilityRunRequest, CapabilitySpec, DatasetContract, ResultEnvelope
from pertura_core.hashing import file_sha256
from pertura_workflow.environment import PROFILE, doctor_environment, environment_lock, environment_prefix, micromamba_path


def run_edger_pseudobulk(
    spec: CapabilitySpec,
    request: CapabilityRunRequest,
    contract: DatasetContract,
    staging: Path,
) -> ResultEnvelope:
    doctor = doctor_environment(PROFILE)
    if not doctor["ok"]:
        return _blocked(spec, request, contract, tuple(doctor["problems"]), {"setup_command": f"pertura env setup {PROFILE}"})
    lock = environment_lock(PROFILE)
    params = request.parameters
    counts_path = _resolve_input(contract, params.get("counts_path"))
    metadata_path = _resolve_input(contract, params.get("metadata_path"))
    gene_column = str(params.get("gene_column") or "gene")
    cell_column = str(params.get("cell_column") or "cell_id")
    condition_column = str(params.get("condition_column") or "condition")
    replicate_column = str(params.get("replicate_column") or "replicate")
    state_column = str(params.get("state_column") or "") or None
    target = str(params.get("target_condition") or "")
    baseline = str(params.get("baseline_condition") or "")
    covariates = [str(item) for item in params.get("covariates") or []]
    paired = bool(params.get("paired", False))
    minimum_cells = int(params.get("minimum_cells_per_pseudobulk", 10))
    if not target or not baseline:
        raise ValueError("target_condition and baseline_condition are required")
    _validate_column_names([gene_column, cell_column, condition_column, replicate_column, *(covariates or []), *(state_column and [state_column] or [])])

    cells, genes, matrix = _read_counts(counts_path, gene_column)
    metadata = _read_metadata(metadata_path, cell_column)
    if set(cells) - set(metadata):
        raise ValueError("count matrix contains cells absent from metadata")
    selected_cells = [cell for cell in cells if metadata[cell].get(condition_column) in {target, baseline}]
    if not selected_cells:
        return _blocked(spec, request, contract, ("no cells belong to the requested contrast",), {})

    sample_keys: dict[tuple[str, ...], list[str]] = {}
    for cell in selected_cells:
        row = metadata[cell]
        replicate = row.get(replicate_column, "").strip()
        condition = row.get(condition_column, "").strip()
        if not replicate:
            return _blocked(spec, request, contract, ("replicate identity is missing for at least one selected cell",), {})
        state = row.get(state_column, "").strip() if state_column else "all"
        covariate_values = tuple(row.get(column, "").strip() for column in covariates)
        key = (replicate, condition, state, *covariate_values)
        sample_keys.setdefault(key, []).append(cell)

    sample_rows = []
    kept_keys = []
    for key, members in sorted(sample_keys.items()):
        if len(members) < minimum_cells:
            continue
        replicate, condition, state, *covariate_values = key
        sample_id = f"pb_{len(sample_rows) + 1:04d}"
        row = {"sample_id": sample_id, "replicate": replicate, "condition": condition, "state": state, "n_cells": len(members)}
        row.update({column: value for column, value in zip(covariates, covariate_values)})
        sample_rows.append(row)
        kept_keys.append((key, members, sample_id))
    condition_units = {
        condition: {row["replicate"] for row in sample_rows if row["condition"] == condition}
        for condition in (target, baseline)
    }
    paired_units = condition_units[target] & condition_units[baseline]
    blockers: list[str] = []
    if min(len(condition_units[target]), len(condition_units[baseline])) < 2:
        blockers.append("fewer than two independent units are available in at least one contrast arm")
    if paired and len(paired_units) < 2:
        blockers.append("paired design has fewer than two paired units")
    if paired and (condition_units[target] != condition_units[baseline]):
        blockers.append("paired design lacks complete replicate overlap")
    for covariate in covariates:
        by_condition = {
            condition: {row[covariate] for row in sample_rows if row["condition"] == condition}
            for condition in (target, baseline)
        }
        if by_condition[target].isdisjoint(by_condition[baseline]):
            blockers.append(f"condition is completely confounded with covariate {covariate}")
    if blockers:
        return _blocked(spec, request, contract, tuple(blockers), {
            "n_independent_units_per_arm": min(len(condition_units[target]), len(condition_units[baseline])),
            "n_paired_units": len(paired_units),
        })

    cell_index = {cell: index for index, cell in enumerate(cells)}
    aggregate: list[list[int]] = []
    for _, members, _ in kept_keys:
        indices = [cell_index[cell] for cell in members]
        aggregate.append([sum(matrix[gene_index][cell_index_] for cell_index_ in indices) for gene_index in range(len(genes))])
    counts_out = staging / "pseudobulk_counts.csv"
    samples_out = staging / "pseudobulk_samples.csv"
    config_out = staging / "edger_config.json"
    _write_aggregated_counts(counts_out, genes, sample_rows, aggregate)
    _write_rows(samples_out, sample_rows)
    config = {
        "baseline": baseline,
        "target": target,
        "paired": paired,
        "covariates": covariates,
        "counts_path": str(counts_out),
        "samples_path": str(samples_out),
        "result_path": str(staging / "edger_results.csv"),
        "design_path": str(staging / "design_matrix.csv"),
        "mds_path": str(staging / "mds.csv"),
        "environment_path": str(staging / "r_environment.json"),
    }
    config_out.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    script = resources.files("pertura_workflow.capabilities").joinpath("runners", "edger_ql.R")
    command = [
        str(micromamba_path()), "run", "--prefix", str(environment_prefix()),
        "Rscript", str(script), str(config_out),
    ]
    completed = subprocess.run(command, text=True, capture_output=True, timeout=spec.timeout_seconds, check=False, env=_minimal_env())
    if completed.returncode != 0:
        raise RuntimeError("edgeR runner failed: " + completed.stderr[-4000:])
    environment_lock_out = staging / "environment_lock.json"
    environment_lock_out.write_text(json.dumps(lock, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    outputs = ("edger_results.csv", "pseudobulk_samples.csv", "design_matrix.csv", "mds.csv", "r_environment.json", "environment_lock.json")
    for name in outputs:
        if not (staging / name).is_file():
            raise RuntimeError(f"edgeR runner omitted required output: {name}")
    independent = min(len(condition_units[target]), len(condition_units[baseline]))
    strict_units = len(paired_units) if paired else independent
    status = AnalysisStatus.completed if strict_units >= 3 else AnalysisStatus.completed_with_caution
    cautions = () if strict_units >= 3 else ("only two independent units support each arm; result is exploratory",)
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
        summary=f"edgeR QL pseudobulk completed for {target} versus {baseline} across {strict_units} independent units.",
        cautions=cautions,
        metrics={
            "n_independent_units_per_arm": independent,
            "n_paired_units": len(paired_units),
            "n_pseudobulk_samples": len(sample_rows),
            "minimum_cells_per_pseudobulk": minimum_cells,
            "paired": paired,
            "method": "edgeR_QL",
        },
        output_paths=outputs,
        output_hashes={name: file_sha256(staging / name) for name in outputs},
        dependencies=request.dependencies,
        metadata={
            "design": "paired_donor_plus_condition" if paired else "covariates_plus_condition",
            "environment_profile": PROFILE,
            "environment_lock_hash": lock["lock_hash"],
        },
    )


def _blocked(spec: CapabilitySpec, request: CapabilityRunRequest, contract: DatasetContract, blockers: tuple[str, ...], metrics: dict[str, Any]) -> ResultEnvelope:
    return ResultEnvelope(
        run_id=request.run_id,
        request_id=request.request_id,
        capability_id=spec.capability_id,
        capability_version=spec.version,
        capability_trust=spec.trust_level,
        contract_id=contract.contract_id,
        contract_hash=contract.canonical_hash,
        scope=request.scope,
        status=AnalysisStatus.blocked,
        result_kind=spec.output_kind,
        source_class=spec.source_class,
        summary="edgeR pseudobulk was blocked before statistical execution.",
        blockers=blockers,
        metrics=metrics,
        dependencies=request.dependencies,
    )


def _resolve_input(contract: DatasetContract, value: Any) -> Path:
    if value in (None, ""):
        raise ValueError("edgeR capability is missing a required input path")
    candidate = Path(str(value)).expanduser()
    roots = [Path(item).expanduser().resolve() for item in contract.source_paths]
    if not candidate.is_absolute():
        directories = [item for item in roots if item.is_dir()]
        if not directories:
            raise ValueError("relative edgeR input requires a directory DatasetContract source")
        candidate = directories[0] / candidate
    resolved = candidate.resolve()
    if not any(resolved == root or (root.is_dir() and root in resolved.parents) for root in roots):
        raise ValueError("edgeR input is not bound to the authoritative DatasetContract")
    if not resolved.is_file():
        raise FileNotFoundError(resolved)
    return resolved


def _read_counts(path: Path, gene_column: str) -> tuple[list[str], list[str], list[list[int]]]:
    delimiter = "\t" if path.suffix.lower() in {".tsv", ".txt"} else ","
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter=delimiter)
        if not reader.fieldnames or gene_column not in reader.fieldnames:
            raise ValueError(f"count matrix must contain gene column {gene_column}")
        cells = [item for item in reader.fieldnames if item != gene_column]
        genes: list[str] = []
        matrix: list[list[int]] = []
        for row in reader:
            gene = str(row.get(gene_column) or "").strip()
            if not gene:
                continue
            values = []
            for cell in cells:
                number = float(row.get(cell) or 0)
                if number < 0 or not number.is_integer():
                    raise ValueError("edgeR requires raw nonnegative integer counts")
                values.append(int(number))
            genes.append(gene)
            matrix.append(values)
    return cells, genes, matrix


def _read_metadata(path: Path, cell_column: str) -> dict[str, dict[str, str]]:
    delimiter = "\t" if path.suffix.lower() in {".tsv", ".txt"} else ","
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter=delimiter)
        if not reader.fieldnames or cell_column not in reader.fieldnames:
            raise ValueError(f"metadata must contain cell column {cell_column}")
        return {str(row[cell_column]).strip(): {key: str(value or "").strip() for key, value in row.items()} for row in reader if str(row.get(cell_column) or "").strip()}


def _write_aggregated_counts(path: Path, genes: list[str], samples: list[dict[str, Any]], aggregate: list[list[int]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["gene", *(row["sample_id"] for row in samples)])
        for gene_index, gene in enumerate(genes):
            writer.writerow([gene, *(sample[gene_index] for sample in aggregate)])


def _write_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = list(rows[0]) if rows else []
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _validate_column_names(columns: list[str]) -> None:
    for column in columns:
        if not column.replace("_", "").isalnum():
            raise ValueError(f"unsafe metadata column name: {column}")


def _minimal_env() -> dict[str, str]:
    allowed = ("SYSTEMROOT", "WINDIR", "TEMP", "TMP", "HOME", "USERPROFILE", "PATH")
    return {key: os.environ[key] for key in allowed if key in os.environ}
