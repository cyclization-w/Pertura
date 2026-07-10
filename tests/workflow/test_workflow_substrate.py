from __future__ import annotations

import json
from pathlib import Path

import pytest

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
    assert "registered target/cell QC eligibility" in report.readiness_by_claim_type["measured_de"].missing


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


def test_preflight_inspects_anndata_content(tmp_path: Path) -> None:
    anndata = pytest.importorskip("anndata")
    np = pytest.importorskip("numpy")
    pd = pytest.importorskip("pandas")

    obs = pd.DataFrame(
        {
            "guide_identity": ["KLF1_sg1", "KLF1_sg2", "NegCtrl0", "NTC_1", "non-targeting_ctrl1", "safe-targeting_2"],
            "donor": ["d1", "d1", "d2", "d2", "d3", "d3"],
            "batch": ["b1", "b1", "b1", "b2", "b2", "b2"],
            "n_genes_by_counts": [1000, 1100, 900, 950, 870, 880],
            "total_counts": [3000, 3100, 2600, 2700, 2500, 2550],
            "pct_counts_mt": [2.0, 2.5, 3.0, 2.8, 3.1, 3.3],
            "doublet_score": [0.01, 0.02, 0.03, 0.01, 0.02, 0.01],
            "leiden": ["0", "0", "1", "1", "2", "2"],
            "cell_type": ["erythroid", "erythroid", "control", "control", "control", "control"],
        },
        index=[f"cell{i}" for i in range(6)],
    )
    var = pd.DataFrame({"feature_types": ["Gene Expression", "Gene Expression", "Gene Expression"]}, index=["KLF1", "GATA1", "GYPA"])
    adata = anndata.AnnData(X=np.ones((6, 3)), obs=obs, var=var)
    adata.layers["counts"] = np.ones((6, 3))
    adata.layers["lognorm"] = np.ones((6, 3))
    adata.obsm["X_pca"] = np.ones((6, 2))
    adata.obsm["X_umap"] = np.ones((6, 2))
    adata.uns["neighbors"] = {"params": {"n_neighbors": 5}}
    h5ad_path = tmp_path / "synthetic_perturbseq.h5ad"
    adata.write_h5ad(h5ad_path)

    report = preflight_workspace(tmp_path)
    metadata = report.detected_metadata

    assert metadata["has_anndata"] is True
    assert metadata["anndata_files"][0]["n_obs"] == 6
    assert "guide_identity" in metadata["obs_columns"]
    assert metadata["has_counts_layer"] is True
    assert metadata["has_normalized_layer"] is True
    assert set(metadata["candidate_embedding_keys"]) >= {"X_pca", "X_umap"}
    assert any(item["column"] == "guide_identity" for item in metadata["candidate_perturbation_columns"])
    assert any(item["column"] == "guide_identity" for item in metadata["candidate_guide_columns"])
    assert any("NegCtrl0" in item["values"] for item in metadata["candidate_control_values"])
    assert any(item["column"] == "donor" for item in metadata["candidate_replicate_columns"])
    assert any(item["column"] == "batch" for item in metadata["candidate_batch_columns"])
    assert any(item["column"] == "pct_counts_mt" for item in metadata["candidate_qc_columns"])
    assert any(item["column"] == "cell_type" for item in metadata["candidate_state_columns"])
    assert report.readiness_by_claim_type["measured_de"].status == "maybe"
    assert report.readiness_by_claim_type["mechanism"].status == "blocked"
    content_candidates = {candidate.candidate_kind for candidate in report.candidate_artifacts}
    assert "perturbation_assignment_candidate" in content_candidates
    assert "control_definition_candidate" in content_candidates
    assert "replicate_scope_candidate" in content_candidates
    assert "cell_qc_candidate" in content_candidates
    assert "cell_state_reference_candidate" in content_candidates
    assert all(not candidate.validator_passed for candidate in report.candidate_artifacts)


