from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

import numpy as np


STANDARD_BUNDLE_NAME = "prediction_bundle.zarr"
ROW_TABLE_NAME = "prediction_rows.parquet"
FEATURE_TABLE_NAME = "prediction_features.parquet"
METADATA_NAME = "prediction_bundle_metadata.json"


def write_chunked_prediction_bundle(
    root: Path,
    *,
    prediction: Any,
    observed: Any,
    row_ids: np.ndarray,
    feature_ids: np.ndarray,
    metadata: Mapping[str, Any],
    uncertainty: Mapping[str, Any] | None,
    chunk_rows: int,
) -> tuple[Path, Path, Path, Path]:
    """Write the provider-neutral P5 store without constructing another full matrix."""

    import pandas as pd
    import zarr

    bundle = Path(root) / STANDARD_BUNDLE_NAME
    rows_path = Path(root) / ROW_TABLE_NAME
    features_path = Path(root) / FEATURE_TABLE_NAME
    metadata_path = Path(root) / METADATA_NAME
    row_count, feature_count = _shape_2d(prediction, "predictions")
    if _shape_2d(observed, "observed") != (row_count, feature_count):
        raise ValueError("prediction and observed matrix dimensions disagree")
    chunks = (max(1, min(int(chunk_rows), row_count)), feature_count)
    group = zarr.open_group(str(bundle), mode="w")
    targets = {
        "predictions": _create_float_array(group, "predictions", (row_count, feature_count), chunks),
        "observed": _create_float_array(group, "observed", (row_count, feature_count), chunks),
    }
    for name, values in sorted((uncertainty or {}).items()):
        if _shape_2d(values, name) != (row_count, feature_count):
            raise ValueError(f"uncertainty array {name} must match prediction shape")
        targets[name] = _create_float_array(group, name, (row_count, feature_count), chunks)
    sources = {"predictions": prediction, "observed": observed} | dict(uncertainty or {})
    for start in range(0, row_count, chunks[0]):
        stop = min(row_count, start + chunks[0])
        for name, source in sources.items():
            block = _dense_block(source, start, stop)
            if block.shape != (stop - start, feature_count) or not np.all(np.isfinite(block)):
                raise ValueError(f"{name} contains invalid shape, NA, or infinite values")
            targets[name][start:stop, :] = block

    per_row: dict[str, list[str]] = {"row_id": np.asarray(row_ids, str).tolist()}
    sidecar: dict[str, Any] = {}
    for name, values in sorted(metadata.items()):
        if isinstance(values, (list, tuple)) and len(values) == row_count:
            per_row[str(name)] = [str(value) for value in values]
        else:
            sidecar[str(name)] = values
    pd.DataFrame(per_row).to_parquet(rows_path, index=False)
    pd.DataFrame({"feature_id": np.asarray(feature_ids, str)}).to_parquet(
        features_path, index=False
    )
    metadata_path.write_text(
        json.dumps(sidecar, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return bundle, rows_path, features_path, metadata_path


def open_chunked_prediction_bundle(
    bundle: Path,
    rows_path: Path,
    features_path: Path,
    metadata_path: Path,
) -> tuple[Any, Any, np.ndarray, np.ndarray, dict[str, Any], dict[str, Any] | None]:
    import pandas as pd
    import zarr

    group = zarr.open_group(str(bundle), mode="r")
    if "predictions" not in group or "observed" not in group:
        raise ValueError("chunked prediction store lacks predictions or observed")
    rows = pd.read_parquet(rows_path)
    features = pd.read_parquet(features_path)
    if "row_id" not in rows or "feature_id" not in features:
        raise ValueError("prediction index sidecars are invalid")
    metadata = {
        str(column): rows[column].astype(str).tolist()
        for column in rows.columns
        if column != "row_id"
    }
    metadata.update(json.loads(metadata_path.read_text(encoding="utf-8")))
    uncertainty = {
        name: group[name]
        for name in ("lower", "upper", "standard_error")
        if name in group
    }
    return (
        group["predictions"],
        group["observed"],
        np.asarray(rows["row_id"].astype(str).tolist(), dtype=str),
        np.asarray(features["feature_id"].astype(str).tolist(), dtype=str),
        metadata,
        uncertainty or None,
    )


def array_content_sha256(values: Any, *, chunk_rows: int) -> str:
    rows, columns = _shape_2d(values, "array")
    digest = hashlib.sha256()
    digest.update(json.dumps({"dtype": "float64", "shape": [rows, columns]}, sort_keys=True).encode("utf-8"))
    for start in range(0, rows, max(1, int(chunk_rows))):
        block = _dense_block(values, start, min(rows, start + max(1, int(chunk_rows))))
        digest.update(np.ascontiguousarray(block, dtype="<f8").tobytes())
    return "sha256:" + digest.hexdigest()


def materialize_with_budget(
    values: Any,
    *,
    max_bytes: int,
    arrays: int,
    label: str,
) -> np.ndarray:
    rows, columns = _shape_2d(values, label)
    required = rows * columns * np.dtype("float64").itemsize * max(1, int(arrays))
    if required > max_bytes:
        raise MemoryError(
            f"{label} requires {required / 1024**3:.3f} GB of dense working memory; "
            "use a smaller frozen subset or a blockwise evaluator"
        )
    return np.asarray(values[:, :], dtype=float)


def _shape_2d(values: Any, label: str) -> tuple[int, int]:
    shape = tuple(int(item) for item in getattr(values, "shape", ()))
    if len(shape) != 2 or shape[0] < 1 or shape[1] < 1:
        raise ValueError(f"{label} must be a non-empty two-dimensional matrix")
    return shape[0], shape[1]


def _dense_block(values: Any, start: int, stop: int) -> np.ndarray:
    block = values[start:stop, :]
    block = block.to_memory() if hasattr(block, "to_memory") else block
    block = block.toarray() if hasattr(block, "toarray") else block
    return np.asarray(block, dtype=float)


def _create_float_array(group: Any, name: str, shape: tuple[int, int], chunks: tuple[int, int]) -> Any:
    return group.create_array(
        name,
        shape=shape,
        chunks=chunks,
        dtype="float64",
        overwrite=True,
    )
