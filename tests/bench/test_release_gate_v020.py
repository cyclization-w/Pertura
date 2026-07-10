from __future__ import annotations

import tomllib
from pathlib import Path

from pertura_bench import release_gate
from pertura_bench.release_gate import (
    EXPECTED_GITATTRIBUTES,
    _attribute_rules,
    _banned_tracked_paths,
    _clean_worktree_check,
    _machine_path_files,
    _package_version_check,
    audit_v020,
)


def test_release_audit_v3_separates_repository_runtime_fixture_and_real_state(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("PERTURA_CACHE_DIR", str(tmp_path / "empty-cache"))
    root = Path(__file__).resolve().parents[2]
    audit = audit_v020(root, require_clean_worktree=False)
    checks = {item["check_id"]: item for item in audit["checks"]}
    assert checks["default_domain_tool_count"]["passed"] is True
    assert checks["legacy_approximation_not_trusted"]["passed"] is True
    assert checks["dashboard_production_bundle"]["passed"] is True
    assert checks["candidate_case_catalog"]["passed"] is True
    assert checks["server_plan_no_manual_placeholders"]["passed"] is True
    assert audit["schema_version"] == "pertura-release-audit-v3"
    repository_checks = [
        item["passed"] for item in audit["checks"] if item["category"] == "repository"
    ]
    assert audit["repository_ready"] is all(repository_checks)
    assert checks["authoritative_inner_repo"]["passed"] is True
    assert checks["portable_attribute_rules"]["passed"] is True
    assert checks["git_worktree_clean"]["passed"] is True
    assert "enforcement disabled" in checks["git_worktree_clean"]["detail"]
    assert checks["tracked_machine_paths_absent"]["passed"] is True
    assert checks["banned_tracked_artifacts_absent"]["passed"] is True
    assert checks["package_version_parity"]["passed"] is True
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    assert audit["build_version"] == str(project["project"]["version"])
    assert audit["runtime_spine_ready"] is True
    assert audit["code_ready"] is True
    assert audit["local_fixture_ready"] is True
    assert audit["optional_environment_ready"] is False
    assert audit["real_benchmark_ready"] is False
    assert audit["release_ready"] is False
    assert "edger_environment" in audit["blocking_checks"]
    assert "validated_target_profiles" in audit["blocking_checks"]
    assert any("real-data" in item for item in audit["remaining_blockers"])


def test_repository_helpers_are_fail_closed_and_allow_in_progress_audit(
    tmp_path: Path,
) -> None:
    assert (
        _clean_worktree_check([" M pyproject.toml"], require_clean_worktree=True).passed
        is False
    )
    relaxed = _clean_worktree_check([" M pyproject.toml"], require_clean_worktree=False)
    assert relaxed.passed is True
    assert "enforcement disabled" in relaxed.detail

    attributes = tmp_path / ".gitattributes"
    attributes.write_text("\n".join(EXPECTED_GITATTRIBUTES) + "\n", encoding="utf-8")
    assert _attribute_rules(attributes) == EXPECTED_GITATTRIBUTES
    attributes.write_text(
        attributes.read_text(encoding="utf-8") + "*.unexpected text\n",
        encoding="utf-8",
    )
    assert _attribute_rules(attributes) != EXPECTED_GITATTRIBUTES

    banned = _banned_tracked_paths(
        [
            "src/pertura_core/models.py",
            "build/lib/pertura.py",
            "src/pertura.egg-info/PKG-INFO",
            "fixtures/private.h5ad",
            "ui/node_modules/vite/index.js",
        ]
    )
    assert banned == [
        "build/lib/pertura.py",
        "fixtures/private.h5ad",
        "src/pertura.egg-info/PKG-INFO",
        "ui/node_modules/vite/index.js",
    ]


def test_machine_path_scan_has_a_narrow_fixture_allowlist(tmp_path: Path) -> None:
    allowed = tmp_path / "tests" / "bench" / "test_benchmark_protocol.py"
    allowed.parent.mkdir(parents=True)
    separator = chr(92)
    allowed.write_text(
        f'path = "C:{separator}data{separator}raw.h5ad"', encoding="utf-8"
    )
    bad = tmp_path / "docs" / "bad.md"
    bad.parent.mkdir()
    bad.write_text(
        f'python = "D:{separator}anaconda{separator}python.exe"', encoding="utf-8"
    )

    findings = _machine_path_files(
        tmp_path,
        [
            "tests/bench/test_benchmark_protocol.py",
            "docs/bad.md",
        ],
    )

    assert findings == ["docs/bad.md"]


def test_release_version_comes_from_metadata_and_matches_repo_source(
    monkeypatch, tmp_path: Path
) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "pertura"\nversion = "9.8.7"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(release_gate, "package_version", lambda: "9.8.7")
    build_version, check = _package_version_check(tmp_path)
    assert build_version == "9.8.7"
    assert check.passed is True

    monkeypatch.setattr(release_gate, "package_version", lambda: "9.8.6")
    _, mismatch = _package_version_check(tmp_path)
    assert mismatch.passed is False
