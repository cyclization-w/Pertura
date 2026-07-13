from __future__ import annotations

import json
from pathlib import Path

from pertura_bench.p21_classic_workflow import run_p21_case, run_p21_suite, write_p21_summary


def test_p21_strict_classic_case_downgrades_mechanism_to_measured_association(tmp_path: Path) -> None:
    result = run_p21_case("strict_measured_association", root=tmp_path)

    assert result.completion is True
    assert result.decision_strengths == ["measured_association"]
    assert result.linked_claims == 1
    assert result.unlinked_claims == 0
    assert result.runner_steps == []


def test_p21_basic_runner_case_records_target_qc_and_de_steps(tmp_path: Path) -> None:
    result = run_p21_case("basic_runners_measured_association", root=tmp_path)

    assert result.completion is True
    assert result.decision_strengths == ["measured_association"]
    assert "run_basic_target_qc" in result.runner_steps
    assert "run_basic_de_for_registered_contrast" in result.runner_steps


def test_p21_candidate_gap_case_keeps_unlinked_claim_out_of_decisions(tmp_path: Path) -> None:
    result = run_p21_case("candidate_claim_gap", root=tmp_path)

    assert result.completion is True
    assert result.decision_strengths == ["measured_association"]
    assert result.linked_claims == 1
    assert result.unlinked_claims == 1
    report = Path(result.report_path).read_text(encoding="utf-8")
    assert "Candidate Claim Gaps" in report
    assert "unlinked_dusp9_claim" in report


def test_p21_partial_success_case_reports_gap_without_decision(tmp_path: Path) -> None:
    result = run_p21_case("partial_success_missing_manifest", root=tmp_path)

    assert result.completion is True
    assert result.decision_strengths == []
    assert result.linked_claims == 0
    assert result.report_path is None


def test_p21_summary_writes_markdown_and_json(tmp_path: Path) -> None:
    results = run_p21_suite(root=tmp_path / "runs")
    md_path, json_path = write_p21_summary(results, output_dir=tmp_path / "summary")

    assert md_path.exists()
    assert json_path.exists()
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "pertura-p21-classic-workflow-v1"
    assert len(payload["results"]) == 4
    assert all(row["completion"] for row in payload["results"])
    assert "classic workflow" in md_path.read_text(encoding="utf-8").lower()