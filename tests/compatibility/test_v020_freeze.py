from __future__ import annotations

from pathlib import Path

from pertura_bench.compatibility import freeze_contracts
from pertura_runtime.claude.tools.product_tools import PRODUCT_TOOL_NAMES


def test_v020_compatibility_snapshots_have_no_drift() -> None:
    root = Path(__file__).resolve().parents[2]
    assert freeze_contracts(root, check=True) == []


def test_v020_domain_tool_surface_remains_five() -> None:
    assert len(PRODUCT_TOOL_NAMES) == 5
