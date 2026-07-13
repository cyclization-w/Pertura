from __future__ import annotations

from typing import Any

from pertura_gate.core.policy import DEFAULT_POLICY, GatePolicy
from pertura_gate.core.schema import (
    Claim,
    EvidenceArtifact,
    EvidenceClass,
    EvidencePredicate,
    EvidenceTier,
    ResolvedStrength,
    StrengthCeiling,
)

STRENGTH_RANK: dict[StrengthCeiling, int] = {
    StrengthCeiling.unsupported: 0,
    StrengthCeiling.observation: 1,
    StrengthCeiling.curated_prior_support: 2,
    StrengthCeiling.predicted_effect: 2,
    StrengthCeiling.measured_target_engagement: 3,
    StrengthCeiling.measured_association: 4,
    StrengthCeiling.replicated_measured_association: 5,
    StrengthCeiling.validated_mechanism_disabled: 6,
}


def strength_rank(strength: StrengthCeiling) -> int:
    return STRENGTH_RANK[strength]


def best_resolution(candidates: list[ResolvedStrength]) -> ResolvedStrength:
    return max(candidates, key=lambda item: strength_rank(item.ceiling))


def predicate_for_artifact(artifact: EvidenceArtifact) -> EvidencePredicate:
    return artifact.effective_evidence_predicate


def intrinsic_warrant(artifact: EvidenceArtifact | None, *, policy: GatePolicy = DEFAULT_POLICY) -> ResolvedStrength:
    if artifact is None:
        return ResolvedStrength(
            artifact_id="missing",
            tier=EvidenceTier.unsupported,
            ceiling=StrengthCeiling.unsupported,
            reasons=["no registered evidence artifact was found"],
        )
    predicate = predicate_for_artifact(artifact)
    if predicate == EvidencePredicate.differential_expression:
        return differential_expression_intrinsic(artifact, policy=policy)
    if predicate == EvidencePredicate.target_engagement:
        return target_engagement_intrinsic(artifact, policy=policy)
    if predicate == EvidencePredicate.module_score_shift:
        return module_score_intrinsic(artifact, policy=policy)
    if predicate == EvidencePredicate.global_transcriptomic_shift:
        return global_shift_intrinsic(artifact, policy=policy)
    if predicate == EvidencePredicate.cell_state_composition_shift:
        return composition_shift_intrinsic(artifact, policy=policy)
    if predicate == EvidencePredicate.predicted_perturbation_response:
        return virtual_prediction_intrinsic(artifact, policy=policy)
    if predicate == EvidencePredicate.prediction_measured_concordance:
        return prediction_concordance_intrinsic(artifact, policy=policy)
    if predicate == EvidencePredicate.predicted_cell_state_transition:
        return virtual_cell_state_transition_intrinsic(artifact, policy=policy)
    if predicate == EvidencePredicate.predicted_effect:
        return ResolvedStrength(
            artifact_id=artifact.artifact_id,
            tier=EvidenceTier.predicted,
            ceiling=StrengthCeiling.predicted_effect,
            reasons=["registered prediction artifact supports prediction evidence only"],
        )
    if predicate == EvidencePredicate.curated_enrichment_context:
        return ResolvedStrength(
            artifact_id=artifact.artifact_id,
            tier=EvidenceTier.curated_prior,
            ceiling=StrengthCeiling.curated_prior_support,
            reasons=["registered curated enrichment artifact provides curated context only unless bound to measured evidence"],
        )
    if predicate == EvidencePredicate.curated_prior_context:
        return ResolvedStrength(
            artifact_id=artifact.artifact_id,
            tier=EvidenceTier.curated_prior,
            ceiling=StrengthCeiling.curated_prior_support,
            reasons=["registered curated prior artifact supports prior context only"],
        )
    if predicate == EvidencePredicate.replication_summary:
        return replication_intrinsic(artifact, policy=policy)
    return ResolvedStrength(
        artifact_id=artifact.artifact_id,
        tier=tier_for_evidence_class(artifact.effective_evidence_class),
        ceiling=StrengthCeiling.observation,
        reasons=[f"artifact predicate {predicate.value} defines scope, context, or eligibility only"],
    )


