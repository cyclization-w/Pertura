from __future__ import annotations

from pathlib import Path

from pertura_runtime.claude.workspace import ClaudeRunWorkspace
from pertura_runtime.product import PerturaProductRuntime


def test_product_runtime_inspect_diagnostic_receipt_and_report(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("PERTURA_AUTHORITY_ROOT", str(tmp_path / "authority-outside-workspace"))
    source = tmp_path / "expression.csv"
    source.write_text(
        "cell_id,replicate,guide,target,G1,G2\n"
        "c1,r1,g1,KLF1,2,0\n"
        "c2,r2,NTC,NTC,0,1\n",
        encoding="utf-8",
    )
    workspace = ClaudeRunWorkspace.create(root=tmp_path / "runs", input_source=source, run_id="product")
    runtime = PerturaProductRuntime(workspace)
    try:
        inspected = runtime.inspect_dataset()
        assert inspected["format"] == "csv"
        assert inspected["identity_status"]["replicate"] == "observed"
        assert "expression_layer" in inspected["unresolved_fields"]

        diagnostic = runtime.run_diagnostic(
            "diagnostic.contract_integrity.v1",
            contract_id=inspected["contract_id"],
        )
        assert diagnostic["status"] == "blocked"
        assert diagnostic["receipt_id"].startswith("receipt_")

        report = runtime.finalize_report()
        assert report["root_digest"].startswith("sha256:")
        assert (workspace.reports_dir / "capability_report.md").exists()
        assert list((tmp_path / "authority-outside-workspace").glob("product-*/authority.sqlite3"))
        assert not (workspace.root / "authority.sqlite3").exists()
    finally:
        runtime.close()
