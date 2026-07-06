from __future__ import annotations

import json
from pathlib import Path

from pertura_workflow.recipes import run_classic_perturbseq


def test_classic_recipe_skeleton_produces_partial_success_report(tmp_path: Path) -> None:
    (tmp_path / "klf1_vs_negctrl_de.csv").write_text("gene,logfc,padj\nKLF1,-1,0.01\n", encoding="utf-8")
    (tmp_path / "guide_to_target_map.csv").write_text("guide,target\nsgKLF1,KLF1\n", encoding="utf-8")

    result = run_classic_perturbseq(tmp_path)

    assert result.recipe_name == "classic_perturbseq"
    assert result.harvest.registered_artifact_ids == []
    assert result.candidate_claims
    assert "partial_success" in result.report_markdown
    assert "Candidate claims are not scientific conclusions" in result.report_markdown
    assert result.workflow_run_manifest is not None
    assert result.workflow_run_manifest.workflow_run_hash.startswith("sha256:")


def test_workflow_cli_recipe_classic_outputs_json(tmp_path: Path) -> None:
    from pertura_workflow.cli import main

    (tmp_path / "klf1_vs_negctrl_de.csv").write_text("gene,logfc,padj\nKLF1,-1,0.01\n", encoding="utf-8")
    out = tmp_path / "classic_recipe.json"
    manifest = tmp_path / "classic_workflow_run.json"

    status = main([
        "recipe",
        "classic",
        str(tmp_path),
        "--format",
        "json",
        "--out",
        str(out),
        "--run-manifest",
        str(manifest),
    ])

    assert status == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    run_manifest = json.loads(manifest.read_text(encoding="utf-8"))
    assert payload["recipe_name"] == "classic_perturbseq"
    assert payload["candidate_claims"]
    assert run_manifest["command"] == "recipe classic"


def test_classic_recipe_configured_path_renders_claim_decision(tmp_path: Path) -> None:
    (tmp_path / "klf1_de.csv").write_text("gene,logfc,padj\nKLF1,-1,0.01\n", encoding="utf-8")
    config = {
        "dataset_id": "synthetic_norman",
        "raw_labels": ["KLF1_NegCtrl0__KLF1_NegCtrl0", "NegCtrl0_NegCtrl0__NegCtrl0_NegCtrl0"],
        "perturbation_raw_label": "KLF1_NegCtrl0__KLF1_NegCtrl0",
        "controls": {"negative_controls": ["NegCtrl0"]},
        "experiment_design": {
            "assay": "Perturb-seq",
            "perturbation_modality": "guide_based_perturb_seq",
            "moi": "low",
            "controls": {"negative_controls": ["NegCtrl0"]},
        },
        "guide_assignment": {
            "assignment_method": "synthetic_guide_calling",
            "assigned_count": 40,
            "unassigned_count": 0,
            "multi_guide_count": 0,
            "target_summary": {"KLF1": 20, "NegCtrl0": 20},
        },
        "target_qc": {
            "target": "KLF1",
            "control": "NegCtrl0",
            "n_target_cells": 20,
            "n_control_cells": 20,
            "guides_per_target": 1,
            "guide_consistency": "single_guide_synthetic",
        },
        "cell_qc": {"n_cells_after_qc": 40, "qc_policy": "synthetic", "passed": True},
        "measured_de": {
            "path": "klf1_de.csv",
            "contrast_left": "KLF1_NegCtrl0__KLF1_NegCtrl0",
            "contrast_baseline": "NegCtrl0_NegCtrl0__NegCtrl0_NegCtrl0",
            "method": "synthetic_wilcoxon",
            "n_left": 20,
            "n_baseline": 20,
            "multiple_testing": "BH",
            "has_padj": True,
        },
        "claim": {
            "claim_id": "classic_klf1_mechanism_overclaim",
            "text": "KLF1 validates an erythroid mechanism in this Perturb-seq experiment.",
            "requested_strength": "validated_mechanism_disabled",
        },
    }
    (tmp_path / "classic_recipe_config.json").write_text(json.dumps(config), encoding="utf-8")

    result = run_classic_perturbseq(tmp_path)

    assert result.harvest.registered_artifact_ids
    assert result.decision_ids
    assert "Claim `classic_klf1_mechanism_overclaim`" in result.report_markdown
    assert "Claim strength ceiling: `measured_association`" in result.report_markdown
    assert "no registered replication" in result.report_markdown
    assert (tmp_path / "artifacts" / "evidence_artifacts.jsonl").exists()
    assert (tmp_path / "artifacts" / "claim_decisions.json").exists()
    assert (tmp_path / "reports" / "evidence_report.md").exists()

