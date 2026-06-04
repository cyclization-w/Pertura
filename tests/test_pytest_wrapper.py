"""Pytest wrapper around the source-tree script harness."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest


def test_script_harness_analysis_spec_segment() -> None:
    root = Path(__file__).resolve().parent.parent
    cmd = [
        sys.executable,
        str(root / "tests" / "test_harness.py"),
        "--segment",
        "analysis_spec_gating",
    ]
    result = subprocess.run(cmd, cwd=root, text=True, capture_output=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr


def test_claim_tests_module_runs_json() -> None:
    root = Path(__file__).resolve().parent.parent
    cmd = [
        sys.executable,
        "-m",
        "pertura.claim_tests",
        "--claim",
        "analysis_graph",
        "--json",
    ]
    result = subprocess.run(cmd, cwd=root, text=True, capture_output=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr


def test_kernel_state_captures_variables(tmp_path: Path) -> None:
    pytest.importorskip("jupyter_client")
    pytest.importorskip("ipykernel")
    from pertura.kernel.session import KernelSession

    workspace = tmp_path / "workspace"
    artifacts = tmp_path / "artifacts"
    workspace.mkdir()
    artifacts.mkdir()
    session = KernelSession(str(workspace), str(artifacts))
    try:
        result = session.execute("att_kernel_state", "x = 123\nprint('ok')", soft_timeout=10, hard_timeout=30)
        variables = (result.get("kernel_state") or {}).get("variables", {})
        assert "x" in variables, result
    finally:
        session.shutdown()
