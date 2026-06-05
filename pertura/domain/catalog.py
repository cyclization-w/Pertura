"""Developer-facing domain browser payloads."""

from __future__ import annotations

from typing import Any

from pertura.spec.models import ConditionSpec, spec_from_dict


def describe_domain(domain, *, include_core_tools: bool = True) -> dict[str, Any]:
    """Return a compact, GUI/CLI-friendly description of a domain pack."""
    graph = spec_from_dict(getattr(domain, "analysis_graph", None))
    registry = domain.registry()
    known_design_fields = _domain_design_fields(domain)
    explicit_capability_ids = {
        item.get("capability_id") or item.get("id")
        for item in (getattr(domain, "capabilities", []) or [])
        if isinstance(item, dict)
    }
    used_by = _capability_usage(graph)
    capability_cards = []
    for cap_id in registry.ids():
        cap = registry.get(cap_id)
        if cap is None:
            continue
        card = cap.compact()
        card["used_by_nodes"] = used_by.get(cap_id, [])
        card["source"] = "domain" if cap_id in explicit_capability_ids else "auto_filled_from_graph"
        card["implementation_tools"] = _implementation_tools(card.get("tools", []))
        capability_cards.append(card)

    nodes = []
    design_fields = set()
    rubric_only = []
    hard_conditions = []
    if graph is not None:
        for node in graph.nodes:
            condition_groups = {
                "requires": [_condition_card(item, known_design_fields=known_design_fields) for item in node.requires],
                "must_confirm": [_condition_card(item, known_design_fields=known_design_fields) for item in node.must_confirm],
                "completion": [_condition_card(item, known_design_fields=known_design_fields) for item in node.completion],
            }
            for group, cards in condition_groups.items():
                for card in cards:
                    design_fields.update(card.get("design_fields", []))
                    if card.get("evaluator_id") == "rubric_only":
                        rubric_only.append({**card, "node_id": node.node_id, "group": group})
                    if card.get("hard"):
                        hard_conditions.append({**card, "node_id": node.node_id, "group": group})
            nodes.append({
                "node_id": node.node_id,
                "title": node.title,
                "purpose": node.purpose,
                "allowed_capabilities": list(node.allowed_capabilities),
                "conditions": condition_groups,
                "recommended_actions": list(node.recommended_actions),
                "expected_outputs": list(node.expected_outputs),
                "next_nodes": list(node.next_nodes),
                "strict_edges": node.strict_edges,
            })

    design_fields.update(known_design_fields)
    design_fields.update(_design_fields_from_capabilities(capability_cards, known_design_fields=known_design_fields))
    payload = {
        "catalog_type": "domain_browser",
        "domain": {
            "name": domain.name,
            "has_analysis_graph": graph is not None,
            "graph_id": graph.graph_id if graph else "",
            "start_node_id": graph.start_node_id if graph else "",
        },
        "concepts": {
            "analysis_node": "A user/domain-authored stage contract that guides navigation and gates.",
            "capability": "A domain action contract exposed to the LLM and users, such as run_de.",
            "tool": "A core runtime primitive, such as execute_code or get_context_review.",
            "design": "Run-level domain facts with provenance, such as user-confirmed controls or sample metadata.",
            "condition": "An executable or rubric-only gate over design, observations, artifacts, and runtime state.",
        },
        "summary": {
            "nodes": len(nodes),
            "capabilities": len(capability_cards),
            "design_fields": len(design_fields),
            "hard_conditions": len(hard_conditions),
            "rubric_only_conditions": len(rubric_only),
        },
        "nodes": nodes,
        "capabilities": capability_cards,
        "capabilities_by_node": {
            node["node_id"]: node["allowed_capabilities"]
            for node in nodes
        },
        "design": {
            "fields": sorted(design_fields),
            "source_policy": {
                "pi_confirmed": "Human/PI authority.",
                "api_confirmed": "Structured user/API input.",
                "data_observed": "Observed from workspace/data audit.",
                "llm_inferred": "LLM inference; useful context but should not satisfy C-tier authority gates by itself.",
            },
        },
        "conditions": {
            "hard": hard_conditions,
            "rubric_only": rubric_only,
        },
        "core_tools": _core_tools() if include_core_tools else [],
    }
    return payload


def _capability_usage(graph) -> dict[str, list[str]]:
    usage: dict[str, list[str]] = {}
    if graph is None:
        return usage
    for node in graph.nodes:
        for cap_id in node.allowed_capabilities:
            usage.setdefault(cap_id, []).append(node.node_id)
    return usage


def _implementation_tools(tool_ids: list[str]) -> list[dict[str, str]]:
    from pertura.tools import tool_permission

    return [
        {"tool_id": tool_id, "permission": tool_permission(tool_id).value}
        for tool_id in tool_ids
    ]


def _core_tools() -> list[dict[str, Any]]:
    from pertura.tools import tool_catalog

    return tool_catalog()


def _condition_card(condition: ConditionSpec, *, known_design_fields: set[str]) -> dict[str, Any]:
    design_fields = []
    field = condition.inputs.get("field")
    if field:
        design_fields.append(str(field))
    for value in condition.inputs.values():
        if isinstance(value, list):
            design_fields.extend(str(item) for item in value if _looks_like_design_field(str(item), known_design_fields))
        elif _looks_like_design_field(str(value), known_design_fields):
            design_fields.append(str(value))
    return {
        "condition_id": condition.condition_id,
        "evaluator_id": condition.evaluator_id,
        "tier": condition.tier,
        "failure_mode": condition.failure_mode,
        "hard": condition.hard,
        "description": condition.description,
        "message": condition.message,
        "inputs": dict(condition.inputs),
        "design_fields": sorted(set(design_fields)),
    }


def _design_fields_from_capabilities(cards: list[dict[str, Any]], *, known_design_fields: set[str]) -> set[str]:
    fields = set()
    for card in cards:
        for item in card.get("required_inputs", []) or []:
            if _looks_like_design_field(str(item), known_design_fields):
                fields.add(item)
    return fields


def _domain_design_fields(domain) -> set[str]:
    metadata = getattr(domain, "metadata", {}) or {}
    fields = metadata.get("design_fields", [])
    if isinstance(fields, dict):
        fields = fields.keys()
    return {str(item) for item in fields if str(item)}


def _looks_like_design_field(value: str, known_design_fields: set[str]) -> bool:
    if value in known_design_fields:
        return True
    return value.endswith("_column") or value.endswith("_labels")
