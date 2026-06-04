"""Readable contracts for public analysis nodes.

An analysis graph is executable by the runtime, but external users also need a
stable way to inspect what each node means before running an agent. These
helpers compile an AnalysisGraphSpec plus a capability registry into a compact
human/LLM-readable contract.
"""

from __future__ import annotations

from typing import Any, Iterable

from pertura.capabilities import Capability, CapabilityRegistry
from pertura.spec.models import (
    AnalysisGraphSpec,
    AnalysisNodeSpec,
    ConditionSpec,
    spec_from_dict,
    validate_analysis_graph,
)
from pertura.spec.conditions import CONDITION_CHECKS


CapabilityInput = CapabilityRegistry | Iterable[Capability | dict[str, Any]] | object


def node_contract(
    spec: AnalysisGraphSpec | dict[str, Any],
    node_id: str,
    *,
    capabilities: CapabilityInput = (),
) -> dict[str, Any]:
    """Return a public contract for one analysis node.

    The contract is intentionally independent from a Workbench run. It can be
    exported in docs, used by a GUI, or injected into an LLM dashboard.
    """
    graph = spec_from_dict(spec)
    if graph is None:
        raise ValueError("Cannot build node contract for empty analysis graph.")
    node = graph.node(node_id)
    if node is None:
        raise ValueError(f"Analysis node not found: {node_id}")
    registry = _as_registry(capabilities)
    capability_cards = [_capability_card(cap_id, registry) for cap_id in node.allowed_capabilities]
    required_inputs = _unique(
        item
        for card in capability_cards
        if not card.get("missing")
        for item in card.get("required_inputs", [])
    )
    expected_observations = _unique(
        item
        for card in capability_cards
        if not card.get("missing")
        for item in card.get("expected_observations", [])
    )
    expected_artifacts = _unique(
        item
        for card in capability_cards
        if not card.get("missing")
        for item in card.get("expected_artifacts", [])
    )
    conditions = [*node.requires, *node.must_confirm, *node.completion]
    input_profile = _input_profile(required_inputs, conditions)
    return {
        "contract_type": "analysis_node_contract",
        "graph_id": graph.graph_id,
        "version": graph.version,
        "node": _node_card(node, graph),
        "navigation": _navigation_card(graph, node),
        "gates": {
            "requires": [_condition_card(item) for item in node.requires],
            "must_confirm": [_condition_card(item) for item in node.must_confirm],
            "completion": [_condition_card(item) for item in node.completion],
        },
        "capabilities": capability_cards,
        "missing_capabilities": [card["id"] for card in capability_cards if card.get("missing")],
        "inputs": input_profile,
        "outputs": {
            "expected_outputs": list(node.expected_outputs),
            "expected_observations": expected_observations,
            "expected_artifacts": expected_artifacts,
        },
        "actions": {
            "recommended": list(node.recommended_actions),
            "template_calls": _template_calls(capability_cards),
            "commit_tools": _unique(
                tool
                for card in capability_cards
                if not card.get("missing")
                for tool in card.get("tools", [])
            ),
        },
        "audit_checklist": _audit_checklist(node, capability_cards),
        "quality": _quality_summary(node, capability_cards),
    }


def graph_contract(
    spec: AnalysisGraphSpec | dict[str, Any],
    *,
    capabilities: CapabilityInput = (),
) -> dict[str, Any]:
    """Return contracts for every node in an analysis graph."""
    graph = spec_from_dict(spec)
    if graph is None:
        raise ValueError("Cannot build graph contract for empty analysis graph.")
    registry = _as_registry(capabilities)
    node_cards = [
        node_contract(graph, node.node_id, capabilities=registry)
        for node in graph.nodes
    ]
    missing = _unique(
        cap_id
        for node in node_cards
        for cap_id in node.get("missing_capabilities", [])
    )
    return {
        "contract_type": "analysis_graph_contract",
        "graph_id": graph.graph_id,
        "version": graph.version,
        "start_node_id": graph.start_node_id,
        "node_count": len(graph.nodes),
        "edge_count": len(graph.edges),
        "missing_capabilities": missing,
        "nodes": node_cards,
    }


