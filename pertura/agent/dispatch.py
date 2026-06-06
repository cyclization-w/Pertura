"""Decision dispatch: single commit point for all state-changing actions."""

from __future__ import annotations

import json
from uuid import uuid4

from pertura.models import (
    Attempt, Branch, Conclusion, Interrupt, Finding, _model_dump,
)
from pertura.memory.compiler import compile_context


def _dispatch_decision(wb, action: str, code: str, assessment: dict,
                       decision: dict, snap, *, parent_attempt=None) -> str:
    """Controller commits decisions. Assessment = what happened. Decision = what to do.

    Actions: respond, execute_code, retry, open_branch, close_branch, ask_user, finish
    """
    d = decision  # shorthand

    if action == "update_design":
        design = d.get("design", {})
        if design:
            wb._emit("design_updated", {
                "design": design,
                "reason": d.get("reason", ""),
                "source": "llm_inferred",
                "confidence": d.get("confidence", "medium"),
            })
            return "design_updated"
        return "blocked"

    if action == "respond":
        text = d.get("response") or assessment.get("summary", "")
        wb._emit("assistant_response_recorded", {"response": {
            "response_id": f"resp_{uuid4().hex[:12]}", "text": text,
            "reason": d.get("reason", ""),
        }})
        return "responded"

    if action == "execute_code" and not code:
        text = d.get("response") or assessment.get("summary", "")
        wb._emit("assistant_response_recorded", {"response": {
            "response_id": f"resp_{uuid4().hex[:12]}", "text": text,
            "reason": d.get("reason", ""),
        }})
        return "responded"

    if action in ("execute_code", "retry", "submit_job") and code:
        parent_ids = [parent_attempt.attempt_id] if (
            parent_attempt and action == "retry") else []
        repair_count = (parent_attempt.repair_count + 1) if (
            parent_attempt and action == "retry") else 0
        parameters = _attempt_parameters(d)
        if action == "submit_job":
            parameters.update({
                "execution_kind": "job",
                "job_backend": d.get("backend", "subprocess"),
                "resources": d.get("resources", {}) or {},
                "expected_outputs": d.get("expected_outputs", []) or [],
                "expected_observations": d.get("expected_observations", []) or [],
                "manifest_path": d.get("manifest_path", ""),
            })
        a = Attempt(
            attempt_id=f"att_{uuid4().hex[:12]}", branch_id=snap.active_branch,
            analysis_node_id=snap.active_node_id,
            title=assessment.get("summary", "Analysis step")[:80],
            objective=assessment.get("summary", "")[:200],
            stage=d.get("stage") or snap.active_node_id or assessment.get("status", "inspect"),
            notebook_cells=[{"source": code, "role": "execute", "title": "Step"}],
            capability_ids=d.get("capability_ids", []),
            parameters=parameters,
            expected_artifacts=d.get("expected_outputs", []) or [],
            design_fields_used=d.get("design_fields_used", _infer_design_fields_from_code(code)),
            parent_ids=parent_ids,
            parent_intervention="retry" if action == "retry" else "",
            repair_count=repair_count,
            rationale=assessment.get("summary", ""),
        )
        wb._emit("attempt_planned", {"attempt": _model_dump(a)})
        return "planned_attempt"

    if action == "open_branch":
        new_branch_id = d.get("branch_id", f"br_{uuid4().hex[:8]}")
        wb._emit("branch_opened", {"branch": _model_dump(Branch(
            branch_id=new_branch_id,
            title=(d.get("branch_question") or
                   assessment.get("summary", "Branch"))[:60],
            parent_id=snap.active_branch,
            anchor_attempt_id=snap.active_attempt,
            anchor_node_id=snap.active_node_id,
            reason=d.get("reason", assessment.get("status", "exploration")),
            question=d.get("branch_question", assessment.get("summary", "")),
            hypothesis=d.get("branch_hypothesis", assessment.get("status", "")),
        ))})
        if code:
            a = Attempt(
                attempt_id=f"att_{uuid4().hex[:12]}", branch_id=new_branch_id,
                analysis_node_id=snap.active_node_id,
                title=assessment.get("summary", "Branch step")[:80],
                objective=assessment.get("summary", "")[:200],
                notebook_cells=[{"source": code, "role": "execute", "title": "Step"}],
                parameters=_attempt_parameters(d),
                design_fields_used=d.get("design_fields_used", _infer_design_fields_from_code(code)),
                rationale="Initial cell in new branch.",
            )
            wb._emit("attempt_planned", {"attempt": _model_dump(a)})
            return "planned_attempt"
        return "branch_opened"

    if action == "switch_branch":
        target_branch = d.get("branch_id", "")
        wb._emit("branch_activated", {"branch_id": target_branch})
        return "branch_switched"

    if action == "close_branch":
        target_branch = d.get("branch_id") or snap.active_branch
        wb._emit("branch_stopped", {
            "branch_id": target_branch,
            "summary": d.get("summary") or assessment.get("summary", ""),
            "conclusion": d.get("conclusion", assessment.get("status", "")),
            "evidence_ids": d.get("evidence_ids", []),
            "implication": d.get("implication_for_parent", ""),
        })
        return "branch_closed"

    if action == "ask_user":
        question = d.get("reason") or assessment.get("summary",
                                                     "The agent needs your input.")
        wb._emit("interrupt_opened", {"interrupt": _model_dump(Interrupt(
            interrupt_id=f"irq_{uuid4().hex[:12]}", source="deliberation",
            question=question,
        ))})
        return "waiting_for_human"

    if action in ("finish", "done"):
        from pertura.hooks import pre_conclusion as _pre_con
        snap_check = wb._store.read_snapshot()
        support_ids = [obs.observation_id
                       for obs in (snap_check.observations[-10:]
                                   if snap_check else [])]
        gate_events = _pre_con(support_ids, snap_check)
        blocking = [e for e in gate_events
                    if e[1].get("severity") == "blocking"]
        if blocking:
            wb._emit("interrupt_opened", {"interrupt": _model_dump(Interrupt(
                interrupt_id=f"irq_{uuid4().hex[:12]}",
                source="pre_conclusion",
                question=f"Cannot finish: {blocking[0][1].get('summary', 'evidence incomplete')}",
            ))})
            return "waiting_for_human"
        audit_block = _finish_audit_gate(wb, snap_check)
        if audit_block:
            return audit_block
        ctx = compile_context(snap_check)
        _generate_conclusions(wb, snap_check, ctx)
        final_audit_block = _finish_audit_gate(wb, wb._store.read_snapshot())
        if final_audit_block:
            return final_audit_block
        wb._emit("run_complete", {})
        return "complete"

    # Unknown action
    wb._emit("finding_recorded", {"finding": _model_dump(Finding(
        finding_id=f"fnd_{uuid4().hex[:12]}",
        finding_type="unknown_action", severity="warning",
        suggested_action="ask_user",
        summary=f"Unknown action: {action}",
    ))})
    return "blocked"


