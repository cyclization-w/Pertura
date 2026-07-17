from __future__ import annotations

import json
import re
from pathlib import Path

from pertura_bench import paper_agent_execution as execution
from pertura_bench.agent_models import AgentBenchmarkResult
from pertura_bench.cli import _exit_code
from pertura_core import DatasetContract
from pertura_core.hashing import canonical_hash, file_sha256
from pertura_workflow.capabilities import CapabilityRegistry


ROOT = Path(__file__).resolve().parents[2]


class _Runtime:
    def __init__(self):
        self.registry = CapabilityRegistry.load_default(include_external=False)
        self.contract = DatasetContract(
            dataset_id="fixture-dataset",
            input_format="h5ad",
            expression_matrix={"path": "fixture.h5ad"},
            identity_fields={
                "control": {"status": "confirmed", "value": ["NTC"]},
                "replicate": {
                    "status": "confirmed",
                    "value": ["rep1", "rep2"],
                },
            },
        )

    def inspect_dataset(self, *args, **kwargs):
        return {"contract_id": self.contract.contract_id}

    def planning_material(self, contract_id=None):
        assert contract_id == self.contract.contract_id
        return self.contract, ()

    def close(self, graceful=True):
        return None


class _Agent:
    def __init__(self, *, workspace, **kwargs):
        self.workspace = workspace
        self.product_runtime = _Runtime()
        self.turn_manager = None

    def configure_completion_guard(self, output_root):
        self.completion_output_root = output_root

    def completion_guard_snapshot(self):
        return {
            "schema_version": "pertura-benchmark-completion-guard-v1",
            "enabled": True,
            "triggered": False,
            "expensive_calls": 0,
            "closure_calls": 0,
            "completion_reads": 0,
            "denied_calls": 0,
        }


def _asset_catalog(tmp_path: Path) -> Path:
    catalog = json.loads(
        (ROOT / "benchmarks/paper_v1/agent_tasks.v2.json").read_text()
    )
    bound_workflows = {}
    for workflow in catalog["workflows"]:
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

        roles = set()
        for task in workflow["turns"]:
            internal = {
                role
                for dependency in ancestors(task)
                for role in by_task[dependency]["required_artifact_roles"]
            }
            if task.get("role") != "optional":
                roles.update(set(task["required_input_roles"]) - internal)
        assets = []
        for role in sorted(roles):
            relative = f"{workflow['workflow_id']}/{role}"
            path = tmp_path / "cache" / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(role.encode())
            assets.append(
                {
                    "role": role,
                    "root": "cache",
                    "relative_path": relative,
                    "content_sha256": file_sha256(path),
                    "kind": "observed",
                }
            )
        bound_workflows[workflow["workflow_id"]] = {"assets": assets}
    payload = {
        "schema_version": "pertura-paper-agent-assets-v1",
        "status": "bound",
        "passed": True,
        "problems": [],
        "workflows": bound_workflows,
    }
    path = tmp_path / "assets.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _bound_task_references(tmp_path: Path) -> Path:
    payload = json.loads(
        (ROOT / "benchmarks/paper_v1/task_references.v1.json").read_text()
    )
    payload.update(
        {
            "schema_version": "pertura-paper-task-reference-catalog-bound-v1",
            "status": "bound",
            "passed": True,
            "problems": [],
        }
    )
    for binding in payload["bindings"]:
        binding["bound_reference_sources"] = [
            {
                "reference_id": source,
                "manifest_sha256": "sha256:" + "1" * 64,
                "pack_tree_sha256": "sha256:" + "2" * 64,
            }
            for source in binding["reference_sources"]
        ]
        if binding["scoring_route"] in {"artifact_evaluator", "hybrid"}:
            binding["evaluators"] = [{"fixture": True}]
        if binding["scoring_route"] == "custom_artifact_evaluator":
            binding["bound_evaluator"] = {"type": "fixture"}
    path = tmp_path / "bound-task-references.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_task_prompt_separates_result_file_from_turn_draft() -> None:
    catalog = json.loads(
        (ROOT / "benchmarks/paper_v1/agent_tasks.v2.json").read_text()
    )
    for candidate_workflow in catalog["workflows"]:
        for candidate_task in candidate_workflow["turns"]:
            candidate = execution._benchmark_result_template(
                workflow=candidate_workflow,
                task=candidate_task,
            )
            AgentBenchmarkResult.model_validate(candidate)
            assert candidate["artifact_roles"] == candidate_task[
                "required_artifact_roles"
            ]

    workflow = next(
        item for item in catalog["workflows"] if item["workflow_id"] == "WF-KANG"
    )
    task = next(
        item for item in workflow["turns"] if item["task_id"] == "KANG-01"
    )

    template = execution._benchmark_result_template(
        workflow=workflow,
        task=task,
    )
    result = AgentBenchmarkResult.model_validate(template)

    assert set(template) == {
        "schema_version",
        "case_id",
        "dataset_id",
        "result_type",
        "analysis_unit",
        "status",
        "findings",
        "metrics",
        "limitations",
        "artifact_roles",
    }
    assert result.case_id == "KANG-01"
    assert list(result.artifact_roles) == task["required_artifact_roles"]
    assert isinstance(template["artifact_roles"], list)
    assert set(template["findings"][0]) == {
        "finding_id",
        "text",
        "metric_ids",
        "artifact_roles",
    }

    for condition in ("pertura_full", "prompt_only", "free_codeact"):
        prompt = execution._task_prompt(
            workflow=workflow,
            task=task,
            condition=condition,
            asset_paths={},
            anchors_by_id={
                anchor_id: {"anchor_id": anchor_id}
                for anchor_id in task["paper_anchor_ids"]
            },
            dependency_contracts={},
        )
        assert "standalone pertura-agent-benchmark-result-v1 JSON file" in prompt
        assert "separate provider response" in prompt
        assert "pertura-turn-draft-v1" in prompt
        assert "Never copy the TurnDraft object" in prompt
        assert "artifact_roles must be a JSON array" in prompt
        assert "hypotheses, questions_for_user, next_steps" in prompt


