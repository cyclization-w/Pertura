import asyncio
import json
import sys
import types
from pathlib import Path

import pytest

from pertura_core import PromotionPolicy
from pertura_gate.core.policy import policy_for_profile
from pertura_runtime.claude.agent import ClaudePerturaAgent
from pertura_runtime.claude.options import ClaudeRuntimeOptions, describe_options
from pertura_runtime.claude.permissions import decide_tool_permission
from pertura_runtime.claude.workspace import ClaudeRunWorkspace


def _fake_sdk(monkeypatch, calls):
    module = types.ModuleType("claude_agent_sdk")
    def tool(name, description, schema):
        def decorate(fn):
            calls[name] = fn
            return fn
        return decorate
    module.tool = tool
    module.create_sdk_mcp_server = lambda **kwargs: kwargs
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", module)


def test_runtime_defaults_to_strict_policy_and_records_hash() -> None:
    payload = describe_options(ClaudeRuntimeOptions())
    assert payload["policy_profile"] == "strict"
    assert payload["policy_hash"] == PromotionPolicy(profile="strict").policy_hash


def test_agent_freezes_policy_in_run_manifest(tmp_path: Path) -> None:
    workspace = ClaudeRunWorkspace.create(root=tmp_path / "runs", run_id="run1")
    agent = ClaudePerturaAgent(workspace=workspace, config=ClaudeRuntimeOptions(policy_profile="paper"))
    manifest = json.loads((workspace.root / "manifest.json").read_text(encoding="utf-8"))
    assert agent.policy.profile == "paper"
    assert manifest["trust_policy"]["profile"] == "paper"
    assert manifest["trust_policy"]["policy_hash"] == agent.policy.policy_hash


@pytest.mark.parametrize("relative", [
    "manifest.json",
    "artifacts/evidence_artifacts.jsonl",
    "artifacts/execution_ledger.jsonl",
    "artifacts/claim_decisions.json",
    "reports/evidence_report.md",
])
def test_permission_guard_blocks_direct_trust_state_writes(tmp_path: Path, relative: str) -> None:
    workspace = ClaudeRunWorkspace.create(root=tmp_path / "runs", run_id="run1")
    decision = decide_tool_permission(workspace=workspace, tool_name="Write", input_data={"file_path": relative})
    assert decision.allowed is False
    assert "runtime-owned trust state" in decision.reason


def test_permission_guard_blocks_bash_ledger_forgery_but_allows_source_artifact(tmp_path: Path) -> None:
    workspace = ClaudeRunWorkspace.create(root=tmp_path / "runs", run_id="run1")
    denied = decide_tool_permission(workspace=workspace, tool_name="Bash", input_data={"command": "echo fake >> artifacts/execution_ledger.jsonl"})
    allowed = decide_tool_permission(workspace=workspace, tool_name="Write", input_data={"file_path": "outputs/target_qc_source.json"})
    assert denied.allowed is False
    assert allowed.allowed is True


def test_mcp_policy_is_bound_and_cannot_be_overridden(monkeypatch, tmp_path: Path) -> None:
    calls = {}; _fake_sdk(monkeypatch, calls)
    from pertura_runtime.claude.tools.evidence_tools import create_evidence_mcp_server
    workspace = ClaudeRunWorkspace.create(root=tmp_path / "runs", run_id="run1")
    create_evidence_mcp_server(workspace, policy=policy_for_profile("paper"))
    with pytest.raises(ValueError, match="runtime-owned"):
        asyncio.run(calls["render_evidence_report"]({"policy_profile": "smoke"}))
    route = asyncio.run(calls["route_analysis_method"]({"objective": "measured_effect", "design": {"moi": "low", "n_replicates": 2, "controls_defined": True, "guide_assignment_validated": True}}))
    assert route["primary_method"] == "pseudobulk_de"
