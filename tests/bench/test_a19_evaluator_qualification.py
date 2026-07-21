from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pandas as pd

from pertura_bench.paper_task_evaluation import evaluate_paper_task
from pertura_bench.paper_agent_execution import _artifact_paths_present
from pertura_core.hashing import file_sha256
from pertura_runtime.project.workspace import ProjectWorkspace


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "qualify_a19_evaluators",
    ROOT / "scripts/qualify_a19_evaluators.py",
)
assert SPEC is not None and SPEC.loader is not None
qualification = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(qualification)
sys.modules.setdefault("qualify_a19_evaluators", qualification)

BINDING_SPEC = importlib.util.spec_from_file_location(
    "qualify_a19_capability_bindings",
    ROOT / "scripts/qualify_a19_capability_bindings.py",
)
assert BINDING_SPEC is not None and BINDING_SPEC.loader is not None
binding_qualification = importlib.util.module_from_spec(BINDING_SPEC)
BINDING_SPEC.loader.exec_module(binding_qualification)


def _generic_fixture(tmp_path: Path, *, numeric: bool):
    paper = tmp_path / "paper"
    output = tmp_path / "positive"
    paper.mkdir()
    output.mkdir()
    reference = paper / "reference.tsv"
    rows = (
        [{"id": "a", "effect": 1.0}, {"id": "b", "effect": -1.0}]
        if numeric
        else [{"id": "a", "label": "x"}, {"id": "b", "label": "y"}]
    )
    pd.DataFrame(rows).to_csv(reference, sep="\t", index=False)
    observed = output / "observed.tsv"
    pd.DataFrame(rows).to_csv(observed, sep="\t", index=False)
    evaluator = {
        "evaluator_id": "fixture",
        "type": "effect_error" if numeric else "classification",
        "observed_output": "observed.tsv",
        "reference_path": "reference.tsv",
        "reference_sha256": file_sha256(reference),
        "key_columns": ["id"],
        **(
            {
                "observed_value_column": "effect",
                "reference_value_column": "effect",
                "maximum_mae": 0.0,
                "maximum_rmse": 0.0,
            }
            if numeric
            else {
                "observed_label_column": "label",
                "reference_label_column": "label",
                "minimum_accuracy": 1.0,
                "minimum_macro_f1": 1.0,
            }
        ),
    }
    binding = {
        "task_reference_id": "fixture",
        "evaluator_id": "task.fixture.v1",
        "evaluation_domain": "scientific_fidelity",
        "evaluators": [evaluator],
        "protocol_evaluator": {
            "allowed_status": ["completed"],
            "allowed_analysis_units": ["donor"],
        },
    }
    task = {"task_id": "FIXTURE"}
    result = {
        "status": "completed",
        "analysis_unit": "donor",
        "findings": [],
        "limitations": [],
    }
    positive = evaluate_paper_task(
        task,
        benchmark_result=result,
        task_output_root=output,
        paper_root=paper,
        bindings=[binding],
    )
    assert positive["status"] == "passed"
    return paper, output, task, result, binding, observed, evaluator


def test_qualification_executes_structural_negative_controls(tmp_path: Path) -> None:
    paper, output, task, result, binding, observed, evaluator = _generic_fixture(
        tmp_path, numeric=False
    )

    controls = qualification._negative_controls(
        task=task,
        result=result,
        binding=binding,
        positive_root=output,
        control_root=tmp_path / "negative",
        observed=[(observed, ("id",), evaluator)],
        paper_root=paper,
    )

    assert set(controls["artifact_controls"]["observed.tsv"]) == {
        "missing_artifact",
        "missing_key",
        "duplicate_key",
        "wrong_row_universe",
        "wrong_categorical_label",
    }
    assert controls["result_controls"]["wrong_analysis_unit"]["status"] == "failed"
    assert all(
        item["status"] == "failed"
        for item in controls["artifact_controls"]["observed.tsv"].values()
    )


