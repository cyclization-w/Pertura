from __future__ import annotations

from pathlib import Path

from pertura_bench.release_gate import audit_v020


def test_release_audit_passes_code_invariants_and_reports_external_blockers(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("PERTURA_CACHE_DIR", str(tmp_path / "empty-cache"))
    root = Path(__file__).resolve().parents[2]
    audit = audit_v020(root)
    checks = {item["check_id"]: item for item in audit["checks"]}
    assert checks["default_domain_tool_count"]["passed"] is True
    assert checks["legacy_approximation_not_trusted"]["passed"] is True
    assert checks["dashboard_production_bundle"]["passed"] is True
    assert audit["schema_version"] == "pertura-release-audit-v2"
    assert audit["code_ready"] is True
    assert audit["local_environment_ready"] is False
    assert audit["release_ready"] is False
    assert "edger_environment" in audit["blocking_checks"]
    assert "validated_target_profiles" in audit["blocking_checks"]
