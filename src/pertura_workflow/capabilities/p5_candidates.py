from __future__ import annotations

import csv
import json
import math
from pathlib import Path
from statistics import NormalDist
from typing import Any

import numpy as np

from pertura_core import (
    AnalysisStatus, CapabilityRunRequest, CapabilitySpec, DatasetContract,
    SourceClass, VirtualStatus,
)
from pertura_core.hashing import canonical_hash
from pertura_workflow.capabilities.candidate_common import (
    blocked, dependency_results, envelope, resolve_input, resource_budget, write_json,
)
from pertura_workflow.capabilities.prediction_store import (
    FEATURE_TABLE_NAME, METADATA_NAME, ROW_TABLE_NAME, STANDARD_BUNDLE_NAME,
    array_content_sha256, open_chunked_prediction_bundle,
    write_chunked_prediction_bundle,
)
from pertura_workflow.exploratory import (
    ExploratoryPredictionBundleContract, ExploratoryVirtualSplitContract,
    audit_virtual_leakage,
)


def run_virtual_split_contract(spec, request, contract, staging):
    axes = request.parameters.get("axes")
    if not isinstance(axes, dict):
        return blocked(spec, request, contract, "virtual split axes are required")
    normalized = {}
    for axis, partitions in axes.items():
        if not isinstance(partitions, dict):
            return blocked(spec, request, contract, f"split axis {axis} is not an object")
        normalized[str(axis)] = {
            part: tuple(sorted(str(item) for item in partitions.get(part) or ()))
            for part in ("train", "validation", "test")
        }
    try:
        split = ExploratoryVirtualSplitContract(
            dataset_id=contract.dataset_id,
            axes=normalized,
            heldout_axes=tuple(request.parameters.get("heldout_axes") or ()),
            state_reference_hash=request.parameters.get("state_reference_hash"),
            module_reference_hash=request.parameters.get("module_reference_hash"),
            dependencies=tuple(
                item for item in request.dependencies if item.kind != "contract"
            ),
        )
    except ValueError as exc:
        return blocked(spec, request, contract, str(exc))
    path = write_json(staging, "virtual_split_contract.json", split.model_dump(mode="json"))
    assignments = staging / "virtual_split_assignments.csv"
    rows = (
        {"axis": axis, "partition": partition, "unit_id": unit}
        for axis, partitions in sorted(split.axes.items())
        for partition in ("train", "validation", "test")
        for unit in partitions[partition]
    )
    _write_csv(assignments, ("axis", "partition", "unit_id"), rows)
    unresolved = [axis for axis, partitions in split.axes.items()
                  if not partitions["test"] or not partitions["train"]]
    cautions = (
        ("some declared axes lack train or test members: " + ", ".join(unresolved),)
        if unresolved else ()
    )
    return envelope(
        spec, request, contract,
        status=AnalysisStatus.completed_with_caution if cautions else AnalysisStatus.completed,
        summary=f"Froze a virtual-evaluation split over {len(split.axes)} axes.",
        cautions=cautions,
        metrics={"n_axes": len(split.axes), "n_test_ids": len(split.test_ids)},
        outputs=(path, assignments),
        metadata={"split_hash": split.canonical_hash, "heldout_axes": list(split.heldout_axes)},
    )


