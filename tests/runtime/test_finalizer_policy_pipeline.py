import json
from pathlib import Path

from pertura_gate.core.policy import policy_for_profile
from pertura_gate.evidence.registry import EvidenceRegistry
from pertura_gate.identity.design_manifest import scope_for_raw_label
from pertura_runtime.claude.finalizer import build_runtime_final_summary
from pertura_runtime.claude.workspace import ClaudeRunWorkspace


def test_runtime_finalizer_reuses_the_explicit_strict_policy(tmp_path: Path) -> None:
    workspace = ClaudeRunWorkspace.create(root=tmp_path / "runs", run_id="run1")
    registry = EvidenceRegistry.for_run(workspace.root)
    source = workspace.outputs_dir / "manifest.json"
    source.write_text("{}\n", encoding="utf-8")
    manifest = registry.register_perturbation_design_manifest(
        path=source,
        dataset_id="dataset",
        raw_labels=["GENE_NTC__GENE_NTC"],
        guide_to_target_map={"GENE": "GENE", "NTC": "negative_control"},
    )
    scope = scope_for_raw_label(manifest.metadata["manifest"], "GENE_NTC__GENE_NTC")
    de_path = workspace.outputs_dir / "de.csv"
    de_path.write_text("gene,padj\nGENE,0.01\n", encoding="utf-8")
    artifact = registry.register_measured_de(
        path=de_path,
        contrast_left="GENE",
        contrast_baseline="NTC",
        method="pseudobulk_de",
        n_left=40,
        n_baseline=40,
        multiple_testing="BH",
        has_padj=True,
        scope=scope,
        eligibility={
            "perturbation_cell_mapping": {"assignment_method": "guide_count_threshold", "guide_to_target_map_hash": "sha256:map"},
            "control_definition": {"negative_controls": ["NTC"], "control_label": "NTC"},
            "target_qc": {"n_target_cells": 40, "n_control_cells": 40},
            "assay_modality": "guide_based_perturb_seq",
            "perturbation_modality": "CRISPRi",
            "moi": "low",
            "estimand": "single_target_marginal",
            "replicate_scope": {"replicate_axis": "donor", "n_replicates": 2},
        },
    )
    workspace.write_json(
        workspace.artifacts_dir / "claims.json",
        {"claims": [{
            "claim_id": "measured_claim",
            "text": "GENE has a measured effect.",
            "subject": {"id": "GENE"},
            "scope": scope,
            "requested_strength": "measured_association",
            "evidence_refs": [artifact.artifact_id],
        }]},
    )
    policy = policy_for_profile("strict")
    summary = build_runtime_final_summary(workspace, status="completed", policy=policy)
    decisions = json.loads((workspace.artifacts_dir / "claim_decisions.json").read_text(encoding="utf-8"))["decisions"]
    state = json.loads((workspace.artifacts_dir / "analysis_state_manifest.json").read_text(encoding="utf-8"))
    assert f"Claim policy: `{policy.profile}` (`{policy.policy_hash}`)" in summary
    assert decisions[0]["policy_hash"] == policy.policy_hash
    assert decisions[0]["max_strength"] == "observation"
    assert state["runtime_policy_hash"] == policy.policy_hash
