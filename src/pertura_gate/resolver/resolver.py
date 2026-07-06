from __future__ import annotations

from dataclasses import dataclass
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
from pertura_gate.resolver.warrant import best_resolution as choose_best_resolution



_ELIGIBILITY_KINDS = {
    ArtifactKind.experiment_design,
    ArtifactKind.guide_assignment,
    ArtifactKind.target_qc,
    ArtifactKind.cell_qc,
}


@dataclass(frozen=True)
class EligibilityValidation:
    profile: EligibilityProfile
    passed: bool
    reasons: list[str]
    sources: list[str]


def resolve_artifact_strength(artifact: EvidenceArtifact | None, *, policy: GatePolicy = DEFAULT_POLICY) -> ResolvedStrength:
    """Resolve the artifact-intrinsic ceiling supported by execution facts.

    This is intentionally artifact-local. Claim-conditioned resolution happens in
    resolve_claim(), where a measured artifact must also satisfy an EligibilityProfile.
    """

    if artifact is None:
        return ResolvedStrength(
            artifact_id="missing",
            tier=EvidenceTier.unsupported,
            ceiling=StrengthCeiling.unsupported,
            reasons=["no registered evidence artifact was found"],
        )
    if artifact.kind == ArtifactKind.measured_de:
        return _resolve_measured_de_intrinsic(artifact, policy=policy)
    if artifact.kind == ArtifactKind.perturbation_efficiency:
        return _resolve_perturbation_efficiency(artifact, policy=policy)
    if artifact.kind == ArtifactKind.module_effect:
        return _resolve_module_effect(artifact, policy=policy)
    if artifact.kind == ArtifactKind.global_effect:
        return _resolve_global_effect(artifact, policy=policy)
    if artifact.kind == ArtifactKind.predicted_effect:
        return ResolvedStrength(
            artifact_id=artifact.artifact_id,
            tier=EvidenceTier.predicted,
            ceiling=StrengthCeiling.predicted_effect,
            reasons=["registered prediction artifact supports prediction evidence only"],
        )
    if artifact.kind == ArtifactKind.curated_enrichment_result:
        return ResolvedStrength(
            artifact_id=artifact.artifact_id,
            tier=EvidenceTier.curated_prior,
            ceiling=StrengthCeiling.curated_prior_support,
            reasons=["registered curated enrichment artifact provides curated context only unless bound to measured evidence"],
        )
    if artifact.kind == ArtifactKind.curated_prior_lookup:
        return ResolvedStrength(
            artifact_id=artifact.artifact_id,
            tier=EvidenceTier.curated_prior,
            ceiling=StrengthCeiling.curated_prior_support,
            reasons=["registered curated prior artifact supports prior context only"],
        )
    if artifact.kind == ArtifactKind.replication_summary:
        return _resolve_replication(artifact, policy=policy)
    return ResolvedStrength(
        artifact_id=artifact.artifact_id,
        tier=_tier_for_evidence_class(artifact.effective_evidence_class),
        ceiling=StrengthCeiling.observation,
        reasons=[f"artifact kind {artifact.kind.value} defines scope or eligibility only in v1"],
    )


def resolve_claim(claim: Claim | dict, registry, policy: GatePolicy = DEFAULT_POLICY) -> ClaimDecision:
    """Resolve a claim-specific ceiling from runtime-registered artifacts."""

    claim_obj = Claim.from_dict(claim) if isinstance(claim, dict) else claim
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
        allowed_surface=_render_allowed_surface(claim_obj, best_resolution.ceiling, candidate_artifacts, evidence_classes),
        reasons=_dedupe(decision_reasons),
        policy_version=policy.version,
        policy_hash=policy.policy_hash,
        resolver_version=policy.resolver_version,
    )


def resolve_claims(claims: list[Claim | dict], registry, policy: GatePolicy = DEFAULT_POLICY) -> list[ClaimDecision]:
    return [resolve_claim(claim, registry, policy=policy) for claim in claims]


