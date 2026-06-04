"""Thin policy layer for risky graph mutations.

Policy is intentionally not a DSL in v1. It only answers whether a proposed
patch can be applied immediately, must wait for approval, or should be rejected.
"""

from __future__ import annotations

from dataclasses import dataclass


_APPROVAL_PATCH_TYPES = {
    "workspace_write",
    "web_research",
    "external_permission",
    "human_authority",
    "high_risk_tool",
}

_REJECT_PATCH_TYPES = {
    "delete_history",
    "mutate_event_log",
    "direct_snapshot_write",
}


@dataclass(frozen=True)
class PolicyDecision:
    action: str
    reason: str = ""
    approval_type: str = ""

    @property
    def allowed(self) -> bool:
        return self.action == "allow"

    @property
    def requires_approval(self) -> bool:
        return self.action == "require_approval"

    @property
    def rejected(self) -> bool:
        return self.action == "reject"


class PolicyEngine:
    """Small governance boundary for LLM proposals and patch application."""

    def evaluate_patch(self, patch) -> PolicyDecision:
        patch_type = _patch_get(patch, "patch_type") or ""
        payload = _patch_get(patch, "payload") or {}
        if patch_type in _REJECT_PATCH_TYPES:
            return PolicyDecision("reject", f"Patch type is forbidden: {patch_type}")
        if payload.get("requires_approval") or patch_type in _APPROVAL_PATCH_TYPES:
            approval_type = payload.get("approval_type") or patch_type or "manual_review"
            reason = payload.get("approval_reason") or _patch_get(patch, "rationale") or f"Patch requires approval: {approval_type}"
            return PolicyDecision("require_approval", reason, approval_type)
        return PolicyDecision("allow")

    def approved(self, snap, subject_id: str) -> bool:
        return any(
            a.subject_id == subject_id and a.status == "resolved" and a.decision == "approved"
            for a in getattr(snap, "approvals", [])
        )

    def open_approval(self, snap, subject_id: str):
        return next((
            a for a in getattr(snap, "approvals", [])
            if a.subject_id == subject_id and a.status == "open"
        ), None)


def _patch_get(patch, key: str):
    if isinstance(patch, dict):
        return patch.get(key)
    return getattr(patch, key)
