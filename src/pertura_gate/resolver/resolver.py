from __future__ import annotations

from dataclasses import dataclass, replace
from uuid import NAMESPACE_URL, uuid5
from typing import Any

from pertura_gate.identity.design_manifest import compare_manifest_scope, manifest_scope_is_strong
from pertura_gate.core.policy import DEFAULT_POLICY, GatePolicy
from pertura_gate.core.schema import (
    ArtifactKind,
    Claim,
    ClaimDecision,
    ClaimDecisionState,
    EligibilityProfile,
    EvidenceArtifact,
    EvidenceClass,
    EvidenceTier,
    ResolvedStrength,
    ScopeFit,
    StrengthCeiling,
)
from pertura_gate.identity.scope import compare_scope
from pertura_gate.resolver.binding import resolve_bound_measured_artifact, resolve_prediction_measured_binding
from pertura_gate.evidence.execution_ledger import artifact_execution_is_in_ledger
from pertura_gate.resolver.warrant import (
    best_resolution as choose_best_resolution,
    intrinsic_warrant,
    surface_for_claim,
)



_ELIGIBILITY_KINDS = {
    ArtifactKind.experiment_design,
    ArtifactKind.guide_assignment,
    ArtifactKind.target_qc,
    ArtifactKind.cell_qc,
    ArtifactKind.control_calibration,
}


@dataclass(frozen=True)
class EligibilityValidation:
    profile: EligibilityProfile
    passed: bool
    reasons: list[str]
    sources: list[str]




@dataclass(frozen=True)
class MeasuredPredicateSpec:
    kind: ArtifactKind
    expected_ceiling: StrengthCeiling
    scope_reason: str
    success_reasons: tuple[str, ...] = ()
    full_eligibility: bool = False
    safety_checks: tuple[str, ...] = (
        "cell_qc",
        "trusted_method",
        "replicate",
        "confound",
        "control_calibration",
    )
    claim_guards: tuple[str, ...] = ()


_MEASURED_PREDICATE_SPECS: dict[ArtifactKind, MeasuredPredicateSpec] = {
    ArtifactKind.measured_de: MeasuredPredicateSpec(
        kind=ArtifactKind.measured_de,
        expected_ceiling=StrengthCeiling.measured_association,
        scope_reason="measured association requires claim and artifact scope to resolve through a PerturbationDesignManifest UID",
        success_reasons=("measured DE artifact has resolved contrast, sample counts, method, and multiple-testing metadata",),
        full_eligibility=True,
        safety_checks=("cell_qc", "trusted_method", "replicate", "confound", "control_calibration", "guide_power"),
    ),
    ArtifactKind.perturbation_efficiency: MeasuredPredicateSpec(
        kind=ArtifactKind.perturbation_efficiency,
        expected_ceiling=StrengthCeiling.measured_target_engagement,
        scope_reason="target engagement requires claim and artifact scope to resolve through a PerturbationDesignManifest UID",
        safety_checks=("cell_qc", "trusted_method", "replicate", "confound", "control_calibration", "guide_power"),
    ),
    ArtifactKind.module_effect: MeasuredPredicateSpec(
        kind=ArtifactKind.module_effect,
        expected_ceiling=StrengthCeiling.measured_association,
        scope_reason="module_effect requires claim and artifact scope to resolve through a PerturbationDesignManifest UID",
    ),
    ArtifactKind.global_effect: MeasuredPredicateSpec(
        kind=ArtifactKind.global_effect,
        expected_ceiling=StrengthCeiling.measured_association,
        scope_reason="global_effect requires claim and artifact scope to resolve through a PerturbationDesignManifest UID",
        claim_guards=("not_gene_specific_de",),
    ),
    ArtifactKind.composition_effect: MeasuredPredicateSpec(
        kind=ArtifactKind.composition_effect,
        expected_ceiling=StrengthCeiling.measured_association,
        scope_reason="composition_effect requires claim and artifact scope to resolve through a PerturbationDesignManifest UID",
        claim_guards=("not_gene_specific_de", "not_target_engagement"),
    ),
}
def resolve_artifact_strength(artifact: EvidenceArtifact | None, *, policy: GatePolicy = DEFAULT_POLICY) -> ResolvedStrength:
    """Resolve the artifact-intrinsic ceiling supported by execution facts."""

    return intrinsic_warrant(artifact, policy=policy)

