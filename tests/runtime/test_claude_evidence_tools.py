from __future__ import annotations

import asyncio
import json
import sys
import types
from dataclasses import dataclass
from pathlib import Path

import pytest

from pertura_runtime.claude.options import ClaudeRuntimeOptions, build_agent_options
from pertura_runtime.claude.workspace import ClaudeRunWorkspace


@dataclass
class FakeClaudeAgentOptions:
    cwd: str
    system_prompt: str
    allowed_tools: list[str]
    mcp_servers: dict


def _install_fake_sdk(monkeypatch, calls: dict) -> None:
    def fake_tool(name, description, schema):
        def decorate(func):
            func._tool_name = name
            func._tool_description = description
            func._tool_schema = schema
            return func
        return decorate

    def fake_create_sdk_mcp_server(*, name, version, tools):
        calls["server"] = {"name": name, "version": version, "tools": tools}
        calls["tools"] = {tool._tool_name: tool for tool in tools}
        return {"name": name, "version": version, "tools": tools}

    fake_sdk = types.SimpleNamespace(
        ClaudeAgentOptions=FakeClaudeAgentOptions,
        tool=fake_tool,
        create_sdk_mcp_server=fake_create_sdk_mcp_server,
    )
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", fake_sdk)


def test_default_claude_options_expose_exactly_five_product_tools(monkeypatch, tmp_path: Path) -> None:
    calls = {}
    _install_fake_sdk(monkeypatch, calls)

    workspace = ClaudeRunWorkspace.create(root=tmp_path / "runs", run_id="run1")
    options = build_agent_options(
        workspace=workspace,
        system_prompt="prompt",
        config=ClaudeRuntimeOptions(enable_audit_hooks=False),
    )

    assert "pertura" in options.mcp_servers
    assert calls["server"]["name"] == "pertura"
    assert [tool._tool_name for tool in calls["server"]["tools"]] == [
        "inspect_dataset",
        "run_diagnostic",
        "run_analysis",
        "evaluate_virtual_model",
        "finalize_report",
    ]
    assert {name for name in options.allowed_tools if name.startswith("mcp__pertura__")} == {
        "mcp__pertura__inspect_dataset",
        "mcp__pertura__run_diagnostic",
        "mcp__pertura__run_analysis",
        "mcp__pertura__evaluate_virtual_model",
        "mcp__pertura__finalize_report",
    }
    assert {"Read", "Glob", "Grep", "Bash", "Write", "Edit", "NotebookEdit"}.issubset(options.allowed_tools)


def test_legacy_tool_surface_is_not_available_in_production(monkeypatch, tmp_path: Path) -> None:
    calls = {}
    _install_fake_sdk(monkeypatch, calls)
    workspace = ClaudeRunWorkspace.create(root=tmp_path / "runs", run_id="legacy")
    with pytest.raises(ValueError, match="read-only"):
        build_agent_options(
            workspace=workspace,
            system_prompt="prompt",
            config=ClaudeRuntimeOptions(enable_audit_hooks=False, tool_surface="legacy"),
        )
    assert "server" not in calls

def test_evidence_mcp_register_and_render_by_path(monkeypatch, tmp_path: Path) -> None:
    calls = {}
    _install_fake_sdk(monkeypatch, calls)

    from pertura_runtime.claude.tools.evidence_tools import create_evidence_mcp_server

    workspace = ClaudeRunWorkspace.create(root=tmp_path / "runs", run_id="run1")
    de_path = workspace.outputs_dir / "DE_KLF1_vs_NegCtrl.csv"
    de_path.write_text("gene,padj\nA,0.01\n", encoding="utf-8")

    create_evidence_mcp_server(workspace)
    register_result = asyncio.run(calls["tools"]["register_measured_de_artifact"]({
        "path": "outputs/DE_KLF1_vs_NegCtrl.csv",
        "contrast_left": "KLF1",
        "contrast_baseline": "NegCtrl",
        "method": "wilcoxon",
        "n_left": 1980,
        "n_baseline": 12015,
        "multiple_testing": "bh",
        "has_padj": True,
        "columns": ["gene", "padj"],
        "source_data": "local",
        "notes": "",
    }))
    render_result = asyncio.run(calls["tools"]["render_evidence_report"]({
        "artifact_ids": ["outputs/DE_KLF1_vs_NegCtrl.csv"],
        "title": "KLF1 Evidence",
        "report_filename": "reports/evidence_report.md",
    }))

    assert register_result["artifact_id"].startswith("measured_de_")
    assert register_result["evidence_class"] == "measured"
    assert register_result["artifact_intrinsic_ceiling"] == "measured_association"
    assert register_result["artifact"]["source_sha256"].startswith("sha256:")
    assert register_result["next_claim_template"] == {
        "scope": register_result["artifact"]["scope"],
        "evidence_refs": [register_result["artifact_id"]],
    }
    assert "requested_strength" not in register_result["next_claim_template"]
    assert register_result["claim_usage"] == "direct_evidence_ref"
    assert register_result["handoff_path"] in {"artifacts/latest_registration.json", "artifacts\\latest_registration.json"}
    latest = json.loads((workspace.artifacts_dir / "latest_registration.json").read_text(encoding="utf-8"))
    assert latest["artifact_id"] == register_result["artifact_id"]
    assert latest["next_claim_template"] == register_result["next_claim_template"]
    assert (workspace.artifacts_dir / "registration_handoffs.jsonl").exists()
    claimable = json.loads((workspace.artifacts_dir / "claimable_artifacts.json").read_text(encoding="utf-8"))
    assert claimable["artifacts"][0]["artifact_id"] == register_result["artifact_id"]
    assert claimable["artifacts"][0]["next_claim_template"] == register_result["next_claim_template"]
    assert render_result["report_path"] == "reports\\evidence_report.md" or render_result["report_path"] == "reports/evidence_report.md"
    assert (workspace.reports_dir / "evidence_report.md").exists()
    assert not (workspace.reports_dir / "reports" / "evidence_report.md").exists()
    assert render_result["resolutions"][0]["ceiling"] == "measured_association"