def _finish_audit_gate(wb, snap) -> str:
    from pertura.core.audit import audit_run
    graph = wb._store.read_graph() if getattr(wb, "_store", None) else {}
    run_dir = getattr(wb._store, "run_dir", None) if getattr(wb, "_store", None) else None
    audit = audit_run(snap, graph or {}, run_dir=run_dir)
    error_codes = [item.get("code", "") for item in audit.get("errors", [])]
    warning_codes = [item.get("code", "") for item in audit.get("warnings", [])]
    if error_codes:
        wb._emit("finding_recorded", {"finding": _model_dump(Finding(
            finding_id=f"fnd_{uuid4().hex[:12]}",
            finding_type="finish_audit_failed",
            severity="blocking",
            suggested_action="audit_run",
            summary=(
                "Cannot finish: run audit found errors "
                f"({', '.join(error_codes[:5])})."
            ),
            affected_ids=error_codes[:12],
        ))})
        wb._emit("interrupt_opened", {"interrupt": _model_dump(Interrupt(
            interrupt_id=f"irq_{uuid4().hex[:12]}",
            source="finish_audit",
            question="Cannot finish until run audit errors are resolved. Call audit_run() for details.",
            default_action="audit_run",
        ))})
        return "waiting_for_human"
    if warning_codes:
        wb._emit("finding_recorded", {"finding": _model_dump(Finding(
            finding_id=f"fnd_{uuid4().hex[:12]}",
            finding_type="finish_audit_warning",
            severity="warning",
            suggested_action="audit_run",
            summary=(
                "Run audit found warnings before finish "
                f"({', '.join(warning_codes[:5])})."
            ),
            affected_ids=warning_codes[:12],
        ))})
    return ""


