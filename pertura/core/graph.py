"""Deterministic graph projection from Snapshot + graph validation."""

from __future__ import annotations

from pertura.models import Snapshot
from pertura.core.relations import edge_propagates_change, enrich_edge, relation_summary


_ALLOWED_EDGES = {
    "contains": set(), "depends_on": set(), "informs": set(), "supersedes": set(),
    "derived_from": set(), "contradicts": set(), "reruns": set(),
    "next_node": {("analysis_node", "analysis_node")},
    "runs_in": {("analysis_node", "attempt")},
    "produces": {("attempt", "artifact"), ("attempt", "observation")},
    "summarizes": {("attempt", "outcome")},
    "triggers": {("attempt", "trigger"), ("outcome", "trigger")},
    "branches_from": {("branch", "branch")},
    "supports": {("observation", "conclusion"), ("artifact", "conclusion")},
    "limits": {("finding", "conclusion"), ("trigger", "conclusion")},
    "observes": {("artifact", "observation")},
    "uses_tool": {("attempt", "tool_call")},
    "decides": {("attempt", "review_decision")},
}


def build_graph(snap: Snapshot) -> dict:
    nodes, edges = [], []
    known_node_ids = set()

    def n(id, type, label, summary="", status="", meta=None):
        known_node_ids.add(id)
        nodes.append({"node_id": id, "node_type": type, "label": label,
                      "summary": summary, "status": status, "metadata": meta or {}})

    def e(src, tgt, etype):
        if src and tgt:
            edges.append(enrich_edge({"source_id": src, "target_id": tgt, "edge_type": etype}))

    n("root", "workspace", snap.workspace or "Workspace", status=snap.phase)
    spec_nodes = {}
    for node in (snap.analysis_spec or {}).get("nodes", []):
        node_id = node.get("node_id", "")
        if not node_id:
            continue
        visit = _latest_visit(snap, node_id)
        status = visit.status if visit else "not_started"
        n(node_id, "analysis_node", node.get("title") or node_id,
          summary=node.get("purpose", ""), status=status,
          meta={
              "allowed_capabilities": node.get("allowed_capabilities", []),
              "recommended_actions": node.get("recommended_actions", []),
              "expected_outputs": node.get("expected_outputs", []),
          })
        e("root", node_id, "contains")
        spec_nodes[node_id] = node
    for node_id, node in spec_nodes.items():
        for target in node.get("next_nodes", []) or []:
            if target in spec_nodes:
                e(node_id, target, "next_node")
    for edge in (snap.analysis_spec or {}).get("edges", []):
        if edge.get("source") in spec_nodes and edge.get("target") in spec_nodes:
            e(edge.get("source"), edge.get("target"), "next_node")
    for b in snap.branches:
        n(b.branch_id, "branch", b.title, status=b.status,
          summary=b.question or b.reason,
          meta={"question": b.question, "hypothesis": b.hypothesis,
                "reason": b.reason, "summary": b.summary,
                "conclusion": b.conclusion, "evidence_ids": b.evidence_ids})
        e("root", b.branch_id, "contains")
        if b.parent_id:
            e(b.parent_id, b.branch_id, "branches_from")
    for a in snap.attempts:
        n(a.attempt_id, "attempt", a.title, summary=a.objective, status=a.status,
          meta={"stage": a.stage, "parameters": a.parameters,
                "capability_ids": a.capability_ids,
                "parent_ids": a.parent_ids,
                "parent_intervention": a.parent_intervention,
                "repair_count": a.repair_count})
        e(a.branch_id, a.attempt_id, "contains")
        if a.analysis_node_id:
            e(a.analysis_node_id, a.attempt_id, "runs_in")
        for pid in a.parent_ids:
            e(pid, a.attempt_id, _attempt_parent_edge_type(a))
    for o in snap.outcomes:
        n(o.outcome_id, "outcome", o.status, summary=o.summary, status=o.status)
        e(o.attempt_id, o.outcome_id, "summarizes")
    for a in snap.artifacts:
        n(a.artifact_id, "artifact", a.kind, summary=a.summary,
          meta={"path": a.path, "metadata": a.metadata})
        if a.attempt_id:
            e(a.attempt_id, a.artifact_id, "produces")
        for input_id in _as_list(a.metadata.get("input_ids")):
            if input_id in known_node_ids and input_id != a.artifact_id:
                e(input_id, a.artifact_id, "derived_from")
    for obs in snap.observations:
        n(obs.observation_id, "observation", f"{obs.type}:{obs.target}",
          summary=f"{obs.metric}={obs.value}", status=obs.type,
          meta={"target": obs.target, "metric": obs.metric,
                "value": obs.value, "contrast": obs.contrast,
                "method": obs.method, "parameters": obs.parameters,
                "uncertainty": obs.uncertainty,
                "variable_key": obs.variable_key,
                "input_ids": obs.input_ids,
                "artifact_id": obs.artifact_id,
                "parameter_hash": obs.parameter_hash,
                "method_version": obs.method_version})
        e(obs.attempt_id, obs.observation_id, "produces")
        if obs.artifact_id:
            e(obs.artifact_id, obs.observation_id, "observes")
        for input_id in obs.input_ids:
            if input_id in known_node_ids and input_id != obs.observation_id:
                e(input_id, obs.observation_id, "derived_from")
    for t in snap.triggers:
        n(t.trigger_id, "trigger", t.trigger_type, summary=t.summary, status=t.status)
        if t.attempt_id:
            e(t.attempt_id, t.trigger_id, "triggers")
    for f in snap.findings:
        n(f.finding_id, "finding", f.finding_type,
          summary=f.summary, status=f.severity,
          meta={"suggested_action": f.suggested_action,
                "affected_ids": f.affected_ids})
        if f.attempt_id:
            e(f.attempt_id, f.finding_id, "informs")
    for br in snap.behavior_runs:
        n(br.behavior_run_id, "behavior_run", br.behavior_id,
          summary=br.error or f"{br.output_count} output event(s)",
          status=br.status,
          meta={"trigger_event_ids": br.trigger_event_ids,
                "output_event_ids": br.output_event_ids})
    for c in snap.conclusions:
        n(c.conclusion_id, "conclusion", c.grade, summary=c.text)
        for sid in c.support_ids:
            e(sid, c.conclusion_id, "supports")
        for lid in c.limitation_ids:
            e(lid, c.conclusion_id, "limits")
    for tc in snap.tool_calls:
        n(tc.tool_call_id, "tool_call", tc.tool_name, summary=tc.result_summary)
        if tc.attempt_id:
            e(tc.attempt_id, tc.tool_call_id, "uses_tool")
    for rd in snap.review_decisions:
        n(rd.review_id, "review_decision", rd.action,
          summary=rd.assessment_summary, status=rd.assessment_status)
        if rd.attempt_id and any(a.attempt_id == rd.attempt_id for a in snap.attempts):
            e(rd.attempt_id, rd.review_id, "decides")

    return {"run_id": snap.run_id,
            "nodes": _dedupe(nodes, "node_id"),
            "edges": _dedupe(edges, None,
                           key_fn=lambda e: f"{e['source_id']}|{e['target_id']}|{e['edge_type']}")}


