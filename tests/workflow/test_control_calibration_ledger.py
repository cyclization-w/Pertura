import csv
from pathlib import Path

from pertura_gate.evidence.execution_ledger import ledger_contains_execution_hash
from pertura_workflow.runners.control_calibration import (
    run_label_permutation_null,
    run_ntc_vs_ntc_calibration,
)


def _inputs(root: Path) -> None:
    metadata = [
        {"cell_id": f"c{i}", "perturbation_uid": "control" if i < 8 else "target"}
        for i in range(16)
    ]
    expression = [
        {"cell_id": f"c{i}", "G1": 1 + (i % 3) * 0.1, "G2": 2 + (i % 2) * 0.1}
        for i in range(16)
    ]
    with (root / "metadata.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(metadata[0])); writer.writeheader(); writer.writerows(metadata)
    with (root / "expression.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(expression[0])); writer.writeheader(); writer.writerows(expression)


def test_control_calibration_runners_write_output_bound_ledger_entries(tmp_path: Path) -> None:
    _inputs(tmp_path)
    ntc = run_ntc_vs_ntc_calibration(tmp_path, expression_csv="expression.csv", metadata_csv="metadata.csv", control_uid="control", layer="log1p")
    permutation = run_label_permutation_null(tmp_path, expression_csv="expression.csv", metadata_csv="metadata.csv", contrast_uid="target_vs_control", left_uid="target", baseline_uid="control", layer="log1p")
    ledger = tmp_path / "artifacts" / "execution_ledger.jsonl"
    for result in (ntc, permutation):
        assert Path(result["execution_ledger_path"]) == ledger
        assert ledger_contains_execution_hash(
            ledger,
            result["execution_hash"],
            method=result["method"],
            source_sha256=result["output_hashes"]["calibration"],
        )