def run_virtual_prediction_ingest(spec, request, contract, staging):
    split_payload = _dependency_json(staging, "virtual_split_contract.json")
    if split_payload is None:
        return blocked(spec, request, contract, "committed virtual split contract is missing")
    try:
        split = ExploratoryVirtualSplitContract.model_validate(split_payload)
    except ValueError as exc:
        return blocked(spec, request, contract, f"invalid virtual split dependency: {exc}")
    value = request.parameters.get("prediction_path")
    if not value:
        return blocked(spec, request, contract, "prediction_path is required")
    try:
        source = resolve_input(contract, value, label="prediction_path")
    except (OSError, ValueError) as exc:
        return blocked(spec, request, contract, str(exc))
    assert source is not None
    fmt = str(request.parameters.get("format") or _prediction_format(source))
    fmt = {"matrix_bundle": "npz", "long_parquet": "long_parquet", "h5ad": "h5ad", "chunked_zarr": "zarr_bundle"}.get(fmt, fmt)
    budget = resource_budget(request.parameters)
    estimated_bytes = _prediction_load_upper_bound(source, fmt, chunk_rows=budget.chunk_rows)
    if estimated_bytes > budget.max_bytes:
        return blocked(
            spec,
            request,
            contract,
            f"prediction ingestion would require up to {estimated_bytes / 1024**3:.3f} GB, exceeding max_memory_gb={budget.max_memory_gb}; convert to chunked Zarr before evaluation",
        )
    try:
        loaded = _load_prediction(source, fmt, request.parameters)
    except (OSError, ValueError, KeyError, ImportError) as exc:
        return blocked(spec, request, contract, f"prediction bundle is invalid: {exc}")
    prediction, observed, row_ids, features, metadata, uncertainty = loaded
    if prediction.shape != observed.shape or prediction.shape != (len(row_ids), len(features)):
        return blocked(spec, request, contract, "prediction and observed matrix dimensions disagree")
    if not _all_finite(prediction, budget.chunk_rows) or not _all_finite(observed, budget.chunk_rows):
        return blocked(spec, request, contract, "prediction bundle contains NA or infinite values")
    if len(set(row_ids.tolist())) != len(row_ids) or len(set(features.tolist())) != len(features):
        return blocked(spec, request, contract, "prediction row or feature IDs are not unique")
    if uncertainty:
        if ("lower" in uncertainty) != ("upper" in uncertainty):
            return blocked(spec, request, contract, "prediction intervals require both lower and upper")
        for name, values in uncertainty.items():
            if values.shape != prediction.shape or not _all_finite(values, budget.chunk_rows):
                return blocked(
                    spec, request, contract,
                    f"uncertainty array {name} must be finite and match prediction shape",
                )
    try:
        metadata, axis_partitions, row_partitions = _partition_prediction_rows(
            split, row_ids, metadata,
        )
    except ValueError as exc:
        return blocked(spec, request, contract, str(exc))
    represented_test = [
        row_id for row_id, partition in zip(row_ids.tolist(), row_partitions)
        if partition == "test"
    ]
    if not represented_test:
        return blocked(spec, request, contract, "prediction bundle contains no frozen test rows")
    try:
        standardized_outputs = write_chunked_prediction_bundle(
            staging,
            prediction=prediction, observed=observed,
            row_ids=row_ids, feature_ids=features,
            metadata=metadata, uncertainty=uncertainty,
            chunk_rows=budget.chunk_rows,
        )
    except (OSError, ValueError, ImportError) as exc:
        return blocked(spec, request, contract, f"could not write chunked prediction store: {exc}")
    uncertainty_kind = (
        "interval" if uncertainty and {"lower", "upper"}.issubset(uncertainty)
        else "standard_error" if uncertainty and "standard_error" in uncertainty
        else "none"
    )
    payload = ExploratoryPredictionBundleContract(
        model_id=str(request.parameters.get("model_id") or "unnamed_model"),
        model_version=str(request.parameters.get("model_version") or "unversioned"),
        split_id=split.virtual_split_id,
        split_hash=split.canonical_hash,
        format={"npz": "matrix_bundle", "h5ad": "h5ad",
                "long_parquet": "long_parquet",
                "zarr_bundle": "chunked_zarr"}[fmt],
        prediction_scale=str(request.parameters.get("prediction_scale") or "expression"),
        row_count=len(row_ids), feature_count=len(features),
        row_index_hash=canonical_hash(row_ids.tolist()),
        feature_index_hash=canonical_hash(features.tolist()),
        row_partition_hash=canonical_hash(row_partitions),
        axis_partition_hash=canonical_hash(axis_partitions),
        prediction_hash=array_content_sha256(prediction, chunk_rows=budget.chunk_rows),
        observed_hash=array_content_sha256(observed, chunk_rows=budget.chunk_rows),
        uncertainty_kind=uncertainty_kind,
        uncertainty_hash=canonical_hash({
            key: array_content_sha256(value, chunk_rows=budget.chunk_rows)
            for key, value in sorted(uncertainty.items())
        }) if uncertainty else None,
        axis_columns=tuple(sorted(split.axes)),
        model_training_ids=tuple(sorted(
            str(item) for item in request.parameters.get("model_training_ids") or ()
        )),
        source_paths=(source.name,),
        dependencies=request.dependencies,
    )
    manifest = write_json(staging, "prediction_bundle_contract.json", payload.model_dump(mode="json"))
    return envelope(
        spec, request, contract, status=AnalysisStatus.completed_with_caution,
        summary=f"Ingested {len(row_ids)} prediction rows across {len(features)} features.",
        cautions=("ingested predictions remain predictions regardless of agreement with observations",),
        metrics={"n_rows": len(row_ids), "n_features": len(features),
                 "n_test_rows": len(represented_test),
                 "n_train_rows": sum(item == "train" for item in row_partitions)},
        outputs=(*standardized_outputs, manifest),
        metadata={"prediction_class_fixed": True, "format": fmt,
                  "standard_format": "chunked_zarr_v1",
                  "split_hash": split.canonical_hash},
    )



