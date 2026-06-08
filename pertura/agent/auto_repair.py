"""Audited execution-level auto repair."""

from __future__ import annotations

import re
from uuid import uuid4

from pertura.models import Interrupt, PatchProposal, _model_dump


_FIXABLE_ERRORS = {
    "KeyError",
    "NameError",
    "ImportError",
    "ModuleNotFoundError",
    "AttributeError",
    "TypeError",
    "ValueError",
}


def maybe_auto_repair(wb, attempt, result: dict, obs_count: int) -> dict:
    """Try a conservative code repair before returning to the main tool loop."""
    eligible, reason = _eligible(wb, attempt, result, obs_count)
    if not eligible:
        return {"status": "skipped", "reason": reason}

    error_text = (result.get("stderr") or result.get("partial_stderr") or "")[-4000:]
    error_type = _error_type(error_text)
    code = _attempt_code(attempt)
    if not code or not error_text:
        return {"status": "skipped", "reason": "missing_code_or_error"}

    try:
        repair = _request_repair(wb.provider, code, error_text, attempt, result)
    except Exception as exc:
        return {"status": "skipped", "reason": f"repair_llm_unavailable:{exc}"}

    fixed_code = str(repair.get("fixed_code") or "").strip()
    confidence = float(repair.get("confidence") or 0)
    change_magnitude = str(repair.get("change_magnitude") or "large").lower()
    risk_level = str(repair.get("risk_level") or "high").lower()
    should_retry = bool(repair.get("should_retry"))
    rationale = str(repair.get("rationale") or repair.get("reasoning") or "Repair proposed")

    if not fixed_code:
        return {"status": "skipped", "reason": "no_fixed_code"}

    patch_id = f"patch_repair_{uuid4().hex[:12]}"
    patch = PatchProposal(
        patch_id=patch_id,
        patch_type="attempt_retry",
        proposed_by="auto_repair",
        rationale=rationale,
        payload={
            "parent_attempt_id": attempt.attempt_id,
            "error_type": error_type,
            "confidence": confidence,
            "change_magnitude": change_magnitude,
            "risk_level": risk_level,
            "fixed_code": fixed_code,
        },
    )
    wb._emit("patch_proposed", {"patch": _model_dump(patch)})

    if should_retry and confidence >= 0.75 and change_magnitude == "small" and risk_level == "low":
        from pertura.agent.gated_dispatch import gated_dispatch
        snap = wb._store.read_snapshot()
        assessment = {
            "status": "auto_repair",
            "summary": rationale[:200],
        }
        decision = {
            "reason": rationale,
            "capability_ids": list(getattr(attempt, "capability_ids", []) or []),
            "parent_attempt_id": attempt.attempt_id,
            "repair_patch_id": patch_id,
        }
        action = gated_dispatch(wb, "retry", fixed_code, assessment, decision, snap, parent_attempt=attempt)
        wb._emit("patch_applied", {"patch_id": patch_id, "event_ids": []})
        return {"status": "applied", "action": action, "patch_id": patch_id}

    wb._emit("interrupt_opened", {"interrupt": _model_dump(Interrupt(
        interrupt_id=f"irq_{uuid4().hex[:12]}",
        source="auto_repair",
        trigger_id=attempt.attempt_id,
        question=(
            "A code repair was proposed but needs review before retrying. "
            f"{rationale}"
        ),
        options=["review repair", "continue without repair"],
        default_action="review_repair",
    ))})
    return {"status": "needs_review", "patch_id": patch_id}


def _eligible(wb, attempt, result: dict, obs_count: int) -> tuple[bool, str]:
    if not result or result.get("returncode") == 0:
        return False, "not_failed"
    if result.get("safety_blocked"):
        return False, "safety_blocked"
    if result.get("timed_out") and result.get("timed_out_at") in {"hard", "heartbeat"}:
        return False, "hard_timeout"
    if obs_count:
        return False, "observations_registered"
    if not _attempt_code(attempt):
        return False, "missing_code"
    snap = wb._store.read_snapshot() if wb._store else None
    if snap is None:
        return False, "missing_snapshot"
    if sum(1 for item in getattr(snap, "patch_proposals", []) if item.patch_type == "attempt_retry") >= 3:
        return False, "repair_budget_exhausted"
    if sum(
        1
        for item in getattr(snap, "patch_proposals", [])
        if item.patch_type == "attempt_retry"
        and (item.payload or {}).get("parent_attempt_id") == attempt.attempt_id
    ) >= 1:
        return False, "attempt_repair_limit"
    error_text = result.get("stderr") or result.get("partial_stderr") or ""
    error_type = _error_type(error_text)
    if error_type not in _FIXABLE_ERRORS:
        return False, f"unfixable_error_type:{error_type or 'unknown'}"
    return True, ""


def _request_repair(provider: str, code: str, error_text: str, attempt, result: dict) -> dict:
    from pertura.planner import _call_llm
    system = "You repair small Python notebook cells for an audited scientific analysis runtime."
    user = f"""Repair this failed Python cell only if the fix is small and low risk.

Attempt: {getattr(attempt, 'attempt_id', '')}
Title: {getattr(attempt, 'title', '')}
Capability ids: {', '.join(getattr(attempt, 'capability_ids', []) or [])}
Returncode: {result.get('returncode')}

Failed code:
```python
{code}
```

Error:
{error_text}

Rules:
- Return fixed complete code, not a diff.
- Mark change_magnitude large unless the fix is a local import/name/API/key/argument correction.
- risk_level must be low only when the code intent is unchanged.
- Do not invent new analysis goals.
"""
    schema = {
        "type": "object",
        "properties": {
            "should_retry": {"type": "boolean"},
            "change_magnitude": {"type": "string", "enum": ["small", "large"]},
            "risk_level": {"type": "string", "enum": ["low", "medium", "high"]},
            "confidence": {"type": "number"},
            "rationale": {"type": "string"},
            "fixed_code": {"type": "string"},
        },
        "required": [
            "should_retry",
            "change_magnitude",
            "risk_level",
            "confidence",
            "rationale",
            "fixed_code",
        ],
        "additionalProperties": False,
    }
    return _call_llm(system, user, schema, provider=provider or "openai")


def _attempt_code(attempt) -> str:
    cells = list(getattr(attempt, "notebook_cells", []) or [])
    if not cells:
        return ""
    first = cells[0]
    if isinstance(first, dict):
        return str(first.get("source") or "")
    return str(getattr(first, "source", "") or "")


def _error_type(text: str) -> str:
    for item in sorted(_FIXABLE_ERRORS):
        if re.search(rf"\b{re.escape(item)}\b", text or ""):
            return item
    return ""