def audit_analysis_graph(
    spec: AnalysisGraphSpec | dict[str, Any],
    *,
    capabilities: CapabilityInput = (),
    strict: bool = False,
) -> dict[str, Any]:
    """Audit graph semantics beyond structural validation.

    This is the user-facing quality gate for authored analysis harnesses. It
    checks whether nodes have executable gates, declared capabilities, expected
    outputs, and enough contract information for an LLM/operator to understand
    what a node is allowed to do.
    """
    graph = spec_from_dict(spec)
    if graph is None:
        raise ValueError("Cannot audit empty analysis graph.")
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    try:
        validate_analysis_graph(graph)
    except Exception as exc:
        errors.append(_issue("invalid_graph", "graph", str(exc), severity="error"))

    registry = _audit_registry(capabilities)
    node_reports = []
    missing_capabilities = []
    rubric_only_conditions = []
    unknown_evaluators = []
    incoming = _incoming_node_ids(graph)

    for node in graph.nodes:
        node_errors: list[dict[str, Any]] = []
        node_warnings: list[dict[str, Any]] = []
        if not node.title:
            node_warnings.append(_issue("missing_title", node.node_id, "Node has no title."))
        if not node.purpose:
            node_warnings.append(_issue("missing_purpose", node.node_id, "Node has no purpose."))
        if node.node_id != graph.start_node_id and node.node_id not in incoming:
            node_warnings.append(_issue(
                "unreachable_node",
                node.node_id,
                "Node is not referenced by any next_nodes or edge.",
            ))
        if not node.allowed_capabilities:
            node_warnings.append(_issue(
                "no_allowed_capabilities",
                node.node_id,
                "Node has no action vocabulary; add at least one capability.",
            ))
        if not node.completion:
            node_warnings.append(_issue(
                "missing_completion",
                node.node_id,
                "Node has no completion condition; add done_when/completion criteria.",
            ))
        condition_report = _audit_conditions(node)
        node_warnings.extend(condition_report["warnings"])
        node_errors.extend(condition_report["errors"])
        rubric_only_conditions.extend(condition_report["rubric_only"])
        unknown_evaluators.extend(condition_report["unknown_evaluators"])

        cap_cards = []
        for cap_id in node.allowed_capabilities:
            cap = registry.get(cap_id)
            if cap is None:
                issue = _issue(
                    "missing_capability",
                    node.node_id,
                    f"Node references capability `{cap_id}` but it is not registered.",
                    severity="error",
                    capability_id=cap_id,
                )
                node_errors.append(issue)
                missing_capabilities.append(cap_id)
                cap_cards.append({"id": cap_id, "missing": True})
                continue
            cap_cards.append(cap.compact())
            node_warnings.extend(_audit_capability(node, cap))

        if not node.expected_outputs and not any(
            card.get("expected_observations") or card.get("expected_artifacts")
            for card in cap_cards
            if not card.get("missing")
        ):
            node_warnings.append(_issue(
                "missing_expected_outputs",
                node.node_id,
                "Node and its capabilities do not declare expected observations or artifacts.",
            ))

        errors.extend(node_errors)
        warnings.extend(node_warnings)
        node_reports.append({
            "node_id": node.node_id,
            "title": node.title,
            "purpose": node.purpose,
            "errors": node_errors,
            "warnings": node_warnings,
            "quality": {
                "capability_count": len(node.allowed_capabilities),
                "missing_capability_count": len([item for item in cap_cards if item.get("missing")]),
                "condition_count": len([*node.requires, *node.must_confirm, *node.completion]),
                "rubric_only_condition_count": len(condition_report["rubric_only"]),
                "unknown_evaluator_count": len(condition_report["unknown_evaluators"]),
                "has_completion": bool(node.completion),
                "has_expected_outputs": bool(node.expected_outputs),
            },
        })

    warnings = _unique_issues(warnings)
    errors = _unique_issues(errors)
    missing_capabilities = sorted(set(missing_capabilities))
    ok = not errors and (not strict or not warnings)
    return {
        "audit_type": "analysis_graph_audit",
        "ok": ok,
        "strict": strict,
        "graph_id": graph.graph_id,
        "version": graph.version,
        "start_node_id": graph.start_node_id,
        "summary": {
            "nodes": len(graph.nodes),
            "edges": len(graph.edges),
            "errors": len(errors),
            "warnings": len(warnings),
            "missing_capabilities": len(missing_capabilities),
            "rubric_only_conditions": len(rubric_only_conditions),
            "unknown_evaluators": len(unknown_evaluators),
        },
        "errors": errors,
        "warnings": warnings,
        "missing_capabilities": missing_capabilities,
        "nodes": node_reports,
        "advice": _audit_advice(errors, warnings),
    }