def resolve_claim(claim: Claim | dict, registry, policy: GatePolicy = DEFAULT_POLICY) -> ClaimDecision:
    """Resolve a claim-specific ceiling from runtime-registered artifacts."""

    claim_obj = Claim.from_dict(claim) if isinstance(claim, dict) else claim
    if hasattr(registry, "resolve_manifest_scope"):
        normalized_scope = registry.resolve_manifest_scope(claim_obj.scope)
        if normalized_scope != claim_obj.scope:
            claim_obj = replace(claim_obj, scope=normalized_scope)
    artifacts: list[EvidenceArtifact] = []
    missing_refs: list[str] = []
    for ref in claim_obj.evidence_refs:
        artifact = registry.get(ref)
        if artifact is None:
            missing_refs.append(ref)
        else:
            artifacts.append(artifact)

    if not claim_obj.evidence_refs:
        return _decision(
            claim_obj,
            StrengthCeiling.unsupported,
            ClaimDecisionState.unsupported,
            ScopeFit.unknown,
            [],
            ["evidence_refs"],
            ["claim does not reference a registered artifact"],
            policy,
        )

    if not artifacts:
        return _decision(
            claim_obj,
            StrengthCeiling.unsupported,
            ClaimDecisionState.unsupported,
            ScopeFit.unknown,
            [],
            missing_refs,
            ["no referenced artifact could be resolved"],
            policy,
        )

    candidate_artifacts: list[EvidenceArtifact] = []
    candidate_resolutions: list[ResolvedStrength] = []
    scope_fits: list[ScopeFit] = []
    reasons: list[str] = []
    for artifact in artifacts:
        scope_fit = compare_scope(claim_obj.scope, artifact.scope)
        if scope_fit == ScopeFit.mismatch:
            reasons.append(f"artifact {artifact.artifact_id} has mismatched scope")
            continue
        candidate_artifacts.append(artifact)
        candidate_resolutions.append(_resolve_claim_artifact(claim_obj, artifact, registry, policy=policy))
        scope_fits.append(scope_fit)

    if not candidate_artifacts:
        return _decision(
            claim_obj,
            StrengthCeiling.unsupported,
            ClaimDecisionState.unsupported,
            ScopeFit.mismatch,
            [],
            missing_refs,
            reasons or ["referenced artifacts do not match claim scope"],
            policy,
        )

    best_resolution = choose_best_resolution(candidate_resolutions)
    supporting = [artifact.artifact_id for artifact in candidate_artifacts]
    evidence_classes = _unique_classes(candidate_artifacts)
    scope_fit = _merge_scope_fits(scope_fits)
    decision_state = _decision_state(claim_obj, best_resolution.ceiling)
    blocked = _blocked_strength(claim_obj, best_resolution.ceiling)
    decision_reasons = list(reasons)
    for resolution in candidate_resolutions:
        decision_reasons.extend(resolution.reasons)
    if missing_refs:
        decision_reasons.append(f"unresolved evidence refs: {', '.join(missing_refs)}")
    return ClaimDecision(
        decision_id=_decision_id(claim_obj.claim_id, supporting, policy.policy_hash),
        claim_id=claim_obj.claim_id,
        decision=decision_state,
        max_strength=best_resolution.ceiling,
        evidence_classes=evidence_classes,
        scope_fit=scope_fit,
        supporting_artifacts=supporting,
        missing_artifacts=missing_refs,
        blocked_requested_strength=blocked,
        allowed_surface=surface_for_claim(claim_obj, best_resolution.ceiling, candidate_artifacts, evidence_classes),
        reasons=_dedupe(decision_reasons),
        policy_version=policy.version,
        policy_hash=policy.policy_hash,
        resolver_version=policy.resolver_version,
    )


def resolve_claims(claims: list[Claim | dict], registry, policy: GatePolicy = DEFAULT_POLICY) -> list[ClaimDecision]:
    return [resolve_claim(claim, registry, policy=policy) for claim in claims]


def _resolve_claim_artifact(claim: Claim, artifact: EvidenceArtifact, registry, *, policy: GatePolicy) -> ResolvedStrength:
    if artifact.kind in _MEASURED_PREDICATE_SPECS:
        return _resolve_measured_predicate_claim(claim, artifact, _MEASURED_PREDICATE_SPECS[artifact.kind], registry, policy=policy)
    if artifact.kind == ArtifactKind.prediction_measured_concordance:
        return _resolve_prediction_measured_concordance(claim, artifact, registry, policy=policy)
    if artifact.kind == ArtifactKind.curated_enrichment_result:
        return _resolve_curated_enrichment(claim, artifact, registry, policy=policy)
    if artifact.kind == ArtifactKind.replication_summary:
        return _resolve_replication_summary(claim, artifact, registry, policy=policy)
    return resolve_artifact_strength(artifact, policy=policy)



def _resolve_measured_predicate_claim(
    claim: Claim,
    artifact: EvidenceArtifact,
    spec: MeasuredPredicateSpec,
    registry,
    *,
    policy: GatePolicy,
) -> ResolvedStrength:
    intrinsic = resolve_artifact_strength(artifact, policy=policy)
    if intrinsic.ceiling != spec.expected_ceiling:
        return intrinsic
    manifest_scope_fit = compare_manifest_scope(claim.scope, artifact.scope)
    if not manifest_scope_is_strong(manifest_scope_fit):
        return ResolvedStrength(
            artifact_id=artifact.artifact_id,
            tier=EvidenceTier.measured,
            ceiling=StrengthCeiling.observation,
            reasons=[spec.scope_reason],
        )
    guard_reasons = _measured_predicate_guard_reasons(claim, artifact, spec)
    if guard_reasons:
        return ResolvedStrength(
            artifact_id=artifact.artifact_id,
            tier=EvidenceTier.measured,
            ceiling=StrengthCeiling.observation,
            reasons=guard_reasons,
        )
    if spec.full_eligibility:
        if not policy.require_measured_eligibility_for_claims:
            return intrinsic
        eligibility = validate_measured_association_eligibility(claim, artifact, registry, policy=policy)
        if not eligibility.passed:
            return ResolvedStrength(
                artifact_id=artifact.artifact_id,
                tier=EvidenceTier.measured,
                ceiling=StrengthCeiling.observation,
                reasons=[
                    "measured effect artifact lacks a validated EligibilityProfile for this claim",
                    *eligibility.reasons,
                ],
            )
        return ResolvedStrength(
            artifact_id=artifact.artifact_id,
            tier=EvidenceTier.measured,
            ceiling=spec.expected_ceiling,
            reasons=[
                *spec.success_reasons,
                "EligibilityProfile satisfied: " + ", ".join(eligibility.sources),
            ],
        )
    safety_reasons = _statistical_safety_reasons_for_claim_artifact(claim, artifact, registry, policy=policy, checks=spec.safety_checks)
    if safety_reasons:
        return ResolvedStrength(
            artifact_id=artifact.artifact_id,
            tier=EvidenceTier.measured,
            ceiling=StrengthCeiling.observation,
            reasons=safety_reasons,
        )
    return intrinsic


def _measured_predicate_guard_reasons(claim: Claim, artifact: EvidenceArtifact, spec: MeasuredPredicateSpec) -> list[str]:
    reasons: list[str] = []
    if "not_gene_specific_de" in spec.claim_guards and _claim_requests_gene_specific_effect(claim):
        if artifact.kind == ArtifactKind.global_effect:
            reasons.append("global-effect artifact does not support gene-specific differential-expression claims")
        elif artifact.kind == ArtifactKind.composition_effect:
            reasons.append("composition-effect artifact does not support gene-specific differential-expression claims")
        else:
            reasons.append(f"{artifact.kind.value} artifact does not support gene-specific differential-expression claims")
    if "not_target_engagement" in spec.claim_guards and _claim_requests_target_engagement(claim):
        reasons.append(f"{artifact.kind.value.replace('_', '-')} artifact does not support target-engagement claims")
    return reasons


