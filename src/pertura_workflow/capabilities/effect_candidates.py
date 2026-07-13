from __future__ import annotations

import csv
import json
import math
import os
import subprocess
from collections import Counter, defaultdict
from importlib import resources
from pathlib import Path
from typing import Any

import numpy as np

from pertura_core import AnalysisStatus, CapabilityRunRequest, CapabilitySpec, DatasetContract

from pertura_workflow.capabilities.candidate_common import (
    blocked,
    dependency_results,
    envelope,
    read_rows,
    resolve_input,
    write_json,
)
from pertura_workflow.environment import environment_prefix, micromamba_path
from pertura_workflow.capabilities.dependency_inputs import retained_cells_for_request


def run_sceptre_association(
    spec: CapabilitySpec,
    request: CapabilityRunRequest,
    contract: DatasetContract,
    staging: Path,
):
    moi = str(request.parameters.get("moi") or "high").lower()
    if moi != "high":
        return blocked(spec, request, contract, "association.sceptre.v1 is reserved for high-MOI designs")
    required_names = (
        "response_matrix_path",
        "guide_matrix_path",
        "guide_target_map_path",
        "discovery_pairs_path",
    )
    paths: dict[str, Path] = {}
    for name in required_names:
        value = request.parameters.get(name)
        if not value:
            return blocked(spec, request, contract, f"{name} is required")
        resolved = resolve_input(contract, value, label=name)
        assert resolved is not None
        paths[name] = resolved
    optional_names = ("response_ids_path", "guide_ids_path", "cell_ids_path", "covariates_path")
    for name in optional_names:
        if request.parameters.get(name):
            resolved = resolve_input(contract, request.parameters[name], label=name)
            assert resolved is not None
            paths[name] = resolved
    retained = retained_cells_for_request(staging, request, required=True)
    retained_path = staging / "sceptre_retained_cells.txt"
    if retained is not None:
        retained_path.write_text(
            "\n".join(sorted(retained)) + "\n",
            encoding="utf-8",
        )
    config = {
        "schema_version": "pertura-sceptre-run-config-v1",
        **{key: str(value) for key, value in paths.items()},
        "output_dir": str(staging),
        "retained_cell_ids_path": str(retained_path) if retained is not None else None,
        "moi": "high",
        "side": str(request.parameters.get("side") or "both"),
        "grna_integration_strategy": str(
            request.parameters.get("grna_integration_strategy") or "union"
        ),
        "assignment_method": str(request.parameters.get("assignment_method") or "mixture"),
        "multiple_testing_alpha": float(request.parameters.get("multiple_testing_alpha", 0.10)),
        "calibration_type1_threshold": float(
            request.parameters.get("calibration_type1_threshold", 0.10)
        ),
        "n_calibration_pairs": int(request.parameters.get("n_calibration_pairs", 500)),
        "calibration_group_size": int(request.parameters.get("calibration_group_size", 2)),
        "parallel": False,
        "n_processors": 1,
        "seed": 1729,
    }
    config_path = write_json(staging, "sceptre_config.json", config)
    completed = _run_r_profile(
        "sceptre-v1",
        resources.files("pertura_workflow.capabilities").joinpath(
            "runners", "sceptre_association.R"
        ),
        config_path,
        timeout=int(request.parameters.get("timeout_seconds", spec.timeout_seconds)),
    )
    if isinstance(completed, str):
        return blocked(
            spec,
            request,
            contract,
            completed,
            metadata={"setup_command": "pertura env setup sceptre-v1"},
        )
    if completed.returncode != 0:
        return blocked(
            spec,
            request,
            contract,
            "SCEPTRE runner failed: " + (completed.stderr or completed.stdout)[-2000:],
        )
    metadata_path = staging / "sceptre_metadata.json"
    calibration_path = staging / "sceptre_calibration.csv"
    results_path = staging / "sceptre_results.csv"
    if not metadata_path.is_file() or not calibration_path.is_file():
        return blocked(spec, request, contract, "SCEPTRE runner returned an incomplete output set")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    calibration_passed = bool(metadata.get("calibration_passed"))
    if not calibration_passed:
        return envelope(
            spec,
            request,
            contract,
            status=AnalysisStatus.blocked,
            summary="SCEPTRE calibration failed; discovery analysis was not accepted.",
            blockers=("SCEPTRE negative-control calibration exceeded the configured threshold",),
            metrics={
                "calibration_type1_rate": metadata.get("calibration_type1_rate"),
                "calibration_passed": False,
            },
            outputs=(config_path, calibration_path, metadata_path),
            metadata={"discovery_executed": bool(metadata.get("discovery_executed"))},
        )
    if not results_path.is_file():
        return blocked(spec, request, contract, "SCEPTRE calibration passed but discovery output is missing")
    fields, rows = read_rows(results_path)
    required_columns = {
        "response_id",
        "grna_target",
        "p_value",
        "fold_change",
        "se_fold_change",
        "FDR",
    }
    if not required_columns.issubset(fields):
        return blocked(
            spec,
            request,
            contract,
            "SCEPTRE result is missing columns: " + ", ".join(sorted(required_columns - set(fields))),
        )
    duplicates = len({(row["response_id"], row["grna_target"]) for row in rows}) != len(rows)
    invalid = any(
        not _finite_probability(row["p_value"]) or not _finite_probability(row["FDR"])
        for row in rows
    )
    if duplicates or invalid:
        return blocked(spec, request, contract, "SCEPTRE output has duplicate pairs or invalid probability values")
    return envelope(
        spec,
        request,
        contract,
        status=AnalysisStatus.completed_with_caution,
        summary=f"SCEPTRE completed {len(rows)} discovery-pair tests after calibration.",
        cautions=(
            "SCEPTRE adapter is synthetic-only validated and cannot support production measured claims",
        ),
        metrics={
            "n_pairs": len(rows),
            "calibration_type1_rate": metadata.get("calibration_type1_rate"),
            "calibration_passed": True,
        },
        outputs=(
            config_path,
            *((retained_path,) if retained is not None else ()),
            calibration_path,
            results_path,
            metadata_path,
        ),
        metadata={
            "environment_profile": "sceptre-v1",
            "method": "sceptre_0.99.0",
            "retained_manifest_applied": retained is not None,
        },
    )


