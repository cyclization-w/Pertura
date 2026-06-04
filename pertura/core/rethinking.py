"""Trace-driven rethinking plans for the LLM tool loop.

This is the compact bridge between audit/provenance views and the next
analysis action. It does not mutate state; it turns a suspicious result,
finding, observation, artifact, or conclusion into a bounded plan the LLM can
use to trace upstream, diagnose, and choose a repair/branch/intervention.
"""

from __future__ import annotations

from typing import Any

from pertura.core.audit import audit_run
from pertura.core.evidence_chain import review_evidence_chain
from pertura.core.graph import build_graph, impact_of_change, trace_upstream


def plan_rethinking(
    snap: Any,
    node_id: str = "",
    *,
    issue: str = "",
    depth: int = 5,
    graph: dict | None = None,
) -> dict[str, Any]:
    """Return a compact trace->diagnose->act plan for questionable results."""
    graph = graph or build_graph(snap)
    target_id = node_id or _infer_rethinking_target(snap)
    safe_depth = max(1, min(int(depth or 5), 8))
    if not target_id:
        audit = audit_run(snap, graph)
        return {
            "view_type": "rethinking_plan",
            "target_id": "",
            "issue": issue,
            "status": "no_target",
            "summary": "No conclusion, observation, artifact, attempt, trigger, or finding is available for trace-driven rethinking.",
            "evidence_review": {},
            "upstream_trace": {},
            "impact": {},
            "suspected_roots": [],
            "recommended_actions": _dedupe_actions([
                {"tool": "get_context_review", "args": {"purpose": "audit"}, "why": "inspect current run state"},
                {"tool": "audit_run", "args": {}, "why": "find the first concrete issue to rethink"},
                *_actions_from_audit(audit),
            ]),
        }

    evidence_review = review_evidence_chain(snap, target_id, graph=graph, limit=12)
    upstream = trace_upstream(graph, target_id, depth=safe_depth)
    impact = impact_of_change(graph, target_id, depth=safe_depth)
    audit = audit_run(snap, graph)
    roots = _suspected_roots(snap, graph, target_id, evidence_review, upstream, audit)
    actions = _recommended_actions(
        snap,
        target_id=target_id,
        issue=issue,
        evidence_review=evidence_review,
        upstream=upstream,
        impact=impact,
        audit=audit,
        roots=roots,
    )
    return {
        "view_type": "rethinking_plan",
        "target_id": target_id,
        "issue": issue,
        "status": _plan_status(evidence_review, audit, roots),
        "summary": _plan_summary(target_id, issue, evidence_review, roots),
        "evidence_review": _compact_evidence_review(evidence_review),
        "upstream_trace": _compact_walk(upstream),
        "impact": _compact_impact(impact),
        "suspected_roots": roots[:10],
        "recommended_actions": actions[:12],
        "policy": {
            "prefer": "data/method/root-cause checks before new biology stories",
            "do_not": "finish or report a questionable conclusion before evidence_review/audit_run are clean or limitations are explicit",
            "human_interrupt": "use ask_user only for authority-bound design semantics, external approvals, or exhausted autonomous recovery",
        },
    }


def _infer_rethinking_target(snap: Any) -> str:
    for finding in reversed(getattr(snap, "findings", []) or []):
        if getattr(finding, "severity", "") in {"blocking", "error", "high", "warning"}:
            affected = list(getattr(finding, "affected_ids", []) or [])
            if affected:
                return affected[0]
            if getattr(finding, "finding_id", ""):
                return finding.finding_id
    for trigger in reversed(getattr(snap, "triggers", []) or []):
        if getattr(trigger, "status", "") == "open" and getattr(trigger, "trigger_id", ""):
            return trigger.trigger_id
    conclusions = list(getattr(snap, "conclusions", []) or [])
    if conclusions:
        return getattr(conclusions[-1], "conclusion_id", "")
    observations = list(getattr(snap, "observations", []) or [])
    if observations:
        return getattr(observations[-1], "observation_id", "")
    artifacts = list(getattr(snap, "artifacts", []) or [])
    if artifacts:
        return getattr(artifacts[-1], "artifact_id", "")
    attempts = list(getattr(snap, "attempts", []) or [])
    if attempts:
        return getattr(attempts[-1], "attempt_id", "")
    return ""


