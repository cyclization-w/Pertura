from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any, Mapping, Sequence

from pertura_core.hashing import file_sha256


def evaluate_artifact_metrics(
    result: Mapping[str, Any],
    evaluators: Sequence[Mapping[str, Any]],
    *,
    output_root: Path | None,
    reference_root: Path | None,
) -> dict[str, Any]:
    """Compare capability artifacts with independently frozen references."""

    continuous: dict[str, float | int | str | None] = {}
    comparisons: list[bool] = []
    reference_hashes: dict[str, str] = {}
    required_outputs: set[str] = set()
    limitations: list[str] = []
    metric_bindings: list[dict[str, str]] = []
    for raw in evaluators:
        evaluator_id = str(raw.get("evaluator_id") or "")
        evaluator_type = str(raw.get("type") or "")
        observed_name = str(raw.get("observed_output") or "")
        reference_name = str(raw.get("reference_path") or "")
        reference_hash = str(raw.get("reference_sha256") or "")
        required_outputs.add(observed_name)
        if output_root is None or reference_root is None:
            comparisons.append(False)
            limitations.append(
                f"{evaluator_id}: artifact/reference roots are unavailable"
            )
            continue
        try:
            observed_path = _resolve_observed_output(
                result, output_root=output_root, logical_name=observed_name
            )
            reference_path = _resolve_relative_file(
                reference_root, reference_name, label="metric reference"
            )
            if file_sha256(reference_path) != reference_hash:
                raise ValueError("metric reference checksum mismatch")
            reference_hashes[f"metric_reference:{evaluator_id}"] = reference_hash
            observed_hash = file_sha256(observed_path)
            observed = _read_table(observed_path)
            reference = _read_table(reference_path)
            observed = _filter_table(
                observed,
                raw.get("observed_filters") or {},
                label="observed",
            )
            reference = _filter_table(
                reference,
                raw.get("reference_filters") or {},
                label="reference",
            )
            if evaluator_type == "table_numeric":
                passed, metrics = _compare_numeric_tables(observed, reference, raw)
            elif evaluator_type == "classification":
                passed, metrics = _compare_classification(observed, reference, raw)
            elif evaluator_type == "rank_concordance":
                passed, metrics = _compare_rank(observed, reference, raw)
            elif evaluator_type == "posterior_calibration":
                passed, metrics = _compare_posterior_calibration(
                    observed, reference, raw
                )
            elif evaluator_type == "cluster_agreement":
                passed, metrics = _compare_cluster_agreement(
                    observed, reference, raw
                )
            elif evaluator_type == "null_calibration":
                passed, metrics = _compare_null_calibration(
                    observed, reference, raw
                )
            elif evaluator_type == "effect_error":
                passed, metrics = _compare_effect_error(observed, reference, raw)
            else:
                raise ValueError(f"unsupported metric evaluator type: {evaluator_type}")
        except (FileNotFoundError, ImportError, OSError, TypeError, ValueError) as exc:
            comparisons.append(False)
            limitations.append(f"{evaluator_id}: {exc}")
            continue
        comparisons.append(passed)
        metric_bindings.append(
            {
                "metric_id": evaluator_id,
                "observed_artifact_role": observed_name,
                "observed_artifact_hash": observed_hash,
                "reference_id": reference_name,
                "reference_hash": reference_hash,
                "evaluator_id": evaluator_id,
            }
        )
        continuous.update(
            {f"{evaluator_id}.{name}": value for name, value in metrics.items()}
        )
        if not passed:
            limitations.append(
                f"{evaluator_id}: frozen artifact comparison did not meet its threshold"
            )
    return {
        "comparisons": tuple(comparisons),
        "continuous_metrics": continuous,
        "reference_hashes": reference_hashes,
        "required_outputs": tuple(sorted(required_outputs)),
        "limitations": tuple(limitations),
        "metric_bindings": tuple(metric_bindings),
    }


