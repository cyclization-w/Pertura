from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import yaml

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


def test_perturbseq_profile_uses_only_pinned_conda_binary_packages() -> None:
    profile_path = (
        Path(environment_module.__file__).parent
        / "environments"
        / "perturbseq-python-v1.yml"
    )
    profile = yaml.safe_load(profile_path.read_text(encoding="utf-8"))
    dependencies = profile["dependencies"]

    assert profile["channels"] == ["conda-forge", "bioconda"]
    assert profile["channel_priority"] == "strict"
    assert not any(isinstance(item, dict) and "pip" in item for item in dependencies)
    assert "pip" not in dependencies
    assert {
        "anndata=0.11.4",
        "scanpy=1.12.1",
        "pandas=2.3",
        "mudata=0.3.2",
        "scikit-learn=1.8",
        "scikit-misc=0.5.2",
        "pertpy=1.1.1",
        "scrublet=0.2.3",
    }.issubset(set(dependencies))
    assert "'xp' in inspect.signature(GaussianMixture._m_step).parameters" in (
        environment_module.PYTHON_IMPORT_SMOKES["perturbseq-python-v1"]
    )


def test_perturbseq_doctor_rejects_version_drift(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("PERTURA_CACHE_DIR", str(tmp_path / "cache"))
    binary = micromamba_path()
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"fixture-micromamba")
    prefix = environment_prefix("perturbseq-python-v1")
    prefix.mkdir(parents=True)
    expected = environment_module.EXPECTED_PERTURBSEQ_PYTHON_VERSIONS
    manifest = {
        "schema_version": "pertura-environment-manifest-v2",
        "profile": "perturbseq-python-v1",
        "platform": "fixture",
        "micromamba": {
            "path": str(binary),
            "sha256": file_sha256(binary),
            "version": "2.6.2-1",
        },
        "prefix": str(prefix),
        "spec_hash": "sha256:fixture",
        "resource_hashes": environment_module._resource_hashes(
            "perturbseq-python-v1"
        ),
        "expected_versions": expected,
        "packages": [],
    }
    manifest["lock_hash"] = canonical_hash(manifest)
    (prefix / "pertura-environment-manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    observed = expected | {"pertpy": "1.1.0"}
    inventory = [
        {
            "name": environment_module.CONDA_PACKAGE_NAMES.get(name, name),
            "version": observed.get(name, "fixture"),
        }
        for name in environment_module.PYTHON_PACKAGES["perturbseq-python-v1"]
    ]

    def fake_run(command, *args, **kwargs):
        if "list" in command:
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(inventory),
                stderr="",
            )
        return SimpleNamespace(
            returncode=0,
            stdout="perturbseq-import-smoke-ok\n",
            stderr="",
        )

    monkeypatch.setattr(environment_module.subprocess, "run", fake_run)

    result = doctor_environment("perturbseq-python-v1")

    assert result["ok"] is False
    assert "expected pertpy 1.1.1, found 1.1.0" in result["problems"]


def test_perturbseq_doctor_rejects_api_import_failure(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("PERTURA_CACHE_DIR", str(tmp_path / "cache"))
    binary = micromamba_path()
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"fixture-micromamba")
    prefix = environment_prefix("perturbseq-python-v1")
    prefix.mkdir(parents=True)
    expected = environment_module.EXPECTED_PERTURBSEQ_PYTHON_VERSIONS
    manifest = {
        "schema_version": "pertura-environment-manifest-v2",
        "profile": "perturbseq-python-v1",
        "platform": "fixture",
        "micromamba": {
            "path": str(binary),
            "sha256": file_sha256(binary),
            "version": "2.6.2-1",
        },
        "prefix": str(prefix),
        "spec_hash": "sha256:fixture",
        "resource_hashes": environment_module._resource_hashes(
            "perturbseq-python-v1"
        ),
        "expected_versions": expected,
        "packages": [],
    }
    manifest["lock_hash"] = canonical_hash(manifest)
    (prefix / "pertura-environment-manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    inventory = [
        {
            "name": environment_module.CONDA_PACKAGE_NAMES.get(name, name),
            "version": expected.get(name, "fixture"),
        }
        for name in environment_module.PYTHON_PACKAGES["perturbseq-python-v1"]
    ]

    def fake_run(command, *args, **kwargs):
        if "list" in command:
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(inventory),
                stderr="",
            )
        return SimpleNamespace(
            returncode=1,
            stdout="",
            stderr="ImportError: cannot import name 'check_use_raw'",
        )

    monkeypatch.setattr(environment_module.subprocess, "run", fake_run)

    result = doctor_environment("perturbseq-python-v1")

    assert result["ok"] is False
    assert any(
        "API compatibility check failed" in problem
        and "check_use_raw" in problem
        for problem in result["problems"]
    )


