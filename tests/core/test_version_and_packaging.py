from __future__ import annotations

import runpy
import sys
import tomllib
import types
import zipfile
from pathlib import Path

import pytest

from pertura_core import version as version_module
from pertura_runtime.claude.tools import product_tools


ROOT = Path(__file__).resolve().parents[2]


def test_package_version_uses_distribution_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        version_module,
        "version",
        lambda distribution: "9.8.7" if distribution == "pertura" else "",
    )
    assert version_module.package_version() == "9.8.7"


def test_mcp_server_uses_package_metadata_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def tool(name: str, description: str, schema: dict[str, object]):
        del name, description, schema
        return lambda function: function

    def create_sdk_mcp_server(**kwargs: object) -> dict[str, object]:
        captured.update(kwargs)
        return captured

    fake_sdk = types.SimpleNamespace(
        tool=tool, create_sdk_mcp_server=create_sdk_mcp_server
    )
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", fake_sdk)
    monkeypatch.setattr(product_tools, "package_version", lambda: "9.8.7")

    server = product_tools.create_product_mcp_server(object())

    assert server["version"] == "9.8.7"
    assert len(server["tools"]) == 5


def test_dashboard_uses_package_metadata_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("fastapi")
    from pertura_runtime import dashboard

    monkeypatch.setattr(dashboard, "package_version", lambda: "9.8.7")
    app = dashboard.create_dashboard_app(object())

    assert app.version == "9.8.7"


def test_python_and_ui_versions_are_synchronized() -> None:
    script = runpy.run_path(str(ROOT / "scripts" / "check_version_sync.py"))
    assert script["check_versions"](ROOT) == []


def test_package_data_and_sdist_manifest_cover_product_resources() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    package_data = project["tool"]["setuptools"]["package-data"]
    assert "cases/*.json" in package_data["pertura_bench"]
    assert "planner_routes.json" in package_data["pertura_workflow.capabilities"]
    assert "dashboard_static/**/*" in package_data["pertura_runtime"]
    assert "agent_bundle/bundle.json" in package_data["pertura_runtime"]
    assert "agent_bundle/.claude-plugin/plugin.json" in package_data["pertura_runtime"]
    assert "agent_bundle/skills/*/SKILL.md" in package_data["pertura_runtime"]
    assert "agent_bundle/skills/*/scripts/*.py" in package_data["pertura_runtime"]
    assert "agent_bundle/skills/*/scripts/*.R" in package_data["pertura_runtime"]
    assert "compatibility/v0.2/*.json" in package_data["pertura_core"]

    manifest = (ROOT / "MANIFEST.in").read_text(encoding="utf-8")
    for required in (
        "src/pertura_bench/cases",
        "src/pertura_runtime/dashboard_static",
        "src/pertura_runtime/agent_bundle",
        "src/pertura_core/compatibility/v0.2",
        "src/pertura_workflow/capabilities/planner_routes.json",
        "ui *.html *.json *.ts *.tsx *.css",
    ):
        assert required in manifest


def test_distribution_checker_declares_wheel_and_sdist_contracts() -> None:
    script = runpy.run_path(str(ROOT / "scripts" / "check_distribution_contents.py"))
    assert "pertura_bench/cases/capability_cases.v1.json" in script["WHEEL_REQUIRED"]
    assert (
        "pertura_workflow/capabilities/planner_routes.json" in script["WHEEL_REQUIRED"]
    )
    assert "ui/package-lock.json" in script["SDIST_REQUIRED"]
    assert "scripts/export_papalexi_guide_assets.R" in script["SDIST_REQUIRED"]
    assert "scripts/export_h5ad_benchmark_tables.py" in script["SDIST_REQUIRED"]
    assert "pertura_runtime/agent_bundle/bundle.json" in script["WHEEL_REQUIRED"]
    assert "pertura_bench/cases/skill_cases.v1.json" in script["WHEEL_REQUIRED"]
    assert (
        "pertura_runtime/agent_bundle/skills/"
        "run-replicate-aware-pseudobulk-de/scripts/run_edger_ql.R"
        in script["WHEEL_REQUIRED"]
    )
    assert (
        "pertura_runtime/agent_bundle/skills/"
        "run-design-preserving-null-calibration/scripts/"
        "run_paired_label_null.R" in script["WHEEL_REQUIRED"]
    )


def test_distribution_checker_rejects_scientific_data_inside_wheel(
    tmp_path: Path,
) -> None:
    script = runpy.run_path(str(ROOT / "scripts" / "check_distribution_contents.py"))
    wheel = tmp_path / "unsafe.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("pertura_bench/fixtures/private_fixture.h5ad", b"not-data")

    verdict = script["check_distribution"](wheel)

    assert verdict["passed"] is False
    assert "pertura_bench/fixtures/private_fixture.h5ad" in verdict["forbidden"]


def test_ci_separates_product_legacy_and_real_synthetic_execution() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert any(
        str(marker).startswith("legacy:")
        for marker in project["tool"]["pytest"]["ini_options"]["markers"]
    )

    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "python -m pytest -q" in workflow
    assert "python -m pytest -c legacy/pytest.ini legacy/tests" in workflow
    assert "PYTHONPATH: legacy/src:src" in workflow
    assert "run-matrix --tier synthetic_ci --repo ." in workflow
    assert "0.2.0a4" not in workflow


def test_distribution_checker_rejects_retired_authority_packages(
    tmp_path: Path,
) -> None:
    script = runpy.run_path(str(ROOT / "scripts" / "check_distribution_contents.py"))
    wheel = tmp_path / "legacy.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("pertura_gate/__init__.py", b"retired")
        archive.writestr(
            "pertura_runtime/retired.py",
            b"from pertura_" + b"gate import Evidence" + b"Registry",
        )

    verdict = script["check_distribution"](wheel)

    assert verdict["passed"] is False
    assert "pertura_gate/__init__.py" in verdict["forbidden"]
    assert "pertura_runtime/retired.py" in verdict["forbidden"]


def test_distribution_contract_excludes_legacy_from_sdist() -> None:
    script = runpy.run_path(str(ROOT / "scripts" / "check_distribution_contents.py"))

    assert not any("legacy/" in path for path in script["SDIST_REQUIRED"])
