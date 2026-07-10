from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from pertura_core.hashing import canonical_hash


class DiagnosticStatus(str, Enum):
    screen_passed = "screen_passed"
    caution = "caution"
    blocked = "blocked"
    unresolved = "unresolved"
    failed = "failed"


class AnalysisStatus(str, Enum):
    completed = "completed"
    completed_with_caution = "completed_with_caution"
    blocked = "blocked"
    failed = "failed"


class VirtualStatus(str, Enum):
    supported = "supported"
    limited = "limited"
    out_of_scope = "out_of_scope"
    failed = "failed"


class CapabilityTrust(str, Enum):
    builtin_trusted = "builtin_trusted"
    installed_untrusted = "installed_untrusted"
    exploratory = "exploratory"


class SourceClass(str, Enum):
    observed_metadata = "observed_metadata"
    measured_result = "measured_result"
    prediction = "prediction"
    curated_prior = "curated_prior"
    hypothesis = "hypothesis"


class CanonicalModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, use_enum_values=False)

    schema_version: str
    canonical_hash: str = ""

    id_field: ClassVar[str]
    id_prefix: ClassVar[str]

    @model_validator(mode="after")
    def _set_and_validate_identity(self) -> "CanonicalModel":
        payload = self.model_dump(mode="json", exclude={"canonical_hash"})
        id_value = str(payload.get(self.id_field) or "")
        if not id_value:
            seed = dict(payload)
            seed.pop(self.id_field, None)
            id_value = f"{self.id_prefix}_{canonical_hash(seed).split(':', 1)[1][:20]}"
            object.__setattr__(self, self.id_field, id_value)
            payload[self.id_field] = id_value
        expected = canonical_hash(payload)
        if self.canonical_hash and self.canonical_hash != expected:
            raise ValueError(f"canonical_hash mismatch for {self.__class__.__name__}")
        object.__setattr__(self, "canonical_hash", expected)
        return self

    def canonical_payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"canonical_hash"})


class DependencyRef(CanonicalModel):
    schema_version: str = "pertura-dependency-v2"
    dependency_id: str = ""
    id_field: ClassVar[str] = "dependency_id"
    id_prefix: ClassVar[str] = "dep"

    kind: str
    object_id: str
    object_hash: str
    required: bool = True
    state: Literal["current", "stale", "missing"] = "current"
    role: str | None = None


class ScopeKey(CanonicalModel):
    schema_version: str = "pertura-scope-v2"
    scope_id: str = ""
    id_field: ClassVar[str] = "scope_id"
    id_prefix: ClassVar[str] = "scope"

    dataset_id: str
    perturbation_ids: tuple[str, ...] = ()
    control_ids: tuple[str, ...] = ()
    state_ids: tuple[str, ...] = ()
    donor_ids: tuple[str, ...] = ()
    replicate_ids: tuple[str, ...] = ()
    batch_ids: tuple[str, ...] = ()
    dose: str | None = None
    timepoint: str | None = None
    contrast_id: str | None = None
    estimand: str | None = None
    unresolved_fields: tuple[str, ...] = ()
    declared_compatibility_rules: tuple[str, ...] = ()
    metadata: dict[str, Any] = Field(default_factory=dict)


class DatasetContract(CanonicalModel):
    schema_version: str = "pertura-dataset-contract-v2"
    contract_id: str = ""
    id_field: ClassVar[str] = "contract_id"
    id_prefix: ClassVar[str] = "contract"

    dataset_id: str
    contract_version: int = 1
    parent_contract_id: str | None = None
    source_paths: tuple[str, ...] = ()
    input_format: str
    expression_matrix: dict[str, Any] = Field(default_factory=dict)
    guide_matrix: dict[str, Any] = Field(default_factory=dict)
    identity_fields: dict[str, dict[str, Any]] = Field(default_factory=dict)
    unresolved_fields: tuple[str, ...] = ()
    dependencies: tuple[DependencyRef, ...] = ()
    metadata: dict[str, Any] = Field(default_factory=dict)
    # Content contracts are deterministic. Registration time belongs to the
    # authority-store event, not the scientific identity.
    created_at_utc: str = ""