def test_qualification_executes_numeric_negative_controls(tmp_path: Path) -> None:
    paper, output, task, result, binding, observed, evaluator = _generic_fixture(
        tmp_path, numeric=True
    )

    controls = qualification._negative_controls(
        task=task,
        result=result,
        binding=binding,
        positive_root=output,
        control_root=tmp_path / "negative",
        observed=[(observed, ("id",), evaluator)],
        paper_root=paper,
    )

    observed_controls = controls["artifact_controls"]["observed.tsv"]
    assert observed_controls["nonfinite_value"]["status"] == "failed"
    assert observed_controls["wrong_effect_or_probability"]["status"] == "failed"


def test_qualification_records_only_executed_controls() -> None:
    source = (ROOT / "scripts/qualify_a19_evaluators.py").read_text(encoding="utf-8")
    assert '"reference_leakage_audit"' not in source
    assert "negative_control_coverage" in source
    assert "_require_failed" in source


def test_qualification_materializes_protocol_balances(tmp_path: Path) -> None:
    task = {
        "output_contract": {
            "artifact_paths": {"accounting": "accounting.json"},
            "artifact_schemas": {
                "accounting.json": [
                    "analyzed",
                    "excluded",
                    "evaluation_cells",
                    "donor_count",
                ]
            },
        }
    }
    binding = {
        "protocol_evaluator": {
            "required_json_values": {
                "accounting.json": {"excluded": 6, "donor_count": 4}
            },
            "required_json_balances": [
                {
                    "output": "accounting.json",
                    "total": "evaluation_cells",
                    "parts": ["analyzed", "excluded"],
                }
            ],
        }
    }
    qualification._fill_protocol_outputs(task, binding, tmp_path)

    payload = qualification._read_json(tmp_path / "accounting.json")
    assert payload == {
        "analyzed": 0,
        "donor_count": 4,
        "evaluation_cells": 6.0,
        "excluded": 6,
    }


def test_protocol_fixtures_satisfy_every_public_artifact_contract(
    tmp_path: Path,
) -> None:
    catalog = json.loads(
        (ROOT / "benchmarks/paper_v1/agent_tasks.v2.json").read_text(encoding="utf-8")
    )
    references = json.loads(
        (ROOT / "benchmarks/paper_v1/task_references.v1.json").read_text(
            encoding="utf-8"
        )
    )
    bindings = {item["task_id"]: item for item in references["bindings"]}
    for workflow in catalog["workflows"]:
        for task in workflow["turns"]:
            output = tmp_path / task["task_id"]
            output.mkdir(parents=True)
            qualification._fill_protocol_outputs(
                task, bindings.get(task["task_id"], {}), output
            )
            assert _artifact_paths_present(output, task["output_contract"]), task[
                "task_id"
            ]

    state_model = tmp_path / "PAPA-02" / "state_reference_model"
    assert state_model.is_dir()
    assert (state_model / "qualification.json").is_file()