def _as_registry(capabilities: CapabilityInput) -> CapabilityRegistry:
    if isinstance(capabilities, CapabilityRegistry):
        return capabilities
    if hasattr(capabilities, "capabilities"):
        return CapabilityRegistry.from_domain(capabilities)
    if capabilities is None:
        return CapabilityRegistry()
    if isinstance(capabilities, dict):
        return CapabilityRegistry([capabilities])
    return CapabilityRegistry(capabilities)  # type: ignore[arg-type]


def _audit_registry(capabilities: CapabilityInput) -> CapabilityRegistry:
    if isinstance(capabilities, CapabilityRegistry):
        return capabilities
    if hasattr(capabilities, "capabilities"):
        return CapabilityRegistry(getattr(capabilities, "capabilities", []) or [])
    if capabilities is None:
        return CapabilityRegistry()
    if isinstance(capabilities, dict):
        return CapabilityRegistry([capabilities])
    return CapabilityRegistry(capabilities)  # type: ignore[arg-type]


def _node_card(node: AnalysisNodeSpec, graph: AnalysisGraphSpec) -> dict[str, Any]:
    return {
        "id": node.node_id,
        "title": node.title,
        "purpose": node.purpose,
        "is_start": node.node_id == graph.start_node_id,
        "strict_edges": node.strict_edges,
    }


def _navigation_card(graph: AnalysisGraphSpec, node: AnalysisNodeSpec) -> dict[str, Any]:
    outgoing = [edge for edge in graph.edges if edge.source == node.node_id]
    incoming = [edge for edge in graph.edges if edge.target == node.node_id]
    return {
        "next_nodes": list(node.next_nodes),
        "reachable_next": [item.node_id for item in graph.reachable_from(node.node_id)],
        "incoming_edges": [_edge_card(edge) for edge in incoming],
        "outgoing_edges": [_edge_card(edge) for edge in outgoing],
    }


def _edge_card(edge) -> dict[str, Any]:
    return {
        "source": edge.source,
        "target": edge.target,
        "edge_type": edge.edge_type,
        "condition_ids": list(edge.condition_ids),
        "description": edge.description,
    }


def _condition_card(cond: ConditionSpec) -> dict[str, Any]:
    return {
        "id": cond.condition_id,
        "evaluator_id": cond.evaluator_id,
        "tier": cond.tier,
        "failure_mode": cond.failure_mode,
        "hard": cond.hard,
        "description": cond.description or cond.message or cond.condition_id,
        "message": cond.message,
        "inputs": cond.inputs,
        "executable": bool(cond.evaluator_id and cond.evaluator_id != "rubric_only"),
    }


def _capability_card(capability_id: str, registry: CapabilityRegistry) -> dict[str, Any]:
    cap = registry.get(capability_id)
    if cap is None:
        return {
            "id": capability_id,
            "missing": True,
            "description": "Capability is referenced by the node but missing from the registry.",
        }
    return cap.compact()