def test_preflight_anndata_dependency_missing_is_nonfatal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "placeholder.h5ad").write_text("not a real h5ad", encoding="utf-8")

    import importlib

    real_import_module = importlib.import_module

    def fake_import_module(name: str, package: str | None = None):
        if name == "anndata":
            raise ImportError("simulated missing anndata")
        return real_import_module(name, package)

    monkeypatch.setattr(importlib, "import_module", fake_import_module)

    report = preflight_workspace(tmp_path)

    assert report.detected_metadata["has_anndata"] is True
    assert report.detected_metadata["anndata_dependency_missing"] is True
    assert report.readiness_by_claim_type["observation"].status == "ready"
    assert any("anndata dependency" in note for note in report.readiness_by_claim_type["observation"].notes)


def test_recommend_next_evidence_uses_content_preflight_gaps(tmp_path: Path) -> None:
    anndata = pytest.importorskip("anndata")
    np = pytest.importorskip("numpy")
    pd = pytest.importorskip("pandas")

    obs = pd.DataFrame(
        {
            "guide_identity": ["KLF1_sg1", "KLF1_sg2", "NegCtrl0", "NTC_1"],
            "donor": ["d1", "d1", "d2", "d2"],
            "cell_type": ["erythroid", "erythroid", "control", "control"],
        },
        index=["c1", "c2", "c3", "c4"],
    )
    var = pd.DataFrame(index=["KLF1", "GATA1"])
    adata = anndata.AnnData(X=np.ones((4, 2)), obs=obs, var=var)
    adata.layers["counts"] = np.ones((4, 2))
    adata.write_h5ad(tmp_path / "workspace.h5ad")

    preflight = preflight_workspace(tmp_path)
    goals = recommend_next_evidence(preflight)
    recommendations = "\n".join(goal.recommendation for goal in goals)
    missing = {goal.missing for goal in goals}

    assert "guide-to-target map" in missing
    assert "registered DesignManifest UID scope" in missing
    assert "registered target/cell QC eligibility" in missing
    assert "Build and register a PerturbationDesignManifest from obs.guide_identity" in recommendations
    assert "Provide or infer guide-to-target mapping for obs.guide_identity" in recommendations



def test_preflight_flags_moi_batch_confounding_x_counts_and_sgntc(tmp_path: Path) -> None:
    anndata = pytest.importorskip("anndata")
    np = pytest.importorskip("numpy")
    pd = pytest.importorskip("pandas")

    obs = pd.DataFrame(
        {
            "guide_identity": ["KLF1_sg1+GATA1_sg2", "KLF1_sg1+GATA1_sg2", "sgNTC1", "sgNonTargeting2"],
            "batch": ["b1", "b1", "b2", "b2"],
            "cell_type": ["erythroid", "erythroid", "control", "control"],
        },
        index=["c1", "c2", "c3", "c4"],
    )
    var = pd.DataFrame(index=["KLF1", "GATA1"])
    adata = anndata.AnnData(X=np.array([[1, 0], [2, 1], [0, 3], [1, 4]], dtype=int), obs=obs, var=var)
    adata.write_h5ad(tmp_path / "moi_confounded.h5ad")

    report = preflight_workspace(tmp_path)
    metadata = report.detected_metadata

    assert metadata["x_counts_hint"]["looks_like_counts"] is True
    assert metadata["has_counts_layer"] is True
    assert any("sgNTC1" in item["values"] for item in metadata["candidate_control_values"])
    assert any("sgNonTargeting2" in item["values"] for item in metadata["candidate_control_values"])
    assert any(item["risk"] == "possible_high_moi_or_combinatorial_assignment" for item in metadata["candidate_moi_risk"])
    assert any(item["status"] == "possible_batch_perturbation_confounding" for item in metadata["batch_perturbation_confounding"])

    measured_missing = set(report.readiness_by_claim_type["measured_de"].missing)
    assert "MOI risk review" in measured_missing
    assert "batch-perturbation confounding review" in measured_missing

    goals = recommend_next_evidence(report)
    recommendations = "\n".join(goal.recommendation for goal in goals)
    assert "Review MOI" in recommendations
    assert "batch x perturbation crosstab" in recommendations
