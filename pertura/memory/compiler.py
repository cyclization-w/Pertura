"""Context compilation: Snapshot/Graph -> bounded ContextView."""

from __future__ import annotations

from pertura.core.views import build_context_view
from pertura.models import Context, Snapshot


def compile_context(snap: Snapshot, *, graph: dict | None = None, max_items: int = 12) -> Context:
    """Return the planner context as a typed, bounded Pydantic object."""
    view = build_context_view(snap, graph, purpose="planner", max_items=max_items)
    return Context(**{
        "run_id": view["run_id"],
        "phase": view["phase"],
        "goal": view["goal"],
        "active_branch": view["active_branch"],
        "active_node_id": view["active_node_id"],
        "analysis_node": view["analysis_node"],
        "current_node_progress": view["current_node_progress"],
        "reachable_nodes": view["reachable_nodes"],
        "blocked_transitions": view["blocked_transitions"],
        "gate_requirements": view["gate_requirements"],
        "design": view["design"],
        "design_meta": view["design_meta"],
        "protocol": view["protocol"],
        "workspace_files": view["workspace_files"],
        "active_stage": view["active_stage"],
        "attempts_done": view["attempts_done"],
        "budget_remaining": view["budget_remaining"],
        "open_triggers": view["open_triggers"],
        "open_approvals": view["open_approvals"],
        "open_interrupts": view["open_interrupts"],
        "capabilities": view["capabilities"],
        "recent_attempts": view["recent_attempts"],
        "recent_artifacts": view["recent_artifacts"],
        "memory": view["memory"],
        "coverage": view["coverage"],
        "observation_memory": view["observation_memory"],
        "intent": view["intent"],
        "recent_findings": view["recent_findings"],
        "graph_summary": view["graph_summary"],
        "truncated": view["truncated"],
    })