def test_evidence_mcp_metadata_artifacts_do_not_return_claim_template(monkeypatch, tmp_path: Path) -> None:
    calls = {}
    _install_fake_sdk(monkeypatch, calls)

    from pertura_runtime.claude.tools.evidence_tools import create_evidence_mcp_server

    workspace = ClaudeRunWorkspace.create(root=tmp_path / "runs", run_id="run1")
    manifest_path = workspace.outputs_dir / "design_manifest_source.json"
    manifest_path.write_text("{}\n", encoding="utf-8")
    qc_path = workspace.outputs_dir / "cell_qc.json"
    qc_path.write_text("{}\n", encoding="utf-8")
    create_evidence_mcp_server(workspace)

    manifest = asyncio.run(calls["tools"]["register_perturbation_design_manifest"]({
        "path": "outputs/design_manifest_source.json",
        "dataset_id": "local",
        "raw_labels": ["KLF1_NegCtrl0__KLF1_NegCtrl0"],
    }))
    cell_qc = asyncio.run(calls["tools"]["register_cell_qc_artifact"]({
        "path": "outputs/cell_qc.json",
        "n_cells_after_qc": 1000,
        "qc_policy": "standard_scanpy_qc",
        "passed": True,
        "scope": {"dataset_id": "local"},
    }))

    assert manifest["next_claim_template"] is None
    assert cell_qc["next_claim_template"] is None
    assert "do not put this artifact_id in evidence_refs" in manifest["claim_usage"]
    assert "do not put this artifact_id in evidence_refs" in cell_qc["claim_usage"]
    assert "requested_strength" not in manifest
    assert "requested_strength" not in cell_qc

def test_evidence_mcp_rejects_reports_as_evidence_source(monkeypatch, tmp_path: Path) -> None:
    calls = {}
    _install_fake_sdk(monkeypatch, calls)

    from pertura_runtime.claude.tools.evidence_tools import create_evidence_mcp_server

    workspace = ClaudeRunWorkspace.create(root=tmp_path / "runs", run_id="run1")
    report_path = workspace.reports_dir / "not_evidence.csv"
    report_path.write_text("x\n", encoding="utf-8")
    create_evidence_mcp_server(workspace)

    try:
        asyncio.run(calls["tools"]["register_predicted_effect_artifact"]({"path": "reports/not_evidence.csv"}))
    except ValueError as exc:
        assert "reports/ cannot be registered" in str(exc)
    else:
        raise AssertionError("reports/ evidence source should have been rejected")


def test_evidence_mcp_claim_report_downgrades_predicted_claim(monkeypatch, tmp_path: Path) -> None:
    calls = {}
    _install_fake_sdk(monkeypatch, calls)

    from pertura_runtime.claude.tools.evidence_tools import create_evidence_mcp_server

    workspace = ClaudeRunWorkspace.create(root=tmp_path / "runs", run_id="run1")
    pred_path = workspace.outputs_dir / "pred.csv"
    pred_path.write_text("target,score\nGENE_X,0.8\n", encoding="utf-8")
    create_evidence_mcp_server(workspace)
    registered = asyncio.run(calls["tools"]["register_predicted_effect_artifact"]({
        "path": "outputs/pred.csv",
        "model_name": "toy-model",
        "perturbation": "KLF1",
        "target": "GENE_X",
    }))

    result = asyncio.run(calls["tools"]["render_evidence_report"]({
        "artifact_ids": [registered["artifact_id"]],
        "claims": [{
            "claim_id": "claim_predicted_as_measured",
            "text": "KLF1 was measured to validate erythroid activation.",
            "subject": {"type": "perturbation", "id": "KLF1"},
            "object": {"type": "gene", "id": "GENE_X"},
            "scope": {"perturbation": "KLF1"},
            "requested_strength": "measured_association",
            "evidence_refs": [registered["artifact_id"]],
        }],
        "report_filename": "evidence_report.md",
    }))

    decision = result["decisions"][0]
    assert decision["max_strength"] == "predicted_effect"
    assert "prediction artifact predicts" in decision["allowed_surface"]
    assert "experimental result" in decision["allowed_surface"]
    assert "measured" not in decision["allowed_surface"].lower()
    assert "validates" not in decision["allowed_surface"].lower()


