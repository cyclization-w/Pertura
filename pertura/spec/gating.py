"""Gate evaluation for AnalysisSpecGraph node transitions and actions."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

from pertura.spec.models import AnalysisGraphSpec, AnalysisNodeSpec, spec_from_dict
from pertura.spec.conditions import ConditionResult, evaluate_conditions
from pertura.models import Snapshot


class GateDecision(BaseModel):
    decision: str = "pass"  # pass | warn | autonomous_recovery | human_interrupt | skip | block
    target_node_id: str = ""
    source_node_id: str = ""
    condition_results: list[dict[str, Any]] = Field(default_factory=list)
    messages: list[str] = Field(default_factory=list)
    reason: str = ""

    @property
    def can_enter(self) -> bool:
        return self.decision in {"pass", "warn"}


class GateEvaluator:
    def __init__(self, spec: AnalysisGraphSpec | dict | None):
        self.spec = spec_from_dict(spec)

    def evaluate_enter(self, snap: Snapshot, target_node_id: str) -> GateDecision:
        if self.spec is None:
            return GateDecision(decision="pass", target_node_id=target_node_id)
        source_node_id = snap.active_node_id
        target = self.spec.node(target_node_id)
        if target is None:
            return GateDecision(
                decision="block",
                source_node_id=source_node_id,
                target_node_id=target_node_id,
                reason=f"Unknown analysis node: {target_node_id}",
            )
        if not self._is_reachable(source_node_id, target_node_id):
            return GateDecision(
                decision="block",
                source_node_id=source_node_id,
                target_node_id=target_node_id,
                reason=f"Node {target_node_id} is not reachable from {source_node_id}.",
            )
        results = evaluate_conditions([*target.requires, *target.must_confirm], snap)
        return _decision_from_results(results, source_node_id, target_node_id)

    def evaluate_completion(self, snap: Snapshot, node_id: str) -> GateDecision:
        if self.spec is None:
            return GateDecision(decision="pass", target_node_id=node_id)
        node = self.spec.node(node_id)
        if node is None:
            return GateDecision(decision="block", target_node_id=node_id, reason=f"Unknown node: {node_id}")
        results = evaluate_conditions(node.completion, snap)
        return _decision_from_results(results, snap.active_node_id, node_id, completion=True)

    def allowed_capabilities(self, node_id: str) -> set[str]:
        if self.spec is None:
            return set()
        node = self.spec.node(node_id)
        return set(node.allowed_capabilities if node else [])

    def reachable_nodes(self, current_node_id: str = "") -> list[AnalysisNodeSpec]:
        if self.spec is None:
            return []
        return self.spec.reachable_from(current_node_id)

    def _is_reachable(self, source_node_id: str, target_node_id: str) -> bool:
        if self.spec is None or not source_node_id:
            return True
        current = self.spec.node(source_node_id)
        if current is None:
            return True
        if target_node_id == source_node_id:
            return True
        reachable = {node.node_id for node in self.spec.reachable_from(source_node_id)}
        return target_node_id in reachable


def gate_event_payload(
    decision: GateDecision,
    *,
    evaluation_id: str | None = None,
    gate_type: str = "enter",
) -> dict:
    return {
        "gate_evaluation": {
            "evaluation_id": evaluation_id or f"gate_{uuid4().hex[:12]}",
            "gate_type": gate_type,
            "source_node_id": decision.source_node_id,
            "target_node_id": decision.target_node_id,
            "decision": decision.decision,
            "reason": decision.reason,
            "messages": decision.messages,
            "condition_results": decision.condition_results,
        }
    }


def _decision_from_results(
    results: list[ConditionResult],
    source_node_id: str,
    target_node_id: str,
    *,
    completion: bool = False,
) -> GateDecision:
    failed = [result for result in results if not result.passed and result.hard]
    payload_results = [
        {
            "condition_id": result.condition_id,
            "passed": result.passed,
            "tier": result.tier,
            "failure_mode": result.failure_mode,
            "message": result.message,
            "details": result.details or {},
        }
        for result in results
    ]
    if not failed:
        warnings = [result.message for result in results if not result.passed]
        return GateDecision(
            decision="warn" if warnings else "pass",
            source_node_id=source_node_id,
            target_node_id=target_node_id,
            condition_results=payload_results,
            messages=warnings,
            reason="; ".join(warnings),
        )
    if any(result.failure_mode == "skip_node" for result in failed):
        decision = "skip"
    elif any(result.failure_mode == "human_interrupt" or result.tier == "C" for result in failed):
        decision = "human_interrupt"
    elif any(result.failure_mode == "autonomous_recovery" or result.tier == "B" for result in failed):
        decision = "autonomous_recovery"
    else:
        decision = "block"
    messages = [result.message or result.condition_id for result in failed]
    return GateDecision(
        decision=decision,
        source_node_id=source_node_id,
        target_node_id=target_node_id,
        condition_results=payload_results,
        messages=messages,
        reason="; ".join(messages),
    )
