"""Helpers for tracing scientific evidence back to executed attempts."""

from __future__ import annotations

from typing import Any


SUCCESS_OUTCOME_STATUSES = {"success", "succeeded", "completed"}


def latest_outcomes_by_attempt(outcomes) -> dict[str, Any]:
    latest: dict[str, Any] = {}
    for outcome in outcomes:
        latest[getattr(outcome, "attempt_id", "")] = outcome
    return latest


def review_evidence_chain(snap: Any, node_id: str = "", *, graph: dict | None = None, limit: int = 12) -> dict[str, Any]:
    """Return a compact self-audit view for a conclusion or observation."""
    attempts = {attempt.attempt_id: attempt for attempt in getattr(snap, "attempts", [])}
    observations = {obs.observation_id: obs for obs in getattr(snap, "observations", [])}
    artifacts = {artifact.artifact_id: artifact for artifact in getattr(snap, "artifacts", [])}
    conclusions = {con.conclusion_id: con for con in getattr(snap, "conclusions", [])}
    outcomes_by_attempt = latest_outcomes_by_attempt(getattr(snap, "outcomes", []))
    stale_ids = _stale_dependency_ids(snap)
    target_id = node_id or _latest_review_target(snap)
    if not target_id:
        return {
            "view_type": "evidence_chain_review",
            "node_id": "",
            "found": False,
            "status": "missing_target",
            "ok": False,
            "summary": "No conclusion or observation is available to review.",
            "checks": [],
            "next_actions": [{"tool": "get_context_review", "args": {"purpose": "audit"}, "why": "inspect current run state before choosing evidence"}],
        }
    safe_limit = max(1, min(int(limit or 12), 50))
    if target_id in conclusions:
        return _review_conclusion(
            conclusions[target_id],
            observations=observations,
            attempts=attempts,
            artifacts=artifacts,
            outcomes_by_attempt=outcomes_by_attempt,
            stale_ids=stale_ids,
            graph=graph or {},
            limit=safe_limit,
        )
    if target_id in observations:
        return _review_observation(
            observations[target_id],
            observations=observations,
            attempts=attempts,
            artifacts=artifacts,
            outcomes_by_attempt=outcomes_by_attempt,
            stale_ids=stale_ids,
            graph=graph or {},
        )
    if target_id in artifacts:
        return _review_artifact(artifacts[target_id], attempts=attempts, stale_ids=stale_ids, graph=graph or {})
    return {
        "view_type": "evidence_chain_review",
        "node_id": target_id,
        "found": False,
        "status": "unknown_node",
        "ok": False,
        "summary": f"No observation, artifact, or conclusion named {target_id} is present in the run.",
        "checks": [],
        "next_actions": [
            {"tool": "get_context_review", "args": {"purpose": "audit"}, "why": "inspect provenance_index for valid evidence ids"},
            {"tool": "audit_run", "args": {}, "why": "find unsupported or stale evidence references"},
        ],
    }


