from __future__ import annotations

import json
from pathlib import Path

from pertura_bench.stage_benchmark import (
    STAGE_BENCH_CASE_IDS,
    run_stage_benchmark_case,
    run_stage_benchmark_suite,
    write_stage_benchmark_summary,
)


def test_stage_benchmark_guide_assignment_is_eligibility_only(tmp_path: Path) -> None:
    result = run_stage_benchmark_case("guide_assignment_eligibility_only", root=tmp_path)

    assert result.completion is True
    assert result.stage_id == "guide_assignment"
    assert result.artifact_kinds == ["guide_assignment"]
    assert result.decision_strengths == []
    assert all(result.metrics.values())


def test_stage_benchmark_cell_state_reference_is_context_only(tmp_path: Path) -> None:
    result = run_stage_benchmark_case("cell_state_reference_context_only", root=tmp_path)

    assert result.completion is True
    assert result.stage_id == "cell_state_reference"
    assert result.artifact_kinds == ["cell_state_reference"]
    assert result.decision_strengths == ["observation"]
    assert all(result.metrics.values())



def test_stage_benchmark_composition_effect_caps_fate_claim(tmp_path: Path) -> None:
    result = run_stage_benchmark_case("composition_effect_association_only", root=tmp_path)

    assert result.completion is True
    assert result.stage_id == "composition_effect"
    assert "composition_effect" in result.artifact_kinds
    assert result.decision_strengths == ["measured_association"]
    assert result.scope_fits == ["exact"]
    assert all(result.metrics.values())
def test_stage_benchmark_measured_de_caps_mechanism(tmp_path: Path) -> None:
    result = run_stage_benchmark_case("measured_de_association_only", root=tmp_path)

    assert result.completion is True
    assert result.stage_id == "measured_de"
    assert "measured_de" in result.artifact_kinds
    assert result.decision_strengths == ["measured_association"]
    assert result.scope_fits == ["exact"]
    assert all(result.metrics.values())


def test_stage_benchmark_claim_report_uses_decision_surface(tmp_path: Path) -> None:
    result = run_stage_benchmark_case("claim_report_decision_surface", root=tmp_path)

    assert result.completion is True
    assert result.stage_id == "claim_report"
    assert result.report_path is not None
    assert Path(result.report_path).exists()
    assert result.decision_strengths == ["measured_association"]
    assert all(result.metrics.values())


def test_stage_benchmark_summary_writes_markdown_and_json(tmp_path: Path) -> None:
    results = run_stage_benchmark_suite(root=tmp_path / "runs")
    md_path, json_path = write_stage_benchmark_summary(results, output_dir=tmp_path / "summary")

    assert len(results) == len(STAGE_BENCH_CASE_IDS)
    assert md_path.exists()
    assert json_path.exists()
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "pertura-stage-benchmark-v1"
    assert len(payload["results"]) == len(STAGE_BENCH_CASE_IDS)
    assert all(row["completion"] for row in payload["results"])
    assert "cell_state_reference" in md_path.read_text(encoding="utf-8")
