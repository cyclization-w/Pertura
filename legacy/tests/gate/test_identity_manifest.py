from __future__ import annotations

from pathlib import Path

from pertura_gate.identity.design_manifest import (
    build_guide_label_manifest,
    build_treatment_condition_manifest,
    scope_for_raw_label,
    target_uid,
)
from pertura_gate.evidence.registry import EvidenceRegistry
from pertura_gate.resolver.resolver import resolve_claim
from pertura_gate.core.schema import Claim, ScopeFit, StrengthCeiling
from pertura_gate.identity.scope import compare_scope


def _registry(tmp_path: Path) -> EvidenceRegistry:
    (tmp_path / "outputs").mkdir(exist_ok=True)
    return EvidenceRegistry(tmp_path / "artifacts" / "evidence_artifacts.jsonl")


def _write(tmp_path: Path, name: str, text: str = "x\n") -> str:
    path = tmp_path / "outputs" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return f"outputs/{name}"


def _eligible():
    return {
        "perturbation_cell_mapping": {
            "assignment_method": "guide_count_threshold",
            "guide_to_target_map_hash": "sha256:guide-map",
        },
        "control_definition": {"negative_controls": ["NegCtrl"], "control_label": "NegCtrl"},
        "target_qc": {"n_target_cells": 120, "n_control_cells": 150},
        "assay_modality": "guide_based_perturb_seq",
        "perturbation_modality": "CRISPRa",
        "moi": "low",
        "estimand": "single_target_marginal",
    }


def test_guide_label_adapter_maps_norman_single_and_control() -> None:
    manifest = build_guide_label_manifest(
        manifest_id="manifest_1",
        dataset_id="GSE133344",
        raw_labels=["KLF1_NegCtrl0__KLF1_NegCtrl0", "NegCtrl0_NegCtrl0__NegCtrl0_NegCtrl0"],
    )

    payload = manifest.to_dict()
    klf1_scope = scope_for_raw_label(payload, "KLF1_NegCtrl0__KLF1_NegCtrl0")
    ctrl_scope = scope_for_raw_label(payload, "NegCtrl0_NegCtrl0__NegCtrl0_NegCtrl0")

    assert klf1_scope["perturbation_uid"] == "target:KLF1"
    assert klf1_scope["control_uid"] == "control:negative_control_pool"
    assert payload["raw_label_index"]["NegCtrl0_NegCtrl0__NegCtrl0_NegCtrl0"] == "control:negative_control_pool"
    assert ctrl_scope["perturbation_uid"] == "control:negative_control_pool"


def test_guide_label_adapter_keeps_combinatorial_distinct_from_single() -> None:
    manifest = build_guide_label_manifest(
        manifest_id="manifest_1",
        raw_labels=["CEBPE_RUNX1T1__CEBPE_RUNX1T1"],
    )
    combo_scope = scope_for_raw_label(manifest.to_dict(), "CEBPE_RUNX1T1__CEBPE_RUNX1T1")
    single_scope = {"design_manifest_id": "manifest_1", "perturbation_uid": target_uid("CEBPE"), "perturbation_kind": "single"}

    assert combo_scope["perturbation_uid"] == "combo:CEBPE+RUNX1T1"
    assert compare_scope(single_scope, combo_scope) == ScopeFit.mismatch


def test_whole_genome_guide_map_can_aggregate_guides_to_target() -> None:
    manifest = build_guide_label_manifest(
        manifest_id="manifest_1",
        raw_labels=["sgKLF1_1", "sgKLF1_2"],
        guide_to_target_map={"sgKLF1_1": "KLF1", "sgKLF1_2": "KLF1"},
    )

    assert scope_for_raw_label(manifest.to_dict(), "sgKLF1_1")["perturbation_uid"] == "target:KLF1"
    assert scope_for_raw_label(manifest.to_dict(), "sgKLF1_2")["perturbation_uid"] == "target:KLF1"
    assert "contrast_uid" not in scope_for_raw_label(manifest.to_dict(), "sgKLF1_1")


def test_control_aliases_map_to_negative_control_pool_and_enable_default_contrast() -> None:
    manifest = build_guide_label_manifest(
        manifest_id="manifest_1",
        raw_labels=["KLF1_sg1", "non-targeting_ctrl1", "NTC_1", "NegCtrl0", "safe-targeting_2"],
        guide_to_target_map={"KLF1_sg1": "KLF1"},
    )

    payload = manifest.to_dict()
    for raw_label in ["non-targeting_ctrl1", "NTC_1", "NegCtrl0", "safe-targeting_2"]:
        assert payload["raw_label_index"][raw_label] == "control:negative_control_pool"
    scope = scope_for_raw_label(payload, "KLF1_sg1")
    assert scope["perturbation_uid"] == "target:KLF1"
    assert scope["control_uid"] == "control:negative_control_pool"
    assert scope["contrast_uid"] == "contrast:target:KLF1:vs:control:negative_control_pool"