def run_virtual_leakage_audit(spec, request, contract, staging):
    split_payload = _dependency_json(staging, "virtual_split_contract.json")
    prediction_payload = _dependency_json(staging, "prediction_bundle_contract.json")
    loaded = _load_standard_bundle(staging, request.parameters)
    if isinstance(loaded, str):
        return blocked(spec, request, contract, loaded)
    if split_payload is None or prediction_payload is None:
        return blocked(spec, request, contract, "split, prediction contract or bundle is missing")
    try:
        split = ExploratoryVirtualSplitContract.model_validate(split_payload)
        prediction = ExploratoryPredictionBundleContract.model_validate(prediction_payload)
    except ValueError as exc:
        return blocked(spec, request, contract, f"virtual contract validation failed: {exc}")
    if prediction.split_hash != split.canonical_hash:
        return blocked(spec, request, contract, "prediction was produced for a different split")
    _, _, row_ids, _, metadata, _ = loaded
    axis_partitions = metadata.get("__axis_partitions")
    row_partitions = metadata.get("__row_partition")
    if (
        not isinstance(axis_partitions, dict)
        or not isinstance(row_partitions, list)
        or len(row_partitions) != len(row_ids)
        or canonical_hash(axis_partitions) != prediction.axis_partition_hash
        or canonical_hash(row_partitions) != prediction.row_partition_hash
    ):
        return blocked(spec, request, contract, "prediction row-partition metadata is missing or drifted")
    test_row_ids = tuple(
        row_id for row_id, partition in zip(row_ids.tolist(), row_partitions)
        if partition == "test"
    )
    unresolved = []
    model_ids = prediction.model_training_ids
    if not model_ids:
        unresolved.append("model_training_ids_not_declared")
    state_ids = tuple(str(item) for item in request.parameters.get("state_reference_training_ids") or ())
    module_ids = tuple(str(item) for item in request.parameters.get("module_reference_training_ids") or ())
    preprocessing_ids = tuple(str(item) for item in request.parameters.get("preprocessing_training_ids") or ())
    for label, values in (
        ("state_reference", state_ids), ("module_reference", module_ids),
        ("preprocessing", preprocessing_ids),
    ):
        if not values and request.parameters.get(f"{label}_used", False):
            unresolved.append(f"{label}_training_ids_not_declared")
    audit = audit_virtual_leakage(
        split, model_training_ids=model_ids,
        state_reference_training_ids=state_ids,
        module_reference_training_ids=module_ids,
        preprocessing_training_ids=preprocessing_ids,
        unresolved_checks=tuple(unresolved),
        test_row_ids=test_row_ids,
    )
    path = write_json(staging, "virtual_leakage_audit.json", audit.model_dump(mode="json"))
    if audit.status == "blocked":
        status, blockers, cautions = VirtualStatus.out_of_scope, audit.reasons, ()
    elif audit.status == "limited":
        status, blockers, cautions = VirtualStatus.limited, (), audit.unresolved_checks
    else:
        status, blockers, cautions = VirtualStatus.supported, (), ()
    return envelope(
        spec, request, contract, status=status,
        summary=f"Virtual leakage audit is {audit.status}.",
        blockers=blockers, cautions=cautions,
        metrics={"test_row_count": len(audit.test_ids),
                 "leakage_reason_count": len(audit.reasons),
                 "unresolved_check_count": len(audit.unresolved_checks)},
        outputs=(path,), metadata={"leakage_status": audit.status},
    )


def run_virtual_baselines(spec, request, contract, staging):
    loaded = _load_standard_bundle(staging, request.parameters)
    if isinstance(loaded, str):
        return blocked(spec, request, contract, loaded)
    prediction, observed, row_ids, features, metadata, _ = loaded
    split_payload = _dependency_json(staging, "virtual_split_contract.json")
    audit = _dependency_json(staging, "virtual_leakage_audit.json")
    if split_payload is None or audit is None:
        return blocked(spec, request, contract, "split or leakage audit is missing")
    if audit.get("status") == "blocked":
        return blocked(spec, request, contract, "leakage audit blocked baseline evaluation")
    split = ExploratoryVirtualSplitContract.model_validate(split_payload)
    row_partitions = metadata.get("__row_partition")
    perturbations = metadata.get("perturbation")
    if not isinstance(row_partitions, list) or len(row_partitions) != len(row_ids):
        return blocked(spec, request, contract, "row-level virtual partitions are missing")
    if not isinstance(perturbations, list) or len(perturbations) != len(row_ids):
        return blocked(spec, request, contract, "row-level perturbation labels are missing")
    train = np.asarray([item == "train" for item in row_partitions], bool)
    test = np.asarray([item == "test" for item in row_partitions], bool)
    if not train.any() or not test.any():
        return blocked(spec, request, contract, "bundle must contain frozen train and test rows")
    perturbations = np.asarray(perturbations, str)
    global_mean = observed[train].mean(axis=0)
    control_ids = set(str(item) for item in request.parameters.get("control_ids") or ())
    control = train & np.asarray([item in control_ids for item in perturbations])
    control_mean = observed[control].mean(axis=0) if control.any() else global_mean
    control_predictions = np.repeat(control_mean[None, :], test.sum(), axis=0)
    context_values = metadata.get("context")
    has_context = isinstance(context_values, list) and len(context_values) == len(row_ids)
    context_predictions = np.empty((test.sum(), observed.shape[1]))
    test_indices = np.flatnonzero(test)
    if has_context:
        context_values = np.asarray(context_values, str)
        for output_index, row_index in enumerate(test_indices):
            match = train & (context_values == context_values[row_index])
            context_predictions[output_index] = (
                observed[match].mean(axis=0) if match.any() else global_mean
            )
    else:
        context_predictions[:] = global_mean
    singles = {
        label: observed[train & (perturbations == label)].mean(axis=0)
        for label in sorted(set(perturbations[train]))
    }
    additive = []
    for row_index in test_indices:
        parts = str(perturbations[row_index]).split("+")
        if len(parts) > 1 and all(part in singles for part in parts):
            value = control_mean + sum(
                (singles[part] - control_mean for part in parts),
                np.zeros_like(control_mean),
            )
        else:
            value = context_predictions[len(additive)]
        additive.append(value)
    additive_predictions = np.asarray(additive)
    baseline_path = staging / "virtual_baselines.npz"
    np.savez_compressed(
        baseline_path,
        test_row_ids=np.asarray(row_ids[test], dtype=str),
        feature_ids=np.asarray(features, dtype=str),
        control_mean=control_predictions, context_mean=context_predictions,
        linear_additive=additive_predictions, observed=observed[test],
    )
    metrics = {}
    for name, values in (
        ("control_mean", control_predictions), ("context_mean", context_predictions),
        ("linear_additive", additive_predictions),
    ):
        metrics[name] = _matrix_metrics(values, observed[test])
    manifest = write_json(staging, "virtual_baseline_results.json", {
        "schema_version": "pertura-virtual-baselines-v0",
        "split_hash": split.canonical_hash, "metrics": metrics,
        "control_ids": sorted(control_ids),
        "context_fallback_used": not has_context,
    })
    cautions = ()
    if not control.any():
        cautions += ("no declared control perturbation was found; control mean used all training rows",)
    if not has_context:
        cautions += ("context labels were absent; context mean equals global training mean",)
    return envelope(
        spec, request, contract,
        status=VirtualStatus.limited if cautions or audit.get("status") == "limited" else VirtualStatus.supported,
        summary="Computed mandatory control, context and linear/additive baselines.",
        cautions=cautions,
        metrics={"n_test_rows": int(test.sum()), "n_features": len(features)},
        outputs=(baseline_path, manifest),
        metadata={"baseline_names": ["control_mean", "context_mean", "linear_additive"]},
    )


