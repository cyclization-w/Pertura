from __future__ import annotations

from pathlib import Path

from pertura_gate.identity.design_manifest import scope_for_raw_label, target_uid
from pertura_gate.core.policy import DEFAULT_POLICY, GatePolicy
from pertura_gate.evidence.registry import EvidenceRegistry
from pertura_gate.render.renderer import render_evidence_report
from pertura_gate.resolver.resolver import resolve_claim
from pertura_gate.core.schema import Claim, ClaimDecisionState, ScopeFit, StrengthCeiling
from pertura_gate.identity.scope import compare_scope


def _registry(tmp_path: Path) -> EvidenceRegistry:
    (tmp_path / "outputs").mkdir(exist_ok=True)
    return EvidenceRegistry(tmp_path / "artifacts" / "evidence_artifacts.jsonl")


def _write(tmp_path: Path, name: str, text: str = "x\n") -> str:
    path = tmp_path / "outputs" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return f"outputs/{name}"


def _eligible(**overrides):
    payload = {
        "perturbation_cell_mapping": {
            "assignment_method": "guide_count_threshold",
            "guide_to_target_map_hash": "sha256:guide-map",
        },
        "control_definition": {"negative_controls": ["NegCtrl"], "control_label": "NegCtrl"},
        "target_qc": {
            "n_target_cells": 120,
            "n_control_cells": 150,
            "guides_per_target": 2,
            "cells_per_guide": {"KLF1_guide1": 60, "KLF1_guide2": 60},
            "min_cell_policy": "pertura_default_v1",
        },
        "assay_modality": "guide_based_perturb_seq",
        "perturbation_modality": "CRISPRa",
        "moi": "low",
        "estimand": "single_target_marginal",
        "control_calibration": {"negative_control_status": "available"},
    }
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(payload.get(key), dict):
            payload[key].update(value)
        else:
            payload[key] = value
    return payload


def _manifest_scope(registry: EvidenceRegistry, tmp_path: Path, raw_label: str = "KLF1_NegCtrl0__KLF1_NegCtrl0") -> dict:
    manifest = registry.register_perturbation_design_manifest(
        path=_write(tmp_path, "design_manifest.json"),
        dataset_id="GSE133344",
        raw_labels=[raw_label],
    )
    return scope_for_raw_label(manifest.metadata["manifest"], raw_label)


def _measured_artifact(registry: EvidenceRegistry, tmp_path: Path, **kwargs):
    provided_scope = kwargs.pop("scope", None)
    params = {
        "path": _write(tmp_path, "de.csv"),
        "contrast_left": "KLF1",
        "contrast_baseline": "NegCtrl",
        "method": "wilcoxon",
        "n_left": 120,
        "n_baseline": 150,
        "multiple_testing": "BH",
        "has_padj": True,
        "source_data": "GSE133344",
        "eligibility": _eligible(),
        "scope": provided_scope or _manifest_scope(registry, tmp_path),
    }
    params.update(kwargs)
    return registry.register_measured_de(**params)



def test_scope_aliases_are_compatible_but_wrong_perturbation_mismatches() -> None:
    compatible = compare_scope(
        {"perturbation": "KLF1", "control": "NegCtrl"},
        {
            "perturbation": "KLF1 CRISPRi perturbation (KLF1_NegCtrl0__KLF1_NegCtrl0)",
            "control": "pooled NegCtrl (NegCtrl10/1/11/0_NegCtrl0)",
        },
    )
    wrong_target = compare_scope(
        {"perturbation": "DUSP9", "control": "NegCtrl"},
        {
            "perturbation": "KLF1 CRISPRi perturbation (KLF1_NegCtrl0__KLF1_NegCtrl0)",
            "control": "pooled NegCtrl (NegCtrl10/1/11/0_NegCtrl0)",
        },
    )
    single_gene_from_combo = compare_scope(
        {"perturbation": "CEBPE", "control": "NegCtrl"},
        {
            "perturbation": "CEBPE_RUNX1T1__CEBPE_RUNX1T1",
            "control": "pooled NegCtrl",
        },
    )
    combo_claim = compare_scope(
        {"perturbation": "CEBPE_RUNX1T1", "control": "NegCtrl"},
        {
            "perturbation": "CEBPE_RUNX1T1__CEBPE_RUNX1T1",
            "control": "pooled NegCtrl",
        },
    )

    assert compatible == ScopeFit.compatible
    assert wrong_target == ScopeFit.mismatch
    assert single_gene_from_combo == ScopeFit.mismatch
    assert combo_claim == ScopeFit.compatible