def _resolve_replication_summary(
    claim: Claim,
    artifact: EvidenceArtifact,
    registry,
    *,
    policy: GatePolicy,
) -> ResolvedStrength:
    intrinsic = resolve_artifact_strength(artifact, policy=policy)
    if intrinsic.ceiling != StrengthCeiling.replicated_measured_association:
        return intrinsic
    resolved_ids = [str(item) for item in artifact.quality.get("resolved_artifact_ids") or artifact.quality.get("measured_artifact_ids") or []]
    valid_ids: list[str] = []
    reasons = list(intrinsic.reasons)
    for artifact_id in resolved_ids:
        measured = registry.get(artifact_id)
        if measured is None:
            reasons.append(f"replication summary references missing measured artifact {artifact_id}")
            continue
        if measured.artifact_id == artifact.artifact_id:
            continue
        measured_claim = claim if claim.scope else replace(claim, scope=dict(measured.scope))
        measured_resolution = _resolve_claim_artifact(measured_claim, measured, registry, policy=policy)
        if measured_resolution.ceiling == StrengthCeiling.measured_association:
            valid_ids.append(measured.artifact_id)
        else:
            reasons.append(f"replicated artifact {measured.artifact_id} does not independently support this claim under policy")
            reasons.extend(measured_resolution.reasons)
    if len(set(valid_ids)) < policy.replication_min_artifacts:
        return ResolvedStrength(
            artifact_id=artifact.artifact_id,
            tier=EvidenceTier.composite,
            ceiling=StrengthCeiling.observation,
            reasons=reasons or ["replication summary does not resolve enough claim-compatible measured artifacts"],
        )
    return ResolvedStrength(
        artifact_id=artifact.artifact_id,
        tier=EvidenceTier.composite,
        ceiling=StrengthCeiling.replicated_measured_association,
        reasons=["replication summary references measured artifacts that independently support this claim under policy"],
    )


def _resolve_prediction_measured_concordance(
    claim: Claim,
    artifact: EvidenceArtifact,
    registry,
    *,
    policy: GatePolicy,
) -> ResolvedStrength:
    intrinsic = resolve_artifact_strength(artifact, policy=policy)
    if intrinsic.ceiling != StrengthCeiling.predicted_effect:
        return intrinsic
    binding = resolve_prediction_measured_binding(claim, artifact, registry)
    reasons = [*intrinsic.reasons, *binding.reasons]
    if binding.reported_scope_match:
        reasons.append(f"reported scope_match {binding.reported_scope_match!r} was recorded as diagnostic only")
    if not binding.resolved or binding.measured_artifact is None:
        return ResolvedStrength(
            artifact_id=artifact.artifact_id,
            tier=EvidenceTier.predicted,
            ceiling=StrengthCeiling.observation,
            reasons=reasons,
        )
    measured_resolution = _resolve_claim_artifact(claim, binding.measured_artifact, registry, policy=policy)
    if measured_resolution.ceiling in {StrengthCeiling.measured_association, StrengthCeiling.measured_target_engagement}:
        reasons.append("bound measured artifact independently supports the measured claim; concordance remains prediction-level context")
        return ResolvedStrength(
            artifact_id=artifact.artifact_id,
            tier=EvidenceTier.predicted,
            ceiling=StrengthCeiling.predicted_effect,
            reasons=reasons,
        )
    reasons.append("bound measured artifact does not independently support measured strength for this claim; concordance remains observational context")
    reasons.extend(measured_resolution.reasons)
    return ResolvedStrength(
        artifact_id=artifact.artifact_id,
        tier=EvidenceTier.predicted,
        ceiling=StrengthCeiling.observation,
        reasons=reasons,
    )

def _resolve_curated_enrichment(
    claim: Claim,
    artifact: EvidenceArtifact,
    registry,
    *,
    policy: GatePolicy,
) -> ResolvedStrength:
    input_id = artifact.quality.get("input_measured_artifact_id") or artifact.predicate.get("input_measured_artifact_id")
    required = ["input_gene_set_hash", "background_universe", "database", "database_version", "term_id", "method", "padj"]
    missing = [field for field in required if not artifact.quality.get(field) and not artifact.predicate.get(field)]
    if not input_id:
        return ResolvedStrength(
            artifact_id=artifact.artifact_id,
            tier=EvidenceTier.curated_prior,
            ceiling=StrengthCeiling.curated_prior_support,
            reasons=["curated enrichment is not bound to a registered measured artifact"],
        )
    binding = resolve_bound_measured_artifact(claim, artifact, str(input_id), registry)
    if not binding.resolved or binding.measured_artifact is None:
        return ResolvedStrength(
            artifact_id=artifact.artifact_id,
            tier=EvidenceTier.curated_prior,
            ceiling=StrengthCeiling.curated_prior_support,
            reasons=binding.reasons or ["curated enrichment could not resolve a UID-compatible measured binding"],
        )
    measured_resolution = _resolve_claim_artifact(claim, binding.measured_artifact, registry, policy=policy)
    if measured_resolution.ceiling != StrengthCeiling.measured_association:
        return ResolvedStrength(
            artifact_id=artifact.artifact_id,
            tier=EvidenceTier.curated_prior,
            ceiling=StrengthCeiling.curated_prior_support,
            reasons=["bound measured artifact does not support a measured association for this claim", *measured_resolution.reasons],
        )
    if missing:
        return ResolvedStrength(
            artifact_id=artifact.artifact_id,
            tier=EvidenceTier.curated_prior,
            ceiling=StrengthCeiling.curated_prior_support,
            reasons=["curated enrichment lacks required enrichment metadata: " + ", ".join(missing)],
        )
    return ResolvedStrength(
        artifact_id=artifact.artifact_id,
        tier=EvidenceTier.curated_prior,
        ceiling=StrengthCeiling.measured_association,
        reasons=["curated enrichment is bound to a runtime-validated measured association and provides curated-prior context only"],
    )