def observation_evidence_status(
    obs: Any | None,
    *,
    attempts: dict[str, Any],
    artifacts: dict[str, Any],
    observations: dict[str, Any],
    outcomes_by_attempt: dict[str, Any],
) -> dict[str, Any]:
    """Return whether an observation can be traced to a successful attempt.

    The walk is intentionally shallow and deterministic: direct observation
    attempt first, then artifact attempt, then declared input ids. This keeps
    audit strict for conclusions without turning every design/manual note into
    a recursive workflow proof.
    """
    if obs is None:
        return {
            "trace_status": "missing_observation",
            "successful": False,
            "path": [],
        }
    obs_id = getattr(obs, "observation_id", "")
    attempt_id = getattr(obs, "attempt_id", "")
    if attempt_id:
        return _attempt_evidence_status(
            attempt_id,
            attempts=attempts,
            outcomes_by_attempt=outcomes_by_attempt,
            source="direct_attempt",
            path=[obs_id, attempt_id],
        )

    artifact_id = getattr(obs, "artifact_id", "")
    artifact = artifacts.get(artifact_id) if artifact_id else None
    if artifact is not None and getattr(artifact, "attempt_id", ""):
        return _attempt_evidence_status(
            getattr(artifact, "attempt_id", ""),
            attempts=attempts,
            outcomes_by_attempt=outcomes_by_attempt,
            source="artifact_attempt",
            path=[obs_id, artifact_id, getattr(artifact, "attempt_id", "")],
            artifact_id=artifact_id,
        )

    for input_id in list(getattr(obs, "input_ids", []) or [])[:12]:
        if input_id in attempts:
            return _attempt_evidence_status(
                input_id,
                attempts=attempts,
                outcomes_by_attempt=outcomes_by_attempt,
                source="input_attempt",
                path=[obs_id, input_id],
            )
        input_artifact = artifacts.get(input_id)
        if input_artifact is not None and getattr(input_artifact, "attempt_id", ""):
            return _attempt_evidence_status(
                getattr(input_artifact, "attempt_id", ""),
                attempts=attempts,
                outcomes_by_attempt=outcomes_by_attempt,
                source="input_artifact_attempt",
                path=[obs_id, input_id, getattr(input_artifact, "attempt_id", "")],
                artifact_id=input_id,
            )
        input_obs = observations.get(input_id)
        if input_obs is not None and getattr(input_obs, "attempt_id", ""):
            return _attempt_evidence_status(
                getattr(input_obs, "attempt_id", ""),
                attempts=attempts,
                outcomes_by_attempt=outcomes_by_attempt,
                source="input_observation_attempt",
                path=[obs_id, input_id, getattr(input_obs, "attempt_id", "")],
            )

    return {
        "trace_status": "untraceable",
        "successful": False,
        "source": "",
        "path": [obs_id] if obs_id else [],
        "reason": "No attempt, artifact attempt, or input provenance is attached to this observation.",
    }


def _attempt_evidence_status(
    attempt_id: str,
    *,
    attempts: dict[str, Any],
    outcomes_by_attempt: dict[str, Any],
    source: str,
    path: list[str],
    artifact_id: str = "",
) -> dict[str, Any]:
    base = {
        "attempt_id": attempt_id,
        "artifact_id": artifact_id,
        "source": source,
        "path": [item for item in path if item],
    }
    if attempt_id not in attempts:
        return {
            **base,
            "trace_status": "unknown_attempt",
            "successful": False,
            "reason": "Evidence references an attempt id that is not present in the run.",
        }
    outcome = outcomes_by_attempt.get(attempt_id)
    if outcome is None:
        return {
            **base,
            "trace_status": "missing_successful_outcome",
            "successful": False,
            "reason": "Evidence attempt has no recorded successful outcome.",
        }
    status = str(getattr(outcome, "status", "")).lower()
    return {
        **base,
        "outcome_id": getattr(outcome, "outcome_id", ""),
        "outcome_status": status,
        "trace_status": "ok" if status in SUCCESS_OUTCOME_STATUSES else "failed_outcome",
        "successful": status in SUCCESS_OUTCOME_STATUSES,
        "reason": "" if status in SUCCESS_OUTCOME_STATUSES else "Evidence attempt outcome was not successful.",
    }


