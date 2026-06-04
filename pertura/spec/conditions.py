"""Condition evaluation against compiled runtime state.

Condition evaluators are deliberately pure and bounded: they read Snapshot
fields and typed observations/artifacts only. They do not inspect files, run
code, call tools, or query the network.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pertura.spec.models import ConditionSpec
from pertura.models import Snapshot


@dataclass(frozen=True)
class ConditionResult:
    condition_id: str
    passed: bool
    tier: str
    failure_mode: str
    message: str
    hard: bool = True
    details: dict[str, Any] | None = None


def evaluate_condition(spec: ConditionSpec, snap: Snapshot) -> ConditionResult:
    evaluator = spec.evaluator_id or spec.condition_id
    fn = CONDITION_CHECKS.get(evaluator)
    if spec.evaluator_id == "rubric_only" or not spec.hard:
        return _result(spec, True, details={"rubric_only": True})
    if fn is None:
        return _result(spec, False, message=f"Unknown condition evaluator: {evaluator}")
    try:
        passed, details = fn(spec, snap)
        return _result(spec, bool(passed), details=details)
    except Exception as exc:
        return _result(spec, False, message=f"Condition evaluator failed: {exc}")


def evaluate_conditions(specs: list[ConditionSpec], snap: Snapshot) -> list[ConditionResult]:
    return [evaluate_condition(spec, snap) for spec in specs]


def _result(
    spec: ConditionSpec,
    passed: bool,
    *,
    message: str = "",
    details: dict[str, Any] | None = None,
) -> ConditionResult:
    return ConditionResult(
        condition_id=spec.condition_id,
        passed=passed,
        tier=spec.tier,
        failure_mode=spec.failure_mode,
        message=message or spec.message or spec.description,
        hard=spec.hard,
        details=details or {},
    )


def _has_workspace_file(spec: ConditionSpec, snap: Snapshot) -> tuple[bool, dict]:
    observations = [
        obs for obs in snap.observations
        if obs.type == "workspace_file"
    ]
    return bool(observations), {"workspace_file_count": len(observations)}


def _has_dataset_loaded_observation(spec: ConditionSpec, snap: Snapshot) -> tuple[bool, dict]:
    matches = [
        obs for obs in snap.observations
        if (
            obs.type in {"schema", "dataset", "anndata"}
            or obs.target.lower() in {"anndata", "adata", "dataset"}
            or obs.metric.lower() in {"shape", "n_obs", "n_vars"}
        )
    ]
    artifacts = [
        art for art in snap.artifacts
        if art.kind in {"anndata", "h5ad", "dataset"}
    ]
    return bool(matches or artifacts), {
        "observation_count": len(matches),
        "artifact_count": len(artifacts),
    }


def _design_field_known(spec: ConditionSpec, snap: Snapshot) -> tuple[bool, dict]:
    field = spec.inputs.get("field", "")
    value = snap.design.get(field)
    has_meta = field in (snap.design_meta or {})
    meta = (snap.design_meta or {}).get(field, {})
    source = meta.get("source", "")
    confirmed = (
        (bool(value) and not has_meta)
        or source in {"pi_confirmed", "user_confirmed", "api_confirmed", "manual_confirmation", "domain_default"}
    )
    if spec.tier == "C" or spec.failure_mode == "human_interrupt":
        return bool(value) and confirmed, {
            "field": field,
            "value": value,
            "source": source,
            "confirmed": confirmed,
        }
    return bool(value), {"field": field, "value": value, "source": source, "confirmed": confirmed}


def _design_any_known(spec: ConditionSpec, snap: Snapshot) -> tuple[bool, dict]:
    fields = spec.inputs.get("fields", [])
    present = {field: snap.design.get(field) for field in fields if snap.design.get(field)}
    if spec.tier == "C" or spec.failure_mode == "human_interrupt":
        confirmed = {
            field: value
            for field, value in present.items()
            if field not in (snap.design_meta or {})
            or (snap.design_meta or {}).get(field, {}).get("source") in {
                "pi_confirmed", "user_confirmed", "api_confirmed", "manual_confirmation", "domain_default"
            }
        }
        return bool(confirmed), {"fields": fields, "present": present, "confirmed": confirmed}
    return bool(present), {"fields": fields, "present": present}


def _manual_confirmation(spec: ConditionSpec, snap: Snapshot) -> tuple[bool, dict]:
    key = spec.inputs.get("key", spec.condition_id)
    confirmations = snap.design.get("confirmations", {}) if isinstance(snap.design, dict) else {}
    value = confirmations.get(key) or snap.design.get(key)
    return bool(value), {"key": key, "value": value}


def _has_artifact_kind(spec: ConditionSpec, snap: Snapshot) -> tuple[bool, dict]:
    kind = spec.inputs.get("kind", "")
    matches = [art for art in snap.artifacts if not kind or art.kind == kind]
    return bool(matches), {"kind": kind, "artifact_count": len(matches)}


def _has_observation(spec: ConditionSpec, snap: Snapshot) -> tuple[bool, dict]:
    target = spec.inputs.get("target", "")
    metric = spec.inputs.get("metric", "")
    matches = []
    for obs in snap.observations:
        if target and obs.target.lower() != str(target).lower():
            continue
        if metric and obs.metric.lower() != str(metric).lower():
            continue
        matches.append(obs)
    return bool(matches), {"target": target, "metric": metric, "observation_count": len(matches)}


def _has_observation_metric(spec: ConditionSpec, snap: Snapshot) -> tuple[bool, dict]:
    metric = spec.inputs.get("metric", "")
    matches = [obs for obs in snap.observations if obs.metric == metric]
    return bool(matches), {"metric": metric, "observation_count": len(matches)}


def _has_capability(spec: ConditionSpec, snap: Snapshot) -> tuple[bool, dict]:
    capability_id = spec.inputs.get("capability_id", "")
    capability_ids = {cap.get("capability_id") or cap.get("id", "") for cap in snap.capabilities}
    return capability_id in capability_ids, {"capability_id": capability_id}


def _no_open_trigger(spec: ConditionSpec, snap: Snapshot) -> tuple[bool, dict]:
    open_triggers = [trigger for trigger in snap.triggers if trigger.status == "open"]
    return not open_triggers, {"open_trigger_count": len(open_triggers)}


CONDITION_CHECKS = {
    "has_workspace_file": _has_workspace_file,
    "has_dataset_loaded_observation": _has_dataset_loaded_observation,
    "design_field_known": _design_field_known,
    "design_any_known": _design_any_known,
    "manual_confirmation": _manual_confirmation,
    "has_artifact_kind": _has_artifact_kind,
    "has_observation": _has_observation,
    "has_observation_metric": _has_observation_metric,
    "has_capability": _has_capability,
    "no_open_trigger": _no_open_trigger,
}
