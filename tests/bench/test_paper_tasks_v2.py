from __future__ import annotations

import json
from pathlib import Path

import pytest

from pertura_bench.capability_bench import server_benchmark_plan
from pertura_bench.paper_tasks import (
    load_paper_task_catalog,
    validate_paper_anchor_catalog,
    validate_task_reference_catalog,
)
from pertura_workflow.capabilities import CapabilityRegistry
from pertura_core.hashing import file_sha256
from pertura_workflow.planner import build_capability_contract_catalog


ROOT = Path(__file__).resolve().parents[2]
CATALOG = ROOT / "benchmarks" / "paper_v1" / "agent_tasks.v2.json"
REFERENCES = ROOT / "benchmarks" / "paper_v1" / "task_references.v1.json"
ANCHORS = ROOT / "benchmarks" / "paper_v1" / "paper_anchors.v1.json"


def test_v2_catalog_freezes_required_shape_without_capability_growth() -> None:
    catalog = load_paper_task_catalog(CATALOG)
    tasks = catalog.tasks()
    primary = [
        task
        for workflow in catalog.workflows
        for task in workflow["turns"]
        if workflow["role"] == "primary" and task.get("role") != "optional"
    ]
    supplemental = [
        task
        for workflow in catalog.workflows
        for task in workflow["turns"]
        if workflow["role"] == "supplemental"
    ]
    assert len(primary) == 18
    assert len(supplemental) == 2
    assert [task["task_id"] for task in tasks if task.get("role") == "optional"] == [
        "VIRT-01"
    ]
    assert {
        tier: sum(task["tier"] == tier for task in primary)
        for tier in ("basic", "intermediate", "advanced")
    } == {
        "basic": 6,
        "intermediate": 8,
        "advanced": 4,
    }
    assert catalog.payload["execution_protocol"]["required_scored_turns"] == 120
    assert catalog.payload["execution_protocol"]["required_agent_sessions"] == 24
    assert len(CapabilityRegistry.load_default(include_external=False).specs()) == 44


def test_every_task_has_one_reference_binding_and_known_paper_anchor() -> None:
    catalog = load_paper_task_catalog(CATALOG)
    references = json.loads(REFERENCES.read_text(encoding="utf-8"))
    anchors = json.loads(ANCHORS.read_text(encoding="utf-8"))
    assert validate_task_reference_catalog(references, catalog.tasks()) == []
    assert validate_paper_anchor_catalog(anchors, catalog.tasks()) == []


def test_papa02_binds_the_frozen_ref03_control_reference() -> None:
    payload = json.loads(REFERENCES.read_text(encoding="utf-8"))
    binding = next(item for item in payload["bindings"] if item["task_id"] == "PAPA-02")
    assert binding["evaluator_templates"][0]["reference_output"] == (
        "control_state_reference/control_assignments.tsv"
    )


def test_formal_server_plan_uses_24_workflow_jobs_not_120_sessions(
    tmp_path: Path,
) -> None:
    bound_references = json.loads(REFERENCES.read_text(encoding="utf-8"))
    bound_references.update(
        {
            "schema_version": "pertura-paper-task-reference-catalog-bound-v1",
            "status": "bound",
            "passed": True,
            "problems": [],
        }
    )
    for binding in bound_references["bindings"]:
        binding["bound_reference_sources"] = [
            {
                "reference_id": source,
                "manifest_sha256": "sha256:" + "1" * 64,
                "pack_tree_sha256": "sha256:" + "2" * 64,
            }
            for source in binding["reference_sources"]
        ]
        if binding["scoring_route"] in {"artifact_evaluator", "hybrid"}:
            binding["evaluators"] = [
                {
                    key: value
                    for key, value in evaluator.items()
                    if key not in {"reference_source", "reference_output", "metric_ids"}
                }
                | {
                    "reference_path": "fixture.tsv",
                    "reference_sha256": "sha256:" + "3" * 64,
                }
                for evaluator in binding["evaluator_templates"]
            ]
        if binding["scoring_route"] == "custom_artifact_evaluator":
            binding["bound_evaluator"] = {"type": "fixture"}
    bound_reference_path = tmp_path / "bound-task-references.json"
    bound_reference_path.write_text(json.dumps(bound_references), encoding="utf-8")
    asset_path = tmp_path / "bound-assets.json"
    task_catalog = load_paper_task_catalog(CATALOG)
    asset_workflows = {}
    for workflow in task_catalog.workflows:
        by_task = {task["task_id"]: task for task in workflow["turns"]}

        def ancestors(task):
            found = set()
            pending = list(task["depends_on_tasks"])
            while pending:
                dependency = pending.pop()
                if dependency in found:
                    continue
                found.add(dependency)
                pending.extend(by_task[dependency]["depends_on_tasks"])
            return found

        external = set()
        for task in workflow["turns"]:
            internal = {
                role
                for dependency in ancestors(task)
                for role in by_task[dependency]["required_artifact_roles"]
            }
            for role in task["required_input_roles"]:
                if role not in internal and not (
                    task.get("role") == "optional"
                    and role == "prediction_manifest_optional"
                ):
                    external.add(role)
        asset_workflows[workflow["workflow_id"]] = {
            "assets": [
                {
                    "role": role,
                    "root": "cache",
                    "relative_path": f"fixture/{role}",
                    "content_sha256": "sha256:" + "4" * 64,
                }
                for role in sorted(external)
            ]
        }
    asset_path.write_text(
        json.dumps(
            {
                "schema_version": "pertura-paper-agent-assets-v1",
                "status": "bound",
                "passed": True,
                "problems": [],
                "workflows": asset_workflows,
            }
        ),
        encoding="utf-8",
    )
    contract_catalog_path = tmp_path / "capability-contracts.json"
    contract_catalog_path.write_text(
        json.dumps(build_capability_contract_catalog(), sort_keys=True),
        encoding="utf-8",
    )
    plan = server_benchmark_plan(
        ROOT,
        paper_task_catalog_path=CATALOG,
        paper_task_reference_catalog_path=bound_reference_path,
        paper_anchor_catalog_path=ANCHORS,
        paper_asset_catalog_path=asset_path,
        capability_contract_catalog_path=contract_catalog_path,
    )
    jobs = [job for job in plan.jobs if job.get("kind") == "paper_agent_workflow"]
    assert len(jobs) == 24
    assert sum(int(job["required_task_count"]) for job in jobs) == 120
    assert not [job for job in plan.jobs if job.get("kind") == "agent_workflow"]
    assert all(job["max_turns_per_task"] == 48 for job in jobs)
    assert all(job["session_scope"]["shared_provider_session"] for job in jobs)
    assert all(job["session_scope"]["condition_repeat_isolated"] for job in jobs)
    assert all(float(job["resources"]["memory_gb"]) >= 32 for job in jobs)
    for job in jobs:
        workflow = task_catalog.workflow(str(job["workflow_id"]))
        required_timeout = sum(
            int(task["resources"]["timeout_seconds"])
            for task in workflow["turns"]
            if task.get("role") != "optional"
        )
        assert (
            int(job["resources"]["walltime_minutes"])
            == (required_timeout + 3600 + 59) // 60
        )
    assert plan.checkpoint_binding["capability_contract_catalog_hash"] == file_sha256(
        contract_catalog_path
    )
    assert all(
        "--capability-contract-catalog" in job["command"]["argv"] for job in jobs
    )