def run_virtual_evaluate_comprehensive(spec, request, contract, staging):
    loaded = _load_standard_bundle(staging, request.parameters)
    baseline_path = _dependency_file(staging, "virtual_baselines.npz")
    audit = _dependency_json(staging, "virtual_leakage_audit.json")
    if isinstance(loaded, str):
        return blocked(spec, request, contract, loaded)
    if baseline_path is None or audit is None:
        return blocked(spec, request, contract, "prediction, baselines or leakage audit is missing")
    if audit.get("status") == "blocked":
        return blocked(spec, request, contract, "leakage audit blocked evaluation")
    prediction, observed, row_ids, features, metadata, uncertainty = loaded
    baseline = np.load(baseline_path, allow_pickle=False)
    test_ids = np.asarray(baseline["test_row_ids"], str)
    index = {name: i for i, name in enumerate(row_ids)}
    if any(name not in index for name in test_ids):
        return blocked(spec, request, contract, "baseline rows do not align with prediction bundle")
    selected = np.asarray([index[name] for name in test_ids])
    pred, truth = prediction[selected], observed[selected]
    minimum_units = int(request.parameters.get("minimum_units", 20))
    minimum_features = int(request.parameters.get("minimum_features", 100))
    iterations = int(request.parameters.get("bootstrap_iterations", 1000))
    if minimum_units < 2 or minimum_features < 2:
        return blocked(spec, request, contract, "minimum_units and minimum_features must be at least 2")
    if iterations < 100:
        return blocked(spec, request, contract, "bootstrap_iterations must be at least 100")
    baseline_names = ("control_mean", "context_mean", "linear_additive")
    baseline_metrics = {
        name: _matrix_metrics(np.asarray(baseline[name], float), truth)
        for name in baseline_names
    }
    model_metrics = _matrix_metrics(pred, truth)
    control = np.asarray(baseline["control_mean"], float)
    model_metrics.update(_direction_and_rank_metrics(pred, truth, control))
    variance_ratio = float(np.var(pred) / max(np.var(truth), 1e-12))
    distance_ratio = float(
        _mean_pairwise_distance(pred) / max(_mean_pairwise_distance(truth), 1e-12)
    )
    model_metrics["collapse_variance_ratio"] = variance_ratio
    model_metrics["collapse_distance_ratio"] = distance_ratio
    wins = [model_metrics["mse"] < baseline_metrics[name]["mse"] for name in baseline_names]
    model_metrics["baseline_win_rate"] = float(np.mean(wins))
    nominal_coverage = float(request.parameters.get("nominal_coverage", 0.90))
    coverage_tolerance = float(request.parameters.get("coverage_tolerance", 0.05))
    if not 0 < nominal_coverage < 1 or not 0 <= coverage_tolerance < 1:
        return blocked(spec, request, contract, "uncertainty coverage parameters are invalid")
    uncertainty_metrics = {}
    if uncertainty:
        if {"lower", "upper"}.issubset(uncertainty):
            lower, upper = uncertainty["lower"][selected], uncertainty["upper"][selected]
            if np.any(lower > upper):
                return blocked(spec, request, contract, "prediction intervals contain lower > upper")
            uncertainty_metrics["interval_coverage"] = float(
                ((truth >= lower) & (truth <= upper)).mean()
            )
            uncertainty_metrics["mean_interval_width"] = float((upper - lower).mean())
        elif "standard_error" in uncertainty:
            se = uncertainty["standard_error"][selected]
            if np.any(se < 0):
                return blocked(spec, request, contract, "standard errors must be nonnegative")
            z_value = NormalDist().inv_cdf((1.0 + nominal_coverage) / 2.0)
            lower, upper = pred - z_value * se, pred + z_value * se
            uncertainty_metrics["interval_coverage"] = float(
                ((truth >= lower) & (truth <= upper)).mean()
            )
    rng = np.random.default_rng(int(request.parameters.get("seed", 1729)))
    bootstrap = []
    for _ in range(iterations):
        rows = rng.integers(0, len(pred), len(pred))
        bootstrap.append(_median_row_spearman(pred[rows], truth[rows]))
    ci = [float(np.quantile(bootstrap, 0.025)), float(np.quantile(bootstrap, 0.975))]
    collapse = variance_ratio < float(request.parameters.get("collapse_variance_ratio_min", 0.10))
    collapse |= distance_ratio < float(request.parameters.get("collapse_distance_ratio_min", 0.10))
    beats_baselines = all(wins)
    limited_reasons = []
    if len(pred) < minimum_units:
        limited_reasons.append(f"only {len(pred)} held-out units; minimum is {minimum_units}")
    if len(features) < minimum_features:
        limited_reasons.append(f"only {len(features)} features; minimum is {minimum_features}")
    if collapse:
        limited_reasons.append("prediction collapse detected")
    if not beats_baselines:
        limited_reasons.append("model did not beat every mandatory baseline")
    if not uncertainty_metrics:
        limited_reasons.append("uncertainty_not_provided")
    elif abs(uncertainty_metrics["interval_coverage"] - nominal_coverage) > coverage_tolerance:
        limited_reasons.append("uncertainty coverage is outside tolerance")
    if audit.get("status") == "limited":
        limited_reasons.append("leakage audit contains unresolved checks")
    result_payload = {
        "schema_version": "pertura-virtual-evaluation-v0",
        "model_metrics": model_metrics, "baseline_metrics": baseline_metrics,
        "uncertainty_metrics": uncertainty_metrics,
        "nominal_coverage": nominal_coverage,
        "coverage_tolerance": coverage_tolerance,
        "median_row_spearman_ci95": ci,
        "collapse_detected": bool(collapse), "beats_all_baselines": bool(beats_baselines),
        "limited_reasons": limited_reasons, "source_class": "prediction",
    }
    output = write_json(staging, "virtual_evaluation.json", result_payload)
    table = staging / "virtual_evaluation_metrics.csv"
    rows = [{"entity": "model", "metric": key, "value": value}
            for key, value in sorted(model_metrics.items())]
    rows += [{"entity": name, "metric": key, "value": value}
             for name, metrics in baseline_metrics.items()
             for key, value in sorted(metrics.items())]
    _write_csv(table, ("entity", "metric", "value"), rows)
    return envelope(
        spec, request, contract,
        status=VirtualStatus.limited if limited_reasons else VirtualStatus.supported,
        summary="Evaluated predictions against held-out observations and mandatory baselines.",
        cautions=limited_reasons,
        metrics={"median_row_spearman": model_metrics["median_row_spearman"],
                 "baseline_win_rate": model_metrics["baseline_win_rate"],
                 "collapse_detected": bool(collapse)},
        outputs=(output, table),
        metadata={"prediction_class_fixed": True, "bootstrap_iterations": iterations},
    )