def test_predicted_artifact_cannot_be_laundered_as_measured(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    artifact = registry.register_predicted_effect(
        path=_write(tmp_path, "prediction.csv"),
        model_name="toy-model",
        perturbation="KLF1",
        target="GENE_X",
    )
    claim = Claim(
        claim_id="claim_predicted_as_measured",
        text="KLF1 was measured to validate GENE_X activation.",
        subject={"type": "perturbation", "id": "KLF1"},
        object={"type": "gene", "id": "GENE_X"},
        scope={"perturbation": "KLF1"},
        requested_strength=StrengthCeiling.measured_association,
        evidence_refs=[artifact.artifact_id],
    )

    decision = resolve_claim(claim, registry)

    assert decision.max_strength == StrengthCeiling.predicted_effect
    assert decision.decision == ClaimDecisionState.allowed_with_downgrade
    assert "prediction artifact predicts" in decision.allowed_surface
    assert "experimental result" in decision.allowed_surface
    assert "measured" not in decision.allowed_surface.lower()
    assert "validate" not in decision.allowed_surface.lower()


def test_curated_prior_cannot_be_laundered_as_validation(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    artifact = registry.register_curated_prior(
        path=_write(tmp_path, "prior.json"),
        database="Reactome",
        database_version="v1",
        term_id="R-HSA-0000",
        term_name="erythroid biology",
        target="GENE_X",
    )
    claim = Claim(
        claim_id="claim_prior_as_validation",
        text="Reactome validates the mechanism.",
        subject={"id": "KLF1"},
        object={"id": "GENE_X"},
        requested_strength=StrengthCeiling.validated_mechanism_disabled,
        evidence_refs=[artifact.artifact_id],
    )

    decision = resolve_claim(claim, registry)

    assert decision.max_strength == StrengthCeiling.curated_prior_support
    assert decision.decision == ClaimDecisionState.allowed_with_downgrade
    assert "curated prior" in decision.allowed_surface.lower()
    assert "proves" not in decision.allowed_surface.lower()
    assert "validation" not in decision.allowed_surface.lower()
    assert "validated mechanism" not in decision.allowed_surface.lower()


def test_measured_association_blocks_mechanism_strength(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    artifact = _measured_artifact(registry, tmp_path)
    claim = Claim(
        claim_id="claim_mechanism",
        text="KLF1 causes an erythroid differentiation mechanism.",
        subject={"id": "KLF1"},
        object={"id": "erythroid_differentiation"},
        scope=artifact.scope,
        requested_strength=StrengthCeiling.validated_mechanism_disabled,
        evidence_refs=[artifact.artifact_id],
    )

    decision = resolve_claim(claim, registry)

    assert decision.max_strength == StrengthCeiling.measured_association
    assert decision.decision == ClaimDecisionState.allowed_with_downgrade
    assert decision.blocked_requested_strength == StrengthCeiling.validated_mechanism_disabled
    assert "measured association only" in decision.allowed_surface


def test_measured_de_with_only_prose_eligibility_is_not_measured_association(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    artifact = _measured_artifact(
        registry,
        tmp_path,
        eligibility={"eligibility_passed": True, "notes": "guide assignment passed"},
    )
    claim = Claim(
        claim_id="claim_prose_only",
        text="KLF1 has a measured association.",
        subject={"id": "KLF1"},
        scope=artifact.scope,
        requested_strength=StrengthCeiling.measured_association,
        evidence_refs=[artifact.artifact_id],
    )

    decision = resolve_claim(claim, registry)

    assert decision.max_strength == StrengthCeiling.observation
    assert any("structured perturbation-cell mapping" in reason or "negative control" in reason for reason in decision.reasons)


def test_independent_design_guide_assignment_and_target_qc_build_eligibility(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    design = registry.register_experiment_design(
        path=_write(tmp_path, "design.json"),
        assay="guide_based_perturb_seq",
        perturbation_modality="CRISPRa",
        moi="low",
        controls={"negative_controls": ["NegCtrl"], "control_label": "NegCtrl"},
        scope={"dataset_id": "GSE133344"},
    )
    guide = registry.register_guide_assignment(
        path=_write(tmp_path, "guides.json"),
        assignment_method="guide_count_threshold",
        guide_to_target_map_hash="sha256:guide-map",
        scope={"dataset_id": "GSE133344"},
    )
    qc = registry.register_target_qc(
        path=_write(tmp_path, "target_qc.json"),
        target="KLF1",
        control="NegCtrl",
        n_target_cells=120,
        n_control_cells=150,
        guides_per_target=2,
        scope={"dataset_id": "GSE133344", "perturbation": "KLF1", "control": "NegCtrl"},
    )
    artifact = _measured_artifact(registry, tmp_path, eligibility={})
    claim = Claim(
        claim_id="claim_with_independent_eligibility",
        text="KLF1 is associated with expression differences.",
        subject={"id": "KLF1"},
        scope=artifact.scope,
        requested_strength=StrengthCeiling.measured_association,
        evidence_refs=[artifact.artifact_id],
    )

    decision = resolve_claim(claim, registry)

    assert {design.artifact_id, guide.artifact_id, qc.artifact_id}
    assert decision.max_strength == StrengthCeiling.measured_association
    assert "EligibilityProfile satisfied" in "; ".join(decision.reasons)



def test_measured_artifact_explicit_eligibility_ids_build_profile(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    manifest = registry.register_perturbation_design_manifest(
        path=_write(tmp_path, "manifest.json"),
        dataset_id="GSE133344",
        raw_labels=["KLF1_NegCtrl0__KLF1_NegCtrl0", "NegCtrl0_NegCtrl0__NegCtrl0_NegCtrl0"],
    )
    manifest_scope = scope_for_raw_label(manifest.metadata["manifest"], "KLF1_NegCtrl0__KLF1_NegCtrl0")
    dataset_scope = {"dataset_id": "GSE133344", "design_manifest_id": manifest.artifact_id}
    design = registry.register_experiment_design(
        path=_write(tmp_path, "design.json"),
        assay="guide_based_perturb_seq",
        perturbation_modality="CRISPRa",
        moi="low",
        controls={"negative_controls": ["NegCtrl0"], "control_label": "NegCtrl0"},
        scope=dataset_scope,
    )
    guide = registry.register_guide_assignment(
        path=_write(tmp_path, "guides.json"),
        assignment_method="observed_guide_barcode_from_cell_identities",
        guide_to_target_map_hash="sha256:guide-map",
        scope=dataset_scope,
    )
    qc = registry.register_target_qc(
        path=_write(tmp_path, "target_qc.json"),
        target="KLF1",
        control="NegCtrl0",
        n_target_cells=1121,
        n_control_cells=2379,
        guides_per_target=1,
        scope=dataset_scope,
    )
    artifact = _measured_artifact(
        registry,
        tmp_path,
        scope=manifest_scope,
        eligibility={
            "experiment_design_id": design.artifact_id,
            "guide_assignment_id": guide.artifact_id,
            "target_qc_id": qc.artifact_id,
            "estimand": "single_target_marginal",
            "n_target_cells": 1121,
            "n_control_cells": 2379,
        },
    )
    claim = Claim(
        claim_id="claim_explicit_eligibility_ids",
        text="KLF1 has a measured association.",
        subject={"id": "KLF1"},
        scope=artifact.scope,
        requested_strength=StrengthCeiling.measured_association,
        evidence_refs=[artifact.artifact_id],
    )

    decision = resolve_claim(claim, registry)

    assert decision.max_strength == StrengthCeiling.measured_association
    joined = "; ".join(decision.reasons)
    assert design.artifact_id in joined
    assert guide.artifact_id in joined
    assert qc.artifact_id in joined
def test_negative_control_missing_blocks_target_vs_control_association(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    artifact = _measured_artifact(registry, tmp_path, eligibility={"perturbation_cell_mapping": {"assignment_method": "guide_count_threshold", "guide_to_target_map_hash": "sha256:guide-map"}, "target_qc": {"n_target_cells": 120, "n_control_cells": 150}, "assay_modality": "guide_based_perturb_seq", "moi": "low", "estimand": "single_target_marginal"})
    claim = Claim(
        claim_id="claim_no_negative_control",
        text="KLF1 is associated with measured expression changes.",
        subject={"id": "KLF1"},
        scope=artifact.scope,
        requested_strength=StrengthCeiling.measured_association,
        evidence_refs=[artifact.artifact_id],
    )

    decision = resolve_claim(claim, registry)

    assert decision.max_strength == StrengthCeiling.observation
    assert any("negative control" in reason for reason in decision.reasons)


def test_high_moi_naive_single_target_marginal_is_blocked(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    artifact = _measured_artifact(registry, tmp_path, eligibility=_eligible(moi="high", estimand="single_target_marginal"))
    claim = Claim(
        claim_id="claim_high_moi_naive",
        text="KLF1 has a marginal single-target effect.",
        subject={"id": "KLF1"},
        scope=artifact.scope,
        requested_strength=StrengthCeiling.measured_association,
        evidence_refs=[artifact.artifact_id],
    )

    decision = resolve_claim(claim, registry)

    assert decision.max_strength == StrengthCeiling.observation
    assert any("high-MOI naive" in reason for reason in decision.reasons)


def test_high_moi_conditional_estimand_with_covariates_can_pass(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    eligibility = _eligible(
        moi="high",
        estimand="single_target_conditional",
        target_qc={"model_covariates": ["co_perturbation_count", "batch"]},
    )
    artifact = _measured_artifact(registry, tmp_path, eligibility=eligibility)
    claim = Claim(
        claim_id="claim_high_moi_conditional",
        text="KLF1 has a conditional measured association.",
        subject={"id": "KLF1"},
        scope=artifact.scope,
        requested_strength=StrengthCeiling.measured_association,
        evidence_refs=[artifact.artifact_id],
    )

    decision = resolve_claim(claim, registry)

    assert decision.max_strength == StrengthCeiling.measured_association


def test_min_cell_policy_changes_decision_and_policy_hash(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    artifact = _measured_artifact(
        registry,
        tmp_path,
        n_left=37,
        n_baseline=80,
        eligibility=_eligible(target_qc={"n_target_cells": 37, "n_control_cells": 80}),
    )
    claim = Claim(
        claim_id="claim_policy_threshold",
        text="KLF1 has a measured association.",
        subject={"id": "KLF1"},
        scope=artifact.scope,
        requested_strength=StrengthCeiling.measured_association,
        evidence_refs=[artifact.artifact_id],
    )
    strict = GatePolicy(minimum_measured_n=50)
    relaxed = GatePolicy(minimum_measured_n=30)

    strict_decision = resolve_claim(claim, registry, policy=strict)
    relaxed_decision = resolve_claim(claim, registry, policy=relaxed)

    assert strict.policy_hash != relaxed.policy_hash
    assert strict_decision.max_strength == StrengthCeiling.observation
    assert relaxed_decision.max_strength == StrengthCeiling.measured_association


def test_wrong_contrast_is_scope_mismatch_and_unsupported(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    artifact = _measured_artifact(registry, tmp_path)
    claim = Claim(
        claim_id="claim_wrong_contrast",
        text="DUSP9 has a measured effect in this artifact.",
        subject={"id": "DUSP9"},
        scope={"design_manifest_id": artifact.scope["design_manifest_id"], "perturbation_uid": target_uid("DUSP9"), "control_uid": artifact.scope["control_uid"]},
        requested_strength=StrengthCeiling.measured_association,
        evidence_refs=[artifact.artifact_id],
    )

    decision = resolve_claim(claim, registry)

    assert decision.max_strength == StrengthCeiling.unsupported
    assert decision.scope_fit == ScopeFit.mismatch
    assert decision.supporting_artifacts == []


def test_model_supplied_evidence_tag_is_ignored_by_validator(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    artifact = registry.register_predicted_effect(
        path=_write(tmp_path, "prediction.json", '{"evidence_class":"measured","strength":"validated_mechanism"}\n'),
        model_name="toy-model",
        perturbation="KLF1",
        target="GENE_X",
        metadata={"evidence_class": "measured", "strength": "validated_mechanism"},
    )

    loaded = registry.get(artifact.artifact_id)

    assert loaded is not None
    assert loaded.effective_evidence_class.value == "predicted"
    assert loaded.kind.value == "predicted_effect"


def test_policy_hash_is_stable_and_changes_when_policy_changes() -> None:
    default_again = GatePolicy()
    changed = GatePolicy(minimum_measured_n=10)

    assert DEFAULT_POLICY.policy_hash == default_again.policy_hash
    assert DEFAULT_POLICY.policy_hash != changed.policy_hash


def test_artifact_source_hash_detects_modified_file(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    path = _write(tmp_path, "de.csv", "gene,padj\nA,0.01\n")
    artifact = registry.register_measured_de(
        path=path,
        contrast_left="KLF1",
        contrast_baseline="NegCtrl",
        method="wilcoxon",
        n_left=120,
        n_baseline=150,
        multiple_testing="BH",
        has_padj=True,
        eligibility=_eligible(),
    )

    assert registry.source_hash_status(artifact) == "match"
    (tmp_path / path).write_text("gene,padj\nA,0.50\n", encoding="utf-8")
    assert registry.source_hash_status(artifact) == "mismatch"


def test_replication_requires_allowed_independent_axis(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    a = _measured_artifact(registry, tmp_path, path=_write(tmp_path, "a.csv"), predicate={"target": "GENE_X"})
    b = _measured_artifact(registry, tmp_path, path=_write(tmp_path, "b.csv"), predicate={"target": "GENE_X"})
    weak = registry.register_replication(
        measured_artifact_ids=[a.artifact_id, b.artifact_id],
        replication_type="guide_level_replication",
    )
    strong = registry.register_replication(
        measured_artifact_ids=[a.artifact_id, b.artifact_id],
        replication_type="biological_replicate_replication",
    )

    weak_decision = resolve_claim(Claim(
        claim_id="weak_rep",
        text="KLF1 replicated association with GENE_X.",
        subject={"id": "KLF1"},
        object={"id": "GENE_X"},
        requested_strength=StrengthCeiling.replicated_measured_association,
        evidence_refs=[weak.artifact_id],
    ), registry)
    strong_decision = resolve_claim(Claim(
        claim_id="strong_rep",
        text="KLF1 replicated association with GENE_X.",
        subject={"id": "KLF1"},
        object={"id": "GENE_X"},
        requested_strength=StrengthCeiling.replicated_measured_association,
        evidence_refs=[strong.artifact_id],
    ), registry)

    assert weak_decision.max_strength == StrengthCeiling.observation
    assert strong_decision.max_strength == StrengthCeiling.replicated_measured_association


def test_claim_report_renders_decision_table(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    artifact = registry.register_predicted_effect(
        path=_write(tmp_path, "prediction.csv"),
        model_name="toy-model",
        perturbation="KLF1",
    )
    report = render_evidence_report(
        registry=registry,
        artifact_ids=[artifact.artifact_id],
        claims=[{
            "claim_id": "claim_pred",
            "text": "KLF1 was observed to validate a mechanism.",
            "subject": {"id": "KLF1"},
            "scope": {"perturbation": "KLF1"},
            "requested_strength": "validated_mechanism_disabled",
            "evidence_refs": [artifact.artifact_id],
        }],
        write_path=tmp_path / "reports" / "evidence_report.md",
    )

    text = report.markdown
    assert "Runtime-calibrated findings" in text
    assert "Evidence / decision table" in text
    assert "predicted_effect" in text
    assert report.decisions[0].max_strength == StrengthCeiling.predicted_effect





def test_crispri_target_down_supports_measured_target_engagement(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    scope = _manifest_scope(registry, tmp_path)
    artifact = registry.register_perturbation_efficiency(
        path=_write(tmp_path, "target_engagement.csv"),
        perturbation="KLF1",
        target_gene="KLF1",
        modality="CRISPRi",
        expected_direction="down",
        observed_direction="down",
        effect_size=-1.2,
        method="target expression DE",
        n_target_cells=120,
        n_control_cells=150,
        scope=scope,
    )
    claim = Claim(
        claim_id="target_engagement_not_mechanism",
        text="KLF1 perturbation validates a downstream mechanism.",
        subject={"id": "KLF1"},
        object={"id": "KLF1"},
        scope=scope,
        requested_strength=StrengthCeiling.validated_mechanism_disabled,
        evidence_refs=[artifact.artifact_id],
    )

    decision = resolve_claim(claim, registry)

    assert decision.max_strength == StrengthCeiling.measured_target_engagement
    assert decision.decision == ClaimDecisionState.allowed_with_downgrade
    assert decision.blocked_requested_strength == StrengthCeiling.validated_mechanism_disabled
    assert "target engagement" in decision.allowed_surface.lower()
    assert "downstream mechanism" in decision.allowed_surface.lower()
    assert "validates" not in decision.allowed_surface.lower()


def test_crispra_target_up_supports_measured_target_engagement(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    scope = _manifest_scope(registry, tmp_path)
    artifact = registry.register_perturbation_efficiency(
        path=_write(tmp_path, "target_engagement_a.csv"),
        perturbation="KLF1",
        target_gene="KLF1",
        modality="CRISPRa",
        expected_direction="up",
        observed_direction="up",
        effect_size=1.1,
        method="target expression DE",
        n_target_cells=120,
        n_control_cells=150,
        scope=scope,
    )

    decision = resolve_claim(Claim(
        claim_id="crispra_target_up",
        text="KLF1 target engagement was measured.",
        subject={"id": "KLF1"},
        object={"id": "KLF1"},
        scope=scope,
        requested_strength=StrengthCeiling.measured_target_engagement,
        evidence_refs=[artifact.artifact_id],
    ), registry)

    assert decision.max_strength == StrengthCeiling.measured_target_engagement


def test_target_engagement_requires_manifest_uid_scope_for_measured_strength(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    artifact = registry.register_perturbation_efficiency(
        path=_write(tmp_path, "raw_scope_target_engagement.csv"),
        perturbation="KLF1",
        target_gene="KLF1",
        modality="CRISPRi",
        expected_direction="down",
        observed_direction="down",
        effect_size=-1.2,
        method="target expression DE",
        n_target_cells=120,
        n_control_cells=150,
        scope={"perturbation": "KLF1"},
    )

    decision = resolve_claim(Claim(
        claim_id="raw_scope_target_engagement",
        text="KLF1 target engagement was measured.",
        subject={"id": "KLF1"},
        object={"id": "KLF1"},
        scope={"perturbation": "KLF1"},
        requested_strength=StrengthCeiling.measured_target_engagement,
        evidence_refs=[artifact.artifact_id],
    ), registry)

    assert decision.max_strength == StrengthCeiling.observation
    assert any("PerturbationDesignManifest UID" in reason for reason in decision.reasons)


def test_crispri_target_up_direction_conflict_downgrades(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    scope = _manifest_scope(registry, tmp_path)
    artifact = registry.register_perturbation_efficiency(
        path=_write(tmp_path, "target_conflict.csv"),
        perturbation="KLF1",
        target_gene="KLF1",
        modality="CRISPRi",
        expected_direction="down",
        observed_direction="up",
        effect_size=0.8,
        method="target expression DE",
        n_target_cells=120,
        n_control_cells=150,
        scope=scope,
    )

    decision = resolve_claim(Claim(
        claim_id="crispri_direction_conflict",
        text="KLF1 target engagement was measured.",
        subject={"id": "KLF1"},
        object={"id": "KLF1"},
        scope=scope,
        requested_strength=StrengthCeiling.measured_target_engagement,
        evidence_refs=[artifact.artifact_id],
    ), registry)

    assert decision.max_strength == StrengthCeiling.observation
    assert any("conflicts" in reason for reason in decision.reasons)


def test_crispra_target_down_direction_conflict_downgrades(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    scope = _manifest_scope(registry, tmp_path)
    artifact = registry.register_perturbation_efficiency(
        path=_write(tmp_path, "target_conflict_a.csv"),
        perturbation="KLF1",
        target_gene="KLF1",
        modality="CRISPRa",
        expected_direction="up",
        observed_direction="down",
        effect_size=-0.8,
        method="target expression DE",
        n_target_cells=120,
        n_control_cells=150,
        scope=scope,
    )

    decision = resolve_claim(Claim(
        claim_id="crispra_direction_conflict",
        text="KLF1 target engagement was measured.",
        subject={"id": "KLF1"},
        object={"id": "KLF1"},
        scope=scope,
        requested_strength=StrengthCeiling.measured_target_engagement,
        evidence_refs=[artifact.artifact_id],
    ), registry)

    assert decision.max_strength == StrengthCeiling.observation
    assert any("conflicts" in reason for reason in decision.reasons)


def test_crispr_ko_target_down_can_support_target_engagement(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    scope = _manifest_scope(registry, tmp_path)
    artifact = registry.register_perturbation_efficiency(
        path=_write(tmp_path, "ko_target_down.csv"),
        perturbation="KLF1",
        target_gene="KLF1",
        modality="CRISPR-KO",
        observed_direction="down",
        effect_size=-1.1,
        method="target expression DE",
        n_target_cells=120,
        n_control_cells=150,
        scope=scope,
    )

    decision = resolve_claim(Claim(
        claim_id="ko_target_down",
        text="KLF1 knockout target engagement was measured.",
        subject={"id": "KLF1"},
        object={"id": "KLF1"},
        scope=scope,
        requested_strength=StrengthCeiling.measured_target_engagement,
        evidence_refs=[artifact.artifact_id],
    ), registry)

    assert decision.max_strength == StrengthCeiling.measured_target_engagement


def test_crispr_ko_unchanged_mrna_is_not_automatic_failure(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    scope = _manifest_scope(registry, tmp_path)
    artifact = registry.register_perturbation_efficiency(
        path=_write(tmp_path, "ko_unchanged.csv"),
        perturbation="KLF1",
        target_gene="KLF1",
        modality="CRISPR-KO",
        observed_direction="unchanged",
        effect_size=0.0,
        method="target expression DE",
        n_target_cells=120,
        n_control_cells=150,
        scope=scope,
    )

    decision = resolve_claim(Claim(
        claim_id="ko_unchanged",
        text="KLF1 knockout perturbation failed because mRNA was unchanged.",
        subject={"id": "KLF1"},
        object={"id": "KLF1"},
        scope=scope,
        requested_strength=StrengthCeiling.measured_target_engagement,
        evidence_refs=[artifact.artifact_id],
    ), registry)

    assert decision.max_strength == StrengthCeiling.observation
    assert any("not automatic" in reason for reason in decision.reasons)


def test_target_engagement_missing_method_or_effect_metadata_downgrades(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    scope = _manifest_scope(registry, tmp_path)
    artifact = registry.register_perturbation_efficiency(
        path=_write(tmp_path, "target_missing_metadata.csv"),
        perturbation="KLF1",
        target_gene="KLF1",
        modality="CRISPRi",
        expected_direction="down",
        observed_direction="down",
        n_target_cells=120,
        n_control_cells=150,
        scope=scope,
    )

    decision = resolve_claim(Claim(
        claim_id="target_missing_metadata",
        text="KLF1 target engagement was measured.",
        subject={"id": "KLF1"},
        object={"id": "KLF1"},
        scope=scope,
        requested_strength=StrengthCeiling.measured_target_engagement,
        evidence_refs=[artifact.artifact_id],
    ), registry)

    assert decision.max_strength == StrengthCeiling.observation
    assert any("measurement method" in reason or "statistical metadata" in reason for reason in decision.reasons)


def test_target_engagement_low_cell_counts_downgrades(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    scope = _manifest_scope(registry, tmp_path)
    artifact = registry.register_perturbation_efficiency(
        path=_write(tmp_path, "target_low_n.csv"),
        perturbation="KLF1",
        target_gene="KLF1",
        modality="CRISPRi",
        expected_direction="down",
        observed_direction="down",
        effect_size=-1.2,
        method="target expression DE",
        n_target_cells=10,
        n_control_cells=150,
        scope=scope,
    )

    decision = resolve_claim(Claim(
        claim_id="target_low_n",
        text="KLF1 target engagement was measured.",
        subject={"id": "KLF1"},
        object={"id": "KLF1"},
        scope=scope,
        requested_strength=StrengthCeiling.measured_target_engagement,
        evidence_refs=[artifact.artifact_id],
    ), registry, policy=GatePolicy(minimum_measured_n=50))

    assert decision.max_strength == StrengthCeiling.observation
    assert any("below policy minimum" in reason for reason in decision.reasons)


def test_target_engagement_self_tags_cannot_upgrade_to_mechanism(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    scope = _manifest_scope(registry, tmp_path)
    artifact = registry.register_perturbation_efficiency(
        path=_write(tmp_path, "target_self_tags.csv"),
        perturbation="KLF1",
        target_gene="KLF1",
        modality="CRISPRi",
        expected_direction="down",
        observed_direction="down",
        effect_size=-1.2,
        method="target expression DE",
        n_target_cells=120,
        n_control_cells=150,
        scope=scope,
        quality={"evidence_class": "validated", "strength": "validated_mechanism", "validated_mechanism": True},
    )

    decision = resolve_claim(Claim(
        claim_id="target_self_tags",
        text="KLF1 target engagement validates a downstream mechanism.",
        subject={"id": "KLF1"},
        object={"id": "KLF1"},
        scope=scope,
        requested_strength=StrengthCeiling.validated_mechanism_disabled,
        evidence_refs=[artifact.artifact_id],
    ), registry)

    assert decision.max_strength == StrengthCeiling.measured_target_engagement
    assert decision.blocked_requested_strength == StrengthCeiling.validated_mechanism_disabled
    assert "validates" not in decision.allowed_surface.lower()

def test_curated_enrichment_bound_to_measured_de_adds_context_not_validation(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    measured = _measured_artifact(registry, tmp_path)
    enrichment = registry.register_curated_enrichment(
        path=_write(tmp_path, "enrichment.json"),
        input_measured_artifact_id=measured.artifact_id,
        input_gene_set_hash="sha256:genes",
        background_universe="expressed_genes",
        database="Reactome",
        database_version="v1",
        term_id="R-HSA-0000",
        term_name="erythroid biology",
        method="ora",
        pvalue=0.001,
        padj=0.01,
        scope=measured.scope,
    )
    claim = Claim(
        claim_id="enrichment_as_validation",
        text="Reactome enrichment validates a mechanism.",
        subject={"id": "KLF1"},
        object={"id": "erythroid biology"},
        scope=measured.scope,
        requested_strength=StrengthCeiling.validated_mechanism_disabled,
        evidence_refs=[enrichment.artifact_id],
    )

    decision = resolve_claim(claim, registry)

    assert decision.max_strength == StrengthCeiling.measured_association
    assert decision.decision == ClaimDecisionState.allowed_with_downgrade
    assert "curated context" in decision.allowed_surface.lower()
    assert "validation" in decision.allowed_surface.lower()
    assert "not a validation" in decision.allowed_surface.lower()


def test_unbound_curated_enrichment_cannot_become_measured(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    enrichment = registry.register_curated_enrichment(
        path=_write(tmp_path, "unbound_enrichment.json"),
        database="Reactome",
        database_version="v1",
        term_id="R-HSA-0000",
        method="ora",
        padj=0.01,
        scope={"perturbation": "KLF1"},
    )

    decision = resolve_claim(Claim(
        claim_id="unbound_enrichment",
        text="Enrichment was measured for KLF1.",
        subject={"id": "KLF1"},
        scope={"perturbation": "KLF1"},
        requested_strength=StrengthCeiling.measured_association,
        evidence_refs=[enrichment.artifact_id],
    ), registry)

    assert decision.max_strength == StrengthCeiling.curated_prior_support
    assert any("not bound" in reason for reason in decision.reasons)


def test_failed_cell_qc_downgrades_measured_association(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    artifact = _measured_artifact(registry, tmp_path)
    registry.register_cell_qc(
        path=_write(tmp_path, "cell_qc.json"),
        n_cells_after_qc=10,
        qc_policy="toy_qc",
        doublet_policy="failed",
        ambient_policy="failed",
        passed=False,
        scope=artifact.scope,
    )
    claim = Claim(
        claim_id="cell_qc_failed",
        text="KLF1 has a measured association.",
        subject={"id": "KLF1"},
        scope=artifact.scope,
        requested_strength=StrengthCeiling.measured_association,
        evidence_refs=[artifact.artifact_id],
    )

    decision = resolve_claim(claim, registry)

    assert decision.max_strength == StrengthCeiling.observation
    assert any("cell QC" in reason for reason in decision.reasons)





def test_string_placeholder_cell_qc_does_not_crash_and_failed_qc_downgrades(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    artifact = _measured_artifact(
        registry,
        tmp_path,
        eligibility={**_eligible(), "cell_qc": "not_yet_evaluated", "target_qc": "not_yet_evaluated"},
    )
    registry.register_cell_qc(
        path=_write(tmp_path, "cell_qc_placeholder_guard.json"),
        n_cells_after_qc=10,
        qc_policy="toy_qc",
        doublet_policy="failed",
        ambient_policy="failed",
        passed=False,
        scope=artifact.scope,
    )
    claim = Claim(
        claim_id="cell_qc_string_placeholder",
        text="KLF1 has a measured association.",
        subject={"id": "KLF1"},
        scope=artifact.scope,
        requested_strength=StrengthCeiling.measured_association,
        evidence_refs=[artifact.artifact_id],
    )

    decision = resolve_claim(claim, registry)

    assert decision.max_strength == StrengthCeiling.observation
    assert any("cell QC" in reason for reason in decision.reasons)


def test_measured_de_promotes_manifest_uid_from_structured_eligibility_for_cell_qc_gate(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    manifest_scope = _manifest_scope(registry, tmp_path)
    raw_scope = {
        "dataset_id": "GSE133344",
        "design_manifest_id": manifest_scope["design_manifest_id"],
        "perturbation": "KLF1",
        "control": "NegCtrl",
    }
    artifact = _measured_artifact(
        registry,
        tmp_path,
        scope=raw_scope,
        eligibility={
            **_eligible(),
            "design_manifest_id": manifest_scope["design_manifest_id"],
            "perturbation_uid": manifest_scope["perturbation_uid"],
            "estimand": "single_target_marginal",
        },
    )
    registry.register_cell_qc(
        path=_write(tmp_path, "cell_qc_promoted_scope.json"),
        n_cells_after_qc=10,
        qc_policy="toy_qc",
        doublet_policy="failed",
        ambient_policy="failed",
        passed=False,
        scope=artifact.scope,
    )
    claim = Claim(
        claim_id="cell_qc_promoted_scope",
        text="KLF1 has a measured association.",
        subject={"id": "KLF1"},
        scope=artifact.scope,
        requested_strength=StrengthCeiling.measured_association,
        evidence_refs=[artifact.artifact_id],
    )

    decision = resolve_claim(claim, registry)

    assert artifact.scope["perturbation_uid"] == manifest_scope["perturbation_uid"]
    assert decision.scope_fit == ScopeFit.exact
    assert decision.max_strength == StrengthCeiling.observation
    assert any("cell QC" in reason for reason in decision.reasons)


def test_passed_structured_cell_qc_can_support_measured_association(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    artifact = _measured_artifact(registry, tmp_path)
    registry.register_cell_qc(
        path=_write(tmp_path, "cell_qc_passed.json"),
        n_cells_after_qc=1000,
        qc_policy="standard_scanpy_qc",
        doublet_policy="filtered",
        ambient_policy="reviewed",
        passed=True,
        scope=artifact.scope,
    )

    decision = resolve_claim(Claim(
        claim_id="cell_qc_passed_measured",
        text="KLF1 has a measured association.",
        subject={"id": "KLF1"},
        scope=artifact.scope,
        requested_strength=StrengthCeiling.measured_association,
        evidence_refs=[artifact.artifact_id],
    ), registry, policy=GatePolicy(require_cell_qc_for_measured_claims=True, minimum_qc_cells=50))

    assert decision.max_strength == StrengthCeiling.measured_association


def test_cell_qc_artifact_alone_is_not_effect_evidence(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    artifact = registry.register_cell_qc(
        path=_write(tmp_path, "cell_qc_only.json"),
        n_cells_after_qc=1000,
        qc_policy="standard_scanpy_qc",
        passed=True,
        scope={"dataset_id": "local"},
    )

    decision = resolve_claim(Claim(
        claim_id="cell_qc_as_effect",
        text="Cell QC proves KLF1 has a measured effect.",
        subject={"id": "KLF1"},
        scope={"dataset_id": "local"},
        requested_strength=StrengthCeiling.measured_association,
        evidence_refs=[artifact.artifact_id],
    ), registry)

    assert decision.max_strength == StrengthCeiling.observation


def test_missing_cell_qc_default_policy_does_not_block_measured_association(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    artifact = _measured_artifact(registry, tmp_path)

    decision = resolve_claim(Claim(
        claim_id="missing_cell_qc_default",
        text="KLF1 has a measured association.",
        subject={"id": "KLF1"},
        scope=artifact.scope,
        requested_strength=StrengthCeiling.measured_association,
        evidence_refs=[artifact.artifact_id],
    ), registry)

    assert decision.max_strength == StrengthCeiling.measured_association


def test_missing_cell_qc_required_policy_downgrades(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    artifact = _measured_artifact(registry, tmp_path)

    decision = resolve_claim(Claim(
        claim_id="missing_cell_qc_required",
        text="KLF1 has a measured association.",
        subject={"id": "KLF1"},
        scope=artifact.scope,
        requested_strength=StrengthCeiling.measured_association,
        evidence_refs=[artifact.artifact_id],
    ), registry, policy=GatePolicy(require_cell_qc_for_measured_claims=True))

    assert decision.max_strength == StrengthCeiling.observation
    assert any("requires structured cell QC" in reason for reason in decision.reasons)


def test_low_post_qc_cell_count_downgrades_under_policy(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    artifact = _measured_artifact(registry, tmp_path)
    registry.register_cell_qc(
        path=_write(tmp_path, "cell_qc_low_n.json"),
        n_cells_after_qc=10,
        qc_policy="standard_scanpy_qc",
        passed=True,
        scope=artifact.scope,
    )

    decision = resolve_claim(Claim(
        claim_id="low_cell_qc_n",
        text="KLF1 has a measured association.",
        subject={"id": "KLF1"},
        scope=artifact.scope,
        requested_strength=StrengthCeiling.measured_association,
        evidence_refs=[artifact.artifact_id],
    ), registry, policy=GatePolicy(minimum_qc_cells=50))

    assert decision.max_strength == StrengthCeiling.observation
    assert any("post-QC cell count" in reason for reason in decision.reasons)


def test_boolean_only_cell_qc_passed_does_not_satisfy_required_qc(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    artifact = _measured_artifact(
        registry,
        tmp_path,
        eligibility={**_eligible(), "cell_qc": {"qc_passed": True}},
    )

    decision = resolve_claim(Claim(
        claim_id="boolean_only_cell_qc",
        text="KLF1 has a measured association.",
        subject={"id": "KLF1"},
        scope=artifact.scope,
        requested_strength=StrengthCeiling.measured_association,
        evidence_refs=[artifact.artifact_id],
    ), registry, policy=GatePolicy(require_cell_qc_for_measured_claims=True))

    assert decision.max_strength == StrengthCeiling.observation
    assert any("boolean-only cell QC" in reason for reason in decision.reasons)


def test_mismatched_failed_cell_qc_does_not_downgrade_unrelated_measured_artifact(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    artifact = _measured_artifact(registry, tmp_path)
    registry.register_cell_qc(
        path=_write(tmp_path, "cell_qc_wrong_scope.json"),
        n_cells_after_qc=10,
        qc_policy="failed_qc",
        passed=False,
        scope={
            "design_manifest_id": artifact.scope["design_manifest_id"],
            "perturbation_uid": "target:DUSP9",
            "control_uid": artifact.scope.get("control_uid"),
            "estimand": artifact.scope.get("estimand"),
        },
    )

    decision = resolve_claim(Claim(
        claim_id="mismatched_cell_qc",
        text="KLF1 has a measured association.",
        subject={"id": "KLF1"},
        scope=artifact.scope,
        requested_strength=StrengthCeiling.measured_association,
        evidence_refs=[artifact.artifact_id],
    ), registry)

    assert decision.max_strength == StrengthCeiling.measured_association


def test_failed_cell_qc_downgrades_target_engagement(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    scope = _manifest_scope(registry, tmp_path)
    artifact = registry.register_perturbation_efficiency(
        path=_write(tmp_path, "target_engagement_with_failed_qc.csv"),
        perturbation="KLF1",
        target_gene="KLF1",
        modality="CRISPRi",
        expected_direction="down",
        observed_direction="down",
        effect_size=-1.2,
        method="target expression DE",
        n_target_cells=120,
        n_control_cells=150,
        scope=scope,
    )
    registry.register_cell_qc(
        path=_write(tmp_path, "failed_cell_qc_for_target.json"),
        n_cells_after_qc=20,
        qc_policy="failed_qc",
        passed=False,
        scope=scope,
    )

    decision = resolve_claim(Claim(
        claim_id="target_engagement_failed_cell_qc",
        text="KLF1 target engagement was measured.",
        subject={"id": "KLF1"},
        object={"id": "KLF1"},
        scope=scope,
        requested_strength=StrengthCeiling.measured_target_engagement,
        evidence_refs=[artifact.artifact_id],
    ), registry)

    assert decision.max_strength == StrengthCeiling.observation
    assert any("cell QC" in reason for reason in decision.reasons)


def test_cell_qc_policy_changes_policy_hash() -> None:
    default = GatePolicy()
    changed = GatePolicy(require_cell_qc_for_measured_claims=True, minimum_qc_cells=50)

    assert default.policy_hash != changed.policy_hash


def test_module_effect_curated_module_supports_measured_association_not_mechanism(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    scope = _manifest_scope(registry, tmp_path)
    artifact = registry.register_module_effect(
        path=_write(tmp_path, "module_effect.json"),
        module_id="erythroid_module",
        module_name="Erythroid module",
        module_source="curated_gene_set",
        module_gene_set_hash="sha256:module-genes",
        scoring_method="scanpy_score_genes",
        effect_size=0.8,
        method="wilcoxon",
        padj=0.01,
        n_target_cells=120,
        n_control_cells=150,
        scope=scope,
        quality={"eligibility": _eligible()},
    )

    decision = resolve_claim(Claim(
        claim_id="module_as_mechanism",
        text="The erythroid module effect validates a KLF1 downstream mechanism.",
        subject={"id": "KLF1"},
        object={"type": "module", "id": "erythroid_module"},
        scope=scope,
        requested_strength=StrengthCeiling.validated_mechanism_disabled,
        evidence_refs=[artifact.artifact_id],
    ), registry)

    assert decision.max_strength == StrengthCeiling.measured_association
    assert decision.decision == ClaimDecisionState.allowed_with_downgrade
    surface = decision.allowed_surface.lower()
    assert "module association" in surface
    assert "downstream mechanism" in surface
    assert "validates" not in surface
    assert "master regulator" not in surface
    assert any("module-score association" in reason for reason in decision.reasons)


def test_module_effect_all_cell_derived_keeps_contamination_caveat(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    scope = _manifest_scope(registry, tmp_path)
    artifact = registry.register_module_effect(
        path=_write(tmp_path, "module_all_cells.json"),
        module_id="all_cell_module",
        module_source="all_cell_derived",
        module_gene_set_hash="sha256:all-cell-module",
        scoring_method="score_genes",
        effect_size=0.5,
        method="t-test",
        pvalue=0.02,
        n_target_cells=120,
        n_control_cells=150,
        scope=scope,
        quality={"eligibility": _eligible()},
    )

    decision = resolve_claim(Claim(
        claim_id="all_cell_module_effect",
        text="KLF1 changes an all-cell-derived module score.",
        subject={"id": "KLF1"},
        object={"type": "module", "id": "all_cell_module"},
        scope=scope,
        requested_strength=StrengthCeiling.measured_association,
        evidence_refs=[artifact.artifact_id],
    ), registry)

    assert decision.max_strength == StrengthCeiling.measured_association
    assert "perturbation-contamination" in decision.allowed_surface.lower()
    assert any("contamination caveat" in reason for reason in decision.reasons)


def test_module_effect_missing_stats_or_failed_cell_qc_downgrades(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    scope = _manifest_scope(registry, tmp_path)
    missing_stats = registry.register_module_effect(
        path=_write(tmp_path, "module_missing_stats.json"),
        module_id="erythroid_module",
        module_source="curated_gene_set",
        module_gene_set_hash="sha256:module-genes",
        scoring_method="score_genes",
        method="wilcoxon",
        n_target_cells=120,
        n_control_cells=150,
        scope=scope,
        quality={"eligibility": _eligible()},
    )

    missing_decision = resolve_claim(Claim(
        claim_id="module_missing_stats",
        text="KLF1 changes the module score.",
        subject={"id": "KLF1"},
        object={"type": "module", "id": "erythroid_module"},
        scope=scope,
        requested_strength=StrengthCeiling.measured_association,
        evidence_refs=[missing_stats.artifact_id],
    ), registry)
    assert missing_decision.max_strength == StrengthCeiling.observation
    assert any("lacks required execution metadata" in reason for reason in missing_decision.reasons)

    failed_qc = registry.register_module_effect(
        path=_write(tmp_path, "module_failed_qc.json"),
        module_id="qc_module",
        module_source="curated_gene_set",
        module_gene_set_hash="sha256:qc-module",
        scoring_method="score_genes",
        effect_size=0.7,
        method="wilcoxon",
        padj=0.01,
        n_target_cells=120,
        n_control_cells=150,
        scope=scope,
        quality={"eligibility": _eligible()},
    )
    registry.register_cell_qc(
        path=_write(tmp_path, "failed_module_cell_qc.json"),
        n_cells_after_qc=10,
        qc_policy="failed_qc",
        passed=False,
        scope=scope,
    )
    failed_qc_decision = resolve_claim(Claim(
        claim_id="module_failed_qc",
        text="KLF1 changes the module score.",
        subject={"id": "KLF1"},
        object={"type": "module", "id": "qc_module"},
        scope=scope,
        requested_strength=StrengthCeiling.measured_association,
        evidence_refs=[failed_qc.artifact_id],
    ), registry)
    assert failed_qc_decision.max_strength == StrengthCeiling.observation
    assert any("cell QC" in reason for reason in failed_qc_decision.reasons)


def test_global_effect_supports_global_response_not_gene_specific_claim(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    scope = _manifest_scope(registry, tmp_path)
    artifact = registry.register_global_effect(
        path=_write(tmp_path, "global_effect.json"),
        metric="energy_distance",
        feature_space="PCA",
        comparison_method="permutation_test",
        distance=0.42,
        null_model="label_permutation",
        padj=0.02,
        n_target_cells=120,
        n_control_cells=150,
        scope=scope,
        quality={"eligibility": _eligible()},
    )

    global_decision = resolve_claim(Claim(
        claim_id="global_response",
        text="KLF1 has a measured global perturbation response.",
        subject={"id": "KLF1"},
        object={"type": "cell_state", "id": "global_response"},
        scope=scope,
        requested_strength=StrengthCeiling.measured_association,
        evidence_refs=[artifact.artifact_id],
    ), registry)
    assert global_decision.max_strength == StrengthCeiling.measured_association
    assert "global perturbation response" in global_decision.allowed_surface.lower()
    assert "gene-specific" in global_decision.allowed_surface.lower()

    gene_decision = resolve_claim(Claim(
        claim_id="global_as_gene_de",
        text="The global distance proves gene-specific differential expression for GENE_X.",
        subject={"id": "KLF1"},
        relation="differential_expression",
        object={"type": "gene", "id": "GENE_X"},
        scope=scope,
        requested_strength=StrengthCeiling.measured_association,
        evidence_refs=[artifact.artifact_id],
    ), registry)
    assert gene_decision.max_strength == StrengthCeiling.observation
    assert any("gene-specific" in reason for reason in gene_decision.reasons)


def test_global_effect_causal_fate_claim_is_surface_limited(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    scope = _manifest_scope(registry, tmp_path)
    artifact = registry.register_global_effect(
        path=_write(tmp_path, "global_fate_effect.json"),
        metric="mmd",
        embedding="UMAP",
        comparison_method="permutation_test",
        effect_size=0.35,
        null_model="label_permutation",
        pvalue=0.005,
        n_target_cells=120,
        n_control_cells=150,
        scope=scope,
        quality={"eligibility": _eligible()},
    )

    decision = resolve_claim(Claim(
        claim_id="global_as_fate_causality",
        text="KLF1 causes a causal fate decision based on global shift.",
        subject={"id": "KLF1"},
        object={"type": "cell_fate", "id": "erythroid fate"},
        scope=scope,
        requested_strength=StrengthCeiling.validated_mechanism_disabled,
        evidence_refs=[artifact.artifact_id],
    ), registry)

    assert decision.max_strength == StrengthCeiling.measured_association
    assert decision.decision == ClaimDecisionState.allowed_with_downgrade
    surface = decision.allowed_surface.lower()
    assert "global perturbation response" in surface
    assert "causal cell-state transition" in surface
    assert "causes" not in surface
    assert "proves" not in surface


def test_p13_artifact_self_tags_cannot_upgrade_to_mechanism(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    scope = _manifest_scope(registry, tmp_path)
    artifact = registry.register_module_effect(
        path=_write(tmp_path, "module_self_tags.json"),
        module_id="erythroid_module",
        module_source="curated_gene_set",
        module_gene_set_hash="sha256:module-genes",
        scoring_method="score_genes",
        effect_size=1.0,
        method="wilcoxon",
        padj=0.001,
        n_target_cells=120,
        n_control_cells=150,
        scope=scope,
        quality={
            "eligibility": _eligible(),
            "evidence_class": "measured",
            "strength": "validated_mechanism",
            "validated_mechanism": True,
        },
    )

    decision = resolve_claim(Claim(
        claim_id="module_self_tags",
        text="Self-tagged module evidence validates a master regulator mechanism.",
        subject={"id": "KLF1"},
        object={"type": "module", "id": "erythroid_module"},
        scope=scope,
        requested_strength=StrengthCeiling.validated_mechanism_disabled,
        evidence_refs=[artifact.artifact_id],
    ), registry)

    assert artifact.effective_evidence_class.value == "measured"
    assert decision.max_strength == StrengthCeiling.measured_association
    assert decision.blocked_requested_strength == StrengthCeiling.validated_mechanism_disabled
    surface = decision.allowed_surface.lower()
    assert "validates" not in surface
    assert "master regulator" not in surface