def test_scientific_failure_does_not_fail_completed_scheduler_job() -> None:
    payload = {
        "schema_version": "pertura-paper-workflow-execution-v1",
        "execution_status": "completed",
        "score_status": "failed",
        "status": "failed",
    }
    assert _exit_code("agent", payload) == 0
    assert (
        _exit_code(
            "agent",
            payload | {"execution_status": "incomplete"},
        )
        == 1
    )
    assert _exit_code("agent", {"status": "failed"}) == 1


def test_paper_asset_kinds_adapt_to_product_registry() -> None:
    expected = {
        "observed": ("observed", "observed_metadata"),
        "derived": ("derived", "measured_result"),
        "exploratory": ("exploratory", "hypothesis"),
        "external_resource": ("external_resource", "curated_prior"),
        "environment_lock": ("external_resource", "curated_prior"),
        "executable": ("external_resource", "curated_prior"),
        "protocol": ("external_resource", "curated_prior"),
        "reference_lock": ("external_resource", "curated_prior"),
        "prior": ("external_resource", "curated_prior"),
    }
    assert execution.PAPER_ASSET_KIND_ADAPTER == expected


def test_environment_lock_registers_as_external_resource(tmp_path: Path) -> None:
    project = execution.ProjectWorkspace.initialize(
        tmp_path / "project",
        logical_name="asset-adapter",
    )
    run = project.create_run(logical_name="asset adapter")
    registry = execution.DataAssetRegistry(
        project_id=project.project.project_id,
        store=project.store,
        object_root=project.objects_dir,
    )
    lock = tmp_path / "paper" / "environment-lock.json"
    lock.parent.mkdir(parents=True)
    lock.write_text('{"lock": "fixture"}\n', encoding="utf-8")

    registered, paths = execution._register_workflow_assets(
        registry,
        project=project,
        run_id=run.run_id,
        raw_assets=(
            {
                "role": "edgeR_environment_lock",
                "root": "paper_root",
                "relative_path": lock.name,
                "content_sha256": file_sha256(lock),
                "kind": "environment_lock",
            },
        ),
        cache=tmp_path / "cache",
        paper_root=lock.parent,
    )

    assert len(registered) == 1
    assert registered[0].kind == "external_resource"
    assert registered[0].source_class == "curated_prior"
    assert paths == {"edgeR_environment_lock": str(lock.resolve())}


