from __future__ import annotations

from pathlib import Path

from pertura_gate.core.policy import policy_for_profile
from pertura_gate.core.schema import Claim, StrengthCeiling
from pertura_gate.evidence.execution_ledger import file_sha256
from pertura_gate.evidence.registry import EvidenceRegistry
from pertura_gate.identity.design_manifest import scope_for_raw_label
from pertura_gate.resolver.resolver import resolve_claim
from pertura_workflow.trusted_run import record_trusted_run


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


def _eligibility_without_calibration() -> dict:
    return {
        "perturbation_cell_mapping": {"assignment_method": "guide_count_threshold", "guide_to_target_map_hash": "sha256:guide-map"},
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
    }




def _trusted_metadata(tmp_path: Path, execution_hash: str | None, method: str | None, artifact_path: str | Path | None = None) -> dict:
    if not execution_hash or not method or artifact_path is None:
        return {}
    output_path = Path(artifact_path)
    if not output_path.is_absolute():
        output_path = tmp_path / output_path
    record = record_trusted_run(
        tmp_path,
        execution_hash=execution_hash,
        runner_name="test_runner",
        runner_version="test_runner_v1",
        method=method,
        input_hashes={"input": "sha256:test-input"},
        output_hashes={"artifact": file_sha256(output_path)},
    )
    return {"execution_ledger_path": record["execution_ledger_path"]}

def _measured_artifact(registry: EvidenceRegistry, tmp_path: Path, *, scope: dict | None = None):
    artifact_path = _write(tmp_path, "de.csv", "gene,padj\nA,0.01\n")
    return registry.register_measured_de(
        path=artifact_path,
        contrast_left="KLF1",
        contrast_baseline="NegCtrl",
        method="sceptre",
        n_left=120,
        n_baseline=150,
        multiple_testing="BH",
        has_padj=True,
        source_data="fixture",
        scope=scope or _scope(registry, tmp_path),
        eligibility=_eligibility_without_calibration(),
        execution_hash="sha256:trusted-runner",
        metadata=_trusted_metadata(tmp_path, "sha256:trusted-runner", "sceptre", artifact_path),
    )


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


def _register_calibration(
    registry: EvidenceRegistry,
    tmp_path: Path,
    *,
    scope: dict,
    ntc_passed: bool = True,
    permutation_passed: bool = True,
    method: str = "basic_control_calibration_v1",
    execution_hash: str | None = "sha256:calibration-runner",
):
    artifact_path = _write(tmp_path, f"calibration_{ntc_passed}_{permutation_passed}.json", "{}\n")
    metadata = _trusted_metadata(tmp_path, execution_hash, method, artifact_path)
    return registry.register_control_calibration(
        path=artifact_path,
        calibration_type="control_null_checks",
        scope=scope,
        ntc_vs_ntc_check={"passed": ntc_passed, "status": "passed" if ntc_passed else "failed"},
        label_permutation_check={"passed": permutation_passed, "status": "passed" if permutation_passed else "failed"},
        alpha=0.05,
        n_features_tested=100,
        n_significant=0 if ntc_passed and permutation_passed else 5,
        method=method,
        execution_hash=execution_hash,
        metadata=metadata,
    )


def test_registered_passed_control_calibration_allows_paper_measured_claim(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    artifact = _measured_artifact(registry, tmp_path)
    _register_calibration(registry, tmp_path, scope=artifact.scope)

    decision = resolve_claim(_claim(artifact), registry, policy=policy_for_profile("paper"))

    assert decision.max_strength == StrengthCeiling.measured_association
    assert any("control_calibration" in source or "EligibilityProfile satisfied" in reason for reason in decision.reasons for source in [reason])


def test_fake_passed_control_calibration_without_trusted_provenance_does_not_satisfy_paper(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    artifact = _measured_artifact(registry, tmp_path)
    _register_calibration(
        registry,
        tmp_path,
        scope=artifact.scope,
        method="claude_hand_wrote_this",
        execution_hash=None,
    )

    decision = resolve_claim(_claim(artifact), registry, policy=policy_for_profile("paper"))

    assert decision.max_strength == StrengthCeiling.observation
    assert any("trusted control calibration provenance" in reason for reason in decision.reasons)


def test_failed_ntc_vs_ntc_calibration_downgrades_under_strict(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    artifact = _measured_artifact(registry, tmp_path)
    _register_calibration(registry, tmp_path, scope=artifact.scope, ntc_passed=False)

    decision = resolve_claim(_claim(artifact), registry, policy=policy_for_profile("strict"))

    assert decision.max_strength == StrengthCeiling.observation
    assert any("failed ntc_vs_ntc_check" in reason for reason in decision.reasons)


def test_failed_label_permutation_calibration_downgrades_under_strict(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    artifact = _measured_artifact(registry, tmp_path)
    _register_calibration(registry, tmp_path, scope=artifact.scope, permutation_passed=False)

    decision = resolve_claim(_claim(artifact), registry, policy=policy_for_profile("strict"))

    assert decision.max_strength == StrengthCeiling.observation
    assert any("failed label_permutation_check" in reason for reason in decision.reasons)


def test_mismatched_scope_calibration_is_ignored_under_paper(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    artifact = _measured_artifact(registry, tmp_path)
    _register_calibration(registry, tmp_path, scope={"design_manifest_id": "different_manifest", "perturbation_uid": "target:OTHER"})

    decision = resolve_claim(_claim(artifact), registry, policy=policy_for_profile("paper"))

    assert decision.max_strength == StrengthCeiling.observation
    assert any("policy requires ntc_vs_ntc_check" in reason for reason in decision.reasons)
    assert any("policy requires label_permutation_check" in reason for reason in decision.reasons)


def test_control_calibration_artifact_alone_cannot_support_effect_claim(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    scope = _scope(registry, tmp_path)
    calibration = _register_calibration(registry, tmp_path, scope=scope)
    claim = Claim(
        claim_id="calibration_as_effect",
        text="The calibration proves KLF1 has a measured effect.",
        subject={"type": "perturbation", "id": "KLF1"},
        scope=scope,
        requested_strength=StrengthCeiling.measured_association,
        evidence_refs=[calibration.artifact_id],
    )

    decision = resolve_claim(claim, registry, policy=policy_for_profile("paper"))

    assert decision.max_strength == StrengthCeiling.observation
    assert all("measured association" not in decision.allowed_surface.lower() for _ in [0])
