from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator


@dataclass
class GuideCountSource:
    """Sparse/backed guide-count access with stable row and column identities."""

    matrix: Any
    cell_ids: tuple[str, ...]
    guide_ids: tuple[str, ...]
    source_format: str
    column_indices: tuple[int, ...] | None = None
    _owner: Any = None

    @property
    def shape(self) -> tuple[int, int]:
        return (len(self.cell_ids), len(self.guide_ids))

    def estimated_peak_memory(self, *, chunk_rows: int = 1024) -> int:
        rows = min(max(1, int(chunk_rows)), max(1, self.shape[0]))
        sparse_bytes = 0
        for name in ("data", "indices", "indptr"):
            value = getattr(self.matrix, name, None)
            sparse_bytes += int(getattr(value, "nbytes", 0))
        chunk_bytes = rows * max(1, self.shape[1]) * 8
        return sparse_bytes + chunk_bytes

    def iter_row_chunks(self, chunk_rows: int = 1024) -> Iterator[tuple[int, Any]]:
        from scipy import sparse

        size = max(1, int(chunk_rows))
        columns = list(self.column_indices) if self.column_indices is not None else None
        for start in range(0, self.shape[0], size):
            stop = min(self.shape[0], start + size)
            chunk = self.matrix[start:stop]
            if columns is not None:
                chunk = chunk[:, columns]
            if not sparse.issparse(chunk):
                chunk = sparse.csr_matrix(chunk)
            yield start, chunk.tocsr()

    def column_values(self, column: int, *, chunk_rows: int = 8192):
        import numpy as np

        values = np.zeros(self.shape[0], dtype=float)
        for start, chunk in self.iter_row_chunks(chunk_rows):
            values[start:start + chunk.shape[0]] = chunk[:, column].toarray().ravel()
        return values

    def to_csr(self, *, chunk_rows: int = 4096):
        from scipy import sparse

        chunks = [chunk for _, chunk in self.iter_row_chunks(chunk_rows)]
        if not chunks:
            return sparse.csr_matrix(self.shape, dtype=float)
        return sparse.vstack(chunks, format="csr")

    def close(self) -> None:
        owner = self._owner
        file_manager = getattr(owner, "file", None)
        if file_manager is not None:
            file_manager.close()
        close = getattr(owner, "close", None)
        if callable(close) and file_manager is None:
            close()


def open_guide_count_source(
    path: str | Path,
    *,
    barcode_column: str | None = None,
    row_manifest_path: str | Path | None = None,
    column_manifest_path: str | Path | None = None,
    modality: str | None = None,
    layer: str | None = None,
    max_memory_gb: float = 4.0,
    chunk_rows: int = 1024,
) -> GuideCountSource:
    source = Path(path)
    suffix = source.suffix.lower()
    max_bytes = int(float(max_memory_gb) * 1024**3)
    if max_bytes <= 0:
        raise ValueError("max_memory_gb must be positive")

    if suffix in {".csv", ".tsv", ".txt"}:
        result = _open_table(source, barcode_column=barcode_column, max_bytes=max_bytes)
    elif suffix == ".npz":
        result = _open_npz(
            source,
            row_manifest_path=row_manifest_path,
            column_manifest_path=column_manifest_path,
        )
    elif suffix == ".h5ad":
        result = _open_h5ad(source, layer=layer)
    elif suffix in {".h5mu", ".mudata"}:
        result = _open_mudata(source, modality=modality, layer=layer)
    elif source.is_dir():
        result = _open_tenx_mtx(source)
    elif suffix in {".h5", ".hdf5"}:
        result = _open_tenx_h5(source)
    else:
        raise ValueError(f"unsupported guide-count format: {source.suffix or source.name}")

    if result.estimated_peak_memory(chunk_rows=chunk_rows) > max_bytes:
        result.close()
        raise MemoryError(
            "guide-count working set exceeds "
            f"max_memory_gb={float(max_memory_gb):g} before assignment"
        )
    return result


def _open_table(path: Path, *, barcode_column: str | None, max_bytes: int) -> GuideCountSource:
    from scipy import sparse

    # Text parsing is streaming and sparse, but a very large compatibility table
    # still creates Python parsing overhead that cannot be bounded reliably.
    if path.stat().st_size * 6 > max_bytes:
        raise MemoryError("guide-count text input exceeds the safe compatibility-table budget")
    delimiter = "\t" if path.suffix.lower() in {".tsv", ".txt"} else ","
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle, delimiter=delimiter)
        header = next(reader, None)
        if not header:
            raise ValueError("guide-count table has no header")
        barcode_index = header.index(barcode_column) if barcode_column else 0
        guide_indices = [index for index in range(len(header)) if index != barcode_index]
        guide_ids = tuple(str(header[index]).strip() for index in guide_indices)
        if not guide_ids or any(not item for item in guide_ids):
            raise ValueError("guide-count table has invalid guide identifiers")
        if len(set(guide_ids)) != len(guide_ids):
            raise ValueError("guide-count table has duplicate guide identifiers")
        cell_ids: list[str] = []
        rows: list[int] = []
        columns: list[int] = []
        values: list[int] = []
        for row_index, record in enumerate(reader):
            if len(record) != len(header):
                raise ValueError(f"guide-count row {row_index + 2} has the wrong column count")
            cell = str(record[barcode_index]).strip()
            if not cell:
                raise ValueError(f"guide-count row {row_index + 2} has an empty barcode")
            cell_ids.append(cell)
            for output_column, input_column in enumerate(guide_indices):
                raw = str(record[input_column]).strip()
                try:
                    numeric = float(raw or "0")
                except ValueError as exc:
                    raise ValueError(f"guide count is not numeric at row {row_index + 2}") from exc
                if numeric < 0 or not numeric.is_integer():
                    raise ValueError("guide counts must be nonnegative integers")
                if numeric:
                    rows.append(row_index)
                    columns.append(output_column)
                    values.append(int(numeric))
    if len(set(cell_ids)) != len(cell_ids):
        raise ValueError("guide-count table has duplicate cell barcodes")
    matrix = sparse.csr_matrix(
        (values, (rows, columns)), shape=(len(cell_ids), len(guide_ids)), dtype="int64"
    )
    return GuideCountSource(matrix, tuple(cell_ids), guide_ids, "text_sparse")