def _suspected_roots(
    snap: Any,
    graph: dict,
    target_id: str,
    evidence_review: dict,
    upstream: dict,
    audit: dict,
) -> list[dict[str, Any]]:
    nodes = {node.get("node_id"): node for node in graph.get("nodes", [])}
    attempts = {item.attempt_id: item for item in getattr(snap, "attempts", [])}
    outcomes = {item.attempt_id: item for item in getattr(snap, "outcomes", [])}
    findings = {item.finding_id: item for item in getattr(snap, "findings", [])}
    roots: list[dict[str, Any]] = []

    def add(root_id: str, root_type: str, reason: str, priority: str = "medium", tool: str = "trace_upstream", args: dict | None = None):
        if not root_id:
            return
        node = nodes.get(root_id, {})
        roots.append({
            "root_id": root_id,
            "root_type": root_type or node.get("node_type", ""),
            "priority": priority,
            "reason": reason,
            "label": node.get("label", ""),
            "summary": node.get("summary", ""),
            "next_action": {"tool": tool, "args": args or {"node_id": root_id, "depth": 5}},
        })

    status = evidence_review.get("status", "")
    if status in {"missing_support", "unsupported"}:
        add(target_id, "conclusion", "Conclusion is not backed by registered support observations.", "high", "audit_run", {})
    if status == "unverified_evidence":
        for item in evidence_review.get("support_checks", [])[:6]:
            support_id = item.get("support_id", "")
            trace_status = (item.get("evidence") or {}).get("trace_status", "")
            if support_id:
                add(support_id, "observation", f"Support observation has unverified execution evidence: {trace_status}.", "high")
    if status == "stale_evidence":
        for stale_id in _stale_ids(snap):
            add(stale_id, "stale_dependency", "Evidence is marked stale by a changed upstream dependency.", "high", "impact_of_change", {"node_id": stale_id, "depth": 5})

    for node in upstream.get("nodes", [])[:20]:
        node_id = node.get("node_id", "")
        node_type = node.get("node_type", "")
        if node_type == "finding":
            finding = findings.get(node_id)
            severity = getattr(finding, "severity", node.get("status", ""))
            if severity in {"blocking", "error", "high", "warning"}:
                add(node_id, "finding", node.get("summary", "") or "Upstream finding may explain the suspicious result.", "high" if severity in {"blocking", "error", "high"} else "medium")
        elif node_type == "attempt":
            outcome = outcomes.get(node_id)
            if outcome is None:
                add(node_id, "attempt", "Upstream attempt has no recorded outcome.", "medium")
            else:
                outcome_status = str(getattr(outcome, "status", "")).lower()
                if outcome_status not in {"success", "succeeded", "completed"}:
                    add(node_id, "attempt", f"Upstream attempt outcome is {outcome_status}.", "high", "retry", {})
        elif node_type == "artifact" and node.get("status") in {"missing", "failed"}:
            add(node_id, "artifact", "Upstream artifact is missing or failed.", "high", "inspect_artifact_summary", {"artifact_id": node_id})

    for issue in [*(audit.get("errors", []) or []), *(audit.get("warnings", []) or [])]:
        details = issue.get("details", {}) or {}
        target = details.get("conclusion_id") or details.get("observation_id") or details.get("artifact_id") or details.get("attempt_id") or issue.get("target_id", "")
        if target and (target == target_id or target in {n.get("node_id") for n in upstream.get("nodes", [])}):
            add(target, "audit_issue", issue.get("message", issue.get("code", "")), "high" if issue.get("severity") == "error" else "medium", "audit_run", {})

    return _dedupe_roots(roots)