def test_evidence_mcp_evaluate_claims_uses_eligibility_profile(monkeypatch, tmp_path: Path) -> None:
    calls = {}
    _install_fake_sdk(monkeypatch, calls)

    from pertura_runtime.claude.tools.evidence_tools import create_evidence_mcp_server

    workspace = ClaudeRunWorkspace.create(root=tmp_path / "runs", run_id="run1")
    de_path = workspace.outputs_dir / "DE_KLF1_vs_NegCtrl.csv"
    de_path.write_text("gene,padj\nA,0.01\n", encoding="utf-8")
    manifest_path = workspace.outputs_dir / "design_manifest_source.json"
    manifest_path.write_text("{}\n", encoding="utf-8")
    create_evidence_mcp_server(workspace)
    manifest = asyncio.run(calls["tools"]["register_perturbation_design_manifest"]({
        "path": "outputs/design_manifest_source.json",
        "dataset_id": "local",
        "raw_labels": ["KLF1_NegCtrl0__KLF1_NegCtrl0"],
    }))
    registered = asyncio.run(calls["tools"]["register_measured_de_artifact"]({
        "path": "outputs/DE_KLF1_vs_NegCtrl.csv",
        "contrast_left": "KLF1",
        "contrast_baseline": "NegCtrl",
        "method": "wilcoxon",
        "n_left": 120,
        "n_baseline": 150,
        "multiple_testing": "BH",
        "has_padj": True,
        "columns": ["gene", "padj"],
        "source_data": "local",
        "eligibility": {
            "perturbation_cell_mapping": {"assignment_method": "guide_count_threshold", "guide_to_target_map_hash": "sha256:map"},
            "control_definition": {"negative_controls": ["NegCtrl"], "control_label": "NegCtrl"},
            "target_qc": {"n_target_cells": 120, "n_control_cells": 150, "guides_per_target": 2},
            "assay_modality": "guide_based_perturb_seq",
            "perturbation_modality": "CRISPRa",
            "moi": "low",
            "estimand": "single_target_marginal",
        },
        "scope": {"design_manifest_id": manifest["artifact_id"], "raw_label": "KLF1_NegCtrl0__KLF1_NegCtrl0"},
    }))

    result = asyncio.run(calls["tools"]["evaluate_claims"]({
        "claims": [{
            "claim_id": "claim_measured",
            "text": "KLF1 validates an erythroid mechanism.",
            "subject": {"id": "KLF1"},
            "scope": registered["artifact"]["scope"],
            "requested_strength": "validated_mechanism_disabled",
            "evidence_refs": [registered["artifact_id"]],
        }],
        "decisions_filename": "claim_decisions.json",
    }))

    decision = result["decisions"][0]
    assert decision["max_strength"] == "measured_association"
    assert decision["decision"] == "allowed_with_downgrade"
    assert result["decisions_path"] in {"artifacts\\claim_decisions.json", "artifacts/claim_decisions.json"}
    assert (workspace.artifacts_dir / "claim_decisions.json").exists()





def test_evidence_mcp_registers_perturbation_efficiency_target_engagement(monkeypatch, tmp_path: Path) -> None:
    calls = {}
    _install_fake_sdk(monkeypatch, calls)

    from pertura_runtime.claude.tools.evidence_tools import create_evidence_mcp_server

    workspace = ClaudeRunWorkspace.create(root=tmp_path / "runs", run_id="run1")
    path = workspace.outputs_dir / "target_engagement.csv"
    path.write_text("target,effect\nKLF1,-1.2\n", encoding="utf-8")
    manifest_path = workspace.outputs_dir / "design_manifest_source.json"
    manifest_path.write_text("{}\n", encoding="utf-8")
    create_evidence_mcp_server(workspace)
    manifest = asyncio.run(calls["tools"]["register_perturbation_design_manifest"]({
        "path": "outputs/design_manifest_source.json",
        "dataset_id": "local",
        "raw_labels": ["KLF1_NegCtrl0__KLF1_NegCtrl0"],
    }))

    registered = asyncio.run(calls["tools"]["register_perturbation_efficiency_artifact"]({
        "path": "outputs/target_engagement.csv",
        "perturbation": "KLF1",
        "target_gene": "KLF1",
        "modality": "CRISPRi",
        "expected_direction": "down",
        "observed_direction": "down",
        "effect_size": -1.2,
        "method": "target expression DE",
        "n_target_cells": 120,
        "n_control_cells": 150,
        "scope": {"design_manifest_id": manifest["artifact_id"], "raw_label": "KLF1_NegCtrl0__KLF1_NegCtrl0"},
    }))
    result = asyncio.run(calls["tools"]["evaluate_claims"]({
        "claims": [{
            "claim_id": "target_engagement_claim",
            "text": "KLF1 target engagement validates a downstream mechanism.",
            "subject": {"id": "KLF1"},
            "object": {"id": "KLF1"},
            "scope": registered["artifact"]["scope"],
            "requested_strength": "validated_mechanism_disabled",
            "evidence_refs": [registered["artifact_id"]],
        }],
    }))

    assert registered["artifact_intrinsic_ceiling"] == "measured_target_engagement"
    decision = result["decisions"][0]
    assert decision["max_strength"] == "measured_target_engagement"
    assert "target engagement" in decision["allowed_surface"].lower()
    assert "downstream mechanism" in decision["allowed_surface"].lower()