def test_guide_map_negative_control_target_is_control_not_combo() -> None:
    manifest = build_guide_label_manifest(
        manifest_id="manifest_1",
        raw_labels=["KLF1_NegCtrl0__KLF1_NegCtrl0", "NegCtrl0_NegCtrl0__NegCtrl0_NegCtrl0"],
        guide_to_target_map={"NegCtrl0": "negative_control"},
    )

    scope = scope_for_raw_label(manifest.to_dict(), "KLF1_NegCtrl0__KLF1_NegCtrl0")

    assert scope["perturbation_uid"] == "target:KLF1"
    assert scope["control_uid"] == "control:negative_control_pool"
    assert scope["perturbation_kind"] == "single"

def test_treatment_adapter_builds_vehicle_contrast_and_requires_vehicle() -> None:
    manifest = build_treatment_condition_manifest(
        manifest_id="drug_manifest",
        conditions=["drugA 10uM 24h", "DMSO"],
    )
    no_vehicle = build_treatment_condition_manifest(
        manifest_id="drug_manifest_2",
        conditions=["drugA 10uM 24h"],
    )

    payload = manifest.to_dict()
    drug_scope = scope_for_raw_label(payload, "drugA 10uM 24h")
    assert drug_scope["control_uid"] == "control:vehicle:dmso"
    assert drug_scope["contrast_uid"].startswith("contrast:treatment:druga")
    assert no_vehicle.to_dict()["contrasts"] == {}


