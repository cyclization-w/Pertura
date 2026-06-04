"""Context models compiled from Snapshot for LLM consumption."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class MemoryEntry(BaseModel):
    subject: str = ""
    metric: str = ""
    current_value: Any = None
    prior_values: list[dict] = Field(default_factory=list)
    signal: str = "data"  # conflict, agreement, thin, confirmed, data
    summary: str = ""


class CoverageEntry(BaseModel):
    subject: str = ""
    methods: int = 0
    branches: int = 0
    observations: int = 0
    contradictions: int = 0
    label: str = "no_coverage"


class IntentEntry(BaseModel):
    branch_id: str = ""
    intent: str = ""
    drift: str = "low"
    summary: str = ""


class Context(BaseModel):
    run_id: str = ""
    phase: str = ""
    goal: str = ""
    active_branch: str = "main"
    active_stage: str = ""
    active_node_id: str = ""
    analysis_node: dict[str, Any] = Field(default_factory=dict)
    current_node_progress: dict[str, Any] = Field(default_factory=dict)
    reachable_nodes: list[dict] = Field(default_factory=list)
    blocked_transitions: list[dict] = Field(default_factory=list)
    gate_requirements: list[dict] = Field(default_factory=list)
    design: dict[str, Any] = Field(default_factory=dict)
    design_meta: dict[str, Any] = Field(default_factory=dict)
    attempts_done: int = 0
    budget_remaining: dict[str, int] = Field(default_factory=dict)
    open_triggers: list[dict] = Field(default_factory=list)
    open_approvals: list[dict] = Field(default_factory=list)
    open_interrupts: list[dict] = Field(default_factory=list)
    capabilities: list[dict] = Field(default_factory=list)
    recent_attempts: list[dict] = Field(default_factory=list)
    recent_artifacts: list[dict] = Field(default_factory=list)
    memory: list[MemoryEntry] = Field(default_factory=list)
    coverage: list[CoverageEntry] = Field(default_factory=list)
    observation_memory: dict[str, Any] = Field(default_factory=dict)
    intent: list[IntentEntry] = Field(default_factory=list)
    recent_findings: list[dict] = Field(default_factory=list)
    graph_summary: dict[str, Any] = Field(default_factory=dict)
    protocol: str = ""
    workspace_files: list[dict] = Field(default_factory=list)
    truncated: bool = False
