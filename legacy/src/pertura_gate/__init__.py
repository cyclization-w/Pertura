"""Legacy evidence-gate compatibility namespace with lazy exports."""

from __future__ import annotations

from importlib import import_module
from typing import Any

_EXPORTS = {
    "DEFAULT_POLICY": ("pertura_gate.core.policy", "DEFAULT_POLICY"),
    "GatePolicy": ("pertura_gate.core.policy", "GatePolicy"),
    "policy_for_profile": ("pertura_gate.core.policy", "policy_for_profile"),
    "EvidenceRegistry": ("pertura_gate.evidence.registry", "EvidenceRegistry"),
    "render_evidence_report": ("pertura_gate.render.renderer", "render_evidence_report"),
    "resolve_artifact_strength": ("pertura_gate.resolver.resolver", "resolve_artifact_strength"),
    "resolve_claim": ("pertura_gate.resolver.resolver", "resolve_claim"),
    "resolve_claims": ("pertura_gate.resolver.resolver", "resolve_claims"),
}

__all__ = list(_EXPORTS)


def __getattr__(name: str) -> Any:
    try:
        module_name, attribute = _EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(name) from exc
    value = getattr(import_module(module_name), attribute)
    globals()[name] = value
    return value
