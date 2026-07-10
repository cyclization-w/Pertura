"""Capability-first workflow namespace with lazy legacy compatibility exports."""

from __future__ import annotations

from importlib import import_module
from typing import Any

_EXPORTS = {
    "EvidenceCandidate": ("pertura_workflow.models", "EvidenceCandidate"),
    "EvidenceGoal": ("pertura_workflow.models", "EvidenceGoal"),
    "HarvestMode": ("pertura_workflow.models", "HarvestMode"),
    "HarvestReport": ("pertura_workflow.models", "HarvestReport"),
    "PreflightReport": ("pertura_workflow.models", "PreflightReport"),
    "WorkflowRunManifest": ("pertura_workflow.models", "WorkflowRunManifest"),
    "WorkflowStateManifest": ("pertura_workflow.models", "WorkflowStateManifest"),
    "preflight_workspace": ("pertura_workflow.preflight", "preflight_workspace"),
    "harvest_artifacts_from_workspace": ("pertura_workflow.harvest", "harvest_artifacts_from_workspace"),
    "recommend_next_evidence": ("pertura_workflow.recommend", "recommend_next_evidence"),
    "run_classic_perturbseq": ("pertura_workflow.recipes", "run_classic_perturbseq"),
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
