from __future__ import annotations

import json
from pathlib import Path

from pertura_bench.agent_models import AgentNarrativeScore, narrative_passes
from pertura_bench.real_execution import (
    _evaluate_metric_references,
    load_design_confirmation_catalog,
    load_metric_reference_catalog,
    load_real_parameter_catalog,
)
from pertura_bench.server_plan import build_server_plan
from pertura_bench.capability_bench import benchmark_specs


def test_execution_success_without_reference_is_not_scientifically_complete() -> None:
    evaluation = _evaluate_metric_references(
        {"status": "completed", "metrics": {"n_rows": 12}, "output_hashes": {}},
        dataset_id="fixture",
        capability_id="fixture.capability.v1",
        capability_version="0.1.0",
        catalog={"datasets": {}},
        catalog_hash="sha256:" + "1" * 64,
    )
    assert evaluation["status"] == "not_available"
    assert evaluation["continuous_metrics"] == {}
    assert evaluation["reference_hashes"]["metric_reference_catalog"].startswith(
        "sha256:"
    )


def test_frozen_and_reported_only_metrics_are_distinct() -> None:
    base = {
        "datasets": {
            "fixture": {
                "capabilities": {
                    "fixture.capability.v1@0.1.0": {
                        "required_outputs": ["table.csv"],
                        "reported_metrics": ["ari"],
                        "metrics": [],
                        "reference_hashes": {},
                    }
                }
            }
        }
    }
    reported = _evaluate_metric_references(
        {"metrics": {"ari": 0.91}},
        dataset_id="fixture",
        capability_id="fixture.capability.v1",
        capability_version="0.1.0",
        catalog=base,
        catalog_hash="sha256:" + "2" * 64,
    )
    assert reported["status"] == "reported_only"
    assert reported["continuous_metrics"] == {"ari": 0.91}
    assert reported["required_outputs"] == ("table.csv",)

    frozen = json.loads(json.dumps(base))
    entry = frozen["datasets"]["fixture"]["capabilities"][
        "fixture.capability.v1@0.1.0"
    ]
    entry["reported_metrics"] = []
    entry["metrics"] = [
        {
            "name": "ari",
            "result_metric": "ari",
            "reference": 0.95,
            "comparison": "absolute_error",
            "tolerance": 0.01,
        }
    ]
    failed = _evaluate_metric_references(
        {"metrics": {"ari": 0.91}},
        dataset_id="fixture",
        capability_id="fixture.capability.v1",
        capability_version="0.1.0",
        catalog=frozen,
        catalog_hash="sha256:" + "3" * 64,
    )
    assert failed["status"] == "failed"
    assert failed["continuous_metrics"]["ari__error"] > 0.01


def test_external_catalog_hashes_bind_server_plan(tmp_path: Path) -> None:
    parameters, _ = load_real_parameter_catalog()
    design, _ = load_design_confirmation_catalog()
    metrics, _ = load_metric_reference_catalog()
    parameter_path = tmp_path / "parameters.json"
    design_path = tmp_path / "design.json"
    metric_path = tmp_path / "metrics.json"
    for path, payload in (
        (parameter_path, parameters),
        (design_path, design),
        (metric_path, metrics),
    ):
        path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")

    root = Path(__file__).resolve().parents[2]
    plan = build_server_plan(
        benchmark_specs(),
        root,
        parameter_catalog_path=parameter_path,
        design_confirmations_path=design_path,
        metric_reference_catalog_path=metric_path,
    )
    assert plan.checkpoint_binding["parameter_catalog_hash"].startswith("sha256:")
    assert plan.checkpoint_binding["design_confirmation_catalog_hash"].startswith(
        "sha256:"
    )
    assert plan.checkpoint_binding["metric_reference_catalog_hash"].startswith(
        "sha256:"
    )
    assert len([job for job in plan.jobs if job["kind"] == "agent_workflow"]) == 48


def test_agent_narrative_automatic_failure_overrides_high_scores() -> None:
    score = AgentNarrativeScore(
        scientific_completeness=4,
        clarity=4,
        limitations_uncertainty=4,
        actionability=4,
        rationale="Fluent but invalid.",
        automatic_failures=("cell_as_independent_replicate",),
    )
    assert narrative_passes(score) is False