def test_evidence_mcp_render_dedupes_inline_and_file_claims(monkeypatch, tmp_path: Path) -> None:
    calls = {}
    _install_fake_sdk(monkeypatch, calls)

    from pertura_runtime.claude.tools.evidence_tools import create_evidence_mcp_server

    workspace = ClaudeRunWorkspace.create(root=tmp_path / "runs", run_id="run1")
    pred_path = workspace.outputs_dir / "pred.csv"
    pred_path.write_text("target,score\nGENE_X,0.8\n", encoding="utf-8")
    create_evidence_mcp_server(workspace)
    registered = asyncio.run(calls["tools"]["register_predicted_effect_artifact"]({
        "path": "outputs/pred.csv",
        "model_name": "toy-model",
        "perturbation": "KLF1",
        "target": "GENE_X",
    }))
    claim = {
        "claim_id": "duplicate_claim",
        "text": "The prediction measured KLF1 activation.",
        "subject": {"id": "KLF1"},
        "scope": {"perturbation": "KLF1"},
        "requested_strength": "measured_association",
        "evidence_refs": [registered["artifact_id"]],
    }
    claims_path = workspace.artifacts_dir / "claims.json"
    claims_path.write_text(json.dumps({"claims": [claim]}), encoding="utf-8")

    result = asyncio.run(calls["tools"]["render_evidence_report"]({
        "artifact_ids": [registered["artifact_id"]],
        "claims": [claim],
        "claims_json_path": "artifacts/claims.json",
        "report_filename": "evidence_report.md",
    }))

    assert len(result["decisions"]) == 1
    assert result["decisions"][0]["claim_id"] == "duplicate_claim"







def test_evidence_mcp_registers_cell_qc_as_observation(monkeypatch, tmp_path: Path) -> None:
    calls = {}
    _install_fake_sdk(monkeypatch, calls)

    from pertura_runtime.claude.tools.evidence_tools import create_evidence_mcp_server

    workspace = ClaudeRunWorkspace.create(root=tmp_path / "runs", run_id="run1")
    path = workspace.outputs_dir / "cell_qc.json"
    path.write_text("{}\n", encoding="utf-8")
    create_evidence_mcp_server(workspace)

    registered = asyncio.run(calls["tools"]["register_cell_qc_artifact"]({
        "path": "outputs/cell_qc.json",
        "n_cells_after_qc": 1000,
        "qc_policy": "standard_scanpy_qc",
        "doublet_policy": "filtered",
        "ambient_policy": "reviewed",
        "passed": True,
        "scope": {"dataset_id": "local"},
    }))

    assert registered["evidence_class"] == "observed_metadata"
    assert registered["artifact_intrinsic_ceiling"] == "observation"


def test_evidence_mcp_registers_control_calibration_artifact(monkeypatch, tmp_path: Path) -> None:
    calls = {}
    _install_fake_sdk(monkeypatch, calls)

    from pertura_runtime.claude.tools.evidence_tools import create_evidence_mcp_server

    workspace = ClaudeRunWorkspace.create(root=tmp_path / "runs", run_id="run1")
    path = workspace.outputs_dir / "control_calibration.json"
    path.write_text("{}\n", encoding="utf-8")
    create_evidence_mcp_server(workspace)

    registered = asyncio.run(calls["tools"]["register_control_calibration_artifact"]({
        "path": "outputs/control_calibration.json",
        "calibration_type": "control_null_checks",
        "ntc_vs_ntc_check": {"passed": True, "status": "passed"},
        "label_permutation_check": {"passed": True, "status": "passed"},
        "alpha": 0.05,
        "n_features_tested": 100,
        "n_significant": 0,
        "method": "basic_control_calibration_v1",
        "execution_hash": "sha256:control-calibration-runner",
        "scope": {"dataset_id": "local"},
    }))

    assert registered["evidence_class"] == "observed_metadata"
    assert registered["evidence_predicate"] == "analysis_eligibility"
    assert registered["artifact_intrinsic_ceiling"] == "observation"
    assert registered["next_claim_template"] is None
    assert registered["artifact"]["eligibility"]["control_calibration"]["ntc_vs_ntc_check"]["passed"] is True