def _input_profile(required_inputs: list[str], conditions: list[ConditionSpec]) -> dict[str, Any]:
    design_fields = set()
    runtime_symbols = set()
    artifacts_or_results = set()
    evidence = set()
    other = set()
    for cond in conditions:
        field = cond.inputs.get("field") if isinstance(cond.inputs, dict) else None
        fields = cond.inputs.get("fields") if isinstance(cond.inputs, dict) else None
        if field:
            design_fields.add(str(field))
        if isinstance(fields, list):
            design_fields.update(str(item) for item in fields if item)
    for item in required_inputs:
        key = item.lower()
        if item in {"control_labels", "guide_column", "target_column", "state_labels"} or key.endswith("_column"):
            design_fields.add(item)
        elif key.startswith("adata"):
            runtime_symbols.add(item)
        elif item in {"effect_table", "effect_result", "state_reference"} or "artifact" in key:
            artifacts_or_results.add(item)
        elif item in {"conclusions", "supported_observations", "observation_memory", "branches", "node_id"}:
            evidence.add(item)
        else:
            other.add(item)
    return {
        "required": sorted(required_inputs),
        "design_fields": sorted(design_fields),
        "runtime_symbols": sorted(runtime_symbols),
        "artifacts_or_results": sorted(artifacts_or_results),
        "evidence": sorted(evidence),
        "other": sorted(other),
    }