def _manifest_column(path: Path, candidates: tuple[str, ...]) -> tuple[str, ...]:
    import pandas as pd

    frame = pd.read_parquet(path)
    for name in candidates:
        if name in frame.columns:
            values = tuple(frame[name].astype(str))
            if len(values) != len(set(values)):
                raise ValueError(f"manifest contains duplicate identities: {name}")
            return values
    raise ValueError(f"manifest {path.name} lacks one of {candidates}")


def _open_npz(
    path: Path,
    *,
    row_manifest_path: str | Path | None,
    column_manifest_path: str | Path | None,
) -> GuideCountSource:
    from scipy import sparse

    rows = Path(row_manifest_path) if row_manifest_path else path.with_name("guide_barcodes.parquet")
    columns = Path(column_manifest_path) if column_manifest_path else path.with_name("guides.parquet")
    if not rows.is_file() or not columns.is_file():
        raise ValueError("CSR guide counts require row and column Parquet manifests")
    matrix = sparse.load_npz(path).tocsr()
    cell_ids = _manifest_column(rows, ("raw_barcode", "cell_id", "barcode"))
    guide_ids = _manifest_column(columns, ("guide_id", "feature_id", "guide"))
    if matrix.shape != (len(cell_ids), len(guide_ids)):
        raise ValueError("guide-count CSR shape does not match its manifests")
    return GuideCountSource(matrix, cell_ids, guide_ids, "materialized_csr")


def _guide_columns(adata: Any) -> tuple[tuple[str, ...], tuple[int, ...] | None]:
    feature_column = next(
        (name for name in ("feature_types", "feature_type") if name in adata.var.columns),
        None,
    )
    if feature_column is None:
        return tuple(adata.var_names.astype(str)), None
    labels = adata.var[feature_column].astype(str).str.lower()
    mask = labels.str.contains("crispr|guide|grna", regex=True).to_numpy()
    if not mask.any():
        raise ValueError("input contains no guide/CRISPR feature type")
    import numpy as np

    indices = tuple(int(item) for item in np.flatnonzero(mask))
    return tuple(adata.var_names[mask].astype(str)), indices


def _open_h5ad(path: Path, *, layer: str | None) -> GuideCountSource:
    import anndata as ad

    owner = ad.read_h5ad(path, backed="r")
    if layer and layer not in owner.layers:
        owner.file.close()
        raise ValueError(f"guide-count layer is missing: {layer}")
    matrix = owner.layers[layer] if layer else owner.X
    guide_ids, columns = _guide_columns(owner)
    return GuideCountSource(
        matrix,
        tuple(owner.obs_names.astype(str)),
        guide_ids,
        "h5ad_backed",
        columns,
        owner,
    )


def _open_mudata(path: Path, *, modality: str | None, layer: str | None) -> GuideCountSource:
    import mudata

    owner = mudata.read_h5mu(path, backed="r")
    keys = tuple(owner.mod.keys())
    selected = modality or next(
        (name for name in keys if any(token in name.lower() for token in ("guide", "gdo", "crispr", "grna"))),
        None,
    )
    if selected is None or selected not in owner.mod:
        owner.close()
        raise ValueError("MuData guide modality is unresolved; provide modality")
    adata = owner.mod[selected]
    if layer and layer not in adata.layers:
        owner.close()
        raise ValueError(f"guide-count layer is missing: {layer}")
    guide_ids, columns = _guide_columns(adata)
    return GuideCountSource(
        adata.layers[layer] if layer else adata.X,
        tuple(adata.obs_names.astype(str)),
        guide_ids,
        "mudata_backed",
        columns,
        owner,
    )


def _from_tenx(adata: Any, source_format: str) -> GuideCountSource:
    guide_ids, columns = _guide_columns(adata)
    return GuideCountSource(
        adata.X,
        tuple(adata.obs_names.astype(str)),
        guide_ids,
        source_format,
        columns,
        adata,
    )


def _open_tenx_mtx(path: Path) -> GuideCountSource:
    import scanpy as sc

    return _from_tenx(sc.read_10x_mtx(path, gex_only=False), "tenx_mex")


def _open_tenx_h5(path: Path) -> GuideCountSource:
    import scanpy as sc

    return _from_tenx(sc.read_10x_h5(path, gex_only=False), "tenx_h5")