def test_evidence_mcp_failed_cell_qc_downgrades_measured_de(monkeypatch, tmp_path: Path) -> None:
    calls = {}
    _install_fake_sdk(monkeypatch, calls)

    from pertura_runtime.claude.tools.evidence_tools import create_evidence_mcp_server

    workspace = ClaudeRunWorkspace.create(root=tmp_path / "runs", run_id="run1")
    de_path = workspace.outputs_dir / "DE_KLF1_vs_NegCtrl.csv"
    de_path.write_text("gene,padj\nA,0.01\n", encoding="utf-8")
    manifest_path = workspace.outputs_dir / "design_manifest_source.json"
    manifest_path.write_text("{}\n", encoding="utf-8")
    qc_path = workspace.outputs_dir / "cell_qc.json"
    qc_path.write_text("{}\n", encoding="utf-8")
    create_evidence_mcp_server(workspace)

    manifest = asyncio.run(calls["tools"]["register_perturbation_design_manifest"]({
        "path": "outputs/design_manifest_source.json",
        "dataset_id": "local",
        "raw_labels": ["KLF1_NegCtrl0__KLF1_NegCtrl0"],
    }))
    registered = asyncio.run(calls["tools"]["register_measured_de_artifact"]({
        "path": "outputs/DE_KLF1_vs_NegCtrl.csv",
        "contrast_left": "KLF1",
        "contrast_baseline": "NegCtrl",
        "method": "wilcoxon",
        "n_left": 120,
        "n_baseline": 150,
        "multiple_testing": "BH",
        "has_padj": True,
        "eligibility": {
            "perturbation_cell_mapping": {"assignment_method": "guide_count_threshold", "guide_to_target_map_hash": "sha256:map"},
            "control_definition": {"negative_controls": ["NegCtrl"], "control_label": "NegCtrl"},
            "target_qc": {"n_target_cells": 120, "n_control_cells": 150, "guides_per_target": 2},
            "assay_modality": "guide_based_perturb_seq",
            "perturbation_modality": "CRISPRa",
            "moi": "low",
            "estimand": "single_target_marginal",
        },
        "scope": {"design_manifest_id": manifest["artifact_id"], "raw_label": "KLF1_NegCtrl0__KLF1_NegCtrl0"},
    }))
    asyncio.run(calls["tools"]["register_cell_qc_artifact"]({
        "path": "outputs/cell_qc.json",
        "n_cells_after_qc": 10,
        "qc_policy": "failed_qc",
        "passed": False,
        "scope": registered["artifact"]["scope"],
    }))

    result = asyncio.run(calls["tools"]["evaluate_claims"]({
        "claims": [{
            "claim_id": "failed_cell_qc_measured",
            "text": "KLF1 has a measured association.",
            "subject": {"id": "KLF1"},
            "scope": registered["artifact"]["scope"],
            "requested_strength": "measured_association",
            "evidence_refs": [registered["artifact_id"]],
        }],
    }))

    decision = result["decisions"][0]
    assert decision["max_strength"] == "observation"
    assert any("cell QC" in reason for reason in decision["reasons"])


def test_evidence_mcp_registers_module_effect_and_evaluates_claim(monkeypatch, tmp_path: Path) -> None:
    calls = {}
    _install_fake_sdk(monkeypatch, calls)

    from pertura_runtime.claude.tools.evidence_tools import create_evidence_mcp_server

    workspace = ClaudeRunWorkspace.create(root=tmp_path / "runs", run_id="run1")
    module_path = workspace.outputs_dir / "module_effect.json"
    module_path.write_text("{}\n", encoding="utf-8")
    manifest_path = workspace.outputs_dir / "design_manifest_source.json"
    manifest_path.write_text("{}\n", encoding="utf-8")
    create_evidence_mcp_server(workspace)
    manifest = asyncio.run(calls["tools"]["register_perturbation_design_manifest"]({
        "path": "outputs/design_manifest_source.json",
        "dataset_id": "local",
        "raw_labels": ["KLF1_NegCtrl0__KLF1_NegCtrl0"],
    }))
    registered = asyncio.run(calls["tools"]["register_module_effect_artifact"]({
        "path": "outputs/module_effect.json",
        "module_id": "erythroid_module",
        "module_source": "curated_gene_set",
        "module_gene_set_hash": "sha256:module",
        "scoring_method": "score_genes",
        "effect_size": 0.8,
        "method": "wilcoxon",
        "padj": 0.01,
        "n_target_cells": 120,
        "n_control_cells": 150,
        "scope": {"design_manifest_id": manifest["artifact_id"], "raw_label": "KLF1_NegCtrl0__KLF1_NegCtrl0"},
        "quality": {"eligibility": {
            "perturbation_cell_mapping": {"assignment_method": "guide_count_threshold", "guide_to_target_map_hash": "sha256:map"},
            "control_definition": {"negative_controls": ["NegCtrl"], "control_label": "NegCtrl"},
            "target_qc": {"n_target_cells": 120, "n_control_cells": 150},
            "assay_modality": "guide_based_perturb_seq",
            "moi": "low",
            "estimand": "single_target_marginal"
        }},
    }))
    result = asyncio.run(calls["tools"]["evaluate_claims"]({
        "claims": [{
            "claim_id": "module_claim",
            "text": "The module effect validates a mechanism.",
            "subject": {"id": "KLF1"},
            "object": {"type": "module", "id": "erythroid_module"},
            "scope": registered["artifact"]["scope"],
            "requested_strength": "validated_mechanism_disabled",
            "evidence_refs": [registered["artifact_id"]],
        }],
    }))

    assert registered["evidence_predicate"] == "module_score_shift"
    assert registered["artifact"]["evidence_predicate"] == "module_score_shift"
    assert registered["artifact_intrinsic_ceiling"] == "measured_association"
    decision = result["decisions"][0]
    assert decision["max_strength"] == "measured_association"
    assert "module-score" in decision["allowed_surface"]
    assert "validates" not in decision["allowed_surface"].lower()