def test_classic_recipe_can_run_basic_de_for_registered_contrast(tmp_path: Path) -> None:
    (tmp_path / "expression.csv").write_text(
        "cell_id,KLF1,GYPA\n"
        "c1,10,6\n"
        "c2,12,5\n"
        "c3,2,1\n"
        "c4,1,2\n",
        encoding="utf-8",
    )
    (tmp_path / "metadata.csv").write_text(
        "cell_id,perturbation_uid\n"
        "c1,target:KLF1\n"
        "c2,target:KLF1\n"
        "c3,control:negative_control_pool\n"
        "c4,control:negative_control_pool\n",
        encoding="utf-8",
    )
    config = {
        "dataset_id": "synthetic_norman_basic_de",
        "raw_labels": ["KLF1_NegCtrl0__KLF1_NegCtrl0", "NegCtrl0_NegCtrl0__NegCtrl0_NegCtrl0"],
        "perturbation_raw_label": "KLF1_NegCtrl0__KLF1_NegCtrl0",
        "controls": {"negative_controls": ["NegCtrl0"]},
        "experiment_design": {
            "assay": "Perturb-seq",
            "perturbation_modality": "guide_based_perturb_seq",
            "moi": "low",
            "controls": {"negative_controls": ["NegCtrl0"]},
        },
        "guide_assignment": {
            "assignment_method": "synthetic_guide_calling",
            "assigned_count": 4,
            "unassigned_count": 0,
            "multi_guide_count": 0,
            "target_summary": {"KLF1": 2, "NegCtrl0": 2},
        },
        "target_qc": {
            "target": "KLF1",
            "control": "NegCtrl0",
            "n_target_cells": 2,
            "n_control_cells": 2,
            "guides_per_target": 1,
            "guide_consistency": "single_guide_synthetic",
        },
        "basic_de": {
            "expression_csv": "expression.csv",
            "metadata_csv": "metadata.csv",
            "layer": "normalized_counts",
            "condition_column": "perturbation_uid",
        },
        "claim": {
            "claim_id": "classic_basic_de_mechanism_overclaim",
            "text": "KLF1 validates an erythroid mechanism in this Perturb-seq experiment.",
            "requested_strength": "validated_mechanism_disabled",
        },
    }
    (tmp_path / "classic_recipe_config.json").write_text(json.dumps(config), encoding="utf-8")

    result = run_classic_perturbseq(tmp_path)

    assert (tmp_path / "outputs").exists()
    assert any(step.name == "run_basic_de_for_registered_contrast" for step in result.workflow_run_manifest.steps)
    assert "classic_basic_de_mechanism_overclaim" in result.report_markdown
    assert "Claim strength ceiling: `measured_association`" in result.report_markdown
    assert "no registered replication" in result.report_markdown

def test_classic_recipe_can_run_basic_target_qc_before_basic_de(tmp_path: Path) -> None:
    (tmp_path / "expression.csv").write_text(
        "cell_id,KLF1,GYPA\n"
        "c1,10,6\n"
        "c2,12,5\n"
        "c3,2,1\n"
        "c4,1,2\n",
        encoding="utf-8",
    )
    (tmp_path / "metadata.csv").write_text(
        "cell_id,perturbation_uid,guide\n"
        "c1,target:KLF1,sgKLF1_1\n"
        "c2,target:KLF1,sgKLF1_2\n"
        "c3,control:negative_control_pool,NegCtrl0\n"
        "c4,control:negative_control_pool,NegCtrl1\n",
        encoding="utf-8",
    )
    (tmp_path / "guide_map.csv").write_text(
        "guide,target\nsgKLF1_1,KLF1\nsgKLF1_2,KLF1\nNegCtrl0,negative_control\nNegCtrl1,negative_control\n",
        encoding="utf-8",
    )
    config = {
        "dataset_id": "synthetic_norman_basic_qc_de",
        "raw_labels": ["KLF1_NegCtrl0__KLF1_NegCtrl0", "NegCtrl0_NegCtrl0__NegCtrl0_NegCtrl0"],
        "perturbation_raw_label": "KLF1_NegCtrl0__KLF1_NegCtrl0",
        "controls": {"negative_controls": ["NegCtrl0", "NegCtrl1"]},
        "experiment_design": {
            "assay": "Perturb-seq",
            "perturbation_modality": "guide_based_perturb_seq",
            "moi": "low",
            "controls": {"negative_controls": ["NegCtrl0", "NegCtrl1"]},
        },
        "guide_assignment": {
            "assignment_method": "synthetic_guide_calling",
            "assigned_count": 4,
            "unassigned_count": 0,
            "multi_guide_count": 0,
            "target_summary": {"KLF1": 2, "NegCtrl": 2},
        },
        "basic_target_qc": {
            "metadata_csv": "metadata.csv",
            "guide_column": "guide",
            "guide_to_target_csv": "guide_map.csv",
            "target": "KLF1",
            "control": "NegCtrl pool",
            "minimum_cells": 2,
        },
        "basic_de": {
            "expression_csv": "expression.csv",
            "metadata_csv": "metadata.csv",
            "layer": "normalized_counts",
            "condition_column": "perturbation_uid",
        },
        "claim": {
            "claim_id": "classic_basic_qc_de_mechanism_overclaim",
            "text": "KLF1 validates an erythroid mechanism in this Perturb-seq experiment.",
            "requested_strength": "validated_mechanism_disabled",
        },
    }
    (tmp_path / "classic_recipe_config.json").write_text(json.dumps(config), encoding="utf-8")

    result = run_classic_perturbseq(tmp_path)

    step_names = [step.name for step in result.workflow_run_manifest.steps]
    assert "run_basic_target_qc" in step_names
    assert "run_basic_de_for_registered_contrast" in step_names
    assert "classic_basic_qc_de_mechanism_overclaim" in result.report_markdown
    assert "Claim strength ceiling: `measured_association`" in result.report_markdown
    assert (tmp_path / "outputs" / "basic_target_qc_KLF1.json").exists()

