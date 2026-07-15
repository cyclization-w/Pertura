from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import io
import json
import math
import zipfile
from pathlib import Path
from statistics import NormalDist
from typing import Any, Iterable


SCHEMA_VERSION = "pertura-paper-ref10-v1"
DATASET_ID = "norman_k562_crispra_2019"
SEED = 1729
N_UNITS_PER_SPLIT = 24
N_FEATURES = 128
NOMINAL_COVERAGE = 0.90


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _array_sha256(values: Any) -> str:
    import numpy as np

    array = np.ascontiguousarray(values)
    digest = hashlib.sha256()
    digest.update(str(array.dtype).encode("ascii"))
    digest.update(b"\0")
    digest.update(json.dumps(list(array.shape)).encode("ascii"))
    digest.update(b"\0")
    digest.update(array.tobytes(order="C"))
    return "sha256:" + digest.hexdigest()


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _write_tsv(
    path: Path, fields: list[str], rows: Iterable[dict[str, Any]]
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=fields,
            delimiter="\t",
            lineterminator="\n",
            extrasaction="ignore",
        )
        writer.writeheader()
        writer.writerows(rows)


def _write_deterministic_npz(path: Path, arrays: dict[str, Any]) -> None:
    """Write an NPZ with fixed ZIP metadata so content hashes are reproducible."""
    import numpy as np

    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(
        path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
    ) as archive:
        for name in sorted(arrays):
            buffer = io.BytesIO()
            np.save(buffer, np.asarray(arrays[name]), allow_pickle=False)
            info = zipfile.ZipInfo(f"{name}.npy", date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, buffer.getvalue(), compress_type=zipfile.ZIP_DEFLATED)


def _open_selection(path: Path):
    if path.suffix == ".gz":
        return gzip.open(path, "rt", encoding="utf-8", newline="")
    return path.open("r", encoding="utf-8", newline="")


def _split_units(
    splits_path: Path, split_name: str
) -> tuple[list[dict[str, str]], Path, dict[str, Any]]:
    payload = json.loads(splits_path.read_text(encoding="utf-8"))
    try:
        record = payload["datasets"][DATASET_ID][split_name]
    except (KeyError, TypeError) as exc:
        raise ValueError(
            f"frozen split catalog is missing {DATASET_ID}/{split_name}"
        ) from exc
    relative = record.get("cell_selection_path")
    if not relative:
        raise ValueError(f"{split_name} split lacks cell_selection_path")
    paper_root = splits_path.resolve().parent.parent
    selection_path = (paper_root / str(relative)).resolve()
    if not selection_path.is_file():
        raise FileNotFoundError(selection_path)
    expected_hash = record.get("cell_selection_file_sha256")
    if expected_hash and _sha256(selection_path) != expected_hash:
        raise ValueError(f"selection file hash drift: {selection_path}")

    by_group: dict[str, dict[str, str]] = {}
    with _open_selection(selection_path) as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        required = {"cell_id", "group_id", "is_control"}
        missing = required.difference(reader.fieldnames or ())
        if missing:
            raise ValueError(
                f"selection file is missing columns: {sorted(missing)}"
            )
        for raw in reader:
            if str(raw["is_control"]).strip().lower() == "true":
                continue
            cell_id = str(raw["cell_id"])
            group_id = str(raw["group_id"])
            candidate = {"cell_id": cell_id, "group_id": group_id}
            previous = by_group.get(group_id)
            if previous is None or cell_id < previous["cell_id"]:
                by_group[group_id] = candidate
    selected = [by_group[key] for key in sorted(by_group)[:N_UNITS_PER_SPLIT]]
    if len(selected) < N_UNITS_PER_SPLIT:
        raise ValueError(
            f"{split_name} split has only {len(selected)} non-control groups; "
            f"REF-10 requires {N_UNITS_PER_SPLIT}"
        )
    return selected, selection_path, record


def _stable_target(group_id: str) -> str:
    digest = hashlib.sha256(group_id.encode("utf-8")).hexdigest()[:12]
    return f"virtual_target_{digest}"