def validate_measured_association_eligibility(
    claim: Claim,
    artifact: EvidenceArtifact,
    registry,
    *,
    policy: GatePolicy = DEFAULT_POLICY,
) -> EligibilityValidation:
    profile = _build_eligibility_profile(claim, artifact, registry, policy=policy)
    reasons: list[str] = []

    modality = _norm(profile.perturbation_modality or profile.assay_modality or "guide_based_perturb_seq")
    is_chemical = modality in {_norm(item) for item in policy.chemical_modalities}
    mapping = profile.perturbation_cell_mapping
    control = profile.control_definition
    target_qc = profile.target_qc
    cell_qc = _merged_cell_qc(profile)

    if is_chemical:
        if not _first(mapping, "treatment_assignment_method", "assignment_method", "dose", "time"):
            reasons.append("chemical perturbation requires structured treatment/dose/time assignment")
    elif not _first(mapping, "assignment_method", "guide_assignment_method", "guide_to_target_map_hash"):
        reasons.append("guide-based claim requires structured perturbation-cell mapping, not prose")

    if not _first(control, "negative_controls", "negative_control_label", "control_label", "control_labels", "vehicle_control"):
        reasons.append("negative control definition is missing")

    n_target = _optional_int(_first(target_qc, "n_target_cells", "n_treated", "n_left"))
    n_control = _optional_int(_first(target_qc, "n_control_cells", "n_control", "n_baseline"))
    if n_target is None:
        n_target = artifact.n_left
    if n_control is None:
        n_control = artifact.n_baseline
    if n_target is None or n_target < policy.minimum_measured_n:
        reasons.append(f"target cell count is below policy minimum {policy.minimum_measured_n}")
    if n_control is None or n_control < policy.minimum_measured_n:
        reasons.append(f"control cell count is below policy minimum {policy.minimum_measured_n}")

    if _is_high_moi(profile, policy):
        estimand = str(profile.estimand or _first(target_qc, "estimand") or "").strip()
        if not estimand:
            reasons.append("high-MOI measured association requires an explicit estimand")
        elif estimand == "single_target_marginal":
            reasons.append("high-MOI naive single-target marginal estimand is not eligible for measured association")
        elif estimand == "single_target_conditional" and not _first(target_qc, "model_covariates", "covariates_documented"):
            reasons.append("high-MOI conditional estimand requires documented covariates")
        elif estimand not in set(policy.allowed_high_moi_estimands):
            reasons.append(f"estimand {estimand!r} is not allowed by policy")

    reasons.extend(_validate_cell_qc(cell_qc, policy))
    reasons.extend(_validate_statistical_safety(profile, artifact, policy, registry=registry))

    if _truthy(_first(target_qc, "eligibility_passed", "guide_assignment_passed", "target_qc_passed")) and len(_structured_keys(profile)) < 3:
        reasons.append("boolean/prose eligibility flags are ignored without structured, hashable fields")

    return EligibilityValidation(profile=profile, passed=not reasons, reasons=reasons, sources=profile.sources or [artifact.artifact_id])


def _build_eligibility_profile(
    claim: Claim,
    measured: EvidenceArtifact,
    registry,
    *,
    policy: GatePolicy = DEFAULT_POLICY,
) -> EligibilityProfile:
    merged: dict[str, Any] = {}
    sources: list[str] = []

    def merge_from(source: dict[str, Any] | None, source_id: str, source_artifact: EvidenceArtifact | None = None) -> None:
        if not source:
            return
        normalized = _normalize_eligibility(source)
        if "control_calibration" in normalized:
            normalized["control_calibration"] = _annotate_control_calibration_source(
                normalized["control_calibration"],
                source_artifact,
                policy,
                registry=registry,
            )
        _deep_merge(merged, normalized)
        if source_id not in sources:
            sources.append(source_id)

    merge_from(measured.eligibility, measured.artifact_id, measured)
    if measured.quality.get("eligibility"):
        merge_from(dict(measured.quality.get("eligibility") or {}), measured.artifact_id, measured)

    explicit_ids = _explicit_eligibility_artifact_ids(measured)
    for artifact_id in explicit_ids:
        artifact = registry.get(artifact_id)
        if artifact is None or artifact.kind not in _ELIGIBILITY_KINDS:
            continue
        if not _eligibility_scope_can_support(claim.scope or measured.scope, measured.scope, artifact.scope):
            continue
        merge_from(artifact.eligibility, artifact.artifact_id, artifact)

    # Legacy-v1 compatibility only. The capability kernel never calls this path:
    # its promotion engine consumes explicit ResultEnvelope dependencies. Keep the
    # historical registry projection readable until the P3 write-surface removal.
    for artifact in registry.list():
        if artifact.artifact_id == measured.artifact_id or artifact.kind not in _ELIGIBILITY_KINDS:
            continue
        if artifact.artifact_id in explicit_ids:
            continue
        if not _eligibility_scope_can_support(claim.scope or measured.scope, measured.scope, artifact.scope):
            continue
        merge_from(artifact.eligibility, artifact.artifact_id, artifact)

    profile = EligibilityProfile.from_dict(merged)
    target_qc = dict(profile.target_qc)
    target_qc.setdefault("n_target_cells", measured.n_left)
    target_qc.setdefault("n_control_cells", measured.n_baseline)
    control = dict(profile.control_definition)
    if measured.contrast_baseline and not any(key in control for key in ["control_label", "negative_control_label", "negative_controls", "control_labels"]):
        control.setdefault("claimed_baseline", measured.contrast_baseline)
    return EligibilityProfile(
        perturbation_cell_mapping=profile.perturbation_cell_mapping,
        control_definition=control,
        target_qc=target_qc,
        cell_qc=profile.cell_qc,
        perturbation_scope=profile.perturbation_scope,
        replicate_scope=profile.replicate_scope,
        assay_modality=profile.assay_modality,
        perturbation_modality=profile.perturbation_modality,
        moi=profile.moi,
        moi_compatibility=profile.moi_compatibility,
        estimand=profile.estimand,
        control_calibration=profile.control_calibration,
        sources=sources,
    )