def run_design_next_panel(spec, request, contract, staging):
    evaluation = _dependency_json(staging, "virtual_evaluation.json")
    if evaluation is None:
        return blocked(spec, request, contract, "committed virtual evaluation is missing")
    candidates = request.parameters.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        return blocked(spec, request, contract, "next-panel candidate records are required")
    budget = float(request.parameters.get("budget", 0))
    if not math.isfinite(budget) or budget <= 0:
        return blocked(spec, request, contract, "next-panel budget must be finite and positive")
    weights = {
        "uncertainty": 0.25, "information_gain": 0.25, "program_coverage": 0.20,
        "biological_diversity": 0.15, "feasibility": 0.15,
    }
    weights.update({str(key): float(value)
                    for key, value in (request.parameters.get("weights") or {}).items()})
    if (
        any(not math.isfinite(value) or value < 0 for value in weights.values())
        or sum(weights.values()) <= 0
    ):
        return blocked(spec, request, contract, "next-panel weights must be finite and nonnegative")
    total = sum(weights.values())
    weights = {key: value / total for key, value in weights.items()}
    records, seen = [], set()
    for raw in candidates:
        if not isinstance(raw, dict) or not raw.get("candidate_id"):
            return blocked(spec, request, contract, "candidate records require candidate_id")
        candidate_id = str(raw["candidate_id"])
        if candidate_id in seen:
            return blocked(spec, request, contract, f"duplicate candidate_id: {candidate_id}")
        seen.add(candidate_id)
        cost = float(raw.get("cost", 1.0))
        if not math.isfinite(cost) or cost <= 0:
            return blocked(spec, request, contract, "candidate costs must be finite and positive")
        components = {key: float(raw.get(key, 0.0)) for key in weights}
        if any(not math.isfinite(value) or not 0.0 <= value <= 1.0
               for value in components.values()):
            return blocked(
                spec, request, contract,
                "next-panel utility components must be finite values in [0, 1]",
            )
        utility = sum(weights[key] * components[key] for key in weights)
        records.append({
            "candidate_id": candidate_id, "cost": cost,
            "utility": utility, "utility_per_cost": utility / cost,
            **components,
        })
    records.sort(key=lambda row: (-row["utility_per_cost"], -row["utility"], row["candidate_id"]))
    selected, rejected, used = [], [], 0.0
    for row in records:
        if used + row["cost"] <= budget + 1e-12:
            selected.append(row)
            used += row["cost"]
        else:
            rejected.append(row)
    table = staging / "next_panel_scores.csv"
    _write_csv(
        table,
        ("candidate_id", "selected", "cost", "utility", "utility_per_cost", *weights),
        ({**row, "selected": int(row in selected)} for row in records),
    )
    manifest = write_json(staging, "next_panel.json", {
        "schema_version": "pertura-next-panel-v0", "source_class": "hypothesis",
        "selected_ids": [row["candidate_id"] for row in selected],
        "rejected_ids": [row["candidate_id"] for row in rejected],
        "budget": budget, "budget_used": used, "weights": weights,
        "selection": "deterministic_greedy_utility_per_cost",
        "evaluation_result_ids": [item["result_id"] for item in dependency_results(staging)
                                  if item.get("result_kind") == "virtual_evaluation"],
        "evaluation_limited_reasons": list(evaluation.get("limited_reasons") or ()),
    })
    cautions = []
    if not selected:
        cautions.append("budget admitted no candidate")
    if evaluation.get("limited_reasons"):
        cautions.append("selection is based on a limited virtual evaluation")
    return envelope(
        spec, request, contract,
        status=AnalysisStatus.completed_with_caution if cautions else AnalysisStatus.completed,
        summary=f"Selected {len(selected)} next-panel hypotheses within budget.",
        cautions=tuple(cautions),
        metrics={"selected_count": len(selected), "budget": budget, "budget_used": used},
        outputs=(table, manifest),
        metadata={"source_class_fixed": "hypothesis", "selection_deterministic": True},
    )


