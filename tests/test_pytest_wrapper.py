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


def test_node_navigation_does_not_loop_from_terminal_node(tmp_path: Path) -> None:
    from pertura.core import GraphController, Store, evaluate_node_navigation
    from pertura.domain.perturbseq import build_perturbseq_analysis_graph

    store = Store(tmp_path / "run_nav_terminal")
    controller = GraphController(store, "nav_terminal")
    spec = build_perturbseq_analysis_graph().model_dump(mode="json")
    controller.append_event("run_started", {"config": {
        "run_id": "nav_terminal",
        "workspace": str(tmp_path),
        "goal": "finish report",
        "domain": "perturbseq",
        "budget": {"max_attempts": 8, "max_branches": 1, "max_repairs": 1},
        "analysis_spec": spec,
        "active_node_id": "report",
    }})
    controller.append_event("node_entered", {
        "node_id": "report",
        "branch_id": "main",
        "reason": "test",
    })
    report_path = tmp_path / "report.md"
    report_path.write_text("report", encoding="utf-8")
    controller.append_event("artifact_registered", {"artifact": {
        "artifact_id": "art_report",
        "kind": "report",
        "path": str(report_path),
        "summary": "Report artifact",
    }})

    nav = evaluate_node_navigation(store.read_snapshot())
    assert nav["status"] == "complete"
    assert nav["roadmap"]["next_node_ids"] == []
    assert "target_node_id" not in nav


def test_node_navigation_stays_when_next_gate_not_ready(tmp_path: Path) -> None:
    from pertura.core import GraphController, Store, evaluate_node_navigation
    from pertura.domain.perturbseq import build_perturbseq_analysis_graph

    store = Store(tmp_path / "run_nav_blocked_next")
    controller = GraphController(store, "nav_blocked_next")
    spec = build_perturbseq_analysis_graph().model_dump(mode="json")
    for node in spec["nodes"]:
        if node["node_id"] == "workspace_inspection":
            node["next_nodes"] = ["guide_assignment"]
    controller.append_event("run_started", {"config": {
        "run_id": "nav_blocked_next",
        "workspace": str(tmp_path),
        "goal": "inspect perturb-seq workspace",
        "domain": "perturbseq",
        "budget": {"max_attempts": 8, "max_branches": 1, "max_repairs": 1},
        "analysis_spec": spec,
        "active_node_id": "workspace_inspection",
    }})
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
    assert nav["status"] == "stay"
    assert nav["candidates"][0]["node_id"] == "guide_assignment"
    assert nav["candidates"][0]["can_enter"] is False


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


def test_starting_job_projects_running_without_snapshot() -> None:
    from pertura.core import compile_execution_state

    jobs = [{"job_id": "job_start", "job_type": "agent_run", "status": "queued"}]
    state = compile_execution_state(None, jobs=jobs)

    assert state["mode"] == "running"
    assert state["activity"]["active_job"]["job_id"] == "job_start"
    assert state["candidate_actions"][0]["kind"] == "pause"


def test_pending_repair_patch_projects_retry_action(tmp_path: Path) -> None:
    from pertura.core import GraphController, Store, compile_candidate_actions, compile_execution_state

    store = Store(tmp_path / "run_repair_action")
    controller = GraphController(store, "repair_action")
    controller.append_event("run_started", {"config": {
        "run_id": "repair_action",
        "workspace": str(tmp_path),
        "goal": "repair action",
        "domain": "test",
        "budget": {"max_attempts": 5, "max_branches": 1, "max_repairs": 1},
    }})
    controller.append_event("patch_proposed", {"patch": {
        "patch_id": "patch_retry",
        "patch_type": "attempt_retry",
        "proposed_by": "auto_repair",
        "rationale": "Fix a small argument mismatch.",
        "payload": {
            "parent_attempt_id": "att_failed",
            "fixed_code": "print('fixed')",
            "risk_level": "medium",
        },
    }})

    snap = store.read_snapshot()
    state = compile_execution_state(snap)
    actions = compile_candidate_actions(snap, execution_state=state, work_order={})
    retry = next(item for item in actions if item["kind"] == "retry_repair")

    assert retry["id"] == "retry_repair:patch_retry"
    assert retry["primary"] is True
    assert retry["payload"]["action_id"] == "retry_repair:patch_retry"