def validate_artifact_evaluator(raw: Mapping[str, Any], *, context: str) -> None:
    evaluator_id = str(raw.get("evaluator_id") or "")
    evaluator_type = str(raw.get("type") or "")
    observed_output = str(raw.get("observed_output") or "")
    reference_path = str(raw.get("reference_path") or "")
    reference_hash = str(raw.get("reference_sha256") or "")
    if not evaluator_id or evaluator_type not in {
        "table_numeric",
        "classification",
        "rank_concordance",
        "posterior_calibration",
        "cluster_agreement",
        "null_calibration",
        "effect_error",
    }:
        raise ValueError(f"invalid artifact evaluator identity: {context}")
    if (not observed_output or "\\" in observed_output or ":" in observed_output or Path(observed_output).is_absolute()):
        raise ValueError(f"artifact evaluator output must be logical/relative: {context}")
    relative = Path(reference_path)
    if (not reference_path or "\\" in reference_path or ":" in reference_path or relative.is_absolute() or ".." in relative.parts):
        raise ValueError(f"artifact evaluator reference escapes catalog root: {context}")
    if re.fullmatch(r"sha256:[0-9a-f]{64}", reference_hash) is None:
        raise ValueError(f"artifact evaluator reference hash is invalid: {context}")
    key_columns = raw.get("key_columns")
    if not isinstance(key_columns, list) or not key_columns:
        raise ValueError(f"artifact evaluator requires key_columns: {context}")
    for filter_name in ("observed_filters", "reference_filters"):
        filters = raw.get(filter_name) or {}
        if not isinstance(filters, Mapping) or any(
            not str(column) or isinstance(value, (dict, list))
            for column, value in filters.items()
        ):
            raise ValueError(
                f"artifact evaluator {filter_name} is invalid: {context}"
            )
    if evaluator_type == "table_numeric":
        value_columns = raw.get("value_columns")
        if not isinstance(value_columns, list) or not value_columns:
            raise ValueError(
                f"numeric artifact evaluator requires value_columns: {context}"
            )
        if float(raw.get("absolute_tolerance", 0.0)) < 0 or float(
            raw.get("relative_tolerance", 0.0)
        ) < 0:
            raise ValueError(f"numeric tolerances must be non-negative: {context}")
    elif evaluator_type == "classification":
        if not str(raw.get("observed_label_column") or "") or not str(
            raw.get("reference_label_column") or ""
        ):
            raise ValueError(
                f"classification evaluator requires label columns: {context}"
            )
        label_type = str(raw.get("label_type") or "categorical")
        if label_type not in {"categorical", "boolean"}:
            raise ValueError(
                f"classification evaluator label_type is invalid: {context}"
            )
        aliases = raw.get("label_aliases") or {}
        if not isinstance(aliases, Mapping) or any(
            not str(canonical)
            or not isinstance(values, list)
            or not values
            or any(not str(value) for value in values)
            for canonical, values in aliases.items()
        ):
            raise ValueError(
                f"classification evaluator label_aliases is invalid: {context}"
            )
        allowed = raw.get("allowed_labels") or []
        if not isinstance(allowed, list) or any(not str(item) for item in allowed):
            raise ValueError(
                f"classification evaluator allowed_labels is invalid: {context}"
            )
        if aliases and set(str(item) for item in aliases) != set(
            str(item) for item in allowed
        ):
            raise ValueError(
                f"classification aliases must cover allowed_labels exactly: {context}"
            )
        if label_type == "boolean" and (aliases or allowed):
            raise ValueError(
                f"boolean classification must not declare categorical labels: {context}"
            )
    elif evaluator_type == "rank_concordance":
        if not str(raw.get("observed_value_column") or "") or not str(
            raw.get("reference_value_column") or ""
        ):
            raise ValueError(
                f"rank evaluator requires value columns: {context}"
            )
    elif evaluator_type == "posterior_calibration":
        if not str(raw.get("probability_column") or "") or not str(
            raw.get("reference_label_column") or ""
        ):
            raise ValueError(
                f"posterior calibration requires probability and label columns: {context}"
            )
    elif evaluator_type == "cluster_agreement":
        if not str(raw.get("observed_label_column") or "") or not str(
            raw.get("reference_label_column") or ""
        ):
            raise ValueError(
                f"cluster agreement requires label columns: {context}"
            )
    elif evaluator_type == "null_calibration":
        if not str(raw.get("pvalue_column") or "") or not str(
            raw.get("reference_signal_column") or ""
        ):
            raise ValueError(
                f"null calibration requires p-value and signal columns: {context}"
            )
    elif evaluator_type == "effect_error":
        if not str(raw.get("observed_value_column") or "") or not str(
            raw.get("reference_value_column") or ""
        ):
            raise ValueError(f"effect error requires value columns: {context}")