def _observed_effects(groups: list[str], feature_count: int) -> Any:
    import numpy as np

    x = np.linspace(-math.pi, math.pi, feature_count, endpoint=False)
    rows = []
    for group in groups:
        digest = hashlib.sha256(group.encode("utf-8")).digest()
        program = int.from_bytes(digest[:2], "little") % 5 + 1
        amplitude = 0.8 + (int.from_bytes(digest[2:4], "little") % 50) / 100.0
        phase = (int.from_bytes(digest[4:6], "little") % 628) / 100.0
        row = amplitude * np.sin(program * x + phase)
        row += 0.35 * np.cos((program + 2) * x - phase / 2.0)
        row += 0.08 * np.sin(np.arange(feature_count) * 0.37 + phase)
        rows.append(row)
    return np.asarray(rows, dtype=np.float64)


def _rank(values: Any) -> Any:
    import numpy as np

    array = np.asarray(values, dtype=float)
    order = np.argsort(array, kind="mergesort")
    ranks = np.empty(len(array), dtype=float)
    ranks[order] = np.arange(len(array), dtype=float)
    for value in np.unique(array):
        indices = np.flatnonzero(array == value)
        ranks[indices] = ranks[indices].mean()
    return ranks


def _median_row_spearman(left: Any, right: Any) -> float:
    import numpy as np

    correlations = []
    for observed, expected in zip(left, right, strict=True):
        observed_rank = _rank(observed)
        expected_rank = _rank(expected)
        if np.std(observed_rank) and np.std(expected_rank):
            correlations.append(
                float(np.corrcoef(observed_rank, expected_rank)[0, 1])
            )
    return float(np.median(correlations)) if correlations else 0.0


def _discriminability(prediction: Any, observed: Any) -> float:
    import numpy as np

    hits = 0
    for index, row in enumerate(prediction):
        distances = np.mean((observed - row) ** 2, axis=1)
        hits += int(int(np.argmin(distances)) == index)
    return float(hits / len(prediction))


def _uncertainty_metrics(
    prediction: Any, observed: Any, standard_error: Any | None
) -> tuple[float | None, float | None]:
    import numpy as np

    if standard_error is None:
        return None, None
    levels = (0.50, 0.80, 0.90, 0.95)
    errors = np.abs(observed - prediction)
    empirical = []
    for level in levels:
        z_value = NormalDist().inv_cdf((1.0 + level) / 2.0)
        empirical.append(float((errors <= z_value * standard_error).mean()))
    ece = float(np.mean(np.abs(np.asarray(empirical) - np.asarray(levels))))
    coverage_index = levels.index(NOMINAL_COVERAGE)
    return empirical[coverage_index], ece


def _metrics(
    prediction: Any,
    observed: Any,
    *,
    best_baseline_rmse: float,
    standard_error: Any | None = None,
) -> dict[str, float | None]:
    import numpy as np

    residual = prediction - observed
    mae = float(np.mean(np.abs(residual)))
    rmse = float(np.sqrt(np.mean(residual**2)))
    nonzero = np.abs(observed) > 1e-12
    direction = float(
        (np.sign(prediction[nonzero]) == np.sign(observed[nonzero])).mean()
    )
    coverage, ece = _uncertainty_metrics(prediction, observed, standard_error)
    improvement = float((best_baseline_rmse - rmse) / best_baseline_rmse)
    return {
        "effect_mae": mae,
        "effect_rmse": rmse,
        "direction_accuracy": direction,
        "spearman": _median_row_spearman(prediction, observed),
        "coverage": coverage,
        "expected_calibration_error": ece,
        "discriminability": _discriminability(prediction, observed),
        "baseline_improvement": improvement,
        "source_class_accuracy": 1.0,
    }


def _bundle_arrays(
    prediction: Any,
    observed: Any,
    row_ids: Any,
    feature_ids: Any,
    metadata: dict[str, list[str]],
    *,
    standard_error: Any | None = None,
) -> dict[str, Any]:
    import numpy as np

    arrays: dict[str, Any] = {
        "predictions": prediction,
        "observed": observed,
        "row_ids": row_ids,
        "feature_ids": feature_ids,
        "metadata_json": np.asarray(
            [json.dumps(metadata, sort_keys=True, separators=(",", ":"))]
        ),
    }
    if standard_error is not None:
        z_value = NormalDist().inv_cdf((1.0 + NOMINAL_COVERAGE) / 2.0)
        arrays["standard_error"] = standard_error
        arrays["lower"] = prediction - z_value * standard_error
        arrays["upper"] = prediction + z_value * standard_error
    return arrays


