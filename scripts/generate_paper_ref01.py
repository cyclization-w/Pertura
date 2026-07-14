from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "pertura-paper-ref01-v1"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return "sha256:" + digest.hexdigest()


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _matrix_profile(matrix: Any) -> dict[str, Any]:
    import numpy as np
    from scipy import sparse

    n_rows, n_columns = (int(value) for value in matrix.shape)
    sample_rows = min(n_rows, 256)
    sample_columns = min(n_columns, 512)
    block = matrix[:sample_rows, :sample_columns]
    if hasattr(block, "to_memory"):
        block = block.to_memory()

    is_sparse = sparse.issparse(block)
    if is_sparse:
        block = block.tocsr()
        values = np.asarray(block.data)
        nonzero_count = int(block.nnz)
    else:
        dense = np.asarray(block)
        values = dense.reshape(-1)
        nonzero_count = int(np.count_nonzero(dense))

    sampled_entries = sample_rows * sample_columns
    finite = bool(np.isfinite(values).all()) if values.size else True
    nonnegative = bool((values >= 0).all()) if values.size else True
    integer_like = (
        bool(np.allclose(values, np.rint(values), atol=1e-6, rtol=0.0))
        if values.size
        else True
    )
    if values.size:
        observed_min = float(values.min())
        observed_max = float(values.max())
        if nonzero_count < sampled_entries:
            observed_min = min(observed_min, 0.0)
    else:
        observed_min = 0.0
        observed_max = 0.0

    if finite and nonnegative and integer_like:
        scale_class = "count_like"
    elif finite and nonnegative:
        scale_class = "nonnegative_transformed"
    elif finite:
        scale_class = "signed_or_scaled"
    else:
        scale_class = "nonfinite"

    dtype = getattr(matrix, "dtype", None)
    return {
        "shape": [n_rows, n_columns],
        "dtype": str(dtype) if dtype is not None else None,
        "python_type": f"{type(matrix).__module__}.{type(matrix).__name__}",
        "storage": "sparse" if is_sparse else "dense",
        "sample_rows": sample_rows,
        "sample_columns": sample_columns,
        "sample_nonzero_fraction": (
            nonzero_count / sampled_entries if sampled_entries else 0.0
        ),
        "sample_finite": finite,
        "sample_nonnegative": nonnegative,
        "sample_integer_like": integer_like,
        "sample_min": observed_min,
        "sample_max": observed_max,
        "sample_scale_class": scale_class,
        "classification_is_sample_based": True,
    }