def _review_conclusion(
    conclusion: Any,
    *,
    observations: dict[str, Any],
    attempts: dict[str, Any],
    artifacts: dict[str, Any],
    outcomes_by_attempt: dict[str, Any],
    stale_ids: set[str],
    graph: dict,
    limit: int,
) -> dict[str, Any]:
    support_checks = []
    for support_id in list(getattr(conclusion, "support_ids", []) or [])[:limit]:
        obs = observations.get(support_id)
        evidence = observation_evidence_status(
            obs,
            attempts=attempts,
            artifacts=artifacts,
            observations=observations,
            outcomes_by_attempt=outcomes_by_attempt,
        )
        support_checks.append({
            "support_id": support_id,
            "node_type": "observation" if obs is not None else "missing",
            "stale": support_id in stale_ids,
            "evidence": evidence,
            "ok": bool(obs is not None and evidence.get("successful") and support_id not in stale_ids),
        })
    missing = [item for item in support_checks if item["node_type"] == "missing"]
    unverified = [item for item in support_checks if item["node_type"] != "missing" and not item["evidence"].get("successful")]
    stale = [item for item in support_checks if item.get("stale")]
    ok = bool(support_checks) and not missing and not unverified and not stale and conclusion.conclusion_id not in stale_ids
    status = "ok" if ok else "unsupported" if not support_checks else "needs_review"
    if unverified:
        status = "unverified_evidence"
    if stale or conclusion.conclusion_id in stale_ids:
        status = "stale_evidence"
    if missing:
        status = "missing_support"
    checks = [
        {"check": "has_support_ids", "ok": bool(support_checks), "count": len(support_checks)},
        {"check": "support_ids_exist", "ok": not missing, "missing": [item["support_id"] for item in missing]},
        {"check": "support_evidence_successful", "ok": not unverified, "failed": _failed_support_summary(unverified)},
        {"check": "support_not_stale", "ok": not stale and conclusion.conclusion_id not in stale_ids, "stale_ids": _stale_summary(conclusion, stale, stale_ids)},
        {"check": "trace_node_available", "ok": _graph_has_node(graph, conclusion.conclusion_id)},
    ]
    return {
        "view_type": "evidence_chain_review",
        "node_id": conclusion.conclusion_id,
        "node_type": "conclusion",
        "found": True,
        "ok": ok,
        "status": status,
        "summary": _conclusion_review_summary(conclusion, status, support_checks),
        "conclusion": {
            "conclusion_id": conclusion.conclusion_id,
            "grade": getattr(conclusion, "grade", ""),
            "text": getattr(conclusion, "text", ""),
            "support_ids": list(getattr(conclusion, "support_ids", []) or [])[:limit],
            "limitation_ids": list(getattr(conclusion, "limitation_ids", []) or [])[:limit],
        },
        "support_checks": support_checks,
        "checks": checks,
        "next_actions": _review_next_actions(conclusion.conclusion_id, support_checks, status),
    }


def _review_observation(
    obs: Any,
    *,
    observations: dict[str, Any],
    attempts: dict[str, Any],
    artifacts: dict[str, Any],
    outcomes_by_attempt: dict[str, Any],
    stale_ids: set[str],
    graph: dict,
) -> dict[str, Any]:
    evidence = observation_evidence_status(
        obs,
        attempts=attempts,
        artifacts=artifacts,
        observations=observations,
        outcomes_by_attempt=outcomes_by_attempt,
    )
    stale = obs.observation_id in stale_ids
    ok = bool(evidence.get("successful") and not stale)
    status = "ok" if ok else "stale_evidence" if stale else "unverified_evidence"
    return {
        "view_type": "evidence_chain_review",
        "node_id": obs.observation_id,
        "node_type": "observation",
        "found": True,
        "ok": ok,
        "status": status,
        "summary": "Observation evidence is verified." if ok else "Observation evidence needs review before it supports a conclusion.",
        "observation": {
            "observation_id": obs.observation_id,
            "target": getattr(obs, "target", ""),
            "metric": getattr(obs, "metric", ""),
            "contrast": getattr(obs, "contrast", ""),
            "method": getattr(obs, "method", ""),
            "value": getattr(obs, "value", None),
            "attempt_id": getattr(obs, "attempt_id", ""),
            "artifact_id": getattr(obs, "artifact_id", ""),
            "input_ids": list(getattr(obs, "input_ids", []) or [])[:12],
        },
        "evidence": evidence,
        "checks": [
            {"check": "evidence_successful", "ok": bool(evidence.get("successful")), "trace_status": evidence.get("trace_status", "")},
            {"check": "not_stale", "ok": not stale, "stale": stale},
            {"check": "trace_node_available", "ok": _graph_has_node(graph, obs.observation_id)},
        ],
        "next_actions": _review_next_actions(obs.observation_id, [{"support_id": obs.observation_id, "stale": stale, "evidence": evidence}], status),
    }


def _review_artifact(artifact: Any, *, attempts: dict[str, Any], stale_ids: set[str], graph: dict) -> dict[str, Any]:
    attempt_id = getattr(artifact, "attempt_id", "")
    attempt_known = bool(attempt_id and attempt_id in attempts)
    stale = artifact.artifact_id in stale_ids
    ok = attempt_known and not stale
    return {
        "view_type": "evidence_chain_review",
        "node_id": artifact.artifact_id,
        "node_type": "artifact",
        "found": True,
        "ok": ok,
        "status": "ok" if ok else "needs_review",
        "summary": "Artifact has an attached attempt." if ok else "Artifact should be traced before being used as evidence.",
        "artifact": {
            "artifact_id": artifact.artifact_id,
            "attempt_id": attempt_id,
            "path": getattr(artifact, "path", ""),
            "kind": getattr(artifact, "kind", ""),
        },
        "checks": [
            {"check": "attempt_known", "ok": attempt_known, "attempt_id": attempt_id},
            {"check": "not_stale", "ok": not stale, "stale": stale},
            {"check": "trace_node_available", "ok": _graph_has_node(graph, artifact.artifact_id)},
        ],
        "next_actions": [{"tool": "trace_upstream", "args": {"node_id": artifact.artifact_id, "depth": 5}, "why": "expand artifact provenance"}],
    }