def test_binding_qualification_collects_failures_and_continues_task(
    tmp_path: Path,
) -> None:
    project = ProjectWorkspace.initialize(tmp_path / "project")
    run = project.create_run(logical_name="qualification")
    conversation = project.create_conversation(run.run_id, title="qualification")
    workspace = project.run_workspace(run.run_id)

    def binding(capability_id: str, *, readiness: str, blockers=()):
        return SimpleNamespace(
            task_id="REPL-03",
            binding_id=f"binding-{capability_id}",
            binding_hash="sha256:" + "1" * 64,
            capability_id=capability_id,
            capability_scientific_hash="sha256:" + "2" * 64,
            tool_name="run_diagnostic",
            contract_id="contract-fixture",
            contract_hash="sha256:" + "3" * 64,
            scope={"dataset_id": "fixture"},
            bound_parameters={},
            input_assets=(),
            dependency_result_ids=(),
            dependency_verification_states=(),
            dependency_receipt_ids=(),
            dependency_binding_ids=(),
            output_mapping={"fixture": "audit"},
            readiness=readiness,
            blockers=tuple(blockers),
        )

    failed = binding("diagnostic.fail.v1", readiness="ready")
    blocked = binding(
        "diagnostic.probe.v1",
        readiness="blocked_probe",
        blockers=("expected fixture blocker",),
    )

    class Runtime:
        project_workspace = project
        _invocation_bindings = {
            failed.binding_id: failed,
            blocked.binding_id: blocked,
        }

        @staticmethod
        def run_diagnostic(capability_id=None, *, binding_id, **kwargs):
            assert capability_id is None
            assert kwargs["parameters"] == {}
            assert kwargs["dependencies"] == []
            if binding_id == failed.binding_id:
                raise RuntimeError("first binding failed")
            return {"status": "blocked", "result_id": None, "receipt_id": None}

    agent = SimpleNamespace(
        product_runtime=Runtime(),
        workspace=workspace,
        conversation_id=conversation.conversation_id,
        manifest=None,
    )
    executor = binding_qualification._BindingQualificationExecutor(
        tasks={
            "REPL-03": {
                "task_id": "REPL-03",
                "dataset_id": "fixture",
                "required_artifact_roles": ["audit"],
                "expected_probe_capabilities": ["diagnostic.probe.v1"],
                "output_contract": {
                    "artifact_roles": ["audit"],
                    "artifact_paths": {"audit": "audit.json"},
                    "artifact_schemas": {"audit.json": ["status"]},
                },
            }
        },
        references={},
        paper_root=tmp_path,
    )

    result = executor(agent, "task REPL-03 (turn 1)", 60)

    assert result.status == "completed"
    assert [item["qualification_status"] for item in executor.records] == [
        "failed_execution",
        "expected_blocked_probe",
    ]
    assert "first binding failed" in executor.records[0]["qualification_error"]
    assert (workspace.root / "outputs/tasks/REPL-03/submission_receipt.json").is_file()


def test_binding_qualification_preserves_real_output_failure(
    tmp_path: Path,
) -> None:
    project = ProjectWorkspace.initialize(tmp_path / "project")
    run = project.create_run(logical_name="qualification-real-output-failure")
    conversation = project.create_conversation(run.run_id, title="qualification")
    workspace = project.run_workspace(run.run_id)
    binding = SimpleNamespace(
        task_id="PAPA-02",
        binding_id="binding-state-fit",
        binding_hash="sha256:" + "1" * 64,
        capability_id="state.reference.fit.v1",
        capability_scientific_hash="sha256:" + "2" * 64,
        tool_name="run_analysis",
        contract_id="contract-fixture",
        contract_hash="sha256:" + "3" * 64,
        scope={"dataset_id": "fixture"},
        bound_parameters={},
        input_assets=(),
        dependency_result_ids=(),
        dependency_verification_states=(),
        dependency_receipt_ids=(),
        dependency_binding_ids=(),
        output_mapping={"state_reference_model": "state_reference_model"},
        readiness="ready",
        blockers=(),
    )

    class Runtime:
        project_workspace = project
        _invocation_bindings = {binding.binding_id: binding}

        @staticmethod
        def run_analysis(objective, *, binding_id, **kwargs):
            assert objective
            assert binding_id == binding.binding_id
            assert kwargs["capability_id"] is None
            assert kwargs["parameters"] == {}
            assert kwargs["dependencies"] == []
            raise RuntimeError("state fit worker failed before commit")

    agent = SimpleNamespace(
        product_runtime=Runtime(),
        workspace=workspace,
        conversation_id=conversation.conversation_id,
        manifest=None,
    )
    task = {
        "task_id": "PAPA-02",
        "dataset_id": "fixture",
        "required_artifact_roles": [
            "state_reference_model",
            "reference_cell_manifest",
            "reference_provenance",
        ],
        "expected_probe_capabilities": [],
        "output_contract": {
            "allowed_analysis_units": ["control_cell"],
            "artifact_roles": [
                "state_reference_model",
                "reference_cell_manifest",
                "reference_provenance",
            ],
            "artifact_paths": {
                "state_reference_model": "state_reference_model",
                "reference_cell_manifest": "reference_cell_manifest.tsv",
                "reference_provenance": "reference_provenance.json",
            },
            "artifact_schemas": {
                "reference_cell_manifest.tsv": [
                    "cell_id",
                    "technical_state_id",
                ]
            },
        },
    }
    executor = binding_qualification._BindingQualificationExecutor(
        tasks={"PAPA-02": task},
        references={},
        paper_root=tmp_path,
    )

    completed = executor(agent, "task PAPA-02 (turn 2)", 60)

    assert completed.status == "completed"
    assert len(executor.records) == 1
    assert executor.records[0]["qualification_status"] == "failed_execution"
    assert "state fit worker failed" in executor.records[0]["qualification_error"]
    materialization_error = executor.scientific_materialization_errors["PAPA-02"]
    assert materialization_error is not None
    assert "state.reference.fit.v1" in materialization_error
    diagnostic = json.loads(
        (
            workspace.root / "qualification_diagnostics/PAPA-02.json"
        ).read_text(encoding="utf-8")
    )
    assert diagnostic["stage"] == "submission_complete"
    assert diagnostic["records"][0]["qualification_status"] == "failed_execution"
    benchmark_result = json.loads(
        (
            workspace.root / "outputs/tasks/PAPA-02/benchmark_result.json"
        ).read_text(encoding="utf-8")
    )
    assert benchmark_result["status"] == "blocked"
    assert (
        workspace.root / "outputs/tasks/PAPA-02/submission_receipt.json"
    ).is_file()