def _resolve_claim_artifact(claim: Claim, artifact: EvidenceArtifact, registry, *, policy: GatePolicy) -> ResolvedStrength:
    if artifact.kind == ArtifactKind.measured_de:
        intrinsic = _resolve_measured_de_intrinsic(artifact, policy=policy)
        if intrinsic.ceiling != StrengthCeiling.measured_association:
            return intrinsic
        manifest_scope_fit = compare_manifest_scope(claim.scope, artifact.scope)
        if not manifest_scope_is_strong(manifest_scope_fit):
            return ResolvedStrength(
                artifact_id=artifact.artifact_id,
                tier=EvidenceTier.measured,
                ceiling=StrengthCeiling.observation,
                reasons=[
                    "measured association requires claim and artifact scope to resolve through a PerturbationDesignManifest UID",
                ],
            )
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
            ceiling=StrengthCeiling.measured_association,
            reasons=[
                "measured DE artifact has resolved contrast, sample counts, method, and multiple-testing metadata",
                "EligibilityProfile satisfied: " + ", ".join(eligibility.sources),
            ],
        )
    if artifact.kind == ArtifactKind.perturbation_efficiency:
        intrinsic = _resolve_perturbation_efficiency(artifact, policy=policy)
        if intrinsic.ceiling != StrengthCeiling.measured_target_engagement:
            return intrinsic
        manifest_scope_fit = compare_manifest_scope(claim.scope, artifact.scope)
        if not manifest_scope_is_strong(manifest_scope_fit):
            return ResolvedStrength(
                artifact_id=artifact.artifact_id,
                tier=EvidenceTier.measured,
                ceiling=StrengthCeiling.observation,
                reasons=[
                    "target engagement requires claim and artifact scope to resolve through a PerturbationDesignManifest UID",
                ],
            )
        cell_qc_reasons = _cell_qc_reasons_for_claim_artifact(claim, artifact, registry, policy=policy)
        if cell_qc_reasons:
            return ResolvedStrength(
                artifact_id=artifact.artifact_id,
                tier=EvidenceTier.measured,
                ceiling=StrengthCeiling.observation,
                reasons=cell_qc_reasons,
            )
        return intrinsic
    if artifact.kind == ArtifactKind.curated_enrichment_result:
        return _resolve_curated_enrichment(claim, artifact, registry, policy=policy)
    if artifact.kind in {ArtifactKind.module_effect, ArtifactKind.global_effect}:
        intrinsic = resolve_artifact_strength(artifact, policy=policy)
        if intrinsic.ceiling != StrengthCeiling.measured_association:
            return intrinsic
        manifest_scope_fit = compare_manifest_scope(claim.scope, artifact.scope)
        if not manifest_scope_is_strong(manifest_scope_fit):
            return ResolvedStrength(
                artifact_id=artifact.artifact_id,
                tier=EvidenceTier.measured,
                ceiling=StrengthCeiling.observation,
                reasons=[
                    f"{artifact.kind.value} requires claim and artifact scope to resolve through a PerturbationDesignManifest UID",
                ],
            )
        if artifact.kind == ArtifactKind.global_effect and _claim_requests_gene_specific_effect(claim):
            return ResolvedStrength(
                artifact_id=artifact.artifact_id,
                tier=EvidenceTier.measured,
                ceiling=StrengthCeiling.observation,
                reasons=["global-effect artifact does not support gene-specific differential-expression claims"],
            )
        if policy.require_measured_eligibility_for_claims:
            eligibility = validate_measured_association_eligibility(claim, artifact, registry, policy=policy)
            if not eligibility.passed:
                return ResolvedStrength(
                    artifact_id=artifact.artifact_id,
                    tier=EvidenceTier.measured,
                    ceiling=StrengthCeiling.observation,
                    reasons=[
                        f"{artifact.kind.value} lacks a validated EligibilityProfile for this claim",
                        *eligibility.reasons,
                    ],
                )
            return ResolvedStrength(
                artifact_id=artifact.artifact_id,
                tier=EvidenceTier.measured,
                ceiling=StrengthCeiling.measured_association,
                reasons=[*intrinsic.reasons, "EligibilityProfile satisfied: " + ", ".join(eligibility.sources)],
            )
        return intrinsic
    return resolve_artifact_strength(artifact, policy=policy)