def run_propeller_composition(
    spec: CapabilitySpec,
    request: CapabilityRunRequest,
    contract: DatasetContract,
    staging: Path,
):
    metadata_path = resolve_input(
        contract,
        request.parameters.get("metadata_path"),
        label="metadata_path",
    )
    fields, rows = read_rows(metadata_path)
    sample_column = str(request.parameters.get("sample_column") or "replicate")
    state_column = str(request.parameters.get("state_column") or "state")
    condition_column = str(request.parameters.get("condition_column") or "condition")
    batch_column = str(request.parameters.get("batch_column") or "batch")
    missing = [
        name for name in (sample_column, state_column, condition_column)
        if name not in fields
    ]
    if missing:
        return blocked(spec, request, contract, "composition metadata is missing: " + ", ".join(missing))
    units_by_arm: dict[str, set[str]] = defaultdict(set)
    batches_by_arm: dict[str, set[str]] = defaultdict(set)
    sample_arm: dict[str, str] = {}
    for row in rows:
        sample = row.get(sample_column, "")
        arm = row.get(condition_column, "")
        if not sample or not arm:
            continue
        if sample in sample_arm and sample_arm[sample] != arm:
            return blocked(spec, request, contract, f"sample maps to multiple conditions: {sample}")
        sample_arm[sample] = arm
        units_by_arm[arm].add(sample)
        if batch_column in fields and row.get(batch_column):
            batches_by_arm[arm].add(row[batch_column])
    if len(units_by_arm) != 2:
        return blocked(spec, request, contract, "Propeller v1 requires exactly two contrast arms")
    minimum_units = min(len(values) for values in units_by_arm.values())
    if minimum_units < 2:
        return blocked(spec, request, contract, "fewer than two independent units are present in an arm")
    if len(batches_by_arm) == 2 and not set.intersection(*batches_by_arm.values()):
        return blocked(spec, request, contract, "condition is completely confounded with batch")
    contrast = list(request.parameters.get("contrast") or sorted(units_by_arm))
    if len(contrast) != 2 or any(arm not in units_by_arm for arm in contrast):
        return blocked(spec, request, contract, "contrast must name the two observed condition levels")
    config = {
        "schema_version": "pertura-propeller-run-config-v1",
        "metadata_path": str(metadata_path),
        "output_dir": str(staging),
        "sample_column": sample_column,
        "state_column": state_column,
        "condition_column": condition_column,
        "batch_column": batch_column if batch_column in fields else None,
        "contrast": contrast,
        "robust": True,
        "trend": False,
    }
    config_path = write_json(staging, "propeller_config.json", config)
    completed = _run_r_profile(
        "composition-v1",
        resources.files("pertura_workflow.capabilities").joinpath(
            "runners", "propeller_composition.R"
        ),
        config_path,
        timeout=int(request.parameters.get("timeout_seconds", spec.timeout_seconds)),
    )
    if isinstance(completed, str):
        return blocked(
            spec,
            request,
            contract,
            completed,
            metadata={"setup_command": "pertura env setup composition-v1"},
        )
    if completed.returncode != 0:
        return blocked(
            spec,
            request,
            contract,
            "Propeller runner failed: " + (completed.stderr or completed.stdout)[-2000:],
        )
    result_path = staging / "propeller_results.csv"
    proportion_path = staging / "sample_state_proportions.csv"
    metadata_output = staging / "propeller_metadata.json"
    if not all(path.is_file() for path in (result_path, proportion_path, metadata_output)):
        return blocked(spec, request, contract, "Propeller runner returned an incomplete output set")
    result_fields, result_rows = read_rows(result_path)
    required_columns = {"cluster", "PropMean", "FDR"}
    if not required_columns.issubset(result_fields):
        return blocked(
            spec,
            request,
            contract,
            "Propeller result is missing columns: " + ", ".join(sorted(required_columns - set(result_fields))),
        )
    invalid = any(not _finite_probability(row["FDR"]) for row in result_rows)
    if invalid or len({row["cluster"] for row in result_rows}) != len(result_rows):
        return blocked(spec, request, contract, "Propeller output has invalid FDR or duplicate state rows")
    cautions = [
        "Propeller adapter is synthetic-only validated and cannot support production measured claims"
    ]
    if minimum_units == 2:
        cautions.append("two units per arm permit execution but remain exploratory")
    return envelope(
        spec,
        request,
        contract,
        status=AnalysisStatus.completed_with_caution,
        summary=f"Propeller tested composition changes for {len(result_rows)} states.",
        cautions=cautions,
        metrics={
            "n_states": len(result_rows),
            "n_independent_units_per_arm": minimum_units,
            "contrast": contrast,
        },
        outputs=(config_path, proportion_path, result_path, metadata_output),
        metadata={"environment_profile": "composition-v1", "method": "speckle_propeller_1.10.0"},
    )


