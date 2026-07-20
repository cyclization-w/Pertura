from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator, Sequence


MAX_BACKED_SOURCE_CHUNK_ROWS = 512


@dataclass
class BackedSelectionStats:
    block_reads: int = 0
    source_rows_read: int = 0
    selected_rows: int = 0


def iter_backed_row_selection(
    matrix,
    row_indices: Sequence[int],
    *,
    column_indices: Sequence[int] | None = None,
    chunk_rows: int = MAX_BACKED_SOURCE_CHUNK_ROWS,
    stats: BackedSelectionStats | None = None,
) -> Iterator[tuple[object, object]]:
    """Yield selected rows without issuing fancy indexes to a backed matrix.

    HDF5-backed AnnData matrices can repeatedly decompress the same physical
    chunks when passed a non-contiguous integer or boolean row index.  Read
    bounded, contiguous source-row ranges instead, then apply row and optional
    column selection after each range is resident in memory.

    ``row_indices`` must be unique and increasing.  This matches AnnData's
    source order and makes the output deterministic without retaining a second
    permutation-sized working array.
    """

    import numpy as np
    from scipy import sparse

    selected_rows = np.asarray(row_indices, dtype=np.int64)
    if selected_rows.ndim != 1:
        raise ValueError("row_indices must be one-dimensional")
    if selected_rows.size and (
        selected_rows[0] < 0 or selected_rows[-1] >= int(matrix.shape[0])
    ):
        raise IndexError("row_indices are outside the backed matrix")
    if selected_rows.size > 1 and np.any(selected_rows[1:] <= selected_rows[:-1]):
        raise ValueError("row_indices must be unique and strictly increasing")

    selected_columns = None
    if column_indices is not None:
        selected_columns = np.asarray(column_indices, dtype=np.int64)
        if selected_columns.ndim != 1:
            raise ValueError("column_indices must be one-dimensional")
        if selected_columns.size and (
            selected_columns.min() < 0
            or selected_columns.max() >= int(matrix.shape[1])
        ):
            raise IndexError("column_indices are outside the backed matrix")

    requested_chunk_rows = int(chunk_rows)
    if requested_chunk_rows < 1:
        raise ValueError("chunk_rows must be positive")
    source_chunk_rows = min(requested_chunk_rows, MAX_BACKED_SOURCE_CHUNK_ROWS)
    counters = stats if stats is not None else BackedSelectionStats()

    for source_start in range(0, int(matrix.shape[0]), source_chunk_rows):
        source_stop = min(int(matrix.shape[0]), source_start + source_chunk_rows)
        selected_start = int(np.searchsorted(selected_rows, source_start, side="left"))
        selected_stop = int(np.searchsorted(selected_rows, source_stop, side="left"))
        if selected_start == selected_stop:
            continue

        # This is deliberately the only index sent to the backed matrix.
        source_block = matrix[source_start:source_stop, :]
        if hasattr(source_block, "to_memory"):
            source_block = source_block.to_memory()
        if sparse.issparse(source_block):
            source_block = source_block.tocsr()
        else:
            source_block = np.asarray(source_block)

        block_rows = selected_rows[selected_start:selected_stop]
        local_rows = block_rows - source_start
        selected_block = source_block[local_rows, :]
        if selected_columns is not None:
            selected_block = selected_block[:, selected_columns]

        counters.block_reads += 1
        counters.source_rows_read += source_stop - source_start
        counters.selected_rows += len(block_rows)
        yield block_rows, selected_block


def materialize_backed_selection(
    matrix,
    row_indices: Sequence[int],
    *,
    column_indices: Sequence[int] | None = None,
    chunk_rows: int = MAX_BACKED_SOURCE_CHUNK_ROWS,
) -> tuple[object, BackedSelectionStats]:
    """Materialize a backed selection using contiguous source-row reads."""

    import numpy as np
    from scipy import sparse

    stats = BackedSelectionStats()
    blocks = [
        block
        for _, block in iter_backed_row_selection(
            matrix,
            row_indices,
            column_indices=column_indices,
            chunk_rows=chunk_rows,
            stats=stats,
        )
    ]
    column_count = (
        len(column_indices) if column_indices is not None else int(matrix.shape[1])
    )
    if not blocks:
        return np.empty((0, column_count)), stats
    if any(sparse.issparse(block) for block in blocks):
        return sparse.vstack(
            [
                block.tocsr() if sparse.issparse(block) else sparse.csr_matrix(block)
                for block in blocks
            ],
            format="csr",
        ), stats
    return np.concatenate([np.asarray(block) for block in blocks], axis=0), stats