def _resolve_observed_output(
    result: Mapping[str, Any], *, output_root: Path, logical_name: str
) -> Path:
    root = output_root.resolve()
    candidates: list[Path] = []
    for raw in result.get("output_paths") or ():
        path = Path(str(raw))
        candidate = path.resolve() if path.is_absolute() else (root / path).resolve()
        if candidate != root and root not in candidate.parents:
            continue
        if str(raw) == logical_name or candidate.name == Path(logical_name).name:
            candidates.append(candidate)
        elif candidate.as_posix().endswith(Path(logical_name).as_posix()):
            candidates.append(candidate)
    unique = sorted(set(candidates))
    if len(unique) != 1 or not unique[0].is_file():
        raise FileNotFoundError(
            f"observed output {logical_name!r} is missing or ambiguous"
        )
    return unique[0]


def _resolve_relative_file(root: Path, relative_name: str, *, label: str) -> Path:
    base = root.resolve()
    path = (base / relative_name).resolve()
    if path != base and base not in path.parents:
        raise ValueError(f"{label} escapes its declared root")
    if not path.is_file():
        raise FileNotFoundError(f"{label} is missing: {relative_name}")
    return path


def _read_table(path: Path):
    try:
        import pandas as pd
    except ImportError as exc:  # pragma: no cover - benchmark environment contract
        raise ImportError("pandas is required for artifact metric evaluation") from exc
    suffix = path.suffix.lower()
    if suffix in {".parquet", ".pq"}:
        return pd.read_parquet(path)
    if suffix in {".tsv", ".txt"}:
        return pd.read_csv(path, sep="\t")
    if suffix == ".csv":
        return pd.read_csv(path)
    if suffix == ".json":
        return pd.read_json(path)
    raise ValueError(f"unsupported metric table format: {suffix}")


def _filter_table(table, filters: Mapping[str, Any], *, label: str):
    filtered = table
    for column, expected in filters.items():
        if str(column) not in filtered.columns:
            raise ValueError(f"{label} table lacks filter column: {column}")
        series = filtered[str(column)]
        if isinstance(expected, bool):
            normalized = series.astype(str).str.strip().str.lower().map(
                {"true": True, "false": False, "1": True, "0": False}
            )
            filtered = filtered.loc[normalized == expected]
        else:
            filtered = filtered.loc[series.astype(str) == str(expected)]
    if filters and filtered.empty:
        raise ValueError(f"{label} table filters selected zero rows")
    return filtered.copy()


def _aligned_tables(observed, reference, key_columns: Sequence[str]):
    keys = [str(item) for item in key_columns]
    for table, label in ((observed, "observed"), (reference, "reference")):
        missing = [name for name in keys if name not in table.columns]
        if missing:
            raise ValueError(f"{label} table lacks key columns: {missing}")
        if table.duplicated(keys).any():
            raise ValueError(f"{label} table contains duplicate metric keys")
    observed_indexed = observed.set_index(keys).sort_index()
    reference_indexed = reference.set_index(keys).sort_index()
    if not observed_indexed.index.equals(reference_indexed.index):
        raise ValueError("observed/reference key sets differ")
    return observed_indexed, reference_indexed


def _compare_numeric_tables(observed, reference, spec: Mapping[str, Any]):
    import numpy as np

    observed, reference = _aligned_tables(
        observed, reference, spec.get("key_columns") or ()
    )
    columns = [str(item) for item in spec.get("value_columns") or ()]
    if not columns:
        raise ValueError("table_numeric evaluator requires value_columns")
    abs_tolerance = float(spec.get("absolute_tolerance", 0.0))
    rel_tolerance = float(spec.get("relative_tolerance", 0.0))
    metrics: dict[str, float | int] = {}
    passed = True
    for column in columns:
        if column not in observed or column not in reference:
            raise ValueError(f"numeric comparison column is missing: {column}")
        left = observed[column].to_numpy(dtype=float)
        right = reference[column].to_numpy(dtype=float)
        finite = np.isfinite(left) & np.isfinite(right)
        absolute = np.abs(left - right)
        relative = absolute / np.maximum(np.abs(right), 1e-12)
        cell_passed = finite & (
            (absolute <= abs_tolerance) | (relative <= rel_tolerance)
        )
        failures = int((~cell_passed).sum())
        metrics[f"{column}.max_absolute_error"] = (
            float(absolute[finite].max()) if finite.any() else math.inf
        )
        metrics[f"{column}.max_relative_error"] = (
            float(relative[finite].max()) if finite.any() else math.inf
        )
        metrics[f"{column}.failed_values"] = failures
        passed = passed and failures == 0
    metrics["row_count"] = int(len(observed))
    return passed, metrics