def run_guide_target_sensitivity(
    spec: CapabilitySpec,
    request: CapabilityRunRequest,
    contract: DatasetContract,
    staging: Path,
):
    table_path = _parameter_or_dependency_table(
        contract,
        staging,
        request.parameters.get("effect_table_path"),
        preferred_names=("guide_effects.csv", "target_guide_efficacy.json"),
    )
    rows = _effect_rows(table_path)
    required = {"guide", "target", "effect"}
    if not rows or not required.issubset(rows[0]):
        return blocked(spec, request, contract, "effect table must contain guide, target and effect")
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["target"])].append(row)
    summaries = []
    caution_count = 0
    for target, guides in sorted(grouped.items()):
        effects = [float(item["effect"]) for item in guides]
        pooled = sum(effects) / len(effects)
        signs = {effect > 0 for effect in effects if effect != 0}
        loo = {
            str(item["guide"]): (
                sum(other["effect"] for other in [
                    {"effect": float(row["effect"])} for row in guides if row is not item
                ]) / (len(guides) - 1)
                if len(guides) > 1 else None
            )
            for item in guides
        }
        unstable = len(signs) > 1 or any(
            value is not None and pooled != 0 and value * pooled < 0
            for value in loo.values()
        )
        caution_count += int(unstable)
        summaries.append(
            {
                "target": target,
                "n_guides": len(guides),
                "pooled_effect": pooled,
                "guide_effects": {
                    str(item["guide"]): float(item["effect"]) for item in guides
                },
                "direction_concordance": max(
                    sum(effect >= 0 for effect in effects),
                    sum(effect <= 0 for effect in effects),
                ) / len(effects),
                "leave_one_guide_out": loo,
                "unstable": unstable,
            }
        )
    output = write_json(
        staging,
        "guide_target_sensitivity.json",
        {
            "schema_version": "pertura-guide-target-sensitivity-v1",
            "targets": summaries,
            "unstable_target_count": caution_count,
        },
    )
    caution = (
        (f"{caution_count} targets are unstable to guide choice or leave-one-guide-out analysis",)
        if caution_count else ()
    )
    return envelope(
        spec,
        request,
        contract,
        status=AnalysisStatus.completed_with_caution if caution else AnalysisStatus.completed,
        summary=f"Compared guide-level and target-pooled effects for {len(summaries)} targets.",
        cautions=caution,
        metrics={"n_targets": len(summaries), "unstable_target_count": caution_count},
        outputs=(output,),
    )


