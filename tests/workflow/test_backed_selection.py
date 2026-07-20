from __future__ import annotations

import numpy as np
import pytest
from scipy import sparse

from pertura_workflow.capabilities.backed_selection import (
    MAX_BACKED_SOURCE_CHUNK_ROWS,
    materialize_backed_selection,
)


class RecordingMatrix:
    def __init__(self, values: np.ndarray):
        self.values = values
        self.shape = values.shape
        self.keys: list[tuple[object, object]] = []

    def __getitem__(self, key):
        self.keys.append(key)
        return self.values[key]


def test_materialize_backed_selection_uses_only_contiguous_source_rows():
    values = np.arange(12 * 7).reshape(12, 7)
    source = RecordingMatrix(values)
    rows = np.asarray([1, 4, 5, 10])
    columns = np.asarray([6, 2, 4])

    observed, stats = materialize_backed_selection(
        source,
        rows,
        column_indices=columns,
        chunk_rows=4,
    )

    np.testing.assert_array_equal(observed, values[rows][:, columns])
    assert source.keys == [
        (slice(0, 4), slice(None)),
        (slice(4, 8), slice(None)),
        (slice(8, 12), slice(None)),
    ]
    assert stats.block_reads == 3
    assert stats.source_rows_read == 12
    assert stats.selected_rows == 4


def test_materialize_backed_selection_skips_empty_source_ranges_and_caps_reads():
    values = np.arange(1100 * 2).reshape(1100, 2)
    source = RecordingMatrix(values)

    observed, stats = materialize_backed_selection(
        source,
        [0, 1099],
        chunk_rows=8192,
    )

    np.testing.assert_array_equal(observed, values[[0, 1099]])
    assert source.keys == [
        (slice(0, MAX_BACKED_SOURCE_CHUNK_ROWS), slice(None)),
        (slice(1024, 1100), slice(None)),
    ]
    assert stats.source_rows_read == MAX_BACKED_SOURCE_CHUNK_ROWS + 76


def test_materialize_backed_selection_preserves_sparse_values():
    values = sparse.csr_matrix(np.arange(10 * 6).reshape(10, 6))

    observed, stats = materialize_backed_selection(
        values,
        [1, 3, 8],
        column_indices=[5, 0, 2],
        chunk_rows=3,
    )

    assert sparse.isspmatrix_csr(observed)
    np.testing.assert_array_equal(
        observed.toarray(),
        values[[1, 3, 8]][:, [5, 0, 2]].toarray(),
    )
    assert stats.selected_rows == 3


@pytest.mark.parametrize("rows", ([2, 1], [1, 1]))
def test_materialize_backed_selection_rejects_noncanonical_row_order(rows):
    with pytest.raises(ValueError, match="unique and strictly increasing"):
        materialize_backed_selection(np.ones((4, 3)), rows)


def test_materialize_backed_selection_reads_real_sparse_h5ad_contiguously(tmp_path):
    anndata = pytest.importorskip("anndata")
    values = sparse.csr_matrix(np.arange(18 * 9).reshape(18, 9))
    path = tmp_path / "selection.h5ad"
    anndata.AnnData(X=values).write_h5ad(path)
    data = anndata.read_h5ad(path, backed="r")
    try:
        observed, stats = materialize_backed_selection(
            data.X,
            [1, 7, 8, 16],
            column_indices=[8, 2, 5],
            chunk_rows=5,
        )
    finally:
        data.file.close()

    np.testing.assert_array_equal(
        observed.toarray(),
        values[[1, 7, 8, 16]][:, [8, 2, 5]].toarray(),
    )
    assert stats.block_reads == 3
    assert stats.selected_rows == 4
