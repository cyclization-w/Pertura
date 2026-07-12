from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from pertura_core.hashing import canonical_hash


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


class InternalModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TurnStatus(StrEnum):
    running = "running"
    completed = "completed"
    needs_input = "needs_input"
    blocked = "blocked"
    failed = "failed"
    cancelled = "cancelled"


class ProjectRecord(InternalModel):
    schema_version: Literal["pertura-project-v1"] = "pertura-project-v1"
    project_id: str = Field(default_factory=lambda: new_id("project"))
    logical_name: str
    created_at: datetime = Field(default_factory=utc_now)
    active_run_id: str | None = None


class AnalysisRunRecord(InternalModel):
    schema_version: Literal["pertura-analysis-run-v1"] = "pertura-analysis-run-v1"
    run_id: str = Field(default_factory=lambda: new_id("run"))
    project_id: str
    logical_name: str
    status: Literal["active", "completed", "legacy_unverified"] = "active"
    created_at: datetime = Field(default_factory=utc_now)
    active_turn_id: str | None = None
    contract_id: str | None = None


class ConversationRecord(InternalModel):
    schema_version: Literal["pertura-conversation-v1"] = "pertura-conversation-v1"
    conversation_id: str = Field(default_factory=lambda: new_id("conversation"))
    project_id: str
    run_id: str
    title: str = "Pertura analysis"
    created_at: datetime = Field(default_factory=utc_now)
    status: Literal["active", "closed"] = "active"


class ProviderSessionBinding(InternalModel):
    schema_version: Literal["pertura-provider-binding-v1"] = "pertura-provider-binding-v1"
    binding_id: str = Field(default_factory=lambda: new_id("binding"))
    conversation_id: str
    provider_id: str
    provider_session_id: str
    model: str | None = None
    tool_hash: str
    skill_bundle_hash: str
    configuration_hash: str
    created_at: datetime = Field(default_factory=utc_now)
    active: bool = True
    continuity_reason: str | None = None


class TurnFindingDraft(InternalModel):
    finding_id: str
    text: str
    declared_role: Literal[
        "measured", "derived", "prediction", "prior", "contradiction", "hypothesis"
    ] = "hypothesis"
    result_ids: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()


class TurnDraft(InternalModel):
    schema_version: Literal["pertura-turn-draft-v1"] = "pertura-turn-draft-v1"
    language: str = "en"
    headline: str
    findings: tuple[TurnFindingDraft, ...] = ()
    hypotheses: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()
    questions_for_user: tuple[str, ...] = ()
    next_steps: tuple[str, ...] = ()
    artifact_refs: tuple[str, ...] = ()


class TurnFinal(InternalModel):
    schema_version: Literal["pertura-turn-final-v1"] = "pertura-turn-final-v1"
    turn_id: str
    status: TurnStatus
    language: str = "en"
    headline: str
    markdown: str
    findings: tuple[dict[str, Any], ...] = ()
    hypotheses: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()
    questions_for_user: tuple[str, ...] = ()
    next_steps: tuple[str, ...] = ()
    artifact_refs: tuple[str, ...] = ()
    result_ids: tuple[str, ...] = ()
    artifact_ids: tuple[str, ...] = ()
    structured: bool = True
    claim_authority: bool = False
    format_error: str | None = None
    created_at: datetime = Field(default_factory=utc_now)


class TurnRecord(InternalModel):
    schema_version: Literal["pertura-turn-record-v1"] = "pertura-turn-record-v1"
    turn_id: str = Field(default_factory=lambda: new_id("turn"))
    conversation_id: str
    run_id: str
    sequence: int
    status: TurnStatus = TurnStatus.running
    user_input: str
    provider_binding_id: str | None = None
    provider_final: str | None = None
    result_ids: tuple[str, ...] = ()
    artifact_ids: tuple[str, ...] = ()
    usage: dict[str, Any] = Field(default_factory=dict)
    trace: dict[str, Any] = Field(default_factory=dict)
    started_at: datetime = Field(default_factory=utc_now)
    completed_at: datetime | None = None


class DataAssetRef(InternalModel):
    schema_version: Literal["pertura-data-asset-v1"] = "pertura-data-asset-v1"
    asset_id: str
    project_id: str
    kind: Literal["observed", "external_resource", "exploratory", "derived"]
    role: str
    format: str
    logical_name: str
    content_sha256: str | None = None
    upstream_lock_hash: str | None = None
    size_bytes: int = Field(ge=0)
    source_class: Literal[
        "observed_metadata", "measured_result", "prediction", "curated_prior", "hypothesis"
    ]
    created_by_turn: str | None = None
    dependencies: tuple[str, ...] = ()
    status: Literal["current", "missing", "drifted"] = "current"

    @model_validator(mode="after")
    def _identity_source(self) -> "DataAssetRef":
        if bool(self.content_sha256) == bool(self.upstream_lock_hash):
            raise ValueError("exactly one of content_sha256 or upstream_lock_hash is required")
        return self

    @property
    def identity_hash(self) -> str:
        return canonical_hash(
            self.model_dump(
                mode="json",
                exclude={"status", "created_by_turn", "logical_name"},
            )
        )


class AssetLocation(InternalModel):
    schema_version: Literal["pertura-asset-location-v1"] = "pertura-asset-location-v1"
    location_id: str = Field(default_factory=lambda: new_id("location"))
    asset_id: str
    absolute_path: str
    storage_mode: Literal["reference", "object", "hardlink", "copy", "logical_binding"]
    observed_sha256: str | None = None
    observed_size_bytes: int | None = None
    checked_at: datetime = Field(default_factory=utc_now)


class AssetBinding(InternalModel):
    schema_version: Literal["pertura-asset-binding-v1"] = "pertura-asset-binding-v1"
    binding_id: str = Field(default_factory=lambda: new_id("asset_binding"))
    run_id: str
    asset_id: str
    role: str
    created_at: datetime = Field(default_factory=utc_now)


class ReportRevision(InternalModel):
    schema_version: Literal["pertura-report-revision-v1"] = "pertura-report-revision-v1"
    report_id: str
    run_id: str
    revision: int = Field(ge=1)
    digest: str
    turn_final_ids: tuple[str, ...] = ()
    json_path: str
    markdown_path: str
    created_at: datetime = Field(default_factory=utc_now)
