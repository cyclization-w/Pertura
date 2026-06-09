"""GUI/API-first perturb-seq workbench projection."""

from __future__ import annotations

from typing import Any

from pertura.models import Event, Snapshot, _model_dump

from .capability_catalog import compile_capability_catalog, render_turn_card
from .design_ledger import compile_design_ledger
from .quality import compile_quality_flags
from .sweeps import compile_branch_board


def compile_perturbseq_view(
    snap: Snapshot | None,
    *,
    events: list[Event] | None = None,
    navigation: dict[str, Any] | None = None,
    outcome_text: str = "",
    last_attempt_delta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    design_ledger = compile_design_ledger(snap)
    active_stage = _active_stage(snap)
    capability_catalog = compile_capability_catalog(
        snap,
        design_ledger,
        active_node_id=active_stage.get("node_id", ""),
    )
    selected = capability_catalog.get("selected_capability") or {}
    branch_board = compile_branch_board(snap, active_capability_id=selected.get("id", ""))
    quality_flags = compile_quality_flags(snap, design_ledger)
    flow = _flow(snap, navigation=navigation or {})
    evidence_board = _evidence_board(snap)
    product_timeline = compile_product_timeline(events or [])
    view = {
        "view_type": "perturbseq_workbench",
        "schema_version": "v1",
        "goal": _goal(snap),
        "design_ledger": design_ledger,
        "active_stage": active_stage,
        "flow": flow,
        "capability_catalog": capability_catalog,
        "ready_capabilities": capability_catalog.get("ready_capabilities", []),
        "blocked_capabilities": capability_catalog.get("blocked_capabilities", []),
        "suggested_questions": design_ledger.get("suggested_questions", []),
        "quality_flags": quality_flags,
        "evidence_board": evidence_board,
        "branch_board": branch_board,
        "product_timeline": product_timeline,
        "navigation": navigation or {},
        "node_execution_guidance": _node_execution_guidance(snap, navigation or {}),
        "hidden_tool_ids": capability_catalog.get("hidden_tool_ids", []),
    }
    view["turn_card_markdown"] = render_turn_card(
        view,
        outcome_text=outcome_text,
        last_attempt_delta=last_attempt_delta,
    )
    return view


def compile_product_timeline(events: list[Event], *, max_items: int = 30) -> list[dict[str, Any]]:
    mapping = {
        "goal_recorded": "planning",
        "node_entered": "planning",
        "node_transition_requested": "planning",
        "attempt_planned": "running_code",
        "execution_output": "execution_output",
        "outcome_recorded": "result_recorded",
        "artifact_registered": "artifact_ready",
        "observation_registered": "observation_recorded",
        "interrupt_opened": "question_opened",
        "patch_proposed": "repair_proposed",
        "patch_applied": "repair_applied",
        "branch_opened": "branch_started",
        "branch_activated": "branch_started",
        "finding_recorded": "blocked",
        "run_complete": "complete",
        "job_submitted": "running_code",
        "job_completed": "result_recorded",
    }
    out = []
    for event in reversed(events or []):
        kind = mapping.get(event.event_type)
        if not kind:
            continue
        payload = event.payload or {}
        out.append({
            "event_id": event.event_id,
            "event_type": event.event_type,
            "product_type": kind,
            "timestamp": str(event.timestamp),
            "title": _timeline_title(kind, payload),
            "summary": _timeline_summary(event.event_type, payload),
        })
        if len(out) >= max_items:
            break
    return list(reversed(out))


def _active_stage(snap: Snapshot | None) -> dict[str, Any]:
    if snap is None:
        return {"node_id": "", "title": "No run", "purpose": ""}
    node_id = getattr(snap, "active_node_id", "")
    for item in (getattr(snap, "analysis_spec", {}) or {}).get("nodes", []) or []:
        if item.get("node_id") == node_id:
            return {
                "node_id": node_id,
                "title": item.get("title") or node_id,
                "purpose": item.get("purpose", ""),
                "allowed_capabilities": item.get("allowed_capabilities", []),
                "next_nodes": item.get("next_nodes", []),
            }
    return {"node_id": node_id, "title": node_id or "Run", "purpose": ""}


def _flow(snap: Snapshot | None, *, navigation: dict[str, Any]) -> list[dict[str, Any]]:
    if snap is None:
        return []
    active = getattr(snap, "active_node_id", "")
    visits = {(visit.node_id, visit.status) for visit in getattr(snap, "node_visits", []) or []}
    out = []
    for index, node in enumerate((getattr(snap, "analysis_spec", {}) or {}).get("nodes", []) or [], start=1):
        node_id = node.get("node_id", "")
        status = "active" if node_id == active else "pending"
        if (node_id, "completed") in visits:
            status = "completed"
        if navigation.get("target_node_id") == node_id:
            status = "next"
        out.append({
            "index": index,
            "node_id": node_id,
            "title": node.get("title") or node_id,
            "purpose": node.get("purpose", ""),
            "status": status,
            "expected_outputs": node.get("expected_outputs", []),
        })
    return out


def _evidence_board(snap: Snapshot | None) -> dict[str, Any]:
    if snap is None:
        return {"observations": [], "artifacts": []}
    observations = []
    for obs in getattr(snap, "observations", []) or []:
        observations.append({
            "observation_id": obs.observation_id,
            "target": obs.target,
            "metric": obs.metric,
            "value": obs.value,
            "contrast": obs.contrast,
            "method": obs.method,
            "branch_id": obs.branch_id,
            "attempt_id": obs.attempt_id,
            "artifact_id": obs.artifact_id,
            "confidence": (obs.uncertainty or {}).get("confidence", ""),
        })
    artifacts = [
        {
            "artifact_id": art.artifact_id,
            "kind": art.kind,
            "summary": art.summary,
            "path": art.path,
            "attempt_id": art.attempt_id,
        }
        for art in getattr(snap, "artifacts", []) or []
    ]
    return {"observations": observations[-30:], "artifacts": artifacts[-20:]}


def _node_execution_guidance(snap: Snapshot | None, navigation: dict[str, Any]) -> dict[str, Any]:
    if snap is None:
        return {}
    if navigation.get("status") == "advance":
        return {
            "primary_instruction": f"Current stage has enough evidence; request transition to {navigation.get('target_node_id')}.",
            "avoid_actions": ["Do not repeat dataset inspection, inspect_workspace, or load_dataset after dataset profiling."],
            "preferred_tools": ["complete_node", "request_node_transition"],
        }
    return {
        "primary_instruction": "",
        "avoid_actions": [],
        "preferred_tools": [],
    }


def _goal(snap: Snapshot | None) -> str:
    if snap is None:
        return ""
    if getattr(snap, "goals", None):
        return snap.goals[-1].text
    return getattr(snap, "goal", "")


def _timeline_title(kind: str, payload: dict[str, Any]) -> str:
    if kind == "planning":
        return payload.get("reason") or payload.get("node_id") or "Planning"
    if kind == "running_code":
        attempt = payload.get("attempt") or {}
        return attempt.get("title") or payload.get("job_type") or "Running code"
    if kind == "artifact_ready":
        artifact = payload.get("artifact") or {}
        return artifact.get("summary") or artifact.get("kind") or "Artifact ready"
    if kind == "observation_recorded":
        obs = payload.get("observation") or {}
        return f"{obs.get('target', 'observation')} {obs.get('metric', '')}".strip()
    if kind == "repair_proposed":
        patch = payload.get("patch") or {}
        return patch.get("rationale") or "Repair proposed"
    if kind == "complete":
        return "Analysis complete"
    return kind.replace("_", " ")


def _timeline_summary(event_type: str, payload: dict[str, Any]) -> str:
    if event_type == "execution_output":
        output = payload.get("output") or payload
        return str(output.get("stderr") or output.get("stdout") or output)[:500]
    if event_type == "outcome_recorded":
        outcome = payload.get("outcome") or {}
        return outcome.get("summary") or outcome.get("status") or ""
    if event_type == "finding_recorded":
        finding = payload.get("finding") or {}
        return finding.get("summary") or ""
    return str(_model_dump(payload))[:500] if payload else ""
