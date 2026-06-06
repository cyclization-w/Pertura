"""Pure replay reducer: events → Snapshot. Also supports incremental replay."""

from __future__ import annotations

from pertura.models import (
    Event, Snapshot, Budget, Branch, Attempt, Outcome, Artifact, Observation,
    ReviewTrigger, Finding, Goal, Conclusion, AssistantResponse, Intervention,
    Interrupt, ApprovalRequest, BehaviorRun, NodeVisit, GateEvaluation,
    ReviewDecision, ToolCall, RuntimeJob,
)
from pertura.models.proposals import PatchProposal


def reduce(events: list[Event]) -> Snapshot:
    if not events:
        raise ValueError("Empty event log")
    first = events[0]
    cfg = first.payload.get("config", {})
    snap = Snapshot(
        run_id=cfg.get("run_id", ""), workspace=cfg.get("workspace", ""),
        goal=cfg.get("goal", ""), domain=cfg.get("domain", ""),
        protocol=cfg.get("protocol", ""),
        budget=Budget(**cfg.get("budget", {})),
        branches=[Branch(branch_id="main", title="Main", reason="main")],
        capabilities=cfg.get("capabilities", []),
        analysis_spec=cfg.get("analysis_spec", {}) or {},
        active_node_id=cfg.get("active_node_id", ""),
        design=cfg.get("design", {}) or {},
        design_meta=cfg.get("design_meta", {}) or {},
    )
    for e in events:
        _apply(snap, e)
    return snap


def reduce_incremental(snap: Snapshot, new_events: list[Event]) -> Snapshot:
    """Apply only new events to an existing snapshot, avoiding full replay."""
    for e in new_events:
        _apply(snap, e)
    return snap


