from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest


def test_synthetic_state_reference_fixture_generator(tmp_path: Path) -> None:
    pytest.importorskip("anndata")
    script = Path(__file__).resolve().parents[2] / "scripts" / "make_synthetic_state_reference_fixture.py"
    out_dir = tmp_path / "fixture"

    result = subprocess.run(
        [sys.executable, str(script), "--out", str(out_dir), "--n-cells", "30", "--n-background-genes", "6"],
        check=True,
        capture_output=True,
        text=True,
    )

    assert "synthetic_state_reference.h5ad" in result.stdout
    manifest = json.loads((out_dir / "fixture_manifest.json").read_text(encoding="utf-8"))
    assert manifest["fixture_name"] == "synthetic_state_reference"
    assert manifest["state_column"] == "synthetic_state"
    assert manifest["n_cells"] == 30
    assert (out_dir / "synthetic_state_reference.h5ad").exists()