def surface_for_claim(
    claim: Claim,
    strength: StrengthCeiling,
    artifacts: list[EvidenceArtifact],
    evidence_classes: list[EvidenceClass],
) -> str:
    subject = subject_text(claim, artifacts)
    target = target_text(claim, artifacts)
    predicates = {artifact.effective_evidence_predicate for artifact in artifacts}
    measured = next((artifact for artifact in artifacts if artifact.effective_evidence_class == EvidenceClass.measured), None)

    if strength == StrengthCeiling.measured_association and EvidencePredicate.curated_enrichment_context in predicates:
        return (
            f"A registered curated enrichment result is bound to runtime-validated measured evidence for {subject}{target}. "
            "This provides curated context for a measured association only; it is not a validation or mechanism claim."
        )
    if strength == StrengthCeiling.measured_association and EvidencePredicate.module_score_shift in predicates:
        module_effect = next((artifact for artifact in artifacts if artifact.effective_evidence_predicate == EvidencePredicate.module_score_shift), None)
        source = norm(module_effect.quality.get("module_source")) if module_effect is not None else ""
        caveat = ""
        if source == "all_cell_derived":
            caveat = " The module was derived from all cells, so perturbation-contamination risk should be considered."
        elif source == "prediction_derived":
            caveat = " The module source is prediction-derived, so the measured score does not validate that prediction."
        return (
            f"Registered module-score evidence supports a measured module association for {subject}{target}. "
            "This does not establish a downstream mechanism or regulatory validation." + caveat
        )
    if strength == StrengthCeiling.measured_association and EvidencePredicate.global_transcriptomic_shift in predicates:
        return (
            f"Registered global-response evidence supports a measured global perturbation response for {subject}. "
            "This does not establish a gene-specific effect, downstream mechanism, or causal cell-state transition."
        )
    if strength == StrengthCeiling.measured_association and EvidencePredicate.cell_state_composition_shift in predicates:
        return (
            f"Registered composition evidence supports a measured cell-state composition association for {subject}. "
            "This does not establish a gene-specific effect, target engagement, causal fate conversion, downstream mechanism, or driver validation."
        )
    if strength == StrengthCeiling.measured_association and EvidencePredicate.prediction_measured_concordance in predicates:
        return (
            f"A registered measured artifact independently supports a measured association for {subject}{target}. "
            "The prediction-measured concordance artifact is contextual only; concordance is not validation of mechanism and does not create measured evidence."
        )
    if strength == StrengthCeiling.measured_association and measured is not None:
        contrast = contrast_text(measured)
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
    if strength == StrengthCeiling.predicted_effect and EvidencePredicate.prediction_measured_concordance in predicates:
        return (
            f"A registered prediction-measured concordance artifact reports concordance for {subject}{target} under the registered metric. "
            "This is concordance only, not validation of mechanism, and it does not create measured evidence."
        )
    if strength == StrengthCeiling.predicted_effect and EvidencePredicate.predicted_cell_state_transition in predicates:
        return (
            f"A registered virtual cell-state transition model predicts a simulated state shift for {subject}{target}. "
            "This is prediction evidence and does not establish causal fate conversion."
        )
    if strength == StrengthCeiling.predicted_effect and EvidencePredicate.predicted_perturbation_response in predicates:
        return (
            f"A registered virtual perturbation model predicts a response for {subject}{target}. This is prediction evidence, "
            "not an experimental result."
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


def surface_for_artifact(artifact: EvidenceArtifact, ceiling: StrengthCeiling) -> str:
    predicate = predicate_for_artifact(artifact)
    if ceiling == StrengthCeiling.measured_association and predicate == EvidencePredicate.differential_expression:
        return (
            f"In the measured contrast `{artifact.contrast_left}` versus `{artifact.contrast_baseline}`, this run "
            f"produced a differential-expression artifact using `{artifact.method}` with multiple-testing metadata "
            f"(`{artifact.multiple_testing}`). This supports a measured association for the resolved contrast. "
            "It does not by itself establish a validated mechanism."
        )
    if ceiling == StrengthCeiling.measured_association and predicate == EvidencePredicate.module_score_shift:
        return (
            "This run registered measured module-score evidence. It can support a measured module association for a resolved "
            "claim scope, but it does not establish a downstream mechanism, regulatory validation, or driver confirmation."
        )
    if ceiling == StrengthCeiling.measured_association and predicate == EvidencePredicate.global_transcriptomic_shift:
        return (
            "This run registered measured global-response evidence. It can support a measured global perturbation-response association "
            "for a resolved claim scope, but it does not establish a gene-specific effect, downstream mechanism, or causal cell-state transition."
        )
    if ceiling == StrengthCeiling.measured_association and predicate == EvidencePredicate.cell_state_composition_shift:
        return (
            "This run registered measured cell-state composition evidence. It can support a measured cell-state composition association "
            "for a resolved claim scope, but it does not establish gene-specific differential expression, target engagement, causal fate "
            "conversion, downstream mechanism, or driver validation."
        )
    if ceiling == StrengthCeiling.measured_association:
        return (
            "This run registered measured effect evidence. It can support a measured association for a resolved claim scope, but it does "
            "not by itself establish a validated mechanism."
        )
    if ceiling == StrengthCeiling.measured_target_engagement:
        return (
            "A registered perturbation-efficiency artifact supports measured target engagement or perturbation response. "
            "This does not establish a downstream mechanism."
        )
    if ceiling == StrengthCeiling.predicted_effect and predicate == EvidencePredicate.prediction_measured_concordance:
        return (
            "This run registered prediction-measured concordance evidence. It reports metric-bound concordance between a virtual prediction "
            "and a registered measured artifact, but it does not create measured evidence or validate a mechanism."
        )
    if ceiling == StrengthCeiling.predicted_effect and predicate == EvidencePredicate.predicted_cell_state_transition:
        return (
            "This run registered virtual cell-state transition evidence. It predicts a simulated state shift, but it does not establish "
            "causal fate conversion or mechanism validation."
        )
    if ceiling == StrengthCeiling.predicted_effect and predicate == EvidencePredicate.predicted_perturbation_response:
        return (
            "This run registered virtual perturbation prediction evidence. It predicts a perturbation response and must not be reported "
            "as an experimental result."
        )
    if ceiling == StrengthCeiling.predicted_effect:
        return "A registered prediction artifact predicts an effect. This is prediction evidence and must not be reported as an experimental result."
    if ceiling == StrengthCeiling.curated_prior_support:
        return "A curated prior artifact provides prior support. This is curated prior context, not an experimental confirmation."
    if ceiling == StrengthCeiling.replicated_measured_association:
        return "Registered measured artifacts support a replicated measured association."
    if ceiling == StrengthCeiling.observation:
        return (
            f"This run produced an observational artifact for predicate `{predicate.value}`. The registered execution metadata "
            "does not support an effect-level scientific conclusion."
        )
    return "No registered measured evidence supports this conclusion in the current run."


def differential_expression_intrinsic(artifact: EvidenceArtifact, *, policy: GatePolicy) -> ResolvedStrength:
    reasons: list[str] = []
    if not artifact.contrast_left or not artifact.contrast_baseline:
        reasons.append("measured DE artifact lacks required metadata: contrast.baseline")
    if artifact.n_left is None or artifact.n_left < policy.minimum_measured_n:
        reasons.append(f"left/target sample count is below policy minimum {policy.minimum_measured_n}")
    if artifact.n_baseline is None or artifact.n_baseline < policy.minimum_measured_n:
        reasons.append(f"baseline/control sample count is below policy minimum {policy.minimum_measured_n}")
    if not artifact.method:
        reasons.append("measured DE artifact does not record the statistical method")
    if not artifact.has_padj or not artifact.multiple_testing:
        reasons.append("measured DE artifact lacks multiple-testing metadata")
    if reasons:
        return ResolvedStrength(artifact.artifact_id, EvidenceTier.measured, StrengthCeiling.observation, reasons)
    return ResolvedStrength(
        artifact.artifact_id,
        EvidenceTier.measured,
        StrengthCeiling.measured_association,
        ["measured DE artifact has resolved contrast, sample counts, method, and multiple-testing metadata"],
    )


def target_engagement_intrinsic(artifact: EvidenceArtifact, *, policy: GatePolicy) -> ResolvedStrength:
    quality = artifact.quality
    missing: list[str] = []
    reasons: list[str] = []
    modality = norm(first(quality, "modality", "perturbation_modality"))
    expected = direction(first(quality, "expected_direction")) or expected_direction_for_modality(modality)
    observed = direction(first(quality, "observed_direction"))
    if not modality:
        missing.append("modality")
    is_ko = modality in {"crispr_ko", "ko", "knockout"}
    if expected is None and not is_ko:
        missing.append("expected_direction")
    if observed is None:
        missing.append("observed_direction")
    if first(quality, "effect_size", "logfc", "delta") is None and first(quality, "pvalue", "padj", "statistic") is None:
        missing.append("effect_size or pvalue/padj")
    if not first(quality, "method"):
        missing.append("method")
    _check_counts(quality, policy, missing, reasons)
    if missing:
        reasons.append("perturbation-efficiency artifact lacks measurement method or statistical metadata: " + ", ".join(dedupe(missing)))
    if is_ko:
        if observed == "unchanged":
            reasons.append("CRISPR-KO target mRNA unchanged is not automatic perturbation failure; target-engagement claim remains observational")
        elif observed in {"down", "altered", "changed"} and not reasons:
            return ResolvedStrength(artifact.artifact_id, EvidenceTier.measured, StrengthCeiling.measured_target_engagement, ["registered perturbation-efficiency artifact supports measured target engagement only"])
    elif expected and observed and expected != observed:
        reasons.append(f"observed direction conflicts with expected direction: expected {expected} for {modality}, observed {observed}")
    if reasons:
        return ResolvedStrength(artifact.artifact_id, EvidenceTier.measured, StrengthCeiling.observation, reasons)
    return ResolvedStrength(artifact.artifact_id, EvidenceTier.measured, StrengthCeiling.measured_target_engagement, ["registered perturbation-efficiency artifact supports measured target engagement only"])


def module_score_intrinsic(artifact: EvidenceArtifact, *, policy: GatePolicy) -> ResolvedStrength:
    quality = artifact.quality
    missing: list[str] = []
    reasons: list[str] = []
    if not first(quality, "module_id", "module_name") and not first(artifact.predicate, "module_id", "module_name"):
        missing.append("module_id/name")
    for keys, label in [
        (("module_source",), "module_source"),
        (("module_gene_set_hash",), "module_gene_set_hash"),
        (("scoring_method",), "scoring_method"),
        (("effect_size", "delta"), "effect_size"),
        (("method",), "method"),
        (("pvalue", "padj"), "pvalue/padj"),
    ]:
        if first(quality, *keys) is None:
            missing.append(label)
    _check_counts(quality, policy, missing, reasons)
    if missing:
        reasons.append("module-effect artifact lacks required execution metadata: " + ", ".join(dedupe(missing)))
    source = norm(quality.get("module_source"))
    if source == "all_cell_derived":
        reasons.append("all-cell-derived module has perturbation-contamination caveat")
    elif source == "prediction_derived":
        reasons.append("prediction-derived module source does not validate the prediction")
    if any(reason.startswith("module-effect artifact lacks") or "below policy minimum" in reason for reason in reasons):
        return ResolvedStrength(artifact.artifact_id, EvidenceTier.measured, StrengthCeiling.observation, reasons)
    return ResolvedStrength(artifact.artifact_id, EvidenceTier.measured, StrengthCeiling.measured_association, ["registered module-effect artifact supports measured module-score association only", *reasons])


def global_shift_intrinsic(artifact: EvidenceArtifact, *, policy: GatePolicy) -> ResolvedStrength:
    quality = artifact.quality
    missing: list[str] = []
    reasons: list[str] = []
    if not first(quality, "metric") and not first(artifact.predicate, "metric"):
        missing.append("metric")
    for keys, label in [
        (("feature_space", "embedding"), "feature_space/embedding"),
        (("comparison_method",), "comparison_method"),
        (("effect_size", "distance"), "effect_size/distance"),
        (("null_model", "permutation_or_test"), "null_model/permutation_or_test"),
        (("pvalue", "padj"), "pvalue/padj"),
    ]:
        if first(quality, *keys) is None:
            missing.append(label)
    _check_counts(quality, policy, missing, reasons)
    if missing:
        reasons.append("global-effect artifact lacks required execution metadata: " + ", ".join(dedupe(missing)))
    if reasons:
        return ResolvedStrength(artifact.artifact_id, EvidenceTier.measured, StrengthCeiling.observation, reasons)
    return ResolvedStrength(artifact.artifact_id, EvidenceTier.measured, StrengthCeiling.measured_association, ["registered global-effect artifact supports measured global perturbation response only"])


def composition_shift_intrinsic(artifact: EvidenceArtifact, *, policy: GatePolicy) -> ResolvedStrength:
    quality = artifact.quality
    predicate = artifact.predicate
    missing: list[str] = []
    reasons: list[str] = []
    if not first(quality, "state_source", "cell_state_reference_artifact_id", "state_reference_artifact_id") and not first(predicate, "state_source"):
        missing.append("state_source/cell_state_reference_artifact_id")
    if not first(quality, "state_assignment_column", "state_column") and not first(predicate, "state_assignment_column"):
        missing.append("state_assignment_column")
    for keys, label in [
        (("comparison_method", "method"), "comparison_method"),
        (("state_counts", "state_counts_by_condition", "counts_by_state", "composition_table", "count_table_path"), "state counts by condition"),
        (("effect_size", "delta", "state_level_deltas", "proportion_delta"), "effect_size/state_level_deltas"),
        (("pvalue", "padj", "statistic", "model_statistic"), "pvalue/padj/statistic"),
    ]:
        if first(quality, *keys) is None:
            missing.append(label)
    _check_counts(quality, policy, missing, reasons)
    if missing:
        reasons.append("composition-effect artifact lacks required execution metadata: " + ", ".join(dedupe(missing)))
    if reasons:
        return ResolvedStrength(artifact.artifact_id, EvidenceTier.measured, StrengthCeiling.observation, reasons)
    return ResolvedStrength(artifact.artifact_id, EvidenceTier.measured, StrengthCeiling.measured_association, ["registered composition-effect artifact supports measured cell-state composition association only"])



def virtual_prediction_intrinsic(artifact: EvidenceArtifact, *, policy: GatePolicy) -> ResolvedStrength:
    quality = artifact.quality
    missing: list[str] = []
    for keys, label in [
        (("tool_name",), "tool_name"),
        (("model_name",), "model_name"),
        (("prediction_method",), "prediction_method"),
        (("prediction_type",), "prediction_type"),
        (("perturbation_query",), "perturbation_query"),
        (("output_schema",), "output_schema"),
    ]:
        if first(quality, *keys) is None:
            missing.append(label)
    if first(quality, "model_version", "model_checkpoint_hash") is None:
        missing.append("model_version or model_checkpoint_hash")
    if first(quality, "n_predicted_genes", "n_predicted_cells") is None:
        missing.append("n_predicted_genes or n_predicted_cells")
    if missing:
        return ResolvedStrength(
            artifact.artifact_id,
            EvidenceTier.predicted,
            StrengthCeiling.observation,
            ["virtual perturbation prediction artifact lacks required model/output metadata: " + ", ".join(dedupe(missing))],
        )
    return ResolvedStrength(
        artifact.artifact_id,
        EvidenceTier.predicted,
        StrengthCeiling.predicted_effect,
        ["registered virtual perturbation prediction artifact supports predicted perturbation response only"],
    )


def prediction_concordance_intrinsic(artifact: EvidenceArtifact, *, policy: GatePolicy) -> ResolvedStrength:
    quality = artifact.quality
    missing: list[str] = []
    for keys, label in [
        (("prediction_artifact_id",), "prediction_artifact_id"),
        (("measured_artifact_id",), "measured_artifact_id"),
        (("metric",), "metric"),
        (("metric_value",), "metric_value"),
        (("denominator",), "denominator"),
        (("comparison_method",), "comparison_method"),
    ]:
        if first(quality, *keys) is None:
            missing.append(label)
    if missing:
        return ResolvedStrength(
            artifact.artifact_id,
            EvidenceTier.predicted,
            StrengthCeiling.observation,
            ["prediction-measured concordance artifact lacks required comparison metadata: " + ", ".join(dedupe(missing))],
        )
    return ResolvedStrength(
        artifact.artifact_id,
        EvidenceTier.predicted,
        StrengthCeiling.predicted_effect,
        ["registered prediction-measured concordance artifact reports concordance only and does not create measured evidence"],
    )


def virtual_cell_state_transition_intrinsic(artifact: EvidenceArtifact, *, policy: GatePolicy) -> ResolvedStrength:
    quality = artifact.quality
    missing: list[str] = []
    for keys, label in [
        (("tool_name",), "tool_name"),
        (("model_or_network_provenance",), "model_or_network_provenance"),
        (("transition_type",), "transition_type"),
        (("perturbation_query",), "perturbation_query"),
        (("state_space_reference",), "state_space_reference"),
    ]:
        if first(quality, *keys) is None:
            missing.append(label)
    if missing:
        return ResolvedStrength(
            artifact.artifact_id,
            EvidenceTier.predicted,
            StrengthCeiling.observation,
            ["virtual cell-state transition artifact lacks required model/state metadata: " + ", ".join(dedupe(missing))],
        )
    return ResolvedStrength(
        artifact.artifact_id,
        EvidenceTier.predicted,
        StrengthCeiling.predicted_effect,
        ["registered virtual cell-state transition artifact supports predicted state transition only"],
    )

def replication_intrinsic(artifact: EvidenceArtifact, *, policy: GatePolicy) -> ResolvedStrength:
    replication_type = artifact.quality.get("replication_type")
    resolved_ids = artifact.quality.get("resolved_artifact_ids") or []
    missing_ids = artifact.quality.get("missing_artifact_ids") or []
    allowed_types = set(policy.allowed_replication_types)
    if policy.upgrade_guide_consistency_to_replication:
        allowed_types.update({"guide_level_replication", "guide_consistency"})
    if replication_type not in allowed_types:
        return ResolvedStrength(artifact.artifact_id, EvidenceTier.composite, StrengthCeiling.observation, [f"replication_type {replication_type!r} is not an allowed independent replication axis"])
    if len(resolved_ids) < policy.replication_min_artifacts or missing_ids:
        return ResolvedStrength(artifact.artifact_id, EvidenceTier.composite, StrengthCeiling.observation, ["replication summary does not resolve enough measured artifacts"])
    return ResolvedStrength(artifact.artifact_id, EvidenceTier.composite, StrengthCeiling.replicated_measured_association, ["registered independent measured artifacts support replicated measured association"])


def tier_for_evidence_class(evidence_class: EvidenceClass) -> EvidenceTier:
    if evidence_class == EvidenceClass.measured:
        return EvidenceTier.measured
    if evidence_class == EvidenceClass.predicted:
        return EvidenceTier.predicted
    if evidence_class == EvidenceClass.curated_prior:
        return EvidenceTier.curated_prior
    if evidence_class in {EvidenceClass.composite_summary, EvidenceClass.composite}:
        return EvidenceTier.composite
    return EvidenceTier.observation


def subject_text(claim: Claim, artifacts: list[EvidenceArtifact]) -> str:
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


def target_text(claim: Claim, artifacts: list[EvidenceArtifact]) -> str:
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


def contrast_text(artifact: EvidenceArtifact) -> str:
    if artifact.contrast_left and artifact.contrast_baseline:
        return f"`{artifact.contrast_left}` versus `{artifact.contrast_baseline}`"
    if artifact.scope.get("contrast"):
        return f"`{artifact.scope['contrast']}`"
    return "registered"


def first(data: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = data.get(key)
        if value not in (None, "", [], {}):
            return value
    return None


def optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def direction(value: Any) -> str | None:
    if value in (None, ""):
        return None
    text = norm(value)
    if text in {"down", "decrease", "decreased", "lower", "downregulated", "down_regulated", "negative"}:
        return "down"
    if text in {"up", "increase", "increased", "higher", "upregulated", "up_regulated", "positive"}:
        return "up"
    if text in {"unchanged", "no_change", "none", "no_detected_difference"}:
        return "unchanged"
    return text


def expected_direction_for_modality(modality: str) -> str | None:
    if modality in {"crispri", "crispr_i", "knockdown"}:
        return "down"
    if modality in {"crispra", "crispr_a", "activation"}:
        return "up"
    return None


def norm(value: Any) -> str:
    return str(value).strip().lower().replace("-", "_").replace(" ", "_")


def dedupe(items: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        if item not in seen:
            result.append(item)
            seen.add(item)
    return result


def _check_counts(quality: dict[str, Any], policy: GatePolicy, missing: list[str], reasons: list[str]) -> None:
    n_target = optional_int(first(quality, "n_target_cells", "n_treated", "n_left"))
    n_control = optional_int(first(quality, "n_control_cells", "n_control", "n_baseline"))
    if n_target is None:
        missing.append("n_target_cells")
    elif n_target < policy.minimum_measured_n:
        reasons.append(f"target cell count is below policy minimum {policy.minimum_measured_n}")
    if n_control is None:
        missing.append("n_control_cells")
    elif n_control < policy.minimum_measured_n:
        reasons.append(f"control cell count is below policy minimum {policy.minimum_measured_n}")
