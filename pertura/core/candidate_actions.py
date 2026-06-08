"""Product-facing candidate action projection.

Candidate actions are not durable state. They are a small control surface for
the GUI and console router, compiled from the current snapshot and product
projections.
"""

from __future__ import annotations

from typing import Any


def compile_candidate_actions(
    snap,
    *,
    execution_state: dict[str, Any] | None = None,
    work_order: dict[str, Any] | None = None,
    jobs: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Return product actions the user can take now."""
    if snap is None:
        active_job = next(
            (item for item in (jobs or []) if item.get("status") in {"queued", "running"}),
            {},
        )
        if active_job:
            return [
                _action(
                    "pause",
                    "pause",
                    "Pause",
                    "Stop the active agent job while the run is starting.",
                    primary=True,
                    endpoint="/api/agent/pause",
                    method="POST",
                ),
                _action(
                    "open_evidence",
                    "open_evidence",
                    "Inspect live status",
                    active_job.get("job_id") or "Agent job is queued.",
                    endpoint="/api/workbench-view",
                    method="GET",
                ),
            ]
        return [_action(
            "start_analysis",
            "start_analysis",
            "Start analysis",
            "Choose a workspace and describe the analysis goal.",
            primary=True,
            endpoint="/api/console/turn",
            method="POST",
            payload={},
        )]

    execution_state = execution_state or {}
    work_order = work_order or {}
    jobs = list(jobs or [])
    mode = str(execution_state.get("mode") or _mode_from_snapshot(snap, jobs))
    actions: list[dict[str, Any]] = []

    active_job = next(
        (item for item in jobs if item.get("status") in {"queued", "running"}),
        {},
    )
    question = execution_state.get("question") or {}
    if question:
        actions.append(_question_action(question))
        actions.append(_action(
            "pause",
            "pause",
            "Pause run",
            "Stop the active agent job while the question is open.",
            endpoint="/api/agent/pause",
            method="POST",
        ))
        return _dedupe_actions(actions)

    if mode == "running" or active_job:
        actions.extend([
            _action(
                "pause",
                "pause",
                "Pause",
                "Stop the active agent job without losing the run state.",
                primary=True,
                endpoint="/api/agent/pause",
                method="POST",
            ),
            _action(
                "open_evidence",
                "open_evidence",
                "Inspect evidence",
                "Open the current evidence and event trail while the run continues.",
                endpoint="/api/workbench-view",
                method="GET",
            ),
        ])
        return _dedupe_actions(actions)

    if mode == "complete":
        actions.extend([
            _action(
                "generate_report",
                "generate_report",
                "Generate report",
                "Compile conclusions, evidence, artifacts, and limitations.",
                primary=True,
                endpoint="/api/report/generate",
                method="POST",
            ),
            _action(
                "open_evidence",
                "open_evidence",
                "Review evidence",
                "Inspect provenance before exporting or sharing the result.",
                endpoint="/api/workbench-view",
                method="GET",
            ),
        ])
        return _dedupe_actions(actions)

    repair_issues = [
        item for item in (execution_state.get("issues") or [])
        if item.get("kind") in {"repair_issue", "audit_issue"}
    ]
    if mode == "repairing" or repair_issues:
        issue = repair_issues[0] if repair_issues else {}
        actions.append(_action(
            "review_repair",
            "review_repair",
            "Review repair",
            issue.get("summary") or "Inspect the blocked repair before continuing.",
            primary=True,
            risk_level="medium",
            evidence_refs=issue.get("affected_ids") or [],
            endpoint="/api/workbench-view",
            method="GET",
        ))

    primary = not any(item.get("primary") for item in actions)
    if mode in {"paused", "ready", "planning", "diagnosing"}:
        actions.append(_action(
            "continue_analysis",
            "continue_analysis",
            "Continue analysis",
            _continue_description(work_order),
            primary=primary,
            endpoint="/api/console/turn",
            method="POST",
            payload={"action_id": "continue_analysis"},
        ))

    selected = work_order.get("selected_capability") or {}
    if selected:
        cap_id = selected.get("id") or selected.get("capability_id") or ""
        if selected.get("ready") is False or selected.get("missing_inputs"):
            actions.append(_action(
                f"review_capability:{cap_id}",
                "review_repair",
                "Resolve capability inputs",
                selected.get("next_repair") or "Review the selected capability before execution.",
                risk_level="low",
                capability_id=cap_id,
                endpoint="/api/workbench-view",
                method="GET",
            ))
        elif cap_id:
            actions.append(_action(
                f"inspect_capability:{cap_id}",
                "inspect_artifact",
                "Inspect capability",
                selected.get("description") or f"Review {cap_id} before the next run step.",
                risk_level="low",
                capability_id=cap_id,
                endpoint="/api/capabilities/view",
                method="GET",
            ))

    if getattr(snap, "artifacts", None):
        latest = snap.artifacts[-1]
        actions.append(_action(
            f"inspect_artifact:{latest.artifact_id}",
            "inspect_artifact",
            "Inspect latest artifact",
            latest.summary or latest.kind or "Open the most recent artifact.",
            risk_level="low",
            evidence_refs=[latest.artifact_id],
            endpoint=f"/api/artifacts/{latest.artifact_id}/preview",
            method="GET",
        ))

    actions.append(_action(
        "open_evidence",
        "open_evidence",
        "Open evidence trail",
        "Review attempts, observations, artifacts, and audit status.",
        risk_level="low",
        endpoint="/api/workbench-view",
        method="GET",
    ))
    return _dedupe_actions(actions)


def _question_action(question: dict[str, Any]) -> dict[str, Any]:
    return _action(
        "answer_question",
        "answer_question",
        "Answer question",
        question.get("question") or question.get("summary") or "Human input is needed.",
        primary=True,
        risk_level="low",
        endpoint=question.get("answer_endpoint") or "",
        method="POST",
        interrupt_id=question.get("issue_id") or "",
        options=question.get("options") or [],
        fields=(question.get("form") or {}).get("fields") or [],
        evidence_refs=question.get("affected_ids") or [],
    )


def _mode_from_snapshot(snap, jobs: list[dict[str, Any]]) -> str:
    if any(item.get("status") in {"queued", "running"} for item in jobs):
        return "running"
    if getattr(snap, "phase", "") == "complete":
        return "complete"
    if any(getattr(item, "status", "") == "open" for item in getattr(snap, "interrupts", []) or []):
        return "needs_user"
    if getattr(snap, "phase", "") == "paused":
        return "paused"
    return "ready"


def _continue_description(work_order: dict[str, Any]) -> str:
    recommended = work_order.get("recommended_actions") or []
    if recommended:
        return f"Let the agent take the next audited step: {recommended[0]}"
    node = work_order.get("active_node") or {}
    title = node.get("title") or node.get("id")
    if title:
        return f"Continue the current audited analysis step: {title}."
    return "Continue from the current run state."


def _action(
    action_id: str,
    kind: str,
    label: str,
    description: str,
    *,
    status: str = "available",
    primary: bool = False,
    risk_level: str = "low",
    endpoint: str = "",
    method: str = "POST",
    payload: dict[str, Any] | None = None,
    interrupt_id: str = "",
    options: list[str] | None = None,
    fields: list[dict[str, Any]] | None = None,
    capability_id: str = "",
    evidence_refs: list[str] | None = None,
    disabled_reason: str = "",
) -> dict[str, Any]:
    return {
        "id": action_id,
        "kind": kind,
        "label": label,
        "description": description,
        "status": status,
        "primary": primary,
        "risk_level": risk_level,
        "endpoint": endpoint,
        "method": method,
        "payload": payload or {},
        "interrupt_id": interrupt_id,
        "options": list(options or []),
        "fields": list(fields or []),
        "capability_id": capability_id,
        "evidence_refs": list(evidence_refs or []),
        "disabled_reason": disabled_reason,
    }


def _dedupe_actions(actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen = set()
    out = []
    for action in actions:
        action_id = action.get("id")
        if not action_id or action_id in seen:
            continue
        seen.add(action_id)
        out.append(action)
    if out and not any(item.get("primary") for item in out):
        out[0]["primary"] = True
    return out[:8]