def _apply(snap: Snapshot, e: Event):
    p = e.payload
    if e.event_type == "run_started":
        snap.phase = "planning"
    elif e.event_type == "attempt_planned":
        a = Attempt(**_with_event_timestamp(p["attempt"], e))
        _upsert(snap.attempts, "attempt_id", a)
        snap.active_attempt = a.attempt_id
        if a.analysis_node_id:
            snap.active_node_id = a.analysis_node_id
    elif e.event_type == "outcome_recorded":
        o = Outcome(**p["outcome"])
        _upsert(snap.outcomes, "outcome_id", o)
        for a in snap.attempts:
            if a.attempt_id == o.attempt_id:
                a.status = "failed" if o.status == "error" else "succeeded" if o.status == "success" else o.status
                break
        snap.phase = "reviewing"
    elif e.event_type == "attempt_stopped":
        for a in snap.attempts:
            if a.attempt_id == p.get("attempt_id"):
                a.status = "stopped"
                break
        if snap.active_attempt == p.get("attempt_id"):
            snap.active_attempt = ""
        snap.phase = "paused"
    elif e.event_type == "artifact_registered":
        _upsert(snap.artifacts, "artifact_id", Artifact(**p["artifact"]))
    elif e.event_type == "observation_registered":
        _upsert(snap.observations, "observation_id", Observation(**_with_event_timestamp(p["observation"], e)))
    elif e.event_type == "trigger_opened":
        _upsert(snap.triggers, "trigger_id", ReviewTrigger(**p["trigger"]))
        snap.phase = "diagnosing"
    elif e.event_type == "trigger_resolved":
        for t in snap.triggers:
            if t.trigger_id == p.get("trigger_id"):
                t.status = "resolved"
                break
        snap.phase = "planning"
    elif e.event_type == "finding_recorded":
        _upsert(snap.findings, "finding_id", Finding(**p["finding"]))
    elif e.event_type == "branch_opened":
        _upsert(snap.branches, "branch_id", Branch(**p["branch"]))
        snap.active_branch = p["branch"]["branch_id"]
        if p["branch"].get("anchor_node_id"):
            snap.active_node_id = p["branch"]["anchor_node_id"]
    elif e.event_type == "branch_stopped":
        for b in snap.branches:
            if b.branch_id == p.get("branch_id"):
                b.status = "stopped"
                b.summary = p.get("summary", b.summary)
                b.conclusion = p.get("conclusion", b.conclusion)
                b.evidence_ids = p.get("evidence_ids", b.evidence_ids)
                if snap.active_branch == b.branch_id:
                    snap.active_branch = b.parent_id or "main"
                break
    elif e.event_type == "branch_activated":
        snap.active_branch = p.get("branch_id", snap.active_branch)
    elif e.event_type == "review_decision_recorded":
        _upsert(snap.review_decisions, "review_id", ReviewDecision(
            review_id=p.get("review_id", f"rev_{p.get('attempt_id', 'unknown')}"),
            attempt_id=p.get("attempt_id", ""),
            action=p.get("decision", p.get("action", "")),
            assessment_status=p.get("assessment_status", ""),
            assessment_summary=p.get("assessment_summary", ""),
            reason=p.get("reason", ""),
            evidence_ids=p.get("evidence_ids", []),
        ))
    elif e.event_type == "goal_recorded":
        _upsert(snap.goals, "goal_id", Goal(**p["goal"]))
    elif e.event_type == "conclusion_recorded":
        _upsert(snap.conclusions, "conclusion_id", Conclusion(**p["conclusion"]))
    elif e.event_type == "intervention_planned":
        _upsert(snap.interventions, "intervention_id", Intervention(**p["intervention"]))
        snap.phase = "planning_intervention"
    elif e.event_type == "intervention_applied":
        for i in snap.interventions:
            if i.intervention_id == p.get("intervention_id"):
                i.status = "applied"; break
    elif e.event_type == "interrupt_opened":
        _upsert(snap.interrupts, "interrupt_id", Interrupt(**p["interrupt"]))
        snap.phase = "waiting_for_human"
    elif e.event_type == "interrupt_resolved":
        for i in snap.interrupts:
            if i.interrupt_id == p.get("interrupt_id"):
                i.status = "resolved"; break
        snap.phase = "planning"
    elif e.event_type == "approval_requested":
        _upsert(snap.approvals, "approval_id", ApprovalRequest(**p["approval"]))
        snap.phase = "waiting_for_approval"
    elif e.event_type == "approval_decided":
        for a in snap.approvals:
            if a.approval_id == p.get("approval_id"):
                a.status = "resolved"
                a.decision = p.get("decision", "")
                a.resolved_by = p.get("resolved_by", "")
                break
        snap.phase = "planning"
    elif e.event_type == "behavior_started":
        _upsert(snap.behavior_runs, "behavior_run_id", BehaviorRun(**p["behavior_run"]))
    elif e.event_type == "behavior_completed":
        for run in snap.behavior_runs:
            if run.behavior_run_id == p.get("behavior_run_id"):
                run.status = "completed"
                run.output_event_ids = p.get("output_event_ids", [])
                run.output_count = p.get("output_count", 0)
                break
    elif e.event_type == "behavior_failed":
        for run in snap.behavior_runs:
            if run.behavior_run_id == p.get("behavior_run_id"):
                run.status = "failed"
                run.error = p.get("error", "")
                break
    elif e.event_type == "analysis_spec_loaded":
        snap.analysis_spec = p.get("analysis_spec", {})
    elif e.event_type == "capabilities_loaded":
        snap.capabilities = p.get("capabilities", snap.capabilities)
    elif e.event_type == "capability_toggled":
        cap_id = p.get("capability_id", "")
        enabled = bool(p.get("enabled", True))
        disabled = set(snap.disabled_capabilities)
        if cap_id:
            if enabled:
                disabled.discard(cap_id)
            else:
                disabled.add(cap_id)
        snap.disabled_capabilities = sorted(disabled)
    elif e.event_type == "gate_evaluated":
        _upsert(snap.gate_evaluations, "evaluation_id", GateEvaluation(
            **_with_event_timestamp(p["gate_evaluation"], e)
        ))
    elif e.event_type == "node_entered":
        node_id = p.get("node_id", "")
        branch_id = p.get("branch_id", snap.active_branch)
        snap.active_node_id = node_id or snap.active_node_id
        _upsert(snap.node_visits, "visit_id", NodeVisit(
            visit_id=p.get("visit_id", f"visit_{node_id}_{branch_id}"),
            node_id=node_id,
            branch_id=branch_id,
            status="active",
            reason=p.get("reason", ""),
            entered_at=e.timestamp,
        ))
        snap.phase = "planning"
    elif e.event_type == "node_transition_blocked":
        node_id = p.get("target_node_id", "")
        _upsert(snap.node_visits, "visit_id", NodeVisit(
            visit_id=p.get("visit_id", f"visit_blocked_{node_id}_{len(snap.node_visits)}"),
            node_id=node_id,
            branch_id=p.get("branch_id", snap.active_branch),
            status="blocked",
            reason=p.get("reason", ""),
            entered_at=e.timestamp,
        ))
        snap.phase = "waiting_for_human" if p.get("decision") == "human_interrupt" else "diagnosing"
    elif e.event_type == "node_skipped":
        node_id = p.get("node_id", "")
        _upsert(snap.node_visits, "visit_id", NodeVisit(
            visit_id=p.get("visit_id", f"visit_skip_{node_id}_{len(snap.node_visits)}"),
            node_id=node_id,
            branch_id=p.get("branch_id", snap.active_branch),
            status="skipped",
            reason=p.get("reason", ""),
            entered_at=e.timestamp,
            completed_at=e.timestamp,
        ))
    elif e.event_type == "node_completed":
        node_id = p.get("node_id", snap.active_node_id)
        branch_id = p.get("branch_id", snap.active_branch)
        for visit in reversed(snap.node_visits):
            if visit.node_id == node_id and visit.branch_id == branch_id and visit.status == "active":
                visit.status = "completed"
                visit.completed_at = e.timestamp
                break
    elif e.event_type == "design_updated":
        design = p.get("design", {}) or {}
        snap.design.update(design)
        source = p.get("source", "") or p.get("design_source", "") or "user_confirmed"
        confidence = p.get("confidence", "") or "unspecified"
        meta = p.get("design_meta", {}) or {}
        for field in design:
            snap.design_meta[field] = {
                "source": meta.get(field, {}).get("source") or source,
                "confidence": meta.get(field, {}).get("confidence") or confidence,
                "event_id": e.event_id,
                "actor": e.actor,
                "reason": p.get("reason", ""),
            }
    elif e.event_type == "assistant_response_recorded":
        _upsert(snap.assistant_responses, "response_id", AssistantResponse(**p["response"]))
    elif e.event_type == "tool_call_recorded":
        _upsert(snap.tool_calls, "tool_call_id", ToolCall(**{
            "tool_call_id": p.get("tool_call_id", ""),
            "tool_name": p.get("tool_name", ""),
            "arguments": p.get("arguments", {}),
            "result_summary": p.get("result_summary", ""),
            "attempt_id": p.get("attempt_id", ""),
            "branch_id": p.get("branch_id", snap.active_branch),
        }))
    elif e.event_type == "job_submitted":
        _upsert(snap.jobs, "job_id", RuntimeJob(**p["job"]))
    elif e.event_type == "job_completed":
        for job in snap.jobs:
            if job.job_id == p.get("job_id"):
                job.status = p.get("status", job.status)
                job.finished_at = p.get("finished_at", job.finished_at)
                job.result = p.get("result", job.result)
                if p.get("log_path"):
                    job.log_path = p.get("log_path", job.log_path)
                if p.get("manifest_path"):
                    job.manifest_path = p.get("manifest_path", job.manifest_path)
                break
    elif e.event_type == "patch_proposed":
        _upsert(snap.patch_proposals, "patch_id", PatchProposal(**p["patch"]))
    elif e.event_type == "patch_applied":
        for patch in snap.patch_proposals:
            if _patch_get(patch, "patch_id") == p.get("patch_id"):
                _patch_set(patch, "status", "applied")
                _patch_set(patch, "applied_event_ids", p.get("event_ids", []))
                break
    elif e.event_type == "patch_rejected":
        for patch in snap.patch_proposals:
            if _patch_get(patch, "patch_id") == p.get("patch_id"):
                _patch_set(patch, "status", "rejected")
                _patch_set(patch, "rejection_reason", p.get("reason", ""))
                break
    elif e.event_type == "run_paused":
        snap.phase = "paused"
    elif e.event_type == "run_resumed":
        snap.phase = "planning"
    elif e.event_type == "run_complete":
        snap.phase = "complete"


def _upsert(items, key, value):
    for i, item in enumerate(items):
        item_value = item.get(key) if isinstance(item, dict) else getattr(item, key)
        new_value = value.get(key) if isinstance(value, dict) else getattr(value, key)
        if item_value == new_value:
            items[i] = value; return
    items.append(value)


def _with_event_timestamp(payload: dict, event: Event) -> dict:
    if "created_at" in payload:
        return payload
    enriched = dict(payload)
    enriched["created_at"] = event.timestamp
    return enriched


def _patch_get(patch, key):
    if isinstance(patch, dict):
        return patch.get(key)
    return getattr(patch, key)


def _patch_set(patch, key, value):
    if isinstance(patch, dict):
        patch[key] = value
    else:
        setattr(patch, key, value)