def run_module_global_effect(
    spec: CapabilitySpec,
    request: CapabilityRunRequest,
    contract: DatasetContract,
    staging: Path,
):
    effect_path = None
    module_path = None
    for result in dependency_results(staging):
        for output in result.get("local_output_paths") or ():
            path = Path(output)
            if not path.is_file():
                continue
            if result.get("result_kind") == "effect_matrix" and path.name == "effect_matrix.npz":
                effect_path = path
            if result.get("result_kind") == "module_reference" and path.name == "gmt_modules.json":
                module_path = path
    if effect_path is None or module_path is None:
        return blocked(
            spec, request, contract,
            "committed effect matrix and imported GMT module reference are required",
        )
    try:
        data = np.load(effect_path, allow_pickle=False)
        matrix = np.asarray(data["effects"], float)
        observed = np.asarray(data["observed_mask"], bool)
        perturbations = np.asarray(data["perturbations"], str)
        features = np.asarray(data["features"], str)
        module_payload = json.loads(module_path.read_text(encoding="utf-8"))
        modules = module_payload["modules"]
    except (OSError, KeyError, ValueError, json.JSONDecodeError) as exc:
        return blocked(spec, request, contract, f"effect or module dependency is invalid: {exc}")
    if (
        matrix.shape != observed.shape
        or matrix.shape != (len(perturbations), len(features))
        or not isinstance(modules, dict)
    ):
        return blocked(spec, request, contract, "effect or module dependency dimensions are invalid")
    feature_index = {gene: index for index, gene in enumerate(features)}
    summaries = []
    global_effects = []
    for row_index, perturbation in enumerate(perturbations):
        tested = observed[row_index]
        tested_values = matrix[row_index, tested]
        global_effects.append({
            "perturbation_id": str(perturbation),
            "n_tested_genes": int(tested.sum()),
            "mean_absolute_effect": (
                float(np.mean(np.abs(tested_values))) if len(tested_values) else None
            ),
        })
        for name, genes in sorted(modules.items()):
            indices = [
                feature_index[str(gene)]
                for gene in genes
                if str(gene) in feature_index
                and observed[row_index, feature_index[str(gene)]]
            ]
            if not indices:
                continue
            signed = matrix[row_index, indices]
            summaries.append({
                "perturbation_id": str(perturbation),
                "module": str(name),
                "n_genes": len(indices),
                "mean_signed_effect": float(np.mean(signed)),
                "direction_consistency": float(max(
                    np.mean(signed >= 0),
                    np.mean(signed <= 0),
                )),
                "new_significance_test_performed": False,
            })
    output = write_json(
        staging,
        "module_global_effect.json",
        {
            "schema_version": "pertura-module-global-effect-v1",
            "source_class": "derived",
            "modules": summaries,
            "global_effects": global_effects,
            "new_significance_tests_performed": False,
        },
    )
    cautions = ()
    if not summaries:
        cautions = ("no imported reference modules overlapped the committed effect matrix",)
    return envelope(
        spec,
        request,
        contract,
        status=AnalysisStatus.completed_with_caution if cautions else AnalysisStatus.completed,
        summary=(
            f"Derived {len(summaries)} perturbation-module summaries "
            f"for {len(perturbations)} perturbations."
        ),
        cautions=cautions,
        metrics={
            "n_perturbations": len(perturbations),
            "n_module_summaries": len(summaries),
        },
        outputs=(output,),
        metadata={"derived_only": True, "new_significance_tests_performed": False},
    )


