"""Gated dispatch layer between LLM tool calls and graph mutations."""

from __future__ import annotations

from uuid import uuid4

from pertura.spec.gating import GateEvaluator, gate_event_payload
from pertura.models import Finding, Interrupt, _model_dump
from pertura.capabilities import CapabilityRegistry


def gated_dispatch(wb, action: str, code: str, assessment: dict, decision: dict, snap, *, parent_attempt=None) -> str:
    """Validate analysis-node semantics before committing state-changing actions."""
    if action == "request_node_transition":
        return _dispatch_node_transition(wb, assessment, decision, snap)
    if action == "complete_node":
        return _dispatch_complete_node(wb, assessment, decision, snap)
    if action == "skip_node":
        return _dispatch_skip_node(wb, assessment, decision, snap)

    if action in {"execute_code", "retry"}:
        blocked = _check_execute_allowed(wb, decision, snap)
        if blocked:
            return blocked

    from pertura.agent.dispatch import _dispatch_decision
    return _dispatch_decision(wb, action, code, assessment, decision, snap, parent_attempt=parent_attempt)


def _dispatch_node_transition(wb, assessment: dict, decision: dict, snap) -> str:
    target_node_id = decision.get("target_node_id") or decision.get("node_id") or decision.get("target", "")
    reason = decision.get("reason") or assessment.get("summary", "")
    wb._emit("node_transition_requested", {
        "source_node_id": snap.active_node_id,
        "target_node_id": target_node_id,
        "branch_id": snap.active_branch,
        "reason": reason,
    })

    gate = GateEvaluator(snap.analysis_spec).evaluate_enter(snap, target_node_id)
    wb._emit("gate_evaluated", gate_event_payload(gate))

    if gate.decision == "skip":
        wb._emit("node_skipped", {
            "node_id": target_node_id,
            "branch_id": snap.active_branch,
            "reason": gate.reason,
        })
        return "node_skipped"

    if gate.can_enter:
        if gate.decision == "warn":
            wb._emit("finding_recorded", {"finding": _model_dump(Finding(
                finding_id=f"fnd_{uuid4().hex[:12]}",
                finding_type="node_gate_warning",
                severity="warning",
                suggested_action="continue",
                summary=gate.reason,
                affected_ids=[target_node_id],
            ))})
        wb._emit("node_entered", {
            "node_id": target_node_id,
            "branch_id": snap.active_branch,
            "reason": reason,
        })
        return "node_entered"

    wb._emit("node_transition_blocked", {
        "source_node_id": snap.active_node_id,
        "target_node_id": target_node_id,
        "branch_id": snap.active_branch,
        "decision": gate.decision,
        "reason": gate.reason,
    })
    if gate.decision == "human_interrupt":
        wb._emit("interrupt_opened", {"interrupt": _model_dump(Interrupt(
            interrupt_id=f"irq_{uuid4().hex[:12]}",
            source="node_gate",
            question=gate.reason or f"Human input required before entering {target_node_id}.",
            options=[],
            default_action="update_design",
        ))})
        return "waiting_for_human"

    wb._emit("finding_recorded", {"finding": _model_dump(Finding(
        finding_id=f"fnd_{uuid4().hex[:12]}",
        finding_type="node_transition_blocked",
        severity="blocking" if gate.decision == "block" else "warning",
        suggested_action="trace_upstream" if gate.decision == "autonomous_recovery" else "ask_user",
        summary=gate.reason,
        affected_ids=[target_node_id],
    ))})
    return "blocked"


def _check_execute_allowed(wb, decision: dict, snap) -> str:
    if snap.analysis_spec and not snap.active_node_id:
        wb._emit("finding_recorded", {"finding": _model_dump(Finding(
            finding_id=f"fnd_{uuid4().hex[:12]}",
            finding_type="missing_analysis_node",
            severity="blocking",
            suggested_action="request_node_transition",
            summary="execute_code requires an active analysis node.",
        ))})
        return "blocked"

    capability_ids = decision.get("capability_ids", []) or []
    if isinstance(capability_ids, str):
        capability_ids = [capability_ids]
    registry = CapabilityRegistry(snap.capabilities)
    missing = [cap for cap in capability_ids if not registry.has(cap)]
    if missing:
        wb._emit("finding_recorded", {"finding": _model_dump(Finding(
            finding_id=f"fnd_{uuid4().hex[:12]}",
            finding_type="unknown_capability",
            severity="blocking",
            suggested_action="list_capabilities",
            summary=f"Unknown capabilities: {', '.join(missing)}",
            affected_ids=[snap.active_node_id] if snap.active_node_id else [],
        ))})
        return "blocked"
    allowed = GateEvaluator(snap.analysis_spec).allowed_capabilities(snap.active_node_id)
    if allowed and capability_ids:
        disallowed = [cap for cap in capability_ids if cap not in allowed]
        if disallowed:
            wb._emit("finding_recorded", {"finding": _model_dump(Finding(
                finding_id=f"fnd_{uuid4().hex[:12]}",
                finding_type="capability_not_allowed_in_node",
                severity="blocking",
                suggested_action="request_node_transition",
                summary=f"Capabilities not allowed in node {snap.active_node_id}: {', '.join(disallowed)}",
                affected_ids=[snap.active_node_id],
            ))})
            return "blocked"
    elif allowed and snap.active_node_id and not capability_ids:
        wb._emit("finding_recorded", {"finding": _model_dump(Finding(
            finding_id=f"fnd_{uuid4().hex[:12]}",
            finding_type="missing_capability_declaration",
            severity="blocking",
            suggested_action="list_capabilities",
            summary=(
                f"execute_code is blocked in node {snap.active_node_id} until it declares "
                f"capability_ids; declare one of: {', '.join(allowed)}"
            ),
            affected_ids=[snap.active_node_id],
        ))})
        return "blocked"
    if len(snap.attempts) >= snap.budget.max_attempts:
        wb._emit("finding_recorded", {"finding": _model_dump(Finding(
            finding_id=f"fnd_{uuid4().hex[:12]}",
            finding_type="budget_exhausted",
            severity="blocking",
            suggested_action="ask_user",
            summary=f"Attempt budget exhausted: {len(snap.attempts)}/{snap.budget.max_attempts}.",
            affected_ids=[snap.active_node_id] if snap.active_node_id else [],
        ))})
        return "blocked"
    return ""


