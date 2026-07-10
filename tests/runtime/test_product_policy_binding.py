from __future__ import annotations

import json
from pathlib import Path

import pytest

from pertura_core import PromotionPolicy
from pertura_runtime.claude.agent import ClaudePerturaAgent
from pertura_runtime.claude.options import ClaudeRuntimeOptions
from pertura_runtime.claude.workspace import ClaudeRunWorkspace
from pertura_runtime.product import PerturaProductRuntime


def test_product_policy_is_persisted_reloaded_and_cannot_drift(tmp_path: Path) -> None:
    workspace = ClaudeRunWorkspace.create(root=tmp_path / "runs", run_id="policy")
    paper = PromotionPolicy(profile="paper")
    runtime = PerturaProductRuntime(workspace, policy=paper)
    assert runtime.promotion_policy is paper
    manifest = json.loads((workspace.root / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["trust_policy"]["policy_hash"] == paper.policy_hash
    runtime.close()

    reopened = PerturaProductRuntime(ClaudeRunWorkspace.open(workspace.root))
    assert reopened.promotion_policy.policy_hash == paper.policy_hash
    reopened.close()

    with pytest.raises(ValueError, match="conflicts"):
        PerturaProductRuntime(
            ClaudeRunWorkspace.open(workspace.root),
            policy=PromotionPolicy(profile="strict"),
        )


def test_product_policy_rejects_tampered_manifest_payload(tmp_path: Path) -> None:
    workspace = ClaudeRunWorkspace.create(root=tmp_path / "runs", run_id="tampered")
    runtime = PerturaProductRuntime(workspace)
    runtime.close()
    manifest_path = workspace.root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["trust_policy"]["payload"]["minimum_independent_units_per_arm"] = 99
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="hash does not match"):
        PerturaProductRuntime(ClaudeRunWorkspace.open(workspace.root))


def test_claude_agent_and_product_runtime_share_policy_instance(tmp_path: Path) -> None:
    workspace = ClaudeRunWorkspace.create(root=tmp_path / "runs", run_id="agent-policy")
    agent = ClaudePerturaAgent(
        workspace=workspace,
        config=ClaudeRuntimeOptions(policy_profile="paper"),
        verbose=False,
    )
    try:
        assert agent.product_runtime.promotion_policy is agent.policy
    finally:
        agent.product_runtime.close()