def _compare_classification(observed, reference, spec: Mapping[str, Any]):
    observed, reference = _aligned_tables(
        observed, reference, spec.get("key_columns") or ()
    )
    observed_column = str(spec.get("observed_label_column") or "verdict")
    reference_column = str(spec.get("reference_label_column") or "verdict")
    if observed_column not in observed or reference_column not in reference:
        raise ValueError("classification label column is missing")
    if observed[observed_column].isna().any() or reference[
        reference_column
    ].isna().any():
        raise ValueError("classification labels contain missing values")
    label_type = str(spec.get("label_type") or "categorical")
    if label_type == "boolean":
        predictions = _strict_boolean_series(
            observed[observed_column], label=f"observed {observed_column}"
        ).astype(str)
        truth = _strict_boolean_series(
            reference[reference_column], label=f"reference {reference_column}"
        ).astype(str)
    elif label_type == "categorical":
        predictions = _canonical_label_series(
            observed[observed_column], spec=spec, label=f"observed {observed_column}"
        )
        truth = _canonical_label_series(
            reference[reference_column], spec=spec, label=f"reference {reference_column}"
        )
    else:
        raise ValueError(f"unsupported classification label type: {label_type}")
    labels = sorted(set(predictions) | set(truth))
    recalls: dict[str, float] = {}
    precisions: dict[str, float] = {}
    f1_values: list[float] = []
    for label in labels:
        true_positive = int(((predictions == label) & (truth == label)).sum())
        false_positive = int(((predictions == label) & (truth != label)).sum())
        false_negative = int(((predictions != label) & (truth == label)).sum())
        precision = true_positive / max(true_positive + false_positive, 1)
        recall = true_positive / max(true_positive + false_negative, 1)
        recalls[label] = recall
        precisions[label] = precision
        f1_values.append(
            2 * precision * recall / max(precision + recall, 1e-12)
        )
    accuracy = float((predictions == truth).mean())
    macro_f1 = float(sum(f1_values) / max(len(f1_values), 1))
    metrics: dict[str, float | int] = {
        "accuracy": accuracy,
        "macro_precision": float(sum(precisions.values()) / max(len(precisions), 1)),
        "macro_recall": float(sum(recalls.values()) / max(len(recalls), 1)),
        "macro_f1": macro_f1,
        "row_count": int(len(truth)),
    }
    for label, recall in recalls.items():
        metrics[f"recall.{label}"] = recall
        metrics[f"precision.{label}"] = precisions[label]
    passed = accuracy >= float(spec.get("minimum_accuracy", 0.0))
    passed = passed and macro_f1 >= float(spec.get("minimum_macro_f1", 0.0))
    blocked_label = spec.get("blocked_label")
    if blocked_label is not None:
        non_blocked_truth = truth != str(blocked_label)
        false_block_count = int(
            ((predictions == str(blocked_label)) & non_blocked_truth).sum()
        )
        non_blocked_count = int(non_blocked_truth.sum())
        false_block = (
            false_block_count / non_blocked_count
            if non_blocked_count
            else 0.0
        )
        metrics["false_block_rate"] = false_block
        metrics["false_block_count"] = false_block_count
        metrics["non_blocked_reference_count"] = non_blocked_count
        passed = passed and false_block <= float(
            spec.get("maximum_false_block_rate", 1.0)
        )
    return passed, metrics


def _compare_rank(observed, reference, spec: Mapping[str, Any]):
    observed, reference = _aligned_tables(
        observed, reference, spec.get("key_columns") or ()
    )
    observed_column = str(spec.get("observed_value_column") or "effect")
    reference_column = str(spec.get("reference_value_column") or "effect")
    if observed_column not in observed or reference_column not in reference:
        raise ValueError("rank comparison value column is missing")
    left = observed[observed_column].astype(float)
    right = reference[reference_column].astype(float)
    if not all(math.isfinite(value) for value in (*left.tolist(), *right.tolist())):
        raise ValueError("rank comparison contains NA or infinite values")
    correlation = float(left.rank(method="average").corr(right.rank(method="average")))
    if not math.isfinite(correlation):
        raise ValueError("rank correlation is undefined")
    minimum = float(spec.get("minimum_spearman", 0.0))
    return correlation >= minimum, {
        "spearman": correlation,
        "row_count": int(len(left)),
    }


