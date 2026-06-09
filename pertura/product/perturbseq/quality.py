"""Perturb-seq quality and boost projection."""

from __future__ import annotations

from typing import Any

from pertura.models import Snapshot


def compile_quality_flags(snap: Snapshot | None, design_ledger: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    if snap is None:
        return []
    ledger = design_ledger or {}
    flags: list[dict[str, Any]] = []
    missing = (ledger.get("summary") or {}).get("blocking_missing") or []
    for field_id in missing:
        flags.append({
            "flag_id": f"missing_design:{field_id}",
            "kind": "design_missing",
            "severity": "blocking",
            "label": f"Missing {field_id.replace('_', ' ')}",
            "suggested_action": "answer_question",
            "evidence_refs": [],
        })
    if not getattr(snap, "observations", []) and getattr(snap, "attempts", []):
        flags.append({
            "flag_id": "no_observations_registered",
            "kind": "evidence_missing",
            "severity": "warning",
            "label": "Code has run but no observations are registered.",
            "suggested_action": "repair_registration",
            "evidence_refs": [snap.attempts[-1].attempt_id],
        })
    for trigger in getattr(snap, "triggers", []) or []:
        if trigger.status == "open":
            flags.append({
                "flag_id": f"trigger:{trigger.trigger_id}",
                "kind": "runtime_issue",
                "severity": trigger.severity,
                "label": trigger.summary,
                "suggested_action": "review_repair",
                "evidence_refs": [trigger.attempt_id] if trigger.attempt_id else [],
            })
    return flags[:12]