def test_evidence_mcp_registers_global_effect_and_blocks_gene_specific_claim(monkeypatch, tmp_path: Path) -> None:
    calls = {}
    _install_fake_sdk(monkeypatch, calls)

    from pertura_runtime.claude.tools.evidence_tools import create_evidence_mcp_server

    workspace = ClaudeRunWorkspace.create(root=tmp_path / "runs", run_id="run1")
    global_path = workspace.outputs_dir / "global_effect.json"
    global_path.write_text("{}\n", encoding="utf-8")
    manifest_path = workspace.outputs_dir / "design_manifest_source.json"
    manifest_path.write_text("{}\n", encoding="utf-8")
    create_evidence_mcp_server(workspace)
    manifest = asyncio.run(calls["tools"]["register_perturbation_design_manifest"]({
        "path": "outputs/design_manifest_source.json",
        "dataset_id": "local",
        "raw_labels": ["KLF1_NegCtrl0__KLF1_NegCtrl0"],
    }))
    registered = asyncio.run(calls["tools"]["register_global_effect_artifact"]({
        "path": "outputs/global_effect.json",
        "metric": "energy_distance",
        "feature_space": "PCA",
        "comparison_method": "permutation_test",
        "distance": 0.4,
        "null_model": "label_permutation",
        "padj": 0.02,
        "n_target_cells": 120,
        "n_control_cells": 150,
        "scope": {"design_manifest_id": manifest["artifact_id"], "raw_label": "KLF1_NegCtrl0__KLF1_NegCtrl0"},
        "quality": {"eligibility": {
            "perturbation_cell_mapping": {"assignment_method": "guide_count_threshold", "guide_to_target_map_hash": "sha256:map"},
            "control_definition": {"negative_controls": ["NegCtrl"], "control_label": "NegCtrl"},
            "target_qc": {"n_target_cells": 120, "n_control_cells": 150},
            "assay_modality": "guide_based_perturb_seq",
            "moi": "low",
            "estimand": "single_target_marginal"
        }},
    }))
    result = asyncio.run(calls["tools"]["evaluate_claims"]({
        "claims": [{
            "claim_id": "global_gene_specific_claim",
            "text": "The global shift proves differential expression for GENE_X.",
            "subject": {"id": "KLF1"},
            "relation": "differential_expression",
            "object": {"type": "gene", "id": "GENE_X"},
            "scope": registered["artifact"]["scope"],
            "requested_strength": "measured_association",
            "evidence_refs": [registered["artifact_id"]],
        }],
    }))

    assert registered["artifact_intrinsic_ceiling"] == "measured_association"
    decision = result["decisions"][0]
    assert decision["max_strength"] == "observation"
    assert any("gene-specific" in reason for reason in decision["reasons"])


def test_evidence_mcp_registers_cell_state_reference_as_context_only(monkeypatch, tmp_path: Path) -> None:
    calls = {}
    _install_fake_sdk(monkeypatch, calls)

    from pertura_runtime.claude.tools.evidence_tools import create_evidence_mcp_server

    workspace = ClaudeRunWorkspace.create(root=tmp_path / "runs", run_id="run1")
    summary_path = workspace.outputs_dir / "state_reference_summary.json"
    summary_path.write_text('{"assignment_column":"leiden"}\n', encoding="utf-8")
    create_evidence_mcp_server(workspace)

    result = asyncio.run(calls["tools"]["register_cell_state_reference_artifact"]({
        "path": "outputs/state_reference_summary.json",
        "assignment_column": "leiden",
        "embedding_methods": [
            {"method": "PCA", "n_components": 20},
            {"method": "UMAP", "basis": "X_umap"},
        ],
        "clustering_method": "leiden",
        "annotation_method": "marker_summary",
        "marker_summary_path": "outputs/cluster_markers.csv",
        "source_data_path": "outputs/annotated.h5ad",
        "source_data_sha256": "sha256:source",
        "scope": {"dataset_id": "synthetic"},
    }))

    assert result["artifact_id"].startswith("cell_state_reference_")
    assert result["evidence_class"] == "observed_metadata"
    assert result["artifact_intrinsic_ceiling"] == "observation"
    assert "scope_definition" in result["artifact_roles"]
    assert "state_context" in result["artifact_roles"]
    assert result["artifact"]["quality"]["embedding_methods"][0]["method"] == "PCA"
    assert result["next_claim_template"] is None
    assert "do not put this artifact_id in evidence_refs" in result["claim_usage"]


