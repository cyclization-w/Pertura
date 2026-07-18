from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from pertura_runtime.agent_bundle import (
    BUNDLED_SKILL_NAMES,
    bundled_skill_manifest,
    resolve_skill_configuration,
)
from pertura_runtime.claude.agent import _validate_sdk_skill_surface
from pertura_runtime.claude.options import (
    ClaudeRuntimeOptions,
    _supported_options,
    describe_options,
)


def test_bundled_skill_manifest_is_current_and_provider_neutral() -> None:
    manifest = bundled_skill_manifest()

    assert manifest["schema_version"] == "pertura-agent-skill-bundle-v1"
    assert manifest["bundle_version"] == "0.2.0"
    assert len(BUNDLED_SKILL_NAMES) == 7
    assert [item["name"] for item in manifest["skills"]] == list(BUNDLED_SKILL_NAMES)
    assert manifest["bundle_hash"].startswith("sha256:")
    assert all(
        item["content_hash"].startswith("sha256:") for item in manifest["skills"]
    )


def test_default_skill_configuration_is_allowlisted_and_path_free_in_provenance() -> (
    None
):
    resolved = resolve_skill_configuration()

    assert [item["type"] for item in resolved.plugins] == ["local"]
    assert resolved.skill_names == tuple(
        f"pertura:{name}" for name in BUNDLED_SKILL_NAMES
    )
    assert resolved.provenance["skill_bundle_hash"].startswith("sha256:")
    assert "path" not in json.dumps(resolved.provenance).lower()
    described = describe_options(ClaudeRuntimeOptions())
    assert described["available_skills"] == list(resolved.skill_names)


def _write_plugin(root: Path, *, plugin_name: str, skill_name: str) -> None:
    (root / ".claude-plugin").mkdir(parents=True)
    (root / "skills" / skill_name).mkdir(parents=True)
    (root / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": plugin_name}),
        encoding="utf-8",
    )
    (root / "skills" / skill_name / "SKILL.md").write_text(
        "---\n"
        f"name: {skill_name}\n"
        "description: A local test skill used only for adapter validation.\n"
        "---\n\n"
        "# Test\n",
        encoding="utf-8",
    )


def test_additional_skill_plugin_is_explicit_hashed_and_namespaced(
    tmp_path: Path,
) -> None:
    plugin = tmp_path / "lab-plugin"
    _write_plugin(plugin, plugin_name="lab", skill_name="lab-qc")

    resolved = resolve_skill_configuration(additional_plugin_paths=[plugin])

    assert resolved.skill_names[-1] == "lab:lab-qc"
    additional = resolved.provenance["additional_skill_plugin_hashes"]
    assert additional == [
        {
            "plugin_name": "lab",
            "skill_names": ["lab-qc"],
            "content_hash": additional[0]["content_hash"],
        }
    ]
    assert additional[0]["content_hash"].startswith("sha256:")


def test_additional_skill_plugin_rejects_missing_and_duplicate_names(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="does not exist"):
        resolve_skill_configuration(additional_plugin_paths=[tmp_path / "missing"])

    malformed = tmp_path / "malformed"
    (malformed / "skills" / "local-skill").mkdir(parents=True)
    (malformed / "skills" / "local-skill" / "SKILL.md").write_text(
        "---\nname: local-skill\ndescription: Local test skill.\n---\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="plugin.json"):
        resolve_skill_configuration(additional_plugin_paths=[malformed])

    duplicate = tmp_path / "duplicate"
    _write_plugin(
        duplicate,
        plugin_name="other",
        skill_name="operate-pertura-workflow",
    )
    with pytest.raises(ValueError, match="duplicate skill names"):
        resolve_skill_configuration(additional_plugin_paths=[duplicate])


@dataclass
class LegacyClaudeOptions:
    cwd: str


def test_required_skill_options_fail_fast_on_old_sdk() -> None:
    with pytest.raises(RuntimeError, match="claude-agent-sdk>=0.1.62"):
        _supported_options(
            LegacyClaudeOptions,
            {"cwd": "x", "plugins": [], "skills": []},
            required_fields={"plugins", "skills"},
        )


def test_sdk_initialized_skill_surface_must_match_allowlist() -> None:
    expected = tuple(sorted(f"pertura:{name}" for name in BUNDLED_SKILL_NAMES))
    _validate_sdk_skill_surface(expected, expected)
    provider_native = ("batch", "code-review", "dataviz")
    _validate_sdk_skill_surface(expected, tuple(sorted((*expected, *provider_native))))
    _validate_sdk_skill_surface((), provider_native)

    with pytest.raises(RuntimeError, match="unexpected skill surface"):
        _validate_sdk_skill_surface(expected, expected[:-1])

    with pytest.raises(RuntimeError, match="unexpected skill surface"):
        _validate_sdk_skill_surface((), (expected[0], *provider_native))


def test_sdk_init_event_records_available_skills(tmp_path: Path) -> None:
    from pertura_runtime.claude.manifest import ClaudeRunManifest
    from pertura_runtime.claude.workspace import ClaudeRunWorkspace

    class SystemMessage:
        subtype = "init"
        data = {
            "skills": [
                "pertura:operate-pertura-workflow",
                "pertura:inspect-perturb-seq-design",
            ],
            "plugins": [{"name": "pertura"}],
        }

    workspace = ClaudeRunWorkspace.create(root=tmp_path / "runs", run_id="init")
    manifest = ClaudeRunManifest(workspace)
    manifest.capture(SystemMessage())
    manifest.flush()

    recorded = json.loads(
        (workspace.root / "manifest.json").read_text(encoding="utf-8")
    )
    assert recorded["sdk_init_seen"] is True
    assert recorded["sdk_plugins"] == ["pertura"]
    assert recorded["sdk_available_skills"] == [
        "pertura:inspect-perturb-seq-design",
        "pertura:operate-pertura-workflow",
    ]


def test_provider_manifest_terminal_state_resets_between_tasks(tmp_path: Path) -> None:
    from pertura_runtime.claude.manifest import ClaudeRunManifest
    from pertura_runtime.claude.workspace import ClaudeRunWorkspace

    class ResultMessage:
        session_id = "session-1"
        result = "first result"
        subtype = "error_max_turns"
        is_error = True
        total_cost_usd = 1.5
        num_turns = 48

    workspace = ClaudeRunWorkspace.create(root=tmp_path / "runs", run_id="reset")
    manifest = ClaudeRunManifest(workspace)
    manifest.capture(ResultMessage())

    assert manifest.terminal_result_seen is True
    assert manifest.is_error is True
    assert manifest.result_text == "first result"
    assert manifest.num_turns == 48

    manifest.reset()

    assert manifest.terminal_result_seen is False
    assert manifest.is_error is False
    assert manifest.result_text == ""
    assert manifest.result_subtype is None
    assert manifest.session_id is None
    assert manifest.num_turns is None
    assert manifest.message_count == 0