def test_perturbseq_design_ledger_compiles_design_and_schema(tmp_path: Path) -> None:
    from pertura.core import GraphController, Store
    from pertura.domain.perturbseq import build_perturbseq_analysis_graph
    from pertura.product.perturbseq import compile_design_ledger

    store = Store(tmp_path / "run_product_ledger")
    controller = GraphController(store, "product_ledger")
    controller.append_event("run_started", {"config": {
        "run_id": "product_ledger",
        "workspace": str(tmp_path),
        "goal": "analyze perturb-seq controls",
        "domain": "perturbseq",
        "analysis_spec": build_perturbseq_analysis_graph().model_dump(mode="json"),
        "design": {"control_labels": ["NTC"], "guide_column": "guide_id"},
        "design_meta": {"control_labels": {"source": "user", "confidence": 1.0}},
        "budget": {"max_attempts": 8, "max_branches": 1, "max_repairs": 1},
    }})
    controller.append_event("observation_registered", {"observation": {
        "observation_id": "obs_schema_columns",
        "type": "schema",
        "target": "dataset",
        "metric": "obs_columns",
        "value": "obs columns: guide_id, target_gene, batch, treatment, control",
        "summary": "AnnData schema includes guide_id, target_gene, batch, treatment, control",
    }})

    ledger = compile_design_ledger(store.read_snapshot())
    by_field = {item["field_id"]: item for item in ledger["fields"]}

    assert by_field["control_labels"]["status"] == "known"
    assert by_field["control_labels"]["display_value"] == "NTC"
    assert by_field["target_column"]["candidates"]
    assert ledger["dataset_profile"]["loaded"] is True


def test_perturbseq_catalog_hides_redundant_dataset_tools(tmp_path: Path) -> None:
    from pertura.core import GraphController, Store
    from pertura.domain.perturbseq import build_perturbseq_analysis_graph
    from pertura.product.perturbseq import compile_capability_catalog, compile_design_ledger

    store = Store(tmp_path / "run_product_catalog")
    controller = GraphController(store, "product_catalog")
    controller.append_event("run_started", {"config": {
        "run_id": "product_catalog",
        "workspace": str(tmp_path),
        "goal": "continue after dataset profile",
        "domain": "perturbseq",
        "analysis_spec": build_perturbseq_analysis_graph().model_dump(mode="json"),
        "active_node_id": "workspace_inspection",
        "budget": {"max_attempts": 8, "max_branches": 1, "max_repairs": 1},
    }})
    controller.append_event("observation_registered", {"observation": {
        "observation_id": "obs_schema_shape",
        "type": "schema",
        "target": "dataset",
        "metric": "shape",
        "value": "100 cells x 20 genes",
    }})

    snap = store.read_snapshot()
    ledger = compile_design_ledger(snap)
    catalog = compile_capability_catalog(snap, ledger, active_node_id="workspace_inspection")

    assert "inspect_workspace" in catalog["hidden_tool_ids"]
    assert "load_dataset" in catalog["hidden_tool_ids"]