def _explicit_eligibility_artifact_ids(measured: EvidenceArtifact) -> list[str]:
    ids: list[str] = []
    for source in [measured.eligibility, measured.quality, measured.metadata]:
        if not isinstance(source, dict):
            continue
        for key, value in source.items():
            if key in {"experiment_design_id", "guide_assignment_id", "target_qc_id", "cell_qc_id"} or key.endswith("_artifact_id"):
                if isinstance(value, str) and value and value not in ids:
                    ids.append(value)
    return ids


def _eligibility_scope_can_support(claim_scope: dict[str, Any] | None, measured_scope: dict[str, Any] | None, artifact_scope: dict[str, Any] | None) -> bool:
    artifact_scope = artifact_scope or {}
    claim_scope = claim_scope or {}
    measured_scope = measured_scope or {}

    artifact_manifest = artifact_scope.get("design_manifest_id")
    expected_manifest = claim_scope.get("design_manifest_id") or measured_scope.get("design_manifest_id")
    if artifact_manifest and expected_manifest and artifact_manifest != expected_manifest:
        return False
    if artifact_manifest and expected_manifest and artifact_manifest == expected_manifest:
        has_artifact_uid = bool(artifact_scope.get("perturbation_uid") or artifact_scope.get("control_uid") or artifact_scope.get("contrast_uid"))
        if not has_artifact_uid:
            return True

    fit = compare_manifest_scope(claim_scope, artifact_scope)
    if fit == ScopeFit.mismatch:
        return False
    if fit in {ScopeFit.exact, ScopeFit.compatible}:
        return True

    legacy_fit = compare_scope(claim_scope or measured_scope, artifact_scope)
    return legacy_fit in {ScopeFit.exact, ScopeFit.compatible}

def _normalize_eligibility(payload: dict[str, Any]) -> dict[str, Any]:
    data = dict(payload or {})
    normalized = {
        "perturbation_cell_mapping": _dict_section(data.get("perturbation_cell_mapping")),
        "control_definition": _dict_section(data.get("control_definition")),
        "target_qc": _dict_section(data.get("target_qc")),
        "cell_qc": _dict_section(data.get("cell_qc")),
        "perturbation_scope": _dict_section(data.get("perturbation_scope")),
        "replicate_scope": _dict_section(data.get("replicate_scope")),
        "control_calibration": _dict_section(data.get("control_calibration")),
    }
    mapping_aliases = [
        "assignment_method", "guide_assignment_method", "guide_to_target_map_hash", "assigned_count",
        "unassigned_count", "multi_guide_count", "moi_inference", "treatment_assignment_method", "dose", "time",
    ]
    control_aliases = ["negative_controls", "negative_control_label", "control_label", "control_labels", "vehicle_control"]
    target_aliases = [
        "n_target_cells", "n_control_cells", "n_treated", "n_control", "n_left", "n_baseline",
        "guides_per_target", "cells_per_guide", "guide_consistency", "min_cell_policy", "model_covariates",
        "covariates_documented", "eligibility_passed", "guide_assignment_passed", "target_qc_passed",
    ]
    cell_qc_aliases = [
        "cell_qc_passed", "qc_passed", "passed", "n_cells_after_qc", "post_qc_cells", "cells_after_qc",
        "qc_policy", "doublet_policy", "ambient_policy", "batch_qc",
    ]
    for key in mapping_aliases:
        if key in data:
            normalized["perturbation_cell_mapping"][key] = data[key]
    for key in control_aliases:
        if key in data:
            normalized["control_definition"][key] = data[key]
    for key in target_aliases:
        if key in data:
            normalized["target_qc"][key] = data[key]
    for key in cell_qc_aliases:
        if key in data:
            normalized["cell_qc"][key] = data[key]
    for key in ["replicate_axis", "replicate_unit", "n_replicates", "replicate_count", "n_replicate_units", "replicate_handling", "confound_flag", "confound_status", "batch_perturbation_confounding", "batch_confounding_status"]:
        if key in data:
            normalized["replicate_scope"][key] = data[key]
    for key in ["negative_control_status", "control_status", "ntc_vs_ntc_check", "label_permutation_check"]:
        if key in data:
            normalized["control_calibration"][key] = data[key]
    for key in ["assay_modality", "perturbation_modality", "moi", "moi_compatibility", "estimand"]:
        if key in data:
            normalized[key] = data[key]
    return {key: value for key, value in normalized.items() if value not in ({}, None, "")}



def _dict_section(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}

def _cell_qc_reasons_for_claim_artifact(
    claim: Claim,
    artifact: EvidenceArtifact,
    registry,
    *,
    policy: GatePolicy,
) -> list[str]:
    profile = _build_cell_qc_profile_for_claim_artifact(claim, artifact, registry)
    return _validate_cell_qc(_merged_cell_qc(profile), policy)


def _build_cell_qc_profile_for_claim_artifact(claim: Claim, artifact: EvidenceArtifact, registry) -> EligibilityProfile:
    merged: dict[str, Any] = {}

    def merge_from(source: dict[str, Any] | None) -> None:
        if source:
            _deep_merge(merged, _normalize_eligibility(source))

    merge_from(artifact.eligibility)
    if isinstance(artifact.quality.get("eligibility"), dict):
        merge_from(dict(artifact.quality.get("eligibility") or {}))

    for candidate in registry.list():
        if candidate.artifact_id == artifact.artifact_id or candidate.kind != ArtifactKind.cell_qc:
            continue
        if not _eligibility_scope_can_support(claim.scope or artifact.scope, artifact.scope, candidate.scope):
            continue
        merge_from(candidate.eligibility)

    return EligibilityProfile.from_dict(merged)


def _statistical_safety_reasons_for_claim_artifact(
    claim: Claim,
    artifact: EvidenceArtifact,
    registry,
    *,
    policy: GatePolicy,
    checks: tuple[str, ...] = ("cell_qc", "trusted_method", "replicate", "confound", "control_calibration", "guide_power"),
) -> list[str]:
    profile = _build_eligibility_profile(claim, artifact, registry, policy=policy)
    reasons: list[str] = []
    if "cell_qc" in checks:
        reasons.extend(_validate_cell_qc(_merged_cell_qc(profile), policy))
    reasons.extend(_validate_statistical_safety(profile, artifact, policy, registry=registry, checks=checks))
    return reasons


