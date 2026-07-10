from __future__ import annotations

import csv
import json
from pathlib import Path

from pertura_runtime.claude.workspace import ClaudeRunWorkspace
from pertura_runtime.product import PerturaProductRuntime


def _write_fixture(root: Path) -> None:
    root.mkdir()
    cells = []
    metadata = []
    conditions = ("target", "baseline", "ntc_a", "ntc_b")
    for replicate in ("r1", "r2", "r3"):
        for condition in conditions:
            for index in range(4):
                cell = f"{replicate}_{condition}_{index}"
                cells.append(cell)
                metadata.append((cell, condition, replicate))
    with (root / "counts.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["gene", *cells])
        writer.writerow(["G1", *(12 if "target" in cell else 3 for cell in cells)])
        writer.writerow(["G2", *(5 for _ in cells)])
        writer.writerow(["G3", *(2 if "ntc_a" in cell else 3 for cell in cells)])
    with (root / "metadata.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["cell_id", "condition", "replicate"])
        writer.writerows(metadata)


def test_calibration_permutes_replicate_labels_never_cells(monkeypatch, tmp_path: Path) -> None:
    source = tmp_path / "screen"
    _write_fixture(source)
    monkeypatch.setenv("PERTURA_AUTHORITY_ROOT", str(tmp_path / "authority"))
    workspace = ClaudeRunWorkspace.create(root=tmp_path / "runs", input_source=source, run_id="calibration")
    runtime = PerturaProductRuntime(workspace)
    try:
        contract = runtime.inspect_dataset(confirmations={"control": ["baseline", "ntc_a", "ntc_b"], "replicate": "replicate"})
        result = runtime.run_analysis(
            "replicate null calibration",
            capability_id="calibration.replicate_null.v1",
            contract_id=contract["contract_id"],
            parameters={
                "counts_path": "counts.csv",
                "metadata_path": "metadata.csv",
                "target_condition": "target",
                "baseline_condition": "baseline",
                "negative_control_conditions": ["ntc_a", "ntc_b"],
                "permutations": 50,
            },
        )
        assert result["status"] == "completed"
        payload = json.loads((workspace.root / result["output_paths"][0]).read_text(encoding="utf-8"))
        assert payload["label_permutation"]["permutation_unit"] == "replicate_label"
        assert payload["cell_label_permutation_performed"] is False
    finally:
        runtime.close()
