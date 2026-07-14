from __future__ import annotations

import math
from pathlib import Path

from pertura_bench.edger_golden import _numeric_error, _read_rows, _write_case


def test_golden_numeric_error_accepts_absolute_or_relative_tolerance() -> None:
    assert _numeric_error(1.0, 1.0 + 1e-9) <= 1e-7
    assert _numeric_error(1e9, 1e9 + 1.0) <= 1e-7
    assert _numeric_error(float("nan"), float("nan")) == 0.0
    assert _numeric_error(float("inf"), float("inf")) == 0.0
    assert _numeric_error(1.0, 2.0) > 1e-7


def test_edger_golden_fixture_is_deterministic_and_non_degenerate(
    tmp_path: Path,
) -> None:
    first_counts, first_metadata = _write_case(
        tmp_path / "first",
        paired=True,
        baseline_units=("r1", "r2", "r3"),
        target_units=("r1", "r2", "r3"),
    )
    second_counts, second_metadata = _write_case(
        tmp_path / "second",
        paired=True,
        baseline_units=("r1", "r2", "r3"),
        target_units=("r1", "r2", "r3"),
    )

    assert first_counts.read_bytes() == second_counts.read_bytes()
    assert first_metadata.read_bytes() == second_metadata.read_bytes()

    rows = _read_rows(first_counts)
    metadata = _read_rows(first_metadata)
    null_gene = next(row for row in rows if row["gene"] == "G12")
    totals: dict[tuple[str, str], int] = {}
    for item in metadata:
        key = (item["replicate"], item["condition"])
        totals[key] = totals.get(key, 0) + int(null_gene[item["cell_id"]])

    paired_differences = {
        totals[(unit, "target")] - totals[(unit, "baseline")]
        for unit in ("r1", "r2", "r3")
    }
    assert paired_differences != {0}
    assert len(paired_differences) > 1
