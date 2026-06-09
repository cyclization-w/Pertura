"""Runtime autopilot for editable analysis workflows.

The controller is deliberately read-only. It decides whether the harness can
complete or advance the current analysis node without asking the LLM to make a
process-control decision. Callers still mutate through gated_dispatch.
"""

from __future__ import annotations

from typing import Any

from pertura.core.node_navigation import evaluate_node_navigation
from pertura.models import Snapshot
from pertura.spec.gating import GateEvaluator


def evaluate_workflow_autopilot(snap: Snapshot | None) -> dict[str, Any]:
    """Return the next workflow-control action for the runtime harness."""
    nav = evaluate_node_navigation(snap)
    if snap is None:
        return _decision("none", "No run snapshot is available.", navigation=nav)
    if not getattr(snap, "analysis_spec", None):
        return _decision("none", "No analysis workflow is loaded.", navigation=nav)
    if nav.get("status") in {"none", "stay", "ask_user", "blocked"}:
        return _decision("none", nav.get("reason", "Workflow autopilot has no action."), navigation=nav)

    current_id = nav.get("current_id") or nav.get("current_node_id") or getattr(snap, "active_node_id", "")
    if not current_id:
        return _decision("none", "No active workflow node is available.", navigation=nav)

    if nav.get("status") == "complete":
        return _decision(
            "auto_complete",
            nav.get("reason", "Current node completion gate passed."),
            current_node_id=current_id,
            navigation=nav,
        )

    candidates = [
        item for item in (nav.get("candidates") or [])
        if item.get("can_enter") and _auto_edge_allowed(snap, current_id, item.get("node_id", ""))
    ]
    if len(candidates) == 1:
        target = candidates[0].get("node_id", "")
        return _decision(
            "auto_advance",
            nav.get("reason") or f"Single ready workflow successor: {target}.",
            current_node_id=current_id,
            target_node_id=target,
            candidates=candidates,
            navigation=nav,
        )
    if len(candidates) > 1:
        return _decision(
            "choose_next",
            "Multiple ready workflow successors are available; user should choose the next stage.",
            current_node_id=current_id,
            candidates=candidates,
            navigation=nav,
        )
    return _decision(
        "none",
        nav.get("reason", "No auto-forward workflow edge is ready."),
        current_node_id=current_id,
        candidates=nav.get("candidates") or [],
        navigation=nav,
    )


def workflow_gap(snap: Snapshot | None) -> dict[str, Any]:
    """Summarize why the current node cannot move yet."""
    decision = evaluate_workflow_autopilot(snap)
    nav = decision.get("navigation") or {}
    status = nav.get("status", "")
    missing = nav.get("missing") or []
    gap_type = "none"
    next_runtime_action = "continue"
    if status == "ask_user":
        gap_type = "human_fact"
        next_runtime_action = "ask_user"
    elif status in {"stay", "blocked"} and missing:
        gap_type = "computable_or_blocked"
        next_runtime_action = "execute_capability" if status == "stay" else "repair"
    elif decision.get("action") in {"auto_complete", "auto_advance"}:
        gap_type = "ready_to_advance"
        next_runtime_action = "harness_autopilot"
    elif decision.get("action") == "choose_next":
        gap_type = "choice"
        next_runtime_action = "choose_next_node"
    return {
        "gap_type": gap_type,
        "next_runtime_action": next_runtime_action,
        "reason": decision.get("reason") or nav.get("reason", ""),
        "missing": missing,
        "autopilot": decision,
    }


def _auto_edge_allowed(snap: Snapshot, source: str, target: str) -> bool:
    if not source or not target:
        return False
    try:
        evaluator = GateEvaluator(getattr(snap, "analysis_spec", {}) or {})
    except Exception:
        return False
    spec = evaluator.spec
    if spec is None:
        return False
    current = spec.node(source)
    if current is not None and target in (getattr(current, "next_nodes", []) or []):
        return True
    for edge in getattr(spec, "edges", []) or []:
        if getattr(edge, "source", "") != source or getattr(edge, "target", "") != target:
            continue
        return getattr(edge, "edge_type", "next") in {"next", "forward"} and bool(getattr(edge, "auto", True))
    return False


def _decision(action: str, reason: str, **extra: Any) -> dict[str, Any]:
    return {
        "action": action,
        "reason": reason,
        **extra,
    }