def _template_calls(capability_cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    calls = []
    for card in capability_cards:
        if card.get("missing"):
            continue
        if card.get("kind") not in {"execute", "review", "report"}:
            continue
        calls.append({
            "tool": "get_capability_template",
            "args": {"capability_id": card["id"]},
        })
    return calls


def _audit_checklist(node: AnalysisNodeSpec, capability_cards: list[dict[str, Any]]) -> list[str]:
    checks = []
    for cond in node.requires:
        checks.append(f"satisfy entry gate: {cond.description or cond.message or cond.condition_id}")
    for cond in node.must_confirm:
        checks.append(f"confirm with user/design authority: {cond.description or cond.message or cond.condition_id}")
    for card in capability_cards:
        if card.get("missing"):
            checks.append(f"define missing capability: {card['id']}")
            continue
        for item in card.get("required_inputs", []):
            checks.append(f"resolve input for {card['id']}: {item}")
        for item in card.get("expected_observations", []):
            checks.append(f"register observation for {card['id']}: {item}")
        for item in card.get("expected_artifacts", []):
            checks.append(f"register artifact for {card['id']}: {item}")
    for cond in node.completion:
        checks.append(f"before completing node: {cond.description or cond.message or cond.condition_id}")
    return _unique(checks)


def _quality_summary(node: AnalysisNodeSpec, capability_cards: list[dict[str, Any]]) -> dict[str, Any]:
    all_conditions = [*node.requires, *node.must_confirm, *node.completion]
    executable = [cond for cond in all_conditions if cond.evaluator_id and cond.evaluator_id != "rubric_only"]
    rubric_only = [cond for cond in all_conditions if cond.evaluator_id == "rubric_only"]
    return {
        "capability_count": len(capability_cards),
        "missing_capability_count": len([card for card in capability_cards if card.get("missing")]),
        "executable_condition_count": len(executable),
        "rubric_only_condition_count": len(rubric_only),
        "manual_confirmation_count": len([cond for cond in all_conditions if cond.tier == "C"]),
    }


def _incoming_node_ids(graph: AnalysisGraphSpec) -> set[str]:
    incoming = {edge.target for edge in graph.edges}
    for node in graph.nodes:
        incoming.update(node.next_nodes)
    return incoming


def _audit_conditions(node: AnalysisNodeSpec) -> dict[str, Any]:
    warnings = []
    errors = []
    rubric_only = []
    unknown_evaluators = []
    for field_name, conditions in (
        ("requires", node.requires),
        ("must_confirm", node.must_confirm),
        ("completion", node.completion),
    ):
        for cond in conditions:
            if cond.evaluator_id == "rubric_only":
                issue = _issue(
                    "rubric_only_condition",
                    node.node_id,
                    f"{field_name} condition `{cond.condition_id}` is not machine-enforced.",
                    condition_id=cond.condition_id,
                    field=field_name,
                )
                warnings.append(issue)
                rubric_only.append(issue)
                continue
            evaluator = cond.evaluator_id or cond.condition_id
            if evaluator not in CONDITION_CHECKS:
                issue = _issue(
                    "unknown_condition_evaluator",
                    node.node_id,
                    f"{field_name} condition `{cond.condition_id}` uses unknown evaluator `{evaluator}`.",
                    severity="error",
                    condition_id=cond.condition_id,
                    evaluator_id=evaluator,
                    field=field_name,
                )
                errors.append(issue)
                unknown_evaluators.append(issue)
    return {
        "warnings": warnings,
        "errors": errors,
        "rubric_only": rubric_only,
        "unknown_evaluators": unknown_evaluators,
    }


def _audit_capability(node: AnalysisNodeSpec, cap: Capability) -> list[dict[str, Any]]:
    warnings = []
    if cap.kind in {"execute", "review", "report"} and not cap.tool_names:
        warnings.append(_issue(
            "capability_without_tools",
            node.node_id,
            f"Capability `{cap.capability_id}` has no tool_names; the agent may not know how to commit it.",
            capability_id=cap.capability_id,
        ))
    if cap.kind in {"execute", "review", "report"} and not (
        cap.expected_observations or cap.expected_artifacts or cap.contract
    ):
        warnings.append(_issue(
            "capability_without_outputs",
            node.node_id,
            f"Capability `{cap.capability_id}` does not declare expected observations/artifacts.",
            capability_id=cap.capability_id,
        ))
    if cap.risk == "high" and cap.kind == "external":
        warnings.append(_issue(
            "high_risk_external_capability",
            node.node_id,
            f"Capability `{cap.capability_id}` is external/high-risk; document why it is needed.",
            capability_id=cap.capability_id,
        ))
    return warnings


def _issue(
    code: str,
    node_id: str,
    message: str,
    *,
    severity: str = "warning",
    **extra,
) -> dict[str, Any]:
    return {
        "severity": severity,
        "code": code,
        "node_id": node_id,
        "message": message,
        **{key: value for key, value in extra.items() if value not in ("", None, [], {})},
    }


def _unique_issues(issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen = set()
    out = []
    for issue in issues:
        key = (
            issue.get("severity"),
            issue.get("code"),
            issue.get("node_id"),
            issue.get("capability_id"),
            issue.get("condition_id"),
            issue.get("field"),
            issue.get("message"),
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(issue)
    return out


def _audit_advice(errors: list[dict[str, Any]], warnings: list[dict[str, Any]]) -> list[dict[str, str]]:
    codes = {item.get("code") for item in [*errors, *warnings]}
    advice = []
    if "missing_capability" in codes:
        advice.append({
            "code": "define_capabilities",
            "message": "Register every node capability in Domain.capabilities using pertura.capability(...).",
        })
    if "rubric_only_condition" in codes:
        advice.append({
            "code": "compile_conditions",
            "message": "Run `pertura spec compile` or use pertura.conditions helpers to make gates executable.",
        })
    if "missing_completion" in codes:
        advice.append({
            "code": "add_completion",
            "message": "Add done_when/completion criteria so node completion is auditable.",
        })
    if "missing_expected_outputs" in codes or "capability_without_outputs" in codes:
        advice.append({
            "code": "declare_outputs",
            "message": "Declare expected observations/artifacts at the node or capability level.",
        })
    if "unknown_condition_evaluator" in codes:
        advice.append({
            "code": "fix_evaluators",
            "message": "Use built-in condition helpers or register a supported evaluator before runtime use.",
        })
    return advice


def _unique(values) -> list:
    seen = set()
    out = []
    for value in values:
        if value in ("", None):
            continue
        key = json_key(value)
        if key in seen:
            continue
        seen.add(key)
        out.append(value)
    return out


def json_key(value) -> str:
    if isinstance(value, (dict, list)):
        import json

        return json.dumps(value, sort_keys=True, default=str)
    return str(value)
