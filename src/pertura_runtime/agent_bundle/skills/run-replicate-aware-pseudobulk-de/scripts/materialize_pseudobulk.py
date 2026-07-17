#!/usr/bin/env python3
"""Aggregate registered single-cell counts by independent unit and condition."""

from __future__ import annotations

import json
import sys
from pathlib import Path


def _path(value: str, *, base: Path) -> Path:
    path = Path(value).expanduser()
    return (path if path.is_absolute() else base / path).resolve()


def _table(path: Path):
    import pandas as pd

    suffixes = {suffix.lower() for suffix in path.suffixes}
    separator = "\t" if suffixes.intersection({".tsv", ".txt"}) else ","
    return pd.read_csv(path, sep=separator, dtype=str)


def _require_columns(frame, columns: list[str], *, label: str) -> None:
    missing = [column for column in columns if column not in frame.columns]
    if missing:
        raise ValueError(f"{label} is missing columns: {missing}")


def main(argv: list[str] | None = None) -> int:
    import anndata as ad
    import numpy as np
    import pandas as pd
    from scipy import sparse

    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        raise SystemExit("usage: materialize_pseudobulk.py CONFIG.json")
    config_path = Path(args[0]).expanduser().resolve()
    config = json.loads(config_path.read_text(encoding="utf-8"))
    base = config_path.parent

    input_path = _path(str(config["input_h5ad"]), base=base)
    output_counts = _path(str(config["output_counts"]), base=base)
    output_samples = _path(str(config["output_samples"]), base=base)
    output_accounting = (
        _path(str(config["output_accounting"]), base=base)
        if config.get("output_accounting")
        else None
    )
    unit_column = str(config["unit_column"])
    condition_column = str(config["condition_column"])
    target_column = str(config.get("target_column") or "")
    cell_id_column = str(config.get("cell_id_column") or "cell_id")
    if (
        unit_column == cell_id_column
        or bool(config.get("unit_is_cell"))
        or bool(config.get("unit_is_guide"))
    ):
        raise ValueError("cells and guides cannot be biological replicates")

    data = ad.read_h5ad(input_path)
    cell_ids = pd.Index(data.obs_names.astype(str), name=cell_id_column)
    if cell_ids.has_duplicates:
        raise ValueError("input cell identities are duplicated")
    metadata = data.obs.copy()
    metadata.index = cell_ids

    if config.get("metadata_tsv"):
        external = _table(_path(str(config["metadata_tsv"]), base=base))
        _require_columns(external, [cell_id_column], label="metadata")
        if external[cell_id_column].duplicated().any():
            raise ValueError("metadata cell identities are duplicated")
        external = external.set_index(cell_id_column)
        missing = cell_ids.difference(external.index)
        if len(missing):
            raise ValueError(f"metadata lacks {len(missing)} input cells")
        metadata = external.loc[cell_ids].copy()

    selected = np.ones(data.n_obs, dtype=bool)
    if config.get("selection_tsv"):
        selection = _table(_path(str(config["selection_tsv"]), base=base))
        selection_column = str(config.get("selection_cell_id_column") or cell_id_column)
        _require_columns(selection, [selection_column], label="selection")
        if selection[selection_column].duplicated().any():
            raise ValueError("selection cell identities are duplicated")
        selection_ids = pd.Index(selection[selection_column].astype(str))
        unknown = selection_ids.difference(cell_ids)
        if len(unknown):
            raise ValueError(f"selection contains {len(unknown)} unknown cells")
        selected = cell_ids.isin(selection_ids)
    if not selected.any():
        raise ValueError("selection contains no cells")

    grouping = [unit_column, condition_column]
    if target_column:
        grouping.append(target_column)
    _require_columns(metadata, grouping, label="metadata")
    selected_metadata = metadata.loc[cell_ids[selected], grouping].copy()
    if selected_metadata.isna().any().any():
        raise ValueError("selected grouping metadata contains missing values")
    selected_metadata = selected_metadata.astype(str)

    layer = config.get("layer")
    matrix = data.layers[str(layer)] if layer else data.X
    matrix = matrix[selected]
    matrix = sparse.csr_matrix(matrix)
    if matrix.data.size and (
        not np.isfinite(matrix.data).all()
        or (matrix.data < 0).any()
        or not np.allclose(matrix.data, np.rint(matrix.data), atol=1e-7)
    ):
        raise ValueError("counts must be finite nonnegative integers")

    keys = pd.MultiIndex.from_frame(selected_metadata[grouping])
    unique_keys = sorted(set(keys.tolist()))
    key_to_index = {key: index for index, key in enumerate(unique_keys)}
    codes = np.array([key_to_index[key] for key in keys.tolist()], dtype=int)
    indicator = sparse.csr_matrix(
        (np.ones(len(codes), dtype=np.int64), (np.arange(len(codes)), codes)),
        shape=(len(codes), len(unique_keys)),
    )
    aggregated = (matrix.T @ indicator).toarray()
    if not np.allclose(aggregated, np.rint(aggregated), atol=1e-7):
        raise ValueError("aggregated counts are not integers")
    aggregated = np.rint(aggregated).astype(np.int64, copy=False)

    sample_rows = []
    sample_ids = []
    sample_format = str(
        config.get("sample_id_format")
        or ("{target}__{unit}__{condition}" if target_column else "{unit}__{condition}")
    )
    for index, key in enumerate(unique_keys):
        values = dict(zip(grouping, key))
        fields = {
            "unit": values[unit_column],
            "condition": values[condition_column],
            "target": values.get(target_column, ""),
        }
        sample_id = sample_format.format(**fields)
        if sample_id in sample_ids:
            raise ValueError("sample_id_format creates duplicate sample identities")
        sample_ids.append(sample_id)
        sample_rows.append(
            {
                "sample_id": sample_id,
                **values,
                "n_cells": int((codes == index).sum()),
            }
        )

    gene_column = str(config.get("gene_column") or "")
    if gene_column:
        if gene_column not in data.var.columns:
            raise ValueError(f"gene column is absent: {gene_column}")
        genes = data.var[gene_column].astype(str).tolist()
    else:
        genes = data.var_names.astype(str).tolist()
    if len(set(genes)) != len(genes):
        raise ValueError("gene identities are duplicated")

    output_counts.parent.mkdir(parents=True, exist_ok=True)
    output_samples.parent.mkdir(parents=True, exist_ok=True)
    counts_frame = pd.DataFrame(aggregated, columns=sample_ids)
    counts_frame.insert(0, "gene", genes)
    counts_frame.to_csv(output_counts, sep="\t", index=False)
    pd.DataFrame(sample_rows).to_csv(output_samples, sep="\t", index=False)
    if output_accounting is not None:
        output_accounting.parent.mkdir(parents=True, exist_ok=True)
        output_accounting.write_text(
            json.dumps(
                {
                    "schema_version": "pertura-pseudobulk-materialization-v1",
                    "input_cells": int(data.n_obs),
                    "selected_cells": int(selected.sum()),
                    "genes": int(data.n_vars),
                    "samples": len(sample_ids),
                    "analysis_unit_column": unit_column,
                    "condition_column": condition_column,
                    "target_column": target_column or None,
                    "cell_is_replicate": False,
                    "guide_is_replicate": False,
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