def validate_measured_association_eligibility(
    claim: Claim,
    artifact: EvidenceArtifact,
    registry,
    *,
    policy: GatePolicy = DEFAULT_POLICY,
) -> EligibilityValidation:
    profile = _build_eligibility_profile(claim, artifact, registry)
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

    if _truthy(_first(target_qc, "eligibility_passed", "guide_assignment_passed", "target_qc_passed")) and len(_structured_keys(profile)) < 3:
        reasons.append("boolean/prose eligibility flags are ignored without structured, hashable fields")

    return EligibilityValidation(profile=profile, passed=not reasons, reasons=reasons, sources=profile.sources or [artifact.artifact_id])


def _build_eligibility_profile(claim: Claim, measured: EvidenceArtifact, registry) -> EligibilityProfile:
    merged: dict[str, Any] = {}
    sources: list[str] = []

    def merge_from(source: dict[str, Any] | None, source_id: str) -> None:
        if not source:
            return
        _deep_merge(merged, _normalize_eligibility(source))
        if source_id not in sources:
            sources.append(source_id)

    merge_from(measured.eligibility, measured.artifact_id)
    if measured.quality.get("eligibility"):
        merge_from(dict(measured.quality.get("eligibility") or {}), measured.artifact_id)

    explicit_ids = _explicit_eligibility_artifact_ids(measured)
    for artifact_id in explicit_ids:
        artifact = registry.get(artifact_id)
        if artifact is None or artifact.kind not in _ELIGIBILITY_KINDS:
            continue
        if not _eligibility_scope_can_support(claim.scope or measured.scope, measured.scope, artifact.scope):
            continue
        merge_from(artifact.eligibility, artifact.artifact_id)

    for artifact in registry.list():
        if artifact.artifact_id == measured.artifact_id or artifact.kind not in _ELIGIBILITY_KINDS:
            continue
        if artifact.artifact_id in explicit_ids:
            continue
        if not _eligibility_scope_can_support(claim.scope or measured.scope, measured.scope, artifact.scope):
            continue
        merge_from(artifact.eligibility, artifact.artifact_id)

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
    if fit in {ScopeFit.exact, ScopeFit.compatible, ScopeFit.weaker, ScopeFit.unknown}:
        return True

    legacy_fit = compare_scope(claim_scope or measured_scope, artifact_scope)
    return legacy_fit != ScopeFit.mismatch

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

def _resolve_measured_de_intrinsic(artifact: EvidenceArtifact, *, policy: GatePolicy) -> ResolvedStrength:
    missing: list[str] = []
    insufficient: list[str] = []
    if not artifact.contrast_left:
        missing.append("contrast.left")
    if not artifact.contrast_baseline:
        missing.append("contrast.baseline")
    if artifact.n_left is None:
        missing.append("n_left")
    elif artifact.n_left < policy.minimum_measured_n:
        insufficient.append(f"n_left {artifact.n_left} is below policy minimum {policy.minimum_measured_n}")
    if artifact.n_baseline is None:
        missing.append("n_baseline")
    elif artifact.n_baseline < policy.minimum_measured_n:
        insufficient.append(f"n_baseline {artifact.n_baseline} is below policy minimum {policy.minimum_measured_n}")
    if not artifact.method:
        missing.append("method")
    if not artifact.multiple_testing:
        missing.append("multiple_testing")
    if not artifact.has_padj:
        missing.append("adjusted p-values")

    if missing or insufficient:
        reasons: list[str] = []
        if missing:
            reasons.append(f"missing execution metadata: {', '.join(missing)}")
        reasons.extend(insufficient)
        return ResolvedStrength(
            artifact_id=artifact.artifact_id,
            tier=EvidenceTier.measured,
            ceiling=StrengthCeiling.observation,
            reasons=reasons,
        )
    return ResolvedStrength(
        artifact_id=artifact.artifact_id,
        tier=EvidenceTier.measured,
        ceiling=StrengthCeiling.measured_association,
        reasons=["measured DE artifact has resolved contrast, sample counts, method, and multiple-testing metadata"],
    )