def test_evidence_mcp_render_accepts_natural_string_claim_fields(monkeypatch, tmp_path: Path) -> None:
    calls = {}
    _install_fake_sdk(monkeypatch, calls)

    from pertura_runtime.claude.tools.evidence_tools import create_evidence_mcp_server

    workspace = ClaudeRunWorkspace.create(root=tmp_path / "runs", run_id="run1")
    pred_path = workspace.outputs_dir / "pred.csv"
    pred_path.write_text("target,score\nGENE_X,0.8\n", encoding="utf-8")
    create_evidence_mcp_server(workspace)
    registered = asyncio.run(calls["tools"]["register_predicted_effect_artifact"]({
        "path": "outputs/pred.csv",
        "model_name": "toy-model",
        "perturbation": "KLF1",
        "target": "GENE_X",
    }))

    result = asyncio.run(calls["tools"]["render_evidence_report"]({
        "artifact_ids": [registered["artifact_id"]],
        "claims": {
            "claim_id": "natural_string_claim",
            "text": "KLF1 perturbation validates an erythroid mechanism.",
            "subject": "KLF1 perturbation",
            "object": "erythroid mechanism",
            "scope": registered["artifact"]["scope"],
            "requested_strength": "validates_mechanism",
            "evidence_refs": [registered["artifact_id"]],
        },
        "report_filename": "evidence_report.md",
    }))

    decision = result["decisions"][0]
    assert decision["claim_id"] == "natural_string_claim"
    assert decision["max_strength"] == "predicted_effect"
    assert decision["decision"] == "allowed_with_downgrade"
    assert decision["blocked_requested_strength"] == "validates_mechanism"
    assert (workspace.artifacts_dir / "claim_decisions.json").exists()


def test_family_registrars_are_not_claude_facing_mcp_tools(monkeypatch, tmp_path: Path) -> None:
    calls = {}
    _install_fake_sdk(monkeypatch, calls)

    workspace = ClaudeRunWorkspace.create(root=tmp_path / "runs", run_id="run1")
    options = build_agent_options(
        workspace=workspace,
        system_prompt="prompt",
        config=ClaudeRuntimeOptions(enable_audit_hooks=False),
    )
    create_tool_names = {tool._tool_name for tool in calls["server"]["tools"]}
    allowed_tool_names = set(options.allowed_tools)
    family_registrars = {
        "register_scope_artifact",
        "register_eligibility_artifact",
        "register_measured_effect_artifact",
        "register_prior_artifact",
        "register_prediction_artifact",
        "register_inferred_structure_artifact",
        "register_ranking_artifact",
        "register_dataset_metadata_artifact",
    }

    assert create_tool_names.isdisjoint(family_registrars)
    assert allowed_tool_names.isdisjoint({f"mcp__pertura_evidence__{name}" for name in family_registrars})


def test_evidence_mcp_registers_composition_effect_and_blocks_fate_claim(monkeypatch, tmp_path: Path) -> None:
    calls = {}
    _install_fake_sdk(monkeypatch, calls)

    from pertura_runtime.claude.tools.evidence_tools import create_evidence_mcp_server

    workspace = ClaudeRunWorkspace.create(root=tmp_path / "runs", run_id="run1")
    comp_path = workspace.outputs_dir / "composition_effect.json"
    comp_path.write_text("{}\n", encoding="utf-8")
    manifest_path = workspace.outputs_dir / "design_manifest_source.json"
    manifest_path.write_text("{}\n", encoding="utf-8")
    create_evidence_mcp_server(workspace)
    manifest = asyncio.run(calls["tools"]["register_perturbation_design_manifest"]({
        "path": "outputs/design_manifest_source.json",
        "dataset_id": "local",
        "raw_labels": ["KLF1_NegCtrl0__KLF1_NegCtrl0"],
    }))
    registered = asyncio.run(calls["tools"]["register_composition_effect_artifact"]({
        "path": "outputs/composition_effect.json",
        "state_source": "cell_state_reference_abc",
        "state_assignment_column": "state_label",
        "comparison_method": "fisher_exact",
        "counts_by_state": {"target": {"erythroid": 80, "other": 40}, "control": {"erythroid": 40, "other": 110}},
        "state_level_deltas": {"erythroid": 0.4, "other": -0.4},
        "effect_size": 0.25,
        "padj": 0.01,
        "n_target_cells": 120,
        "n_control_cells": 150,
        "scope": {"design_manifest_id": manifest["artifact_id"], "raw_label": "KLF1_NegCtrl0__KLF1_NegCtrl0"},
        "quality": {
            "eligibility": {
                "perturbation_cell_mapping": {"assignment_method": "guide_count_threshold", "guide_to_target_map_hash": "sha256:map"},
                "control_definition": {"negative_controls": ["NegCtrl"], "control_label": "NegCtrl"},
                "target_qc": {"n_target_cells": 120, "n_control_cells": 150},
                "assay_modality": "guide_based_perturb_seq",
                "moi": "low",
                "estimand": "single_target_marginal",
            },
        },
    }))
    result = asyncio.run(calls["tools"]["evaluate_claims"]({
        "claims": [{
            "claim_id": "composition_fate_claim",
            "text": "KLF1 causes erythroid fate conversion through a validated mechanism.",
            "subject": {"id": "KLF1"},
            "object": {"type": "cell_state", "id": "erythroid"},
            "scope": registered["artifact"]["scope"],
            "requested_strength": "causal_fate_conversion",
            "evidence_refs": [registered["artifact_id"]],
        }],
    }))

    assert registered["artifact_intrinsic_ceiling"] == "measured_association"
    assert registered["artifact_id"].startswith("composition_effect_")
    decision = result["decisions"][0]
    assert decision["max_strength"] == "measured_association"
    assert "composition association" in decision["allowed_surface"]
    assert "does not establish" in decision["allowed_surface"]




