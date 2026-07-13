from __future__ import annotations

import json
from pathlib import Path

from pertura_runtime.claude.permissions import decide_tool_permission
from pertura_runtime.claude.prompt import write_prompt_files
from pertura_runtime.claude.workspace import ClaudeRunWorkspace


def test_claude_workspace_creates_run_bundle(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    workspace = ClaudeRunWorkspace.create(root=tmp_path / "runs", input_source=source, run_id="run1")

    assert workspace.root.exists()
    assert workspace.input_dir.exists()
    assert workspace.outputs_dir.exists()
    assert (workspace.input_dir / "source_path.txt").read_text(encoding="utf-8").strip() == str(source.resolve())
    manifest = json.loads((workspace.root / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["runtime"] == "claude_agent_sdk"
    assert manifest["status"] == "created"


def test_claude_prompt_files_are_written(tmp_path: Path) -> None:
    workspace = ClaudeRunWorkspace.create(root=tmp_path, run_id="run1")
    prompt = write_prompt_files(workspace, task="Inspect data")

    assert "capability-first" in prompt
    assert (workspace.task_dir / "PERTURA_TASK.md").read_text(encoding="utf-8") == "Inspect data"
    contract = (workspace.task_dir / "PERTURA_OUTPUT_CONTRACT.md").read_text(encoding="utf-8")
    assert "inspect_dataset" in contract
    assert "run_diagnostic" in contract
    assert "run_analysis" in contract
    assert "evaluate_virtual_model" in contract
    assert "finalize_report" in contract
    assert "CodeAct remains available" in contract
    assert "independent verifier" in contract
    helper = workspace.task_dir / "helpers" / "policy_threshold_probe.py"
    assert not helper.exists(), "the active product must not inject the legacy policy helper"

def test_prompt_records_interaction_mode(tmp_path: Path) -> None:
    workspace = ClaudeRunWorkspace.create(root=tmp_path / "runs", run_id="run1")

    prompt = write_prompt_files(workspace, task="Inspect data", interaction_mode="interactive")

    assert "Operating mode:\ninteractive" in prompt
    assert "user_supplied_metadata" in prompt
    assert "cannot by itself raise claim strength" in prompt


def test_describe_options_records_interaction_mode() -> None:
    from pertura_runtime.claude.options import ClaudeRuntimeOptions, describe_options

    payload = describe_options(ClaudeRuntimeOptions(interaction_mode="interactive"))

    assert payload["interaction_mode"] == "interactive"

def test_permission_guard_blocks_writes_to_input(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    workspace = ClaudeRunWorkspace.create(root=tmp_path / "runs", input_source=source, run_id="run1")

    denied = decide_tool_permission(
        workspace=workspace,
        tool_name="Write",
        input_data={"file_path": str(workspace.input_dir / "bad.txt")},
    )
    assert not denied.allowed
    assert "read-only input" in denied.reason

    allowed = decide_tool_permission(
        workspace=workspace,
        tool_name="Write",
        input_data={"file_path": str(workspace.outputs_dir / "ok.txt")},
    )
    assert allowed.allowed


def test_permission_guard_blocks_destructive_bash_against_input(tmp_path: Path) -> None:
    workspace = ClaudeRunWorkspace.create(root=tmp_path / "runs", run_id="run1")
    decision = decide_tool_permission(
        workspace=workspace,
        tool_name="Bash",
        input_data={"command": f"rm -rf {workspace.input_dir}"},
    )
    assert not decision.allowed


def test_supported_options_filters_unknown_sdk_fields() -> None:
    from dataclasses import dataclass

    from pertura_runtime.claude.options import _supported_options

    @dataclass
    class FakeOptions:
        cwd: str
        system_prompt: str

    filtered = _supported_options(
        FakeOptions,
        {"cwd": "run", "system_prompt": "prompt", "unknown": "drop", "model": None},
    )

    assert filtered == {"cwd": "run", "system_prompt": "prompt"}


def test_permission_guard_blocks_relative_bash_redirect_to_input(tmp_path: Path) -> None:
    workspace = ClaudeRunWorkspace.create(root=tmp_path / "runs", run_id="run1")
    decision = decide_tool_permission(
        workspace=workspace,
        tool_name="Bash",
        input_data={"command": "echo x > input/project/bad.txt"},
    )
    assert not decision.allowed


def test_permission_guard_blocks_python_write_to_input(tmp_path: Path) -> None:
    workspace = ClaudeRunWorkspace.create(root=tmp_path / "runs", run_id="run1")
    decision = decide_tool_permission(
        workspace=workspace,
        tool_name="Bash",
        input_data={"command": "python -c \"open('input/project/bad.txt','w').write('x')\""},
    )
    assert not decision.allowed


def test_stream_renderer_deduplicates_system_spam() -> None:
    from pertura_runtime.claude.stream import ClaudeStreamRenderer

    class SystemMessage:
        session_id = ""

    lines: list[str] = []
    renderer = ClaudeStreamRenderer(output_fn=lines.append)

    renderer.render(SystemMessage())
    renderer.render(SystemMessage())
    renderer.render(SystemMessage())

    assert lines == ["[session] started"]



def test_python_environment_preflight_records_json_package() -> None:
    import sys

    from pertura_runtime.claude.python_env import prepare_python_environment

    environment = prepare_python_environment(sys.executable, required_packages=["json"])

    assert environment.python_executable.exists()
    assert environment.sys_prefix.exists()
    assert environment.packages["json"].status == "ok"
    assert "PATH" in environment.env_overlay
    assert environment.shell_python.replace("\\", "/") == environment.shell_python


def test_prompt_includes_resolved_python_environment(tmp_path: Path) -> None:
    import sys

    from pertura_runtime.claude.prompt import build_system_prompt
    from pertura_runtime.claude.python_env import prepare_python_environment

    workspace = ClaudeRunWorkspace.create(root=tmp_path / "runs", run_id="run1")
    environment = prepare_python_environment(sys.executable, required_packages=["json"])

    prompt = build_system_prompt(workspace, python_environment=environment)

    assert environment.shell_python in prompt
    assert "Do not use bare `python`" in prompt
    assert "pertura_python_self_check_ok" in prompt
    assert "memorized/public dataset knowledge" in prompt
    assert "finalize_report" in prompt
    assert "not observed in local files" in prompt


def test_describe_options_redacts_env_values() -> None:
    from pertura_runtime.claude.options import ClaudeRuntimeOptions, describe_options

    payload = describe_options(
        ClaudeRuntimeOptions(
            env={"ANTHROPIC_API_KEY": "secret-value", "PATH": "some-path"},
            python_exe="/tmp/python",
        )
    )

    assert payload["env_keys"] == ["ANTHROPIC_API_KEY", "PATH"]
    assert "secret-value" not in str(payload)
    assert payload["python_exe"] == "/tmp/python"



def test_manifest_tracks_sdk_result_error(tmp_path: Path) -> None:
    from pertura_runtime.claude.manifest import ClaudeRunManifest

    class ResultMessage:
        is_error = True
        subtype = "error_during_execution"
        result = None
        total_cost_usd = 0.1
        num_turns = 2
        session_id = "session-1"
        model = "model-x"

    workspace = ClaudeRunWorkspace.create(root=tmp_path / "runs", run_id="run1")
    manifest = ClaudeRunManifest(workspace)

    manifest.capture(ResultMessage())
    manifest.flush(status="failed")

    payload = json.loads((workspace.root / "manifest.json").read_text(encoding="utf-8"))
    assert payload["is_error"] is True
    assert payload["result_subtype"] == "error_during_execution"



def test_default_claude_options_autoapprove_codeact_tools() -> None:
    from pertura_runtime.claude.options import ClaudeRuntimeOptions

    config = ClaudeRuntimeOptions()

    assert "Bash" in config.allowed_tools
    assert "Read" in config.allowed_tools
    assert "Write" in config.allowed_tools
    assert "mcp__pertura__inspect_dataset" in config.allowed_tools
    assert "mcp__pertura__finalize_report" in config.allowed_tools
    assert len([item for item in config.allowed_tools if item.startswith("mcp__pertura__")]) == 5






def test_runtime_final_summary_uses_evidence_report_not_claude_final(tmp_path: Path) -> None:
    from pertura_runtime.claude.finalizer import build_runtime_final_summary
    from pertura_gate.evidence.registry import EvidenceRegistry
    from pertura_gate.render.renderer import render_evidence_report

    workspace = ClaudeRunWorkspace.create(root=tmp_path / "runs", run_id="run1")
    registry = EvidenceRegistry.for_run(workspace.root)
    artifact = registry.register_measured_de(
        path="outputs/de.csv",
        contrast_left="KLF1",
        contrast_baseline="NegCtrl",
        method="wilcoxon",
        n_left=20,
        n_baseline=30,
        multiple_testing="bh",
        has_padj=True,
    )
    render_evidence_report(
        registry=registry,
        artifact_ids=[artifact.artifact_id],
        write_path=workspace.reports_dir / "evidence_report.md",
    )
    workspace.write_text(workspace.logs_dir / "claude_final.md", "KLF1 is a strong validated driver.\n")

    summary = build_runtime_final_summary(workspace, status="completed")

    assert "reports/evidence_report.md" in summary
    assert "measured_association" in summary
    assert "strong validated driver" not in summary
    assert "Claude draft final retained in internal audit logs" in summary
    assert "logs/claude_final.md" not in summary
    assert (workspace.reports_dir / "pertura_final.md").exists()


def test_runtime_final_summary_auto_renders_registered_evidence(tmp_path: Path) -> None:
    from pertura_runtime.claude.finalizer import build_runtime_final_summary
    from pertura_gate.evidence.registry import EvidenceRegistry

    workspace = ClaudeRunWorkspace.create(root=tmp_path / "runs", run_id="run1")
    registry = EvidenceRegistry.for_run(workspace.root)
    registry.register_measured_de(
        path="outputs/de.csv",
        contrast_left="KLF1",
        contrast_baseline="NegCtrl",
        method="wilcoxon",
        n_left=20,
        n_baseline=30,
        multiple_testing="bh",
        has_padj=True,
    )

    summary = build_runtime_final_summary(workspace, status="completed")

    assert (workspace.reports_dir / "evidence_report.md").exists()
    assert "measured_association" in summary


def test_stream_renderer_hides_free_prose_by_default() -> None:
    from pertura_runtime.claude.stream import ClaudeStreamRenderer

    class TextBlock:
        text = "KLF1 is a strong validated driver."

    class ThinkingBlock:
        thinking = "private scientific interpretation"

    class AssistantMessage:
        content = [TextBlock(), ThinkingBlock()]

    lines: list[str] = []
    renderer = ClaudeStreamRenderer(output_fn=lines.append)

    renderer.render(AssistantMessage())

    assert lines == []


def test_stream_renderer_hides_tool_result_content_by_default() -> None:
    from pertura_runtime.claude.stream import ClaudeStreamRenderer

    class ToolResultBlock:
        content = "Top DE genes imply a validated mechanism."
        is_error = False

    class UserMessage:
        content = [ToolResultBlock()]

    lines: list[str] = []
    renderer = ClaudeStreamRenderer(output_fn=lines.append)

    renderer.render(UserMessage())

    assert lines == ["[tool-result] ok"]
    assert "validated mechanism" not in "\n".join(lines)


def test_stream_renderer_raw_stream_shows_free_prose() -> None:
    from pertura_runtime.claude.stream import ClaudeStreamRenderer

    class TextBlock:
        text = "debug prose"

    class AssistantMessage:
        content = [TextBlock()]

    lines: list[str] = []
    renderer = ClaudeStreamRenderer(output_fn=lines.append, raw_stream=True)

    renderer.render(AssistantMessage())

    assert lines == ["[assistant] debug prose"]



def test_runtime_final_prefers_claim_calibrated_report_over_artifact_fallback(tmp_path: Path) -> None:
    from pertura_runtime.claude.finalizer import build_runtime_final_summary

    workspace = ClaudeRunWorkspace.create(root=tmp_path / "runs", run_id="run1")
    workspace.write_text(
        workspace.reports_dir / "evidence_report.md",
        "# Artifact-only fallback\n\n## Evidence `a`\n\n- Artifact intrinsic ceiling: `measured_association`\n",
    )
    workspace.write_text(
        workspace.reports_dir / "smoke03_evidence_report.md",
        "# Smoke 03\n\n## Runtime-calibrated findings\n\nclaim-conditioned result\n\n## Evidence / decision table\n",
    )

    summary = build_runtime_final_summary(workspace, status="failed", error="Claude SDK result error: error_max_turns")

    assert "reports/smoke03_evidence_report.md" in summary
    assert "claim-conditioned result" in summary
    assert "Artifact-only fallback" not in summary


def test_runtime_final_auto_renders_claim_file_when_claude_times_out_before_render(tmp_path: Path) -> None:
    from pertura_runtime.claude.finalizer import build_runtime_final_summary
    from pertura_gate.evidence.registry import EvidenceRegistry

    workspace = ClaudeRunWorkspace.create(root=tmp_path / "runs", run_id="run1")
    workspace.write_text(workspace.outputs_dir / "combinatorial_guide_summary.json", "{}\n")
    registry = EvidenceRegistry.for_run(workspace.root)
    artifact = registry.register_guide_assignment(
        path="outputs/combinatorial_guide_summary.json",
        assignment_method="local guide_identity metadata parsing",
        assigned_count=100,
        unassigned_count=0,
        multi_guide_count=40,
        guide_distribution={"dual_gene_combinatorial": {"cells": 40}},
        target_summary={"focal_combinatorial": "CEBPE_RUNX1T1__CEBPE_RUNX1T1"},
        guide_to_target_map_hash="sha256:guide-map",
        scope={"dataset_id": "GSE133344", "perturbation": "CEBPE_RUNX1T1", "control": "NegCtrl"},
    )
    workspace.write_text(
        workspace.outputs_dir / "smoke04_claims.json",
        json.dumps(
            [
                {
                    "claim_id": "single_gene_from_combo",
                    "text": "CEBPE alone validates a downstream mechanism.",
                    "scope": {"dataset_id": "GSE133344", "perturbation": "CEBPE", "control": "NegCtrl"},
                    "requested_strength": "validated_mechanism_disabled",
                    "evidence_refs": [artifact.artifact_id],
                },
                {
                    "claim_id": "combo_observed",
                    "text": "CEBPE_RUNX1T1 guide identity is observed.",
                    "scope": {"dataset_id": "GSE133344", "perturbation": "CEBPE_RUNX1T1", "control": "NegCtrl"},
                    "requested_strength": "observation",
                    "evidence_refs": [artifact.artifact_id],
                },
            ]
        ),
    )
    workspace.write_text(
        workspace.reports_dir / "evidence_report.md",
        "# Artifact-only fallback\n\n## Evidence `x`\n",
    )

    summary = build_runtime_final_summary(workspace, status="failed", error="Claude SDK result error: error_max_turns")

    assert "## Runtime-calibrated findings" in summary
    assert "single_gene_from_combo" in summary
    assert "unsupported" in summary
    assert "combo_observed" in summary
    assert "Artifact-only fallback" not in summary
    assert (workspace.artifacts_dir / "claim_decisions.json").exists()

def test_runtime_final_renders_existing_decisions_and_manifest(tmp_path: Path) -> None:
    from pertura_runtime.claude.finalizer import build_runtime_final_summary

    workspace = ClaudeRunWorkspace.create(root=tmp_path / "runs", run_id="run1")
    workspace.write_json(
        workspace.artifacts_dir / "claim_decisions.json",
        {
            "decisions": [
                {
                    "decision_id": "dec_1",
                    "claim_id": "claim_from_decisions",
                    "decision": "allowed_with_downgrade",
                    "max_strength": "predicted_effect",
                    "evidence_classes": ["predicted"],
                    "scope_fit": "exact",
                    "supporting_artifacts": ["pred_1"],
                    "blocked_requested_strength": "measured_association",
                    "allowed_surface": "A registered prediction artifact predicts an effect. This is prediction evidence, not a measured result.",
                    "policy_hash": "sha256:test-policy",
                    "reasons": ["prediction evidence only"],
                }
            ]
        },
    )

    summary = build_runtime_final_summary(workspace, status="completed")

    assert "claim_from_decisions" in summary
    assert "Runtime-calibrated findings" in summary
    assert "prediction evidence" in summary
    assert "Pertura Evidence Report" not in summary
    state = json.loads((workspace.artifacts_dir / "analysis_state_manifest.json").read_text(encoding="utf-8"))
    assert state["decision_ids"] == ["dec_1"]
    assert state["policy_hashes"] == ["sha256:test-policy"]
    assert state["evidence_report"] == "reports/evidence_report.md"


def test_output_contract_requires_contract_and_receipt_before_measured_claims(tmp_path: Path) -> None:
    workspace = ClaudeRunWorkspace.create(root=tmp_path / "runs", run_id="run1")

    write_prompt_files(workspace, task="Inspect data")
    contract = (workspace.task_dir / "PERTURA_OUTPUT_CONTRACT.md").read_text(encoding="utf-8")

    assert "inspect_dataset" in contract
    assert "signed receipt" in contract
    assert "Never infer missing control" in contract
    assert "Never write an effect through a design confirmation" in contract


def test_p06_smoke_task_contracts_use_manifest_uid_scope() -> None:
    root = Path(__file__).resolve().parents[2]
    smoke01 = (root / "docs" / "smoke_tasks" / "01_measured_association_with_eligibility.md").read_text(encoding="utf-8")
    smoke04 = (root / "docs" / "smoke_tasks" / "04_dual_guide_attribution_trap.md").read_text(encoding="utf-8")
    smoke05 = (root / "docs" / "smoke_tasks" / "05_policy_threshold_probe.md").read_text(encoding="utf-8")

    assert "register_perturbation_design_manifest" in smoke01
    assert "manifest-derived UID fields" in smoke01
    assert '"perturbation_uid": "target:KLF1"' in smoke01
    assert "<copy>" not in smoke01
    assert "measured association can pass only because" in smoke01

    assert "register_perturbation_design_manifest" in smoke04
    assert "combo:CEBPE+RUNX1T1" in smoke04
    assert "target:CEBPE" in smoke04

    assert "task/helpers/policy_threshold_probe.py" in smoke05
    assert "Do not inspect internal Pertura source files" in smoke05
    assert "Do not write a custom policy script from scratch" in smoke05
    assert "Strict policy caps" in smoke05


def test_policy_threshold_helper_is_not_staged_into_active_run_bundle(tmp_path: Path) -> None:
    workspace = ClaudeRunWorkspace.create(root=tmp_path / "runs", run_id="run1")

    write_prompt_files(workspace, task="Smoke 05")

    helper = workspace.task_dir / "helpers" / "policy_threshold_probe.py"
    assert not helper.exists(), "the active product must not inject the legacy policy helper"

def test_policy_threshold_helper_script_generates_decisions(tmp_path: Path, monkeypatch) -> None:
    import runpy

    workspace = ClaudeRunWorkspace.create(root=tmp_path / "runs", run_id="run1")
    monkeypatch.chdir(workspace.root)

    runpy.run_path(str(Path(__file__).resolve().parents[2] / "scripts" / "policy_threshold_probe.py"), run_name="__main__")

    decisions = json.loads((workspace.outputs_dir / "policy_threshold_decisions.json").read_text(encoding="utf-8"))
    strict = decisions["strict_policy"]
    relaxed = decisions["relaxed_policy"]

    assert strict["policy_hash"] != relaxed["policy_hash"]
    assert strict["decision"]["max_strength"] == "observation"
    assert any("below policy minimum 50" in reason for reason in strict["decision"]["reasons"])
    assert relaxed["decision"]["max_strength"] == "measured_association"
    assert (workspace.artifacts_dir / "claim_decisions.json").exists()
    assert (workspace.reports_dir / "evidence_report.md").exists()





def test_runtime_turn_final_records_claim_report_decisions(tmp_path: Path) -> None:
    from pertura_runtime.claude.finalizer import build_runtime_final_summary

    workspace = ClaudeRunWorkspace.create(root=tmp_path / "runs", run_id="run1")
    workspace.update_manifest({"stage_id": "claim_report"})
    workspace.write_json(
        workspace.artifacts_dir / "claim_decisions.json",
        {
            "decisions": [
                {
                    "decision_id": "decision_1",
                    "claim_id": "claim_mechanism_overreach",
                    "decision": "allowed_with_downgrade",
                    "max_strength": "measured_association",
                    "scope_fit": "exact",
                    "supporting_artifacts": ["measured_de_1"],
                    "blocked_requested_strength": "validated_mechanism_disabled",
                    "allowed_surface": "Measured association is supported; downstream mechanism is not established.",
                    "policy_hash": "sha256:policy",
                    "reasons": ["validated mechanism remains disabled"],
                }
            ]
        },
    )
    workspace.write_text(workspace.logs_dir / "claude_final.md", "KLF1 validates a mechanism.\n")

    summary = build_runtime_final_summary(workspace, status="completed")

    turn_payload = json.loads((workspace.artifacts_dir / "turn_final.json").read_text(encoding="utf-8"))
    turn_markdown = (workspace.reports_dir / "turn_final.md").read_text(encoding="utf-8")
    state = json.loads((workspace.artifacts_dir / "analysis_state_manifest.json").read_text(encoding="utf-8"))

    assert turn_payload["stage_id"] == "claim_report"
    assert turn_payload["surface_type"] == "claim_decision_surface"
    assert turn_payload["claim_decisions"] == ["claim_mechanism_overreach"]
    assert any("validated mechanism remains disabled" in reason for reason in turn_payload["blocked_or_downgraded_reasons"])
    assert state["turn_final"] == "reports/turn_final.md"
    assert "## Runtime Turn Final" in summary
    assert "claim_mechanism_overreach" in turn_markdown
    assert "logs/claude_final.md" not in summary
    assert "KLF1 validates a mechanism" not in summary


def test_runtime_turn_final_uses_stage_contract_for_non_claim_stage(tmp_path: Path) -> None:
    from pertura_runtime.claude.finalizer import build_runtime_final_summary
    from pertura_gate.evidence.registry import EvidenceRegistry

    workspace = ClaudeRunWorkspace.create(root=tmp_path / "runs", run_id="run1")
    workspace.update_manifest({"stage_id": "cell_state_reference"})
    registry = EvidenceRegistry.for_run(workspace.root)
    registry.register_measured_de(
        path="outputs/de.csv",
        contrast_left="KLF1",
        contrast_baseline="NegCtrl",
        method="wilcoxon",
        n_left=20,
        n_baseline=30,
        multiple_testing="bh",
        has_padj=True,
    )

    build_runtime_final_summary(workspace, status="completed")

    turn_payload = json.loads((workspace.artifacts_dir / "turn_final.json").read_text(encoding="utf-8"))
    turn_markdown = (workspace.reports_dir / "turn_final.md").read_text(encoding="utf-8")

    assert turn_payload["stage_id"] == "cell_state_reference"
    assert turn_payload["surface_type"] == "evidence_summary"
    assert turn_payload["claim_decisions"] == []
    assert "claim_decision_surface" not in turn_markdown
    assert "Registered 1 evidence artifact" in turn_markdown
    assert "`measured_association`" in turn_markdown


def test_runtime_turn_final_marks_empty_completed_run_as_no_evidence_registered(tmp_path: Path) -> None:
    from pertura_runtime.claude.finalizer import build_runtime_final_summary

    workspace = ClaudeRunWorkspace.create(root=tmp_path / "runs", run_id="run1")

    build_runtime_final_summary(workspace, status="completed")

    turn_payload = json.loads((workspace.artifacts_dir / "turn_final.json").read_text(encoding="utf-8"))

    assert turn_payload["stage_id"] == "unstaged"
    assert turn_payload["status"] == "no_evidence_registered"
    assert turn_payload["surface_type"] == "progress_only"
    assert turn_payload["what_was_done"] == ["No evidence artifacts were registered."]


def test_agent_preflight_failure_writes_capability_final_without_legacy_turn_state(tmp_path: Path, monkeypatch) -> None:
    import asyncio

    import pertura_runtime.claude.agent as agent_module
    from pertura_runtime.claude.agent import ClaudePerturaAgent
    from pertura_runtime.claude.options import ClaudeRuntimeOptions
    from pertura_runtime.claude.python_env import PythonEnvironmentError

    def fail_preflight(*args, **kwargs):
        raise PythonEnvironmentError("preflight failed", payload={"packages": {"scanpy": {"status": "error"}}})

    monkeypatch.setattr(agent_module, "prepare_python_environment", fail_preflight)
    workspace = ClaudeRunWorkspace.create(root=tmp_path / "runs", run_id="run1")
    agent = ClaudePerturaAgent(
        workspace=workspace,
        config=ClaudeRuntimeOptions(stage_id="cell_state_reference"),
        output_fn=lambda _line: None,
    )

    result = asyncio.run(agent.run("Run the stage."))

    assert result.status == "failed"
    assert (workspace.reports_dir / "pertura_final.md").exists()
    manifest = json.loads((workspace.root / "manifest.json").read_text(encoding="utf-8"))

    assert not (workspace.artifacts_dir / "turn_final.json").exists()
    assert "PythonEnvironmentError" in (workspace.reports_dir / "pertura_final.md").read_text(encoding="utf-8")
    assert manifest["runtime_final_path"] == "reports/pertura_final.md"
    assert manifest["turn_final_path"] is None
