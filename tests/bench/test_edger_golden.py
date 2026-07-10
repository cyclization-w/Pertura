from __future__ import annotations

import math

from pertura_bench.edger_golden import _numeric_error


def test_golden_numeric_error_accepts_absolute_or_relative_tolerance() -> None:
    assert _numeric_error(1.0, 1.0 + 1e-9) <= 1e-7
    assert _numeric_error(1e9, 1e9 + 1.0) <= 1e-7
    assert _numeric_error(float("nan"), float("nan")) == 0.0
    assert _numeric_error(float("inf"), float("inf")) == 0.0
    assert _numeric_error(1.0, 2.0) > 1e-7
