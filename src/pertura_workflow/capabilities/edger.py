from __future__ import annotations

import csv
import json
import math
import os
import subprocess
from collections import Counter
from importlib import resources
from pathlib import Path
from typing import Any

from pertura_core import AnalysisStatus, CapabilityRunRequest, CapabilitySpec, DatasetContract, ResultEnvelope
from pertura_core.hashing import file_sha256
from pertura_workflow.capabilities.dependency_inputs import (
    apply_retained_cells,
    dependency_grounding_metadata,
    retained_cells_for_request,
)
from pertura_workflow.capabilities.execution_context import authoritative_input_roots
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
    retained_cells = retained_cells_for_request(staging, request, required=True)
    contrast_cells = [cell for cell in cells if metadata[cell].get(condition_column) in {target, baseline}]
    selected_cells = apply_retained_cells(contrast_cells, retained_cells)
    grounding = dependency_grounding_metadata(retained_cells, selected_cells)
    if not selected_cells:
        return _blocked(
            spec,
            request,
            contract,
            ("no retained cells belong to the requested contrast",),
            grounding,
        )

    sample_keys: dict[tuple[str, ...], list[str]] = {}
    for cell in selected_cells:
        row = metadata[cell]
        replicate = row.get(replicate_column, "").strip()
        condition = row.get(condition_column, "").strip()
        if not replicate:
            return _blocked(
                spec,
                request,
                contract,
                ("replicate identity is missing for at least one selected cell",),
                grounding,
            )
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
            **grounding,
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
    output_validation = _validate_edger_outputs(
        staging,
        baseline=baseline,
        target=target,
        expected_environment_lock=lock,
    )
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
            **grounding,
            **output_validation,
        },
        output_paths=outputs,
        output_hashes={name: file_sha256(staging / name) for name in outputs},
        dependencies=request.dependencies,
        metadata={
            "design": "paired_donor_plus_condition" if paired else "covariates_plus_condition",
            "environment_profile": PROFILE,
            "environment_lock_hash": lock["lock_hash"],
            "execution_grounding": grounding,
        },
    )


_RESULT_COLUMNS = ("gene", "logFC", "F", "PValue", "FDR", "dispersion")
_SAMPLE_COLUMNS = ("sample_id", "replicate", "condition", "state", "n_cells")
_MDS_COLUMNS = ("sample_id", "leading_logFC_1", "leading_logFC_2")
_R_ENVIRONMENT_KEYS = ("R", "Bioconductor", "edgeR", "limma", "jsonlite")