def _resolve_perturbation_efficiency(artifact: EvidenceArtifact, *, policy: GatePolicy) -> ResolvedStrength:
    quality = artifact.quality
    modality = _norm(quality.get("modality") or artifact.scope.get("modality") or "")
    observed = _direction(quality.get("observed_direction") or artifact.predicate.get("direction"))
    expected = _direction(quality.get("expected_direction")) or _expected_direction_for_modality(modality)
    method = quality.get("method")
    target = artifact.predicate.get("target") or artifact.scope.get("target")
    n_target = _optional_int(_first(quality, "n_target_cells", "n_treated", "n_left"))
    n_control = _optional_int(_first(quality, "n_control_cells", "n_control", "n_baseline"))
    has_effect = _first(quality, "effect_size", "pvalue", "padj") is not None
    reasons: list[str] = []

    if not target:
        reasons.append("target-engagement artifact lacks a target gene")
    if not method:
        reasons.append("target-engagement artifact lacks a measurement method")
    if not observed:
        reasons.append("target-engagement artifact lacks observed direction")
    if n_target is not None and n_target < policy.minimum_measured_n:
        reasons.append(f"target cell count is below policy minimum {policy.minimum_measured_n}")
    if n_control is not None and n_control < policy.minimum_measured_n:
        reasons.append(f"control cell count is below policy minimum {policy.minimum_measured_n}")
    if not has_effect:
        reasons.append("target-engagement artifact lacks effect or statistical metadata")

    if modality in {"crispr_ko", "ko", "knockout"} and observed == "unchanged":
        reasons.append("CRISPR-KO target mRNA unchanged is not automatic perturbation-efficiency failure")
    elif expected and observed and expected != observed:
        reasons.append(f"observed target direction {observed!r} conflicts with expected {expected!r}")

    if reasons:
        return ResolvedStrength(
            artifact_id=artifact.artifact_id,
            tier=EvidenceTier.measured,
            ceiling=StrengthCeiling.observation,
            reasons=reasons,
        )
    return ResolvedStrength(
        artifact_id=artifact.artifact_id,
        tier=EvidenceTier.measured,
        ceiling=StrengthCeiling.measured_target_engagement,
        reasons=["registered perturbation-efficiency artifact supports measured target engagement only"],
    )


def _resolve_curated_enrichment(
    claim: Claim,
    artifact: EvidenceArtifact,
    registry,
    *,
    policy: GatePolicy,
) -> ResolvedStrength:
    input_id = artifact.quality.get("input_measured_artifact_id")
    required = ["input_gene_set_hash", "background_universe", "database", "database_version", "term_id", "method", "padj"]
    missing = [field for field in required if not artifact.quality.get(field) and not artifact.predicate.get(field)]
    if not input_id:
        return ResolvedStrength(
            artifact_id=artifact.artifact_id,
            tier=EvidenceTier.curated_prior,
            ceiling=StrengthCeiling.curated_prior_support,
            reasons=["curated enrichment is not bound to a registered measured artifact"],
        )
    measured = registry.get(str(input_id))
    if measured is None:
        return ResolvedStrength(
            artifact_id=artifact.artifact_id,
            tier=EvidenceTier.curated_prior,
            ceiling=StrengthCeiling.curated_prior_support,
            reasons=[f"bound measured artifact {input_id!r} could not be resolved"],
        )
    measured_resolution = _resolve_claim_artifact(claim, measured, registry, policy=policy)
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

