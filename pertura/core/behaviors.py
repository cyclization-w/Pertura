"""Deterministic reactive behaviors for the scientific graph."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable
from uuid import uuid4

from pertura.models import Event, Snapshot
from pertura.core.observation_memory import build_observation_memory_view

BehaviorEvent = tuple[str, dict, str]
BehaviorFn = Callable[[list[Event], Snapshot, dict | None], list[BehaviorEvent]]


@dataclass(frozen=True)
class Behavior:
    name: str
    fn: BehaviorFn

    def run(self, events: list[Event], snap: Snapshot, graph: dict | None) -> list[BehaviorEvent]:
        return self.fn(events, snap, graph)


class BehaviorRegistry:
    def __init__(self, behaviors: list[Behavior] | None = None):
        self.behaviors = behaviors or default_behaviors()

    def run(self, events: list[Event], snap: Snapshot, graph: dict | None = None) -> list[BehaviorEvent]:
        emitted: list[BehaviorEvent] = []
        for behavior in self.behaviors:
            emitted.extend(behavior.run(events, snap, graph))
        return emitted


def default_behaviors() -> list[Behavior]:
    return [
        Behavior("runtime_failure_trigger", runtime_failure_trigger),
        Behavior("zero_observation_finding", zero_observation_finding),
        Behavior("manifest_completeness_finding", manifest_completeness_finding),
        Behavior("unsupported_conclusion_finding", unsupported_conclusion_finding),
        Behavior("observation_conflict_finding", observation_conflict_finding),
        Behavior("observation_divergence_finding", observation_divergence_finding),
        Behavior("design_change_stale_finding", design_change_stale_finding),
    ]


def runtime_failure_trigger(events: list[Event], snap: Snapshot, graph: dict | None) -> list[BehaviorEvent]:
    out: list[BehaviorEvent] = []
    for event in events:
        if event.event_type != "outcome_recorded":
            continue
        outcome = event.payload.get("outcome", {})
        if outcome.get("status") != "error":
            continue
        attempt_id = outcome.get("attempt_id", "")
        if _has_open_trigger(snap, attempt_id, "runtime_error"):
            continue
        summary = outcome.get("summary", "Execution failed.")
        stderr = (outcome.get("metrics", {}).get("stderr") or "")[-500:]
        if stderr:
            summary = f"{summary}. {stderr}"
        out.append(("trigger_opened", {"trigger": {
            "trigger_id": f"trg_{uuid4().hex[:12]}",
            "attempt_id": attempt_id,
            "trigger_type": "runtime_error",
            "severity": "blocking",
            "summary": summary,
            "status": "open",
        }}, "behavior:runtime_failure_trigger"))
    return out


def zero_observation_finding(events: list[Event], snap: Snapshot, graph: dict | None) -> list[BehaviorEvent]:
    out: list[BehaviorEvent] = []
    for event in events:
        if event.event_type != "outcome_recorded":
            continue
        outcome = event.payload.get("outcome", {})
        attempt_id = outcome.get("attempt_id", "")
        if outcome.get("status") != "success":
            continue
        if outcome.get("metrics", {}).get("observations_registered", 0) != 0:
            continue
        if _has_finding(snap, attempt_id, "missing_context"):
            continue
        out.append(("finding_recorded", {"finding": {
            "finding_id": f"fnd_{uuid4().hex[:12]}",
            "attempt_id": attempt_id,
            "finding_type": "missing_context",
            "severity": "warning",
            "suggested_action": "rerun",
            "summary": "No observations registered. LLM code may not have called register_observation().",
        }}, "behavior:zero_observation_finding"))
    return out


def unsupported_conclusion_finding(events: list[Event], snap: Snapshot, graph: dict | None) -> list[BehaviorEvent]:
    out: list[BehaviorEvent] = []
    for event in events:
        if event.event_type != "conclusion_recorded":
            continue
        conclusion = event.payload.get("conclusion", {})
        support_ids = conclusion.get("support_ids", [])
        if support_ids:
            continue
        conclusion_id = conclusion.get("conclusion_id", "")
        if _has_finding(snap, conclusion_id, "missing_context"):
            continue
        out.append(("finding_recorded", {"finding": {
            "finding_id": f"fnd_{uuid4().hex[:12]}",
            "attempt_id": "",
            "finding_type": "missing_context",
            "severity": "warning",
            "suggested_action": "downgrade",
            "summary": f"Conclusion {conclusion_id} has no supporting observation or artifact ids.",
            "affected_ids": [conclusion_id] if conclusion_id else [],
        }}, "behavior:unsupported_conclusion_finding"))
    return out


def observation_conflict_finding(events: list[Event], snap: Snapshot, graph: dict | None) -> list[BehaviorEvent]:
    out: list[BehaviorEvent] = []
    for event in events:
        if event.event_type != "observation_registered":
            continue
        observation = event.payload.get("observation", {})
        value = observation.get("value")
        if not isinstance(value, (int, float)):
            continue
        target = observation.get("target", "")
        metric = observation.get("metric", "")
        contrast = observation.get("contrast", "")
        method = observation.get("method", "")
        if not target or not metric:
            continue
        siblings = [
            obs for obs in snap.observations
            if obs.target == target
            and obs.metric == metric
            and obs.contrast == contrast
            and obs.method == method
            and obs.observation_id != observation.get("observation_id")
            and isinstance(obs.value, (int, float))
        ]
        conflicts = [obs for obs in siblings if obs.value * value < 0]
        if not conflicts:
            continue
        affected = [observation.get("observation_id", "")] + [obs.observation_id for obs in conflicts]
        if any(_same_affected(f.affected_ids, affected) for f in snap.findings if f.finding_type == "observation_conflict"):
            continue
        out.append(("finding_recorded", {"finding": {
            "finding_id": f"fnd_{uuid4().hex[:12]}",
            "attempt_id": observation.get("attempt_id", ""),
            "finding_type": "observation_conflict",
            "severity": "warning",
            "suggested_action": "trace_upstream",
            "summary": f"Conflicting {target}/{metric} observations under contrast={contrast or 'none'}, method={method or 'none'}.",
            "affected_ids": [item for item in affected if item],
        }}, "behavior:observation_conflict_finding"))
    return out


def observation_divergence_finding(events: list[Event], snap: Snapshot, graph: dict | None) -> list[BehaviorEvent]:
    if not any(event.event_type == "observation_registered" for event in events):
        return []
    memory = build_observation_memory_view(snap, limit=20)
    out: list[BehaviorEvent] = []
    for divergence in memory.get("divergences", []):
        left = divergence.get("left", {})
        right = divergence.get("right", {})
        affected = [
            item for item in [left.get("observation_id"), right.get("observation_id")]
            if item
        ]
        if any(_same_affected(f.affected_ids, affected)
               for f in snap.findings
               if f.finding_type == "observation_divergence"):
            continue
        out.append(("finding_recorded", {"finding": {
            "finding_id": f"fnd_{uuid4().hex[:12]}",
            "attempt_id": right.get("attempt_id", "") or left.get("attempt_id", ""),
            "finding_type": "observation_divergence",
            "severity": "warning",
            "suggested_action": "trace_upstream",
            "summary": (
                f"{divergence.get('subject_metric', '')} has opposite signs across contexts: "
                f"{left.get('observation_id')}={left.get('value')} vs "
                f"{right.get('observation_id')}={right.get('value')}"
            ),
            "affected_ids": affected,
        }}, "behavior:observation_divergence_finding"))
    return out


def manifest_completeness_finding(events: list[Event], snap: Snapshot, graph: dict | None) -> list[BehaviorEvent]:
    out: list[BehaviorEvent] = []
    for event in events:
        if event.event_type != "outcome_recorded":
            continue
        outcome = event.payload.get("outcome", {})
        attempt_id = outcome.get("attempt_id", "")
        if outcome.get("status") != "success" or not attempt_id:
            continue
        observations = [obs for obs in snap.observations if obs.attempt_id == attempt_id]
        missing = []
        for obs in observations:
            fields = _missing_observation_fields(obs)
            if fields:
                missing.append({"observation_id": obs.observation_id, "missing": fields})
        if not missing:
            continue
        if _has_finding(snap, attempt_id, "manifest_incomplete"):
            continue
        out.append(("finding_recorded", {"finding": {
            "finding_id": f"fnd_{uuid4().hex[:12]}",
            "attempt_id": attempt_id,
            "finding_type": "manifest_incomplete",
            "severity": "warning",
            "suggested_action": "rerun",
            "summary": (
                f"{len(missing)} observation(s) have incomplete structured fields: "
                + "; ".join(f"{item['observation_id']} missing {','.join(item['missing'])}" for item in missing[:3])
            ),
            "affected_ids": [item["observation_id"] for item in missing],
        }}, "behavior:manifest_completeness_finding"))
    return out


def design_change_stale_finding(events: list[Event], snap: Snapshot, graph: dict | None) -> list[BehaviorEvent]:
    """Flag downstream memory after PI/user design facts change.

    This is intentionally conservative: it does not mutate or downgrade old
    observations. It records a finding so ContextView and the LLM planner can
    trace impacted variables and decide what to rerun.
    """
    out: list[BehaviorEvent] = []
    for event in events:
        if event.event_type != "design_updated":
            continue
        changed = sorted((event.payload.get("design") or {}).keys())
        if not changed:
            continue
        affected = _affected_by_design_change(snap, changed)
        if not affected:
            continue
        if any(
            finding.finding_type == "potentially_stale_dependency"
            and set(finding.affected_ids) == set(affected)
            for finding in snap.findings
        ):
            continue
        out.append(("finding_recorded", {"finding": {
            "finding_id": f"fnd_{uuid4().hex[:12]}",
            "finding_type": "potentially_stale_dependency",
            "severity": "warning",
            "suggested_action": "trace_upstream",
            "summary": (
                f"Design fields changed ({', '.join(changed)}). "
                f"{len(affected)} prior observation/conclusion node(s) may need rerun or reinterpretation."
            ),
            "affected_ids": affected[:50],
        }}, "behavior:design_change_stale_finding"))
    return out


def _has_open_trigger(snap: Snapshot, attempt_id: str, trigger_type: str) -> bool:
    return any(
        trigger.attempt_id == attempt_id
        and trigger.trigger_type == trigger_type
        and trigger.status == "open"
        for trigger in snap.triggers
    )


def _has_finding(snap: Snapshot, attempt_id: str, finding_type: str) -> bool:
    return any(
        finding.attempt_id == attempt_id
        and finding.finding_type == finding_type
        for finding in snap.findings
    )


def _same_affected(left: list[str], right: list[str]) -> bool:
    return set(left) == set(item for item in right if item)


def _missing_observation_fields(obs) -> list[str]:
    required = ["target", "metric", "value"]
    metric = (obs.metric or "").lower()
    obs_type = (obs.type or "").lower()
    if any(token in metric or token in obs_type for token in ("logfc", "p_value", "de", "differential")):
        required.extend(["contrast", "method"])
    missing = []
    for field in required:
        value = getattr(obs, field)
        if value in ("", None, []):
            missing.append(field)
    return missing


def _affected_by_design_change(snap: Snapshot, changed_fields: list[str]) -> list[str]:
    # Prefer structured design dependency declarations. Fall back to weak
    # parameter/input text matching only for legacy observations.
    changed = {field.lower() for field in changed_fields}
    attempts_by_id = {attempt.attempt_id: attempt for attempt in snap.attempts}
    affected = []
    for obs in snap.observations:
        declared = {field.lower() for field in getattr(obs, "design_fields_used", [])}
        attempt = attempts_by_id.get(obs.attempt_id)
        if attempt:
            declared.update(field.lower() for field in getattr(attempt, "design_fields_used", []))
        if declared and declared.intersection(changed):
            affected.append(obs.observation_id)
            continue
        if declared:
            continue
        searchable = " ".join([
            *obs.input_ids,
            str(obs.parameters),
            obs.contrast,
            obs.method,
            obs.metric,
        ]).lower()
        if any(field.lower() in searchable for field in changed_fields):
            affected.append(obs.observation_id)
    if not affected:
        affected = [
            obs.observation_id
            for obs in snap.observations
            if obs.type != "workspace_file"
        ]
    affected.extend(conclusion.conclusion_id for conclusion in snap.conclusions)
    return [item for item in affected if item]
