from __future__ import annotations

from pathlib import Path

from pertura_gate.core.policy import DEFAULT_POLICY, GatePolicy, policy_for_profile
from pertura_gate.core.schema import Claim, StrengthCeiling
from pertura_gate.evidence.registry import EvidenceRegistry
from pertura_gate.identity.design_manifest import scope_for_raw_label
from pertura_gate.resolver.resolver import resolve_claim


def _registry(tmp_path: Path) -> EvidenceRegistry:
    (tmp_path / "outputs").mkdir(parents=True, exist_ok=True)
    return EvidenceRegistry(tmp_path / "artifacts" / "evidence_artifacts.jsonl")


def _write(tmp_path: Path, name: str, text: str = "x\n") -> str:
    path = tmp_path / "outputs" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return f"outputs/{name}"


def _scope(registry: EvidenceRegistry, tmp_path: Path) -> dict:
    manifest = registry.register_perturbation_design_manifest(
        path=_write(tmp_path, "design_manifest.json"),
        dataset_id="fixture",
        raw_labels=["KLF1_NegCtrl0__KLF1_NegCtrl0"],
    )
    return scope_for_raw_label(manifest.metadata["manifest"], "KLF1_NegCtrl0__KLF1_NegCtrl0")


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
            "cells_per_guide": {"KLF1_g1": 60, "KLF1_g2": 60},
            "guide_consistency": "passed",
        },
        "cell_qc": {"n_cells_after_qc": 270, "qc_policy": "fixture"},
        "replicate_scope": {"replicate_axis": "donor", "n_replicates": 2},
        "assay_modality": "guide_based_perturb_seq",
        "perturbation_modality": "CRISPRi",
        "moi": "low",
        "estimand": "single_target_marginal",
        "control_calibration": {
            "negative_control_status": "available",
            "ntc_vs_ntc_check": {"passed": True, "status": "passed"},
            "label_permutation_check": {"passed": True, "status": "passed"},
        },
    }
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(payload.get(key), dict):
            payload[key].update(value)
        else:
            payload[key] = value
    return payload


def _artifact(registry: EvidenceRegistry, tmp_path: Path, **kwargs):
    params = {
        "path": _write(tmp_path, kwargs.pop("filename", "de.csv")),
        "contrast_left": "KLF1",
        "contrast_baseline": "NegCtrl",
        "method": "sceptre",
        "n_left": 120,
        "n_baseline": 150,
        "multiple_testing": "BH",
        "has_padj": True,
        "source_data": "fixture",
        "scope": _scope(registry, tmp_path),
        "eligibility": _eligible(),
    }
    params.update(kwargs)
    return registry.register_measured_de(**params)


def _claim(artifact) -> Claim:
    return Claim(
        claim_id="claim",
        text="KLF1 has a measured association.",
        subject={"type": "perturbation", "id": "KLF1"},
        relation="measured_association",
        object={"type": "gene_set", "id": "erythroid"},
        scope=dict(artifact.scope),
        requested_strength=StrengthCeiling.measured_association,
        evidence_refs=[artifact.artifact_id],
    )


def test_default_policy_is_smoke_and_profile_hash_expands_fields() -> None:
    assert DEFAULT_POLICY == policy_for_profile("smoke")
    strict = policy_for_profile("strict")
    modified = GatePolicy(profile="strict", require_trusted_method_for_measured_claims=True)
    assert strict.policy_hash != DEFAULT_POLICY.policy_hash
    assert strict.policy_hash != modified.policy_hash
    assert strict.to_canonical_dict()["require_trusted_method_for_measured_claims"] is True