def test_classic_recipe_reports_unlinked_candidate_claim_gaps(tmp_path: Path) -> None:
    (tmp_path / "klf1_de.csv").write_text("gene,logfc,padj\nKLF1,-1,0.01\n", encoding="utf-8")
    config = {
        "dataset_id": "synthetic_norman_claim_gaps",
        "raw_labels": ["KLF1_NegCtrl0__KLF1_NegCtrl0", "NegCtrl0_NegCtrl0__NegCtrl0_NegCtrl0"],
        "perturbation_raw_label": "KLF1_NegCtrl0__KLF1_NegCtrl0",
        "controls": {"negative_controls": ["NegCtrl0"]},
        "experiment_design": {
            "assay": "Perturb-seq",
            "perturbation_modality": "guide_based_perturb_seq",
            "moi": "low",
            "controls": {"negative_controls": ["NegCtrl0"]},
        },
        "guide_assignment": {
            "assignment_method": "synthetic_guide_calling",
            "assigned_count": 40,
            "unassigned_count": 0,
            "multi_guide_count": 0,
            "target_summary": {"KLF1": 20, "NegCtrl0": 20},
        },
        "target_qc": {
            "target": "KLF1",
            "control": "NegCtrl0",
            "n_target_cells": 20,
            "n_control_cells": 20,
            "guides_per_target": 1,
            "guide_consistency": "single_guide_synthetic",
        },
        "measured_de": {
            "path": "klf1_de.csv",
            "contrast_left": "KLF1_NegCtrl0__KLF1_NegCtrl0",
            "contrast_baseline": "NegCtrl0_NegCtrl0__NegCtrl0_NegCtrl0",
            "method": "synthetic_wilcoxon",
            "n_left": 20,
            "n_baseline": 20,
            "multiple_testing": "BH",
            "has_padj": True,
        },
        "candidate_claims": [
            {
                "claim_id": "linked_klf1_claim",
                "text": "KLF1 validates an erythroid mechanism.",
                "perturbation_raw_label": "KLF1_NegCtrl0__KLF1_NegCtrl0",
                "requested_strength": "validated_mechanism_disabled",
            },
            {
                "claim_id": "unlinked_dusp9_claim",
                "text": "DUSP9 validates a mechanism.",
                "perturbation_raw_label": "DUSP9_NegCtrl0__DUSP9_NegCtrl0",
                "requested_strength": "validated_mechanism_disabled",
            },
        ],
    }
    (tmp_path / "classic_recipe_config.json").write_text(json.dumps(config), encoding="utf-8")

    result = run_classic_perturbseq(tmp_path)

    assert "Claim `linked_klf1_claim`" in result.report_markdown
    assert "Candidate Claim Gaps" in result.report_markdown
    assert "unlinked_dusp9_claim" in result.report_markdown
    assert len(result.decision_ids) == 1
    assert any(record["candidate_claim_id"] == "unlinked_dusp9_claim" and record["status"] == "unlinked" for record in result.candidate_claims)