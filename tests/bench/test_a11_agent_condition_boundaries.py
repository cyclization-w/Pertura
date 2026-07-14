from __future__ import annotations

import json
import sys
from types import ModuleType, SimpleNamespace

from pertura_bench.agent_judge import grade_turn_final
from pertura_bench.agent_server_execution import evaluate_server_agent_hard_gates
from pertura_runtime.claude.options import ClaudeRuntimeOptions


def _baseline_final(text: str) -> dict:
    return {
        "status": "completed",
        "structured": True,
        "claim_authority": False,
        "findings": [
            {
                "finding_id": "finding-1",
                "text": text,
                "role": "measured",
                "ceiling": "unscored_provider_claim",
                "result_ids": [],
                "limitations": [],
            }
        ],
    }


def _case() -> dict:
    return {
        "case_id": "fixture-agent",
        "dataset_id": "fixture-dataset",
        "benchmark_track": "primary",
        "expected_benchmark_result_type": "fixture_result",
        "expected_capability_dag": [],
        "expected_statuses": ["completed"],
        "expected_turns": 1,
        "required_result_roles": [],
        "required_artifact_roles": ["primary_dataset"],
        "scope_claim_constraints": [
            "no_cell_as_replicate",
            "no_prediction_as_measurement",
            "no_candidate_as_strong_measured",
        ],
        "max_memory_gb": 4,
        "timeout_seconds": 60,
    }


def _benchmark_output(tmp_path):
    output = tmp_path / "benchmark_result.json"
    output.write_text(
        json.dumps(
            {
                "schema_version": "pertura-agent-benchmark-result-v1",
                "case_id": "fixture-agent",
                "dataset_id": "fixture-dataset",
                "result_type": "fixture_result",
                "analysis_unit": "replicate",
                "status": "completed",
                "findings": [{"finding_id": "f1", "text": "bounded result"}],
                "metrics": {},
                "limitations": [],
                "artifact_roles": [],
            }
        ),
        encoding="utf-8",
    )
    return output

def _options() -> ClaudeRuntimeOptions:
    return ClaudeRuntimeOptions(
        model="fixed-model",
        interaction_mode="benchmark",
        benchmark_condition="prompt_only",
        domain_tools_enabled=False,
        enable_bundled_skills=False,
    )


def test_baseline_hard_gate_scores_preserved_claim_without_pertura_results(tmp_path) -> None:
    output = _benchmark_output(tmp_path)
    gates = evaluate_server_agent_hard_gates(
        _case(),
        condition="prompt_only",
        final=_baseline_final("The perturbation produced a measured response."),
        turns=(SimpleNamespace(provider_final="measured response"),),
        authority={"committed": []},
        registered_asset_roles={"primary_dataset"},
        output_files=(output,),
        runtime_options=_options(),
        timed_out=False,
        scientific_metric_evaluation={"status": "passed"},
        resource_enforcement="scheduler",
        enforced_memory_gb=4.0,
        enforced_n_jobs=1,
    )

    assert all(gates.values())


def test_baseline_hard_gate_detects_cell_as_replicate_overclaim(tmp_path) -> None:
    output = _benchmark_output(tmp_path)
    text = "We treated each cell as an independent replicate and proved the effect."
    gates = evaluate_server_agent_hard_gates(
        _case(),
        condition="prompt_only",
        final=_baseline_final(text),
        turns=(SimpleNamespace(provider_final=text),),
        authority={"committed": []},
        registered_asset_roles={"primary_dataset"},
        output_files=(output,),
        runtime_options=_options(),
        timed_out=False,
        scientific_metric_evaluation={"status": "passed"},
        resource_enforcement="scheduler",
        enforced_memory_gb=4.0,
        enforced_n_jobs=1,
    )

    assert gates["scope_claim_constraints"] is False


def test_narrative_judge_request_failure_is_unavailable_without_fallback(
    tmp_path, monkeypatch
) -> None:
    class Completions:
        @staticmethod
        def create(**_kwargs):
            raise RuntimeError("network unavailable")

    class Client:
        def __init__(self, **_kwargs):
            self.chat = SimpleNamespace(completions=Completions())

    module = ModuleType("openai")
    module.OpenAI = Client
    monkeypatch.setitem(sys.modules, "openai", module)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")

    grade = grade_turn_final(
        _baseline_final("A bounded observation."),
        execution_verdict={"status": "passed"},
        output_path=tmp_path / "grade.json",
    )

    assert grade["status"] == "judge_unavailable"
    assert grade["fallback_used"] is False
    assert "RuntimeError" in grade["reason"]

def test_pertura_authority_claim_requires_a_cited_trusted_receipt(tmp_path) -> None:
    output = _benchmark_output(tmp_path)
    final = _baseline_final("A strong measured effect was found.")
    final["claim_authority"] = True
    final["findings"][0]["ceiling"] = "strong_measured"
    options = ClaudeRuntimeOptions(
        model="fixed-model",
        interaction_mode="benchmark",
        benchmark_condition="pertura_full",
        domain_tools_enabled=True,
        enable_bundled_skills=True,
    )

    gates = evaluate_server_agent_hard_gates(
        _case(),
        condition="pertura_full",
        final=final,
        turns=(SimpleNamespace(provider_final="A strong measured effect was found."),),
        authority={"committed": []},
        registered_asset_roles={"primary_dataset"},
        output_files=(output,),
        runtime_options=options,
        timed_out=False,
        scientific_metric_evaluation={"status": "passed"},
        resource_enforcement="scheduler",
        enforced_memory_gb=4.0,
        enforced_n_jobs=1,
    )

    assert gates["claim_surface_condition"] is False

def test_missing_condition_neutral_result_cannot_pass_agent_hard_gates(
    tmp_path,
) -> None:
    gates = evaluate_server_agent_hard_gates(
        _case(),
        condition="prompt_only",
        final=_baseline_final("A bounded analysis."),
        turns=(SimpleNamespace(provider_final="A bounded analysis."),),
        authority={"committed": []},
        registered_asset_roles={"primary_dataset"},
        output_files=(),
        runtime_options=_options(),
        timed_out=False,
        scientific_metric_evaluation={"status": "not_available"},
        resource_enforcement="scheduler",
        enforced_memory_gb=4.0,
        enforced_n_jobs=1,
    )
    assert gates["benchmark_result_present"] is False
    assert gates["benchmark_result_schema_valid"] is False
    assert gates["scientific_reference_metrics"] is False


def test_declared_resource_budget_without_enforcement_does_not_pass(
    tmp_path,
) -> None:
    output = _benchmark_output(tmp_path)
    gates = evaluate_server_agent_hard_gates(
        _case(),
        condition="prompt_only",
        final=_baseline_final("A bounded analysis."),
        turns=(SimpleNamespace(provider_final="A bounded analysis."),),
        authority={"committed": []},
        registered_asset_roles={"primary_dataset"},
        output_files=(output,),
        runtime_options=_options(),
        timed_out=False,
        scientific_metric_evaluation={"status": "passed"},
    )
    assert gates["resource_budget_declared"] is True
    assert gates["resource_budget_enforced"] is False