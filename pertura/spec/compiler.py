"""Build-time compiler for natural-language analysis conditions.

The runtime gate evaluator only executes ConditionSpec objects. This compiler
is the authoring-time bridge from user/domain prose to executable conditions.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Literal

from pertura.spec.conditions import CONDITION_CHECKS
from pertura.spec.models import (
    AnalysisGraphSpec,
    ConditionSpec,
    compile_condition,
    condition,
    spec_from_dict,
)


CompilerProvider = Literal["deterministic", "openai", "anthropic"]


@dataclass
class ConditionCompileReport:
    spec: AnalysisGraphSpec
    provider: str
    executable: list[dict] = field(default_factory=list)
    rubric_only: list[dict] = field(default_factory=list)
    unmapped: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "graph_id": self.spec.graph_id,
            "provider": self.provider,
            "executable": self.executable,
            "rubric_only": self.rubric_only,
            "unmapped": self.unmapped,
            "warnings": self.warnings,
            "spec": self.spec.model_dump(mode="json"),
        }


def compile_conditions(
    spec: AnalysisGraphSpec | dict,
    *,
    domain_context: str = "",
    provider: CompilerProvider = "deterministic",
) -> ConditionCompileReport:
    """Compile rubric-only or prose-like conditions into executable checks.

    Deterministic mode uses the local vocabulary and is safe for CI. LLM modes
    are build-time only and must map to existing CONDITION_CHECKS evaluators.
    """
    graph = spec_from_dict(spec)
    if graph is None:
        raise ValueError("Cannot compile empty analysis graph spec.")

    report = ConditionCompileReport(spec=graph, provider=provider)
    candidates = _collect_rubric_conditions(graph)
    llm_mappings = {}
    if provider != "deterministic" and candidates:
        llm_mappings = _compile_with_llm(
            candidates,
            domain_context=domain_context,
            provider=provider,
        )

    for node in graph.nodes:
        for field_name in ("requires", "must_confirm", "completion"):
            items = getattr(node, field_name)
            for idx, item in enumerate(items):
                if item.evaluator_id != "rubric_only":
                    _record(report.executable, node.node_id, field_name, item, "already_executable")
                    continue
                text = item.description or item.message or item.condition_id
                mapped = None
                if text in llm_mappings:
                    mapped = _validated_condition(llm_mappings[text], text)
                if mapped is None:
                    mapped = compile_condition(text, context=field_name)
                    if mapped.evaluator_id == "rubric_only":
                        _record(report.rubric_only, node.node_id, field_name, item, "unmapped")
                        continue
                items[idx] = mapped
                _record(report.executable, node.node_id, field_name, mapped, "compiled")
    return report


def _collect_rubric_conditions(spec: AnalysisGraphSpec) -> list[str]:
    seen = set()
    out = []
    for node in spec.nodes:
        for item in [*node.requires, *node.must_confirm, *node.completion]:
            if item.evaluator_id != "rubric_only":
                continue
            text = item.description or item.message or item.condition_id
            if text and text not in seen:
                seen.add(text)
                out.append(text)
    return out


def _compile_with_llm(
    conditions: list[str],
    *,
    domain_context: str,
    provider: str,
) -> dict[str, dict]:
    from pertura.planner import _call_llm

    schema = {
        "type": "object",
        "properties": {
            "mappings": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "source_text": {"type": "string"},
                        "condition_id": {"type": "string"},
                        "evaluator_id": {"type": "string", "enum": sorted(CONDITION_CHECKS.keys())},
                        "tier": {"type": "string", "enum": ["A", "B", "C"]},
                        "failure_mode": {
                            "type": "string",
                            "enum": ["warn", "autonomous_recovery", "human_interrupt", "skip_node", "block"],
                        },
                        "inputs": {"type": "object"},
                        "message": {"type": "string"},
                        "hard": {"type": "boolean"},
                    },
                    "required": ["source_text", "condition_id", "evaluator_id", "tier", "failure_mode", "inputs", "message", "hard"],
                    "additionalProperties": False,
                },
            },
            "unmapped": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["mappings", "unmapped"],
        "additionalProperties": False,
    }
    prompt = {
        "domain_context": domain_context,
        "allowed_evaluators": _evaluator_vocabulary(),
        "natural_language_conditions": conditions,
        "instruction": "Map each condition only when it can be enforced by an allowed evaluator. Leave uncertain items unmapped.",
    }
    result = _call_llm(
        "Compile scientific analysis preconditions into executable ConditionSpec mappings.",
        json.dumps(prompt, ensure_ascii=False),
        schema,
        provider=provider,
    )
    return {
        item["source_text"]: item
        for item in result.get("mappings", [])
        if item.get("source_text")
    }


def _validated_condition(payload: dict, source_text: str) -> ConditionSpec | None:
    evaluator = payload.get("evaluator_id", "")
    if evaluator not in CONDITION_CHECKS:
        return None
    return condition(
        payload.get("condition_id") or source_text,
        evaluator_id=evaluator,
        tier=payload.get("tier", "A"),
        failure_mode=payload.get("failure_mode", "warn"),
        description=source_text,
        inputs=payload.get("inputs", {}),
        message=payload.get("message") or source_text,
        hard=bool(payload.get("hard", True)),
    )


def _evaluator_vocabulary() -> list[dict]:
    return [
        {"evaluator_id": name, "description": _describe_evaluator(name)}
        for name in sorted(CONDITION_CHECKS.keys())
    ]


def _describe_evaluator(name: str) -> str:
    return {
        "has_workspace_file": "workspace contains at least one discovered file or directory",
        "has_dataset_loaded_observation": "dataset/schema/AnnData observation or dataset artifact exists",
        "design_field_known": "a specific design field is present in snap.design",
        "design_any_known": "at least one of several design fields is present in snap.design",
        "manual_confirmation": "a user confirmation key is present in snap.design or snap.design.confirmations",
        "has_artifact_kind": "an artifact of a given kind exists",
        "has_observation": "a matching observation exists",
        "has_observation_metric": "an observation with a given metric exists",
        "has_capability": "a capability id is available",
        "no_open_trigger": "there are no open review triggers",
    }.get(name, name)


def _record(target: list[dict], node_id: str, field_name: str,
            cond: ConditionSpec, status: str) -> None:
    target.append({
        "node_id": node_id,
        "field": field_name,
        "condition_id": cond.condition_id,
        "evaluator_id": cond.evaluator_id,
        "tier": cond.tier,
        "failure_mode": cond.failure_mode,
        "status": status,
        "description": cond.description,
    })