def is_trusted_execution(artifact: EvidenceArtifact, policy: GatePolicy, registry=None) -> bool:
    method = _norm(_first(artifact.quality, "method", "runner_method", "comparison_method", "scoring_method") or artifact.method or "")
    trusted_methods = {_norm(item) for item in policy.trusted_runner_methods}
    if not method or method not in trusted_methods:
        return False
    if policy.trusted_runner_requires_execution_hash and not artifact.execution_hash:
        return False
    if policy.trusted_runner_requires_ledger_entry:
        run_root = getattr(registry, "run_root", None)
        if run_root is None or not artifact_execution_is_in_ledger(artifact, run_root, method=method):
            return False
    return True


def _control_calibration_method(artifact: EvidenceArtifact | None) -> str:
    if artifact is None:
        return ""
    calibration = {}
    if isinstance(artifact.eligibility, dict):
        calibration = dict(artifact.eligibility.get("control_calibration") or {})
    return _norm(
        _first(artifact.quality, "method", "runner_method", "calibration_method")
        or artifact.method
        or _first(calibration, "method", "calibration_method")
        or ""
    )


def is_trusted_control_calibration(artifact: EvidenceArtifact | None, policy: GatePolicy, registry=None) -> bool:
    if artifact is None or artifact.kind != ArtifactKind.control_calibration:
        return False
    method = _control_calibration_method(artifact)
    trusted_methods = {_norm(item) for item in policy.trusted_calibration_methods}
    if not method or method not in trusted_methods:
        return False
    if policy.trusted_calibration_requires_execution_hash and not artifact.execution_hash:
        return False
    if policy.trusted_runner_requires_ledger_entry:
        run_root = getattr(registry, "run_root", None)
        if run_root is None or not artifact_execution_is_in_ledger(artifact, run_root, method=method):
            return False
    return True


def _annotate_control_calibration_source(
    control_calibration: dict[str, Any],
    source_artifact: EvidenceArtifact | None,
    policy: GatePolicy,
    *,
    registry=None,
) -> dict[str, Any]:
    annotated = dict(control_calibration or {})
    if not annotated:
        return annotated
    source_id = source_artifact.artifact_id if source_artifact is not None else None
    source_kind = source_artifact.kind.value if source_artifact is not None else "inline"
    source_method = _control_calibration_method(source_artifact)
    source_trusted = is_trusted_control_calibration(source_artifact, policy, registry=registry)
    annotated.setdefault("_source_artifact_id", source_id)
    annotated.setdefault("_source_kind", source_kind)
    annotated.setdefault("_source_method", source_method)
    annotated.setdefault("_source_trusted", source_trusted)
    for key in ["ntc_vs_ntc_check", "label_permutation_check"]:
        check = annotated.get(key)
        if isinstance(check, dict):
            check = dict(check)
            check.setdefault("_source_artifact_id", source_id)
            check.setdefault("_source_kind", source_kind)
            check.setdefault("_source_method", source_method)
            check.setdefault("_source_trusted", source_trusted)
            annotated[key] = check
    return annotated


def _validate_statistical_safety(
    profile: EligibilityProfile,
    artifact: EvidenceArtifact,
    policy: GatePolicy,
    *,
    registry=None,
    checks: tuple[str, ...] = ("trusted_method", "replicate", "confound", "control_calibration", "guide_power"),
) -> list[str]:
    reasons: list[str] = []
    trusted = is_trusted_execution(artifact, policy, registry=registry)
    if "trusted_method" in checks and policy.require_trusted_method_for_measured_claims and not trusted:
        reasons.append("strict policy requires trusted runner provenance with an allowed method and execution hash")
    if "replicate" in checks:
        reasons.extend(_validate_replicate_scope(profile.replicate_scope, policy, trusted))
    if "confound" in checks:
        reasons.extend(_validate_confounding(profile.replicate_scope, policy))
    if "control_calibration" in checks:
        reasons.extend(_validate_control_calibration(profile.control_calibration, policy))
    if "guide_power" in checks:
        reasons.extend(_validate_guide_power(profile.target_qc, policy))
    return reasons


def _validate_replicate_scope(replicate_scope: dict[str, Any], policy: GatePolicy, trusted_execution: bool) -> list[str]:
    if not policy.require_replicate_scope_for_measured_claims:
        return []
    handling = _norm(_first(replicate_scope, "replicate_handling", "handling") or "")
    if handling == "method_internal":
        if policy.allow_method_internal_replicate_handling and trusted_execution:
            return []
        return ["method-internal replicate handling requires trusted runner provenance"]
    axis = _first(replicate_scope, "replicate_axis", "replicate_unit", "axis", "unit")
    n_replicates = _optional_int(_first(replicate_scope, "n_replicates", "replicate_count", "n_replicate_units"))
    if axis and (n_replicates is None or n_replicates >= 2):
        return []
    return ["strict policy requires an explicit independent replicate axis or trusted method-internal replicate handling"]


def _validate_confounding(replicate_scope: dict[str, Any], policy: GatePolicy) -> list[str]:
    if not policy.batch_confounding_fail_blocks_measured:
        return []
    values = [
        _first(replicate_scope, "confound_flag", "batch_perturbation_confounding"),
        _first(replicate_scope, "confound_status", "batch_confounding_status"),
    ]
    for value in values:
        if _falsey(value):
            continue
        status = _norm(value)
        if _truthy(value) or status in {"possible_batch_perturbation_confounding", "batch_perturbation_confounding", "nested", "perfectly_nested", "failed", "fail"}:
            return ["registered eligibility reports batch-perturbation confounding for this claim scope"]
    return []


def _validate_control_calibration(control_calibration: dict[str, Any], policy: GatePolicy) -> list[str]:
    reasons: list[str] = []
    structured = any(value not in (None, "", {}, []) for value in control_calibration.values())
    if policy.require_control_calibration_for_measured_claims and not structured:
        reasons.append("policy requires structured control calibration metadata for measured claims")
    negative_status = _norm(_first(control_calibration, "negative_control_status", "control_status") or "")
    if policy.control_calibration_fail_blocks_measured and negative_status in {"failed", "fail", "invalid", "unavailable", "missing"}:
        reasons.append("registered control calibration reports failed negative-control status")
    reasons.extend(_validate_named_calibration_check(control_calibration, policy, "ntc_vs_ntc_check", policy.require_ntc_vs_ntc_check_for_measured_claims))
    reasons.extend(_validate_named_calibration_check(control_calibration, policy, "label_permutation_check", policy.require_label_permutation_check_for_measured_claims))
    return reasons