def test_binding_qualification_distinguishes_terminal_and_chain_blocks(
    tmp_path: Path,
) -> None:
    project = ProjectWorkspace.initialize(tmp_path / "project")
    run = project.create_run(logical_name="qualification-status")
    conversation = project.create_conversation(run.run_id, title="qualification-status")
    workspace = project.run_workspace(run.run_id)

    def binding(capability_id: str, tool_name: str):
        return SimpleNamespace(
            task_id="REPL-01",
            binding_id=f"binding-{capability_id}",
            binding_hash="sha256:" + "1" * 64,
            capability_id=capability_id,
            capability_scientific_hash="sha256:" + "2" * 64,
            tool_name=tool_name,
            contract_id="contract-fixture",
            contract_hash="sha256:" + "3" * 64,
            scope={"dataset_id": "fixture"},
            bound_parameters={},
            input_assets=(),
            dependency_result_ids=(),
            dependency_verification_states=(),
            dependency_receipt_ids=(),
            dependency_binding_ids=(),
            output_mapping={"fixture": "audit"},
            readiness="ready",
            blockers=(),
        )

    diagnostic = binding("diagnostic.audit.v1", "run_diagnostic")
    analysis = binding("analysis.chain.v1", "run_analysis")

    def result(result_id: str, status: str, blocker: str):
        return SimpleNamespace(
            result_id=result_id,
            status=SimpleNamespace(value=status),
            blockers=(blocker,),
            cautions=(),
            capability_trust=SimpleNamespace(value="exploratory"),
            canonical_hash="sha256:" + result_id[-1] * 64,
            output_hashes={},
        )

    diagnostic_result = result(
        "result_diagnostic_1", "blocked", "design fact remains unresolved"
    )
    analysis_result = result(
        "result_analysis_2", "blocked", "required upstream result is unusable"
    )

    class Runtime:
        project_workspace = project
        _invocation_bindings = {
            diagnostic.binding_id: diagnostic,
            analysis.binding_id: analysis,
        }

        @staticmethod
        def run_diagnostic(capability_id=None, *, binding_id, **kwargs):
            assert capability_id is None
            assert kwargs["parameters"] == {}
            assert kwargs["dependencies"] == []
            assert binding_id == diagnostic.binding_id
            return {
                "status": "blocked",
                "result_id": diagnostic_result.result_id,
                "receipt_id": None,
                "blockers": list(diagnostic_result.blockers),
            }

        @staticmethod
        def run_analysis(objective, *, binding_id, **kwargs):
            assert objective
            assert binding_id == analysis.binding_id
            assert kwargs["capability_id"] is None
            assert kwargs["parameters"] == {}
            assert kwargs["dependencies"] == []
            return {
                "status": "blocked",
                "result_id": analysis_result.result_id,
                "receipt_id": None,
                "blockers": list(analysis_result.blockers),
                "required_upstream": ["upstream.fixture.v1"],
                "candidate_result_ids": ["result_candidate_3"],
                "dependency_verdicts": [
                    {
                        "capability_id": "upstream.fixture.v1",
                        "usable": False,
                        "reasons": ["status_not_accepted"],
                    }
                ],
            }

        @staticmethod
        def planning_material(contract_id):
            assert contract_id == "contract-fixture"
            return None, (diagnostic_result, analysis_result)

    agent = SimpleNamespace(
        product_runtime=Runtime(),
        workspace=workspace,
        conversation_id=conversation.conversation_id,
        manifest=None,
    )
    executor = binding_qualification._BindingQualificationExecutor(
        tasks={
            "REPL-01": {
                "task_id": "REPL-01",
                "dataset_id": "fixture",
                "required_artifact_roles": ["audit"],
                "expected_probe_capabilities": [],
                "output_contract": {
                    "artifact_roles": ["audit"],
                    "artifact_paths": {"audit": "audit.json"},
                    "artifact_schemas": {"audit.json": ["status"]},
                },
            }
        },
        references={},
        paper_root=tmp_path,
    )

    completed = executor(agent, "task REPL-01 (turn 1)", 60)

    assert completed.status == "completed"
    assert [item["qualification_status"] for item in executor.records] == [
        "executed_terminal_diagnostic_block",
        "failed_validation",
    ]
    assert executor.records[0]["result_blockers"] == ["design fact remains unresolved"]
    failed = executor.records[1]
    assert failed["result_status"] == "blocked"
    assert failed["result_blockers"] == ["required upstream result is unusable"]
    assert failed["response_required_upstream"] == ["upstream.fixture.v1"]
    assert failed["response_dependency_verdicts"][0]["reasons"] == [
        "status_not_accepted"
    ]


