from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from pertura_core import PromotionPolicy
from pertura_bench.compatibility import compatibility_payloads
from pertura_runtime.claude.options import ClaudeRuntimeOptions, describe_options


ROOT = Path(__file__).resolve().parents[2]


def test_v2_promotion_policy_has_one_runtime_neutral_identity() -> None:
    policy = PromotionPolicy(profile="strict")
    frozen = compatibility_payloads()["promotion-policy.json"]

    assert describe_options(ClaudeRuntimeOptions())["policy_hash"] == policy.policy_hash
    assert frozen["policy_hash"] == policy.policy_hash
    assert frozen["policy"]["version"] == "pertura-promotion-v2"


def test_product_cli_import_does_not_load_legacy_runtime_spine() -> None:
    script = """
import json
import sys
import pertura_runtime.product_cli
import pertura_runtime.claude.agent
blocked = [
    name for name in sys.modules
    if name == 'pertura_gate' or name.startswith('pertura_gate.')
    or name == 'pertura_runtime.stages'
    or name == 'pertura_runtime.claude.finalizer'
    or name == 'pertura_runtime.claude.tools.evidence_tools'
    or name == 'pertura_workflow.preflight'
    or name == 'pertura_workflow.harvest'
    or name.startswith('pertura_workflow.recipes')
]
print(json.dumps(sorted(blocked)))
"""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT / "src")
    completed = subprocess.run(
        [sys.executable, "-c", script],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    assert json.loads(completed.stdout) == []


def test_executor_registry_does_not_import_optional_scientific_modules() -> None:
    script = """
import json
import sys
import pertura_workflow.capabilities.executors
blocked = [
    name for name in sys.modules
    if name in {
        'pertura_workflow.capabilities.p4_candidates',
        'pertura_workflow.capabilities.p5_candidates',
        'pertura_workflow.capabilities.state_candidates',
        'pertura_workflow.capabilities.target_candidates',
        'pertura_workflow.capabilities.effect_candidates',
    }
]
print(json.dumps(sorted(blocked)))
"""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT / "src")
    completed = subprocess.run(
        [sys.executable, "-c", script], check=True, capture_output=True, text=True, env=env
    )
    assert json.loads(completed.stdout) == []


def test_capability_options_reject_stage_and_legacy_surface(tmp_path: Path) -> None:
    from pertura_runtime.claude.prompt import build_system_prompt
    from pertura_runtime.claude.workspace import ClaudeRunWorkspace

    workspace = ClaudeRunWorkspace.create(root=tmp_path / "runs", run_id="run")
    with pytest.raises(ValueError, match="stage prompts"):
        build_system_prompt(workspace, stage_id="cell_state_reference")


def test_product_cli_has_no_legacy_command_dispatch() -> None:
    script = """
import json
import sys
from pertura_runtime.product_cli import main
try:
    main(['harvest', '--tool-surface', 'legacy'])
except SystemExit:
    pass
else:
    raise AssertionError('legacy command was accepted')
print(json.dumps('pertura_workflow.cli' in sys.modules))
"""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT / "src")
    completed = subprocess.run(
        [sys.executable, "-c", script],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    assert json.loads(completed.stdout) is False


def test_retired_authority_spine_is_physically_isolated() -> None:
    assert not (ROOT / "src" / "pertura_gate").exists()
    assert (ROOT / "legacy" / "src" / "pertura_gate").is_dir()
    retired_active_files = (
        ROOT / "src" / "pertura_runtime" / "claude" / "finalizer.py",
        ROOT / "src" / "pertura_runtime" / "claude" / "mcp_server.py",
        ROOT / "src" / "pertura_runtime" / "claude" / "tools" / "evidence_tools.py",
    )
    assert not any(path.exists() for path in retired_active_files)

    forbidden = (
        "Evidence" + "Artifact",
        "Evidence" + "Registry",
        "mcp__pertura_" + "evidence__",
        "from pertura_" + "gate",
        "import pertura_" + "gate",
    )
    findings = []
    for package in ("pertura_runtime", "pertura_workflow", "pertura_bench"):
        for source in (ROOT / "src" / package).rglob("*.py"):
            if any(token in source.read_text(encoding="utf-8") for token in forbidden):
                findings.append(source.relative_to(ROOT).as_posix())
    assert findings == []
