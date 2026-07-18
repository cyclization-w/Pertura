from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from pertura_bench.agent_judge import project_judge_answer
from pertura_bench.agent_server_execution import evaluate_server_agent_hard_gates
from pertura_bench.metric_evaluators import evaluate_artifact_metrics
from pertura_bench.models import BenchmarkSubsetSpec
from pertura_bench.operations import _select_v2_cells
from pertura_bench.resource_evidence import (
    enforce_runtime_resource_budget,
    load_resource_evidence,
    observe_runtime_resources,
    validate_resource_request,
)
from pertura_bench.references import (
    freeze_reference,
    generate_reference,
    validate_reference,
)
from pertura_bench.models import BenchmarkSubsetLock
from pertura_core.hashing import file_sha256


def test_subset_v2_checks_independent_units_per_arm() -> None:
    obs = pd.DataFrame(
        {
            "target": ["A", "A", "B", "B", "NTC", "NTC"],
            "replicate": ["r1", "r2", "r1", "r1", "r3", "r4"],
        }
    )
    spec = BenchmarkSubsetSpec(
        dataset_id="dataset",
        source_lock_hash="sha256:" + "1" * 64,
        split_id="evaluation-v1",
        split="evaluation",
        unit_id_column="replicate",
        group_column="target",
        control_selector={"column": "target", "op": "eq", "value": "NTC"},
        selected_groups=("A", "B"),
        selected_control_units=("r3", "r4"),
        minimum_units_per_arm=2,
    )
    import numpy as np

    with pytest.raises(ValueError, match="B"):
        _select_v2_cells(obs, spec, np.random.default_rng(1729))


def test_metric_evaluators_compute_calibration_cluster_null_and_effect(tmp_path: Path) -> None:
    observed = pd.DataFrame(
        {
            "id": ["a", "b", "c", "d"],
            "probability": [0.1, 0.8, 0.7, 0.2],
            "cluster": ["x", "y", "y", "x"],
            "pvalue": [0.8, 0.01, 0.03, 0.7],
            "effect": [0.0, 1.1, 0.9, 0.0],
        }
    )
    reference = pd.DataFrame(
        {
            "id": ["a", "b", "c", "d"],
            "label": [0, 1, 1, 0],
            "cluster": ["x", "y", "y", "x"],
            "signal": [False, True, True, False],
            "effect": [0.0, 1.0, 1.0, 0.0],
        }
    )
    observed_path = tmp_path / "observed.csv"
    observed.to_csv(observed_path, index=False)
    reference_root = tmp_path / "references"
    reference_root.mkdir()
    reference_path = reference_root / "reference.csv"
    reference.to_csv(reference_path, index=False)
    common = {
        "observed_output": "observed.csv",
        "reference_path": "reference.csv",
        "reference_sha256": file_sha256(reference_path),
        "key_columns": ["id"],
    }
    evaluators = [
        common | {"evaluator_id": "posterior", "type": "posterior_calibration", "probability_column": "probability", "reference_label_column": "label", "maximum_brier": 0.1, "maximum_ece": 0.3},
        common | {"evaluator_id": "clusters", "type": "cluster_agreement", "observed_label_column": "cluster", "reference_label_column": "cluster", "minimum_ari": 0.9},
        common | {"evaluator_id": "null", "type": "null_calibration", "pvalue_column": "pvalue", "reference_signal_column": "signal", "maximum_type_i_error": 0.05, "minimum_power": 0.9, "maximum_fdr": 0.05},
        common | {"evaluator_id": "effect", "type": "effect_error", "observed_value_column": "effect", "reference_value_column": "effect", "maximum_mae": 0.1, "maximum_rmse": 0.1},
    ]
    verdict = evaluate_artifact_metrics(
        {"output_paths": ["observed.csv"]},
        evaluators,
        output_root=tmp_path,
        reference_root=reference_root,
    )
    assert all(verdict["comparisons"])
    assert verdict["continuous_metrics"]["clusters.ari"] == pytest.approx(1.0)
    assert len(verdict["metric_bindings"]) == 4
    assert all(
        binding["observed_artifact_hash"] == file_sha256(observed_path)
        and binding["reference_hash"] == file_sha256(reference_path)
        for binding in verdict["metric_bindings"]
    )


def test_judge_projection_removes_authority_and_provider_fingerprints() -> None:
    projection = project_judge_answer(
        {
            "headline": "Bounded result",
            "findings": [{"text": "Candidate estimate", "ceiling": "candidate"}],
            "limitations": ["Exploratory"],
            "artifact_refs": ["effect.csv"],
            "claim_authority": True,
            "result_ids": ["result-secret"],
            "provider": "claude",
            "condition": "pertura_full",
        }
    ).model_dump(mode="json")
    rendered = json.dumps(projection)
    assert "Candidate estimate" in rendered
    for forbidden in ("claim_authority", "result-secret", "claude", "pertura_full", "ceiling"):
        assert forbidden not in rendered


def test_scheduler_string_without_evidence_cannot_pass_resource_gate(tmp_path: Path) -> None:
    # The other gates are intentionally irrelevant; this regression targets the
    # resource claim, which must be backed by scheduler/cgroup evidence.
    gates = evaluate_server_agent_hard_gates(
        {
            "case_id": "case",
            "dataset_id": "dataset",
            "expected_capability_dag": [],
            "allowed_auxiliary_capabilities": [],
            "benchmark_track": "primary",
            "expected_turns": 0,
            "expected_statuses": ["completed"],
            "max_memory_gb": 4.0,
            "timeout_seconds": 1800,
        },
        condition="prompt_only",
        final=None,
        turns=(),
        authority={"committed": []},
        registered_asset_roles=set(),
        output_files=(),
        runtime_options=type("Options", (), {"domain_tools_enabled": False, "enable_bundled_skills": False})(),
        timed_out=False,
        resource_enforcement="scheduler",
        enforced_memory_gb=4.0,
        enforced_n_jobs=1,
    )
    assert gates["resource_budget_enforced"] is False