def _read_selection(
    path: Path,
) -> tuple[list[dict[str, str]], str]:
    cell_digest = hashlib.sha256()
    rows: list[dict[str, str]] = []
    with gzip.open(path, "rt", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        required = {"cell_id", "group_id", "unit_id", "is_control"}
        missing = required - set(reader.fieldnames or ())
        if missing:
            raise ValueError(
                f"selection file {path} is missing columns: {sorted(missing)}"
            )
        for row in reader:
            cell_id = str(row["cell_id"])
            cell_digest.update((cell_id + "\n").encode("utf-8"))
            rows.append({key: str(value) for key, value in row.items()})
    return rows, "sha256:" + cell_digest.hexdigest()


def _distribution(values: list[int]) -> dict[str, float | int]:
    if not values:
        return {"minimum": 0, "median": 0.0, "maximum": 0}
    return {
        "minimum": int(min(values)),
        "median": float(statistics.median(values)),
        "maximum": int(max(values)),
    }


def _split_tabulation(
    paper_root: Path,
    split_record: dict[str, Any],
) -> tuple[dict[str, Any], set[str]]:
    selection_path = paper_root / split_record["cell_selection_path"]
    if not selection_path.is_file():
        raise FileNotFoundError(selection_path)
    file_hash = _sha256(selection_path)
    if file_hash != split_record["cell_selection_file_sha256"]:
        raise ValueError(f"selection file hash drift: {selection_path}")

    rows, cell_ids_hash = _read_selection(selection_path)
    if cell_ids_hash != split_record["cell_ids_sha256"]:
        raise ValueError(f"cell identity hash drift: {selection_path}")
    if len(rows) != split_record["selected_cell_count"]:
        raise ValueError(f"selection row-count drift: {selection_path}")

    cell_ids = [row["cell_id"] for row in rows]
    if len(cell_ids) != len(set(cell_ids)):
        raise ValueError(f"duplicate cell identities: {selection_path}")

    group_counts = Counter(row["group_id"] for row in rows)
    group_units: dict[str, set[str]] = defaultdict(set)
    unit_counts = Counter(row["unit_id"] for row in rows)
    control_rows = [row for row in rows if row["is_control"].lower() == "true"]
    case_rows = [row for row in rows if row["is_control"].lower() != "true"]
    for row in rows:
        group_units[row["group_id"]].add(row["unit_id"])

    group_table = [
        {
            "group_id": group_id,
            "cell_count": int(group_counts[group_id]),
            "unit_count": len(group_units[group_id]),
        }
        for group_id in sorted(group_counts)
    ]
    return (
        {
            "split": split_record["split"],
            "split_id": split_record["split_id"],
            "group_column": split_record["group_column"],
            "unit_id_column": split_record["unit_id_column"],
            "strata_columns": split_record["strata_columns"],
            "include_filters": split_record["include_filters"],
            "exclude_filters": split_record["exclude_filters"],
            "control_selector": split_record["control_selector"],
            "seed": split_record["seed"],
            "selected_cell_count": len(rows),
            "case_cell_count": len(case_rows),
            "control_cell_count": len(control_rows),
            "group_count": len(group_counts),
            "unit_count": len(unit_counts),
            "control_unit_count": len({row["unit_id"] for row in control_rows}),
            "cells_per_group": _distribution(list(group_counts.values())),
            "cells_per_unit": _distribution(list(unit_counts.values())),
            "units_per_group": _distribution(
                [len(values) for values in group_units.values()]
            ),
            "groups": group_table,
            "cell_ids_sha256": cell_ids_hash,
            "selection_file_sha256": file_hash,
        },
        set(cell_ids),
    )


def generate(
    datasets_path: Path,
    splits_path: Path,
    output_dir: Path,
) -> dict[str, Any]:
    import anndata as ad

    datasets_payload = json.loads(datasets_path.read_text(encoding="utf-8"))
    splits_payload = json.loads(splits_path.read_text(encoding="utf-8"))
    paper_root = splits_path.resolve().parent.parent
    output_dir.mkdir(parents=True, exist_ok=True)

    profiles: dict[str, Any] = {}
    tabulations: dict[str, Any] = {}

    for dataset_id in sorted(datasets_payload["datasets"]):
        dataset_record = datasets_payload["datasets"][dataset_id]
        artifact = Path(dataset_record["artifact_path"]).resolve()
        if not artifact.is_file():
            raise FileNotFoundError(artifact)
        if artifact.stat().st_size != int(dataset_record["size_bytes"]):
            raise ValueError(f"dataset size drift: {dataset_id}")

        print(f"Profiling {dataset_id}: {artifact}", flush=True)
        data = ad.read_h5ad(artifact, backed="r")
        try:
            profiles[dataset_id] = {
                "artifact_sha256": dataset_record["artifact_sha256"],
                "size_bytes": artifact.stat().st_size,
                "shape": [int(data.n_obs), int(data.n_vars)],
                "obs_names_unique": bool(data.obs_names.is_unique),
                "var_names_unique": bool(data.var_names.is_unique),
                "obs_columns": sorted(str(value) for value in data.obs.columns),
                "var_columns": sorted(str(value) for value in data.var.columns),
                "x": _matrix_profile(data.X),
                "layers": {
                    str(name): _matrix_profile(data.layers[name])
                    for name in sorted(data.layers.keys())
                },
                "obsm_keys": sorted(str(value) for value in data.obsm.keys()),
                "uns_keys": sorted(str(value) for value in data.uns.keys()),
            }
        finally:
            if getattr(data, "file", None) is not None:
                data.file.close()

        split_entry = splits_payload["datasets"][dataset_id]
        split_tables: dict[str, Any] = {}
        split_cells: dict[str, set[str]] = {}
        for split in ("calibration", "evaluation"):
            table, cells = _split_tabulation(paper_root, split_entry[split])
            split_tables[split] = table
            split_cells[split] = cells

        overlap = split_cells["calibration"] & split_cells["evaluation"]
        if overlap:
            raise ValueError(
                f"calibration/evaluation cell overlap for {dataset_id}: "
                f"{len(overlap)}"
            )
        tabulations[dataset_id] = {
            "study_role": dataset_record["study_role"],
            "experiment_class": dataset_record["experiment_class"],
            "design_confirmation_status": "pending_manual_reference_review",
            "calibration": split_tables["calibration"],
            "evaluation": split_tables["evaluation"],
            "cell_overlap_count": 0,
        }

    profiles_path = output_dir / "dataset_profiles.json"
    tabulations_path = output_dir / "design_tabulations.json"
    _write_json(
        profiles_path,
        {
            "schema_version": SCHEMA_VERSION,
            "reference_pack_id": "REF-01",
            "generator_job_id": "REF-01-A",
            "datasets": profiles,
        },
    )
    _write_json(
        tabulations_path,
        {
            "schema_version": SCHEMA_VERSION,
            "reference_pack_id": "REF-01",
            "generator_job_id": "REF-01-A",
            "datasets": tabulations,
        },
    )

    script_path = Path(__file__).resolve()
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "reference_pack_id": "REF-01",
        "completed_jobs": ["REF-01-A"],
        "pending_jobs": ["REF-01-B"],
        "readiness": "generated_partial",
        "independent_of_pertura_results": True,
        "input_files": {
            "datasets.json": _sha256(datasets_path),
            "splits.json": _sha256(splits_path),
        },
        "generator_script_sha256": _sha256(script_path),
        "output_files": {
            profiles_path.name: _sha256(profiles_path),
            tabulations_path.name: _sha256(tabulations_path),
        },
        "limitations": [
            "Matrix scale labels are based on deterministic bounded samples.",
            "Design confirmations are not inferred and remain pending manual reference review.",
            "Planted intake and design failure fixtures are generated separately by REF-01-B."
        ],
    }
    manifest_path = output_dir / "manifest.json"
    _write_json(manifest_path, manifest)
    return {
        "reference_pack_id": "REF-01",
        "readiness": manifest["readiness"],
        "dataset_count": len(profiles),
        "output_dir": str(output_dir),
        "manifest_sha256": _sha256(manifest_path),
        "outputs": manifest["output_files"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Generate independent REF-01 dataset-profile and design-tabulation "
            "references without importing Pertura runtime results."
        )
    )
    parser.add_argument("--datasets", type=Path, required=True)
    parser.add_argument("--splits", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = generate(
        args.datasets.resolve(),
        args.splits.resolve(),
        args.output.resolve(),
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