def _partition_prediction_rows(split, row_ids, metadata):
    row_count = len(row_ids)
    normalized = {}
    axis_partitions = {}
    for axis, partitions in sorted(split.axes.items()):
        values = metadata.get(axis)
        if values is None and axis == "perturbation":
            values = row_ids.tolist()
        if not isinstance(values, (list, tuple)) or len(values) != row_count:
            raise ValueError(f"prediction metadata must provide one {axis} value per row")
        values = [str(item) for item in values]
        lookup = {}
        for partition in ("train", "validation", "test"):
            for value in partitions.get(partition, ()):
                lookup[str(value)] = partition
        missing = sorted({value for value in values if value not in lookup})
        if missing:
            preview = ", ".join(missing[:5])
            raise ValueError(f"prediction axis {axis} contains values absent from split: {preview}")
        normalized[axis] = values
        axis_partitions[axis] = [lookup[value] for value in values]
    row_partitions = []
    for index in range(row_count):
        memberships = {values[index] for values in axis_partitions.values()}
        if "test" in memberships:
            row_partitions.append("test")
        elif "validation" in memberships:
            row_partitions.append("validation")
        else:
            row_partitions.append("train")
    normalized["__axis_partitions"] = axis_partitions
    normalized["__row_partition"] = row_partitions
    return normalized, axis_partitions, row_partitions


def _prediction_load_upper_bound(path, fmt, *, chunk_rows):
    """Conservative pre-allocation bound for dense sources and one Zarr chunk."""
    if fmt == "zarr_bundle":
        try:
            loaded = open_chunked_prediction_bundle(
                path / STANDARD_BUNDLE_NAME,
                path / ROW_TABLE_NAME,
                path / FEATURE_TABLE_NAME,
                path / METADATA_NAME,
            )
            prediction, _, _, _, _, uncertainty = loaded
            chunk_rows = int(getattr(prediction, "chunks", (256,))[0] or 256)
            return chunk_rows * int(prediction.shape[1]) * 8 * (2 + len(uncertainty or {}))
        except Exception:
            return int(sum(item.stat().st_size for item in path.rglob("*") if item.is_file())) * 4
    if fmt == "h5ad":
        try:
            import anndata
            adata = anndata.read_h5ad(path, backed="r")
            try:
                return min(int(adata.n_obs), int(chunk_rows)) * int(adata.n_vars) * 8 * 4
            finally:
                if getattr(adata, "file", None):
                    adata.file.close()
        except Exception:
            return int(path.stat().st_size) * 32
    # Compressed NPZ and Parquet sizes are not memory sizes; use a conservative
    # multiplier and verify exact shapes immediately after loading.
    return int(path.stat().st_size) * 32