def _generate_conclusions(wb, snap, ctx):
    """Generate conclusions. Falls back to rule-based when no LLM key."""
    if not snap.observations:
        return

    from pertura.hooks import pre_conclusion
    from pertura.agent.loop import _has_key
    support_ids = [obs.observation_id
                   for obs in (snap.observations[-10:] if snap else [])]
    for evt_type, payload, _actor in pre_conclusion(support_ids, snap):
        wb._emit(evt_type, payload)

    if not _has_key(wb.provider):
        grade = "inconclusive"
        if ctx and ctx.coverage:
            grades = [c.label for c in ctx.coverage
                      if c.label in ("convergent", "adequate")]
            if len(grades) >= len(ctx.coverage) * 0.7:
                grade = "supported"
            elif grades:
                grade = "tentative"
        conclusion = Conclusion(
            conclusion_id=f"con_{uuid4().hex[:12]}",
            text=(f"Run completed with {len(snap.observations)} observations "
                  f"across {len(snap.attempts)} attempts. "
                  f"{len(snap.branches)} branch(es) explored. "
                  f"Evidence grade: {grade}."),
            grade=grade, support_ids=support_ids,
        )
        wb._emit("conclusion_recorded", {"conclusion": _model_dump(conclusion)})
        return

    coverage_summary = [
        {"subject": c.subject, "methods": c.methods,
         "contradictions": c.contradictions, "label": c.label}
        for c in ctx.coverage
    ]
    memory_summary = [
        {"subject": m.subject, "signal": m.signal, "value": m.current_value}
        for m in ctx.memory if m.signal in ("agreement", "conflict")
    ]

    try:
        conclusion_text = _generate_conclusion_text(
            ctx.goal or snap.goal, coverage_summary, memory_summary)
        conclusion = Conclusion(
            conclusion_id=f"con_{uuid4().hex[:12]}",
            text=conclusion_text.get("text", ""),
            grade=conclusion_text.get("grade", "tentative"),
            support_ids=support_ids,
        )
        wb._emit("conclusion_recorded", {"conclusion": _model_dump(conclusion)})
    except Exception:
        conclusion = Conclusion(
            conclusion_id=f"con_{uuid4().hex[:12]}",
            text="LLM narrative generation failed. Evidence preserved in event log.",
            grade="inconclusive", support_ids=support_ids,
        )
        wb._emit("conclusion_recorded", {"conclusion": _model_dump(conclusion)})


def _generate_conclusion_text(goal: str, coverage: list, memory: list) -> dict:
    from pertura.planner import _call_llm
    prompt = json.dumps({
        "goal": goal, "coverage": coverage, "key_findings": memory,
    }, ensure_ascii=False)[:4000]
    schema = {
        "type": "object", "properties": {
            "text": {"type": "string"},
            "grade": {"type": "string",
                     "enum": ["robust", "supported", "tentative", "inconclusive", "negative"]},
        }, "required": ["text", "grade"], "additionalProperties": False,
    }
    return _call_llm(
        "You are a scientific reviewer. Draft a concise conclusion with a grade.",
        prompt, schema,
    )


def _infer_design_fields_from_code(code: str) -> list[str]:
    lowered = (code or "").lower()
    field_terms = {
        "control_labels": ["control", "ntc", "negative_control"],
        "guide_column": ["guide", "grna", "sgrna"],
        "target_column": ["target", "perturbation"],
        "batch_column": ["batch", "sample", "replicate"],
        "perturbation_modality": ["crispra", "crispri", "knockout", "ko"],
        "moi": ["moi"],
        "loading_strategy": ["loading", "droplet"],
    }
    return [
        field
        for field, terms in field_terms.items()
        if any(term in lowered for term in terms)
    ]


def _attempt_parameters(decision: dict) -> dict:
    params = dict(decision.get("parameters", {}) or {})
    if "expected_runtime_seconds" in decision:
        params["expected_runtime_seconds"] = decision.get("expected_runtime_seconds")
    return params