def _compare_posterior_calibration(observed, reference, spec: Mapping[str, Any]):
    import numpy as np

    observed, reference = _aligned_tables(
        observed, reference, spec.get("key_columns") or ()
    )
    probability_column = str(spec["probability_column"])
    label_column = str(spec["reference_label_column"])
    if probability_column not in observed or label_column not in reference:
        raise ValueError("posterior calibration columns are missing")
    probability = observed[probability_column].to_numpy(dtype=float)
    truth = reference[label_column].to_numpy(dtype=float)
    if not np.isfinite(probability).all() or not np.isfinite(truth).all():
        raise ValueError("posterior calibration contains non-finite values")
    if ((probability < 0) | (probability > 1)).any() or not set(truth).issubset({0.0, 1.0}):
        raise ValueError("posterior probabilities/labels are outside their valid range")
    brier = float(np.mean((probability - truth) ** 2))
    bins = max(2, int(spec.get("bins", 10)))
    edges = np.linspace(0.0, 1.0, bins + 1)
    ece = 0.0
    for index in range(bins):
        in_bin = (probability >= edges[index]) & (
            probability <= edges[index + 1]
            if index == bins - 1
            else probability < edges[index + 1]
        )
        if in_bin.any():
            ece += float(in_bin.mean()) * abs(
                float(probability[in_bin].mean()) - float(truth[in_bin].mean())
            )
    passed = brier <= float(spec.get("maximum_brier", 1.0))
    passed = passed and ece <= float(spec.get("maximum_ece", 1.0))
    return passed, {"brier": brier, "ece": ece, "row_count": int(len(truth))}


def _compare_cluster_agreement(observed, reference, spec: Mapping[str, Any]):
    observed, reference = _aligned_tables(
        observed, reference, spec.get("key_columns") or ()
    )
    observed_column = str(spec["observed_label_column"])
    reference_column = str(spec["reference_label_column"])
    if observed_column not in observed or reference_column not in reference:
        raise ValueError("cluster agreement label columns are missing")
    left = observed[observed_column].astype(str).tolist()
    right = reference[reference_column].astype(str).tolist()
    ari = _adjusted_rand_index(left, right)
    metrics: dict[str, float | int] = {"ari": ari, "row_count": len(left)}
    passed = ari >= float(spec.get("minimum_ari", 0.0))
    rejection_column = spec.get("rejection_column")
    reference_rejection_column = spec.get("reference_rejection_column")
    if rejection_column and reference_rejection_column:
        if rejection_column not in observed or reference_rejection_column not in reference:
            raise ValueError("mapping rejection columns are missing")
        rejection = _strict_boolean_series(
            observed[rejection_column], label=f"observed {rejection_column}"
        )
        expected = _strict_boolean_series(
            reference[reference_rejection_column],
            label=f"reference {reference_rejection_column}",
        )
        rejection_accuracy = float((rejection == expected).mean())
        metrics["mapping_rejection_accuracy"] = rejection_accuracy
        passed = passed and rejection_accuracy >= float(
            spec.get("minimum_rejection_accuracy", 0.0)
        )
    return passed, metrics


def _canonical_label_series(series, *, spec: Mapping[str, Any], label: str):
    aliases = spec.get("label_aliases") or {}
    allowed = tuple(str(item) for item in spec.get("allowed_labels") or ())
    if aliases and not isinstance(aliases, Mapping):
        raise ValueError("classification label aliases must be an object")
    lookup: dict[str, str] = {}
    for canonical, raw_aliases in aliases.items():
        canonical_label = str(canonical)
        if not isinstance(raw_aliases, list) or not raw_aliases:
            raise ValueError(
                f"classification aliases for {canonical_label} must be a non-empty list"
            )
        for alias in (canonical_label, *raw_aliases):
            normalized = str(alias).strip().casefold()
            prior = lookup.setdefault(normalized, canonical_label)
            if prior != canonical_label:
                raise ValueError(f"classification alias is ambiguous: {alias}")
    allowed_lookup = {item.strip().casefold(): item for item in allowed}

    def normalize(value: Any) -> str:
        text = str(value).strip()
        normalized = text.casefold()
        if lookup:
            if normalized not in lookup:
                raise ValueError(
                    f"{label} contains an unknown classification label: {text}"
                )
            return lookup[normalized]
        if allowed:
            if normalized not in allowed_lookup:
                raise ValueError(
                    f"{label} contains an unknown classification label: {text}"
                )
            return allowed_lookup[normalized]
        return text

    return series.map(normalize)


