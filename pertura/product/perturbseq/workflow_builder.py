"""Perturb-seq workflow authoring projection and helpers."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from pertura.capabilities import CapabilityRegistry
from pertura.spec.conditions import CONDITION_CHECKS
from pertura.spec.contracts import audit_analysis_graph
from pertura.spec.models import AnalysisGraphSpec, ConditionSpec, condition, spec_from_dict


CHECK_CATALOG = [
    {
        "id": "dataset_loaded",
        "label": "Dataset loaded",
        "evaluator_id": "has_dataset_loaded_observation",
        "default_group": "requires",
        "message": "Dataset or schema has been summarized.",
    },
    {
        "id": "control_labels_known",
        "label": "Control labels known",
        "evaluator_id": "design_field_known",
        "default_group": "requires",
        "inputs": {"field": "control_labels"},
        "tier": "C",
        "failure_mode": "human_interrupt",
        "message": "Control labels should be confirmed before interpretation.",
    },
    {
        "id": "guide_column_known",
        "label": "Guide column known",
        "evaluator_id": "design_field_known",
        "default_group": "requires",
        "inputs": {"field": "guide_column"},
        "tier": "C",
        "failure_mode": "human_interrupt",
        "message": "Guide column should be confirmed.",
    },
    {
        "id": "target_column_known",
        "label": "Target column known",
        "evaluator_id": "design_field_known",
        "default_group": "requires",
        "inputs": {"field": "target_column"},
        "tier": "C",
        "failure_mode": "human_interrupt",
        "message": "Target column should be confirmed.",
    },
    {
        "id": "qc_metrics_registered",
        "label": "QC metrics registered",
        "evaluator_id": "has_observation_metric",
        "default_group": "completion",
        "inputs": {"metric": "n_cells"},
        "message": "QC metrics are registered.",
    },
    {
        "id": "target_coverage_registered",
        "label": "Target coverage registered",
        "evaluator_id": "has_observation",
        "default_group": "completion",
        "inputs": {"metric": "target_coverage"},
        "message": "Target coverage is registered.",
    },
    {
        "id": "guide_assignment_registered",
        "label": "Guide assignment registered",
        "evaluator_id": "has_observation",
        "default_group": "completion",
        "inputs": {"metric": "guide_assignment"},
        "message": "Guide assignment method is registered.",
    },
    {
        "id": "effect_observation_registered",
        "label": "Effect observation registered",
        "evaluator_id": "has_observation_metric",
        "default_group": "completion",
        "inputs": {"metric": "logFC"},
        "message": "At least one effect observation is registered.",
    },
    {
        "id": "report_artifact_registered",
        "label": "Report artifact registered",
        "evaluator_id": "has_artifact_kind",
        "default_group": "completion",
        "inputs": {"kind": "report"},
        "message": "Report artifact is registered.",
    },
    {
        "id": "no_open_trigger",
        "label": "No open runtime trigger",
        "evaluator_id": "no_open_trigger",
        "default_group": "requires",
        "message": "Open runtime triggers should be resolved.",
    },
]


def workflow_builder_view(
    *,
    snap=None,
    domain=None,
    run_id: str = "",
    capabilities=None,
) -> dict[str, Any]:
    """Return the event-backed workflow authoring surface."""
    current = _current_spec(snap=snap, domain=domain)
    draft = deepcopy(getattr(snap, "analysis_spec_draft", {}) or {}) if snap is not None else {}
    effective = draft or current
    registry = _registry(capabilities or getattr(domain, "capabilities", []) or getattr(snap, "capabilities", []) or [])
    audit = _audit(effective, registry)
    active_node_id = getattr(snap, "active_node_id", "") if snap is not None else ""
    return {
        "view_type": "workflow_builder",
        "schema_version": "v1",
        "run_id": getattr(snap, "run_id", run_id) if snap is not None else run_id,
        "active_node_id": active_node_id,
        "node_catalog": compile_node_catalog(domain=domain, capabilities=registry),
        "check_catalog": compile_check_catalog(),
        "current_spec": current,
        "draft_spec": draft,
        "effective_spec": effective,
        "draft_meta": getattr(snap, "workflow_draft_meta", {}) if snap is not None else {},
        "audit": audit,
        "can_apply": bool(draft) and not audit.get("errors"),
    }


def compile_node_catalog(*, domain=None, capabilities=None) -> list[dict[str, Any]]:
    spec = _domain_spec(domain)
    registry = _registry(capabilities or getattr(domain, "capabilities", []) or [])
    nodes = []
    for node in spec.nodes:
        nodes.append({
            "node_id": node.node_id,
            "title": node.title or node.node_id,
            "purpose": node.purpose,
            "node_spec": node.model_dump(mode="json"),
            "allowed_capabilities": list(node.allowed_capabilities),
            "requires": [_condition_card(item) for item in node.requires],
            "prechecks": [_condition_card(item) for item in [*node.requires, *node.must_confirm]],
            "done_when": [_condition_card(item) for item in node.completion],
            "expected_outputs": list(node.expected_outputs),
            "next_nodes": list(node.next_nodes),
            "capabilities": [
                _capability_card(cap_id, registry)
                for cap_id in node.allowed_capabilities
            ],
            "default_position": _default_position(spec, node.node_id),
        })
    return nodes


def compile_check_catalog() -> list[dict[str, Any]]:
    out = []
    for item in CHECK_CATALOG:
        out.append({
            **item,
            "available": item.get("evaluator_id") in CONDITION_CHECKS,
            "condition": condition_from_catalog(item["id"]).model_dump(mode="json"),
        })
    return out


def normalize_workflow_spec(spec: dict[str, Any]) -> dict[str, Any]:
    graph = spec_from_dict(spec)
    if graph is None:
        raise ValueError("Workflow spec is empty.")
    normalized = graph.model_dump(mode="json")
    metadata = normalized.setdefault("metadata", {})
    ui = metadata.setdefault("ui", {})
    positions = ui.setdefault("positions", {})
    for index, node in enumerate(normalized.get("nodes", []) or []):
        positions.setdefault(node.get("node_id", ""), {"x": 40 + index * 230, "y": 80})
    return normalized


def condition_from_catalog(check_id: str, *, group: str = "") -> ConditionSpec:
    item = next((row for row in CHECK_CATALOG if row["id"] == check_id), None)
    if item is None:
        raise ValueError(f"Unknown workflow check: {check_id}")
    return condition(
        item["id"],
        evaluator_id=item.get("evaluator_id", ""),
        tier=item.get("tier", "A"),
        failure_mode=item.get("failure_mode", "warn"),
        inputs=item.get("inputs", {}),
        message=item.get("message", ""),
        description=item.get("label", ""),
    )


def _current_spec(*, snap=None, domain=None) -> dict[str, Any]:
    if snap is not None and getattr(snap, "analysis_spec", None):
        return deepcopy(getattr(snap, "analysis_spec", {}) or {})
    return _domain_spec(domain).model_dump(mode="json")


def _domain_spec(domain=None) -> AnalysisGraphSpec:
    spec = getattr(domain, "analysis_graph", None)
    if spec:
        parsed = spec_from_dict(spec)
        if parsed is not None:
            return parsed
    from pertura.domain.perturbseq import build_perturbseq_analysis_graph

    return build_perturbseq_analysis_graph()


def _registry(capabilities) -> CapabilityRegistry:
    if isinstance(capabilities, CapabilityRegistry):
        return capabilities
    return CapabilityRegistry(list(capabilities or []))


def _audit(spec: dict[str, Any], registry: CapabilityRegistry) -> dict[str, Any]:
    try:
        return audit_analysis_graph(spec, capabilities=registry)
    except Exception as exc:
        return {
            "ok": False,
            "errors": [{"code": "invalid_workflow", "message": str(exc)}],
            "warnings": [],
        }


def _condition_card(item: ConditionSpec) -> dict[str, Any]:
    return {
        "condition_id": item.condition_id,
        "label": item.description or item.message or item.condition_id.replace("_", " "),
        "evaluator_id": item.evaluator_id,
        "tier": item.tier,
        "failure_mode": item.failure_mode,
        "inputs": dict(item.inputs or {}),
        "message": item.message,
        "hard": item.hard,
    }


def _capability_card(cap_id: str, registry: CapabilityRegistry) -> dict[str, Any]:
    cap = registry.get(cap_id)
    if cap is None:
        return {"id": cap_id, "missing": True}
    data = cap.compact()
    return {
        "id": data.get("id", cap_id),
        "title": data.get("title") or cap_id.replace("_", " "),
        "description": data.get("description", ""),
        "kind": data.get("kind", ""),
        "required_inputs": data.get("required_inputs", []),
        "expected_observations": data.get("expected_observations", []),
        "expected_artifacts": data.get("expected_artifacts", []),
        "tools": data.get("tools", []),
    }


def _default_position(spec: AnalysisGraphSpec, node_id: str) -> dict[str, int]:
    positions = ((spec.metadata or {}).get("ui") or {}).get("positions") or {}
    if node_id in positions:
        return positions[node_id]
    ids = [node.node_id for node in spec.nodes]
    index = ids.index(node_id) if node_id in ids else 0
    return {"x": 40 + index * 230, "y": 80}
