from __future__ import annotations

from pathlib import Path

from pertura_gate.core.policy import policy_for_profile
from pertura_gate.core.schema import Claim, StrengthCeiling
from pertura_gate.evidence.registry import EvidenceRegistry
from pertura_gate.identity.design_manifest import scope_for_raw_label
from pertura_gate.resolver.resolver import resolve_claim
from pertura_workflow.runners import run_pseudobulk_de_for_registered_contrast


def _write_inputs(tmp_path: Path) -> None:
    rows = ["cell_id,perturbation_uid,donor"]
    expr = ["cell_id,G1,G2"]
    for donor in ["d1", "d2"]:
        for i in range(12):
            cell = f"k_{donor}_{i}"
            rows.append(f"{cell},target:KLF1,{donor}")
            expr.append(f"{cell},{10 + i % 2},{2 + i % 2}")
        for i in range(12):
            cell = f"n_{donor}_{i}"
            rows.append(f"{cell},control:negative_control_pool,{donor}")
            expr.append(f"{cell},{2 + i % 2},{2 + i % 2}")
    (tmp_path / "metadata.csv").write_text("\n".join(rows) + "\n", encoding="utf-8")
    (tmp_path / "expression.csv").write_text("\n".join(expr) + "\n", encoding="utf-8")


def _registry(tmp_path: Path) -> EvidenceRegistry:
    (tmp_path / "outputs").mkdir(parents=True, exist_ok=True)
    return EvidenceRegistry(tmp_path / "artifacts" / "evidence_artifacts.jsonl")


def _scope(registry: EvidenceRegistry, tmp_path: Path) -> dict:
    manifest = registry.register_perturbation_design_manifest(
        path="outputs/design_manifest.json",
        dataset_id="fixture",
        raw_labels=["KLF1_NegCtrl0__KLF1_NegCtrl0"],
    )
    return scope_for_raw_label(manifest.metadata["manifest"], "KLF1_NegCtrl0__KLF1_NegCtrl0")


def _eligibility() -> dict:
    return {
        "perturbation_cell_mapping": {"assignment_method": "guide_count_threshold", "guide_to_target_map_hash": "sha256:guide-map"},
        "control_definition": {"negative_controls": ["NegCtrl"], "control_label": "NegCtrl"},
        "target_qc": {
            "n_target_cells": 24,
            "n_control_cells": 24,
            "guides_per_target": 2,
            "cells_per_guide": {"KLF1_g1": 12, "KLF1_g2": 12},
            "guide_consistency": "passed",
        },
        "cell_qc": {"n_cells_after_qc": 48, "qc_policy": "fixture"},
        "replicate_scope": {"replicate_axis": "donor", "n_replicates": 2},
        "assay_modality": "guide_based_perturb_seq",
        "perturbation_modality": "CRISPRi",
        "moi": "low",
        "estimand": "single_target_marginal",
    }


def test_pseudobulk_runner_writes_de_table_and_execution_ledger(tmp_path: Path) -> None:
    _write_inputs(tmp_path)

    result = run_pseudobulk_de_for_registered_contrast(
        tmp_path,
        expression_csv="expression.csv",
        metadata_csv="metadata.csv",
        contrast_uid="contrast:KLF1_vs_NTC",
        left_uid="target:KLF1",
        baseline_uid="control:negative_control_pool",
        replicate_column="donor",
        layer="normalized_counts",
    )

    assert Path(result["path"]).exists()
    assert result["method"] == "exploratory_normal_approximation"
    assert result["trust_level"] == "exploratory"
    assert result["execution_hash"].startswith("sha256:")
    assert Path(result["execution_ledger_path"]).exists()
    assert not (tmp_path / "artifacts" / "evidence_artifacts.jsonl").exists()


def test_pseudobulk_runner_rejects_missing_replicate_axis(tmp_path: Path) -> None:
    _write_inputs(tmp_path)
    try:
        run_pseudobulk_de_for_registered_contrast(
            tmp_path,
            expression_csv="expression.csv",
            metadata_csv="metadata.csv",
            contrast_uid="contrast:KLF1_vs_NTC",
            left_uid="target:KLF1",
            baseline_uid="control:negative_control_pool",
            replicate_column="",
            layer="normalized_counts",
        )
    except ValueError as exc:
        assert "replicate_column" in str(exc)
    else:
        raise AssertionError("missing replicate_column should be rejected")


def test_ledger_backed_legacy_approximation_cannot_pass_strict_measured_claim(tmp_path: Path) -> None:
    _write_inputs(tmp_path)
    result = run_pseudobulk_de_for_registered_contrast(
        tmp_path,
        expression_csv="expression.csv",
        metadata_csv="metadata.csv",
        contrast_uid="contrast:KLF1_vs_NTC",
        left_uid="target:KLF1",
        baseline_uid="control:negative_control_pool",
        replicate_column="donor",
        layer="normalized_counts",
    )
    registry = _registry(tmp_path)
    scope = _scope(registry, tmp_path)
    artifact = registry.register_measured_de(
        path=result["relative_path"],
        contrast_left="KLF1",
        contrast_baseline="NegCtrl",
        method=result["method"],
        n_left=result["n_left"],
        n_baseline=result["n_baseline"],
        multiple_testing=result["multiple_testing"],
        has_padj=result["has_padj"],
        source_data="fixture",
        scope=scope,
        eligibility=_eligibility(),
        execution_hash=result["execution_hash"],
        metadata={"execution_ledger_path": result["execution_ledger_path"]},
    )
    claim = Claim(
        claim_id="pseudobulk_claim",
        text="KLF1 has a measured association.",
        subject={"type": "perturbation", "id": "KLF1"},
        relation="measured_association",
        object={"type": "gene_set", "id": "erythroid"},
        scope=dict(artifact.scope),
        requested_strength=StrengthCeiling.measured_association,
        evidence_refs=[artifact.artifact_id],
    )

    decision = resolve_claim(claim, registry, policy=policy_for_profile("strict"))

    assert decision.max_strength != StrengthCeiling.measured_association
    assert any("trusted" in reason for reason in decision.reasons)