def _resolve_module_effect(artifact: EvidenceArtifact, *, policy: GatePolicy) -> ResolvedStrength:
    quality = artifact.quality
    predicate = artifact.predicate
    missing: list[str] = []
    reasons: list[str] = []

    if not _first(predicate, "module_id", "module_name") and not _first(quality, "module_id", "module_name"):
        missing.append("module_id/module_name")
    for field in ["module_source", "module_gene_set_hash", "scoring_method", "method"]:
        if not quality.get(field):
            missing.append(field)
    if _first(quality, "effect_size") is None:
        missing.append("effect_size")
    if _first(quality, "pvalue", "padj") is None:
        missing.append("pvalue/padj")

    n_target = _optional_int(_first(quality, "n_target_cells", "n_treated", "n_left"))
    n_control = _optional_int(_first(quality, "n_control_cells", "n_control", "n_baseline"))
    if n_target is None:
        missing.append("n_target_cells")
    elif n_target < policy.minimum_measured_n:
        reasons.append(f"target cell count is below policy minimum {policy.minimum_measured_n}")
    if n_control is None:
        missing.append("n_control_cells")
    elif n_control < policy.minimum_measured_n:
        reasons.append(f"control cell count is below policy minimum {policy.minimum_measured_n}")

    if missing:
        reasons.append("module-effect artifact lacks required execution metadata: " + ", ".join(_dedupe(missing)))
    if reasons:
        return ResolvedStrength(
            artifact_id=artifact.artifact_id,
            tier=EvidenceTier.measured,
            ceiling=StrengthCeiling.observation,
            reasons=reasons,
        )

    source = _norm(quality.get("module_source"))
    support_reason = "registered module-effect artifact supports measured module-score association only"
    if source == "all_cell_derived":
        support_reason += "; all-cell-derived module has perturbation-contamination caveat"
    elif source == "prediction_derived":
        support_reason += "; prediction-derived module source does not validate the prediction or mechanism"
    return ResolvedStrength(
        artifact_id=artifact.artifact_id,
        tier=EvidenceTier.measured,
        ceiling=StrengthCeiling.measured_association,
        reasons=[support_reason],
    )


def _resolve_global_effect(artifact: EvidenceArtifact, *, policy: GatePolicy) -> ResolvedStrength:
    quality = artifact.quality
    missing: list[str] = []
    reasons: list[str] = []

    if not _first(quality, "metric") and not _first(artifact.predicate, "metric"):
        missing.append("metric")
    if not _first(quality, "feature_space", "embedding"):
        missing.append("feature_space/embedding")
    if not quality.get("comparison_method"):
        missing.append("comparison_method")
    if _first(quality, "effect_size", "distance") is None:
        missing.append("effect_size/distance")
    if not _first(quality, "null_model", "permutation_or_test"):
        missing.append("null_model/permutation_or_test")
    if _first(quality, "pvalue", "padj") is None:
        missing.append("pvalue/padj")

    n_target = _optional_int(_first(quality, "n_target_cells", "n_treated", "n_left"))
    n_control = _optional_int(_first(quality, "n_control_cells", "n_control", "n_baseline"))
    if n_target is None:
        missing.append("n_target_cells")
    elif n_target < policy.minimum_measured_n:
        reasons.append(f"target cell count is below policy minimum {policy.minimum_measured_n}")
    if n_control is None:
        missing.append("n_control_cells")
    elif n_control < policy.minimum_measured_n:
        reasons.append(f"control cell count is below policy minimum {policy.minimum_measured_n}")

    if missing:
        reasons.append("global-effect artifact lacks required execution metadata: " + ", ".join(_dedupe(missing)))
    if reasons:
        return ResolvedStrength(
            artifact_id=artifact.artifact_id,
            tier=EvidenceTier.measured,
            ceiling=StrengthCeiling.observation,
            reasons=reasons,
        )
    return ResolvedStrength(
        artifact_id=artifact.artifact_id,
        tier=EvidenceTier.measured,
        ceiling=StrengthCeiling.measured_association,
        reasons=["registered global-effect artifact supports measured global perturbation response only"],
    )

def _resolve_replication(artifact: EvidenceArtifact, *, policy: GatePolicy) -> ResolvedStrength:
    replication_type = artifact.quality.get("replication_type")
    resolved_ids = artifact.quality.get("resolved_artifact_ids") or []
    missing_ids = artifact.quality.get("missing_artifact_ids") or []
    allowed_types = set(policy.allowed_replication_types)
    if policy.upgrade_guide_consistency_to_replication:
        allowed_types.add("guide_level_replication")
        allowed_types.add("guide_consistency")
    if replication_type not in allowed_types:
        return ResolvedStrength(
            artifact_id=artifact.artifact_id,
            tier=EvidenceTier.composite,
            ceiling=StrengthCeiling.observation,
            reasons=[f"replication_type {replication_type!r} is not an allowed independent replication axis"],
        )
    if len(resolved_ids) < policy.replication_min_artifacts or missing_ids:
        return ResolvedStrength(
            artifact_id=artifact.artifact_id,
            tier=EvidenceTier.composite,
            ceiling=StrengthCeiling.observation,
            reasons=["replication summary does not resolve enough measured artifacts"],
        )
    return ResolvedStrength(
        artifact_id=artifact.artifact_id,
        tier=EvidenceTier.composite,
        ceiling=StrengthCeiling.replicated_measured_association,
        reasons=["registered independent measured artifacts support replicated measured association"],
    )


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
        allowed_surface=_render_allowed_surface(claim, strength, [], []),
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


