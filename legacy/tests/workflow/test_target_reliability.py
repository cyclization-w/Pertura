import csv
import json
from pathlib import Path

from pertura_gate.evidence.execution_ledger import ledger_contains_execution_hash
from pertura_workflow.runners.target_reliability import run_target_reliability_audit


def _write_fixture(root: Path, *, confounded: bool = False, detectable: bool = True) -> None:
    root.mkdir(parents=True, exist_ok=True)
    metadata = root / "metadata.csv"
    expression = root / "expression.csv"
    meta_rows = []
    expr_rows = []
    for index in range(40):
        batch = "b1" if confounded else ("b1" if index % 2 == 0 else "b2")
        meta_rows.append({"cell_id": f"t{index}", "perturbation_uid": "uid_target", "guide": "g1" if index < 20 else "g2", "batch": batch, "donor": "d1" if index % 2 == 0 else "d2"})
        expr_rows.append({"cell_id": f"t{index}", "GENE": 0.2 if detectable else 0.0})
    for index in range(40):
        batch = "b2" if confounded else ("b1" if index % 2 == 0 else "b2")
        meta_rows.append({"cell_id": f"c{index}", "perturbation_uid": "uid_control", "guide": "nt1", "batch": batch, "donor": "d1" if index % 2 == 0 else "d2"})
        expr_rows.append({"cell_id": f"c{index}", "GENE": 2.0 if detectable else (1.0 if index == 0 else 0.0)})
    with metadata.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(meta_rows[0])); writer.writeheader(); writer.writerows(meta_rows)
    with expression.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(expr_rows[0])); writer.writeheader(); writer.writerows(expr_rows)


def _run(root: Path, **overrides):
    return run_target_reliability_audit(
        root,
        expression_csv="expression.csv",
        metadata_csv="metadata.csv",
        target_uid="uid_target",
        control_uid="uid_control",
        target="GENE",
        target_gene="GENE",
        layer="log1p",
        guide_column="guide",
        batch_column="batch",
        replicate_column="donor",
        **overrides,
    )


def test_target_reliability_reports_eligible_multiguide_target_and_trusted_run(tmp_path: Path) -> None:
    _write_fixture(tmp_path)
    result = _run(tmp_path)
    assert result["status"] == "eligible"
    assert result["direction_supported"] is True
    assert result["guide_concordance"] == 1.0
    assert result["eligibility"]["target_engagement_interpretation"] is True
    assert Path(result["path"]).exists()
    assert ledger_contains_execution_hash(
        tmp_path / "artifacts" / "execution_ledger.jsonl",
        result["execution_hash"],
        method="target_reliability_audit",
        source_sha256=result["output_hashes"]["target_reliability"],
    )


def test_target_reliability_blocks_dropout_ambiguous_target(tmp_path: Path) -> None:
    _write_fixture(tmp_path, detectable=False)
    result = _run(tmp_path)
    assert result["status"] == "blocked"
    assert result["eligibility"]["measured_effect_analysis"] is False
    assert "target_gene_low_detectability" in {item["code"] for item in result["findings"]}


def test_target_reliability_blocks_batch_nested_contrast(tmp_path: Path) -> None:
    _write_fixture(tmp_path, confounded=True)
    result = _run(tmp_path)
    assert result["status"] == "blocked"
    assert result["batch_coverage"]["has_shared_levels"] is False
    assert "batch_perturbation_confounding" in {item["code"] for item in result["findings"]}


def test_persisted_target_reliability_is_structured_json(tmp_path: Path) -> None:
    _write_fixture(tmp_path)
    result = _run(tmp_path)
    payload = json.loads(Path(result["path"]).read_text(encoding="utf-8"))
    assert payload["schema_version"] == "pertura-target-reliability-v1"
    assert payload["execution_hash"] == result["execution_hash"]
