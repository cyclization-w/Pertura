"""Deterministic relation semantics for the scientific graph.

The graph remains a projection, not a workflow DAG. Relation effects give a
small amount of machine-readable meaning to edges so LLM/tool views can explain
why an upstream change matters without introducing a separate gate system.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RelationEffect:
    edge_type: str
    category: str
    impact: str
    propagates_change: bool
    summary: str

    def as_dict(self) -> dict:
        return {
            "edge_type": self.edge_type,
            "category": self.category,
            "impact": self.impact,
            "propagates_change": self.propagates_change,
            "summary": self.summary,
        }


RELATION_EFFECTS: dict[str, RelationEffect] = {
    "contains": RelationEffect(
        "contains", "structural", "context_only", False,
        "Parent contains child; useful for navigation but not a scientific dependency.",
    ),
    "informs": RelationEffect(
        "informs", "review", "review_attention", True,
        "Source informs a finding or decision; changes should be reviewed.",
    ),
    "depends_on": RelationEffect(
        "depends_on", "dependency", "recompute_or_recheck", True,
        "Target depends on source; source changes can invalidate target interpretation.",
    ),
    "derived_from": RelationEffect(
        "derived_from", "derivation", "recompute_value", True,
        "Target value was derived from source value or artifact.",
    ),
    "produces": RelationEffect(
        "produces", "execution", "recompute_output", True,
        "Attempt produced the target output.",
    ),
    "summarizes": RelationEffect(
        "summarizes", "execution", "review_outcome", True,
        "Outcome summarizes an attempt execution.",
    ),
    "observes": RelationEffect(
        "observes", "extraction", "reextract_observation", True,
        "Observation was extracted from an artifact.",
    ),
    "supports": RelationEffect(
        "supports", "scientific_support", "reconsider_conclusion", True,
        "Source supports a conclusion; changes affect conclusion weight.",
    ),
    "limits": RelationEffect(
        "limits", "scientific_limitation", "reconsider_conclusion", True,
        "Source limits a conclusion; changes affect reportability or confidence.",
    ),
    "contradicts": RelationEffect(
        "contradicts", "scientific_conflict", "resolve_conflict", True,
        "Source contradicts target; changes should trigger conflict review.",
    ),
    "triggers": RelationEffect(
        "triggers", "review", "diagnose_issue", True,
        "Source triggered a review issue.",
    ),
    "reruns": RelationEffect(
        "reruns", "lineage", "compare_rerun", True,
        "Target reruns or repairs source; compare old and new outputs.",
    ),
    "branches_from": RelationEffect(
        "branches_from", "branching", "compare_branch", True,
        "Target branch forked from source branch.",
    ),
    "uses_tool": RelationEffect(
        "uses_tool", "execution", "check_tool_assumption", True,
        "Attempt used a tool call; tool behavior can affect outputs.",
    ),
    "decides": RelationEffect(
        "decides", "decision", "review_decision", True,
        "Attempt produced a review/planning decision.",
    ),
    "next_node": RelationEffect(
        "next_node", "analysis_spec", "check_transition_gate", False,
        "Analysis node can transition to the target node by spec.",
    ),
    "runs_in": RelationEffect(
        "runs_in", "analysis_spec", "review_node_context", True,
        "Attempt was executed inside an analysis node.",
    ),
    "supersedes": RelationEffect(
        "supersedes", "versioning", "prefer_newer_or_compare", True,
        "Source supersedes target; downstream consumers should prefer or compare source.",
    ),
}


def relation_effect(edge_type: str) -> RelationEffect:
    return RELATION_EFFECTS.get(edge_type, RelationEffect(
        edge_type=edge_type,
        category="unknown",
        impact="review",
        propagates_change=True,
        summary="Unknown relation; treat as reviewable dependency.",
    ))


def enrich_edge(edge: dict) -> dict:
    enriched = dict(edge)
    enriched["effect"] = relation_effect(edge.get("edge_type", "")).as_dict()
    return enriched


def edge_propagates_change(edge: dict) -> bool:
    effect = edge.get("effect")
    if isinstance(effect, dict):
        return bool(effect.get("propagates_change", True))
    return relation_effect(edge.get("edge_type", "")).propagates_change


def relation_summary(edges: list[dict]) -> dict:
    by_category: dict[str, int] = {}
    by_impact: dict[str, int] = {}
    for edge in edges:
        effect = edge.get("effect") or relation_effect(edge.get("edge_type", "")).as_dict()
        category = effect.get("category", "unknown")
        impact = effect.get("impact", "review")
        by_category[category] = by_category.get(category, 0) + 1
        by_impact[impact] = by_impact.get(impact, 0) + 1
    return {"by_category": by_category, "by_impact": by_impact}