def _prediction_format(path):
    if path.is_dir() and (path / STANDARD_BUNDLE_NAME).is_dir():
        return "zarr_bundle"
    suffix = path.suffix.lower()
    if suffix == ".npz":
        return "npz"
    if suffix == ".h5ad":
        return "h5ad"
    if suffix == ".parquet":
        return "long_parquet"
    raise ValueError(
        "supported prediction formats are H5AD, NPZ, long Parquet, and a chunked Zarr bundle directory"
    )


def _load_prediction(path, fmt, parameters):
    if fmt == "zarr_bundle":
        return open_chunked_prediction_bundle(
            path / STANDARD_BUNDLE_NAME,
            path / ROW_TABLE_NAME,
            path / FEATURE_TABLE_NAME,
            path / METADATA_NAME,
        )
    if fmt == "npz":
        data = np.load(path, allow_pickle=False)
        prediction = np.asarray(data["predictions"], float)
        observed = np.asarray(data["observed"], float)
        rows = np.asarray(data["row_ids"], str)
        features = np.asarray(data["feature_ids"], str)
        metadata = json.loads(str(data["metadata_json"][0])) if "metadata_json" in data else {}
        uncertainty = {}
        for name in ("lower", "upper", "standard_error"):
            if name in data:
                uncertainty[name] = np.asarray(data[name], float)
        return prediction, observed, rows, features, metadata, uncertainty or None
    if fmt == "h5ad":
        import anndata
        adata = anndata.read_h5ad(path, backed="r")
        prediction_layer = str(parameters.get("prediction_layer") or "prediction")
        observed_layer = str(parameters.get("observed_layer") or "observed")
        if prediction_layer not in adata.layers or observed_layer not in adata.layers:
            raise ValueError("H5AD requires explicit prediction and observed layers")
        prediction = adata.layers[prediction_layer]
        observed = adata.layers[observed_layer]
        metadata = {column: adata.obs[column].astype(str).tolist()
                    for column in parameters.get("axis_columns") or ()}
        return prediction, observed, np.asarray(adata.obs_names, str), np.asarray(adata.var_names, str), metadata, None
    if fmt == "long_parquet":
        import pandas as pd
        frame = pd.read_parquet(path)
        required = {"row_id", "feature_id", "prediction", "observed"}
        if not required.issubset(frame.columns):
            raise ValueError("long Parquet requires row_id, feature_id, prediction and observed")
        if frame.duplicated(["row_id", "feature_id"]).any():
            raise ValueError("long Parquet contains duplicate row-feature pairs")
        rows, features = sorted(frame.row_id.astype(str).unique()), sorted(frame.feature_id.astype(str).unique())
        expected = len(rows) * len(features)
        if len(frame) != expected:
            raise ValueError("long Parquet must contain a complete rectangular matrix")
        pi = frame.pivot(index="row_id", columns="feature_id", values="prediction").reindex(index=rows, columns=features)
        oi = frame.pivot(index="row_id", columns="feature_id", values="observed").reindex(index=rows, columns=features)
        metadata = {}
        for column in parameters.get("axis_columns") or ():
            values = frame[["row_id", column]].drop_duplicates("row_id").set_index("row_id")
            metadata[column] = values.reindex(rows)[column].astype(str).tolist()
        return pi.to_numpy(float), oi.to_numpy(float), np.asarray(rows), np.asarray(features), metadata, None
    raise ValueError(f"unsupported prediction format: {fmt}")