def _validate_named_calibration_check(control_calibration: dict[str, Any], policy: GatePolicy, key: str, required: bool) -> list[str]:
    value = control_calibration.get(key)
    if not isinstance(value, dict):
        if required:
            return [f"policy requires {key} for measured claims"]
        return []
    status = _norm(_first(value, "status", "result", "passed") or "")
    if required and policy.require_trusted_calibration_for_required_checks and not _truthy(value.get("_source_trusted")):
        return [f"policy requires trusted control calibration provenance for {key}"]
    if _falsey(_first(value, "passed")) or status in {"failed", "fail", "invalid", "not_calibrated"}:
        return [f"registered control calibration reports failed {key}"]
    return []


def _validate_guide_power(target_qc: dict[str, Any], policy: GatePolicy) -> list[str]:
    reasons: list[str] = []
    if policy.minimum_guides_per_target is not None:
        guides = _optional_int(_first(target_qc, "guides_per_target", "n_guides", "guide_count"))
        if guides is None:
            reasons.append(f"guide count is missing under policy minimum {policy.minimum_guides_per_target}")
        elif guides < policy.minimum_guides_per_target:
            reasons.append(f"guide count {guides} is below policy minimum {policy.minimum_guides_per_target}")
    if policy.minimum_cells_per_guide is not None:
        cells_per_guide = target_qc.get("cells_per_guide")
        if isinstance(cells_per_guide, dict) and cells_per_guide:
            low = [str(guide) for guide, count in cells_per_guide.items() if (_optional_int(count) or 0) < policy.minimum_cells_per_guide]
            if low:
                reasons.append(f"cells per guide below policy minimum {policy.minimum_cells_per_guide}: {', '.join(low)}")
        else:
            reasons.append(f"cells-per-guide metadata is missing under policy minimum {policy.minimum_cells_per_guide}")
    if policy.guide_consistency_fail_blocks_measured:
        consistency = _norm(_first(target_qc, "guide_consistency", "guide_consistency_status") or "")
        if consistency in {"failed", "fail", "discordant", "inconsistent", "weak"}:
            reasons.append("registered target QC reports failed guide consistency")
    return reasons


def _merged_cell_qc(profile: EligibilityProfile) -> dict[str, Any]:
    cell_qc = dict(profile.cell_qc)
    legacy = profile.target_qc
    for key in [
        "cell_qc_passed", "qc_passed", "passed", "n_cells_after_qc", "post_qc_cells", "cells_after_qc",
        "qc_policy", "doublet_policy", "ambient_policy", "batch_qc",
    ]:
        if key in legacy and key not in cell_qc:
            cell_qc[key] = legacy[key]
    return cell_qc


def _validate_cell_qc(cell_qc: dict[str, Any], policy: GatePolicy) -> list[str]:
    reasons: list[str] = []
    structured = _cell_qc_structured_keys(cell_qc)
    pass_value = _first(cell_qc, "cell_qc_passed", "qc_passed", "passed")

    if policy.require_cell_qc_for_measured_claims and not structured:
        if pass_value is not None:
            reasons.append("boolean-only cell QC flags are ignored without structured cell QC fields")
        else:
            reasons.append("policy requires structured cell QC metadata for measured claims")

    if policy.cell_qc_fail_blocks_measured and _falsey(pass_value):
        reasons.append("cell QC artifact reports failed QC for this claim scope")

    if policy.minimum_qc_cells is not None:
        n_qc = _optional_int(_first(cell_qc, "n_cells_after_qc", "post_qc_cells", "cells_after_qc"))
        if n_qc is None:
            reasons.append(f"cell QC cell count is missing under policy minimum {policy.minimum_qc_cells}")
        elif n_qc < policy.minimum_qc_cells:
            reasons.append(f"post-QC cell count {n_qc} is below policy minimum {policy.minimum_qc_cells}")

    return reasons


def _cell_qc_structured_keys(cell_qc: dict[str, Any]) -> set[str]:
    keys: set[str] = set()
    for key in ["n_cells_after_qc", "post_qc_cells", "cells_after_qc", "qc_policy", "doublet_policy", "ambient_policy", "batch_qc"]:
        value = cell_qc.get(key)
        if value not in (None, "", {}):
            keys.add(key)
    return keys

def _decision(
    claim: Claim,
    strength: StrengthCeiling,
    state: ClaimDecisionState,
    scope_fit: ScopeFit,
    supporting: list[str],
    missing: list[str],
    reasons: list[str],
    policy: GatePolicy,
) -> ClaimDecision:
    return ClaimDecision(
        decision_id=_decision_id(claim.claim_id, supporting, policy.policy_hash),
        claim_id=claim.claim_id,
        decision=state,
        max_strength=strength,
        scope_fit=scope_fit,
        supporting_artifacts=supporting,
        missing_artifacts=missing,
        blocked_requested_strength=_blocked_strength(claim, strength),
        allowed_surface=surface_for_claim(claim, strength, [], []),
        reasons=reasons,
        policy_version=policy.version,
        policy_hash=policy.policy_hash,
        resolver_version=policy.resolver_version,
    )


def _decision_state(claim: Claim, max_strength: StrengthCeiling) -> ClaimDecisionState:
    if max_strength == StrengthCeiling.unsupported:
        return ClaimDecisionState.unsupported
    requested = _requested_strength(claim)
    if requested is not None and requested != max_strength:
        return ClaimDecisionState.allowed_with_downgrade
    return ClaimDecisionState.allowed


def _blocked_strength(claim: Claim, max_strength: StrengthCeiling) -> StrengthCeiling | str | None:
    requested = _requested_strength(claim)
    if requested is None or requested == max_strength:
        return None
    return requested