def _validate_edger_outputs(
    staging: Path,
    *,
    baseline: str,
    target: str,
    expected_environment_lock: dict[str, Any],
) -> dict[str, int]:
    """Fail closed on malformed or scientifically unbound edgeR outputs."""

    sample_fields, sample_rows = _read_strict_csv(
        staging / "pseudobulk_samples.csv", "pseudobulk sample manifest"
    )
    _require_columns(sample_fields, _SAMPLE_COLUMNS, "pseudobulk sample manifest")
    if not sample_rows:
        _invalid_output("pseudobulk sample manifest is empty")
    sample_ids = [row["sample_id"] for row in sample_rows]
    _require_unique_nonempty(sample_ids, "pseudobulk sample_id")
    conditions = {row["condition"] for row in sample_rows}
    if conditions != {baseline, target}:
        _invalid_output(
            "pseudobulk sample conditions do not exactly match the requested contrast"
        )
    for row in sample_rows:
        if not row["replicate"] or not row["state"]:
            _invalid_output("pseudobulk samples require replicate and state identities")
        n_cells = _finite_number(row["n_cells"], "pseudobulk n_cells")
        if not n_cells.is_integer() or n_cells <= 0:
            _invalid_output("pseudobulk n_cells must be a positive integer")

    count_genes = _validate_pseudobulk_counts(
        staging / "pseudobulk_counts.csv", sample_ids
    )

    result_fields, result_rows = _read_strict_csv(
        staging / "edger_results.csv", "edgeR result table"
    )
    _require_columns(result_fields, _RESULT_COLUMNS, "edgeR result table")
    if not result_rows:
        _invalid_output("edgeR result table is empty")
    result_genes = [row["gene"] for row in result_rows]
    _require_unique_nonempty(result_genes, "edgeR result gene")
    if not set(result_genes).issubset(count_genes):
        _invalid_output("edgeR result contains genes absent from pseudobulk counts")
    for row in result_rows:
        gene = row["gene"]
        _finite_number(row["logFC"], f"edgeR logFC for gene {gene}")
        f_statistic = _finite_number(row["F"], f"edgeR F for gene {gene}")
        p_value = _finite_number(row["PValue"], f"edgeR PValue for gene {gene}")
        fdr = _finite_number(row["FDR"], f"edgeR FDR for gene {gene}")
        dispersion = _finite_number(
            row["dispersion"], f"edgeR dispersion for gene {gene}"
        )
        if f_statistic < 0:
            _invalid_output(
                f"edgeR F must be nonnegative for gene {gene}: {f_statistic}"
            )
        if dispersion < 0:
            _invalid_output(
                "edgeR dispersion must be nonnegative "
                f"for gene {gene}: {dispersion}"
            )
        if not (0 <= p_value <= 1) or not (0 <= fdr <= 1):
            _invalid_output("edgeR PValue and FDR must lie in [0, 1]")

    design_fields, design_rows = _read_strict_csv(
        staging / "design_matrix.csv", "edgeR design matrix"
    )
    if not design_fields or design_fields[0] != "sample_id":
        _invalid_output("edgeR design matrix must begin with sample_id")
    design_columns = list(design_fields[1:])
    if not design_columns:
        _invalid_output("edgeR design matrix contains no model columns")
    if [row["sample_id"] for row in design_rows] != sample_ids:
        _invalid_output("edgeR design matrix does not align with pseudobulk samples")
    condition_columns = [name for name in design_columns if name.startswith("condition")]
    if len(condition_columns) != 1:
        _invalid_output("edgeR design matrix lacks a unique condition contrast column")
    design_values = [
        [_finite_number(row[column], f"edgeR design value {column}") for column in design_columns]
        for row in design_rows
    ]
    if _matrix_rank(design_values) != len(design_columns):
        _invalid_output("edgeR design matrix is not full rank")

    mds_fields, mds_rows = _read_strict_csv(staging / "mds.csv", "edgeR MDS table")
    _require_columns(mds_fields, _MDS_COLUMNS, "edgeR MDS table")
    if [row["sample_id"] for row in mds_rows] != sample_ids:
        _invalid_output("edgeR MDS table does not align with pseudobulk samples")
    for row in mds_rows:
        _finite_number(row["leading_logFC_1"], "edgeR MDS coordinate 1")
        _finite_number(row["leading_logFC_2"], "edgeR MDS coordinate 2")

    try:
        r_environment = json.loads(
            (staging / "r_environment.json").read_text(encoding="utf-8")
        )
        observed_lock = json.loads(
            (staging / "environment_lock.json").read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError) as exc:
        _invalid_output(f"edgeR environment metadata is invalid JSON: {exc}")
    if not isinstance(r_environment, dict):
        _invalid_output("edgeR R environment metadata must be an object")
    for key in _R_ENVIRONMENT_KEYS:
        if not isinstance(r_environment.get(key), str) or not r_environment[key].strip():
            _invalid_output(f"edgeR R environment is missing {key}")
    session_info = r_environment.get("sessionInfo")
    if not (
        (isinstance(session_info, str) and session_info.strip())
        or (
            isinstance(session_info, list)
            and session_info
            and all(isinstance(item, str) and item.strip() for item in session_info)
        )
    ):
        _invalid_output("edgeR R environment is missing sessionInfo")
    expected_versions = expected_environment_lock.get("expected_versions") or {}
    for key, expected in expected_versions.items():
        if str(r_environment.get(key) or "") != str(expected):
            _invalid_output(f"edgeR R environment version mismatch for {key}")
    if observed_lock != expected_environment_lock:
        _invalid_output("edgeR environment lock output does not match the executed lock")

    return {
        "validated_result_gene_count": len(result_rows),
        "validated_pseudobulk_sample_count": len(sample_rows),
        "validated_design_column_count": len(design_columns),
    }


