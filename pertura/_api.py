"""FastAPI server for the Pertura workbench."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel


class RunRequest(BaseModel):
    workspace: str
    goal: str = ""
    steps: int = 5


class AgentRunRequest(BaseModel):
    workspace: str = ""
    goal: str = ""
    max_turns: int = 20
    max_repairs: int = 5
    no_progress_limit: int = 3


class AnswerRequest(BaseModel):
    answer: str


class AnalysisSpecRequest(BaseModel):
    analysis_spec: dict
    reason: str = "api_update"


class AnalysisSpecCompileRequest(BaseModel):
    analysis_spec: dict
    provider: str = "deterministic"
    apply: bool = False
    reason: str = "api_compile"
    domain_context: str = ""


class DesignUpdateRequest(BaseModel):
    design: dict
    reason: str = "api_update"
    source: str = "api_confirmed"
    confidence: str = "high"


class CapabilityToggleRequest(BaseModel):
    enabled: bool = True
    reason: str = "api_toggle"


class ConsoleTurnRequest(BaseModel):
    message: str = ""
    workspace: str = ""
    action_id: str = ""
    answers: dict = {}


class WorkflowDraftRequest(BaseModel):
    analysis_spec: dict
    reason: str = "workflow_builder"


class WorkflowApplyRequest(BaseModel):
    reason: str = "workflow_builder_apply"


def _field_from_answer_action(action_id: str) -> str:
    if not action_id.startswith("answer_question:"):
        return ""
    return action_id.split(":", 1)[1].strip()


def _console_design_patch(message: str, answers: dict, action_id: str) -> dict:
    """Compile Analysis Console question answers into a design update."""
    import json

    from pertura.product.perturbseq.ontology import FIELD_BY_ID, normalize_design_key
    from pertura.spec.design_answer import compile_design_answer

    field_id = normalize_design_key(_field_from_answer_action(action_id))
    if field_id not in FIELD_BY_ID:
        field_id = ""

    normalized_answers = {}
    for key, value in (answers or {}).items():
        if value in ("", None, []):
            continue
        norm_key = normalize_design_key(key)
        if norm_key == "answer" and field_id:
            norm_key = field_id
        if norm_key in FIELD_BY_ID:
            normalized_answers[norm_key] = value

    expected = [field_id] if field_id else sorted(normalized_answers)
    patch = {}
    if normalized_answers:
        patch = compile_design_answer(
            json.dumps(normalized_answers, ensure_ascii=False),
            expected_fields=expected,
        )
        for key, value in normalized_answers.items():
            if key not in patch or key == "control_labels":
                patch[key] = _coerce_console_design_value(key, value)
    elif message and field_id:
        patch = compile_design_answer(message, expected_fields=[field_id])
        patch.setdefault(field_id, _coerce_console_design_value(field_id, message))
    elif message:
        patch = compile_design_answer(message)
    return {key: value for key, value in patch.items() if key in FIELD_BY_ID and value not in ("", None, [])}


def _coerce_console_design_value(field_id: str, value):
    if isinstance(value, (list, tuple, set)):
        return [item for item in value if item not in ("", None)]
    if not isinstance(value, str):
        return value
    text = str(value or "").strip()
    if field_id == "control_labels":
        import re

        labels = [item.strip() for item in re.split(r"[,;/]|\band\b", text, flags=re.IGNORECASE) if item.strip()]
        return labels or text
    return text


def analysis_spec_audit_payload(workbench, *, run_id: str = "", strict: bool = False) -> dict:
    from pertura.spec.contracts import audit_analysis_graph
    spec = _analysis_spec_for_workbench(workbench, run_id=run_id)
    if not spec:
        raise ValueError("No analysis spec")
    return audit_analysis_graph(
        spec,
        capabilities=_capability_registry_for_workbench(workbench, run_id=run_id),
        strict=strict,
    )


def analysis_spec_contract_payload(workbench, *, node_id: str = "", run_id: str = "") -> dict:
    from pertura.spec.contracts import graph_contract, node_contract
    from pertura.spec.models import spec_from_dict
    spec = spec_from_dict(_analysis_spec_for_workbench(workbench, run_id=run_id))
    if spec is None:
        raise ValueError("No analysis spec")
    registry = _capability_registry_for_workbench(workbench, run_id=run_id)
    if node_id:
        if spec.node(node_id) is None:
            raise ValueError(f"Node {node_id} not found")
        return node_contract(spec, node_id, capabilities=registry)
    return graph_contract(spec, capabilities=registry)


def runtime_node_contract_payload(workbench, *, node_id: str = "", run_id: str = "") -> dict:
    from pertura.tools.registry import execute_tool
    snap = _snapshot_for_workbench(workbench, run_id=run_id)
    if not snap:
        raise ValueError("No run snapshot")
    payload = execute_tool("get_node_contract", {"node_id": node_id} if node_id else {}, snap=snap)
    if "error" in payload:
        raise ValueError(payload["error"])
    return payload


def context_review_payload(
    workbench,
    *,
    run_id: str = "",
    purpose: str = "audit",
    max_items: int = 8,
    token_budget: int = 6000,
    runtime_state: dict | None = None,
) -> dict:
    from pertura.tools.registry import execute_tool
    snap = _snapshot_for_workbench(workbench, run_id=run_id)
    if not snap:
        raise ValueError("No run snapshot")
    payload = execute_tool(
        "get_context_review",
        {
            "purpose": purpose,
            "max_items": max_items,
            "token_budget": token_budget,
            "runtime_state": runtime_state or {},
        },
        snap=snap,
    )
    if "error" in payload:
        raise ValueError(payload["error"])
    return payload


def run_audit_payload(workbench, *, run_id: str = "") -> dict:
    from pertura.core.audit import audit_run
    snap = _snapshot_for_workbench(workbench, run_id=run_id)
    store = _store_for_workbench(workbench, run_id=run_id)
    if not snap:
        raise ValueError("No run snapshot")
    graph = store.read_graph() if store else None
    run_dir = getattr(store, "run_dir", "") if store else ""
    return audit_run(snap, graph or {}, run_dir=run_dir)


def rethinking_payload(
    workbench,
    *,
    run_id: str = "",
    node_id: str = "",
    issue: str = "",
    depth: int = 5,
) -> dict:
    from pertura.core.rethinking import plan_rethinking
    snap = _snapshot_for_workbench(workbench, run_id=run_id)
    store = _store_for_workbench(workbench, run_id=run_id)
    if not snap:
        raise ValueError("No run snapshot")
    graph = store.read_graph() if store else None
    return plan_rethinking(snap, node_id, issue=issue, depth=depth, graph=graph or {})


def harness_manifest_payload() -> dict:
    from pertura.core import build_harness_manifest
    return build_harness_manifest()


def domain_browser_payload(workbench, *, include_core_tools: bool = True) -> dict:
    return workbench.domain.describe(include_core_tools=include_core_tools)


def workflow_builder_payload(workbench, *, run_id: str = "") -> dict:
    from pertura.product.perturbseq import workflow_builder_view

    snap = _snapshot_for_workbench(workbench, run_id=run_id)
    return workflow_builder_view(
        snap=snap,
        domain=workbench.domain,
        run_id=run_id or getattr(workbench, "_run_id", ""),
        capabilities=_capability_registry_for_workbench(workbench, run_id=run_id),
    )


def workbench_view_payload(
    workbench,
    *,
    run_id: str = "",
    max_items: int = 8,
    token_budget: int = 6000,
    jobs: list[dict] | None = None,
    include_debug: bool = True,
) -> dict:
    """Return the stable compact UI contract for the workbench shell.

    This endpoint is intentionally a projection over existing runtime views. It
    gives a GUI the current decision surface without exposing full event logs,
    notebooks, or the complete graph by default.
    """
    from pertura.models import _model_dump

    snap = _snapshot_for_workbench(workbench, run_id=run_id)
    store = _store_for_workbench(workbench, run_id=run_id)
    status = _status_for_snapshot(snap) if snap else dict(getattr(workbench, "status", {}) or {})
    graph = store.read_graph() if store else (workbench.graph or {"nodes": [], "edges": []})

    active_node_id = (snap.active_node_id if snap else "") or ""
    node_contract = _safe_payload(
        lambda: runtime_node_contract_payload(workbench, node_id=active_node_id, run_id=run_id),
        default={},
    )
    context_review = {}
    run_audit = {}
    rethinking = {}
    if include_debug:
        context_review = _safe_payload(
            lambda: context_review_payload(
                workbench,
                run_id=run_id,
                purpose="ui",
                max_items=max_items,
                token_budget=token_budget,
            ),
            default={},
        )
        run_audit = _safe_payload(
            lambda: run_audit_payload(workbench, run_id=run_id),
            default={},
        )
        rethinking = _safe_payload(
            lambda: rethinking_payload(
                workbench,
                run_id=run_id,
                node_id=active_node_id,
                issue="workbench_view",
                depth=4,
            ),
            default={},
        )

    open_interrupts = [
        _model_dump(item) for item in ((snap.interrupts if snap else []) or [])
        if item.status == "open"
    ][:max_items]
    open_triggers = [
        _model_dump(item) for item in ((snap.triggers if snap else []) or [])
        if item.status == "open"
    ][:max_items]
    open_findings = [
        _model_dump(item) for item in ((snap.findings if snap else []) or [])
        if item.severity in {"warning", "blocking"}
    ][-max_items:]
    recent_attempts = [_attempt_card(item, snap=snap) for item in ((snap.attempts if snap else []) or [])[-max_items:]]
    artifact_summary = [_artifact_card(item) for item in ((snap.artifacts if snap else []) or [])[-max_items:]]
    recent_events = store.read_events()[-max_items * 6:] if store else []
    runtime_events = _runtime_event_cards(recent_events, max_items=max_items)
    capability_view = capabilities_view_payload(workbench, run_id=run_id, node_id=active_node_id, max_items=100)
    active_work_order = {}
    if snap:
        from pertura.core import build_active_work_order
        visible_tools = [
            item.get("tool_id", "")
            for item in ((capability_view.get("llm_tool_surface") or {}).get("visible_tools") or [])
            if item.get("tool_id")
        ]
        active_work_order = build_active_work_order(
            snap,
            trace_driven_rethinking=(context_review or {}).get("trace_driven_rethinking", {}),
            tool_names=visible_tools,
        )
    perturbseq_view = {}
    if snap and getattr(snap, "domain", "") == "perturbseq":
        from pertura.product.perturbseq import compile_perturbseq_view

        perturbseq_view = compile_perturbseq_view(
            snap,
            events=recent_events,
            navigation=(active_work_order.get("navigation") or {}),
        )
        if active_work_order:
            active_work_order["perturbseq"] = perturbseq_view
            if perturbseq_view.get("turn_card_markdown"):
                active_work_order["markdown"] = perturbseq_view["turn_card_markdown"]

    domain_payload = domain_browser_payload(workbench, include_core_tools=False)
    runtime_jobs = [_model_dump(item) for item in ((snap.jobs if snap else []) or [])]
    queue_jobs = jobs or []
    execution_state = execution_state_payload(
        workbench,
        run_id=run_id,
        jobs=queue_jobs,
        selected_node_id=active_node_id,
    )
    from pertura.core import compile_candidate_actions
    candidate_actions = compile_candidate_actions(
        snap,
        execution_state=execution_state,
        work_order=active_work_order,
        jobs=(runtime_jobs + queue_jobs)[:max_items],
    )
    execution_state["candidate_actions"] = candidate_actions
    return {
        "view_type": "workbench_view",
        "schema_version": "v1",
        "run_id": status.get("run_id", run_id),
        "execution_state": execution_state,
        "status": status,
        "active": {
            "node_id": active_node_id,
            "branch_id": snap.active_branch if snap else "",
            "attempt_id": snap.active_attempt if snap else "",
        },
        "budget": _model_dump(snap.budget) if snap else {},
        "analysis": {
            "graph_summary": {
                "nodes": len((graph or {}).get("nodes", [])),
                "edges": len((graph or {}).get("edges", [])),
            },
            "active_node_contract": node_contract,
            "domain": domain_payload.get("domain", {}),
            "nodes": [
                {
                    "node_id": item.get("node_id", ""),
                    "title": item.get("title", ""),
                    "purpose": item.get("purpose", ""),
                    "allowed_capabilities": item.get("allowed_capabilities", []),
                    "recommended_actions": item.get("recommended_actions", []),
                    "expected_outputs": item.get("expected_outputs", []),
                    "next_nodes": item.get("next_nodes", []),
                    "strict_edges": item.get("strict_edges", False),
                    "hard_conditions": sum(
                        1
                        for group in (item.get("conditions", {}) or {}).values()
                        for condition in group
                        if condition.get("hard")
                    ),
                    "rubric_only_conditions": sum(
                        1
                        for group in (item.get("conditions", {}) or {}).values()
                        for condition in group
                        if condition.get("evaluator_id") == "rubric_only"
                    ),
                }
                for item in domain_payload.get("nodes", [])
            ],
            "capabilities_by_node": domain_payload.get("capabilities_by_node", {}),
            "capabilities_view": capability_view,
            "active_work_order": active_work_order,
            "candidate_actions": candidate_actions,
        },
        "perturbseq": perturbseq_view,
        "agent_context": context_review,
        "review": {
            "open_interrupts": open_interrupts,
            "open_triggers": open_triggers,
            "open_findings": open_findings,
            "run_audit_summary": _audit_summary_card(run_audit),
            "rethinking": _rethinking_card(rethinking),
        },
        "activity": {
            "recent_attempts": recent_attempts,
            "jobs": (runtime_jobs + queue_jobs)[:max_items],
            "runtime_events": runtime_events,
            "product_events": (perturbseq_view.get("product_timeline") or [])[:max_items],
        },
        "artifacts": {
            "recent": artifact_summary,
            "total": len(snap.artifacts) if snap else 0,
        },
        "report": _report_summary_for_snapshot(snap),
        "links": {
            "ui_info": "/api/ui-info",
            "workbench_view": "/api/workbench-view",
            "console_turn": "/api/console/turn",
            "workflow_builder": "/api/workflow-builder",
            "graph": "/api/graph",
            "domain": "/api/domain",
            "node_contract": "/api/node-contract",
            "context_review": "/api/context-review",
            "run_audit": "/api/run-audit",
            "rethink": "/api/rethink",
            "artifacts": "/api/artifacts",
            "jobs": "/api/jobs",
            "events_stream": "/api/events/stream",
            "capabilities_view": "/api/capabilities/view",
            "derivation_view": "/api/derivation-view",
            "interrupts": "/api/interrupts",
        },
    }


def execution_state_payload(
    workbench,
    *,
    run_id: str = "",
    jobs: list[dict] | None = None,
    selected_node_id: str = "",
) -> dict:
    from pertura.core.execution_state import compile_execution_state
    from pertura.models import _model_dump

    snap = _snapshot_for_workbench(workbench, run_id=run_id)
    store = _store_for_workbench(workbench, run_id=run_id)
    graph = store.read_graph() if store else (workbench.graph or {"nodes": [], "edges": []})
    runtime_jobs = jobs or []
    if snap:
        runtime_jobs = [_model_dump(item) for item in ((snap.jobs if snap else []) or [])] + runtime_jobs
    return compile_execution_state(
        snap,
        graph=graph,
        jobs=runtime_jobs,
        selected_node_id=selected_node_id,
    )


def capabilities_view_payload(workbench, *, run_id: str = "", node_id: str = "", max_items: int = 100) -> dict:
    from pertura.capabilities import CapabilityRegistry
    from pertura.spec.gating import GateEvaluator
    from pertura.tools.permissions import tool_permission
    from pertura.tools.registry import tool_schemas

    snap = _snapshot_for_workbench(workbench, run_id=run_id)
    registry = _capability_registry_for_workbench(workbench, run_id=run_id)
    active_node_id = node_id or (snap.active_node_id if snap else "")
    disabled = set((snap.disabled_capabilities if snap else []) or [])
    allowed = set()
    if snap and snap.analysis_spec and active_node_id:
        allowed = set(GateEvaluator(snap.analysis_spec).allowed_capabilities(active_node_id))
    readiness = _capability_readiness_by_id(workbench, run_id=run_id, node_id=active_node_id)
    visible_tool_names = {
        item["function"]["name"]
        for item in tool_schemas(snap=snap, scoped=True)
    }
    if snap and getattr(snap, "domain", "") == "perturbseq":
        from pertura.product.perturbseq import compile_design_ledger, compile_capability_catalog

        ledger = compile_design_ledger(snap)
        catalog = compile_capability_catalog(snap, ledger, active_node_id=active_node_id)
        visible_tool_names -= set(catalog.get("hidden_tool_ids") or [])
    tool_surface = _llm_tool_surface(
        snap=snap,
        visible_tool_names=visible_tool_names,
        tool_permission=tool_permission,
    )

    capabilities = []
    for cap in registry.summarize(limit=max_items):
        cap_id = cap.get("id") or cap.get("capability_id") or ""
        tool_names = cap.get("tools", []) or []
        ready = readiness.get(cap_id, {})
        enabled = cap_id not in disabled
        allowed_here = bool(cap_id and (not allowed or cap_id in allowed))
        missing_inputs = ready.get("missing_inputs", []) or []
        why = []
        if not enabled:
            why.append("disabled_for_run")
        if allowed and not allowed_here:
            why.append("not_allowed_in_active_node")
        if missing_inputs:
            why.append("missing_inputs")
        permission = _highest_permission(tool_names, tool_permission)
        tool_visibility = [
            _tool_visibility_card(tool_name, visible_tool_names=visible_tool_names, snap=snap, tool_permission=tool_permission)
            for tool_name in tool_names
        ]
        tool_visible = any(item["visible_to_llm"] for item in tool_visibility) if tool_visibility else True
        if tool_names and not tool_visible:
            why.append("implementation_tools_hidden_this_turn")
        capabilities.append({
            "capability_id": cap_id,
            "title": cap.get("title", cap_id.replace("_", " ")),
            "description": cap.get("description", ""),
            "stage": cap.get("stage", ""),
            "kind": cap.get("kind", ""),
            "tool_names": tool_names,
            "allowed_in_active_node": allowed_here,
            "required_inputs": cap.get("required_inputs", []) or [],
            "missing_inputs": missing_inputs,
            "expected_artifacts": cap.get("expected_artifacts", []) or [],
            "expected_observations": cap.get("expected_observations", []) or [],
            "permission_tier": permission,
            "backend_hint": cap.get("backend", ""),
            "enabled": enabled,
            "ready": bool(ready.get("ready")) and enabled and allowed_here and tool_visible,
            "llm_actionable": bool(ready.get("ready")) and enabled and allowed_here and tool_visible,
            "tool_visibility": tool_visibility,
            "why_unavailable": why,
        })
    return {
        "view_type": "capabilities_view",
        "schema_version": "v1",
        "run_id": snap.run_id if snap else run_id,
        "active_node_id": active_node_id,
        "disabled_capabilities": sorted(disabled),
        "llm_tool_surface": tool_surface,
        "capabilities": capabilities,
    }


def derivation_view_payload(workbench, *, run_id: str = "", focus_node: str = "", depth: int = 4) -> dict:
    graph = (_store_for_workbench(workbench, run_id=run_id).read_graph()
             if _store_for_workbench(workbench, run_id=run_id)
             else (workbench.graph or {"nodes": [], "edges": []}))
    nodes = graph.get("nodes", []) or []
    edges = graph.get("edges", []) or []
    if not focus_node:
        focus_node = _default_derivation_focus(nodes, getattr(workbench, "status", {}) or {})
    selected_ids = {focus_node} if focus_node else set()
    if focus_node:
        from pertura.core import build_impact_view, build_trace_view
        trace = build_trace_view(graph, focus_node, depth=depth)
        impact = build_impact_view(graph, focus_node, depth=depth)
        selected_ids.update(node.get("node_id", "") for node in trace.get("nodes", []))
        selected_ids.update(node.get("node_id", "") for node in impact.get("nodes", []))
    if not selected_ids:
        selected_ids = {node.get("node_id", "") for node in nodes[:50]}
    lane_order = ["Inputs", "Attempts", "Artifacts", "Observations", "Conclusions"]
    included_nodes = [node for node in nodes if node.get("node_id") in selected_ids]
    if len(included_nodes) < 8:
        included_nodes = nodes[:50]
        selected_ids = {node.get("node_id", "") for node in included_nodes}
    included_edges = [
        edge for edge in edges
        if edge.get("source_id") in selected_ids and edge.get("target_id") in selected_ids
    ]
    lanes = {lane: [] for lane in lane_order}
    all_counts = {lane: 0 for lane in lane_order}
    for node in nodes:
        lane = _derivation_lane(node.get("node_type", ""))
        all_counts[lane] = all_counts.get(lane, 0) + 1
    for node in included_nodes:
        lanes[_derivation_lane(node.get("node_type", ""))].append(node)
    issue_edges = [
        edge for edge in included_edges
        if _is_issue_edge(edge, {node.get("node_id"): node for node in included_nodes})
    ]
    focus_path = _focus_path_ids(included_nodes, included_edges, focus_node)
    return {
        "view_type": "derivation_view",
        "schema_version": "v1",
        "run_id": graph.get("run_id", run_id),
        "focus_node": focus_node,
        "lane_order": lane_order,
        "lanes": [{"lane": lane, "nodes": lanes.get(lane, [])} for lane in lane_order],
        "nodes": included_nodes,
        "edges": included_edges,
        "focus_path": focus_path,
        "issue_edges": issue_edges,
        "folded_counts": {
            lane: max(0, all_counts.get(lane, 0) - len(lanes.get(lane, [])))
            for lane in lane_order
        },
        "summary": {
            "all_nodes": len(nodes),
            "all_edges": len(edges),
            "visible_nodes": len(included_nodes),
            "visible_edges": len(included_edges),
            "issues": len(issue_edges),
        },
    }


def _open_store_for_run(run_id: str):
    from pertura.core import Store
    d = Path("runs") / run_id
    return Store(d) if (d / "events.db").exists() else None


def _store_for_workbench(workbench, *, run_id: str = ""):
    rid = run_id or getattr(workbench, "_run_id", "")
    if rid and rid == getattr(workbench, "_run_id", "") and getattr(workbench, "_store", None):
        return workbench._store
    return _open_store_for_run(rid) if rid else None


def _snapshot_for_workbench(workbench, *, run_id: str = ""):
    store = _store_for_workbench(workbench, run_id=run_id)
    return store.read_snapshot() if store else None


def _analysis_spec_for_workbench(workbench, *, run_id: str = ""):
    snap = _snapshot_for_workbench(workbench, run_id=run_id)
    if snap and snap.analysis_spec:
        return snap.analysis_spec
    return workbench.domain.analysis_graph or {}


def _capability_registry_for_workbench(workbench, *, run_id: str = ""):
    from pertura.capabilities import CapabilityRegistry
    snap = _snapshot_for_workbench(workbench, run_id=run_id)
    if snap and snap.capabilities:
        return CapabilityRegistry(snap.capabilities)
    return CapabilityRegistry(getattr(workbench.domain, "capabilities", []) or [])


def _safe_payload(fn, *, default: dict) -> dict:
    try:
        payload = fn()
        return payload if isinstance(payload, dict) else default
    except Exception as exc:
        return {"error": str(exc)}


def _status_for_snapshot(snap) -> dict:
    if not snap:
        return {"state": "no_snapshot"}
    return {
        "run_id": snap.run_id,
        "phase": snap.phase,
        "workspace": snap.workspace,
        "goal": snap.goal,
        "attempts": len(snap.attempts),
        "observations": len(snap.observations),
        "artifacts": len(snap.artifacts),
        "conclusions": len(snap.conclusions),
        "triggers_open": len([item for item in snap.triggers if item.status == "open"]),
        "interrupts_open": len([item for item in snap.interrupts if item.status == "open"]),
        "branches": len(snap.branches),
    }


def _attempt_card(attempt, *, snap) -> dict:
    outcomes = [item for item in (snap.outcomes if snap else []) if item.attempt_id == attempt.attempt_id]
    observations = [item for item in (snap.observations if snap else []) if item.attempt_id == attempt.attempt_id]
    artifacts = [item for item in (snap.artifacts if snap else []) if item.attempt_id == attempt.attempt_id]
    last_outcome = outcomes[-1] if outcomes else None
    metrics = dict(getattr(last_outcome, "metrics", {}) or {}) if last_outcome else {}
    kernel_state = metrics.get("kernel_state", {}) if isinstance(metrics.get("kernel_state", {}), dict) else {}
    variables = kernel_state.get("variables", {}) if isinstance(kernel_state.get("variables", {}), dict) else {}
    return {
        "attempt_id": attempt.attempt_id,
        "title": attempt.title,
        "objective": attempt.objective,
        "stage": attempt.stage,
        "status": attempt.status,
        "analysis_node_id": attempt.analysis_node_id,
        "branch_id": attempt.branch_id,
        "capability_ids": list(attempt.capability_ids),
        "rationale": attempt.rationale,
        "repair_count": attempt.repair_count,
        "code_preview": _attempt_code_preview(attempt),
        "outcome_status": last_outcome.status if last_outcome else "",
        "outcome_summary": last_outcome.summary if last_outcome else "",
        "execution": {
            "returncode": metrics.get("returncode"),
            "timed_out": metrics.get("timed_out", False),
            "soft_timeout_hit": metrics.get("soft_timeout_hit", False),
            "execution_time": metrics.get("execution_time"),
            "stdout_chars": metrics.get("stdout_chars", 0),
            "stderr_tail": metrics.get("stderr", ""),
            "observations_registered": metrics.get("observations_registered", 0),
            "kernel_refs": list(variables)[:12],
        },
        "observations": len(observations),
        "artifacts": len(artifacts),
        "created_at": str(attempt.created_at),
    }


def _attempt_code_preview(attempt, *, max_chars: int = 1800) -> str:
    cells = getattr(attempt, "notebook_cells", []) or []
    for cell in cells:
        source = cell.get("source", "") if isinstance(cell, dict) else ""
        if source:
            text = str(source)
            return text[:max_chars] + ("..." if len(text) > max_chars else "")
    return ""


def _artifact_card(artifact) -> dict:
    return {
        "artifact_id": artifact.artifact_id,
        "attempt_id": artifact.attempt_id,
        "kind": artifact.kind,
        "summary": artifact.summary,
        "path": artifact.path,
        "metadata": artifact.metadata,
        "preview_url": f"/api/artifacts/{artifact.artifact_id}/preview",
        "file_url": f"/api/artifacts/{artifact.artifact_id}/file",
    }


def _audit_summary_card(audit: dict) -> dict:
    if not audit or "error" in audit:
        return audit or {}
    errors = audit.get("errors", []) or []
    warnings = audit.get("warnings", []) or []
    next_actions = audit.get("next_actions", []) or []
    return {
        "audit_type": audit.get("audit_type", "run_audit"),
        "ok": not errors,
        "errors": len(errors),
        "warnings": len(warnings),
        "next_actions": next_actions[:5],
    }


def _rethinking_card(payload: dict) -> dict:
    if not payload or "error" in payload:
        return payload or {}
    return {
        "view_type": payload.get("view_type", ""),
        "status": payload.get("status", ""),
        "summary": payload.get("summary", ""),
        "suspected_roots": (payload.get("suspected_roots", []) or [])[:5],
        "recommended_actions": (payload.get("recommended_actions", []) or [])[:5],
    }


def _report_summary_for_snapshot(snap) -> dict:
    if not snap:
        return {"available": False}
    return {
        "available": bool(snap.conclusions or snap.observations),
        "conclusions": [
            {
                "conclusion_id": item.conclusion_id,
                "text": item.text,
                "grade": item.grade,
                "support_count": len(item.support_ids),
                "limitation_count": len(item.limitation_ids),
            }
            for item in snap.conclusions[-8:]
        ],
        "observation_count": len(snap.observations),
        "artifact_count": len(snap.artifacts),
    }


def _capability_readiness_by_id(workbench, *, run_id: str = "", node_id: str = "") -> dict:
    payload = _safe_payload(
        lambda: runtime_node_contract_payload(workbench, run_id=run_id, node_id=node_id),
        default={},
    )
    runtime = payload.get("runtime", {}) if isinstance(payload, dict) else {}
    cards = runtime.get("capability_readiness", []) or []
    return {card.get("id", ""): card for card in cards if card.get("id")}


def _llm_tool_surface(*, snap, visible_tool_names: set[str], tool_permission) -> dict:
    from pertura.tools.registry import TOOLS

    tools = [
        _tool_visibility_card(
            tool_name,
            visible_tool_names=visible_tool_names,
            snap=snap,
            tool_permission=tool_permission,
        )
        for tool_name in sorted(TOOLS)
    ]
    visible = [item for item in tools if item["visible_to_llm"]]
    hidden = [item for item in tools if not item["visible_to_llm"]]
    return {
        "surface_type": "scoped_llm_tools",
        "visible_count": len(visible),
        "hidden_count": len(hidden),
        "visible_tools": visible,
        "hidden_tools": hidden,
        "summary": _tool_surface_summary(visible, hidden),
    }


def _tool_visibility_card(tool_name: str, *, visible_tool_names: set[str], snap, tool_permission) -> dict:
    from pertura.tools.registry import TOOLS

    spec = TOOLS.get(tool_name, {})
    visible = tool_name in visible_tool_names
    permission = tool_permission(tool_name).value
    return {
        "tool_id": tool_name,
        "permission_tier": permission,
        "description": spec.get("description", ""),
        "visible_to_llm": visible,
        "why_hidden": [] if visible else _tool_hidden_reasons(tool_name, permission=permission, snap=snap),
    }


def _tool_hidden_reasons(tool_name: str, *, permission: str, snap) -> list[str]:
    reasons = []
    open_interrupts = [
        item for item in (getattr(snap, "interrupts", []) or [])
        if getattr(item, "status", "") == "open"
    ] if snap is not None else []
    if open_interrupts and permission not in {"local_read"} and tool_name not in {"ask_user", "update_design", "finish"}:
        reasons.append("open_human_interrupt")
    if permission == "external_read":
        reasons.append("external_read_requires_policy_or_approval")
    if permission == "execute":
        if snap is not None and getattr(snap, "analysis_spec", {}) and not getattr(snap, "active_node_id", ""):
            reasons.append("requires_active_analysis_node")
        try:
            budget = getattr(snap, "budget", None)
            max_attempts = int(getattr(budget, "max_attempts", 0) or 0)
            attempts = getattr(snap, "attempts", []) or []
            if max_attempts and len(attempts) >= max_attempts:
                reasons.append("attempt_budget_exhausted")
        except Exception:
            pass
    if not reasons:
        reasons.append("hidden_by_current_tool_scope")
    return reasons


def _tool_surface_summary(visible: list[dict], hidden: list[dict]) -> dict:
    def count_by(items: list[dict], key: str) -> dict:
        counts: dict[str, int] = {}
        for item in items:
            value = str(item.get(key) or "")
            counts[value] = counts.get(value, 0) + 1
        return counts

    hidden_reasons: dict[str, int] = {}
    for item in hidden:
        for reason in item.get("why_hidden", []) or []:
            hidden_reasons[reason] = hidden_reasons.get(reason, 0) + 1
    return {
        "visible_by_permission": count_by(visible, "permission_tier"),
        "hidden_by_permission": count_by(hidden, "permission_tier"),
        "hidden_reasons": hidden_reasons,
    }


def _highest_permission(tool_names: list[str], tool_permission) -> str:
    order = ["local_read", "external_read", "execute", "state_change", "privileged"]
    highest = "local_read"
    for tool_name in tool_names or []:
        value = tool_permission(tool_name).value
        if order.index(value) > order.index(highest):
            highest = value
    return highest


def _runtime_event_cards(events: list, *, max_items: int) -> list[dict]:
    interesting = {
        "node_transition_requested": "node_transition",
        "node_transition_blocked": "gate_blocked",
        "gate_evaluated": "gate_evaluated",
        "attempt_planned": "attempt_started",
        "execution_output": "execution_output",
        "outcome_recorded": "execution_result",
        "artifact_registered": "artifact_registered",
        "observation_registered": "observation_registered",
        "finding_recorded": "critic_finding",
        "interrupt_opened": "human_interrupt",
        "job_submitted": "job_submitted",
        "job_completed": "job_completed",
    }
    cards = []
    for event in reversed(events or []):
        kind = interesting.get(event.event_type)
        if not kind:
            continue
        payload = event.payload or {}
        cards.append({
            "event_id": event.event_id,
            "event_type": event.event_type,
            "card_type": kind,
            "timestamp": str(event.timestamp),
            "title": _event_card_title(event.event_type, payload),
            "summary": _event_card_summary(event.event_type, payload),
        })
        if len(cards) >= max_items:
            break
    return list(reversed(cards))


def _product_event_cards(events: list, *, max_items: int) -> list[dict]:
    try:
        from pertura.product.perturbseq import compile_product_timeline

        return compile_product_timeline(events or [], max_items=max_items)
    except Exception:
        return []


def _sse_event(event_type: str, payload: dict) -> str:
    import json
    return f"event: {event_type}\ndata: {json.dumps(payload, default=str)}\n\n"


def _looks_like_report_request(message: str) -> bool:
    text = (message or "").strip().lower()
    if not text:
        return False
    return any(token in text for token in {
        "report",
        "generate report",
        "final report",
        "summary report",
        "总结",
        "报告",
        "生成报告",
    })


def _event_card_title(event_type: str, payload: dict) -> str:
    if event_type == "attempt_planned":
        return (payload.get("attempt") or {}).get("title", "Attempt planned")
    if event_type == "outcome_recorded":
        outcome = payload.get("outcome") or {}
        status = outcome.get("status") or "outcome"
        return f"Execution {status}"
    if event_type == "execution_output":
        stream = str(payload.get("stream") or "stdout").upper()
        return f"{stream} output"
    if event_type == "artifact_registered":
        return (payload.get("artifact") or {}).get("kind", "Artifact")
    if event_type == "observation_registered":
        obs = payload.get("observation") or {}
        return f"{obs.get('type', 'observation')}:{obs.get('target', '')}"
    if event_type == "finding_recorded":
        return (payload.get("finding") or {}).get("finding_type", "Finding")
    if event_type == "interrupt_opened":
        return "Human interrupt"
    if event_type in {"job_submitted", "job_completed"}:
        return payload.get("job_id") or (payload.get("job") or {}).get("job_id", "Job")
    return event_type.replace("_", " ")


def _event_card_summary(event_type: str, payload: dict) -> str:
    for key in ("reason", "summary", "question"):
        if payload.get(key):
            return str(payload.get(key))
    if event_type == "execution_output":
        return str(payload.get("text") or "")[-500:]
    for entity_key in ("finding", "interrupt", "artifact", "observation", "attempt", "outcome", "job", "patch"):
        entity = payload.get(entity_key)
        if isinstance(entity, dict):
            return str(entity.get("summary") or entity.get("question") or entity.get("rationale") or entity.get("status") or "")
    return ""


def _derivation_lane(node_type: str) -> str:
    text = str(node_type or "").lower()
    if text in {"workspace", "dataset", "metadata", "description", "parameter_set", "analysis_node", "branch"}:
        return "Inputs"
    if text in {"attempt", "tool_call", "code_cell", "intervention", "diagnosis", "backward_trace", "job"}:
        return "Attempts"
    if text in {"artifact", "outcome"}:
        return "Artifacts"
    if text in {"conclusion", "report"}:
        return "Conclusions"
    return "Observations"


def _default_derivation_focus(nodes: list[dict], status: dict) -> str:
    for wanted in ("conclusion", "observation", "finding", "attempt"):
        for node in reversed(nodes):
            if node.get("node_type") == wanted:
                return node.get("node_id", "")
    return status.get("active_node_id", "")


def _is_issue_edge(edge: dict, nodes_by_id: dict[str, dict]) -> bool:
    if edge.get("edge_type") in {"limits", "contradicts", "triggers", "informs"}:
        return True
    src_type = (nodes_by_id.get(edge.get("source_id", "")) or {}).get("node_type")
    tgt_type = (nodes_by_id.get(edge.get("target_id", "")) or {}).get("node_type")
    return src_type in {"finding", "trigger"} or tgt_type in {"finding", "trigger"}


def _focus_path_ids(nodes: list[dict], edges: list[dict], focus_node: str) -> list[str]:
    if not focus_node:
        return []
    ids = {focus_node}
    for edge in edges:
        if edge.get("source_id") in ids or edge.get("target_id") in ids:
            ids.add(edge.get("source_id", ""))
            ids.add(edge.get("target_id", ""))
    lane_rank = {"Inputs": 0, "Attempts": 1, "Artifacts": 2, "Observations": 3, "Conclusions": 4}
    ordered = sorted(
        [node for node in nodes if node.get("node_id") in ids],
        key=lambda node: (lane_rank.get(_derivation_lane(node.get("node_type", "")), 99), node.get("node_id", "")),
    )
    return [node.get("node_id", "") for node in ordered]


def _react_dist_dir() -> Path:
    package_dist = Path(__file__).parent / "frontend_dist"
    if (package_dist / "index.html").exists():
        return package_dist
    repo_dist = Path(__file__).resolve().parent.parent / "frontend" / "dist"
    return repo_dist


def _ui_info_payload(workbench, *, requested_ui: str, effective_ui: str, react_dist: Path) -> dict:
    react_available = (react_dist / "index.html").exists()
    return {
        "view_type": "ui_info",
        "product": "perturbseq_workbench",
        "domain": getattr(getattr(workbench, "domain", None), "name", ""),
        "requested_ui": requested_ui,
        "effective_ui": effective_ui,
        "canonical_ui": "builtin_html",
        "react": {
            "available": react_available,
            "path": str(react_dist) if react_available else "",
            "status": "experimental_explicit_only",
        },
        "surfaces": {
            "html": "/",
            "terminal": "pertura chat | pertura inspect | pertura context",
            "react": "--ui react",
        },
        "primary_projection": "/api/workbench-view",
        "console_turn": "/api/console/turn",
        "event_stream": "/api/events/stream",
        "workflow_builder": "/api/workflow-builder",
        "debug_projection": "/api/context-review",
        "stable_fields": [
            "perturbseq",
            "execution_state",
            "candidate_actions",
            "activity.product_events",
            "artifacts",
            "report",
        ],
    }


def create_app(workbench, *, ui: str = "auto"):
    from fastapi import FastAPI, HTTPException, Request
    from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
    from fastapi.staticfiles import StaticFiles

    from pertura.core import Store, build_impact_view, build_trace_view
    from pertura.jobs import JobRunner
    from pertura.models import _model_dump
    from pertura.tools.registry import inspect_artifact_summary

    app = FastAPI(title="Pertura Workbench", version="1.0.0")
    runner = JobRunner(max_workers=1)
    ui_mode = ui if ui in {"auto", "builtin", "react"} else "builtin"
    react_dist = _react_dist_dir()
    use_react = ui_mode == "react" and (react_dist / "index.html").exists()
    effective_ui = "react" if use_react else "builtin_html"
    if use_react and (react_dist / "assets").exists():
        app.mount("/assets", StaticFiles(directory=str(react_dist / "assets")), name="assets")

    def _store_for_run(run_id: str = ""):
        return _store_for_workbench(workbench, run_id=run_id)

    def _snapshot_for_run(run_id: str = ""):
        return _snapshot_for_workbench(workbench, run_id=run_id)

    def _graph_for_run(run_id: str = ""):
        store = _store_for_run(run_id)
        if store:
            graph = store.read_graph()
            if graph:
                return graph
        return workbench.graph or {"nodes": [], "edges": []}

    def _analysis_spec_for_run(run_id: str = ""):
        return _analysis_spec_for_workbench(workbench, run_id=run_id)

    def _capability_registry_for_run(run_id: str = ""):
        return _capability_registry_for_workbench(workbench, run_id=run_id)

    def _run_with_cancel(payload: dict, cancel_event):
        previous = getattr(workbench, "_cancel_event", None)
        workbench.set_cancel_event(cancel_event)
        try:
            return workbench.run(
                payload.get("workspace", ""),
                goal=payload.get("goal", ""),
                steps=int(payload.get("steps", 5)),
            )
        finally:
            workbench.set_cancel_event(previous)

    def _step_with_cancel(payload: dict, cancel_event):
        previous = getattr(workbench, "_cancel_event", None)
        workbench.set_cancel_event(cancel_event)
        try:
            return {"actions": workbench.step(int(payload.get("steps", 1)))}
        finally:
            workbench.set_cancel_event(previous)

    def _agent_with_cancel(payload: dict, cancel_event):
        previous = getattr(workbench, "_cancel_event", None)
        workbench.set_cancel_event(cancel_event)
        try:
            return workbench.run_until_pause(
                payload.get("workspace", ""),
                goal=payload.get("goal", ""),
                max_turns=int(payload.get("max_turns", 20)),
                max_repairs=int(payload.get("max_repairs", 5)),
                no_progress_limit=int(payload.get("no_progress_limit", 3)),
            )
        finally:
            workbench.set_cancel_event(previous)

    def _active_agent_job():
        for item in runner.list_jobs():
            if item.get("job_type") in {"agent_run", "agent_continue"} and item.get("status") in {"queued", "running"}:
                return item
        return None

    def _submit_agent_job(job_type: str, req: AgentRunRequest):
        active = _active_agent_job()
        if active:
            return {
                "job_id": active.get("job_id", ""),
                "status": active.get("status", "running"),
                "already_running": True,
            }
        job = runner.submit(
            job_type=job_type,
            payload=_model_dump(req),
            run_id=getattr(workbench, "_run_id", ""),
        )
        return {"job_id": job.job_id, "status": "queued", "already_running": False}

    def _console_state_payload(extra: dict | None = None):
        payload = dict(extra or {})
        payload["execution_state"] = execution_state_payload(workbench, jobs=runner.list_jobs()[:8])
        payload["candidate_actions"] = payload["execution_state"].get("candidate_actions", [])
        return payload

    def _patch_get(patch, key: str):
        if isinstance(patch, dict):
            return patch.get(key)
        return getattr(patch, key, None)

    def _handle_repair_action(action_id: str):
        snap = _snapshot_for_run()
        if snap is None:
            raise HTTPException(400, "No active run")
        if ":" not in action_id:
            raise HTTPException(400, "Repair action requires a patch id")
        action, patch_id = action_id.split(":", 1)
        patch = next(
            (item for item in reversed(list(getattr(snap, "patch_proposals", []) or []))
             if _patch_get(item, "patch_id") == patch_id),
            None,
        )
        if patch is None:
            raise HTTPException(404, f"Unknown repair patch: {patch_id}")
        if _patch_get(patch, "status") != "proposed":
            raise HTTPException(400, f"Repair patch is already {_patch_get(patch, 'status')}")
        if workbench._controller is None:
            from pertura.core import GraphController

            store = _store_for_run()
            if store is None:
                raise HTTPException(400, "No event store is available")
            workbench._controller = GraphController(store, getattr(workbench, "_run_id", "") or snap.run_id)
        if action == "reject_repair":
            workbench._controller.reject_patch(patch_id, "Rejected from analysis console.")
            return _console_state_payload({"handled": "reject_repair", "patch_id": patch_id})
        if action != "retry_repair":
            raise HTTPException(400, f"Unknown repair action: {action}")

        payload = _patch_get(patch, "payload") or {}
        fixed_code = str(payload.get("fixed_code") or "").strip()
        parent_attempt_id = str(payload.get("parent_attempt_id") or "")
        if not fixed_code or not parent_attempt_id:
            raise HTTPException(400, "Repair patch is missing fixed_code or parent_attempt_id")
        parent_attempt = next(
            (item for item in getattr(snap, "attempts", []) or [] if item.attempt_id == parent_attempt_id),
            None,
        )
        if parent_attempt is None:
            raise HTTPException(404, f"Unknown parent attempt: {parent_attempt_id}")
        from pertura.agent.gated_dispatch import gated_dispatch

        assessment = {"status": "user_approved_repair", "summary": _patch_get(patch, "rationale") or "User approved repair"}
        decision = {
            "reason": _patch_get(patch, "rationale") or "User approved repair",
            "capability_ids": list(getattr(parent_attempt, "capability_ids", []) or []),
            "parent_attempt_id": parent_attempt_id,
            "repair_patch_id": patch_id,
        }
        dispatch_action = gated_dispatch(workbench, "retry", fixed_code, assessment, decision, snap, parent_attempt=parent_attempt)
        if dispatch_action != "planned_attempt":
            return _console_state_payload({
                "handled": "retry_repair_blocked",
                "patch_id": patch_id,
                "dispatch_action": dispatch_action,
            })
        workbench._emit("patch_applied", {"patch_id": patch_id, "event_ids": []})
        payload = _submit_agent_job("agent_continue", AgentRunRequest())
        return _console_state_payload({
            "handled": "retry_repair",
            "patch_id": patch_id,
            "dispatch_action": dispatch_action,
            **payload,
        })

    class ConsoleTurnRouter:
        """State router for the product Analysis Console.

        The console is not a durable chat log. Each user turn becomes exactly
        one audited runtime intent: start, answer, continue, pause, repair, or
        report. Keeping that router explicit prevents the API layer from
        becoming another hidden agent loop.
        """

        def route(self, req: ConsoleTurnRequest) -> dict:
            import json
            from uuid import uuid4
            from pertura.models import Goal, _model_dump

            message = (req.message or "").strip()
            action_id = (req.action_id or "").strip()
            snap = _snapshot_for_run()
            active = _active_agent_job()

            if action_id == "pause":
                return agent_pause()
            if action_id.startswith(("retry_repair:", "reject_repair:")):
                return _handle_repair_action(action_id)

            if snap and (action_id == "generate_report" or _looks_like_report_request(message)):
                report_payload = workbench.report()
                return _console_state_payload({
                    "handled": "generate_report",
                    "report": report_payload,
                })

            open_interrupt = next((
                item for item in ((snap.interrupts if snap else []) or [])
                if item.status == "open"
            ), None)
            if open_interrupt and (action_id in {"", "answer_question"} or req.answers or message):
                answer_text = (
                    json.dumps(req.answers, ensure_ascii=False)
                    if req.answers
                    else message
                )
                if not answer_text:
                    raise HTTPException(400, "Answer is required for the active question")
                workbench.answer(open_interrupt.interrupt_id, answer_text)
                return _console_state_payload({
                    "handled": "answer_question",
                    "interrupt_id": open_interrupt.interrupt_id,
                })

            if snap and (action_id.startswith("answer_question") or req.answers):
                design_patch = _console_design_patch(message, req.answers or {}, action_id)
                if not design_patch:
                    raise HTTPException(400, "A structured design answer is required for this question")
                workbench.update_design(
                    design_patch,
                    reason=f"console_turn:{action_id or 'answer_question'}",
                    source="user_confirmed",
                    confidence="high",
                )
                if active:
                    return _console_state_payload({
                        "handled": "answer_question",
                        "design": design_patch,
                        "job_id": active.get("job_id", ""),
                        "status": active.get("status", "running"),
                        "already_running": True,
                    })
                if getattr(snap, "phase", "") in {"complete", "cancelled"}:
                    return _console_state_payload({
                        "handled": "answer_question",
                        "design": design_patch,
                    })
                payload = _submit_agent_job("agent_continue", AgentRunRequest())
                return _console_state_payload({
                    "handled": "answer_question",
                    "design": design_patch,
                    **payload,
                })

            if active:
                return _console_state_payload({
                    "handled": "already_running",
                    "job_id": active.get("job_id", ""),
                    "status": active.get("status", "running"),
                    "already_running": True,
                })

            if snap is None:
                payload = _submit_agent_job("agent_run", AgentRunRequest(
                    workspace=req.workspace or "data",
                    goal=message,
                ))
                return _console_state_payload({"handled": "start_analysis", **payload})

            if message:
                workbench._emit("goal_recorded", {"goal": _model_dump(Goal(
                    goal_id=f"goal_{uuid4().hex[:12]}",
                    text=message,
                    status="active",
                ))})

            payload = _submit_agent_job("agent_continue", AgentRunRequest())
            return _console_state_payload({"handled": "continue_analysis", **payload})

    console_turn_router = ConsoleTurnRouter()

    runner.register_handler("run", _run_with_cancel)
    runner.register_handler("step", _step_with_cancel)
    runner.register_handler("agent_run", _agent_with_cancel)
    runner.register_handler("agent_continue", _agent_with_cancel)

    @app.get("/", response_class=HTMLResponse)
    def gui():
        if use_react:
            return FileResponse(str(react_dist / "index.html"))
        tpl = Path(__file__).parent / "_gui.html"
        return tpl.read_text(encoding="utf-8").replace("{domain_name}", workbench.domain.name)

    @app.get("/api/status")
    def status():
        return workbench.status

    @app.get("/api/ui-info")
    def ui_info():
        return _ui_info_payload(
            workbench,
            requested_ui=ui_mode,
            effective_ui=effective_ui,
            react_dist=react_dist,
        )

    @app.get("/api/runtime-status")
    def runtime_status(recent: int = 20):
        payload = workbench.runtime_status(recent=recent)
        payload["jobs"] = runner.list_jobs()[:recent]
        return payload

    @app.get("/api/workbench-view")
    def workbench_view(run_id: str = "", max_items: int = 8, token_budget: int = 6000, include_debug: bool = True):
        return workbench_view_payload(
            workbench,
            run_id=run_id,
            max_items=max_items,
            token_budget=token_budget,
            jobs=runner.list_jobs()[:max_items],
            include_debug=include_debug,
        )

    @app.get("/api/events/stream")
    async def events_stream(
        request: Request,
        run_id: str = "",
        max_items: int = 20,
        poll_ms: int = 750,
    ):
        async def stream():
            import asyncio
            import json

            seen_event_ids: set[str] = set()
            seen_product_event_ids: set[str] = set()
            last_jobs_signature = ""
            interval = max(0.25, min(5.0, poll_ms / 1000))
            yield _sse_event("ready", {
                "run_id": run_id or getattr(workbench, "_run_id", ""),
                "stream": "event_store",
            })
            while True:
                if await request.is_disconnected():
                    break

                store = _store_for_run(run_id)
                if store:
                    cards = _runtime_event_cards(
                        store.read_events()[-max_items * 4:],
                        max_items=max_items,
                    )
                    for card in cards:
                        event_id = str(card.get("event_id") or "")
                        if not event_id or event_id in seen_event_ids:
                            continue
                        seen_event_ids.add(event_id)
                        yield _sse_event("runtime_event", card)
                    product_cards = _product_event_cards(
                        store.read_events()[-max_items * 4:],
                        max_items=max_items,
                    )
                    for card in product_cards:
                        event_id = str(card.get("event_id") or "")
                        if not event_id or event_id in seen_product_event_ids:
                            continue
                        seen_product_event_ids.add(event_id)
                        yield _sse_event("product_event", card)

                jobs = runner.list_jobs()[:max_items]
                jobs_signature = json.dumps(jobs, sort_keys=True, default=str)
                if jobs_signature != last_jobs_signature:
                    last_jobs_signature = jobs_signature
                    yield _sse_event("jobs", {"jobs": jobs})

                await asyncio.sleep(interval)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    @app.get("/api/execution-state")
    def execution_state(run_id: str = "", selected_node_id: str = ""):
        return execution_state_payload(
            workbench,
            run_id=run_id,
            jobs=runner.list_jobs()[:8],
            selected_node_id=selected_node_id,
        )

    @app.get("/api/capabilities/view")
    def capabilities_view(run_id: str = "", node_id: str = "", max_items: int = 100):
        return capabilities_view_payload(workbench, run_id=run_id, node_id=node_id, max_items=max_items)

    @app.post("/api/capabilities/{capability_id}/toggle")
    def toggle_capability(capability_id: str, req: CapabilityToggleRequest):
        try:
            workbench.set_capability_enabled(capability_id, req.enabled, reason=req.reason)
            return capabilities_view_payload(workbench, node_id="", max_items=100)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get("/api/derivation-view")
    def derivation_view(focus_node: str = "", run_id: str = "", depth: int = 4):
        return derivation_view_payload(workbench, run_id=run_id, focus_node=focus_node, depth=depth)

    @app.get("/api/graph")
    def graph(run_id: str = ""):
        return _graph_for_run(run_id)

    @app.get("/api/analysis-spec")
    def analysis_spec(run_id: str = ""):
        return _analysis_spec_for_run(run_id)

    @app.get("/api/workflow-builder")
    def workflow_builder(run_id: str = ""):
        return workflow_builder_payload(workbench, run_id=run_id)

    @app.post("/api/workflow-builder/draft")
    def workflow_builder_draft(req: WorkflowDraftRequest):
        if workbench._store is None:
            raise HTTPException(409, "Start or initialize a run before saving a workflow draft.")
        from pertura.product.perturbseq.workflow_builder import normalize_workflow_spec

        spec = normalize_workflow_spec(req.analysis_spec)
        workbench._emit("analysis_spec_draft_saved", {
            "analysis_spec": spec,
            "reason": req.reason,
        })
        return workflow_builder_payload(workbench)

    @app.post("/api/workflow-builder/apply")
    def workflow_builder_apply(req: WorkflowApplyRequest):
        if workbench._store is None:
            raise HTTPException(409, "Start or initialize a run before applying a workflow draft.")
        snap = workbench._store.read_snapshot()
        draft = getattr(snap, "analysis_spec_draft", {}) or {}
        if not draft:
            raise HTTPException(400, "No workflow draft to apply.")
        from pertura.product.perturbseq.workflow_builder import normalize_workflow_spec
        from pertura.spec.contracts import audit_analysis_graph

        spec = normalize_workflow_spec(draft)
        audit = audit_analysis_graph(spec, capabilities=_capability_registry_for_workbench(workbench))
        if audit.get("errors"):
            raise HTTPException(400, {"message": "Workflow draft has errors.", "audit": audit})
        old_active = getattr(snap, "active_node_id", "")
        old_active_exists = any(
            item.get("node_id") == old_active
            for item in (spec or {}).get("nodes", [])
        )
        start = (spec or {}).get("start_node_id", "")
        should_enter_start = bool(start and (not old_active or not old_active_exists))

        workbench.domain.analysis_graph = spec
        workbench._emit("analysis_spec_draft_applied", {
            "analysis_spec": spec,
            "reason": req.reason,
        })
        fresh = workbench._store.read_snapshot()
        has_active_start_visit = any(
            getattr(item, "node_id", "") == start
            and getattr(item, "branch_id", "") == getattr(fresh, "active_branch", "main")
            and getattr(item, "status", "") == "active"
            for item in getattr(fresh, "node_visits", []) or []
        )
        if should_enter_start and not has_active_start_visit:
            workbench._emit("node_entered", {
                "node_id": start,
                "branch_id": getattr(fresh, "active_branch", "main"),
                "reason": "workflow_builder_apply",
            })
        return workbench_view_payload(workbench, max_items=8, include_debug=False)

    @app.post("/api/workflow-builder/reset")
    def workflow_builder_reset(req: WorkflowApplyRequest):
        if workbench._store is None:
            raise HTTPException(409, "Start or initialize a run before clearing a workflow draft.")
        workbench._emit("analysis_spec_draft_cleared", {"reason": req.reason})
        return workflow_builder_payload(workbench)

    @app.get("/api/domain")
    def domain_browser(include_core_tools: bool = True):
        return domain_browser_payload(workbench, include_core_tools=include_core_tools)

    @app.get("/api/domain/capabilities")
    def domain_capabilities(node_id: str = ""):
        payload = domain_browser_payload(workbench, include_core_tools=False)
        caps = payload.get("capabilities", [])
        if node_id:
            allowed = set(payload.get("capabilities_by_node", {}).get(node_id, []))
            caps = [item for item in caps if item.get("id") in allowed]
        return {
            "domain": payload.get("domain", {}),
            "node_id": node_id,
            "capabilities": caps,
        }

    @app.get("/api/analysis-spec/audit")
    def analysis_spec_audit(run_id: str = "", strict: bool = False):
        try:
            return analysis_spec_audit_payload(workbench, run_id=run_id, strict=strict)
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.get("/api/analysis-spec/contract")
    def analysis_spec_contract(node_id: str = "", run_id: str = ""):
        try:
            return analysis_spec_contract_payload(workbench, node_id=node_id, run_id=run_id)
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.get("/api/node-contract")
    def runtime_node_contract(node_id: str = "", run_id: str = ""):
        try:
            return runtime_node_contract_payload(workbench, node_id=node_id, run_id=run_id)
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.get("/api/context-review")
    def context_review(
        run_id: str = "",
        purpose: str = "audit",
        max_items: int = 8,
        token_budget: int = 6000,
    ):
        try:
            return context_review_payload(
                workbench,
                run_id=run_id,
                purpose=purpose,
                max_items=max_items,
                token_budget=token_budget,
            )
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.get("/api/run-audit")
    def run_audit(run_id: str = ""):
        try:
            return run_audit_payload(workbench, run_id=run_id)
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.get("/api/rethink")
    def rethink_latest(run_id: str = "", issue: str = "", depth: int = 5):
        try:
            return rethinking_payload(
                workbench,
                run_id=run_id,
                issue=issue,
                depth=depth,
            )
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.get("/api/rethink/{node_id}")
    def rethink_node(node_id: str, run_id: str = "", issue: str = "", depth: int = 5):
        try:
            return rethinking_payload(
                workbench,
                run_id=run_id,
                node_id=node_id,
                issue=issue,
                depth=depth,
            )
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.get("/api/harness-manifest")
    def harness_manifest():
        return harness_manifest_payload()

    @app.post("/api/analysis-spec")
    def update_analysis_spec(req: AnalysisSpecRequest):
        workbench.load_analysis_spec(req.analysis_spec, reason=req.reason)
        return workbench.status

    @app.post("/api/analysis-spec/compile")
    def compile_analysis_spec(req: AnalysisSpecCompileRequest):
        from pertura.spec.compiler import compile_conditions
        report = compile_conditions(
            req.analysis_spec,
            provider=req.provider,
            domain_context=req.domain_context,
        )
        payload = report.to_dict()
        if req.apply:
            workbench.load_analysis_spec(payload["spec"], reason=req.reason)
        return payload

    @app.post("/api/design")
    def update_design(req: DesignUpdateRequest):
        workbench.update_design(req.design, reason=req.reason,
                                source=req.source, confidence=req.confidence)
        return workbench.status

    @app.get("/api/trace/{node_id}")
    def trace_node(node_id: str, depth: int = 4, run_id: str = ""):
        graph = _graph_for_run(run_id)
        if not any(n.get("node_id") == node_id for n in graph.get("nodes", [])):
            raise HTTPException(404, f"Node {node_id} not found")
        return build_trace_view(graph, node_id, depth=depth)

    @app.get("/api/impact/{node_id}")
    def impact_node(node_id: str, depth: int = 4, run_id: str = ""):
        graph = _graph_for_run(run_id)
        if not any(n.get("node_id") == node_id for n in graph.get("nodes", [])):
            raise HTTPException(404, f"Node {node_id} not found")
        return build_impact_view(graph, node_id, depth=depth)

    @app.get("/api/report")
    def report():
        return workbench.report_preview()

    @app.post("/api/report/generate")
    def generate_report():
        return workbench.report()

    @app.post("/api/console/turn")
    def console_turn(req: ConsoleTurnRequest):
        return console_turn_router.route(req)

    @app.post("/api/run")
    def run(req: RunRequest):
        result = workbench.run(req.workspace, goal=req.goal, steps=req.steps)
        return {**result, **workbench.status}

    @app.post("/api/step")
    def step():
        actions = workbench.step(1)
        return {"actions": actions, **workbench.status}

    @app.get("/api/runs")
    def list_runs():
        runs_dir = Path("runs")
        if not runs_dir.exists():
            return {"runs": []}
        runs = []
        for d in sorted(runs_dir.iterdir(), reverse=True):
            db = d / "events.db"
            if not db.exists():
                continue
            try:
                snap = Store(d).read_snapshot()
                runs.append({
                    "run_id": snap.run_id if snap else d.name,
                    "phase": snap.phase if snap else "unknown",
                    "workspace": snap.workspace if snap else "",
                    "goal": snap.goal if snap else "",
                    "attempts": len(snap.attempts) if snap else 0,
                    "observations": len(snap.observations) if snap else 0,
                })
            except Exception:
                runs.append({"run_id": d.name, "phase": "error"})
        return {"runs": runs}

    @app.get("/api/artifacts")
    def list_artifacts(run_id: str = ""):
        snap = _snapshot_for_run(run_id)
        if not snap:
            return {"artifacts": []}
        return {"artifacts": [_model_dump(a) for a in snap.artifacts]}

    @app.get("/api/artifacts/{artifact_id}/preview")
    def preview_artifact(artifact_id: str, run_id: str = ""):
        snap = _snapshot_for_run(run_id)
        if not snap:
            raise HTTPException(404, "No data")
        preview = inspect_artifact_summary(artifact_id=artifact_id, snap=snap)
        if "error" in preview:
            raise HTTPException(404, f"Artifact {artifact_id} not found")
        return {"artifact_id": artifact_id, **preview}

    @app.get("/api/artifacts/{artifact_id}/file")
    def artifact_file(artifact_id: str, run_id: str = ""):
        snap = _snapshot_for_run(run_id)
        if not snap:
            raise HTTPException(404, "No data")
        artifact = next((item for item in snap.artifacts if item.artifact_id == artifact_id), None)
        if not artifact:
            raise HTTPException(404, f"Artifact {artifact_id} not found")
        try:
            from pertura.tools.registry import _allowed_path
            path = _allowed_path(artifact.path, snap=snap)
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
        if not path.exists() or not path.is_file():
            raise HTTPException(404, "Artifact file not found")
        return FileResponse(str(path))

    @app.post("/api/jobs/run")
    def start_run_job(req: RunRequest):
        job = runner.submit(
            job_type="run",
            payload=_model_dump(req),
            run_id=getattr(workbench, "_run_id", ""),
        )
        return {"job_id": job.job_id, "status": "queued"}

    @app.post("/api/jobs/step")
    def start_step_job():
        job = runner.submit(
            job_type="step",
            payload={"steps": 1},
            run_id=getattr(workbench, "_run_id", ""),
        )
        return {"job_id": job.job_id, "status": "queued"}

    @app.post("/api/agent/start")
    def agent_start(req: AgentRunRequest):
        payload = _submit_agent_job("agent_run", req)
        payload["execution_state"] = execution_state_payload(workbench, jobs=runner.list_jobs()[:8])
        return payload

    @app.post("/api/agent/continue")
    def agent_continue(req: AgentRunRequest):
        payload = _submit_agent_job("agent_continue", req)
        payload["execution_state"] = execution_state_payload(workbench, jobs=runner.list_jobs()[:8])
        return payload

    @app.post("/api/agent/pause")
    def agent_pause():
        cancelled = 0
        current_cancel = getattr(workbench, "_cancel_event", None)
        if current_cancel is not None:
            current_cancel.set()
        for item in runner.list_jobs():
            if item.get("job_type") in {"agent_run", "agent_continue"} and item.get("status") in {"queued", "running"}:
                if runner.cancel(item.get("job_id", "")):
                    cancelled += 1
        return {
            "cancelled": cancelled > 0,
            "cancelled_jobs": cancelled,
            "execution_state": execution_state_payload(workbench, jobs=runner.list_jobs()[:8]),
        }

    @app.get("/api/jobs")
    def list_jobs():
        return {"jobs": runner.list_jobs()}

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str):
        job = runner.get(job_id)
        if not job:
            raise HTTPException(404, f"Job {job_id} not found")
        return job

    @app.post("/api/jobs/{job_id}/cancel")
    def cancel_job(job_id: str):
        return {"cancelled": runner.cancel(job_id)}

    @app.post("/api/jobs/{job_id}/retry")
    def retry_job(job_id: str):
        job = runner.retry(job_id)
        if not job:
            raise HTTPException(409, f"Job {job_id} is not retryable")
        return job.to_dict()

    @app.post("/api/answer/{interrupt_id}")
    def answer(interrupt_id: str, req: AnswerRequest):
        workbench.answer(interrupt_id, req.answer)
        return workbench.status

    @app.get("/api/interrupts")
    def interrupts():
        snap = _snapshot_for_run()
        return {"interrupts": [
            _model_dump(i) for i in (snap.interrupts if snap else [])
            if i.status == "open"
        ]}

    return app