def test_manifest_uid_scope_supports_measured_association(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    manifest_artifact = registry.register_perturbation_design_manifest(
        path=_write(tmp_path, "manifest.json"),
        dataset_id="GSE133344",
        raw_labels=["KLF1_NegCtrl0__KLF1_NegCtrl0"],
    )
    scope = scope_for_raw_label(manifest_artifact.metadata["manifest"], "KLF1_NegCtrl0__KLF1_NegCtrl0")
    artifact = registry.register_measured_de(
        path=_write(tmp_path, "de.csv"),
        contrast_left="KLF1",
        contrast_baseline="NegCtrl",
        method="wilcoxon",
        n_left=120,
        n_baseline=150,
        multiple_testing="BH",
        has_padj=True,
        scope=scope,
        eligibility=_eligible(),
    )
    claim = Claim(
        claim_id="uid_claim",
        text="KLF1 has a measured association.",
        subject={"id": "KLF1"},
        scope=scope,
        requested_strength=StrengthCeiling.measured_association,
        evidence_refs=[artifact.artifact_id],
    )

    decision = resolve_claim(claim, registry)

    assert decision.max_strength == StrengthCeiling.measured_association
    assert decision.scope_fit == ScopeFit.exact


def test_manifest_path_ref_resolves_to_registered_manifest_uid_scope(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    manifest = registry.register_perturbation_design_manifest(
        path=_write(tmp_path, "perturbation_design_manifest.json"),
        dataset_id="GSE133344",
        raw_labels=["KLF1_NegCtrl0__KLF1_NegCtrl0", "NegCtrl0_NegCtrl0__NegCtrl0_NegCtrl0"],
        guide_to_target_map={"NegCtrl0": "negative_control"},
    )

    scope = registry.resolve_manifest_scope(
        {
            "design_manifest_id": "outputs/perturbation_design_manifest.json",
            "raw_label": "KLF1_NegCtrl0__KLF1_NegCtrl0",
            "estimand": "single_target_marginal",
        }
    )

    assert scope["design_manifest_id"] == manifest.artifact_id
    assert scope["perturbation_uid"] == "target:KLF1"
    assert scope["control_uid"] == "control:negative_control_pool"
    assert scope["contrast_uid"] == "contrast:target:KLF1:vs:control:negative_control_pool"


def test_manifest_basename_ref_does_not_resolve_to_uid_scope(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    registry.register_perturbation_design_manifest(
        path=_write(tmp_path, "perturbation_design_manifest.json"),
        dataset_id="GSE133344",
        raw_labels=["KLF1_NegCtrl0__KLF1_NegCtrl0"],
    )

    scope = registry.resolve_manifest_scope(
        {
            "design_manifest_id": "perturbation_design_manifest.json",
            "raw_label": "KLF1_NegCtrl0__KLF1_NegCtrl0",
        }
    )

    assert scope["design_manifest_id"] == "perturbation_design_manifest.json"
    assert "perturbation_uid" not in scope
    assert "contrast_uid" not in scope

def test_register_measured_de_autolinks_manifest_path_and_contrast_label(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    manifest = registry.register_perturbation_design_manifest(
        path=_write(tmp_path, "perturbation_design_manifest.json"),
        dataset_id="GSE133344",
        raw_labels=["KLF1_NegCtrl0__KLF1_NegCtrl0", "NegCtrl0_NegCtrl0__NegCtrl0_NegCtrl0"],
        guide_to_target_map={"NegCtrl0": "negative_control"},
    )
    artifact = registry.register_measured_de(
        path=_write(tmp_path, "de.csv"),
        contrast_left="KLF1_NegCtrl0__KLF1_NegCtrl0",
        contrast_baseline="NegCtrl pool",
        method="wilcoxon",
        n_left=120,
        n_baseline=150,
        multiple_testing="BH",
        has_padj=True,
        scope={"design_manifest_id": "outputs/perturbation_design_manifest.json"},
        eligibility=_eligible(),
    )
    claim = Claim(
        claim_id="autolinked_claim",
        text="KLF1 has a measured association.",
        subject={"id": "KLF1"},
        scope=artifact.scope,
        requested_strength=StrengthCeiling.measured_association,
        evidence_refs=[artifact.artifact_id],
    )

    decision = resolve_claim(claim, registry)

    assert artifact.scope["design_manifest_id"] == manifest.artifact_id
    assert artifact.scope["perturbation_uid"] == "target:KLF1"
    assert artifact.scope["control_uid"] == "control:negative_control_pool"
    assert decision.max_strength == StrengthCeiling.measured_association
    assert decision.scope_fit == ScopeFit.exact


def test_raw_contrast_uid_with_manifest_cannot_upgrade_scope(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    manifest = registry.register_perturbation_design_manifest(
        path=_write(tmp_path, "perturbation_design_manifest.json"),
        dataset_id="GSE133344",
        raw_labels=["KLF1_NegCtrl0__KLF1_NegCtrl0"],
    )
    raw_scope = {
        "design_manifest_id": manifest.artifact_id,
        "contrast_uid": "KLF1_NegCtrl0__KLF1_NegCtrl0_vs_NegCtrl0_NegCtrl0__NegCtrl0_NegCtrl0",
        "estimand": "single_target_marginal",
    }
    artifact = registry.register_measured_de(
        path=_write(tmp_path, "de.csv"),
        contrast_left="KLF1_NegCtrl0__KLF1_NegCtrl0",
        contrast_baseline="NegCtrl pool",
        method="wilcoxon",
        n_left=120,
        n_baseline=150,
        multiple_testing="BH",
        has_padj=True,
        scope=raw_scope,
        eligibility=_eligible(),
    )
    claim = Claim(
        claim_id="raw_contrast_uid",
        text="KLF1 has a measured association.",
        subject={"id": "KLF1"},
        scope=raw_scope,
        requested_strength=StrengthCeiling.measured_association,
        evidence_refs=[artifact.artifact_id],
    )

    decision = resolve_claim(claim, registry)

    assert artifact.scope["manifest_uid_validation_error"] == ["contrast_uid"]
    assert decision.max_strength != StrengthCeiling.measured_association
    assert decision.scope_fit == ScopeFit.mismatch

def test_manifest_path_without_uid_or_raw_label_cannot_upgrade_scope(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    registry.register_perturbation_design_manifest(
        path=_write(tmp_path, "perturbation_design_manifest.json"),
        dataset_id="GSE133344",
        raw_labels=["KLF1_NegCtrl0__KLF1_NegCtrl0"],
    )
    artifact = registry.register_measured_de(
        path=_write(tmp_path, "de.csv"),
        contrast_left="KLF1_NegCtrl0__KLF1_NegCtrl0",
        contrast_baseline="NegCtrl pool",
        method="wilcoxon",
        n_left=120,
        n_baseline=150,
        multiple_testing="BH",
        has_padj=True,
        scope={"design_manifest_id": "outputs/perturbation_design_manifest.json"},
        eligibility=_eligible(),
    )
    claim = Claim(
        claim_id="manifest_path_only",
        text="KLF1 has a measured association.",
        subject={"id": "KLF1"},
        scope={"design_manifest_id": "outputs/perturbation_design_manifest.json", "estimand": "single_target_marginal"},
        requested_strength=StrengthCeiling.measured_association,
        evidence_refs=[artifact.artifact_id],
    )

    decision = resolve_claim(claim, registry)

    assert decision.max_strength != StrengthCeiling.measured_association
    assert decision.scope_fit == ScopeFit.unknown

def test_raw_string_scope_no_longer_upgrades_to_measured_association(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    artifact = registry.register_measured_de(
        path=_write(tmp_path, "de.csv"),
        contrast_left="KLF1",
        contrast_baseline="NegCtrl",
        method="wilcoxon",
        n_left=120,
        n_baseline=150,
        multiple_testing="BH",
        has_padj=True,
        scope={"perturbation": "KLF1", "control": "NegCtrl"},
        eligibility=_eligible(),
    )
    claim = Claim(
        claim_id="raw_claim",
        text="KLF1 has a measured association.",
        subject={"id": "KLF1"},
        scope={"perturbation": "KLF1", "control": "NegCtrl"},
        requested_strength=StrengthCeiling.measured_association,
        evidence_refs=[artifact.artifact_id],
    )

    decision = resolve_claim(claim, registry)

    assert decision.max_strength == StrengthCeiling.observation
    assert any("PerturbationDesignManifest UID" in reason for reason in decision.reasons)


def test_evidence_refs_do_not_resolve_by_basename(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    artifact = registry.register_predicted_effect(
        path=_write(tmp_path, "prediction.csv"),
        model_name="toy",
        perturbation="KLF1",
    )
    decision = resolve_claim(
        Claim(
            claim_id="basename_ref",
            text="KLF1 was predicted.",
            scope={"perturbation": "KLF1"},
            requested_strength=StrengthCeiling.predicted_effect,
            evidence_refs=[Path(artifact.path).name],
        ),
        registry,
    )

    assert decision.max_strength == StrengthCeiling.unsupported
    assert decision.missing_artifacts == ["prediction.csv"]


def test_registry_canonicalizes_focal_uid_scope_alias_for_observation_artifact(tmp_path: Path) -> None:
    from pertura_gate.evidence.registry import EvidenceRegistry
    from pertura_gate.resolver.resolver import resolve_claim
    from pertura_gate.core.schema import Claim, ScopeFit, StrengthCeiling

    outputs = tmp_path / "outputs"
    outputs.mkdir()
    (outputs / "guide.json").write_text("{}\n", encoding="utf-8")
    registry = EvidenceRegistry(tmp_path / "artifacts" / "evidence_artifacts.jsonl")
    manifest = registry.register_perturbation_design_manifest(
        path="outputs/guide.json",
        dataset_id="GSE133344",
        raw_labels=["CEBPE_RUNX1T1__CEBPE_RUNX1T1", "NegCtrl0_NegCtrl0__NegCtrl0_NegCtrl0"],
    )
    artifact = registry.register_guide_assignment(
        path="outputs/guide.json",
        assignment_method="local guide_identity metadata parsing",
        assigned_count=100,
        unassigned_count=0,
        multi_guide_count=20,
        scope={
            "design_manifest_id": manifest.artifact_id,
            "focal_perturbation_uid": "combo:CEBPE+RUNX1T1",
            "dataset_id": "GSE133344",
        },
    )

    combo_decision = resolve_claim(
        Claim(
            claim_id="combo_observed",
            text="The CEBPE_RUNX1T1 combinatorial guide identity is observed.",
            scope={
                "design_manifest_id": manifest.artifact_id,
                "perturbation_uid": "combo:CEBPE+RUNX1T1",
                "control_uid": "control:negative_control_pool",
                "estimand": "combinatorial",
            },
            requested_strength=StrengthCeiling.observation,
            evidence_refs=[artifact.artifact_id],
        ),
        registry,
    )
    single_decision = resolve_claim(
        Claim(
            claim_id="single_from_combo",
            text="CEBPE alone validates a mechanism.",
            scope={
                "design_manifest_id": manifest.artifact_id,
                "perturbation_uid": "target:CEBPE",
                "control_uid": "control:negative_control_pool",
                "estimand": "single_target_marginal",
            },
            requested_strength=StrengthCeiling.validated_mechanism_disabled,
            evidence_refs=[artifact.artifact_id],
        ),
        registry,
    )

    assert artifact.scope["perturbation_uid"] == "combo:CEBPE+RUNX1T1"
    assert combo_decision.max_strength == StrengthCeiling.observation
    assert combo_decision.scope_fit in {ScopeFit.exact, ScopeFit.compatible}
    assert single_decision.max_strength == StrengthCeiling.unsupported
    assert single_decision.scope_fit == ScopeFit.mismatch