def trace_upstream(graph: dict, node_id: str, *, depth: int = 4) -> dict:
    """Return upstream nodes and edges feeding a node."""
    nodes = {n["node_id"]: n for n in graph.get("nodes", [])}
    incoming: dict[str, list[dict]] = {}
    for edge in graph.get("edges", []):
        incoming.setdefault(edge["target_id"], []).append(edge)
    return _walk_graph(nodes, incoming, node_id, depth, direction="upstream")


def impact_of_change(graph: dict, node_id: str, *, depth: int = 4) -> dict:
    """Return downstream nodes likely affected by a changed node."""
    nodes = {n["node_id"]: n for n in graph.get("nodes", [])}
    outgoing: dict[str, list[dict]] = {}
    for edge in graph.get("edges", []):
        outgoing.setdefault(edge["source_id"], []).append(edge)
    return _walk_graph(nodes, outgoing, node_id, depth, direction="downstream", impact_only=True)


def validate_graph(graph: dict) -> list[str]:
    """Validate edge type / node type consistency. Returns list of violations."""
    violations = []
    node_types = {n["node_id"]: n["node_type"] for n in graph.get("nodes", [])}
    for edge in graph.get("edges", []):
        src = edge.get("source_id", "")
        tgt = edge.get("target_id", "")
        etype = edge.get("edge_type", "")
        if src not in node_types:
            violations.append(f"Edge source not found: {src}")
        if tgt not in node_types:
            violations.append(f"Edge target not found: {tgt}")
        if etype in _ALLOWED_EDGES and _ALLOWED_EDGES[etype]:
            stype = node_types.get(src, "")
            ttype = node_types.get(tgt, "")
            if (stype, ttype) not in _ALLOWED_EDGES[etype]:
                violations.append(f"Illegal edge: {stype} -[{etype}]-> {ttype}")
    return violations