def _validate_pseudobulk_counts(path: Path, sample_ids: list[str]) -> set[str]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fields = tuple(reader.fieldnames or ())
        if len(fields) != len(set(fields)) or not fields or fields[0] != "gene":
            _invalid_output("pseudobulk count matrix has an invalid or duplicate header")
        if list(fields[1:]) != sample_ids:
            _invalid_output("pseudobulk count matrix does not align with sample manifest")
        genes: set[str] = set()
        for row in reader:
            if None in row:
                _invalid_output("pseudobulk count matrix contains extra fields")
            gene = str(row.get("gene") or "").strip()
            if not gene or gene in genes:
                _invalid_output("pseudobulk count genes must be nonempty and unique")
            genes.add(gene)
            for sample_id in sample_ids:
                value = _finite_number(row.get(sample_id), "pseudobulk count")
                if value < 0 or not value.is_integer():
                    _invalid_output("pseudobulk counts must be nonnegative integers")
    if not genes:
        _invalid_output("pseudobulk count matrix is empty")
    return genes


def _read_strict_csv(path: Path, label: str) -> tuple[tuple[str, ...], list[dict[str, str]]]:
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            fields = tuple(reader.fieldnames or ())
            if not fields or any(not field for field in fields):
                _invalid_output(f"{label} has an empty header")
            if len(fields) != len(set(fields)):
                _invalid_output(f"{label} has duplicate columns")
            rows: list[dict[str, str]] = []
            for row in reader:
                if None in row:
                    _invalid_output(f"{label} contains extra fields")
                normalized = {
                    field: str(row.get(field) or "").strip() for field in fields
                }
                if not any(normalized.values()):
                    _invalid_output(f"{label} contains an empty row")
                rows.append(normalized)
            return fields, rows
    except OSError as exc:
        _invalid_output(f"{label} cannot be read: {exc}")


def _require_columns(
    observed: tuple[str, ...], required: tuple[str, ...], label: str
) -> None:
    missing = sorted(set(required) - set(observed))
    if missing:
        _invalid_output(f"{label} is missing required columns: {', '.join(missing)}")


def _require_unique_nonempty(values: list[str], label: str) -> None:
    if any(not value for value in values):
        _invalid_output(f"{label} contains an empty identity")
    duplicates = sorted(value for value, count in Counter(values).items() if count > 1)
    if duplicates:
        _invalid_output(f"{label} contains duplicate identities: {', '.join(duplicates[:5])}")


def _finite_number(value: Any, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        _invalid_output(f"{label} is not numeric")
    if not math.isfinite(number):
        _invalid_output(f"{label} is not finite")
    return number


def _matrix_rank(matrix: list[list[float]]) -> int:
    if not matrix or not matrix[0]:
        return 0
    work = [list(row) for row in matrix]
    rows = len(work)
    columns = len(work[0])
    scale = max((abs(value) for row in work for value in row), default=1.0)
    tolerance = max(1.0, scale) * 1e-10
    rank = 0
    for column in range(columns):
        pivot = max(range(rank, rows), key=lambda row: abs(work[row][column]))
        if abs(work[pivot][column]) <= tolerance:
            continue
        work[rank], work[pivot] = work[pivot], work[rank]
        pivot_value = work[rank][column]
        for candidate in range(rank + 1, rows):
            factor = work[candidate][column] / pivot_value
            if abs(factor) <= tolerance:
                continue
            for remaining in range(column, columns):
                work[candidate][remaining] -= factor * work[rank][remaining]
        rank += 1
        if rank == rows:
            break
    return rank


def _invalid_output(message: str) -> None:
    raise RuntimeError(f"edgeR output validation failed: {message}")


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
    roots = authoritative_input_roots(contract)
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