class CapabilitySpec(CanonicalModel):
    schema_version: str = "pertura-capability-spec-v2"
    capability_spec_id: str = ""
    id_field: ClassVar[str] = "capability_spec_id"
    id_prefix: ClassVar[str] = "capspec"

    capability_id: str
    version: str
    phase: int = Field(ge=1, le=7)
    kind: Literal["diagnostic", "analysis", "virtual", "report"]
    summary: str
    trust_level: CapabilityTrust = CapabilityTrust.builtin_trusted
    executor: str
    validator: str
    input_requirements: tuple[str, ...] = ()
    depends_on: tuple[str, ...] = ()
    dependency_kinds: tuple[str, ...] = ()
    output_kind: str
    source_class: SourceClass
    claim_permissions: tuple[str, ...] = ()
    timeout_seconds: int = Field(default=900, ge=1)
    parameters_schema: dict[str, Any] = Field(default_factory=dict)
    implemented: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)


class CapabilityRunRequest(CanonicalModel):
    schema_version: str = "pertura-capability-run-request-v2"
    request_id: str = ""
    id_field: ClassVar[str] = "request_id"
    id_prefix: ClassVar[str] = "request"

    run_id: str
    capability_id: str
    capability_version: str
    contract_id: str
    contract_hash: str
    scope: ScopeKey
    objective: str | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)
    dependencies: tuple[DependencyRef, ...] = ()
    requested_at_utc: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


ResultStatus = DiagnosticStatus | AnalysisStatus | VirtualStatus


class ResultEnvelope(CanonicalModel):
    schema_version: str = "pertura-result-envelope-v2"
    result_id: str = ""
    id_field: ClassVar[str] = "result_id"
    id_prefix: ClassVar[str] = "result"

    run_id: str
    request_id: str
    capability_id: str
    capability_version: str
    capability_trust: CapabilityTrust
    contract_id: str
    contract_hash: str
    scope: ScopeKey
    status: ResultStatus
    result_kind: str
    source_class: SourceClass
    summary: str
    blockers: tuple[str, ...] = ()
    cautions: tuple[str, ...] = ()
    metrics: dict[str, Any] = Field(default_factory=dict)
    output_paths: tuple[str, ...] = ()
    output_hashes: dict[str, str] = Field(default_factory=dict)
    dependencies: tuple[DependencyRef, ...] = ()
    receipt_id: str | None = None
    stale: bool = False
    completed_at_utc: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    metadata: dict[str, Any] = Field(default_factory=dict)


class RunReceipt(CanonicalModel):
    schema_version: str = "pertura-run-receipt-v2"
    receipt_id: str = ""
    id_field: ClassVar[str] = "receipt_id"
    id_prefix: ClassVar[str] = "receipt"

    run_id: str
    request_id: str
    result_id: str
    result_hash: str
    capability_id: str
    capability_version: str
    contract_id: str
    contract_hash: str
    scope_hash: str
    policy_hash: str
    dependency_hashes: dict[str, str] = Field(default_factory=dict)
    output_hashes: dict[str, str] = Field(default_factory=dict)
    broker_instance_id: str
    broker_exit_state: Literal["running", "sealed"] = "running"
    signed_at_utc: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    public_key: str
    signature: str = ""
    run_root_digest: str | None = None

    def signing_payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"canonical_hash", "signature"})


class ScientificStatement(CanonicalModel):
    schema_version: str = "pertura-scientific-statement-v2"
    statement_id: str = ""
    id_field: ClassVar[str] = "statement_id"
    id_prefix: ClassVar[str] = "statement"

    run_id: str
    text: str
    language: str = "en"
    source_class: SourceClass
    scope: ScopeKey
    result_ids: tuple[str, ...] = ()
    requested_strength: str
    limitations: tuple[str, ...] = ()
    metadata: dict[str, Any] = Field(default_factory=dict)


class PromotionDecision(CanonicalModel):
    schema_version: str = "pertura-promotion-decision-v2"
    decision_id: str = ""
    id_field: ClassVar[str] = "decision_id"
    id_prefix: ClassVar[str] = "promotion"

    run_id: str
    statement_id: str
    status: Literal["promoted", "downgraded", "blocked"]
    max_strength: str
    source_class: SourceClass
    result_ids: tuple[str, ...] = ()
    receipt_ids: tuple[str, ...] = ()
    dependency_ids: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()
    policy_hash: str
    decided_at_utc: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class DesignConfirmation(CanonicalModel):
    schema_version: str = "pertura-design-confirmation-v2"
    confirmation_id: str = ""
    id_field: ClassVar[str] = "confirmation_id"
    id_prefix: ClassVar[str] = "confirmation"

    run_id: str
    contract_id: str
    field: Literal["control", "guide_target", "replicate", "state_label", "donor", "batch"]
    value: Any
    rationale: str
    confirmed_by: str = "user"
    created_at_utc: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