def test_smoke_task_selection_runs_only_requested_turn(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(execution, "ClaudePerturaAgent", _Agent)
    monkeypatch.setattr(
        execution,
        "_resource_evidence",
        lambda path: {
            "mode": "scheduler",
            "scheduler_job_id": "fixture-job",
            "requested_memory_gb": 8,
            "actual_memory_gb": 8,
            "n_jobs": 1,
            "timeout_seconds": 7200,
            "peak_rss_mb": 100,
        },
    )
    invoked = []
    prompts = []

    def execute(agent, prompt, timeout):
        invoked.append(re.search(r"task (REPL-\d+)", prompt).group(1))
        prompts.append(prompt)

    result = execution.run_paper_agent_workflow(
        "WF-REPL",
        repo_root=ROOT,
        cache=tmp_path / "cache",
        paper_root=tmp_path / "paper",
        output=tmp_path / "runs",
        condition="prompt_only",
        repeat_index=1,
        task_catalog_path=ROOT / "benchmarks/paper_v1/agent_tasks.v2.json",
        task_reference_catalog_path=_bound_task_references(tmp_path),
        paper_anchor_catalog_path=ROOT
        / "benchmarks/paper_v1/paper_anchors.v1.json",
        asset_catalog_path=_asset_catalog(tmp_path),
        resource_evidence_path=tmp_path / "resource.json",
        smoke_task_ids=("REPL-03",),
        turn_executor=execute,
        verify_checkpoint=False,
    )

    assert invoked == ["REPL-03"]
    assert "isolated non-formal smoke" in prompts[0]
    assert "do not recreate, repair, or inspect them" in prompts[0]
    assert "Upstream repair contracts: {}" in prompts[0]
    assert "You may repair a missing upstream artifact" not in prompts[0]
    assert result["smoke_task_ids"] == ["REPL-03"]
    assert result["required_task_count"] == 1
    manifest = json.loads(
        (Path(result["execution_root"]) / "input_manifest.json").read_text()
    )
    assert manifest["smoke_task_ids"] == ["REPL-03"]


def test_smoke_task_selection_rejects_unknown_task(tmp_path: Path) -> None:
    try:
        execution.run_paper_agent_workflow(
            "WF-REPL",
            repo_root=ROOT,
            cache=tmp_path / "cache",
            paper_root=tmp_path / "paper",
            output=tmp_path / "runs",
            condition="prompt_only",
            repeat_index=1,
            task_catalog_path=ROOT / "benchmarks/paper_v1/agent_tasks.v2.json",
            task_reference_catalog_path=tmp_path / "unused.json",
            paper_anchor_catalog_path=tmp_path / "unused.json",
            asset_catalog_path=tmp_path / "unused.json",
            smoke_task_ids=("REPL-99",),
            turn_executor=lambda *args: None,
            verify_checkpoint=False,
        )
    except ValueError as exc:
        assert "unknown smoke task IDs" in str(exc)
    else:
        raise AssertionError("unknown smoke task was accepted")


def test_evidence_interpretation_prompt_forbids_recomputation() -> None:
    catalog = json.loads(
        (ROOT / "benchmarks/paper_v1/agent_tasks.v2.json").read_text()
    )
    workflow = next(
        item for item in catalog["workflows"] if item["workflow_id"] == "WF-PAPA"
    )
    task = next(
        item for item in workflow["turns"] if item["task_id"] == "PAPA-07"
    )
    prompt = execution._task_prompt(
        workflow=workflow,
        task=task,
        condition="pertura_full",
        asset_paths={},
        anchors_by_id={
            anchor_id: {"anchor_id": anchor_id}
            for anchor_id in task["paper_anchor_ids"]
        },
        dependency_contracts={"PAPA-03": {"artifact_paths": {}}},
        isolated_smoke=True,
    )

    assert "evidence-interpretation task" in prompt
    assert "do not recompute or refit the frozen evidence" in prompt
    assert 'Upstream repair contracts: {}' in prompt
    assert (
        '"global_effect_claims": '
        '"outputs/tasks/PAPA-07/global_effect_claims.tsv"'
    ) in prompt
    assert (
        '"global_effect_limitations": '
        '"outputs/tasks/PAPA-07/global_effect_limitations.json"'
    ) in prompt
    assert "Do not write them directly under outputs/." in prompt


def test_capability_brief_is_disclosed_only_to_pertura_full() -> None:
    catalog = json.loads(
        (ROOT / "benchmarks/paper_v1/agent_tasks.v2.json").read_text()
    )
    workflow = next(
        item for item in catalog["workflows"] if item["workflow_id"] == "WF-PAPA"
    )
    task = next(
        item for item in workflow["turns"] if item["task_id"] == "PAPA-01"
    )
    brief = {
        "plan_id": "plan_fixture",
        "plan_hash": "sha256:" + "1" * 64,
        "route": "capability_or_codeact",
        "dataset_contract": {
            "contract_id": "contract_fixture",
            "contract_hash": "sha256:" + "2" * 64,
        },
        "active_window": [
            {
                "capability_id": "guide.integrity.v1",
                "status": "blocked",
            }
        ],
        "codeact_handoff": {
            "protocol_id": "pseudobulk.edger_ql.v1",
            "handoff_hash": "sha256:" + "3" * 64,
            "status": "ready",
            "blockers": [],
            "method_family": "edgeR_ql_pseudobulk",
            "binding": {
                "analysis_unit": "donor",
                "design": "~ donor + condition",
                "pairing": "required",
            },
            "environment": {
                "profile": "edger-v1",
                "variable": "PERTURA_EDGER_ENV",
                "ready": True,
            },
            "invocation": {
                "script_path": "outputs/tasks/PAPA-01/run_edger.R",
                "command": "fixture-edger-command",
            },
            "outputs": {
                "benchmark_result": (
                    "outputs/tasks/PAPA-01/benchmark_result.json"
                )
            },
            "authority": {
                "source_class": "exploratory",
                "capability_receipt": False,
            },
        },
        "completion_checklist": ["write result"],
        "stop_conditions": ["do not inspect YAML"],
    }
    common = {
        "workflow": workflow,
        "task": task,
        "asset_paths": {},
        "anchors_by_id": {
            anchor_id: {"anchor_id": anchor_id}
            for anchor_id in task["paper_anchor_ids"]
        },
        "dependency_contracts": {},
        "execution_brief": brief,
    }

    full = execution._task_prompt(condition="pertura_full", **common)
    prompt = execution._task_prompt(condition="prompt_only", **common)
    free = execution._task_prompt(condition="free_codeact", **common)

    assert "plan_fixture" in full
    assert "task/capability_plans/PAPA-01.json" in full
    assert "Do not call inspect_dataset again" in full
    assert "fixture-edger-command" in full
    assert "do not probe rpy2" in full
    assert "plan_fixture" not in prompt
    assert "plan_fixture" not in free
    assert "fixture-edger-command" not in prompt
    assert "fixture-edger-command" not in free
    for text in (full, prompt, free):
        assert "completion guard" in text


def test_pertura_full_runner_writes_answer_free_task_brief(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(execution, "ClaudePerturaAgent", _Agent)
    monkeypatch.setattr(
        execution,
        "_paper_environment_readiness",
        lambda registry, capability_ids, **kwargs: {
            "python-science-v1": True,
            "perturbseq-python-v1": True,
        },
    )
    monkeypatch.setattr(
        execution,
        "_resource_evidence",
        lambda path: {
            "mode": "scheduler",
            "scheduler_job_id": "fixture-job",
            "requested_memory_gb": 8,
            "actual_memory_gb": 8,
            "n_jobs": 1,
            "timeout_seconds": 7200,
            "peak_rss_mb": 100,
        },
    )
    prompts = []

    def execute(agent, prompt, timeout):
        prompts.append(prompt)
        task_root = agent.workspace.root / "outputs/tasks/PAPA-01"
        task_root.mkdir(parents=True, exist_ok=True)
        task_root.joinpath("benchmark_result.json").write_text(
            json.dumps(
                {
                    "schema_version": "pertura-agent-benchmark-result-v1",
                    "case_id": "PAPA-01",
                    "dataset_id": "papalexi_thp1_eccite",
                    "result_type": "fixture",
                    "analysis_unit": "cell",
                    "status": "completed",
                    "findings": [],
                    "metrics": {},
                    "limitations": ["fixture"],
                    "artifact_roles": [
                        "guide_assignment",
                        "ambient_qc",
                        "retained_cell_manifest",
                        "alignment_audit",
                    ],
                }
            ),
            encoding="utf-8",
        )

    result = execution.run_paper_agent_workflow(
        "WF-PAPA",
        repo_root=ROOT,
        cache=tmp_path / "cache",
        paper_root=tmp_path / "paper",
        output=tmp_path / "runs",
        condition="pertura_full",
        repeat_index=1,
        task_catalog_path=ROOT / "benchmarks/paper_v1/agent_tasks.v2.json",
        task_reference_catalog_path=_bound_task_references(tmp_path),
        paper_anchor_catalog_path=ROOT
        / "benchmarks/paper_v1/paper_anchors.v1.json",
        asset_catalog_path=_asset_catalog(tmp_path),
        resource_evidence_path=tmp_path / "resource.json",
        smoke_task_ids=("PAPA-01",),
        turn_executor=execute,
        verify_checkpoint=False,
    )

    root = Path(result["execution_root"])
    manifest = json.loads((root / "input_manifest.json").read_text())
    brief_record = manifest["capability_execution_briefs"][0]
    brief_path = root / "project/.pertura/runs"
    generated = list(brief_path.rglob("task/capability_plans/PAPA-01.json"))
    assert len(generated) == 1
    brief = json.loads(generated[0].read_text())

    assert brief_record["task_id"] == "PAPA-01"
    assert brief_record["plan_hash"] == brief["plan_hash"]
    expected = next(
        task["expected_capability_dag"]
        for workflow in json.loads(
            (ROOT / "benchmarks/paper_v1/agent_tasks.v2.json").read_text()
        )["workflows"]
        for task in workflow["turns"]
        if task["task_id"] == "PAPA-01"
    )
    assert brief["candidate_capability_ids"] == expected
    serialized = json.dumps(brief).lower()
    for forbidden in (
        "grader",
        "task_reference",
        "reference_sources",
        "expected answer",
        "evaluation truth",
    ):
        assert forbidden not in serialized
    assert brief["assets"]
    assert all(item.get("asset_id") for item in brief["assets"].values())
    assert brief["plan_id"] in prompts[0]


def test_environment_readiness_is_candidate_scoped_and_manifest_only(
    tmp_path: Path, monkeypatch
) -> None:
    registry = CapabilityRegistry.load_default(include_external=False)
    prefix = tmp_path / "edger-v1"
    prefix.mkdir()
    manifest = {
        "schema_version": "pertura-environment-manifest-v2",
        "profile": "edger-v1",
        "platform": "fixture",
        "resource_hashes": {},
        "expected_versions": {},
    }
    manifest["lock_hash"] = canonical_hash(manifest)
    prefix.joinpath("pertura-environment-manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    requested_profiles = []

    def resolve_prefix(profile: str) -> Path:
        requested_profiles.append(profile)
        assert profile == "edger-v1"
        return prefix

    monkeypatch.setattr(execution, "environment_prefix", resolve_prefix)
    readiness = execution._paper_environment_readiness(
        registry,
        ("de.pseudobulk.edger.v1",),
    )

    assert readiness == {"edger-v1": True}
    assert requested_profiles == ["edger-v1"]


def test_workflow_reuses_one_session_and_isolates_task_outputs(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(execution, "ClaudePerturaAgent", _Agent)
    monkeypatch.setattr(
        execution,
        "_resource_evidence",
        lambda path: {
            "mode": "scheduler",
            "scheduler_job_id": "fixture-job",
            "requested_memory_gb": 8,
            "actual_memory_gb": 8,
            "n_jobs": 1,
            "timeout_seconds": 7200,
            "peak_rss_mb": 100,
        },
    )
    catalog = json.loads(
        (ROOT / "benchmarks/paper_v1/agent_tasks.v2.json").read_text()
    )
    repl_tasks = {
        task["task_id"]: task
        for task in catalog["workflows"][0]["turns"]
    }

    def execute(agent, prompt, timeout):
        task_id = re.search(r"task (REPL-\d+)", prompt).group(1)
        task = repl_tasks[task_id]
        task_root = agent.workspace.root / "outputs" / "tasks" / task_id
        task_root.mkdir(parents=True, exist_ok=True)
        (task_root / "benchmark_result.json").write_text(
            json.dumps(
                {
                    "schema_version": "pertura-agent-benchmark-result-v1",
                    "case_id": task_id,
                    "dataset_id": "replogle_k562_essential_2022",
                    "result_type": "fixture",
                    "analysis_unit": "target",
                    "status": "completed",
                    "findings": [
                        {
                            "finding_id": "f1",
                            "text": "fixture",
                            "artifact_roles": task["required_artifact_roles"],
                        }
                    ],
                    "metrics": {},
                    "limitations": ["fixture"],
                    "artifact_roles": task["required_artifact_roles"],
                }
            ),
            encoding="utf-8",
        )

    asset_catalog = _asset_catalog(tmp_path)
    task_references = _bound_task_references(tmp_path)
    result = execution.run_paper_agent_workflow(
        "WF-REPL",
        repo_root=ROOT,
        cache=tmp_path / "cache",
        paper_root=tmp_path / "paper",
        output=tmp_path / "runs",
        condition="prompt_only",
        repeat_index=1,
        task_catalog_path=ROOT / "benchmarks/paper_v1/agent_tasks.v2.json",
        task_reference_catalog_path=task_references,
        paper_anchor_catalog_path=ROOT / "benchmarks/paper_v1/paper_anchors.v1.json",
        asset_catalog_path=asset_catalog,
        resource_evidence_path=tmp_path / "resource.json",
        turn_executor=execute,
        verify_checkpoint=False,
    )
    root = Path(result["execution_root"])
    manifest = json.loads((root / "input_manifest.json").read_text())
    assert result["execution_status"] == "completed"
    assert result["score_status"] == result["status"]
    assert result["smoke_task_ids"] is None
    assert manifest["max_turns_per_task"] == 32
    assert len(result["task_records"]) == 4
    assert len({manifest["project_id"]}) == 1
    assert len({manifest["analysis_run_id"]}) == 1
    assert len({manifest["conversation_id"]}) == 1
    assert {
        path.parent.name
        for path in (root / "tasks").glob("*/benchmark_result.json")
    } == set(repl_tasks)
    assert all(
        (root / "tasks" / task_id / "verdict.json").is_file()
        for task_id in repl_tasks
    )

    second = execution.run_paper_agent_workflow(
        "WF-REPL",
        repo_root=ROOT,
        cache=tmp_path / "cache",
        paper_root=tmp_path / "paper",
        output=tmp_path / "runs",
        condition="free_codeact",
        repeat_index=1,
        task_catalog_path=ROOT / "benchmarks/paper_v1/agent_tasks.v2.json",
        task_reference_catalog_path=task_references,
        paper_anchor_catalog_path=ROOT / "benchmarks/paper_v1/paper_anchors.v1.json",
        asset_catalog_path=asset_catalog,
        resource_evidence_path=tmp_path / "resource.json",
        turn_executor=execute,
        verify_checkpoint=False,
    )
    assert second["execution_root"] != result["execution_root"]


def test_workflow_resource_gate_accepts_maximum_turn_allocation() -> None:
    task = {
        "resources": {
            "max_memory_gb": 4,
            "n_jobs": 1,
            "timeout_seconds": 600,
        }
    }
    evidence = {
        "mode": "scheduler",
        "scheduler_job_id": "job-1",
        "requested_memory_gb": 8,
        "actual_memory_gb": 8,
        "n_jobs": 1,
        "timeout_seconds": 7200,
        "peak_rss_mb": 1024,
    }
    assert execution._task_resource_gate(task, evidence) is True
    assert (
        execution._task_resource_gate(
            task,
            evidence | {"actual_memory_gb": 4, "peak_rss_mb": 5000},
        )
        is False
    )

    rlimit_evidence = {
        "mode": "rlimit",
        "enforcement_active": True,
        "rlimit_identity": "pid:123:RLIMIT_AS",
        "rlimit_as_bytes": 8 * 1024**3,
        "requested_memory_gb": 8,
        "actual_memory_gb": 8,
        "n_jobs": 1,
        "timeout_seconds": 7200,
        "peak_rss_mb": 1024,
    }
    assert execution._task_resource_gate(task, rlimit_evidence) is True
    assert (
        execution._task_resource_gate(
            task, rlimit_evidence | {"enforcement_active": False}
        )
        is False
    )


def test_workflow_regrades_later_additive_upstream_repair(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(execution, "ClaudePerturaAgent", _Agent)
    monkeypatch.setattr(
        execution,
        "_resource_evidence",
        lambda path: {
            "mode": "scheduler",
            "scheduler_job_id": "fixture-job",
            "requested_memory_gb": 8,
            "actual_memory_gb": 8,
            "n_jobs": 1,
            "timeout_seconds": 7200,
            "peak_rss_mb": 100,
        },
    )
    evaluation_calls: list[str] = []

    def evaluate(task, *args, **kwargs):
        evaluation_calls.append(str(task["task_id"]))
        return {"status": "passed", "problems": []}

    monkeypatch.setattr(execution, "evaluate_paper_task", evaluate)

    def execute(agent, prompt, timeout):
        if "task KANG-01" in prompt:
            return None
        root = agent.workspace.root / "outputs/tasks/KANG-01"
        root.mkdir(parents=True, exist_ok=True)
        (root / "pseudobulk_counts.tsv").write_text(
            "gene\tsample\tcount\nG1\td1_ctrl\t10\n", encoding="utf-8"
        )
        (root / "design_matrix.tsv").write_text(
            "sample\tdonor\tcondition\nd1_ctrl\td1\tctrl\n",
            encoding="utf-8",
        )
        (root / "de_results.tsv").write_text(
            "gene\tlogFC\tF\tPValue\tFDR\nG1\t1\t2\t0.01\t0.02\n",
            encoding="utf-8",
        )
        (root / "null_calibration.tsv").write_text(
            "permutation_id\ttype1_rate\tnull_effect_bias\t"
            "exchangeability_violation_count\n1\t0.05\t0\t0\n",
            encoding="utf-8",
        )
        (root / "benchmark_result.json").write_text(
            json.dumps(
                {
                    "schema_version": "pertura-agent-benchmark-result-v1",
                    "case_id": "KANG-01",
                    "dataset_id": "kang18_8vs8_pbmc",
                    "result_type": "donor_aware_de",
                    "analysis_unit": "donor",
                    "status": "completed",
                    "limitations": ["four donors"],
                    "artifact_roles": [
                        "pseudobulk_counts",
                        "design_matrix",
                        "de_results",
                        "null_calibration",
                    ],
                }
            ),
            encoding="utf-8",
        )

    result = execution.run_paper_agent_workflow(
        "WF-KANG",
        repo_root=ROOT,
        cache=tmp_path / "cache",
        paper_root=tmp_path / "paper",
        output=tmp_path / "runs",
        condition="free_codeact",
        repeat_index=1,
        task_catalog_path=ROOT / "benchmarks/paper_v1/agent_tasks.v2.json",
        task_reference_catalog_path=_bound_task_references(tmp_path),
        paper_anchor_catalog_path=ROOT
        / "benchmarks/paper_v1/paper_anchors.v1.json",
        asset_catalog_path=_asset_catalog(tmp_path),
        resource_evidence_path=tmp_path / "resource.json",
        turn_executor=execute,
        verify_checkpoint=False,
    )
    root = Path(result["execution_root"])
    first = json.loads((root / "tasks/KANG-01/verdict.json").read_text())
    second = json.loads((root / "tasks/KANG-02/verdict.json").read_text())

    assert first["post_workflow_regraded"] is True
    assert first["repaired_after_turn"] is True
    assert first["result_problem"] is None
    assert first["hard_gates"]["benchmark_result_schema_valid"] is True
    assert first["hard_gates"]["required_artifact_paths"] is True
    assert (root / "tasks/KANG-01/benchmark_result.json").is_file()
    assert second["hard_gates"]["dependencies_present"] is True
    assert evaluation_calls == ["KANG-01", "KANG-02", "KANG-01"]


def test_paper_science_python_uses_frozen_environment(
    tmp_path: Path, monkeypatch
) -> None:
    prefix = tmp_path / "python-science-v1"
    python = prefix / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_bytes(b"")
    monkeypatch.setenv("PERTURA_PYTHON_SCIENCE_ENV", str(prefix))

    assert execution._paper_science_python() == str(python.resolve())
    assert "pertpy" not in execution.PAPER_CODEACT_PACKAGES
    assert "decoupler" not in execution.PAPER_CODEACT_PACKAGES


def test_judge_task_context_resolves_declared_paper_anchors() -> None:
    task_catalog = json.loads(
        (ROOT / "benchmarks/paper_v1/agent_tasks.v2.json").read_text()
    )
    anchor_catalog = json.loads(
        (ROOT / "benchmarks/paper_v1/paper_anchors.v1.json").read_text()
    )
    workflow = next(
        item for item in task_catalog["workflows"]
        if item["workflow_id"] == "WF-KANG"
    )
    task = next(
        item for item in workflow["turns"] if item["task_id"] == "KANG-01"
    )
    anchors_by_id = {
        item["anchor_id"]: item for item in anchor_catalog["anchors"]
    }

    context = execution._judge_task_context(
        workflow=workflow,
        task=task,
        anchors_by_id=anchors_by_id,
    )

    assert context["case_id"] == "KANG-01"
    assert context["dataset_id"] == "kang18_8vs8_pbmc"
    assert [item["anchor_id"] for item in context["paper_anchors"]] == [
        "ANCHOR-KANG-DONOR"
    ]


def test_artifact_contract_checks_table_headers_and_json_fields(
    tmp_path: Path,
) -> None:
    (tmp_path / "result.tsv").write_text(
        "cell_id\tstate\nc1\tkept\n", encoding="utf-8"
    )
    (tmp_path / "summary.json").write_text(
        json.dumps({"count": 1}), encoding="utf-8"
    )
    contract = {
        "artifact_roles": ["result", "summary"],
        "artifact_paths": {
            "result": "result.tsv",
            "summary": "summary.json",
        },
        "artifact_schemas": {
            "result.tsv": ["cell_id", "state"],
            "summary.json": ["count"],
        },
    }
    assert execution._artifact_paths_present(tmp_path, contract) is True
    contract["artifact_schemas"]["result.tsv"].append("missing")
    assert execution._artifact_paths_present(tmp_path, contract) is False


def test_regrade_never_invokes_provider(tmp_path: Path) -> None:
    root = tmp_path / "run"
    root.mkdir()
    (root / "input_manifest.json").write_text(
        json.dumps(
            {
                "workflow": {
                    "workflow_id": "WF-X",
                    "dataset_id": "D",
                    "turns": [{"task_id": "T", "objective": "O"}],
                }
            }
        ),
        encoding="utf-8",
    )
    (root / "tasks/T").mkdir(parents=True)
    (root / "tasks/T/verdict.json").write_text("{}", encoding="utf-8")
    result = execution.regrade_paper_agent_workflow(root)
    assert result["provider_invoked"] is False
    assert result["task_records"] == [
        {"task_id": "T", "status": "judge_unavailable"}
    ]


def test_dependency_assets_include_transitive_ancestors(tmp_path: Path) -> None:
    tasks = {
        "A": {
            "task_id": "A",
            "depends_on_tasks": [],
            "output_contract": {
                "artifact_paths": {"a_result": "a.tsv"},
            },
        },
        "B": {
            "task_id": "B",
            "depends_on_tasks": ["A"],
            "output_contract": {
                "artifact_paths": {"b_result": "b.json"},
            },
        },
        "C": {
            "task_id": "C",
            "depends_on_tasks": ["B"],
            "output_contract": {"artifact_paths": {}},
        },
    }
    a_path = tmp_path / "outputs/tasks/A/a.tsv"
    b_path = tmp_path / "outputs/tasks/B/b.json"
    a_path.parent.mkdir(parents=True)
    b_path.parent.mkdir(parents=True)
    a_path.write_text("value\n1\n", encoding="utf-8")
    b_path.write_text("{}\n", encoding="utf-8")

    observed = execution._dependency_asset_paths(
        tmp_path,
        task=tasks["C"],
        tasks_by_id=tasks,
    )
    assert observed == {
        "a_result": str(a_path.resolve()),
        "b_result": str(b_path.resolve()),
    }


def test_dependency_gate_requires_complete_upstream_contract(
    tmp_path: Path,
) -> None:
    tasks = {
        "A": {
            "task_id": "A",
            "depends_on_tasks": [],
            "required_artifact_roles": ["table"],
            "output_contract": {
                "artifact_roles": ["table"],
                "artifact_paths": {"table": "table.tsv"},
                "artifact_schemas": {"table.tsv": ["value"]},
            },
        },
        "B": {
            "task_id": "B",
            "depends_on_tasks": ["A"],
            "required_artifact_roles": [],
            "output_contract": {},
        },
    }
    output = tmp_path / "outputs/tasks/A"
    output.mkdir(parents=True)
    (output / "benchmark_result.json").write_text(
        json.dumps(
            {
                "schema_version": "pertura-agent-benchmark-result-v1",
                "case_id": "A",
                "dataset_id": "D",
                "result_type": "fixture",
                "analysis_unit": "donor",
                "status": "completed",
                "artifact_roles": ["table"],
            }
        ),
        encoding="utf-8",
    )
    assert (
        execution._dependency_outputs_complete(
            tmp_path,
            task=tasks["B"],
            tasks_by_id=tasks,
            dataset_id="D",
        )
        is False
    )

    (output / "table.tsv").write_text("value\n1\n", encoding="utf-8")
    assert (
        execution._dependency_outputs_complete(
            tmp_path,
            task=tasks["B"],
            tasks_by_id=tasks,
            dataset_id="D",
        )
        is True
    )


def test_prior_output_guard_allows_additive_repair_only() -> None:
    before = {"benchmark_result.json": "sha256:old"}
    repaired = {
        "benchmark_result.json": "sha256:old",
        "missing_dependency.tsv": "sha256:new",
    }
    assert execution._existing_files_unchanged(before, repaired) is True
    assert (
        execution._existing_files_unchanged(
            before,
            {"benchmark_result.json": "sha256:mutated"},
        )
        is False
    )
    assert execution._existing_files_unchanged(before, {}) is False