def _latest_review_target(snap: Any) -> str:
    conclusions = list(getattr(snap, "conclusions", []) or [])
    if conclusions:
        return getattr(conclusions[-1], "conclusion_id", "")
    observations = list(getattr(snap, "observations", []) or [])
    if observations:
        return getattr(observations[-1], "observation_id", "")
    return ""


def _stale_dependency_ids(snap: Any) -> set[str]:
    stale: set[str] = set()
    for finding in getattr(snap, "findings", []) or []:
        if getattr(finding, "finding_type", "") == "potentially_stale_dependency":
            stale.update(item for item in getattr(finding, "affected_ids", []) if item)
    return stale


def _graph_has_node(graph: dict | None, node_id: str) -> bool:
    if not graph:
        return False
    return any(node.get("node_id") == node_id for node in graph.get("nodes", []))


def _failed_support_summary(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "support_id": item.get("support_id", ""),
            "trace_status": item.get("evidence", {}).get("trace_status", ""),
            "attempt_id": item.get("evidence", {}).get("attempt_id", ""),
            "outcome_status": item.get("evidence", {}).get("outcome_status", ""),
            "reason": item.get("evidence", {}).get("reason", ""),
        }
        for item in items[:12]
    ]


def _stale_summary(conclusion: Any, stale_checks: list[dict[str, Any]], stale_ids: set[str]) -> list[str]:
    out = [item.get("support_id", "") for item in stale_checks if item.get("support_id")]
    if getattr(conclusion, "conclusion_id", "") in stale_ids:
        out.append(getattr(conclusion, "conclusion_id", ""))
    return out[:12]


def _conclusion_review_summary(conclusion: Any, status: str, support_checks: list[dict[str, Any]]) -> str:
    if status == "ok":
        return f"Conclusion {conclusion.conclusion_id} is backed by successful, non-stale support evidence."
    if status == "unsupported":
        return f"Conclusion {conclusion.conclusion_id} has no support ids."
    if status == "missing_support":
        return f"Conclusion {conclusion.conclusion_id} references missing support ids."
    if status == "unverified_evidence":
        return f"Conclusion {conclusion.conclusion_id} has support observations without successful execution evidence."
    if status == "stale_evidence":
        return f"Conclusion {conclusion.conclusion_id} depends on stale evidence."
    return f"Conclusion {conclusion.conclusion_id} has {len(support_checks)} support item(s) that need review."


def _review_next_actions(node_id: str, support_checks: list[dict[str, Any]], status: str) -> list[dict[str, Any]]:
    actions = [{"tool": "trace_upstream", "args": {"node_id": node_id, "depth": 5}, "why": "expand the full provenance path"}]
    if status in {"missing_support", "unsupported", "unverified_evidence", "stale_evidence"}:
        actions.append({"tool": "audit_run", "args": {}, "why": "see deterministic evidence-chain findings and repair actions"})
    for item in support_checks[:6]:
        support_id = item.get("support_id") or item.get("id")
        if support_id:
            actions.append({"tool": "trace_upstream", "args": {"node_id": support_id, "depth": 5}, "why": "inspect support evidence provenance"})
    if status == "stale_evidence":
        actions.append({"tool": "impact_of_change", "args": {"node_id": node_id, "depth": 5}, "why": "inspect downstream items that may need reinterpretation"})
    return _dedupe_actions(actions)[:10]


def _dedupe_actions(actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen = set()
    out = []
    for action in actions:
        key = (action.get("tool", ""), tuple(sorted((action.get("args") or {}).items())))
        if key in seen:
            continue
        seen.add(key)
        out.append(action)
    return out
