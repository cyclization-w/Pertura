"""Conservative analysis-node navigation projection.

The navigator is a product/runtime projection: it reads the current snapshot
and analysis spec, then recommends whether the active analysis node should
stay put, ask the user, complete, or advance to an immediate next node. It does
not mutate state; callers must still go through gated dispatch.
"""

from __future__ import annotations

from typing import Any

from pertura.models import Snapshot
from pertura.spec.gating import GateEvaluator


def evaluate_node_navigation(snap: Snapshot | None) -> dict[str, Any]:
    if snap is None:
        return _decision("none", "No run snapshot is available.")
    try:
        evaluator = GateEvaluator(getattr(snap, "analysis_spec", {}) or {})
    except Exception as exc:
        return _decision("stay", f"Analysis graph is incomplete: {exc}")
    spec = evaluator.spec
    if spec is None:
        return _decision("stay", "No analysis graph is loaded.")
    current_id = getattr(snap, "active_node_id", "") or spec.start_node_id
    current = spec.node(current_id)
    if current is None:
        return _decision("stay", f"Active node is not in the analysis graph: {current_id}.", current_id=current_id)

    roadmap = _roadmap(spec, current_id)
    blockers = _blocking_runtime_triggers(snap)
    if blockers:
        return _decision(
            "blocked",
            "Open blocking runtime trigger should be repaired or resolved before node navigation.",
            current_id=current_id,
            roadmap=roadmap,
            blockers=blockers,
        )
    if _has_open_interrupt(snap):
        return _decision(
            "ask_user",
            "Open human interrupt should be answered before node navigation.",
            current_id=current_id,
            roadmap=roadmap,
        )

    completion = evaluator.evaluate_completion(snap, current_id)
    if not completion.condition_results and not _node_has_material_progress(snap, current_id):
        return _decision(
            "stay",
            "Current node has no completion gate and no material progress yet.",
            current_id=current_id,
            roadmap=roadmap,
        )
    missing = [
        item.get("message") or item.get("condition_id", "")
        for item in completion.condition_results
        if not item.get("passed") and item.get("tier") != "rubric_only"
    ]
    if completion.decision == "human_interrupt":
        return _decision(
            "ask_user",
            completion.reason or "Human confirmation is required before completing this node.",
            current_id=current_id,
            roadmap=roadmap,
            missing=missing,
        )
    if not completion.can_enter:
        return _decision(
            "stay",
            completion.reason or "Current node completion gate has not passed.",
            current_id=current_id,
            roadmap=roadmap,
            missing=missing,
        )

    candidates = _next_candidates(evaluator, snap, current_id)
    if not candidates:
        return _decision(
            "complete",
            "Current node completion gate passed and no next node is configured.",
            current_id=current_id,
            roadmap=roadmap,
        )

    selected = _select_next_candidate(candidates)
    return _decision(
        "advance",
        f"Current node completion gate passed; next node candidate is {selected['node_id']}.",
        current_id=current_id,
        target_node_id=selected["node_id"],
        roadmap=roadmap,
        candidates=candidates,
    )


def _decision(status: str, reason: str, **extra: Any) -> dict[str, Any]:
    return {
        "status": status,
        "reason": reason,
        **extra,
    }


def _roadmap(spec, current_id: str) -> dict[str, Any]:
    nodes = list(getattr(spec, "nodes", []) or [])
    ids = [node.node_id for node in nodes]
    current_index = ids.index(current_id) if current_id in ids else -1
    current = spec.node(current_id)
    next_ids = list(getattr(current, "next_nodes", []) or []) if current else []
    if not next_ids and current:
        next_ids = [node.node_id for node in spec.reachable_from(current_id) if node.node_id != current_id]
    return {
        "current_index": current_index + 1 if current_index >= 0 else 0,
        "total_nodes": len(nodes),
        "current_node_id": current_id,
        "next_node_ids": next_ids[:5],
        "remaining_node_ids": ids[current_index + 1: current_index + 6] if current_index >= 0 else ids[:5],
    }


def _next_candidates(evaluator: GateEvaluator, snap: Snapshot, current_id: str) -> list[dict[str, Any]]:
    spec = evaluator.spec
    if spec is None:
        return []
    current = spec.node(current_id)
    candidate_nodes = spec.reachable_from(current_id)
    if current and current.next_nodes:
        ordered = []
        for node_id in current.next_nodes:
            node = spec.node(node_id)
            if node is not None:
                ordered.append(node)
        candidate_nodes = ordered
    out: list[dict[str, Any]] = []
    for node in candidate_nodes:
        if node.node_id == current_id:
            continue
        gate = evaluator.evaluate_enter(snap, node.node_id)
        out.append({
            "node_id": node.node_id,
            "title": node.title,
            "purpose": node.purpose,
            "decision": gate.decision,
            "can_enter": gate.can_enter,
            "reason": gate.reason,
        })
    return out[:5]


def _select_next_candidate(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    for item in candidates:
        if item.get("can_enter"):
            return item
    return candidates[0]


def _blocking_runtime_triggers(snap: Snapshot) -> list[dict[str, Any]]:
    out = []
    for item in getattr(snap, "triggers", []) or []:
        if getattr(item, "status", "") != "open":
            continue
        if getattr(item, "severity", "") not in {"blocking", "error", "high"}:
            continue
        out.append({
            "trigger_id": item.trigger_id,
            "trigger_type": item.trigger_type,
            "attempt_id": item.attempt_id,
            "summary": item.summary,
            "severity": item.severity,
        })
    return out


def _has_open_interrupt(snap: Snapshot) -> bool:
    return any(getattr(item, "status", "") == "open" for item in getattr(snap, "interrupts", []) or [])


def _node_has_material_progress(snap: Snapshot, node_id: str) -> bool:
    attempts = [
        item for item in getattr(snap, "attempts", []) or []
        if getattr(item, "analysis_node_id", "") == node_id
        and getattr(item, "branch_id", "") == getattr(snap, "active_branch", "main")
    ]
    attempt_ids = {getattr(item, "attempt_id", "") for item in attempts}
    if any(getattr(item, "status", "") == "succeeded" for item in attempts):
        return True
    if any(getattr(obs, "attempt_id", "") in attempt_ids for obs in getattr(snap, "observations", []) or []):
        return True
    if any(getattr(art, "attempt_id", "") in attempt_ids for art in getattr(snap, "artifacts", []) or []):
        return True
    return False