def test_qualification_constructs_posterior_calibration_positive(
    tmp_path: Path,
) -> None:
    paper = tmp_path / "paper"
    output = tmp_path / "positive"
    paper.mkdir()
    output.mkdir()
    reference = paper / "reference.tsv"
    pd.DataFrame(
        [
            {"id": "a", "reference_responder": 1},
            {"id": "b", "reference_responder": 0},
        ]
    ).to_csv(reference, sep="\t", index=False)
    evaluator = {
        "evaluator_id": "posterior",
        "type": "posterior_calibration",
        "observed_output": "observed.tsv",
        "reference_path": "reference.tsv",
        "reference_sha256": file_sha256(reference),
        "key_columns": ["id"],
        "probability_column": "responder_probability",
        "reference_label_column": "reference_responder",
        "maximum_brier": 0.0,
        "maximum_ece": 0.0,
        "bins": 2,
    }
    binding = {
        "task_reference_id": "fixture",
        "evaluator_id": "task.fixture.v1",
        "evaluation_domain": "scientific_fidelity",
        "evaluators": [evaluator],
        "protocol_evaluator": {
            "allowed_status": ["completed"],
            "allowed_analysis_units": ["cell"],
        },
    }

    qualification._materialize_generic_positive(binding, output, paper)

    observed = pd.read_csv(output / "observed.tsv", sep="\t")
    assert observed["responder_probability"].tolist() == [1.0, 0.0]
    verdict = evaluate_paper_task(
        {"task_id": "FIXTURE"},
        benchmark_result={
            "status": "completed",
            "analysis_unit": "cell",
            "findings": [],
            "limitations": [],
        },
        task_output_root=output,
        paper_root=paper,
        bindings=[binding],
    )
    assert verdict["status"] == "passed"
