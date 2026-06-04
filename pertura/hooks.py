"""Hook layer — lifecycle checks extracted from _execute_attempt.

Each hook runs deterministically. LLM judgment is separate.
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4


def pre_execute(code: str, workspace: str, artifacts_dir: str) -> list[dict]:
    """Run before code execution. Returns list of events to emit."""
    events = []
    from pertura.kernel.safety import check as safety_check
    violations = safety_check(code, workspace=workspace, artifacts_dir=artifacts_dir)
    if violations:
        events.append(("safety_violation_recorded", {
            "violations": violations,
            "code_preview": code[:200],
            "severity": "blocking",
        }, "hook"))
    return events


def pre_conclusion(support_ids: list[str], snap) -> list[dict]:
    """Validate conclusion before committing. Checks evidence chain."""
    events = []
    if not support_ids:
        events.append(("finding_recorded", {
            "finding": {
                "finding_id": f"fnd_{uuid4().hex[:12]}",
                "finding_type": "missing_context",
                "severity": "warning",
                "suggested_action": "downgrade",
                "summary": "Conclusion has no supporting evidence ids.",
            }
        }, "hook"))
    # Check for unresolved blocking triggers
    blocking = [t for t in snap.triggers if t.severity == "blocking" and t.status == "open"] if snap else []
    if blocking:
        events.append(("finding_recorded", {
            "finding": {
                "finding_id": f"fnd_{uuid4().hex[:12]}",
                "finding_type": "suspicious_choice",
                "severity": "blocking",
                "suggested_action": "downgrade",
                "summary": f"Conclusion has {len(blocking)} unresolved blocking trigger(s).",
            }
        }, "hook"))
    return events


def post_tool_call(tool_name: str, args: dict, result_summary: str, attempt_id: str = "") -> list[dict]:
    """Record tool call provenance."""
    events = [("tool_call_recorded", {
        "tool_call_id": f"tc_{uuid4().hex[:10]}",
        "tool_name": tool_name,
        "arguments": args,
        "result_summary": result_summary[:500],
        "attempt_id": attempt_id,
    }, "tool")]
    return events


def post_capability_contract(attempt, snap, result: dict | None = None) -> list[dict]:
    """Check whether a successful attempt satisfied its capability contract.

    This is intentionally advisory. It records warnings rather than blocking
    execution, because the LLM may legitimately produce a limitation, ask a
    user question, or pivot branch instead of the expected artifact.
    """
    result = result or {}
    if result.get("returncode") not in (None, 0):
        return []

    from pertura.capabilities import CapabilityRegistry
    from pertura.core.capability_contracts import capability_output_gaps

    registry = CapabilityRegistry(snap.capabilities)
    attempt_observations = [
        obs for obs in snap.observations
        if obs.attempt_id == attempt.attempt_id
    ]
    attempt_artifacts = [
        artifact for artifact in snap.artifacts
        if artifact.attempt_id == attempt.attempt_id
    ]
    events = []
    for capability_id in attempt.capability_ids:
        cap = registry.get(capability_id)
        if cap is None:
            continue
        gaps = capability_output_gaps(cap, attempt_observations, attempt_artifacts)
        missing_observations = gaps["missing_observations"]
        missing_artifacts = gaps["missing_artifacts"]
        if not missing_observations and not missing_artifacts:
            continue
        missing_parts = []
        if missing_observations:
            missing_parts.append(f"observations={missing_observations}")
        if missing_artifacts:
            missing_parts.append(f"artifacts={missing_artifacts}")
        events.append(("finding_recorded", {
            "finding": {
                "finding_id": f"fnd_{uuid4().hex[:12]}",
                "attempt_id": attempt.attempt_id,
                "finding_type": "capability_contract_missing_output",
                "severity": "warning",
                "suggested_action": "retry",
                "summary": (
                    f"Capability '{capability_id}' did not register expected "
                    f"{'; '.join(missing_parts)}."
                ),
                "affected_ids": [attempt.attempt_id],
            }
        }, "hook"))
    return events
