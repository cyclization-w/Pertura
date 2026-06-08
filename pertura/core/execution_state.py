"""Product-facing execution-state projection.

This module intentionally does not introduce durable state. It compiles the
current snapshot into a small UI/API surface while preserving the lower-level
event vocabulary used by replay, audit, and trace.
"""

from __future__ import annotations

from typing import Any

from pertura.models import Snapshot, _model_dump


def compile_runtime_issues(snap: Snapshot | None, *, limit: int = 20) -> list[dict[str, Any]]:
    """Map internal review entities into a small RuntimeIssue projection."""
    if snap is None:
        return []

    issues: list[dict[str, Any]] = []
    for item in getattr(snap, "interrupts", []) or []:
        if getattr(item, "status", "") != "open":
            continue
        fields = _interrupt_form_fields(snap, item)
        issues.append({
            "issue_id": item.interrupt_id,
            "kind": "question",
            "source_event_type": "interrupt_opened",
            "source": item.source,
            "severity": "blocking",
            "status": item.status,
            "summary": item.question,
            "question": item.question,
            "affected_ids": [item.trigger_id] if item.trigger_id else [],
            "suggested_action": item.default_action or "answer",
            "answer_endpoint": f"/api/answer/{item.interrupt_id}",
            "options": list(getattr(item, "options", []) or []),
            "form": {"fields": fields},
        })

    for item in getattr(snap, "approvals", []) or []:
        if getattr(item, "status", "") != "open":
            continue
        issues.append({
            "issue_id": item.approval_id,
            "kind": "approval_issue",
            "source_event_type": "approval_requested",
            "source": item.approval_type,
            "severity": "blocking",
            "status": item.status,
            "summary": item.reason,
            "question": item.reason,
            "affected_ids": [item.subject_id] if item.subject_id else [],
            "suggested_action": "decide_approval",
            "answer_endpoint": "",
        })

    for item in getattr(snap, "triggers", []) or []:
        if getattr(item, "status", "") != "open":
            continue
        issues.append({
            "issue_id": item.trigger_id,
            "kind": "repair_issue",
            "source_event_type": "trigger_opened",
            "source": item.trigger_type,
            "severity": item.severity,
            "status": item.status,
            "summary": item.summary,
            "question": "",
            "affected_ids": [item.attempt_id] if item.attempt_id else [],
            "suggested_action": "repair",
            "answer_endpoint": "",
        })

    actionable_findings = [
        item for item in (getattr(snap, "findings", []) or [])
        if getattr(item, "severity", "") in {"warning", "high", "blocking", "error"}
    ]
    for item in actionable_findings[-limit:]:
        suggested = getattr(item, "suggested_action", "") or "repair"
        kind = "audit_issue" if "audit" in suggested or "audit" in item.finding_type else "repair_issue"
        issues.append({
            "issue_id": item.finding_id,
            "kind": kind,
            "source_event_type": "finding_recorded",
            "source": item.finding_type,
            "severity": item.severity,
            "status": "open",
            "summary": item.summary,
            "question": "",
            "affected_ids": list(item.affected_ids or []),
            "suggested_action": suggested,
            "answer_endpoint": "",
        })

    return issues[:limit]


