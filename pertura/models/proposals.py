"""Proposal models for LLM-generated plans."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from .entities import _now


class NotebookCell(BaseModel):
    role: str = "execute"
    title: str = ""
    source: str


class AttemptProposal(BaseModel):
    proposal_id: str = ""
    title: str = ""
    objective: str = ""
    stage: str = ""
    capability_ids: list[str] = Field(default_factory=list)
    notebook_cells: list[NotebookCell] = Field(default_factory=list)
    expected_artifacts: list[str] = Field(default_factory=list)
    required_validators: list[str] = Field(default_factory=list)
    parameters: dict[str, Any] = Field(default_factory=dict)
    rationale: str = ""


class InterventionProposal(BaseModel):
    proposal_id: str = ""
    trigger_id: str = ""
    intervention_type: str = ""
    target_ids: list[str] = Field(default_factory=list)
    notebook_cells: list[NotebookCell] = Field(default_factory=list)
    rationale: str = ""
    params: dict[str, Any] = Field(default_factory=dict)
    branch_reason: str = ""


class PatchProposal(BaseModel):
    patch_id: str
    patch_type: str = ""
    proposed_by: str = "llm"
    status: str = "proposed"
    rationale: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)
    applied_event_ids: list[str] = Field(default_factory=list)
    rejection_reason: str = ""
    created_at: Any = Field(default_factory=_now)