def _recommended_actions(
    snap: Any,
    *,
    target_id: str,
    issue: str,
    evidence_review: dict,
    upstream: dict,
    impact: dict,
    audit: dict,
    roots: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = [
        {"tool": "review_evidence_chain", "args": {"node_id": target_id, "limit": 12}, "why": "start with compact evidence validity before expanding the graph"},
        {"tool": "trace_upstream", "args": {"node_id": target_id, "depth": 5}, "why": "inspect direct upstream dependencies for data/method causes"},
    ]
    if evidence_review.get("status") in {"stale_evidence", "unverified_evidence", "missing_support", "unsupported"}:
        actions.append({"tool": "audit_run", "args": {}, "why": "get deterministic errors, warnings, and repair menu before rerun/report"})
    if evidence_review.get("status") == "stale_evidence":
        actions.append({"tool": "impact_of_change", "args": {"node_id": target_id, "depth": 5}, "why": "find downstream conclusions/branches affected by stale evidence"})
    if roots:
        root = roots[0]
        action = root.get("next_action") or {}
        if action.get("tool"):
            actions.append({**action, "why": root.get("reason", "inspect suspected root cause")})
    for action in _actions_from_audit(audit)[:6]:
        actions.append(action)
    for action in evidence_review.get("next_actions", [])[:6]:
        actions.append(action)

    issue_l = issue.lower()
    if any(term in issue_l for term in ("empty", "negative", "no result", "weak")):
        actions.append({"tool": "get_node_contract", "args": {}, "why": "check whether current node supports aggregation, alternative method, or branch pivot"})
        actions.append({"tool": "open_branch", "args": {"question": issue or "unproductive negative result", "reason": "negative_or_weak_result_pivot"}, "why": "branch only if the current negative/weak result blocks progress"})
    if any(term in issue_l for term in ("wrong", "contrast", "batch", "control", "guide")):
        actions.append({"tool": "get_node_contract", "args": {}, "why": "inspect design/input requirements before changing parameters"})
        actions.append({"tool": "query_observation_memory", "args": {}, "why": "compare prior values across methods/contrasts/branches before rerun"})
    if impact.get("affected", {}).get("by_type", {}).get("conclusion"):
        actions.append({"tool": "impact_of_change", "args": {"node_id": target_id, "depth": 5}, "why": "prioritize downstream conclusions that may need re-interpretation"})
    return _dedupe_actions(actions)


def _actions_from_audit(audit: dict) -> list[dict[str, Any]]:
    out = []
    for item in audit.get("next_actions", []) or []:
        tool = item.get("tool", "")
        if not tool:
            continue
        out.append({
            "tool": tool,
            "args": item.get("args", {}) or {},
            "why": item.get("reason") or item.get("message") or f"follow audit issue {item.get('issue_code', '')}",
        })
    return out


def _compact_evidence_review(review: dict) -> dict[str, Any]:
    keys = ["view_type", "node_id", "node_type", "found", "ok", "status", "summary", "checks", "next_actions"]
    out = {key: review.get(key) for key in keys if key in review}
    if "support_checks" in review:
        out["support_checks"] = review.get("support_checks", [])[:8]
    if "evidence" in review:
        out["evidence"] = review.get("evidence")
    return out


def _compact_walk(walk: dict) -> dict[str, Any]:
    nodes = walk.get("nodes", [])
    edges = walk.get("edges", [])
    return {
        "start_node_id": walk.get("start_node_id", ""),
        "direction": walk.get("direction", ""),
        "depth": walk.get("depth", 0),
        "node_count": len(nodes),
        "edge_count": len(edges),
        "nodes": [
            {
                "node_id": node.get("node_id", ""),
                "node_type": node.get("node_type", ""),
                "label": node.get("label", ""),
                "summary": node.get("summary", ""),
                "status": node.get("status", ""),
            }
            for node in nodes[:12]
        ],
        "relation_summary": walk.get("relation_summary", {}),
    }


def _compact_impact(impact: dict) -> dict[str, Any]:
    affected = impact.get("affected", {}) or {}
    return {
        "start_node_id": impact.get("start_node_id", ""),
        "direction": impact.get("direction", ""),
        "affected_by_type": affected.get("by_type", {}),
        "key_nodes": {
            key: value[:8]
            for key, value in (affected.get("key_nodes", {}) or {}).items()
            if value
        },
        "impact_actions": affected.get("impact_actions", [])[:8],
    }


def _plan_status(evidence_review: dict, audit: dict, roots: list[dict[str, Any]]) -> str:
    if evidence_review.get("status") in {"stale_evidence", "unverified_evidence", "missing_support", "unsupported"}:
        return "needs_trace_driven_repair"
    if roots:
        return "needs_root_cause_review"
    if audit.get("summary", {}).get("errors", 0):
        return "audit_errors_present"
    return "review_then_continue"


def _plan_summary(target_id: str, issue: str, evidence_review: dict, roots: list[dict[str, Any]]) -> str:
    bits = [f"Rethink `{target_id}`"]
    if issue:
        bits.append(f"because: {issue}")
    if evidence_review.get("status"):
        bits.append(f"evidence status: {evidence_review.get('status')}")
    if roots:
        bits.append(f"top suspected root: {roots[0].get('root_id')} ({roots[0].get('reason')})")
    return "; ".join(bits) + "."


def _stale_ids(snap: Any) -> set[str]:
    stale: set[str] = set()
    for finding in getattr(snap, "findings", []) or []:
        if getattr(finding, "finding_type", "") == "potentially_stale_dependency":
            stale.update(item for item in getattr(finding, "affected_ids", []) if item)
    return stale


def _dedupe_roots(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen = set()
    out = []
    priority_rank = {"high": 0, "medium": 1, "low": 2}
    for item in sorted(items, key=lambda value: (priority_rank.get(value.get("priority", "medium"), 1), value.get("root_id", ""))):
        key = (item.get("root_id", ""), item.get("root_type", ""), item.get("reason", ""))
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def _dedupe_actions(actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen = set()
    out = []
    for action in actions:
        args = action.get("args", {}) or {}
        try:
            args_key = tuple(sorted(args.items()))
        except TypeError:
            args_key = tuple(sorted((key, str(value)) for key, value in args.items()))
        key = (action.get("tool", ""), args_key)
        if key in seen:
            continue
        seen.add(key)
        out.append(action)
    return out
