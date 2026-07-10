from __future__ import annotations

import csv
from pathlib import Path

from pertura_runtime.claude.workspace import ClaudeRunWorkspace
from pertura_runtime.product import PerturaProductRuntime


def _write_fixture(root: Path, *, collision: bool = False) -> None:
    root.mkdir(parents=True, exist_ok=True)
    rna = ["AAAA-1", "AAAC-1", "AACA-1", "AACC-1", "ACAA-1", "ACAC-1", "ACCA-1", "ACCC-1"]
    if collision:
        rna[1] = "AAAA-2"
    (root / "rna_barcodes.csv").write_text("barcode\n" + "\n".join(rna) + "\n", encoding="utf-8")
    with (root / "guide_counts.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["barcode", "g1", "g2"])
        for index, barcode in enumerate(rna):
            writer.writerow([barcode, 12 if index < 4 else 0, 0 if index < 4 else 11])
    (root / "guide_map.csv").write_text("guide,target\ng1,KLF1\ng2,NTC\n", encoding="utf-8")
    with (root / "raw_guide_counts.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["barcode", "g1", "g2"])
        for barcode in rna:
            writer.writerow([barcode, 1, 1])
        writer.writerow(["GGGG-1", 1, 0])
        writer.writerow(["GGGA-1", 0, 1])


def test_guide_assignment_detects_mixture_ambient_moi_and_publishes_manifest(monkeypatch, tmp_path: Path) -> None:
    source = tmp_path / "screen"
    _write_fixture(source)
    monkeypatch.setenv("PERTURA_AUTHORITY_ROOT", str(tmp_path / "authority"))
    workspace = ClaudeRunWorkspace.create(root=tmp_path / "runs", input_source=source, run_id="guide-qc")
    runtime = PerturaProductRuntime(workspace)
    try:
        contract = runtime.inspect_dataset(confirmations={"control": "NTC", "guide": "guide_counts.csv", "target": "guide_map.csv"})
        result = runtime.run_diagnostic(
            "diagnostic.guide_assignment.v1",
            contract_id=contract["contract_id"],
            parameters={
                "guide_counts_path": "guide_counts.csv",
                "rna_barcodes_path": "rna_barcodes.csv",
                "guide_map_path": "guide_map.csv",
                "raw_guide_counts_path": "raw_guide_counts.csv",
                "design_moi": "low",
            },
        )
        assert result["status"] in {"screen_passed", "caution"}
        assert result["receipt_id"].startswith("receipt_")
        assert len(result["output_paths"]) == 3
        assert all((workspace.root / path).exists() for path in result["output_paths"])
    finally:
        runtime.close()


def test_barcode_suffix_collision_blocks_assignment(monkeypatch, tmp_path: Path) -> None:
    source = tmp_path / "screen"
    _write_fixture(source, collision=True)
    monkeypatch.setenv("PERTURA_AUTHORITY_ROOT", str(tmp_path / "authority"))
    workspace = ClaudeRunWorkspace.create(root=tmp_path / "runs", input_source=source, run_id="collision")
    runtime = PerturaProductRuntime(workspace)
    try:
        contract = runtime.inspect_dataset()
        result = runtime.run_diagnostic(
            "diagnostic.guide_assignment.v1",
            contract_id=contract["contract_id"],
            parameters={
                "guide_counts_path": "guide_counts.csv",
                "rna_barcodes_path": "rna_barcodes.csv",
                "guide_map_path": "guide_map.csv",
            },
        )
        assert result["status"] == "blocked"
        assert any("collisions" in blocker for blocker in result["blockers"])
    finally:
        runtime.close()
