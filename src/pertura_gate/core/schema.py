from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any


class ArtifactKind(str, Enum):
    perturbation_design_manifest = "perturbation_design_manifest"
    experiment_design = "experiment_design"
    guide_assignment = "guide_assignment"
    target_qc = "target_qc"
    measured_de = "measured_de"
    perturbation_efficiency = "perturbation_efficiency"
    cell_qc = "cell_qc"
    predicted_effect = "predicted_effect"
    curated_prior_lookup = "curated_prior_lookup"
    curated_enrichment_result = "curated_enrichment_result"
    module_effect = "module_effect"
    global_effect = "global_effect"
    replication_summary = "replication_summary"
    scope_artifact = "scope_artifact"
    measured_effect = "measured_effect"
    inferred_structure = "inferred_structure"
    prediction_artifact = "prediction_artifact"
    ranking_artifact = "ranking_artifact"
    qc_summary = "qc_summary"
    guide_summary = "guide_summary"
    generic_observation = "generic_observation"


class EvidenceClass(str, Enum):
    observed_metadata = "observed_metadata"
    observation = "observed_metadata"
    measured = "measured"
    predicted = "predicted"
    curated_prior = "curated_prior"
    measured_inferred = "measured_inferred"
    composite_summary = "composite_summary"
    composite = "composite_summary"


class ArtifactRole(str, Enum):
    scope_definition = "scope_definition"
    analysis_eligibility = "analysis_eligibility"
    effect_evidence = "effect_evidence"
    prior_context = "prior_context"
    prediction_evidence = "prediction_evidence"
    ranking_summary = "ranking_summary"


class EvidenceTier(str, Enum):
    observation = "observation"
    measured = "measured"
    predicted = "predicted"
    curated_prior = "curated_prior"
    composite = "composite"
    unsupported = "unsupported"


class StrengthCeiling(str, Enum):
    unsupported = "unsupported"
    observation = "observation"
    curated_prior_support = "curated_prior_support"
    predicted_effect = "predicted_effect"
    measured_target_engagement = "measured_target_engagement"
    measured_association = "measured_association"
    replicated_measured_association = "replicated_measured_association"
    validated_mechanism_disabled = "validated_mechanism_disabled"


class ScopeFit(str, Enum):
    exact = "exact"
    compatible = "compatible"
    weaker = "weaker"
    mismatch = "mismatch"
    unknown = "unknown"


class ClaimDecisionState(str, Enum):
    allowed = "allowed"
    allowed_with_downgrade = "allowed_with_downgrade"
    unsupported = "unsupported"


@dataclass(frozen=True)
class EligibilityProfile:
    perturbation_cell_mapping: dict[str, Any] = field(default_factory=dict)
    control_definition: dict[str, Any] = field(default_factory=dict)
    target_qc: dict[str, Any] = field(default_factory=dict)
    cell_qc: dict[str, Any] = field(default_factory=dict)
    perturbation_scope: dict[str, Any] = field(default_factory=dict)
    replicate_scope: dict[str, Any] = field(default_factory=dict)
    assay_modality: str | None = None
    perturbation_modality: str | None = None
    moi: str | int | float | None = None
    moi_compatibility: str | None = None
    estimand: str | None = None
    control_calibration: dict[str, Any] = field(default_factory=dict)
    sources: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "perturbation_cell_mapping": dict(self.perturbation_cell_mapping),
            "control_definition": dict(self.control_definition),
            "target_qc": dict(self.target_qc),
            "cell_qc": dict(self.cell_qc),
            "perturbation_scope": dict(self.perturbation_scope),
            "replicate_scope": dict(self.replicate_scope),
            "assay_modality": self.assay_modality,
            "perturbation_modality": self.perturbation_modality,
            "moi": self.moi,
            "moi_compatibility": self.moi_compatibility,
            "estimand": self.estimand,
            "control_calibration": dict(self.control_calibration),
            "sources": list(self.sources),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> "EligibilityProfile":
        data = dict(payload or {})
        return cls(
            perturbation_cell_mapping=_dict_field(data.get("perturbation_cell_mapping")),
            control_definition=_dict_field(data.get("control_definition")),
            target_qc=_dict_field(data.get("target_qc")),
            cell_qc=_dict_field(data.get("cell_qc")),
            perturbation_scope=_dict_field(data.get("perturbation_scope")),
            replicate_scope=_dict_field(data.get("replicate_scope")),
            assay_modality=data.get("assay_modality"),
            perturbation_modality=data.get("perturbation_modality"),
            moi=data.get("moi"),
            moi_compatibility=data.get("moi_compatibility"),
            estimand=data.get("estimand"),
            control_calibration=_dict_field(data.get("control_calibration")),
            sources=[str(item) for item in data.get("sources") or []],
        )