def _requested_strength(claim: Claim) -> StrengthCeiling | str | None:
    if isinstance(claim.requested_strength, StrengthCeiling):
        return claim.requested_strength
    if not claim.requested_strength:
        return None
    try:
        return StrengthCeiling(str(claim.requested_strength))
    except ValueError:
        return str(claim.requested_strength)


def _unique_classes(artifacts: list[EvidenceArtifact]) -> list[EvidenceClass]:
    seen: set[EvidenceClass] = set()
    result: list[EvidenceClass] = []
    for artifact in artifacts:
        evidence_class = artifact.effective_evidence_class
        if evidence_class not in seen:
            result.append(evidence_class)
            seen.add(evidence_class)
    return result


def _merge_scope_fits(scope_fits: list[ScopeFit]) -> ScopeFit:
    if not scope_fits:
        return ScopeFit.unknown
    if ScopeFit.exact in scope_fits:
        return ScopeFit.exact
    if ScopeFit.compatible in scope_fits:
        return ScopeFit.compatible
    if ScopeFit.weaker in scope_fits:
        return ScopeFit.weaker
    if ScopeFit.unknown in scope_fits:
        return ScopeFit.unknown
    return ScopeFit.mismatch



def _decision_id(claim_id: str, artifact_ids: list[str], policy_hash: str) -> str:
    text = "|".join([claim_id, *artifact_ids, policy_hash])
    return "decision_" + uuid5(NAMESPACE_URL, text).hex[:12]



def _claim_requests_gene_specific_effect(claim: Claim) -> bool:
    relation = _norm(claim.relation or "")
    object_type = _norm(claim.object.get("type") or claim.object.get("object_type") or "")
    text = _norm(claim.text)
    if object_type in {"gene", "target_gene"}:
        return True
    if relation in {"changes_expression", "differential_expression", "gene_expression", "de_gene"}:
        return True
    return "gene_specific" in text or "differential_expression" in text or "differential_expression" in relation

def _claim_requests_target_engagement(claim: Claim) -> bool:
    relation = _norm(claim.relation or "")
    object_type = _norm(claim.object.get("type") or claim.object.get("object_type") or "")
    text = _norm(claim.text)
    if relation in {"target_engagement", "perturbation_efficiency", "target_expression", "target_knockdown"}:
        return True
    if object_type in {"target_engagement", "perturbation_efficiency"}:
        return True
    return "target_engagement" in text or "perturbation_efficiency" in text


def _subject_text(claim: Claim, artifacts: list[EvidenceArtifact]) -> str:
    for source in [claim.subject, claim.scope]:
        for key in ["id", "perturbation", "subject", "gene"]:
            if source.get(key):
                return str(source[key])
    for artifact in artifacts:
        if artifact.scope.get("perturbation"):
            return str(artifact.scope["perturbation"])
        if artifact.contrast_left:
            return str(artifact.contrast_left)
    return "the queried perturbation"


def _target_text(claim: Claim, artifacts: list[EvidenceArtifact]) -> str:
    for source in [claim.object, claim.scope]:
        for key in ["id", "target", "gene", "term_id", "pathway"]:
            if source.get(key):
                return f" involving {source[key]}"
    for artifact in artifacts:
        for source in [artifact.predicate, artifact.scope]:
            for key in ["target", "gene", "term_id", "pathway"]:
                if source.get(key):
                    return f" involving {source[key]}"
    return ""


def _contrast_text(artifact: EvidenceArtifact) -> str:
    if artifact.contrast_left and artifact.contrast_baseline:
        return f"`{artifact.contrast_left}` versus `{artifact.contrast_baseline}`"
    if artifact.scope.get("contrast"):
        return f"`{artifact.scope['contrast']}`"
    return "registered"


def _deep_merge(target: dict[str, Any], source: dict[str, Any]) -> None:
    for key, value in source.items():
        if (
            key in {"ntc_vs_ntc_check", "label_permutation_check"}
            and isinstance(value, dict)
            and isinstance(target.get(key), dict)
            and _truthy(target[key].get("_source_trusted"))
            and not _truthy(value.get("_source_trusted"))
        ):
            continue
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _deep_merge(target[key], value)
        elif value not in (None, "", {}, []):
            target[key] = value


def _first(data: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = data.get(key)
        if value not in (None, "", [], {}):
            return value
    return None


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _norm(value: Any) -> str:
    return str(value).strip().lower().replace("-", "_").replace(" ", "_")



def _direction(value: Any) -> str | None:
    if value in (None, ""):
        return None
    text = _norm(value)
    if text in {"down", "decrease", "decreased", "lower", "downregulated", "down_regulated", "negative"}:
        return "down"
    if text in {"up", "increase", "increased", "higher", "upregulated", "up_regulated", "positive"}:
        return "up"
    if text in {"unchanged", "no_change", "none", "no_detected_difference"}:
        return "unchanged"
    return text


def _expected_direction_for_modality(modality: str) -> str | None:
    if modality in {"crispri", "crispr_i", "knockdown"}:
        return "down"
    if modality in {"crispra", "crispr_a", "activation"}:
        return "up"
    return None
def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "passed", "pass"}


def _falsey(value: Any) -> bool:
    if isinstance(value, bool):
        return not value
    if value is None:
        return False
    return str(value).strip().lower() in {"0", "false", "no", "failed", "fail"}


def _structured_keys(profile: EligibilityProfile) -> set[str]:
    keys: set[str] = set()
    for section in [profile.perturbation_cell_mapping, profile.control_definition, profile.target_qc, profile.cell_qc, profile.control_calibration]:
        keys.update(section)
    for key in ["assay_modality", "perturbation_modality", "moi", "estimand"]:
        if getattr(profile, key):
            keys.add(key)
    return keys


def _is_high_moi(profile: EligibilityProfile, policy: GatePolicy) -> bool:
    moi = profile.moi
    if moi is None:
        moi = profile.perturbation_cell_mapping.get("moi_inference")
    if moi is None:
        return False
    if isinstance(moi, (int, float)):
        return float(moi) > 1.0
    return _norm(moi) in {_norm(item) for item in policy.high_moi_values}


def _dedupe(items: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        if item not in seen:
            result.append(item)
            seen.add(item)
    return result
















