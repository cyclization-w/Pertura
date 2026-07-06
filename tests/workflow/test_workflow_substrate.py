from __future__ import annotations

import json
from pathlib import Path

from pertura_gate.core.policy import DEFAULT_POLICY, GatePolicy
from pertura_workflow.harvest import harvest_artifacts_from_workspace
from pertura_workflow.models import HarvestMode, WorkflowRunManifest, WorkflowRunStep
from pertura_workflow.preflight import preflight_workspace
from pertura_workflow.recommend import recommend_next_evidence


def test_preflight_detects_files_candidates_and_readiness(tmp_path: Path) -> None:
    (tmp_path / "klf1_vs_negctrl_de.csv").write_text("gene,logfc,padj\nKLF1,-1,0.01\n", encoding="utf-8")
    (tmp_path / "guide_to_target_map.csv").write_text("guide,target\nsgKLF1,KLF1\n", encoding="utf-8")
    (tmp_path / "smoke_enrichment.json").write_text("{}", encoding="utf-8")

    report = preflight_workspace(tmp_path)

    assert report.detected_metadata["has_de_table"] is True
    assert report.detected_metadata["has_guide_metadata"] is True
    assert report.detected_metadata["has_enrichment_table"] is True
    assert {candidate.candidate_kind for candidate in report.candidate_artifacts} >= {
        "measured_de_table",
        "guide_metadata",
        "enrichment_table",
    }
    assert report.readiness_by_claim_type["measured_de"].status == "maybe"
    assert "target/cell QC eligibility" in report.readiness_by_claim_type["measured_de"].missing


def test_harvest_candidate_only_never_writes_registry(tmp_path: Path) -> None:
    (tmp_path / "prediction_output.csv").write_text("target,score\nKLF1,0.9\n", encoding="utf-8")
    registry_path = tmp_path / "artifacts" / "evidence_artifacts.jsonl"

    report = harvest_artifacts_from_workspace(
        tmp_path,
        mode=HarvestMode.candidate_only,
        registry_path=registry_path,
    )

    assert report.mode == HarvestMode.candidate_only
    assert report.candidates
    assert report.registered_artifact_ids == []
    assert not registry_path.exists()
    assert "candidate_only mode never writes" in "; ".join(report.reasons)


def test_auto_register_strict_does_not_register_ambiguous_candidates(tmp_path: Path) -> None:
    (tmp_path / "klf1_de.csv").write_text("gene,logfc,padj\nKLF1,-1,0.01\n", encoding="utf-8")
    registry_path = tmp_path / "artifacts" / "evidence_artifacts.jsonl"

    report = harvest_artifacts_from_workspace(
        tmp_path,
        mode=HarvestMode.auto_register_strict,
        registry_path=registry_path,
    )

    assert report.candidates
    assert all(not candidate.validator_passed for candidate in report.candidates)
    assert report.registered_artifact_ids == []
    assert not registry_path.exists()


def test_recommend_next_evidence_names_specific_missing_inputs(tmp_path: Path) -> None:
    (tmp_path / "virtual_prediction.csv").write_text("gene,score\nKLF1,0.7\n", encoding="utf-8")

    preflight = preflight_workspace(tmp_path)
    goals = recommend_next_evidence(preflight)
    missing = {goal.missing for goal in goals}

    assert "compatible measured artifact" in missing
    assert "model provenance" in missing
    assert any("measured artifact" in goal.recommendation for goal in goals)


def test_workflow_run_manifest_hash_changes_with_policy() -> None:
    first = WorkflowRunManifest(
        workflow_run_id="workflow_run_test",
        command="preflight",
        workspace="workspace",
        mode="benchmark",
        policy_hash=DEFAULT_POLICY.policy_hash,
        steps=[WorkflowRunStep("preflight", "passed")],
    )
    second = WorkflowRunManifest(
        workflow_run_id="workflow_run_test",
        command="preflight",
        workspace="workspace",
        mode="benchmark",
        policy_hash=GatePolicy(minimum_measured_n=20).policy_hash,
        steps=[WorkflowRunStep("preflight", "passed")],
    )

    assert first.workflow_run_hash != second.workflow_run_hash
    assert first.to_dict()["policy_hash"] == DEFAULT_POLICY.policy_hash


def test_workflow_cli_preflight_writes_report_and_run_manifest(tmp_path: Path) -> None:
    from pertura_workflow.cli import main

    (tmp_path / "klf1_de.csv").write_text("gene,logfc,padj\nKLF1,-1,0.01\n", encoding="utf-8")
    report_path = tmp_path / "preflight.json"
    manifest_path = tmp_path / "workflow_run_manifest.json"

    status = main(
        [
            "preflight",
            str(tmp_path),
            "--format",
            "json",
            "--out",
            str(report_path),
            "--run-manifest",
            str(manifest_path),
        ]
    )

    assert status == 0
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert payload["candidate_artifacts"]
    assert manifest["command"] == "preflight"
    assert manifest["workflow_run_hash"].startswith("sha256:")