def _dict_field(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


@dataclass(frozen=True)
class EvidenceArtifact:
    artifact_id: str
    kind: ArtifactKind
    path: str
    evidence_class: EvidenceClass | None = None
    artifact_roles: list[ArtifactRole | str] = field(default_factory=list)
    schema_version: str = "pertura-evidence-v1"
    adapter_version: str = "pertura-gate-v1"
    created_by: str = "claude_codeact"
    contrast_left: str | None = None
    contrast_baseline: str | None = None
    method: str | None = None
    n_left: int | None = None
    n_baseline: int | None = None
    multiple_testing: str | None = None
    has_padj: bool = False
    columns: list[str] = field(default_factory=list)
    source_data: str | None = None
    notes: str | None = None
    scope: dict[str, Any] = field(default_factory=dict)
    predicate: dict[str, Any] = field(default_factory=dict)
    quality: dict[str, Any] = field(default_factory=dict)
    eligibility: dict[str, Any] = field(default_factory=dict)
    provenance: dict[str, Any] = field(default_factory=dict)
    source_sha256: str | None = None
    code_sha256: str | None = None
    execution_hash: str | None = None
    provenance_level: str = "runtime_registered"
    created_at_utc: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def effective_evidence_class(self) -> EvidenceClass:
        if self.evidence_class is not None:
            return self.evidence_class
        return default_evidence_class(self.kind)

    def to_dict(self) -> dict[str, Any]:
        roles = [item.value if isinstance(item, ArtifactRole) else str(item) for item in self.artifact_roles]
        return {
            "artifact_id": self.artifact_id,
            "kind": self.kind.value,
            "artifact_type": self.kind.value,
            "evidence_class": self.effective_evidence_class.value,
            "artifact_roles": roles,
            "schema_version": self.schema_version,
            "adapter_version": self.adapter_version,
            "path": self.path,
            "created_by": self.created_by,
            "contrast": {
                "left": self.contrast_left,
                "baseline": self.contrast_baseline,
            },
            "method": self.method,
            "n_left": self.n_left,
            "n_baseline": self.n_baseline,
            "multiple_testing": self.multiple_testing,
            "has_padj": self.has_padj,
            "columns": list(self.columns),
            "source_data": self.source_data,
            "notes": self.notes,
            "scope": dict(self.scope),
            "predicate": dict(self.predicate),
            "quality": dict(self.quality),
            "eligibility": dict(self.eligibility),
            "provenance": dict(self.provenance),
            "source_sha256": self.source_sha256,
            "code_sha256": self.code_sha256,
            "execution_hash": self.execution_hash,
            "provenance_level": self.provenance_level,
            "created_at_utc": self.created_at_utc,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "EvidenceArtifact":
        contrast = payload.get("contrast") or {}
        kind = ArtifactKind(payload.get("kind") or payload.get("artifact_type") or ArtifactKind.generic_observation.value)
        evidence_class_raw = payload.get("evidence_class")
        evidence_class = EvidenceClass(evidence_class_raw) if evidence_class_raw else default_evidence_class(kind)
        return cls(
            artifact_id=str(payload["artifact_id"]),
            kind=kind,
            evidence_class=evidence_class,
            artifact_roles=[_artifact_role(item) for item in payload.get("artifact_roles") or []],
            schema_version=str(payload.get("schema_version") or "pertura-evidence-v1"),
            adapter_version=str(payload.get("adapter_version") or "pertura-gate-v1"),
            path=str(payload["path"]),
            created_by=str(payload.get("created_by") or "claude_codeact"),
            contrast_left=payload.get("contrast_left") or contrast.get("left"),
            contrast_baseline=payload.get("contrast_baseline") or contrast.get("baseline"),
            method=payload.get("method"),
            n_left=_optional_int(payload.get("n_left")),
            n_baseline=_optional_int(payload.get("n_baseline")),
            multiple_testing=payload.get("multiple_testing"),
            has_padj=bool(payload.get("has_padj", False)),
            columns=[str(item) for item in payload.get("columns") or []],
            source_data=payload.get("source_data"),
            notes=payload.get("notes"),
            scope=dict(payload.get("scope") or {}),
            predicate=dict(payload.get("predicate") or {}),
            quality=dict(payload.get("quality") or {}),
            eligibility=dict(payload.get("eligibility") or {}),
            provenance=dict(payload.get("provenance") or {}),
            source_sha256=payload.get("source_sha256"),
            code_sha256=payload.get("code_sha256"),
            execution_hash=payload.get("execution_hash"),
            provenance_level=str(payload.get("provenance_level") or "runtime_registered"),
            created_at_utc=str(payload.get("created_at_utc") or datetime.now(timezone.utc).isoformat()),
            metadata=dict(payload.get("metadata") or {}),
        )


@dataclass(frozen=True)
class ResolvedStrength:
    artifact_id: str
    tier: EvidenceTier
    ceiling: StrengthCeiling
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "tier": self.tier.value,
            "ceiling": self.ceiling.value,
            "artifact_intrinsic_ceiling": self.ceiling.value,
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True)
class Claim:
    claim_id: str
    text: str
    subject: dict[str, Any] = field(default_factory=dict)
    relation: str | None = None
    object: dict[str, Any] = field(default_factory=dict)
    scope: dict[str, Any] = field(default_factory=dict)
    requested_strength: StrengthCeiling | str | None = None
    evidence_refs: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        requested = self.requested_strength.value if isinstance(self.requested_strength, StrengthCeiling) else self.requested_strength
        return {
            "claim_id": self.claim_id,
            "text": self.text,
            "subject": dict(self.subject),
            "relation": self.relation,
            "object": dict(self.object),
            "scope": dict(self.scope),
            "requested_strength": requested,
            "evidence_refs": list(self.evidence_refs),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "Claim":
        requested = payload.get("requested_strength")
        try:
            requested_value: StrengthCeiling | str | None = StrengthCeiling(requested) if requested else None
        except ValueError:
            requested_value = str(requested)
        return cls(
            claim_id=str(payload.get("claim_id") or "claim"),
            text=str(payload.get("text") or ""),
            subject=dict(payload.get("subject") or {}),
            relation=payload.get("relation"),
            object=dict(payload.get("object") or {}),
            scope=dict(payload.get("scope") or {}),
            requested_strength=requested_value,
            evidence_refs=[str(item) for item in payload.get("evidence_refs") or []],
        )


@dataclass(frozen=True)
class ClaimDecision:
    decision_id: str
    claim_id: str
    decision: ClaimDecisionState
    max_strength: StrengthCeiling
    evidence_classes: list[EvidenceClass] = field(default_factory=list)
    scope_fit: ScopeFit = ScopeFit.unknown
    supporting_artifacts: list[str] = field(default_factory=list)
    missing_artifacts: list[str] = field(default_factory=list)
    blocked_requested_strength: StrengthCeiling | str | None = None
    allowed_surface: str = ""
    reasons: list[str] = field(default_factory=list)
    policy_version: str = "pertura-gate-v1"
    policy_hash: str = ""
    resolver_version: str = "pertura-gate-resolver-v1"

    def to_dict(self) -> dict[str, Any]:
        blocked = self.blocked_requested_strength.value if isinstance(self.blocked_requested_strength, StrengthCeiling) else self.blocked_requested_strength
        return {
            "decision_id": self.decision_id,
            "claim_id": self.claim_id,
            "decision": self.decision.value,
            "max_strength": self.max_strength.value,
            "claim_strength_ceiling": self.max_strength.value,
            "evidence_classes": [item.value for item in self.evidence_classes],
            "scope_fit": self.scope_fit.value,
            "supporting_artifacts": list(self.supporting_artifacts),
            "missing_artifacts": list(self.missing_artifacts),
            "blocked_requested_strength": blocked,
            "allowed_surface": self.allowed_surface,
            "reasons": list(self.reasons),
            "policy_version": self.policy_version,
            "policy_hash": self.policy_hash,
            "resolver_version": self.resolver_version,
        }


@dataclass(frozen=True)
class RenderedReport:
    markdown: str
    artifacts: list[EvidenceArtifact]
    resolutions: list[ResolvedStrength]
    decisions: list[ClaimDecision] = field(default_factory=list)
    report_path: Path | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "markdown": self.markdown,
            "artifacts": [artifact.to_dict() for artifact in self.artifacts],
            "resolutions": [resolution.to_dict() for resolution in self.resolutions],
            "decisions": [decision.to_dict() for decision in self.decisions],
            "report_path": str(self.report_path) if self.report_path else None,
        }


def default_evidence_class(kind: ArtifactKind) -> EvidenceClass:
    if kind in {ArtifactKind.measured_de, ArtifactKind.measured_effect, ArtifactKind.global_effect, ArtifactKind.module_effect, ArtifactKind.perturbation_efficiency}:
        return EvidenceClass.measured
    if kind in {ArtifactKind.predicted_effect, ArtifactKind.prediction_artifact}:
        return EvidenceClass.predicted
    if kind in {ArtifactKind.curated_prior_lookup, ArtifactKind.curated_enrichment_result}:
        return EvidenceClass.curated_prior
    if kind == ArtifactKind.replication_summary:
        return EvidenceClass.composite_summary
    if kind == ArtifactKind.inferred_structure:
        return EvidenceClass.measured_inferred
    return EvidenceClass.observed_metadata


def _artifact_role(value: Any) -> ArtifactRole | str:
    try:
        return ArtifactRole(str(value))
    except ValueError:
        return str(value)


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    return int(value)