def test_perturbseq_work_order_prefers_turn_card_and_transition(tmp_path: Path) -> None:
    from pertura.core import GraphController, Store, build_active_work_order
    from pertura.domain.perturbseq import build_perturbseq_analysis_graph

    store = Store(tmp_path / "run_product_work_order")
    controller = GraphController(store, "product_work_order")
    controller.append_event("run_started", {"config": {
        "run_id": "product_work_order",
        "workspace": str(tmp_path),
        "goal": "inspect and then advance",
        "domain": "perturbseq",
        "analysis_spec": build_perturbseq_analysis_graph().model_dump(mode="json"),
        "active_node_id": "workspace_inspection",
        "budget": {"max_attempts": 8, "max_branches": 1, "max_repairs": 1},
    }})
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
        "value": "100 cells x 20 genes",
    }})

    work_order = build_active_work_order(store.read_snapshot(), tool_names=["inspect_workspace", "load_dataset", "request_node_transition"])
    markdown = work_order["markdown"]

    assert markdown.startswith("# Perturb-seq Turn Card")
    assert work_order["perturbseq"]["navigation"]["status"] == "advance"
    assert "request_node_transition" in work_order["recommended_actions"][0]
    assert "Do not repeat dataset inspection" in markdown
    assert "dataset_load_plan" not in work_order


def test_perturbseq_view_projects_flow_and_product_events(tmp_path: Path) -> None:
    from pertura.core import GraphController, Store
    from pertura.domain.perturbseq import build_perturbseq_analysis_graph
    from pertura.product.perturbseq import compile_perturbseq_view

    store = Store(tmp_path / "run_product_view")
    controller = GraphController(store, "product_view")
    controller.append_event("run_started", {"config": {
        "run_id": "product_view",
        "workspace": str(tmp_path),
        "goal": "build a product timeline",
        "domain": "perturbseq",
        "analysis_spec": build_perturbseq_analysis_graph().model_dump(mode="json"),
        "active_node_id": "workspace_inspection",
        "budget": {"max_attempts": 8, "max_branches": 1, "max_repairs": 1},
    }})
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
        "value": "100 cells x 20 genes",
    }})

    view = compile_perturbseq_view(store.read_snapshot(), events=store.read_events())

    assert view["view_type"] == "perturbseq_workbench"
    assert view["flow"]
    assert view["design_ledger"]["dataset_profile"]["loaded"] is True
    assert any(item["product_type"] == "observation_recorded" for item in view["product_timeline"])


def test_workflow_builder_projects_catalog_and_draft_events(tmp_path: Path) -> None:
    from pertura.core import GraphController, Store
    from pertura.domain.perturbseq import build_perturbseq_analysis_graph
    from pertura.product.perturbseq import workflow_builder_view

    store = Store(tmp_path / "run_workflow_builder")
    controller = GraphController(store, "workflow_builder")
    spec = build_perturbseq_analysis_graph().model_dump(mode="json")
    draft = dict(spec)
    draft["metadata"] = {"ui": {"positions": {"workspace_inspection": {"x": 12, "y": 34}}}}
    controller.append_event("run_started", {"config": {
        "run_id": "workflow_builder",
        "workspace": str(tmp_path),
        "goal": "edit workflow",
        "domain": "perturbseq",
        "analysis_spec": spec,
        "budget": {"max_attempts": 8, "max_branches": 1, "max_repairs": 1},
    }})
    controller.append_event("analysis_spec_draft_saved", {
        "analysis_spec": draft,
        "reason": "test",
    })

    snap = store.read_snapshot()
    view = workflow_builder_view(snap=snap)

    assert view["node_catalog"]
    assert view["check_catalog"]
    assert view["draft_spec"]["metadata"]["ui"]["positions"]["workspace_inspection"]["x"] == 12
    assert view["draft_meta"]["status"] == "draft"

    controller.append_event("analysis_spec_draft_applied", {
        "analysis_spec": draft,
        "reason": "test_apply",
    })
    applied = store.read_snapshot()
    assert applied.analysis_spec_draft == {}
    assert applied.workflow_draft_meta["status"] == "applied"
    assert applied.analysis_spec["metadata"]["ui"]["positions"]["workspace_inspection"]["y"] == 34


