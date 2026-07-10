from __future__ import annotations

import json
from pathlib import Path

from pertura_runtime.product_cli import main


def test_cli_inspect_then_diagnostic_uses_persistent_authority(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("PERTURA_AUTHORITY_ROOT", str(tmp_path / "authority"))
    project = tmp_path / "project"
    project.mkdir()
    (project / "expression.csv").write_text("cell_id,replicate,G1\nc1,r1,1\nc2,r2,2\n", encoding="utf-8")
    inspect_out = tmp_path / "inspect.json"
    diagnostic_out = tmp_path / "diagnostic.json"

    assert main(["inspect", str(project), "--out", str(inspect_out)]) == 0
    contract = json.loads(inspect_out.read_text(encoding="utf-8"))
    assert contract["contract_id"].startswith("contract_")

    assert main([
        "diagnostic",
        "diagnostic.contract_integrity.v1",
        str(project),
        "--contract-id",
        contract["contract_id"],
        "--out",
        str(diagnostic_out),
    ]) == 0
    diagnostic = json.loads(diagnostic_out.read_text(encoding="utf-8"))
    assert diagnostic["receipt_id"].startswith("receipt_")
    assert not list((tmp_path / "authority").glob("current-*/signing.key"))


def test_cli_migrate_run_never_creates_trusted_receipt(tmp_path: Path) -> None:
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    (legacy / "manifest.json").write_text("{}\n", encoding="utf-8")
    out = tmp_path / "migrated.json"
    assert main(["migrate-run", str(legacy), "--workspace", str(tmp_path), "--out", str(out)]) == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["status"] == "legacy_unverified"
    assert payload["receipt_id"] is None


def test_cli_capability_list_reports_five_phase_kernel(capsys) -> None:
    assert main(["capabilities", "list"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert any(item["capability_id"] == "de.pseudobulk.edger.v1" for item in payload["capabilities"])