def generate(splits_path: Path, output: Path) -> dict[str, Any]:
    import numpy as np

    calibration, calibration_path, calibration_record = _split_units(
        splits_path, "calibration"
    )
    evaluation, evaluation_path, evaluation_record = _split_units(
        splits_path, "evaluation"
    )
    calibration_groups = [row["group_id"] for row in calibration]
    evaluation_groups = [row["group_id"] for row in evaluation]
    calibration_cells = [row["cell_id"] for row in calibration]
    evaluation_cells = [row["cell_id"] for row in evaluation]
    group_overlap = sorted(set(calibration_groups) & set(evaluation_groups))
    cell_overlap = sorted(set(calibration_cells) & set(evaluation_cells))
    if group_overlap or cell_overlap:
        raise ValueError(
            "frozen Norman calibration/evaluation identities overlap: "
            f"groups={group_overlap[:3]}, cells={cell_overlap[:3]}"
        )

    calibration_targets = [_stable_target(value) for value in calibration_groups]
    evaluation_targets = [_stable_target(value) for value in evaluation_groups]
    target_overlap = sorted(set(calibration_targets) & set(evaluation_targets))
    if target_overlap:
        raise ValueError(f"derived virtual target identities overlap: {target_overlap[:3]}")

    groups = calibration_groups + evaluation_groups
    cells = calibration_cells + evaluation_cells
    targets = calibration_targets + evaluation_targets
    row_ids = np.asarray(
        [f"train_row_{index:03d}" for index in range(len(calibration))]
        + [f"test_row_{index:03d}" for index in range(len(evaluation))]
    )
    feature_ids = np.asarray([f"gene_{index:03d}" for index in range(N_FEATURES)])
    observed = _observed_effects(groups, N_FEATURES)
    rng = np.random.default_rng(SEED)
    noise_scale = 0.08
    clean_prediction = observed + rng.normal(0.0, noise_scale, observed.shape)
    standard_error = np.full(observed.shape, noise_scale, dtype=np.float64)

    train_count = len(calibration)
    evaluation_observed = observed[train_count:]
    training_mean_row = observed[:train_count].mean(axis=0)
    training_mean = np.repeat(
        training_mean_row[None, :], len(evaluation), axis=0
    )
    no_change = np.zeros_like(evaluation_observed)
    no_change_rmse = float(np.sqrt(np.mean((no_change - evaluation_observed) ** 2)))
    training_mean_rmse = float(
        np.sqrt(np.mean((training_mean - evaluation_observed) ** 2))
    )
    best_baseline_rmse = min(no_change_rmse, training_mean_rmse)

    metadata = {
        "perturbation": targets,
        "combo": groups,
        "context": cells,
    }
    split_contract = {
        "schema_version": "pertura-paper-virtual-split-truth-v1",
        "dataset_id": DATASET_ID,
        "heldout_axes": ["perturbation", "combo", "context"],
        "axes": {
            "perturbation": {
                "train": calibration_targets,
                "validation": [],
                "test": evaluation_targets,
            },
            "combo": {
                "train": calibration_groups,
                "validation": [],
                "test": evaluation_groups,
            },
            "context": {
                "train": calibration_cells,
                "validation": [],
                "test": evaluation_cells,
            },
        },
        "overlap_counts": {
            "target": len(target_overlap),
            "construct": len(group_overlap),
            "cell": len(cell_overlap),
        },
        "source_split_ids": {
            "calibration": calibration_record.get("split_id"),
            "evaluation": evaluation_record.get("split_id"),
        },
    }

    output.mkdir(parents=True, exist_ok=True)
    bundles_root = output / "controlled_prediction_bundles"
    bundles_root.mkdir(parents=True, exist_ok=True)
    print("REF-10-A: writing controlled prediction bundles", flush=True)

    full_training_ids = sorted(
        set(calibration_targets + calibration_groups + calibration_cells)
    )
    specifications = [
        {
            "bundle_id": "clean",
            "prediction": clean_prediction,
            "expected_ingest": "accepted",
            "expected_leakage": "clear",
            "expected_evaluation": "limited_uncertainty_missing",
            "model_training_ids": full_training_ids,
        },
        {
            "bundle_id": "clean_uncertainty",
            "prediction": clean_prediction,
            "standard_error": standard_error,
            "expected_ingest": "accepted",
            "expected_leakage": "clear",
            "expected_evaluation": "supported",
            "model_training_ids": full_training_ids,
        },
        {
            "bundle_id": "leaky_target",
            "prediction": clean_prediction,
            "expected_ingest": "accepted",
            "expected_leakage": "blocked",
            "expected_evaluation": "blocked",
            "model_training_ids": full_training_ids + [evaluation_targets[0]],
            "leakage_reasons": ["test_perturbation_in_model_training_ids"],
        },
        {
            "bundle_id": "leaky_cell",
            "prediction": clean_prediction,
            "expected_ingest": "accepted",
            "expected_leakage": "blocked",
            "expected_evaluation": "blocked",
            "model_training_ids": full_training_ids + [evaluation_cells[0]],
            "leakage_reasons": ["test_context_in_model_training_ids"],
        },
        {
            "bundle_id": "transposed",
            "prediction": clean_prediction.T,
            "expected_ingest": "blocked",
            "expected_leakage": "not_run",
            "expected_evaluation": "not_run",
            "model_training_ids": full_training_ids,
            "ingest_reasons": ["prediction_observed_dimension_mismatch"],
        },
        {
            "bundle_id": "duplicate_identity",
            "prediction": clean_prediction,
            "expected_ingest": "blocked",
            "expected_leakage": "not_run",
            "expected_evaluation": "not_run",
            "model_training_ids": full_training_ids,
            "duplicate_first_row": True,
            "ingest_reasons": ["duplicate_row_id"],
        },
        {
            "bundle_id": "direction_flipped",
            "prediction": -clean_prediction,
            "expected_ingest": "accepted",
            "expected_leakage": "clear",
            "expected_evaluation": "limited_model_quality",
            "model_training_ids": full_training_ids,
        },
        {
            "bundle_id": "no_change",
            "prediction": np.vstack(
                [clean_prediction[:train_count], np.zeros_like(evaluation_observed)]
            ),
            "expected_ingest": "accepted",
            "expected_leakage": "clear",
            "expected_evaluation": "baseline_reference",
            "model_training_ids": full_training_ids,
        },
        {
            "bundle_id": "training_mean",
            "prediction": np.vstack(
                [clean_prediction[:train_count], training_mean]
            ),
            "expected_ingest": "accepted",
            "expected_leakage": "clear",
            "expected_evaluation": "baseline_reference",
            "model_training_ids": full_training_ids,
        },
    ]

    truth_records = []
    bundle_paths: dict[str, Path] = {}
    for specification in specifications:
        bundle_id = str(specification["bundle_id"])
        bundle_dir = bundles_root / bundle_id
        bundle_dir.mkdir(parents=True, exist_ok=True)
        local_row_ids = row_ids.copy()
        if specification.get("duplicate_first_row"):
            local_row_ids[1] = local_row_ids[0]
        arrays = _bundle_arrays(
            specification["prediction"],
            observed,
            local_row_ids,
            feature_ids,
            metadata,
            standard_error=specification.get("standard_error"),
        )
        matrix_path = bundle_dir / "prediction_bundle.npz"
        _write_deterministic_npz(matrix_path, arrays)
        record = {
            "bundle_id": bundle_id,
            "source_class": "prediction",
            "dataset_id": DATASET_ID,
            "format": "matrix_bundle",
            "prediction_scale": "effect",
            "matrix_path": matrix_path.relative_to(output).as_posix(),
            "matrix_sha256": _sha256(matrix_path),
            "prediction_shape": list(np.asarray(specification["prediction"]).shape),
            "observed_shape": list(observed.shape),
            "row_count": len(row_ids),
            "feature_count": len(feature_ids),
            "row_index_sha256": _array_sha256(local_row_ids),
            "feature_index_sha256": _array_sha256(feature_ids),
            "prediction_sha256": _array_sha256(specification["prediction"]),
            "observed_sha256": _array_sha256(observed),
            "uncertainty_kind": (
                "standard_error" if specification.get("standard_error") is not None else "none"
            ),
            "model_training_ids": sorted(
                set(str(value) for value in specification["model_training_ids"])
            ),
            "expected_ingest": specification["expected_ingest"],
            "expected_leakage": specification["expected_leakage"],
            "expected_evaluation": specification["expected_evaluation"],
            "ingest_reasons": specification.get("ingest_reasons", []),
            "leakage_reasons": specification.get("leakage_reasons", []),
        }
        _write_json(bundle_dir / "bundle_manifest.json", record)
        record["bundle_manifest_sha256"] = _sha256(
            bundle_dir / "bundle_manifest.json"
        )
        truth_records.append(record)
        bundle_paths[bundle_id] = matrix_path

    truth_path = output / "prediction_bundle_truth.json"
    _write_json(
        truth_path,
        {
            "schema_version": "pertura-paper-prediction-bundle-truth-v1",
            "dataset_id": DATASET_ID,
            "source_class": "prediction",
            "split_contract": split_contract,
            "bundles": truth_records,
            "attack_summary": {
                "leakage_positive_count": 2,
                "leakage_detected_count": 2,
                "leakage_false_positive_count": 0,
                "orientation_attack_count": 1,
                "orientation_detected_count": 1,
                "duplicate_attack_count": 1,
                "duplicate_detected_count": 1,
            },
        },
    )

    observed_path = output / "held_out_observed_effects.tsv"
    _write_tsv(
        observed_path,
        ["row_id", "feature_id", "observed_effect"],
        (
            {
                "row_id": row_ids[train_count + row_index],
                "feature_id": feature_id,
                "observed_effect": f"{evaluation_observed[row_index, feature_index]:.12g}",
            }
            for row_index in range(len(evaluation))
            for feature_index, feature_id in enumerate(feature_ids)
        ),
    )

    print("REF-10-B: computing independent baselines and metrics", flush=True)
    baseline_path = output / "virtual_baseline_reference.tsv"
    baseline_rows = []
    for baseline_id, values in (
        ("no_change", no_change),
        ("training_mean", training_mean),
    ):
        for row_index, row_id in enumerate(row_ids[train_count:]):
            for feature_index, feature_id in enumerate(feature_ids):
                baseline_rows.append(
                    {
                        "baseline_id": baseline_id,
                        "row_id": row_id,
                        "feature_id": feature_id,
                        "value": f"{values[row_index, feature_index]:.12g}",
                        "training_contact_count": train_count,
                        "evaluation_contact_count": 0,
                    }
                )
    _write_tsv(
        baseline_path,
        [
            "baseline_id",
            "row_id",
            "feature_id",
            "value",
            "training_contact_count",
            "evaluation_contact_count",
        ],
        baseline_rows,
    )

    metric_rows = []
    metric_by_bundle: dict[str, dict[str, float | None]] = {}
    valid_metric_bundles = {
        "clean": clean_prediction[train_count:],
        "clean_uncertainty": clean_prediction[train_count:],
        "direction_flipped": -clean_prediction[train_count:],
        "no_change": no_change,
        "training_mean": training_mean,
    }
    for bundle_id, prediction in valid_metric_bundles.items():
        metrics = _metrics(
            prediction,
            evaluation_observed,
            best_baseline_rmse=best_baseline_rmse,
            standard_error=(
                standard_error[train_count:]
                if bundle_id == "clean_uncertainty"
                else None
            ),
        )
        metric_by_bundle[bundle_id] = metrics
        for metric_id, value in metrics.items():
            metric_rows.append(
                {
                    "bundle_id": bundle_id,
                    "metric_id": metric_id,
                    "value": "not_available" if value is None else f"{value:.12g}",
                    "status": "not_available" if value is None else "computed",
                    "source_class": "prediction",
                    "evaluation_split": "evaluation",
                }
            )
    metric_path = output / "virtual_metric_reference.tsv"
    _write_tsv(
        metric_path,
        [
            "bundle_id",
            "metric_id",
            "value",
            "status",
            "source_class",
            "evaluation_split",
        ],
        metric_rows,
    )

    clean_metrics = metric_by_bundle["clean_uncertainty"]
    leakage_recall = 1.0
    leakage_precision = 1.0
    output_paths = {
        "prediction_bundle_truth.json": truth_path,
        "held_out_observed_effects.tsv": observed_path,
        "virtual_baseline_reference.tsv": baseline_path,
        "virtual_metric_reference.tsv": metric_path,
    }
    output_files = {name: _sha256(path) for name, path in output_paths.items()}
    output_files.update(
        {
            f"controlled_prediction_bundles/{bundle_id}/prediction_bundle.npz": _sha256(path)
            for bundle_id, path in sorted(bundle_paths.items())
        }
    )
    metrics = {
        "target_overlap_count": len(target_overlap),
        "construct_overlap_count": len(group_overlap),
        "cell_overlap_count": len(cell_overlap),
        "identity_match": 1.0,
        "orientation_detection": 1.0,
        "duplicate_detection": 1.0,
        "leakage_recall": leakage_recall,
        "leakage_precision": leakage_precision,
        "false_certification_count": 0,
        "baseline_value_mae": 0.0,
        "effect_mae": clean_metrics["effect_mae"],
        "effect_rmse": clean_metrics["effect_rmse"],
        "direction_accuracy": clean_metrics["direction_accuracy"],
        "spearman": clean_metrics["spearman"],
        "coverage": clean_metrics["coverage"],
        "expected_calibration_error": clean_metrics[
            "expected_calibration_error"
        ],
        "discriminability": clean_metrics["discriminability"],
        "baseline_improvement": clean_metrics["baseline_improvement"],
        "source_class_accuracy": 1.0,
    }
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "reference_pack_id": "REF-10",
        "completed_jobs": ["REF-10-A", "REF-10-B"],
        "pending_jobs": [],
        "readiness": "generated",
        "independent_of_pertura_results": True,
        "input_files": {
            "splits.json": _sha256(splits_path),
            "norman_calibration_selection": _sha256(calibration_path),
            "norman_evaluation_selection": _sha256(evaluation_path),
        },
        "generator_script_sha256": _sha256(Path(__file__).resolve()),
        "output_files": output_files,
        "counts": {
            "controlled_prediction_bundles": len(specifications),
            "train_units": train_count,
            "evaluation_units": len(evaluation),
            "features": N_FEATURES,
            "leakage_attacks": 2,
            "identity_attacks": 2,
            "metric_rows": len(metric_rows),
            "baseline_value_rows": len(baseline_rows),
            "real_norman_expression_values_loaded": 0,
            "trained_models": 0,
        },
        "metrics": metrics,
        "parameters": {
            "seed": SEED,
            "units_per_split": N_UNITS_PER_SPLIT,
            "features": N_FEATURES,
            "nominal_coverage": NOMINAL_COVERAGE,
            "fixture_type": "controlled_prediction_fixture",
        },
        "environment": {"numpy": np.__version__},
        "limitations": [
            "REF-10 is a controlled protocol fixture and does not validate real Norman virtual-model performance.",
            "Frozen Norman cell and construct identities bind the split, but the real Norman expression matrix is not read.",
            "No model is trained; clean, leaky, malformed, baseline, and uncertainty bundles are planted attacks or controls.",
            "All model outputs remain in the prediction source class and cannot become measured findings.",
            "P5 remains supplemental and optional; an absent real prediction bundle must not block the primary benchmark.",
            "Self-consistency metrics in this manifest are reference-generation checks; observed Pertura scores are computed later.",
        ],
    }
    manifest_path = output / "manifest.json"
    _write_json(manifest_path, manifest)
    print("REF-10: manifest written", flush=True)
    return {
        "schema_version": SCHEMA_VERSION,
        "reference_pack_id": "REF-10",
        "readiness": "generated",
        "completed_jobs": manifest["completed_jobs"],
        "pending_jobs": [],
        "passed": True,
        "problems": [],
        "manifest_sha256": _sha256(manifest_path),
        "metrics": metrics,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate compact controlled virtual-prediction references."
    )
    parser.add_argument("--splits", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = generate(args.splits.resolve(), args.output.resolve())
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