def test_workflow_autopilot_auto_advances_single_ready_successor(tmp_path: Path) -> None:
    from pertura.core import GraphController, Store, evaluate_workflow_autopilot
    from pertura.domain.perturbseq import build_perturbseq_analysis_graph

    store = Store(tmp_path / "run_workflow_autopilot_single")
    controller = GraphController(store, "workflow_autopilot_single")
    spec = build_perturbseq_analysis_graph().model_dump(mode="json")
    for node in spec["nodes"]:
        if node["node_id"] == "workspace_inspection":
            node["next_nodes"] = ["experimental_design"]
    controller.append_event("run_started", {"config": {
        "run_id": "workflow_autopilot_single",
        "workspace": str(tmp_path),
        "goal": "advance automatically",
        "domain": "perturbseq",
        "analysis_spec": spec,
        "budget": {"max_attempts": 8, "max_branches": 1, "max_repairs": 1},
    }})
    controller.append_event("node_entered", {
        "node_id": "workspace_inspection",
        "branch_id": "main",
        "reason": "test",
    })
    controller.append_event("observation_registered", {"observation": {
        "observation_id": "obs_dataset_shape",
        "type": "schema",
        "target": "dataset",
        "metric": "shape",
        "value": "100x20",
    }})

    decision = evaluate_workflow_autopilot(store.read_snapshot())

    assert decision["action"] == "auto_advance"
    assert decision["target_node_id"] == "experimental_design"


def test_workflow_autopilot_asks_user_for_multiple_ready_successors(tmp_path: Path) -> None:
    from pertura.core import GraphController, Store, evaluate_workflow_autopilot
    from pertura.domain.perturbseq import build_perturbseq_analysis_graph

    store = Store(tmp_path / "run_workflow_autopilot_choice")
    controller = GraphController(store, "workflow_autopilot_choice")
    spec = build_perturbseq_analysis_graph().model_dump(mode="json")
    for node in spec["nodes"]:
        if node["node_id"] == "workspace_inspection":
            node["next_nodes"] = ["experimental_design", "scrna_qc"]
        if node["node_id"] in {"experimental_design", "scrna_qc"}:
            node["requires"] = []
    controller.append_event("run_started", {"config": {
        "run_id": "workflow_autopilot_choice",
        "workspace": str(tmp_path),
        "goal": "choose next stage",
        "domain": "perturbseq",
        "analysis_spec": spec,
        "budget": {"max_attempts": 8, "max_branches": 1, "max_repairs": 1},
    }})
    controller.append_event("node_entered", {
        "node_id": "workspace_inspection",
        "branch_id": "main",
        "reason": "test",
    })
    controller.append_event("observation_registered", {"observation": {
        "observation_id": "obs_dataset_shape",
        "type": "schema",
        "target": "dataset",
        "metric": "shape",
        "value": "100x20",
    }})

    decision = evaluate_workflow_autopilot(store.read_snapshot())

    assert decision["action"] == "choose_next"
    assert {item["node_id"] for item in decision["candidates"]} == {"experimental_design", "scrna_qc"}


def test_scoped_tools_hide_load_dataset_after_dataset_profile(tmp_path: Path) -> None:
    from pertura.core import GraphController, Store
    from pertura.domain.perturbseq import build_perturbseq_analysis_graph
    from pertura.tools.registry import tool_schemas

    store = Store(tmp_path / "run_tool_scope_dataset")
    controller = GraphController(store, "tool_scope_dataset")
    controller.append_event("run_started", {"config": {
        "run_id": "tool_scope_dataset",
        "workspace": str(tmp_path),
        "goal": "do not reload dataset",
        "domain": "perturbseq",
        "analysis_spec": build_perturbseq_analysis_graph().model_dump(mode="json"),
        "active_node_id": "workspace_inspection",
        "budget": {"max_attempts": 8, "max_branches": 1, "max_repairs": 1},
    }})
    controller.append_event("observation_registered", {"observation": {
        "observation_id": "obs_dataset_shape",
        "type": "schema",
        "target": "dataset",
        "metric": "shape",
        "value": "100x20",
    }})

    tool_names = {item["function"]["name"] for item in tool_schemas(snap=store.read_snapshot(), scoped=True)}

    assert "load_dataset" not in tool_names