def _render_allowed_surface(
    claim: Claim,
    strength: StrengthCeiling,
    artifacts: list[EvidenceArtifact],
    evidence_classes: list[EvidenceClass],
) -> str:
    subject = _subject_text(claim, artifacts)
    target = _target_text(claim, artifacts)
    curated_enrichment = next((artifact for artifact in artifacts if artifact.kind == ArtifactKind.curated_enrichment_result), None)
    module_effect = next((artifact for artifact in artifacts if artifact.kind == ArtifactKind.module_effect), None)
    global_effect = next((artifact for artifact in artifacts if artifact.kind == ArtifactKind.global_effect), None)
    measured = next((artifact for artifact in artifacts if artifact.effective_evidence_class == EvidenceClass.measured), None)

    if strength == StrengthCeiling.measured_association and curated_enrichment is not None:
        return (
            f"A registered curated enrichment result is bound to runtime-validated measured evidence for {subject}{target}. "
            "This provides curated context for a measured association only; it is not a validation or mechanism claim."
        )
    if strength == StrengthCeiling.measured_association and module_effect is not None:
        source = _norm(module_effect.quality.get("module_source"))
        caveat = ""
        if source == "all_cell_derived":
            caveat = " The module was derived from all cells, so perturbation-contamination risk should be considered."
        elif source == "prediction_derived":
            caveat = " The module source is prediction-derived, so the measured score does not validate that prediction."
        return (
            f"Registered module-score evidence supports a measured module association for {subject}{target}. "
            "This does not establish a downstream mechanism or regulatory validation." + caveat
        )
    if strength == StrengthCeiling.measured_association and global_effect is not None:
        return (
            f"Registered global-response evidence supports a measured global perturbation response for {subject}. "
            "This does not establish a gene-specific effect, downstream mechanism, or causal cell-state transition."
        )
    if strength == StrengthCeiling.measured_association and measured is not None:
        contrast = _contrast_text(measured)
        return (
            f"In the registered {contrast} Perturb-seq contrast, {subject} is associated with measured "
            f"expression differences{target}. This supports a measured association only; no registered "
            "replication, orthogonal validation, or rescue-assay artifact supports a validated mechanism."
        )
    if strength == StrengthCeiling.replicated_measured_association:
        return (
            f"Registered independent measured artifacts support a replicated measured association for {subject}{target}. "
            "This still does not by itself establish a validated mechanism."
        )
    if strength == StrengthCeiling.measured_target_engagement:
        return (
            f"Registered target-engagement evidence supports measured target engagement for {subject}{target}. "
            "This does not establish a downstream mechanism."
        )
    if strength == StrengthCeiling.predicted_effect:
        return (
            f"A registered prediction artifact predicts an effect for {subject}{target}. This is prediction evidence, "
            "and must not be reported as an experimental result."
        )
    if strength == StrengthCeiling.curated_prior_support:
        return (
            f"A curated prior artifact provides prior support related to {subject}{target}. This is curated prior "
            "context, not an experimental confirmation."
        )
    if strength == StrengthCeiling.observation:
        return (
            "A registered artifact exists, but runtime-validated metadata and eligibility support only a file-level "
            "observation and not an effect-level scientific conclusion for this claim."
        )
    return "No registered artifact with compatible scope and runtime-validated metadata supports this claim."

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


def _tier_for_evidence_class(evidence_class: EvidenceClass) -> EvidenceTier:
    if evidence_class == EvidenceClass.measured:
        return EvidenceTier.measured
    if evidence_class == EvidenceClass.predicted:
        return EvidenceTier.predicted
    if evidence_class == EvidenceClass.curated_prior:
        return EvidenceTier.curated_prior
    if evidence_class in {EvidenceClass.composite_summary, EvidenceClass.composite}:
        return EvidenceTier.composite
    return EvidenceTier.observation


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