def compile_execution_state(
    snap: Snapshot | None,
    *,
    graph: dict | None = None,
    jobs: list[dict[str, Any]] | None = None,
    selected_node_id: str = "",
) -> dict[str, Any]:
    """Return the stable product-facing execution state for GUI/API use."""
    if snap is None:
        payload = {
            "view_type": "execution_state",
            "schema_version": "v1",
            "mode": "not_initialized",
            "run_id": "",
            "stop_reason": "",
            "current_task": {"node_id": "", "title": "No run", "purpose": ""},
            "question": {},
            "issues": [],
            "recommended_actions": ["start_analysis"],
            "visible_capabilities": [],
            "evidence_summary": {},
            "activity": {"jobs": jobs or []},
            "debug_refs": {},
        }
        from pertura.core.candidate_actions import compile_candidate_actions
        payload["candidate_actions"] = compile_candidate_actions(None, execution_state=payload, jobs=jobs or [])
        return payload

    runtime_jobs = list(jobs or [])
    issues = compile_runtime_issues(snap)
    question = next((item for item in issues if item.get("kind") == "question"), {})
    blocking_issues = [
        item for item in issues
        if item.get("kind") != "question" and item.get("severity") in {"blocking", "error", "high"}
    ]
    active_job = next(
        (item for item in runtime_jobs if item.get("status") in {"queued", "running"}),
        {},
    )
    node_id = selected_node_id or getattr(snap, "active_node_id", "") or ""
    node = _analysis_node(snap, node_id)
    mode = _execution_mode(snap, question=question, blocking_issues=blocking_issues, active_job=active_job)
    from pertura.core.node_navigation import evaluate_node_navigation
    navigation = evaluate_node_navigation(snap)
    payload = {
        "view_type": "execution_state",
        "schema_version": "v1",
        "mode": mode,
        "run_id": snap.run_id,
        "stop_reason": _stop_reason_hint(snap, mode=mode, question=question),
        "current_task": {
            "node_id": node_id,
            "title": node.get("title") or node_id or "Run",
            "purpose": node.get("purpose", ""),
            "branch_id": snap.active_branch,
            "goal": snap.goals[-1].text if snap.goals else snap.goal,
        },
        "question": question,
        "issues": issues,
        "recommended_actions": _recommended_actions(mode, question, blocking_issues, node),
        "visible_capabilities": list(node.get("allowed_capabilities") or [])[:12],
        "navigation": navigation,
        "evidence_summary": {
            "attempts": len(snap.attempts),
            "observations": len(snap.observations),
            "artifacts": len(snap.artifacts),
            "conclusions": len(snap.conclusions),
            "recent_attempts": [_compact_attempt(item) for item in snap.attempts[-5:]],
            "recent_artifacts": [_compact_artifact(item) for item in snap.artifacts[-5:]],
        },
        "activity": {
            "phase": snap.phase,
            "active_attempt": snap.active_attempt,
            "jobs": runtime_jobs[:8],
            "active_job": active_job,
        },
        "debug_refs": {
            "graph_nodes": len((graph or {}).get("nodes", [])),
            "graph_edges": len((graph or {}).get("edges", [])),
            "open_issue_ids": [item.get("issue_id", "") for item in issues if item.get("issue_id")],
        },
    }
    from pertura.core.candidate_actions import compile_candidate_actions
    payload["candidate_actions"] = compile_candidate_actions(
        snap,
        execution_state=payload,
        jobs=runtime_jobs,
    )
    return payload


def _execution_mode(snap: Snapshot, *, question: dict, blocking_issues: list[dict], active_job: dict) -> str:
    if snap.phase == "complete":
        return "complete"
    if active_job:
        return "running"
    if question:
        return "needs_user"
    if snap.phase == "paused":
        return "paused"
    if blocking_issues:
        return "repairing"
    if _active_attempt_is_running(snap):
        return "running"
    return "ready"


def _stop_reason_hint(snap: Snapshot, *, mode: str, question: dict) -> str:
    if mode == "complete":
        return "complete"
    if mode == "needs_user":
        return "missing_key" if question.get("source") == "missing_api_key" else "human_interrupt"
    if mode == "paused":
        return "cancelled"
    return ""


def _analysis_node(snap: Snapshot, node_id: str) -> dict[str, Any]:
    for item in (snap.analysis_spec or {}).get("nodes", []) or []:
        if item.get("node_id") == node_id:
            return item
    return {}


def _active_attempt_is_running(snap: Snapshot) -> bool:
    active_attempt_id = getattr(snap, "active_attempt", "") or ""
    if not active_attempt_id:
        return False
    for item in getattr(snap, "attempts", []) or []:
        if getattr(item, "attempt_id", "") == active_attempt_id:
            return getattr(item, "status", "") in {"planned", "running", "queued"}
    return True


def _recommended_actions(mode: str, question: dict, blocking_issues: list[dict], node: dict[str, Any]) -> list[str]:
    if mode == "not_initialized":
        return ["start_analysis"]
    if mode == "needs_user":
        return ["answer_question" if question else "review_question"]
    if mode == "repairing":
        actions = [item.get("suggested_action", "") for item in blocking_issues if item.get("suggested_action")]
        return _dedupe(actions) or ["repair_issue"]
    if mode == "complete":
        return ["generate_report"]
    actions = list(node.get("recommended_actions") or [])
    return actions[:5] or ["continue_analysis"]


def _compact_attempt(item) -> dict[str, Any]:
    data = _model_dump(item)
    return {
        "attempt_id": data.get("attempt_id", ""),
        "title": data.get("title", ""),
        "status": data.get("status", ""),
        "analysis_node_id": data.get("analysis_node_id", ""),
        "capability_ids": data.get("capability_ids", []),
    }


def _compact_artifact(item) -> dict[str, Any]:
    data = _model_dump(item)
    return {
        "artifact_id": data.get("artifact_id", ""),
        "kind": data.get("kind", ""),
        "summary": data.get("summary", ""),
        "path": data.get("path", ""),
    }


def _dedupe(values: list[str]) -> list[str]:
    seen = set()
    out = []
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def _interrupt_form_fields(snap: Snapshot, interrupt) -> list[dict[str, Any]]:
    try:
        from pertura.spec.design_answer import expected_fields_from_interrupt
        fields = expected_fields_from_interrupt(snap, interrupt)
    except Exception:
        fields = []
    return [
        {
            "name": field,
            "label": field.replace("_", " "),
            "type": "text",
            "required": True,
        }
        for field in fields
    ]
