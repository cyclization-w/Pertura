from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pertura_gate.core.schema import (
    ArtifactKind,
    ArtifactRole,
    EvidenceClass,
    EvidencePredicate,
    StrengthCeiling,
)


@dataclass(frozen=True)
class EvidenceRegistrationSpec:
    required_fields: frozenset[str] = frozenset()
    scope_mapping: dict[str, str] = field(default_factory=dict)
    quality_mapping: dict[str, str] = field(default_factory=dict)
    predicate_mapping: dict[str, str] = field(default_factory=dict)
    eligibility_mapping: dict[str, str] = field(default_factory=dict)
    registration_adapter: str | None = None


@dataclass(frozen=True)
class EvidenceTypeDefinition:
    type_id: str
    artifact_kind: ArtifactKind
    evidence_class: EvidenceClass
    evidence_predicate: EvidencePredicate
    intrinsic_ceiling: StrengthCeiling
    default_roles: tuple[ArtifactRole, ...] = ()
    registration: EvidenceRegistrationSpec = field(default_factory=EvidenceRegistrationSpec)


def _registration(adapter: str | None = None, *required: str) -> EvidenceRegistrationSpec:
    return EvidenceRegistrationSpec(required_fields=frozenset(required), registration_adapter=adapter)


EVIDENCE_CATALOG: dict[str, EvidenceTypeDefinition] = {
    "perturbation_design_manifest": EvidenceTypeDefinition(
        "perturbation_design_manifest",
        ArtifactKind.perturbation_design_manifest,
        EvidenceClass.observed_metadata,
        EvidencePredicate.scope_definition,
        StrengthCeiling.observation,
        (ArtifactRole.scope_definition,),
        _registration("register_perturbation_design_manifest"),
    ),
    "cell_state_reference": EvidenceTypeDefinition(
        "cell_state_reference",
        ArtifactKind.cell_state_reference,
        EvidenceClass.observed_metadata,
        EvidencePredicate.state_context,
        StrengthCeiling.observation,
        (ArtifactRole.scope_definition, ArtifactRole.state_context),
        _registration("register_cell_state_reference"),
    ),
    "experiment_design": EvidenceTypeDefinition(
        "experiment_design",
        ArtifactKind.experiment_design,
        EvidenceClass.observed_metadata,
        EvidencePredicate.analysis_eligibility,
        StrengthCeiling.observation,
        (ArtifactRole.scope_definition, ArtifactRole.analysis_eligibility),
        _registration("register_experiment_design"),
    ),
    "guide_assignment": EvidenceTypeDefinition(
        "guide_assignment",
        ArtifactKind.guide_assignment,
        EvidenceClass.observed_metadata,
        EvidencePredicate.analysis_eligibility,
        StrengthCeiling.observation,
        (ArtifactRole.analysis_eligibility,),
        _registration("register_guide_assignment"),
    ),
    "target_qc": EvidenceTypeDefinition(
        "target_qc",
        ArtifactKind.target_qc,
        EvidenceClass.observed_metadata,
        EvidencePredicate.analysis_eligibility,
        StrengthCeiling.observation,
        (ArtifactRole.analysis_eligibility,),
        _registration("register_target_qc"),
    ),
    "cell_qc": EvidenceTypeDefinition(
        "cell_qc",
        ArtifactKind.cell_qc,
        EvidenceClass.observed_metadata,
        EvidencePredicate.analysis_eligibility,
        StrengthCeiling.observation,
        (ArtifactRole.analysis_eligibility,),
        _registration("register_cell_qc"),
    ),
    "control_calibration": EvidenceTypeDefinition(
        "control_calibration",
        ArtifactKind.control_calibration,
        EvidenceClass.observed_metadata,
        EvidencePredicate.analysis_eligibility,
        StrengthCeiling.observation,
        (ArtifactRole.analysis_eligibility,),
        _registration("register_control_calibration"),
    ),
    "measured_de": EvidenceTypeDefinition(
        "measured_de",
        ArtifactKind.measured_de,
        EvidenceClass.measured,
        EvidencePredicate.differential_expression,
        StrengthCeiling.measured_association,
        (ArtifactRole.effect_evidence,),
        _registration("register_measured_de", "contrast_left", "contrast_baseline", "method", "n_left", "n_baseline", "multiple_testing", "has_padj"),
    ),
    "perturbation_efficiency": EvidenceTypeDefinition(
        "perturbation_efficiency",
        ArtifactKind.perturbation_efficiency,
        EvidenceClass.measured,
        EvidencePredicate.target_engagement,
        StrengthCeiling.measured_target_engagement,
        (ArtifactRole.effect_evidence,),
        _registration("register_perturbation_efficiency"),
    ),
    "module_effect": EvidenceTypeDefinition(
        "module_effect",
        ArtifactKind.module_effect,
        EvidenceClass.measured,
        EvidencePredicate.module_score_shift,
        StrengthCeiling.measured_association,
        (ArtifactRole.effect_evidence,),
        _registration("register_module_effect"),
    ),
    "global_effect": EvidenceTypeDefinition(
        "global_effect",
        ArtifactKind.global_effect,
        EvidenceClass.measured,
        EvidencePredicate.global_transcriptomic_shift,
        StrengthCeiling.measured_association,
        (ArtifactRole.effect_evidence,),
        _registration("register_global_effect"),
    ),
    "composition_effect": EvidenceTypeDefinition(
        "composition_effect",
        ArtifactKind.composition_effect,
        EvidenceClass.measured,
        EvidencePredicate.cell_state_composition_shift,
        StrengthCeiling.measured_association,
        (ArtifactRole.effect_evidence,),
        _registration("register_composition_effect"),
    ),
    "measured_effect": EvidenceTypeDefinition(
        "measured_effect",
        ArtifactKind.measured_effect,
        EvidenceClass.measured,
        EvidencePredicate.metadata_observation,
        StrengthCeiling.observation,
        (ArtifactRole.effect_evidence,),
    ),
    "predicted_effect": EvidenceTypeDefinition(
        "predicted_effect",
        ArtifactKind.predicted_effect,
        EvidenceClass.predicted,
        EvidencePredicate.predicted_effect,
        StrengthCeiling.predicted_effect,
        (ArtifactRole.prediction_evidence,),
        _registration("register_predicted_effect", "model_name"),
    ),
    "virtual_perturbation_prediction": EvidenceTypeDefinition(
        "virtual_perturbation_prediction",
        ArtifactKind.virtual_perturbation_prediction,
        EvidenceClass.predicted,
        EvidencePredicate.predicted_perturbation_response,
        StrengthCeiling.predicted_effect,
        (ArtifactRole.prediction_evidence,),
        _registration("register_virtual_perturbation_prediction"),
    ),
    "prediction_measured_concordance": EvidenceTypeDefinition(
        "prediction_measured_concordance",
        ArtifactKind.prediction_measured_concordance,
        EvidenceClass.predicted,
        EvidencePredicate.prediction_measured_concordance,
        StrengthCeiling.predicted_effect,
        (ArtifactRole.prediction_evidence,),
        _registration("register_prediction_measured_concordance"),
    ),
    "virtual_cell_state_transition": EvidenceTypeDefinition(
        "virtual_cell_state_transition",
        ArtifactKind.virtual_cell_state_transition,
        EvidenceClass.predicted,
        EvidencePredicate.predicted_cell_state_transition,
        StrengthCeiling.predicted_effect,
        (ArtifactRole.prediction_evidence,),
        _registration("register_virtual_cell_state_transition"),
    ),
    "prediction_artifact": EvidenceTypeDefinition(
        "prediction_artifact",
        ArtifactKind.prediction_artifact,
        EvidenceClass.predicted,
        EvidencePredicate.predicted_effect,
        StrengthCeiling.predicted_effect,
        (ArtifactRole.prediction_evidence,),
    ),
    "curated_prior_lookup": EvidenceTypeDefinition(
        "curated_prior_lookup",
        ArtifactKind.curated_prior_lookup,
        EvidenceClass.curated_prior,
        EvidencePredicate.curated_prior_context,
        StrengthCeiling.curated_prior_support,
        (ArtifactRole.prior_context,),
        _registration("register_curated_prior", "database"),
    ),
    "curated_enrichment_result": EvidenceTypeDefinition(
        "curated_enrichment_result",
        ArtifactKind.curated_enrichment_result,
        EvidenceClass.curated_prior,
        EvidencePredicate.curated_enrichment_context,
        StrengthCeiling.curated_prior_support,
        (ArtifactRole.prior_context,),
        _registration("register_curated_enrichment", "input_measured_artifact_id", "database", "database_version", "term_id", "method"),
    ),
    "replication_summary": EvidenceTypeDefinition(
        "replication_summary",
        ArtifactKind.replication_summary,
        EvidenceClass.composite_summary,
        EvidencePredicate.replication_summary,
        StrengthCeiling.replicated_measured_association,
        (ArtifactRole.effect_evidence,),
        _registration("register_replication"),
    ),
    "scope_artifact": EvidenceTypeDefinition(
        "scope_artifact",
        ArtifactKind.scope_artifact,
        EvidenceClass.observed_metadata,
        EvidencePredicate.scope_definition,
        StrengthCeiling.observation,
        (ArtifactRole.scope_definition,),
    ),
    "inferred_structure": EvidenceTypeDefinition(
        "inferred_structure",
        ArtifactKind.inferred_structure,
        EvidenceClass.measured_inferred,
        EvidencePredicate.metadata_observation,
        StrengthCeiling.observation,
        (ArtifactRole.effect_evidence,),
    ),
    "ranking_artifact": EvidenceTypeDefinition(
        "ranking_artifact",
        ArtifactKind.ranking_artifact,
        EvidenceClass.composite_summary,
        EvidencePredicate.metadata_observation,
        StrengthCeiling.observation,
        (ArtifactRole.ranking_summary,),
    ),
    "qc_summary": EvidenceTypeDefinition(
        "qc_summary",
        ArtifactKind.qc_summary,
        EvidenceClass.observed_metadata,
        EvidencePredicate.analysis_eligibility,
        StrengthCeiling.observation,
        (ArtifactRole.analysis_eligibility,),
    ),
    "guide_summary": EvidenceTypeDefinition(
        "guide_summary",
        ArtifactKind.guide_summary,
        EvidenceClass.observed_metadata,
        EvidencePredicate.analysis_eligibility,
        StrengthCeiling.observation,
        (ArtifactRole.analysis_eligibility,),
    ),
    "dataset_metadata": EvidenceTypeDefinition(
        "dataset_metadata",
        ArtifactKind.scope_artifact,
        EvidenceClass.observed_metadata,
        EvidencePredicate.scope_definition,
        StrengthCeiling.observation,
        (ArtifactRole.scope_definition,),
    ),
    "generic_observation": EvidenceTypeDefinition(
        "generic_observation",
        ArtifactKind.generic_observation,
        EvidenceClass.observed_metadata,
        EvidencePredicate.metadata_observation,
        StrengthCeiling.observation,
        (),
    ),
}

_KIND_INDEX: dict[ArtifactKind, EvidenceTypeDefinition] = {}
for definition in EVIDENCE_CATALOG.values():
    _KIND_INDEX.setdefault(definition.artifact_kind, definition)


def get_evidence_type(type_id: str) -> EvidenceTypeDefinition | None:
    return EVIDENCE_CATALOG.get(str(type_id))


def evidence_type_exists(type_id: str) -> bool:
    return str(type_id) in EVIDENCE_CATALOG


def evidence_type_for_kind(kind: ArtifactKind) -> EvidenceTypeDefinition | None:
    return _KIND_INDEX.get(kind)


def artifact_kind_type_ids() -> set[str]:
    return {definition.artifact_kind.value for definition in EVIDENCE_CATALOG.values()}
