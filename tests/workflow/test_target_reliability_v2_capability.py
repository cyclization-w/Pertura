from __future__ import annotations

import csv
from pathlib import Path

from pertura_runtime.claude.workspace import ClaudeRunWorkspace
from pertura_runtime.product import PerturaProductRuntime


def _write_fixture(root: Path, *, control_detected: bool = True) -> None:
    root.mkdir(parents=True, exist_ok=True)
    with (root / "expression.csv").open("w", newline="", encoding="utf-8") as expression, (root / "metadata.csv").open("w", newline="", encoding="utf-8") as metadata:
        expr = csv.writer(expression)
        meta = csv.writer(metadata)
        expr.writerow(["cell_id", "KLF1", "SIG1", "SIG2"])
        meta.writerow(["cell_id", "perturbation_uid", "guide", "batch", "replicate", "mixscape_class"])
        for replicate in ("r1", "r2", "r3"):
            for index in range(12):
                cell = f"t_{replicate}_{index}"
                expr.writerow([cell, 1, 1, 2])
                meta.writerow([cell, "target:KLF1", f"g{index % 3 + 1}", "b1", replicate, "KO"])
            for index in range(12):
                cell = f"c_{replicate}_{index}"
                expr.writerow([cell, 8 if control_detected else 0, 4, 4])
                meta.writerow([cell, "control:NTC", "NTC", "b1", replicate, "NT"])


def test_target_reliability_v2_reports_bootstrap_guides_loo_and_responder(monkeypatch, tmp_path: Path) -> None:
    source = tmp_path / "screen"
    _write_fixture(source)
    monkeypatch.setenv("PERTURA_AUTHORITY_ROOT", str(tmp_path / "authority"))
    workspace = ClaudeRunWorkspace.create(root=tmp_path / "runs", input_source=source, run_id="target-v2")
    runtime = PerturaProductRuntime(workspace)
    try:
        contract = runtime.inspect_dataset(confirmations={"control": "control:NTC", "target": "target:KLF1", "replicate": "replicate"})
        result = runtime.run_diagnostic(
            "target.reliability.v2",
            contract_id=contract["contract_id"],
            parameters={
                "expression_path": "expression.csv",
                "metadata_path": "metadata.csv",
                "target_uid": "target:KLF1",
                "control_uid": "control:NTC",
                "target_gene": "KLF1",
                "mixscape_class_column": "mixscape_class",
                "signature_genes": ["SIG1", "SIG2"],
                "bootstrap_iterations": 100,
            },
        )
        assert result["status"] == "caution"
        assert result["receipt_id"].startswith("receipt_")
        assert any("not benchmark-validated" in item for item in result["cautions"])
        assert (workspace.root / result["output_paths"][0]).exists()
    finally:
        runtime.close()


def test_low_detectability_without_signature_blocks_target_reliability(monkeypatch, tmp_path: Path) -> None:
    source = tmp_path / "screen"
    _write_fixture(source, control_detected=False)
    monkeypatch.setenv("PERTURA_AUTHORITY_ROOT", str(tmp_path / "authority"))
    workspace = ClaudeRunWorkspace.create(root=tmp_path / "runs", input_source=source, run_id="target-low")
    runtime = PerturaProductRuntime(workspace)
    try:
        contract = runtime.inspect_dataset()
        result = runtime.run_diagnostic(
            "target.reliability.v2",
            contract_id=contract["contract_id"],
            parameters={
                "expression_path": "expression.csv",
                "metadata_path": "metadata.csv",
                "target_uid": "target:KLF1",
                "control_uid": "control:NTC",
                "target_gene": "KLF1",
                "bootstrap_iterations": 20,
            },
        )
        assert result["status"] == "blocked"
        assert any("detectability" in blocker for blocker in result["blockers"])
    finally:
        runtime.close()
