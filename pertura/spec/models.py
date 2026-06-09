"""Editable analysis graph specification for Pertura v2.

The spec graph is user/domain-authored process intent. It is separate from
the runtime event graph, which remains the event-sourced record of what
actually happened.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field


ConditionTier = Literal["A", "B", "C"]
FailureMode = Literal[
    "warn",
    "autonomous_recovery",
    "human_interrupt",
    "skip_node",
    "block",
]


class ConditionSpec(BaseModel):
    condition_id: str
    evaluator_id: str = ""
    tier: ConditionTier = "A"
    failure_mode: FailureMode = "warn"
    description: str = ""
    inputs: dict[str, Any] = Field(default_factory=dict)
    message: str = ""
    hard: bool = True


class AnalysisNodeSpec(BaseModel):
    node_id: str
    title: str = ""
    purpose: str = ""
    allowed_capabilities: list[str] = Field(default_factory=list)
    requires: list[ConditionSpec] = Field(default_factory=list)
    must_confirm: list[ConditionSpec] = Field(default_factory=list)
    completion: list[ConditionSpec] = Field(default_factory=list)
    recommended_actions: list[str] = Field(default_factory=list)
    expected_outputs: list[str] = Field(default_factory=list)
    next_nodes: list[str] = Field(default_factory=list)
    strict_edges: bool = False


class AnalysisEdgeSpec(BaseModel):
    source: str
    target: str
    edge_type: str = "next"
    auto: bool = True
    condition_ids: list[str] = Field(default_factory=list)
    description: str = ""


class AnalysisGraphSpec(BaseModel):
    graph_id: str
    version: str = "v1"
    start_node_id: str = "workspace_inspection"
    nodes: list[AnalysisNodeSpec] = Field(default_factory=list)
    edges: list[AnalysisEdgeSpec] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    def node(self, node_id: str) -> AnalysisNodeSpec | None:
        return next((node for node in self.nodes if node.node_id == node_id), None)

    def reachable_from(self, current_node_id: str = "") -> list[AnalysisNodeSpec]:
        if not current_node_id:
            return list(self.nodes)
        current = self.node(current_node_id)
        if current is None:
            return list(self.nodes)
        if current.strict_edges:
            allowed = set(current.next_nodes)
            allowed.update(edge.target for edge in self.edges if edge.source == current_node_id)
            return [node for node in self.nodes if node.node_id in allowed]
        if current.next_nodes:
            allowed = set(current.next_nodes)
            allowed.update(edge.target for edge in self.edges if edge.source == current_node_id)
            return [node for node in self.nodes if node.node_id in allowed]
        return list(self.nodes)


ConditionInput = str | ConditionSpec | dict[str, Any]
CapabilityInput = Any


def condition(
    condition_id: str,
    *,
    evaluator_id: str = "",
    tier: ConditionTier = "A",
    failure_mode: FailureMode = "warn",
    description: str = "",
    inputs: dict[str, Any] | None = None,
    message: str = "",
    hard: bool = True,
) -> ConditionSpec:
    """Create an explicit machine-enforced condition."""
    return ConditionSpec(
        condition_id=condition_id,
        evaluator_id=evaluator_id or condition_id,
        tier=tier,
        failure_mode=failure_mode,
        description=description or condition_id.replace("_", " "),
        inputs=inputs or {},
        message=message,
        hard=hard,
    )


def _compile_conditions(items: list[ConditionInput] | None, *, context: str) -> list[ConditionSpec]:
    return [compile_condition(item, context=context) for item in (items or [])]


def _extend_unique(target: list[str], values: list[str]) -> None:
    for value in values:
        if value and value not in target:
            target.append(value)


class AnalysisNodeBuilder:
    """Fluent builder for one analysis node.

    This is the public ergonomic layer. It mutates the underlying
    AnalysisNodeSpec, while the runtime still consumes plain serializable specs.
    """

    def __init__(self, graph: "AnalysisGraph", spec: AnalysisNodeSpec):
        self._graph = graph
        self._spec = spec

    def title(self, text: str) -> "AnalysisNodeBuilder":
        self._spec.title = text
        return self

    def goal(self, text: str) -> "AnalysisNodeBuilder":
        self._spec.purpose = text
        return self

    def use(self, *capability_ids: CapabilityInput) -> "AnalysisNodeBuilder":
        from pertura.capabilities import to_capability_id

        _extend_unique(self._spec.allowed_capabilities, [to_capability_id(item) for item in capability_ids])
        return self

    def enter_if(self, *conditions: ConditionInput) -> "AnalysisNodeBuilder":
        self._spec.requires.extend(_compile_conditions(list(conditions), context="requires"))
        return self

    def confirm(self, *conditions: ConditionInput) -> "AnalysisNodeBuilder":
        self._spec.must_confirm.extend(_compile_conditions(list(conditions), context="must_confirm"))
        return self

    def done_when(self, *conditions: ConditionInput) -> "AnalysisNodeBuilder":
        self._spec.completion.extend(_compile_conditions(list(conditions), context="completion"))
        return self

    def recommend(self, *actions: str) -> "AnalysisNodeBuilder":
        _extend_unique(self._spec.recommended_actions, list(actions))
        return self

    def expect(self, *outputs: str) -> "AnalysisNodeBuilder":
        _extend_unique(self._spec.expected_outputs, list(outputs))
        return self

    def next(self, *node_ids: str, strict: bool | None = None) -> "AnalysisNodeBuilder":
        _extend_unique(self._spec.next_nodes, list(node_ids))
        if strict is not None:
            self._spec.strict_edges = strict
        return self

    def strict_edges(self, enabled: bool = True) -> "AnalysisNodeBuilder":
        self._spec.strict_edges = enabled
        return self

    def edge_to(
        self,
        target: str,
        *,
        edge_type: str = "next",
        condition_ids: list[str] | None = None,
        description: str = "",
    ) -> "AnalysisNodeBuilder":
        self._graph.add_edge(
            self._spec.node_id,
            target,
            edge_type=edge_type,
            condition_ids=condition_ids,
            description=description,
        )
        return self

    def start(self) -> "AnalysisNodeBuilder":
        self._graph.set_start(self._spec.node_id)
        return self

    def end(self) -> "AnalysisGraph":
        return self._graph

    @property
    def spec(self) -> AnalysisNodeSpec:
        return self._spec


class AnalysisGraph:
    """Small builder API exposed by the runtime and the future pertura facade."""

    def __init__(self, graph_id: str, *, version: str = "v1", start_node_id: str = "workspace_inspection"):
        self.spec = AnalysisGraphSpec(
            graph_id=graph_id,
            version=version,
            start_node_id=start_node_id,
        )

    def node(self, node_id: str, *, title: str = "", purpose: str = "") -> AnalysisNodeBuilder:
        existing = self.spec.node(node_id)
        if existing is None:
            existing = AnalysisNodeSpec(node_id=node_id, title=title, purpose=purpose)
            self.spec.nodes.append(existing)
        else:
            if title:
                existing.title = title
            if purpose:
                existing.purpose = purpose
        return AnalysisNodeBuilder(self, existing)

    def add_node(
        self,
        node_id: str,
        *,
        title: str = "",
        purpose: str = "",
        allowed_capabilities: list[CapabilityInput] | None = None,
        requires: list[ConditionInput] | None = None,
        must_confirm: list[ConditionInput] | None = None,
        completion: list[ConditionInput] | None = None,
        recommended_actions: list[str] | None = None,
        expected_outputs: list[str] | None = None,
        next_nodes: list[str] | None = None,
        strict_edges: bool = False,
    ) -> "AnalysisGraph":
        self.spec.nodes.append(AnalysisNodeSpec(
            node_id=node_id,
            title=title,
            purpose=purpose,
            allowed_capabilities=_compile_capabilities(allowed_capabilities),
            requires=_compile_conditions(requires, context="requires"),
            must_confirm=_compile_conditions(must_confirm, context="must_confirm"),
            completion=_compile_conditions(completion, context="completion"),
            recommended_actions=recommended_actions or [],
            expected_outputs=expected_outputs or [],
            next_nodes=next_nodes or [],
            strict_edges=strict_edges,
        ))
        return self

    def add_edge(
        self,
        source: str,
        target: str,
        *,
        edge_type: str = "next",
        condition_ids: list[str] | None = None,
        description: str = "",
    ) -> "AnalysisGraph":
        self.spec.edges.append(AnalysisEdgeSpec(
            source=source,
            target=target,
            edge_type=edge_type,
            condition_ids=condition_ids or [],
            description=description,
        ))
        return self

    def set_start(self, node_id: str) -> "AnalysisGraph":
        self.spec.start_node_id = node_id
        return self

    def to_spec(self) -> AnalysisGraphSpec:
        return self.spec


def _compile_capabilities(items: list[CapabilityInput] | None) -> list[str]:
    from pertura.capabilities import to_capability_id

    out: list[str] = []
    for item in items or []:
        cap_id = to_capability_id(item)
        if cap_id and cap_id not in out:
            out.append(cap_id)
    return out

    def to_dict(self) -> dict:
        return self.spec.model_dump(mode="json")

    def export(self, path: str | Path) -> Path:
        out = Path(path)
        out.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        return out


def compile_condition(item: str | ConditionSpec | dict, *, context: str) -> ConditionSpec:
    """Compile user-friendly node strings into machine conditions when possible.

    Unmapped strings in `requires` and `completion` become rubric-only entries.
    Unmapped strings in `must_confirm` become C-tier manual confirmations.
    """
    if isinstance(item, ConditionSpec):
        return item
    if isinstance(item, dict):
        return ConditionSpec(**item)
    text = str(item)
    mapped = _map_natural_language_condition(text)
    if mapped:
        tier = mapped.get("tier", "A")
        failure_mode = mapped.get("failure_mode", "warn")
        if context == "must_confirm":
            tier = "C"
            failure_mode = "human_interrupt"
        return condition(
            mapped["condition_id"],
            evaluator_id=mapped["evaluator_id"],
            tier=tier,
            failure_mode=failure_mode,
            description=text,
            inputs=mapped.get("inputs", {}),
            message=text,
        )
    if context == "must_confirm":
        condition_id = _slug(text)
        return condition(
            condition_id,
            evaluator_id="manual_confirmation",
            tier="C",
            failure_mode="human_interrupt",
            description=text,
            inputs={"key": condition_id},
            message=text,
        )
    return condition(
        _slug(text),
        evaluator_id="rubric_only",
        tier="A",
        failure_mode="warn",
        description=text,
        message=text,
        hard=False,
    )


def spec_from_dict(data: dict | AnalysisGraphSpec | None) -> AnalysisGraphSpec | None:
    if data is None:
        return None
    if isinstance(data, AnalysisGraphSpec):
        return data
    if not data:
        return None
    return AnalysisGraphSpec(**data)


def load_analysis_graph(path: str | Path) -> AnalysisGraphSpec:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    spec = AnalysisGraphSpec(**data)
    validate_analysis_graph(spec)
    return spec


def save_analysis_graph(spec: AnalysisGraphSpec | dict, path: str | Path) -> Path:
    graph = spec_from_dict(spec)
    if graph is None:
        raise ValueError("Cannot save empty analysis graph spec.")
    validate_analysis_graph(graph)
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(graph.model_dump(mode="json"), ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def validate_analysis_graph(spec: AnalysisGraphSpec) -> None:
    ids = [node.node_id for node in spec.nodes]
    duplicates = sorted({node_id for node_id in ids if ids.count(node_id) > 1})
    if duplicates:
        raise ValueError(f"Duplicate analysis node ids: {', '.join(duplicates)}")
    if spec.start_node_id and spec.start_node_id not in ids:
        raise ValueError(f"start_node_id not found: {spec.start_node_id}")
    known = set(ids)
    for node in spec.nodes:
        missing = [target for target in node.next_nodes if target not in known]
        if missing:
            raise ValueError(f"Node {node.node_id} references unknown next_nodes: {', '.join(missing)}")
    for edge in spec.edges:
        if edge.source not in known:
            raise ValueError(f"Edge source not found: {edge.source}")
        if edge.target not in known:
            raise ValueError(f"Edge target not found: {edge.target}")


def _slug(text: str) -> str:
    import re
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", text.strip().lower()).strip("_")
    return slug[:80] or "condition"


def _map_natural_language_condition(text: str) -> dict | None:
    lower = text.lower()
    if "dataset" in lower and ("loaded" in lower or "available" in lower):
        return {"condition_id": "dataset_loaded", "evaluator_id": "has_dataset_loaded_observation"}
    if "workspace" in lower and "file" in lower:
        return {"condition_id": "has_workspace_file", "evaluator_id": "has_workspace_file"}
    if "control" in lower and ("label" in lower or "defined" in lower or "known" in lower):
        return {
            "condition_id": "control_labels_defined",
            "evaluator_id": "design_field_known",
            "inputs": {"field": "control_labels"},
            "tier": "C",
            "failure_mode": "human_interrupt",
        }
    if "guide" in lower and "column" in lower:
        return {
            "condition_id": "guide_column_known",
            "evaluator_id": "design_field_known",
            "inputs": {"field": "guide_column"},
            "tier": "C",
            "failure_mode": "human_interrupt",
        }
    if "target" in lower and ("column" in lower or "mapping" in lower):
        return {
            "condition_id": "target_mapping_known",
            "evaluator_id": "design_field_known",
            "inputs": {"field": "target_column"},
            "tier": "B",
            "failure_mode": "autonomous_recovery",
        }
    if "moi" in lower or "loading" in lower or "modality" in lower or "crispr" in lower:
        return {
            "condition_id": "perturbation_design_known",
            "evaluator_id": "design_any_known",
            "inputs": {"fields": ["perturbation_modality", "moi", "loading_strategy"]},
            "tier": "C",
            "failure_mode": "human_interrupt",
        }
    if "de result" in lower or "differential expression" in lower:
        return {
            "condition_id": "has_de_result",
            "evaluator_id": "has_artifact_kind",
            "inputs": {"kind": "de_result"},
        }
    if "observation" in lower or "metric" in lower:
        return {"condition_id": "has_observation", "evaluator_id": "has_observation"}
    return None
