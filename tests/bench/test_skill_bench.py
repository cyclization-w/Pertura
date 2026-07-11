from __future__ import annotations

from pathlib import Path

from pertura_bench.skill_bench import (
    skill_benchmark_matrix,
    validate_skill_bundle_static,
    validate_skill_cases,
)


ROOT = Path(__file__).resolve().parents[2]


def test_skill_case_catalog_has_locked_behavior_mix() -> None:
    verdict = validate_skill_cases()

    assert verdict["ok"] is True
    assert verdict["case_count"] == 24
    assert verdict["counts"] == {
        "single_positive": 12,
        "multi_positive": 4,
        "negative": 8,
    }


def test_skill_bundle_static_validation_passes() -> None:
    verdict = validate_skill_bundle_static(ROOT)

    assert verdict["ok"] is True
    assert verdict["case_count"] == 24
    assert verdict["bundle_hash"].startswith("sha256:")
    assert verdict["problems"] == []


def test_skill_matrix_does_not_fabricate_model_behavior_readiness() -> None:
    matrix = skill_benchmark_matrix(ROOT)

    assert matrix["skill_bundle_ready"] is True
    assert matrix["claude_skill_adapter_ready"] is True
    assert matrix["openai_adapter_ready"] is False
    assert matrix["skill_behavior_benchmark_ready"] is False
    assert matrix["behavior_status"] == "not_run_environment_missing"