def test_environment_setup_streams_micromamba_output(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("PERTURA_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("MAMBA_ROOT_PREFIX", str(tmp_path / "inherited-home-root"))
    monkeypatch.setenv("CONDA_PKGS_DIRS", str(tmp_path / "inherited-home-pkgs"))
    binary = micromamba_path()
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"fixture-micromamba")
    prefix = environment_prefix("python-science-v1")
    calls = []
    original_run = environment_module.subprocess.run

    def fake_run(command, *args, **kwargs):
        calls.append((command, kwargs))
        if "create" in command:
            prefix.mkdir(parents=True)
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if "list" in command:
            inventory = [
                {
                    "name": environment_module.CONDA_PACKAGE_NAMES.get(name, name),
                    "version": "fixture",
                }
                for name in environment_module.PYTHON_PACKAGES[
                    "python-science-v1"
                ]
            ]
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(inventory),
                stderr="",
            )
        return original_run(command, *args, **kwargs)

    monkeypatch.setattr(environment_module, "_ensure_micromamba", lambda: binary)
    monkeypatch.setattr(environment_module.subprocess, "run", fake_run)

    result = environment_module.setup_environment("python-science-v1")

    create_command, create_kwargs = calls[0]
    assert "create" in create_command
    assert create_kwargs["stdout"] is sys.stderr
    assert create_kwargs["stderr"] is sys.stderr
    assert "capture_output" not in create_kwargs
    controlled = create_kwargs["env"]
    cache = tmp_path / "cache"
    assert controlled["MAMBA_ROOT_PREFIX"] == str(cache / "mamba-root")
    assert controlled["CONDA_PKGS_DIRS"] == str(cache / "pkgs")
    assert controlled["TEMP"] == str(cache / "tmp")
    assert controlled["TMP"] == str(cache / "tmp")
    if sys.platform != "win32":
        assert controlled["TMPDIR"] == str(cache / "tmp")
    assert all(kwargs.get("env") == controlled for _, kwargs in calls[:2])
    assert result["doctor"]["ok"] is True
    assert (
        prefix / "pertura-environment-manifest.json"
    ).is_file()


def test_composition_profile_uses_only_pinned_conda_bioconda_packages() -> None:
    environment_root = Path(environment_module.__file__).parent / "environments"
    profile = yaml.safe_load(
        (environment_root / "composition-v1.yml").read_text(encoding="utf-8")
    )
    dependencies = profile["dependencies"]

    assert profile["channels"] == ["conda-forge", "bioconda"]
    assert profile["channel_priority"] == "strict"
    assert not any(isinstance(item, dict) and "pip" in item for item in dependencies)
    assert {
        "r-base=4.5.3",
        "bioconductor-speckle=1.10.0",
        "bioconductor-limma=3.66.0",
        "bioconductor-edger=4.8.2",
    }.issubset(set(dependencies))

    validator = (environment_root / "install-composition-v1.R").read_text(
        encoding="utf-8"
    )
    assert "BiocManager::install" not in validator
    assert "install.packages" not in validator
    assert "library(speckle)" in validator
    assert "library(limma)" in validator
    assert "library(edgeR)" in validator


def test_sceptre_installer_avoids_github_api_and_has_source_fallbacks() -> None:
    installer = (
        Path(environment_module.__file__).parent
        / "environments"
        / "install-sceptre-v1.R"
    ).read_text(encoding="utf-8")

    assert 'target_version <- "0.99.0"' in installer
    assert "codeload.github.com/Katsevich-Lab/sceptre" in installer
    assert 'Sys.getenv("CONDA_PREFIX"' in installer
    assert "SSL_CERT_FILE" in installer
    assert "CURL_CA_BUNDLE" in installer
    assert 'Sys.which("git")' in installer
    assert '"clone", "--depth", "1", "--branch", target_version' in installer
    assert "remotes::install_local" in installer
    assert "already_installed" in installer
    assert "install_github" not in installer


def test_composition_doctor_loads_packages_before_reporting_versions(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("PERTURA_CACHE_DIR", str(tmp_path / "cache"))
    binary = micromamba_path()
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"fixture-micromamba")
    prefix = environment_prefix("composition-v1")
    prefix.mkdir(parents=True)
    expected = environment_module.EXPECTED_COMPOSITION_VERSIONS
    manifest = {
        "schema_version": "pertura-environment-manifest-v2",
        "profile": "composition-v1",
        "platform": "fixture",
        "micromamba": {
            "path": str(binary),
            "sha256": file_sha256(binary),
            "version": "2.6.2-1",
        },
        "prefix": str(prefix),
        "spec_hash": "sha256:fixture",
        "resource_hashes": environment_module._resource_hashes(
            "composition-v1"
        ),
        "expected_versions": expected,
        "packages": [],
    }
    manifest["lock_hash"] = canonical_hash(manifest)
    (prefix / "pertura-environment-manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    observed_command: list[str] = []

    def fake_run(command, *args, **kwargs):
        observed_command.extend(command)
        output = "\n".join(
            ("4.5.3", "3.22", "1.10.0", "3.66.0", "4.8.2")
        )
        return SimpleNamespace(returncode=0, stdout=output, stderr="")

    monkeypatch.setattr(environment_module.subprocess, "run", fake_run)

    result = doctor_environment("composition-v1")

    assert result["ok"] is True
    expression = observed_command[-1]
    assert "library(speckle)" in expression
    assert "library(limma)" in expression
    assert "library(edgeR)" in expression
