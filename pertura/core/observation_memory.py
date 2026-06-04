"""Typed observation memory queries for LLM and graph consumers."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from pertura.models import CoverageEntry, MemoryEntry, Observation, Snapshot, _model_dump


def observation_key(obs: Observation) -> str:
    """Stable semantic key for a measured scientific variable."""
    if obs.variable_key:
        return obs.variable_key
    return "|".join([
        obs.target or "",
        obs.metric or "",
        obs.contrast or "",
    ])


def build_observation_memory_view(
    snap: Snapshot,
    *,
    target: str = "",
    metric: str = "",
    contrast: str = "",
    method: str = "",
    branch_id: str = "",
    limit: int = 10,
) -> dict:
    """Return compact variable-level memory for matching observations."""
    observations = _filter_observations(
        snap.observations,
        target=target,
        metric=metric,
        contrast=contrast,
        method=method,
        branch_id=branch_id,
    )
    groups = _group_by_variable(observations)
    variables = []
    for key, records in groups.items():
        variables.append(_summarize_variable(key, records))
    variables.sort(key=lambda item: (
        {"conflicted": 0, "thin": 1, "divergent": 2, "convergent": 3}.get(item["coverage_label"], 4),
        item["variable_key"],
    ))
    divergences = _cross_context_divergences(observations)
    return {
        "view_type": "observation_memory",
        "query": {
            "target": target,
            "metric": metric,
            "contrast": contrast,
            "method": method,
            "branch_id": branch_id,
        },
        "observation_count": len(observations),
        "variable_count": len(variables),
        "variables": variables[:limit],
        "conflicts": [v for v in variables if v["conflict_count"] > 0][:limit],
        "divergences": divergences[:limit],
        "needs_review": [v for v in variables if v["conflict_count"] > 0][:limit] + divergences[:limit],
        "coverage": [_coverage_payload(v) for v in variables[:limit]],
        "summary": _memory_view_summary(variables, divergences),
        "truncated": len(variables) > limit,
    }


def build_memory_entries(snap: Snapshot) -> list[MemoryEntry]:
    entries = []
    for key, records in _group_by_variable(snap.observations).items():
        summary = _summarize_variable(key, records)
        latest = summary["latest"]
        entries.append(MemoryEntry(
            subject=latest.get("target", ""),
            metric=latest.get("metric", ""),
            current_value=latest.get("value"),
            prior_values=summary["prior_values"],
            signal=_memory_signal(summary),
            summary=_memory_summary(summary),
        ))
    return sorted(entries, key=lambda e: {"conflict": 0, "thin": 1, "agreement": 2, "confirmed": 3}.get(e.signal, 4))[:20]


def build_coverage_entries(snap: Snapshot) -> list[CoverageEntry]:
    entries = []
    for key, records in _group_by_variable(snap.observations).items():
        summary = _summarize_variable(key, records)
        latest = summary["latest"]
        entries.append(CoverageEntry(
            subject=latest.get("target", "") or key,
            methods=summary["method_count"],
            branches=summary["branch_count"],
            observations=summary["observation_count"],
            contradictions=summary["conflict_count"],
            label=summary["coverage_label"],
        ))
    return sorted(entries, key=lambda e: {"conflicted": 0, "thin": 1, "divergent": 2, "convergent": 3}.get(e.label, 4))


def _filter_observations(
    observations: list[Observation],
    *,
    target: str,
    metric: str,
    contrast: str,
    method: str,
    branch_id: str,
) -> list[Observation]:
    result = []
    for obs in observations:
        if target and obs.target.lower() != target.lower():
            continue
        if metric and obs.metric.lower() != metric.lower():
            continue
        if contrast and obs.contrast.lower() != contrast.lower():
            continue
        if method and obs.method.lower() != method.lower():
            continue
        if branch_id and obs.branch_id != branch_id:
            continue
        result.append(obs)
    return result


def _group_by_variable(observations: list[Observation]) -> dict[str, list[Observation]]:
    groups: dict[str, list[Observation]] = defaultdict(list)
    for obs in observations:
        groups[observation_key(obs)].append(obs)
    return dict(groups)


def _summarize_variable(key: str, records: list[Observation]) -> dict:
    ordered = sorted(records, key=lambda obs: str(obs.created_at))
    latest = ordered[-1] if ordered else None
    numeric_values = [obs.value for obs in ordered if isinstance(obs.value, (int, float))]
    signs = {_sign(value) for value in numeric_values if _sign(value) != 0}
    conflicts = _conflicts(ordered)
    methods = sorted({obs.method for obs in ordered if obs.method})
    branches = sorted({obs.branch_id for obs in ordered if obs.branch_id})
    contrasts = sorted({obs.contrast for obs in ordered if obs.contrast})
    if conflicts:
        label = "conflicted"
    elif len(methods) >= 2 or len(branches) >= 2:
        label = "convergent" if len(signs) <= 1 else "divergent"
    elif len(ordered) <= 1:
        label = "thin"
    else:
        label = "adequate"
    return {
        "variable_key": key,
        "observation_count": len(ordered),
        "method_count": len(methods),
        "branch_count": len(branches),
        "contrast_count": len(contrasts),
        "methods": methods,
        "branches": branches,
        "contrasts": contrasts,
        "values": [_observation_payload(obs) for obs in ordered[-10:]],
        "latest": _observation_payload(latest) if latest else {},
        "prior_values": [_observation_payload(obs) for obs in ordered[:-1][-5:]],
        "conflict_count": len(conflicts),
        "conflicts": conflicts,
        "coverage_label": label,
    }


def _conflicts(records: list[Observation]) -> list[dict]:
    conflicts = []
    for idx, left in enumerate(records):
        for right in records[idx + 1:]:
            if not _numeric_conflict(left.value, right.value):
                continue
            conflicts.append({
                "left": _observation_payload(left),
                "right": _observation_payload(right),
                "reason": "opposite_numeric_sign",
            })
    return conflicts


def _cross_context_divergences(records: list[Observation]) -> list[dict]:
    """Find sign disagreements for same target/metric across contexts.

    These are review prompts, not automatic contradictions: contrast or method
    changes may legitimately flip direction, but the LLM should notice them.
    """
    grouped: dict[str, list[Observation]] = defaultdict(list)
    for obs in records:
        grouped[f"{obs.target}|{obs.metric}"].append(obs)
    divergences = []
    for key, group in grouped.items():
        if len(group) < 2:
            continue
        for idx, left in enumerate(group):
            for right in group[idx + 1:]:
                if not _numeric_conflict(left.value, right.value):
                    continue
                if observation_key(left) == observation_key(right):
                    continue
                divergences.append({
                    "subject_metric": key,
                    "left": _observation_payload(left),
                    "right": _observation_payload(right),
                    "reason": "opposite_sign_across_context",
                    "context_difference": {
                        "contrast_changed": left.contrast != right.contrast,
                        "method_changed": left.method != right.method,
                        "branch_changed": left.branch_id != right.branch_id,
                        "parameter_hash_changed": left.parameter_hash != right.parameter_hash,
                    },
                })
    return divergences


def _numeric_conflict(left: Any, right: Any) -> bool:
    return (
        isinstance(left, (int, float))
        and isinstance(right, (int, float))
        and left is not None
        and right is not None
        and left * right < 0
    )


def _sign(value: int | float) -> int:
    if value > 0:
        return 1
    if value < 0:
        return -1
    return 0


def _observation_payload(obs: Observation | None) -> dict:
    if obs is None:
        return {}
    payload = _model_dump(obs)
    return {
        "observation_id": payload.get("observation_id", ""),
        "target": payload.get("target", ""),
        "metric": payload.get("metric", ""),
        "value": payload.get("value"),
        "contrast": payload.get("contrast", ""),
        "method": payload.get("method", ""),
        "branch_id": payload.get("branch_id", ""),
        "attempt_id": payload.get("attempt_id", ""),
        "artifact_id": payload.get("artifact_id", ""),
        "variable_key": payload.get("variable_key", ""),
        "parameters": payload.get("parameters", {}),
        "parameter_hash": payload.get("parameter_hash", ""),
        "method_version": payload.get("method_version", ""),
        "input_ids": payload.get("input_ids", []),
    }


def _coverage_payload(summary: dict) -> dict:
    return {
        "variable_key": summary["variable_key"],
        "observations": summary["observation_count"],
        "methods": summary["method_count"],
        "branches": summary["branch_count"],
        "contrasts": summary["contrast_count"],
        "conflicts": summary["conflict_count"],
        "label": summary["coverage_label"],
    }


def _memory_signal(summary: dict) -> str:
    label = summary["coverage_label"]
    if label == "conflicted":
        return "conflict"
    if label == "convergent":
        return "confirmed"
    if label == "thin":
        return "thin"
    return "agreement"


def _memory_summary(summary: dict) -> str:
    latest = summary["latest"]
    return (
        f"{summary['variable_key']}: latest={latest.get('value')} "
        f"({latest.get('method') or 'no method'}, {latest.get('contrast') or 'no contrast'}); "
        f"coverage={summary['coverage_label']}, conflicts={summary['conflict_count']}"
    )


def _memory_view_summary(variables: list[dict], divergences: list[dict]) -> dict:
    labels: dict[str, int] = {}
    for variable in variables:
        label = variable["coverage_label"]
        labels[label] = labels.get(label, 0) + 1
    return {
        "variables": len(variables),
        "strict_conflicts": sum(1 for variable in variables if variable["conflict_count"] > 0),
        "cross_context_divergences": len(divergences),
        "coverage_labels": labels,
    }
