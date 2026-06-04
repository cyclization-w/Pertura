"""Public condition helpers for authoring analysis graphs.

These helpers are the user-facing vocabulary. They compile to ConditionSpec
objects that the runtime can evaluate deterministically.
"""

from __future__ import annotations

from pertura.spec.models import ConditionSpec, ConditionTier, condition


def workspace_files_available(*, message: str = "Workspace files are available.") -> ConditionSpec:
    return condition(
        "has_workspace_file",
        evaluator_id="has_workspace_file",
        message=message,
    )


def dataset_loaded(*, message: str = "Dataset is loaded or summarized.") -> ConditionSpec:
    return condition(
        "dataset_loaded",
        evaluator_id="has_dataset_loaded_observation",
        message=message,
    )


def design_confirmed(
    field: str,
    *,
    tier: ConditionTier = "C",
    message: str = "",
) -> ConditionSpec:
    return condition(
        f"{field}_confirmed",
        evaluator_id="design_field_known",
        tier=tier,
        failure_mode="human_interrupt" if tier == "C" else "autonomous_recovery",
        inputs={"field": field},
        message=message or f"Design field '{field}' must be confirmed.",
    )


def design_any_confirmed(
    fields: list[str],
    *,
    condition_id: str = "design_any_confirmed",
    tier: ConditionTier = "C",
    message: str = "",
) -> ConditionSpec:
    return condition(
        condition_id,
        evaluator_id="design_any_known",
        tier=tier,
        failure_mode="human_interrupt" if tier == "C" else "autonomous_recovery",
        inputs={"fields": fields},
        message=message or f"At least one design field must be confirmed: {', '.join(fields)}.",
    )


def artifact_exists(kind: str, *, message: str = "") -> ConditionSpec:
    return condition(
        f"has_{kind}_artifact",
        evaluator_id="has_artifact_kind",
        inputs={"kind": kind},
        message=message or f"Artifact of kind '{kind}' must exist.",
    )


def observation_exists(
    *,
    target: str = "",
    metric: str = "",
    message: str = "",
) -> ConditionSpec:
    suffix = "_".join(item for item in [target, metric] if item) or "observation"
    return condition(
        f"has_{suffix}",
        evaluator_id="has_observation",
        inputs={"target": target, "metric": metric},
        message=message or "A matching observation must exist.",
    )


def observation_metric(metric: str, *, message: str = "") -> ConditionSpec:
    return condition(
        f"has_{metric}",
        evaluator_id="has_observation_metric",
        inputs={"metric": metric},
        message=message or f"Observation metric '{metric}' must exist.",
    )


def capability_available(capability_id: str, *, message: str = "") -> ConditionSpec:
    return condition(
        f"capability_{capability_id}_available",
        evaluator_id="has_capability",
        inputs={"capability_id": capability_id},
        message=message or f"Capability '{capability_id}' must be available.",
    )


def no_open_trigger(*, message: str = "No open review trigger remains.") -> ConditionSpec:
    return condition(
        "no_open_trigger",
        evaluator_id="no_open_trigger",
        message=message,
    )


def manual_confirmation(key: str, *, message: str = "") -> ConditionSpec:
    return condition(
        key,
        evaluator_id="manual_confirmation",
        tier="C",
        failure_mode="human_interrupt",
        inputs={"key": key},
        message=message or f"Manual confirmation required: {key}.",
    )
