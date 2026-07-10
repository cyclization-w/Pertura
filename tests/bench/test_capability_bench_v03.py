from __future__ import annotations

import pytest
from pydantic import ValidationError

from pertura_bench.capability_bench import (
    CANDIDATE_CAPABILITIES,
    benchmark_specs,
    coverage_matrix,
    run_protocol_cases,
    server_benchmark_plan,
    validate_cases,
)
from pertura_bench.capability_models import CapabilityBenchmarkCase


def test_candidate_matrix_is_code_ready_but_not_release_ready() -> None:
    validation = validate_cases()
    matrix = coverage_matrix()
    assert validation["ok"] is True
    assert validation["candidate_count"] == 20
    assert len(CANDIDATE_CAPABILITIES) == 20
    assert len(matrix.entries) == 20
    assert matrix.code_ready is True
    assert matrix.local_fixture_ready is True
    assert matrix.real_benchmark_ready is False
    assert matrix.release_ready is False
    assert all(len(spec.cases) == 6 for spec in benchmark_specs())


def test_protocol_verdicts_are_deterministic_and_do_not_claim_real_data() -> None:
    first = run_protocol_cases("guide.assignment.nb_mixture.v1")
    second = run_protocol_cases("guide.assignment.nb_mixture.v1")
    assert first == second
    assert {item["outcome"] for item in first} == {"passed"}
    real = run_protocol_cases("guide.assignment.nb_mixture.v1", tier="full_dataset")
    assert {item["outcome"] for item in real} == {"not_available"}


def test_server_plan_is_scheduler_neutral() -> None:
    plan = server_benchmark_plan()
    assert plan.scheduler == "neutral"
    assert set(plan.datasets) == {
        "replogle_k562_essential_2022",
        "papalexi_thp1_eccite",
        "norman_k562_crispra_2019",
        "kang18_8vs8_pbmc",
    }
    assert all("resources" in job and "command" in job for job in plan.jobs)


def test_capability_case_rejects_absolute_fixture_identity() -> None:
    with pytest.raises(ValidationError, match="absolute paths"):
        CapabilityBenchmarkCase(
            capability_id="guide.integrity.v1",
            capability_version="0.1.0",
            tier="synthetic_ci",
            scenario="bad",
            fixture_id="C:/private/fixture",
        )
