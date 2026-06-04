"""Schema validation for event payloads at the graph write boundary."""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from pertura.core.errors import PerturaError
from pertura.models import (
    ApprovalRequest,
    Artifact,
    AssistantResponse,
    Attempt,
    BehaviorRun,
    Branch,
    Conclusion,
    Finding,
    GateEvaluation,
    Goal,
    Interrupt,
    Intervention,
    Observation,
    Outcome,
    PatchProposal,
    ReviewTrigger,
)


class EventSchemaError(PerturaError, ValueError):
    """Raised when an event payload does not match its declared schema."""

    default_code = "event.schema_error"
    default_doc_path = "errors/event-schema"


_ENTITY_PAYLOADS = {
    "attempt_planned": ("attempt", Attempt),
    "outcome_recorded": ("outcome", Outcome),
    "artifact_registered": ("artifact", Artifact),
    "observation_registered": ("observation", Observation),
    "trigger_opened": ("trigger", ReviewTrigger),
    "finding_recorded": ("finding", Finding),
    "branch_opened": ("branch", Branch),
    "goal_recorded": ("goal", Goal),
    "conclusion_recorded": ("conclusion", Conclusion),
    "intervention_planned": ("intervention", Intervention),
    "interrupt_opened": ("interrupt", Interrupt),
    "approval_requested": ("approval", ApprovalRequest),
    "behavior_started": ("behavior_run", BehaviorRun),
    "gate_evaluated": ("gate_evaluation", GateEvaluation),
    "assistant_response_recorded": ("response", AssistantResponse),
    "patch_proposed": ("patch", PatchProposal),
}


_REQUIRED_KEYS = {
    "run_started": {"config": dict},
    "attempt_stopped": {"attempt_id": str},
    "trigger_resolved": {"trigger_id": str},
    "branch_stopped": {"branch_id": str},
    "branch_activated": {"branch_id": str},
    "review_decision_recorded": {"review_id": str},
    "intervention_applied": {"intervention_id": str},
    "interrupt_resolved": {"interrupt_id": str},
    "approval_decided": {"approval_id": str, "decision": str},
    "behavior_completed": {"behavior_run_id": str},
    "behavior_failed": {"behavior_run_id": str, "error": str},
    "analysis_spec_loaded": {"analysis_spec": dict},
    "capabilities_loaded": {"capabilities": list},
    "node_entered": {"node_id": str},
    "node_transition_blocked": {"target_node_id": str},
    "node_skipped": {"node_id": str},
    "node_completed": {"node_id": str},
    "design_updated": {"design": dict},
    "tool_call_recorded": {"tool_call_id": str, "tool_name": str},
    "patch_applied": {"patch_id": str, "event_ids": list},
    "patch_rejected": {"patch_id": str, "reason": str},
    "node_transition_requested": {"target_node_id": str},
    "analysis_spec_compiled": {"report": dict},
    "safety_violation_recorded": {"violations": list, "severity": str},
    "attempt_soft_timeout": {"attempt_id": str},
}


_EMPTY_OR_OPTIONAL_PAYLOADS = {
    "run_paused",
    "run_resumed",
    "run_complete",
}


_KNOWN_EVENT_TYPES = (
    set(_ENTITY_PAYLOADS)
    | set(_REQUIRED_KEYS)
    | _EMPTY_OR_OPTIONAL_PAYLOADS
)


def validate_event_payload(event_type: str, payload: dict[str, Any]) -> None:
    if not isinstance(event_type, str) or not event_type:
        raise EventSchemaError("event_type must be a non-empty string")
    if event_type not in _KNOWN_EVENT_TYPES:
        raise EventSchemaError(f"Unknown event_type: {event_type}")
    if not isinstance(payload, dict):
        raise EventSchemaError(f"{event_type} payload must be a dict")

    if event_type in _ENTITY_PAYLOADS:
        key, model = _ENTITY_PAYLOADS[event_type]
        if key not in payload:
            raise EventSchemaError(f"{event_type} payload missing `{key}`")
        value = payload.get(key)
        if not isinstance(value, dict):
            raise EventSchemaError(f"{event_type}.{key} must be a dict")
        _validate_model(event_type, key, model, value)
        _validate_entity_special_cases(event_type, key, value)
        return

    if event_type in _REQUIRED_KEYS:
        for key, expected_type in _REQUIRED_KEYS[event_type].items():
            if key not in payload:
                raise EventSchemaError(f"{event_type} payload missing `{key}`")
            if not isinstance(payload[key], expected_type):
                raise EventSchemaError(
                    f"{event_type}.{key} must be {expected_type.__name__}"
                )
        _validate_special_cases(event_type, payload)
        return

    if event_type in _EMPTY_OR_OPTIONAL_PAYLOADS:
        return


def _validate_model(event_type: str, key: str, model: Any, value: dict[str, Any]) -> None:
    try:
        model(**value)
    except ValidationError as exc:
        raise EventSchemaError(f"{event_type}.{key} invalid: {exc}") from exc


def _validate_entity_special_cases(event_type: str, key: str, value: dict[str, Any]) -> None:
    if event_type != "observation_registered":
        return
    required_nonempty = ("observation_id", "type", "target", "metric")
    for field in required_nonempty:
        if not isinstance(value.get(field), str) or not value.get(field):
            raise EventSchemaError(f"{event_type}.{key}.{field} must be a non-empty string")
    if "value" not in value or value.get("value") is None:
        raise EventSchemaError(f"{event_type}.{key}.value must be present")


def _validate_special_cases(event_type: str, payload: dict[str, Any]) -> None:
    if event_type == "run_started":
        config = payload.get("config") or {}
        for field in ("run_id", "workspace", "goal", "domain"):
            if field not in config:
                raise EventSchemaError(f"run_started.config missing `{field}`")
            if not isinstance(config.get(field), str):
                raise EventSchemaError(f"run_started.config.{field} must be str")
    elif event_type == "approval_decided":
        if payload.get("decision") not in {"approved", "rejected"}:
            raise EventSchemaError("approval_decided.decision must be approved or rejected")
    elif event_type == "behavior_completed":
        if "output_event_ids" in payload and not isinstance(payload["output_event_ids"], list):
            raise EventSchemaError("behavior_completed.output_event_ids must be list")
        if "output_count" in payload and not isinstance(payload["output_count"], int):
            raise EventSchemaError("behavior_completed.output_count must be int")