def test_same_artifact_passes_smoke_but_downgrades_in_strict_without_trusted_execution(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    artifact = _artifact(registry, tmp_path, execution_hash=None)

    smoke_decision = resolve_claim(_claim(artifact), registry, policy=policy_for_profile("smoke"))
    strict_decision = resolve_claim(_claim(artifact), registry, policy=policy_for_profile("strict"))

    assert smoke_decision.max_strength == StrengthCeiling.measured_association
    assert strict_decision.max_strength == StrengthCeiling.observation
    assert any("trusted runner provenance" in reason for reason in strict_decision.reasons)


def test_self_reported_trusted_method_requires_execution_hash(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    artifact = _artifact(registry, tmp_path, method="sceptre", execution_hash=None)
    decision = resolve_claim(_claim(artifact), registry, policy=policy_for_profile("strict"))
    assert decision.max_strength == StrengthCeiling.observation
    assert any("execution hash" in reason or "trusted runner provenance" in reason for reason in decision.reasons)


def test_trusted_method_with_execution_hash_and_replicates_passes_strict(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    artifact = _artifact(registry, tmp_path, method="sceptre", execution_hash="sha256:runner-execution")
    decision = resolve_claim(_claim(artifact), registry, policy=policy_for_profile("strict"))
    assert decision.max_strength == StrengthCeiling.measured_association


def test_method_internal_replicate_handling_requires_trusted_execution(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    artifact = _artifact(
        registry,
        tmp_path,
        execution_hash=None,
        eligibility=_eligible(replicate_scope={"replicate_handling": "method_internal"}),
    )
    decision = resolve_claim(_claim(artifact), registry, policy=policy_for_profile("strict"))
    assert decision.max_strength == StrengthCeiling.observation
    assert any("method-internal replicate handling" in reason for reason in decision.reasons)


def test_registered_batch_confounding_downgrades_strict_but_preflight_hint_alone_does_not(tmp_path: Path) -> None:
    clean_registry = _registry(tmp_path / "clean")
    clean_artifact = _artifact(clean_registry, tmp_path / "clean", execution_hash="sha256:runner-execution")
    clean_decision = resolve_claim(_claim(clean_artifact), clean_registry, policy=policy_for_profile("strict"))
    assert clean_decision.max_strength == StrengthCeiling.measured_association

    confounded_registry = _registry(tmp_path / "confounded")
    confounded = _artifact(
        confounded_registry,
        tmp_path / "confounded",
        execution_hash="sha256:runner-execution",
        eligibility=_eligible(replicate_scope={"replicate_axis": "donor", "n_replicates": 2, "confound_flag": True}),
    )
    confounded_decision = resolve_claim(_claim(confounded), confounded_registry, policy=policy_for_profile("strict"))
    assert confounded_decision.max_strength == StrengthCeiling.observation
    assert any("batch-perturbation confounding" in reason for reason in confounded_decision.reasons)


def test_failed_control_calibration_downgrades_and_paper_requires_null_checks(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    failed = _artifact(
        registry,
        tmp_path,
        execution_hash="sha256:runner-execution",
        eligibility=_eligible(control_calibration={"ntc_vs_ntc_check": {"passed": False}}),
    )
    strict_decision = resolve_claim(_claim(failed), registry, policy=policy_for_profile("strict"))
    assert strict_decision.max_strength == StrengthCeiling.observation
    assert any("failed ntc_vs_ntc_check" in reason for reason in strict_decision.reasons)

    paper_registry = _registry(tmp_path / "paper")
    missing_null = _artifact(
        paper_registry,
        tmp_path / "paper",
        execution_hash="sha256:runner-execution",
        eligibility=_eligible(control_calibration={"ntc_vs_ntc_check": None, "label_permutation_check": None}),
    )
    paper_decision = resolve_claim(_claim(missing_null), paper_registry, policy=policy_for_profile("paper"))
    assert paper_decision.max_strength == StrengthCeiling.observation
    assert any("policy requires ntc_vs_ntc_check" in reason for reason in paper_decision.reasons)
    assert any("policy requires label_permutation_check" in reason for reason in paper_decision.reasons)


def test_artifact_self_tags_do_not_create_trusted_execution(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    artifact = _artifact(
        registry,
        tmp_path,
        execution_hash=None,
        quality={"trusted": True, "validated_mechanism": True, "method": "sceptre"},
        metadata={"trusted": True},
    )
    decision = resolve_claim(_claim(artifact), registry, policy=policy_for_profile("strict"))
    assert decision.max_strength == StrengthCeiling.observation
    assert any("trusted runner provenance" in reason for reason in decision.reasons)


def test_composition_paper_profile_does_not_require_de_guide_power_metadata(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    scope = _scope(registry, tmp_path)
    eligibility = {
        "cell_qc": {"n_cells_after_qc": 400, "qc_policy": "fixture"},
        "replicate_scope": {"replicate_axis": "donor", "n_replicates": 2},
        "control_calibration": {
            "negative_control_status": "available",
            "ntc_vs_ntc_check": {"passed": True, "status": "passed"},
            "label_permutation_check": {"passed": True, "status": "passed"},
        },
    }
    artifact = registry.register_composition_effect(
        path=_write(tmp_path, "composition_effect.json"),
        state_source="cell_state_reference_abc",
        state_assignment_column="state_label",
        comparison_method="fisher_exact",
        state_counts_by_condition={
            "KLF1": {"state_a": 140, "state_b": 60},
            "negative_control_pool": {"state_a": 80, "state_b": 120},
        },
        state_level_deltas={"state_a": {"delta_proportion": 0.3}},
        effect_size=0.296,
        padj=0.001,
        n_target_cells=200,
        n_control_cells=200,
        scope=scope,
        quality={"eligibility": eligibility},
        execution_hash="sha256:composition-runner",
    )
    claim = Claim(
        claim_id="composition_paper_without_guide_power",
        text="KLF1 is associated with a measured cell-state composition shift.",
        subject={"type": "perturbation", "id": "KLF1"},
        object={"type": "cell_state", "id": "state_a"},
        scope=artifact.scope,
        requested_strength=StrengthCeiling.measured_association,
        evidence_refs=[artifact.artifact_id],
    )

    decision = resolve_claim(claim, registry, policy=policy_for_profile("paper"))

    assert decision.max_strength == StrengthCeiling.measured_association
    assert not any("guide count" in reason or "cells-per-guide" in reason for reason in decision.reasons)