def run_method_null_calibration(
    spec: CapabilitySpec,
    request: CapabilityRunRequest,
    contract: DatasetContract,
    staging: Path,
):
    unit = str(request.parameters.get("permutation_unit") or "replicate_label")
    if unit not in {"replicate", "replicate_label", "independent_unit"}:
        return blocked(spec, request, contract, "null calibration must permute replicate/independent-unit labels")
    table_path = _parameter_or_dependency_table(
        contract,
        staging,
        request.parameters.get("null_results_path"),
        preferred_names=("null_results.csv", "sceptre_calibration.csv"),
    )
    fields, rows = read_rows(table_path)
    p_column = next((name for name in ("p_value", "PValue", "pvalue") if name in fields), None)
    if not p_column:
        return blocked(spec, request, contract, "null result table lacks a p-value column")
    pvalues = [float(row[p_column]) for row in rows if row.get(p_column) not in {"", None}]
    if not pvalues or any(not math.isfinite(value) or value < 0 or value > 1 for value in pvalues):
        return blocked(spec, request, contract, "null result table contains no valid p-values")
    alpha = float(request.parameters.get("alpha", 0.05))
    maximum_rate = float(request.parameters.get("maximum_type1_rate", 0.10))
    type1_rate = sum(value <= alpha for value in pvalues) / len(pvalues)
    calibration_passed = type1_rate <= maximum_rate
    output = write_json(
        staging,
        "method_null_calibration.json",
        {
            "schema_version": "pertura-method-null-calibration-v1",
            "method": str(request.parameters.get("method") or "unknown"),
            "permutation_unit": unit,
            "n_null_tests": len(pvalues),
            "alpha": alpha,
            "type1_rate": type1_rate,
            "maximum_type1_rate": maximum_rate,
            "calibration_passed": calibration_passed,
        },
    )
    if not calibration_passed:
        return envelope(
            spec,
            request,
            contract,
            status=AnalysisStatus.blocked,
            summary="Method-specific null calibration failed.",
            blockers=("null type-I rate exceeds the configured maximum",),
            metrics={"n_null_tests": len(pvalues), "type1_rate": type1_rate, "calibration_passed": False},
            outputs=(output,),
        )
    return envelope(
        spec,
        request,
        contract,
        status=AnalysisStatus.completed_with_caution,
        summary=f"Null calibration passed across {len(pvalues)} replicate-level tests.",
        cautions=("calibration is synthetic-only validated until server benchmark execution",),
        metrics={"n_null_tests": len(pvalues), "type1_rate": type1_rate, "calibration_passed": True},
        outputs=(output,),
    )


def _run_r_profile(
    profile: str,
    runner: Any,
    config_path: Path,
    *,
    timeout: int,
) -> subprocess.CompletedProcess[str] | str:
    binary = micromamba_path()
    prefix = environment_prefix(profile)
    if not binary.is_file() or not prefix.is_dir():
        return f"{profile} environment is missing"
    command = [
        str(binary),
        "run",
        "--prefix",
        str(prefix),
        "Rscript",
        str(runner),
        str(config_path),
    ]
    allowed = {
        key: os.environ[key]
        for key in ("SYSTEMROOT", "WINDIR", "TEMP", "TMP", "HOME", "USERPROFILE", "PATH")
        if key in os.environ
    }
    return subprocess.run(
        command,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=timeout,
        check=False,
        env=allowed,
    )


def _finite_probability(value: Any) -> bool:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(number) and 0 <= number <= 1


def _parameter_or_dependency_table(
    contract: DatasetContract,
    staging: Path,
    value: Any,
    *,
    preferred_names: tuple[str, ...],
) -> Path:
    if value not in (None, ""):
        resolved = resolve_input(contract, value, label="analysis table")
        assert resolved is not None
        return resolved
    for result in dependency_results(staging):
        for output in result.get("local_output_paths") or []:
            path = Path(output)
            if path.name in preferred_names and path.is_file():
                return path
    raise ValueError("explicit dependency does not expose a required analysis table")


def _effect_rows(path: Path) -> list[dict[str, Any]]:
    if path.suffix.lower() == ".json":
        payload = json.loads(path.read_text(encoding="utf-8"))
        if "guide_effects" in payload:
            target = str(payload.get("target_gene") or payload.get("target_uid") or "target")
            return [
                {"guide": guide, "target": target, "effect": float(values["effect"])}
                for guide, values in payload["guide_effects"].items()
            ]
        return list(payload.get("rows") or [])
    fields, rows = read_rows(path)
    return rows