def graph_violations_to_findings(violations: list[str]) -> list[dict]:
    """Convert graph violations into Finding payloads for event recording."""
    findings = []
    for i, v in enumerate(violations):
        findings.append({
            "finding_id": f"fnd_graph_{i}",
            "finding_type": "graph_violation",
            "severity": "warning",
            "suggested_action": "review",
            "summary": v,
        })
    return findings


def _dedupe(items, key_field=None, *, key_fn=None):
    seen = set()
    result = []
    for item in items:
        k = key_fn(item) if key_fn else item[key_field] if key_field else str(item)
        if k not in seen:
            seen.add(k); result.append(item)
    return result


def _attempt_parent_edge_type(attempt) -> str:
    intervention = (attempt.parent_intervention or "").lower()
    if attempt.repair_count > 0 or "retry" in intervention or "rerun" in intervention:
        return "reruns"
    return "depends_on"


def _as_list(value) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def _latest_visit(snap: Snapshot, node_id: str):
    for visit in reversed(snap.node_visits):
        if visit.node_id == node_id and visit.branch_id == snap.active_branch:
            return visit
    return None


def _walk_graph(nodes: dict, adjacency: dict, start: str, depth: int, *, direction: str,
                impact_only: bool = False) -> dict:
    visited = {start}
    frontier = [(start, 0)]
    out_edges = []
    while frontier:
        current, dist = frontier.pop(0)
        if dist >= depth:
            continue
        for edge in adjacency.get(current, []):
            if impact_only and not edge_propagates_change(edge):
                continue
            neighbor = edge["source_id"] if direction == "upstream" else edge["target_id"]
            out_edges.append(edge)
            if neighbor not in visited:
                visited.add(neighbor)
                frontier.append((neighbor, dist + 1))
    deduped_edges = _dedupe(out_edges, None, key_fn=lambda e: f"{e['source_id']}|{e['target_id']}|{e['edge_type']}")
    return {
        "start_node_id": start,
        "direction": direction,
        "depth": depth,
        "nodes": [nodes[node_id] for node_id in visited if node_id in nodes],
        "edges": deduped_edges,
        "relation_summary": relation_summary(deduped_edges),
        "affected": _affected_summary(nodes, visited, deduped_edges) if direction == "downstream" else {},
    }


def _affected_summary(nodes: dict, visited: set[str], edges: list[dict]) -> dict:
    affected_nodes = [nodes[node_id] for node_id in visited if node_id in nodes]
    by_type: dict[str, int] = {}
    key_nodes: dict[str, list[dict]] = {
        "attempts": [],
        "artifacts": [],
        "observations": [],
        "conclusions": [],
        "triggers": [],
        "findings": [],
        "branches": [],
    }
    type_to_bucket = {
        "attempt": "attempts",
        "artifact": "artifacts",
        "observation": "observations",
        "conclusion": "conclusions",
        "trigger": "triggers",
        "finding": "findings",
        "branch": "branches",
    }
    for node in affected_nodes:
        node_type = node.get("node_type", "unknown")
        by_type[node_type] = by_type.get(node_type, 0) + 1
        bucket = type_to_bucket.get(node_type)
        if bucket:
            key_nodes[bucket].append({
                "node_id": node.get("node_id"),
                "label": node.get("label", ""),
                "summary": node.get("summary", ""),
                "status": node.get("status", ""),
            })
    impacts = []
    for edge in edges:
        effect = edge.get("effect", {})
        impact = effect.get("impact", "")
        if impact and impact not in impacts:
            impacts.append(impact)
    return {
        "by_type": by_type,
        "key_nodes": key_nodes,
        "impact_actions": impacts,
    }
