from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from pertura_core.hashing import canonical_hash, file_sha256
import pertura_workflow.environment as environment_module
from pertura_workflow.environment import doctor_environment, environment_prefix, micromamba_path


def test_environment_doctor_is_offline_and_actionable(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("PERTURA_CACHE_DIR", str(tmp_path / "cache"))
    result = doctor_environment("edger-v1")
    assert result["ok"] is False
    assert any("pertura env setup edger-v1" in item for item in result["problems"])
    assert not micromamba_path().exists()
    assert not environment_prefix().exists()


def test_environment_manifest_v2_detects_resource_drift(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("PERTURA_CACHE_DIR", str(tmp_path / "cache"))
    binary = micromamba_path()
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"fixture-micromamba")
    prefix = environment_prefix()
    prefix.mkdir(parents=True)
    manifest = {
        "schema_version": "pertura-environment-manifest-v2",
        "profile": "edger-v1",
        "platform": "fixture",
        "micromamba": {"path": str(binary), "sha256": file_sha256(binary), "version": "2.6.2-1"},
        "prefix": str(prefix),
        "spec_hash": "sha256:fixture",
        "resource_hashes": environment_module._resource_hashes("edger-v1"),
        "expected_versions": environment_module.EXPECTED_EDGER_VERSIONS,
        "packages": [],
    }
    manifest["lock_hash"] = canonical_hash(manifest)
    (prefix / "pertura-environment-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    output = "\n".join(("4.5.3", "1.30.27", "3.22", "4.8.2", "3.66.0", "2.0.0"))
    monkeypatch.setattr(environment_module.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=output, stderr=""))

    assert doctor_environment("edger-v1")["ok"] is True
    monkeypatch.setattr(environment_module, "_resource_hashes", lambda profile: {"environment_spec": "sha256:drift"})
    drifted = doctor_environment("edger-v1")
    assert drifted["ok"] is False
    assert "resource hash drift" in "; ".join(drifted["problems"])

def test_candidate_environment_profiles_are_explicit_and_hash_bound(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("PERTURA_CACHE_DIR", str(tmp_path / "cache"))
    assert set(environment_module.SUPPORTED_PROFILES) == {
        "edger-v1",
        "python-science-v1",
        "perturbseq-python-v1",
        "sceptre-v1",
        "composition-v1",
        "interpretation-v1",
        "virtual-eval-v1",
    }
    for profile in (
        "python-science-v1", "perturbseq-python-v1", "sceptre-v1",
        "composition-v1", "interpretation-v1", "virtual-eval-v1",
    ):
        result = doctor_environment(profile)
        assert result["ok"] is False
        assert any(f"pertura env setup {profile}" in item for item in result["problems"])
        hashes = environment_module._resource_hashes(profile)
        assert "environment_spec" in hashes
        if profile in {"sceptre-v1", "composition-v1"}:
            assert set(hashes) == {"environment_spec", "installer", "runner"}
        if profile in {
            "python-science-v1", "perturbseq-python-v1",
            "interpretation-v1", "virtual-eval-v1",
        }:
            assert any(key.startswith("python_runner:") for key in hashes)