def test_evidence_mcp_registers_virtual_prediction_and_concordance(monkeypatch, tmp_path: Path) -> None:
    calls = {}
    _install_fake_sdk(monkeypatch, calls)

    from pertura_runtime.claude.tools.evidence_tools import create_evidence_mcp_server

    workspace = ClaudeRunWorkspace.create(root=tmp_path / "runs", run_id="run1")
    pred_path = workspace.outputs_dir / "virtual_prediction.json"
    pred_path.write_text('{"gene":"GENE_X","delta":1.0}\n', encoding="utf-8")
    de_path = workspace.outputs_dir / "measured_de.csv"
    de_path.write_text("gene,padj\nGENE_X,0.01\n", encoding="utf-8")
    conc_path = workspace.outputs_dir / "concordance.json"
    conc_path.write_text('{"metric":"spearman","value":0.7}\n', encoding="utf-8")
    create_evidence_mcp_server(workspace)

    pred = asyncio.run(calls["tools"]["register_virtual_perturbation_prediction_artifact"]({
        "path": "outputs/virtual_prediction.json",
        "tool_name": "GEARS",
        "tool_version": "0.1",
        "model_name": "toy-gears",
        "model_checkpoint_hash": "sha256:model",
        "prediction_method": "inference",
        "prediction_type": "delta_expression",
        "perturbation_query": {"perturbation": "KLF1"},
        "output_schema": {"columns": ["gene", "delta"]},
        "n_predicted_genes": 1,
        "scope": {"perturbation": "KLF1"},
    }))
    measured = asyncio.run(calls["tools"]["register_measured_de_artifact"]({
        "path": "outputs/measured_de.csv",
        "contrast_left": "KLF1",
        "contrast_baseline": "NegCtrl",
        "method": "wilcoxon",
        "n_left": 120,
        "n_baseline": 150,
        "multiple_testing": "BH",
        "has_padj": True,
        "scope": {"perturbation": "KLF1"},
    }))
    concordance = asyncio.run(calls["tools"]["register_prediction_measured_concordance_artifact"]({
        "path": "outputs/concordance.json",
        "prediction_artifact_id": pred["artifact_id"],
        "measured_artifact_id": measured["artifact_id"],
        "metric": "spearman",
        "metric_value": 0.7,
        "denominator": 1,
        "scope_match": "exact",
        "comparison_method": "shared_gene_delta_correlation",
        "scope": {"perturbation": "KLF1"},
    }))

    assert pred["evidence_class"] == "predicted"
    assert pred["evidence_predicate"] == "predicted_perturbation_response"
    assert pred["artifact_intrinsic_ceiling"] == "predicted_effect"
    assert concordance["evidence_predicate"] == "prediction_measured_concordance"
    assert concordance["artifact_intrinsic_ceiling"] == "predicted_effect"
    assert concordance["next_claim_template"] == {
        "scope": concordance["artifact"]["scope"],
        "evidence_refs": [concordance["artifact_id"]],
    }


def test_evidence_mcp_registers_virtual_cell_state_transition(monkeypatch, tmp_path: Path) -> None:
    calls = {}
    _install_fake_sdk(monkeypatch, calls)

    from pertura_runtime.claude.tools.evidence_tools import create_evidence_mcp_server

    workspace = ClaudeRunWorkspace.create(root=tmp_path / "runs", run_id="run1")
    transition_path = workspace.outputs_dir / "celloracle_transition.json"
    transition_path.write_text('{"state":"erythroid","score":0.4}\n', encoding="utf-8")
    create_evidence_mcp_server(workspace)

    result = asyncio.run(calls["tools"]["register_virtual_cell_state_transition_artifact"]({
        "path": "outputs/celloracle_transition.json",
        "tool_name": "CellOracle",
        "model_or_network_provenance": {"network_hash": "sha256:network"},
        "transition_type": "vector_field",
        "perturbation_query": {"perturbation": "KLF1"},
        "state_space_reference": {"basis": "umap"},
        "scope": {"perturbation": "KLF1"},
    }))

    assert result["evidence_class"] == "predicted"
    assert result["evidence_predicate"] == "predicted_cell_state_transition"
    assert result["artifact_intrinsic_ceiling"] == "predicted_effect"
