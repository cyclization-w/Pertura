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


def test_node_navigation_recommends_next_stage(tmp_path: Path) -> None:
    from pertura.core import GraphController, Store, evaluate_node_navigation
    from pertura.domain.perturbseq import build_perturbseq_analysis_graph

    store = Store(tmp_path / "run_nav")
    controller = GraphController(store, "nav")
    controller.append_event("run_started", {"config": {
        "run_id": "nav",
        "workspace": str(tmp_path),
        "goal": "inspect perturb-seq workspace",
        "domain": "perturbseq",
        "budget": {"max_attempts": 8, "max_branches": 1, "max_repairs": 1},
        "capabilities": [],
    }})
    controller.append_event("analysis_spec_loaded", {
        "analysis_spec": build_perturbseq_analysis_graph().model_dump(mode="json"),
        "reason": "test",
    })
    controller.append_event("node_entered", {
        "node_id": "workspace_inspection",
        "branch_id": "main",
        "reason": "test",
    })
    controller.append_event("observation_registered", {"observation": {
        "observation_id": "obs_schema_shape",
        "type": "schema",
        "target": "dataset",
        "metric": "shape",
        "value": "10x5",
    }})

    nav = evaluate_node_navigation(store.read_snapshot())
    assert nav["status"] == "advance"
    assert nav["target_node_id"] == "experimental_design"
    assert nav["roadmap"]["current_index"] == 1


def test_empty_behaviors_do_not_pollute_graph(tmp_path: Path) -> None:
    from pertura.core import GraphController, Store

    store = Store(tmp_path / "run_behavior_noise")
    controller = GraphController(store, "noise")
    controller.append_event("run_started", {"config": {
        "run_id": "noise",
        "workspace": str(tmp_path),
        "goal": "behavior noise",
        "domain": "test",
        "budget": {"max_attempts": 5, "max_branches": 1, "max_repairs": 1},
        "capabilities": [],
    }})
    controller.append_event("observation_registered", {"observation": {
        "observation_id": "obs_shape",
        "type": "schema",
        "target": "dataset",
        "metric": "shape",
        "value": "10x5",
    }})

    snap = store.read_snapshot()
    graph = store.read_graph()
    assert not snap.behavior_runs
    assert not any(node.get("node_type") == "behavior_run" for node in graph.get("nodes", []))