def test_trans_de_and_global_effect_are_not_new_capabilities() -> None:
    catalog = load_paper_task_catalog(CATALOG)
    by_id = {task["task_id"]: task for task in catalog.tasks()}
    assert by_id["PAPA-06"]["execution_mode"] == "codeact_scientific"
    assert by_id["PAPA-06"]["expected_capability_dag"] == []
    assert by_id["PAPA-07"]["execution_mode"] == "evidence_interpretation"
    assert by_id["PAPA-07"]["expected_capability_dag"] == []


def test_v2_catalog_freezes_task_scoped_skill_bindings() -> None:
    catalog = load_paper_task_catalog(CATALOG)
    by_id = {task["task_id"]: task for task in catalog.tasks()}
    from pertura_bench.paper_tasks import PAPER_TASK_SKILLS

    assert {
        task_id: tuple(task["pertura_skills"]) for task_id, task in by_id.items()
    } == PAPER_TASK_SKILLS
    assert by_id["KANG-01"]["pertura_skills"] == [
        "operate-pertura-workflow",
        "run-replicate-aware-pseudobulk-de",
        "run-design-preserving-null-calibration",
    ]
    assert by_id["PAPA-06"]["pertura_skills"] == [
        "operate-pertura-workflow",
        "run-replicate-aware-pseudobulk-de",
    ]
    assert by_id["PAPA-07"]["pertura_skills"] == ["interpret-perturb-seq-results"]
    assert by_id["KANG-01"]["split_usage"] == ["calibration", "evaluation"]
    assert "calibration_split" in by_id["KANG-01"]["required_input_roles"]
    assert (
        by_id["KANG-01"]["codeact_protocol"]["input_role_bindings"][
            "calibration_selection"
        ]
        == "calibration_split"
    )
    assert by_id["KANG-01"]["codeact_protocol"]["target"] == "stim"
    for task_id in ("KANG-01", "PAPA-06"):
        protocol = by_id[task_id]["codeact_protocol"]
        assert protocol["environment_profile"] == "edger-v1"
        assert protocol["environment_variable"] == "PERTURA_EDGER_ENV"
        assert protocol["entrypoint"] == "Rscript"
    assert by_id["KANG-01"]["codeact_protocol"]["gene_identity"] == (
        "adata.var_names"
    )
    assert by_id["PAPA-06"]["codeact_protocol"]["gene_identity"] == (
        "registered_gene_id"
    )
    assert by_id["KANG-01"]["codeact_protocol"]["column_bindings"] == {
        "cell_id_column": "cell_id",
        "selection_cell_id_column": "cell_id",
        "unit_column": "ind",
        "condition_column": "stim",
    }


def test_formal_server_plan_rejects_unbound_paper_catalogs() -> None:
    with pytest.raises(ValueError, match="task-reference catalog is not bound"):
        server_benchmark_plan(
            ROOT,
            paper_task_catalog_path=CATALOG,
            paper_task_reference_catalog_path=REFERENCES,
            paper_anchor_catalog_path=ANCHORS,
            paper_asset_catalog_path=ANCHORS,
        )
