"""Core entity Pydantic models: Event, Snapshot, and all graph nodes."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _model_dump(obj) -> dict:
    if isinstance(obj, dict):
        return obj
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json")
    if hasattr(obj, "dict"):
        return obj.dict()
    return obj


# ── Event log ───────────────────────────────────────────────────────────

class Event(BaseModel):
    event_id: str
    event_type: str
    run_id: str
    timestamp: datetime = Field(default_factory=_now)
    actor: str = "system"
    payload: dict[str, Any] = Field(default_factory=dict)


# ── Budget ──────────────────────────────────────────────────────────────

class Budget(BaseModel):
    max_attempts: int = 20
    max_branches: int = 3
    max_repairs: int = 3


# ── Core nodes ──────────────────────────────────────────────────────────

class Attempt(BaseModel):
    attempt_id: str
    branch_id: str = "main"
    analysis_node_id: str = ""
    title: str = ""
    objective: str = ""
    stage: str = ""
    status: str = "planned"
    notebook_cells: list[dict] = Field(default_factory=list)
    capability_ids: list[str] = Field(default_factory=list)
    parameters: dict[str, Any] = Field(default_factory=dict)
    expected_artifacts: list[str] = Field(default_factory=list)
    required_validators: list[str] = Field(default_factory=list)
    design_fields_used: list[str] = Field(default_factory=list)
    parent_ids: list[str] = Field(default_factory=list)
    parent_intervention: str = ""
    repair_count: int = 0
    rationale: str = ""
    created_at: datetime = Field(default_factory=_now)


class Outcome(BaseModel):
    outcome_id: str
    attempt_id: str
    status: str = "success"
    summary: str = ""
    metrics: dict[str, Any] = Field(default_factory=dict)


class Artifact(BaseModel):
    artifact_id: str
    attempt_id: str = ""
    path: str = ""
    kind: str = ""
    summary: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class Observation(BaseModel):
    """A typed scientific reading extracted from analysis output.

    Every quantitative finding — effect size, coverage, concordance,
    module score — is an Observation. The graph uses Observations to
    detect conflicts, thin evidence, and parameter sensitivity across
    attempts and branches.
    """
    observation_id: str
    type: str = "custom"
    target: str = ""
    metric: str = ""
    value: Any = None
    contrast: str = ""
    method: str = ""
    parameters: dict[str, Any] = Field(default_factory=dict)
    uncertainty: dict[str, Any] = Field(default_factory=dict)
    attempt_id: str = ""
    branch_id: str = ""
    artifact_id: str = ""
    variable_key: str = ""
    input_ids: list[str] = Field(default_factory=list)
    design_fields_used: list[str] = Field(default_factory=list)
    parameter_hash: str = ""
    method_version: str = ""
    created_at: datetime = Field(default_factory=_now)


class ReviewTrigger(BaseModel):
    trigger_id: str
    attempt_id: str = ""
    trigger_type: str = ""
    severity: str = "warning"
    summary: str = ""
    status: str = "open"


class Finding(BaseModel):
    finding_id: str
    attempt_id: str = ""
    finding_type: str = "continue_ok"
    severity: str = "info"
    suggested_action: str = "continue"
    summary: str = ""
    affected_ids: list[str] = Field(default_factory=list)


class Branch(BaseModel):
    branch_id: str
    title: str = ""
    parent_id: str = ""
    anchor_attempt_id: str = ""
    anchor_node_id: str = ""
    reason: str = "main"
    question: str = ""
    hypothesis: str = ""
    status: str = "active"
    summary: str = ""
    conclusion: str = ""
    evidence_ids: list[str] = Field(default_factory=list)


class Goal(BaseModel):
    goal_id: str
    text: str = ""
    status: str = "active"


class Conclusion(BaseModel):
    conclusion_id: str
    text: str = ""
    grade: str = "inconclusive"
    support_ids: list[str] = Field(default_factory=list)
    limitation_ids: list[str] = Field(default_factory=list)


class Intervention(BaseModel):
    intervention_id: str
    trigger_id: str = ""
    intervention_type: str = ""
    status: str = "proposed"
    summary: str = ""
    target_ids: list[str] = Field(default_factory=list)
    notebook_cells: list[dict] = Field(default_factory=list)
    params: dict[str, Any] = Field(default_factory=dict)
    branch_reason: str = ""
    created_at: str = ""


class ToolCall(BaseModel):
    tool_call_id: str
    tool_name: str = ""
    arguments: dict[str, Any] = Field(default_factory=dict)
    result_summary: str = ""
    attempt_id: str = ""
    branch_id: str = ""


class Interrupt(BaseModel):
    interrupt_id: str
    source: str = ""
    trigger_id: str = ""
    question: str = ""
    options: list[str] = Field(default_factory=list)
    default_action: str = "ask_user"
    status: str = "open"


class ApprovalRequest(BaseModel):
    approval_id: str
    subject_id: str = ""
    subject_type: str = "patch"
    approval_type: str = ""
    reason: str = ""
    status: str = "open"
    decision: str = ""
    resolved_by: str = ""
    created_at: datetime = Field(default_factory=_now)


class BehaviorRun(BaseModel):
    behavior_run_id: str
    behavior_id: str = ""
    status: str = "started"
    trigger_event_ids: list[str] = Field(default_factory=list)
    output_event_ids: list[str] = Field(default_factory=list)
    output_count: int = 0
    error: str = ""
    created_at: datetime = Field(default_factory=_now)


class NodeVisit(BaseModel):
    visit_id: str
    node_id: str
    branch_id: str = "main"
    status: str = "active"
    reason: str = ""
    entered_at: datetime = Field(default_factory=_now)
    completed_at: datetime | None = None


class GateEvaluation(BaseModel):
    evaluation_id: str
    gate_type: str = "enter"
    source_node_id: str = ""
    target_node_id: str = ""
    decision: str = "pass"
    reason: str = ""
    messages: list[str] = Field(default_factory=list)
    condition_results: list[dict[str, Any]] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=_now)


# ── Graph (derived from events) ─────────────────────────────────────────

class GraphNode(BaseModel):
    node_id: str
    node_type: str
    label: str
    summary: str = ""
    status: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class GraphEdge(BaseModel):
    source_id: str
    target_id: str
    edge_type: str


class AttemptGraph(BaseModel):
    run_id: str = ""
    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)


class AssistantResponse(BaseModel):
    response_id: str
    text: str = ""
    reason: str = ""


class ReviewDecision(BaseModel):
    review_id: str
    attempt_id: str = ""
    action: str = ""
    assessment_status: str = ""
    assessment_summary: str = ""
    reason: str = ""
    evidence_ids: list[str] = Field(default_factory=list)


# ── Snapshot (full state from event replay) ─────────────────────────────

class Snapshot(BaseModel):
    run_id: str = ""
    phase: str = "initialized"
    workspace: str = ""
    goal: str = ""
    domain: str = ""
    attempts: list[Attempt] = Field(default_factory=list)
    outcomes: list[Outcome] = Field(default_factory=list)
    artifacts: list[Artifact] = Field(default_factory=list)
    observations: list[Observation] = Field(default_factory=list)
    triggers: list[ReviewTrigger] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)
    interventions: list[Intervention] = Field(default_factory=list)
    assistant_responses: list[AssistantResponse] = Field(default_factory=list)
    review_decisions: list[ReviewDecision] = Field(default_factory=list)
    tool_calls: list[ToolCall] = Field(default_factory=list)
    interrupts: list[Interrupt] = Field(default_factory=list)
    approvals: list[ApprovalRequest] = Field(default_factory=list)
    behavior_runs: list[BehaviorRun] = Field(default_factory=list)
    node_visits: list[NodeVisit] = Field(default_factory=list)
    gate_evaluations: list[GateEvaluation] = Field(default_factory=list)
    branches: list[Branch] = Field(default_factory=list)
    goals: list[Goal] = Field(default_factory=list)
    conclusions: list[Conclusion] = Field(default_factory=list)
    patch_proposals: list[Any] = Field(default_factory=list)
    capabilities: list[dict] = Field(default_factory=list)
    analysis_spec: dict[str, Any] = Field(default_factory=dict)
    active_node_id: str = ""
    design: dict[str, Any] = Field(default_factory=dict)
    design_meta: dict[str, Any] = Field(default_factory=dict)
    protocol: str = ""
    budget: Budget = Field(default_factory=Budget)
    active_branch: str = "main"
    active_attempt: str = ""