def _load_standard_bundle(staging, parameters):
    bundle = _dependency_file(staging, STANDARD_BUNDLE_NAME)
    rows_path = _dependency_file(staging, ROW_TABLE_NAME)
    features_path = _dependency_file(staging, FEATURE_TABLE_NAME)
    metadata_path = _dependency_file(staging, METADATA_NAME)
    if bundle is not None:
        if rows_path is None or features_path is None or metadata_path is None:
            return "chunked prediction bundle sidecars are missing"
        try:
            loaded = open_chunked_prediction_bundle(
                bundle, rows_path, features_path, metadata_path,
            )
            prediction, observed, rows, features, metadata, uncertainty = loaded
            budget = resource_budget(parameters, columns_hint=len(features))
            budget.require_dense(
                len(rows), len(features),
                arrays=2 * (2 + len(uncertainty or {})),
                label="virtual evaluation working matrices",
            )
            prediction = np.asarray(prediction[:, :], float)
            observed = np.asarray(observed[:, :], float)
            uncertainty = {
                name: np.asarray(values[:, :], float)
                for name, values in (uncertainty or {}).items()
            }
            return prediction, observed, rows, features, metadata, uncertainty or None
        except (OSError, ValueError, KeyError, ImportError, MemoryError, json.JSONDecodeError) as exc:
            return f"prediction bundle is invalid: {exc}"
    # Read-only compatibility for alpha workspaces created before the Zarr store.
    path = _dependency_file(staging, "prediction_bundle.npz")
    if path is None:
        return "committed prediction bundle is missing"
    try:
        data = np.load(path, allow_pickle=False)
        prediction, observed = np.asarray(data["predictions"], float), np.asarray(data["observed"], float)
        rows, features = np.asarray(data["row_ids"], str), np.asarray(data["feature_ids"], str)
        budget = resource_budget(parameters, columns_hint=len(features))
        budget.require_dense(
            len(rows), len(features), arrays=4,
            label="legacy virtual evaluation working matrices",
        )
        metadata = json.loads(str(data["metadata_json"][0]))
        uncertainty = {name: np.asarray(data[name], float)
                       for name in ("lower", "upper", "standard_error") if name in data}
    except (OSError, KeyError, ValueError, json.JSONDecodeError) as exc:
        return f"prediction bundle is invalid: {exc}"
    return prediction, observed, rows, features, metadata, uncertainty or None


def _dependency_file(staging, name):
    for result in dependency_results(staging):
        for value in result.get("local_output_paths") or ():
            path = Path(value)
            if path.exists() and path.name == name:
                return path
    return None


def _dependency_json(staging, name):
    path = _dependency_file(staging, name)
    return json.loads(path.read_text(encoding="utf-8")) if path else None


def _all_finite(values, chunk_rows):
    rows = int(values.shape[0])
    step = max(1, int(chunk_rows))
    for start in range(0, rows, step):
        block = values[start:min(rows, start + step), :]
        block = block.to_memory() if hasattr(block, "to_memory") else block
        block = block.toarray() if hasattr(block, "toarray") else block
        if not np.all(np.isfinite(np.asarray(block, float))):
            return False
    return True


def _dense(value):
    value = value.to_memory() if hasattr(value, "to_memory") else value
    value = value.toarray() if hasattr(value, "toarray") else value
    return np.asarray(value, float)


def _matrix_metrics(prediction, observed):
    residual = prediction - observed
    return {
        "mse": float(np.mean(residual ** 2)),
        "mae": float(np.mean(np.abs(residual))),
        "median_row_spearman": _median_row_spearman(prediction, observed),
        "median_feature_spearman": _median_row_spearman(prediction.T, observed.T),
    }


def _direction_and_rank_metrics(prediction, observed, control, *, chunk_rows=256):
    prediction_delta, observed_delta = prediction - control, observed - control
    direction = float((np.sign(prediction_delta) == np.sign(observed_delta)).mean())
    ranks, hits = [], 0
    for index, row in enumerate(prediction):
        true_distance = float(np.mean((row - observed[index]) ** 2))
        less = 0
        equal_before = 0
        for start in range(0, len(observed), chunk_rows):
            stop = min(len(observed), start + chunk_rows)
            distances = np.mean((observed[start:stop] - row) ** 2, axis=1)
            less += int(np.sum(distances < true_distance))
            equal_indices = np.flatnonzero(distances == true_distance) + start
            equal_before += int(np.sum(equal_indices < index))
        rank = 1 + less + equal_before
        ranks.append(rank)
        hits += rank == 1
    return {
        "direction_accuracy": direction,
        "median_true_match_rank": float(np.median(ranks)),
        "discriminability_top1": float(hits / len(prediction)),
    }


def _median_row_spearman(left, right):
    values = []
    for a, b in zip(left, right):
        ar, br = _rank(a), _rank(b)
        if np.std(ar) == 0 or np.std(br) == 0:
            continue
        values.append(float(np.corrcoef(ar, br)[0, 1]))
    return float(np.median(values)) if values else 0.0


def _rank(values):
    order = np.argsort(values, kind="mergesort")
    ranks = np.empty(len(values), float)
    ranks[order] = np.arange(len(values), dtype=float)
    for value in np.unique(values):
        indices = np.flatnonzero(values == value)
        ranks[indices] = ranks[indices].mean()
    return ranks


def _mean_pairwise_distance(matrix, *, chunk_rows=256):
    if len(matrix) < 2:
        return 0.0
    total = 0.0
    count = 0
    for left_start in range(0, len(matrix), chunk_rows):
        left = matrix[left_start:left_start + chunk_rows]
        for right_start in range(left_start, len(matrix), chunk_rows):
            right = matrix[right_start:right_start + chunk_rows]
            distances = np.sqrt(np.sum((left[:, None, :] - right[None, :, :]) ** 2, axis=2))
            if left_start == right_start:
                upper = np.triu_indices(len(left), k=1)
                total += float(distances[upper].sum())
                count += len(upper[0])
            else:
                total += float(distances.sum())
                count += int(distances.size)
    return total / count if count else 0.0


def _write_csv(path, fields, rows):
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