def _strict_boolean_series(series, *, label: str):
    def parse(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if value == 1:
                return True
            if value == 0:
                return False
        text = str(value).strip().casefold()
        if text in {"true", "1"}:
            return True
        if text in {"false", "0"}:
            return False
        raise ValueError(f"{label} contains an invalid boolean value: {value}")

    return series.map(parse)


def _adjusted_rand_index(left: Sequence[str], right: Sequence[str]) -> float:
    from collections import Counter

    if len(left) != len(right) or not left:
        raise ValueError("ARI inputs must be non-empty and aligned")
    contingency = Counter(zip(left, right))
    left_counts = Counter(left)
    right_counts = Counter(right)
    choose2 = lambda value: value * (value - 1) / 2
    sum_cells = sum(choose2(value) for value in contingency.values())
    sum_left = sum(choose2(value) for value in left_counts.values())
    sum_right = sum(choose2(value) for value in right_counts.values())
    total = choose2(len(left))
    if total == 0:
        return 1.0
    expected = sum_left * sum_right / total
    maximum = 0.5 * (sum_left + sum_right)
    return 1.0 if maximum == expected else float((sum_cells - expected) / (maximum - expected))


def _compare_null_calibration(observed, reference, spec: Mapping[str, Any]):
    import numpy as np

    observed, reference = _aligned_tables(
        observed, reference, spec.get("key_columns") or ()
    )
    pvalue_column = str(spec["pvalue_column"])
    signal_column = str(spec["reference_signal_column"])
    if pvalue_column not in observed or signal_column not in reference:
        raise ValueError("null calibration columns are missing")
    pvalues = observed[pvalue_column].to_numpy(dtype=float)
    signal = reference[signal_column].astype(bool).to_numpy()
    if not np.isfinite(pvalues).all() or ((pvalues < 0) | (pvalues > 1)).any():
        raise ValueError("p-values are non-finite or outside [0, 1]")
    alpha = float(spec.get("alpha", 0.05))
    rejected = pvalues <= alpha
    null = ~signal
    type_i = float(rejected[null].mean()) if null.any() else None
    power = float(rejected[signal].mean()) if signal.any() else None
    discoveries = int(rejected.sum())
    fdr = float((rejected & null).sum() / discoveries) if discoveries else 0.0
    metrics = {"type_i_error": type_i, "power": power, "fdr": fdr, "discoveries": discoveries}
    passed = type_i is None or type_i <= float(spec.get("maximum_type_i_error", 1.0))
    passed = passed and fdr <= float(spec.get("maximum_fdr", 1.0))
    passed = passed and (power is None or power >= float(spec.get("minimum_power", 0.0)))
    return passed, metrics


def _compare_effect_error(observed, reference, spec: Mapping[str, Any]):
    import numpy as np

    observed, reference = _aligned_tables(
        observed, reference, spec.get("key_columns") or ()
    )
    observed_column = str(spec["observed_value_column"])
    reference_column = str(spec["reference_value_column"])
    if observed_column not in observed or reference_column not in reference:
        raise ValueError("effect comparison columns are missing")
    left = observed[observed_column].to_numpy(dtype=float)
    right = reference[reference_column].to_numpy(dtype=float)
    if not np.isfinite(left).all() or not np.isfinite(right).all():
        raise ValueError("effect comparison contains non-finite values")
    error = left - right
    mae = float(np.mean(np.abs(error)))
    rmse = float(np.sqrt(np.mean(error ** 2)))
    passed = mae <= float(spec.get("maximum_mae", math.inf))
    passed = passed and rmse <= float(spec.get("maximum_rmse", math.inf))
    return passed, {"mae": mae, "rmse": rmse, "row_count": int(len(left))}