def test_resource_evidence_must_match_capability_request(tmp_path: Path) -> None:
    path = tmp_path / "resource.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "pertura-resource-evidence-v1",
                "mode": "scheduler",
                "scheduler_job_id": "job-17",
                "requested_memory_gb": 4.0,
                "actual_memory_gb": 4.0,
                "peak_rss_mb": 512.0,
                "cpu_count": 1,
                "n_jobs": 1,
                "timeout_seconds": 1800,
                "wall_clock_seconds": 12.0,
                "thread_environment": {"OMP_NUM_THREADS": "1"},
            }
        ),
        encoding="utf-8",
    )
    evidence = load_resource_evidence(path)
    validate_resource_request(evidence, memory_gb=4.0, n_jobs=1)
    with pytest.raises(ValueError, match="memory requests disagree"):
        validate_resource_request(evidence, memory_gb=8.0, n_jobs=1)


def test_scheduler_resource_evidence_uses_actual_slurm_allocation(
    tmp_path: Path, monkeypatch
) -> None:
    path = tmp_path / "resource.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "pertura-resource-evidence-v1",
                "mode": "scheduler",
                "scheduler_job_id": "job-48",
                "requested_memory_gb": 48.0,
                "actual_memory_gb": 32.0,
                "cpu_count": 99,
                "n_jobs": 1,
                "timeout_seconds": 3600,
                "thread_environment": {"OMP_NUM_THREADS": "1"},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("SLURM_JOB_ID", "job-48")
    monkeypatch.setenv("SLURM_MEM_PER_NODE", str(48 * 1024))
    monkeypatch.setenv("SLURM_CPUS_PER_TASK", "1")

    evidence = load_resource_evidence(path)

    assert evidence["requested_memory_gb"] == 48.0
    assert evidence["actual_memory_gb"] == 48.0
    assert evidence["cpu_count"] == 1
    assert evidence["allocation_source"] == "slurm_environment"
    assert evidence["slurm_memory_source"] == "SLURM_MEM_PER_NODE"
    validate_resource_request(evidence, memory_gb=48.0, n_jobs=1)


def test_rlimit_resource_template_is_enforced_then_observed(
    tmp_path: Path, monkeypatch
) -> None:
    path = tmp_path / "resource.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "pertura-resource-evidence-v1",
                "mode": "rlimit",
                "requested_memory_gb": 8.0,
                "actual_memory_gb": 8.0,
                "cpu_count": 1,
                "n_jobs": 1,
                "timeout_seconds": 7200,
                "thread_environment": {"OMP_NUM_THREADS": "1"},
            }
        ),
        encoding="utf-8",
    )
    evidence = load_resource_evidence(path)
    assert evidence["peak_rss_mb"] == 0
    assert evidence["wall_clock_seconds"] == 0
    assert evidence["enforcement_active"] is False

    monkeypatch.setattr(
        "pertura_bench.resource_evidence._apply_rlimit_as", lambda limit: limit
    )
    monkeypatch.setattr(
        "pertura_bench.resource_evidence._peak_rss_mb",
        lambda: (256.0, "fixture"),
    )
    enforced = enforce_runtime_resource_budget(evidence)
    observed = observe_runtime_resources(enforced, started_monotonic=0.0)
    assert observed["enforcement_active"] is True
    assert observed["rlimit_identity"].endswith(":RLIMIT_AS")
    assert observed["rlimit_as_bytes"] == 8 * 1024**3
    assert observed["peak_rss_mb"] == 256.0
    assert observed["wall_clock_seconds"] > 0


def test_reference_pipeline_binds_subset_generator_environment_and_outputs(
    tmp_path: Path,
) -> None:
    subset_path = tmp_path / "subset.lock.json"
    subset_path.write_text(
        BenchmarkSubsetLock(
            dataset_id="fixture",
            subset_spec_hash="sha256:" + "1" * 64,
            source_lock_hash="sha256:" + "2" * 64,
            output_sha256="sha256:" + "3" * 64,
            n_cells=4,
            n_genes=2,
            subset_script_hash="sha256:" + "4" * 64,
            selected_ids_sha256="sha256:" + "5" * 64,
            selection_manifest_sha256="sha256:" + "6" * 64,
            selection_summary={"split": "evaluation"},
        ).model_dump_json(indent=2),
        encoding="utf-8",
    )
    generator = tmp_path / "generator.py"
    generator.write_text(
        "from pathlib import Path\n"
        "import sys\n"
        "assert Path(sys.argv[1]).name == 'parameters.json'\n"
        "Path('reference.csv').write_text('id,value\\na,1\\n', encoding='utf-8')\n",
        encoding="utf-8",
    )
    environment = tmp_path / "environment.json"
    environment.write_text('{"lock":"fixture"}', encoding="utf-8")
    parameters = tmp_path / "parameters.json"
    parameters.write_text("{}", encoding="utf-8")
    output = tmp_path / "reference"
    generated = generate_reference(
        dataset_id="fixture",
        split="evaluation",
        subset_lock=subset_path,
        generator_script=generator,
        environment_lock=environment,
        parameters=parameters,
        output=output,
    )
    assert generated["artifacts"]["reference.csv"] == file_sha256(
        output / "reference.csv"
    )
    assert validate_reference(output)["ok"] is True
    frozen = freeze_reference(output, tmp_path / "frozen.json")
    assert frozen["provenance_hash"] == generated["provenance_hash"]