def _dispatch_complete_node(wb, assessment: dict, decision: dict, snap) -> str:
    node_id = decision.get("node_id") or snap.active_node_id
    if not node_id:
        wb._emit("finding_recorded", {"finding": _model_dump(Finding(
            finding_id=f"fnd_{uuid4().hex[:12]}",
            finding_type="missing_analysis_node",
            severity="blocking",
            suggested_action="request_node_transition",
            summary="complete_node requires an active analysis node.",
        ))})
        return "blocked"
    if node_id != snap.active_node_id:
        wb._emit("finding_recorded", {"finding": _model_dump(Finding(
            finding_id=f"fnd_{uuid4().hex[:12]}",
            finding_type="invalid_node_completion",
            severity="blocking",
            suggested_action="request_node_transition",
            summary=f"Cannot complete node {node_id}; active node is {snap.active_node_id or 'unset'}.",
            affected_ids=[node_id],
        ))})
        return "blocked"
    active_visit = next((
        visit for visit in reversed(snap.node_visits)
        if visit.node_id == node_id
        and visit.branch_id == snap.active_branch
        and visit.status == "active"
    ), None)
    if active_visit is None:
        wb._emit("finding_recorded", {"finding": _model_dump(Finding(
            finding_id=f"fnd_{uuid4().hex[:12]}",
            finding_type="invalid_node_completion",
            severity="blocking",
            suggested_action="request_node_transition",
            summary=f"Cannot complete node {node_id}; no active visit exists on branch {snap.active_branch}.",
            affected_ids=[node_id],
        ))})
        return "blocked"

    gate = GateEvaluator(snap.analysis_spec).evaluate_completion(snap, node_id)
    wb._emit("gate_evaluated", gate_event_payload(gate, gate_type="completion"))
    if gate.decision in {"human_interrupt", "block", "autonomous_recovery"}:
        wb._emit("finding_recorded", {"finding": _model_dump(Finding(
            finding_id=f"fnd_{uuid4().hex[:12]}",
            finding_type="node_completion_blocked",
            severity="blocking" if gate.decision != "autonomous_recovery" else "warning",
            suggested_action="ask_user" if gate.decision == "human_interrupt" else "trace_upstream",
            summary=gate.reason,
            affected_ids=[node_id],
        ))})
        if gate.decision == "human_interrupt":
            wb._emit("interrupt_opened", {"interrupt": _model_dump(Interrupt(
                interrupt_id=f"irq_{uuid4().hex[:12]}",
                source="node_completion_gate",
                question=gate.reason or f"Human input required before completing {node_id}.",
                default_action="update_design",
            ))})
            return "waiting_for_human"
        return "blocked"
    if gate.decision == "warn":
        wb._emit("finding_recorded", {"finding": _model_dump(Finding(
            finding_id=f"fnd_{uuid4().hex[:12]}",
            finding_type="node_completion_warning",
            severity="warning",
            suggested_action="continue",
            summary=gate.reason,
            affected_ids=[node_id],
        ))})
    wb._emit("node_completed", {
        "node_id": node_id,
        "branch_id": snap.active_branch,
        "summary": decision.get("summary") or assessment.get("summary", ""),
    })
    return "node_completed"


def _dispatch_skip_node(wb, assessment: dict, decision: dict, snap) -> str:
    node_id = decision.get("node_id") or decision.get("target_node_id") or snap.active_node_id
    if not node_id:
        wb._emit("finding_recorded", {"finding": _model_dump(Finding(
            finding_id=f"fnd_{uuid4().hex[:12]}",
            finding_type="missing_analysis_node",
            severity="blocking",
            suggested_action="request_node_transition",
            summary="skip_node requires node_id or an active analysis node.",
        ))})
        return "blocked"
    if snap.analysis_spec and not any(node.get("node_id") == node_id for node in snap.analysis_spec.get("nodes", [])):
        wb._emit("finding_recorded", {"finding": _model_dump(Finding(
            finding_id=f"fnd_{uuid4().hex[:12]}",
            finding_type="unknown_analysis_node",
            severity="blocking",
            suggested_action="request_node_transition",
            summary=f"Unknown analysis node: {node_id}",
        ))})
        return "blocked"
    if snap.analysis_spec and node_id != snap.active_node_id:
        reachable = {
            node.node_id
            for node in GateEvaluator(snap.analysis_spec).reachable_nodes(snap.active_node_id)
        }
        if node_id not in reachable:
            wb._emit("finding_recorded", {"finding": _model_dump(Finding(
                finding_id=f"fnd_{uuid4().hex[:12]}",
                finding_type="invalid_node_skip",
                severity="blocking",
                suggested_action="request_node_transition",
                summary=f"Cannot skip node {node_id}; it is not current or reachable from {snap.active_node_id or 'unset'}.",
                affected_ids=[node_id],
            ))})
            return "blocked"
    wb._emit("node_skipped", {
        "node_id": node_id,
        "branch_id": snap.active_branch,
        "reason": decision.get("reason") or assessment.get("summary", ""),
    })
    return "node_skipped"
