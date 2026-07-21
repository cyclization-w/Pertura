from __future__ import annotations

import json
import inspect
import re
from pathlib import Path
from types import SimpleNamespace

from pertura_bench import paper_agent_execution as execution
from pertura_bench.agent_models import AgentBenchmarkResult
from pertura_bench.cli import _exit_code
from pertura_bench.task_submission import TaskSubmissionService
from pertura_core import DatasetContract
from pertura_core.hashing import file_sha256
from pertura_workflow.capabilities import CapabilityRegistry


ROOT = Path(__file__).resolve().parents[2]
PROMPT_WORKSPACE = (
    ROOT / ".test-paper-workspace" / ".pertura" / "runs" / "run-fixture"
).resolve()


def test_codeact_task_prompt_freezes_environment_for_all_conditions() -> None:
    catalog = json.loads((ROOT / "benchmarks/paper_v1/agent_tasks.v2.json").read_text())
    workflow = next(
        item for item in catalog["workflows"] if item["workflow_id"] == "WF-KANG"
    )
    task = next(item for item in workflow["turns"] if item["task_id"] == "KANG-01")
    anchors = {
        anchor_id: {"anchor_id": anchor_id} for anchor_id in task["paper_anchor_ids"]
    }

    for condition in ("pertura_full", "prompt_only", "free_codeact"):
        prompt = execution._task_prompt(
            workflow=workflow,
            task=task,
            condition=condition,
            workspace_root=PROMPT_WORKSPACE,
            asset_paths={},
            anchors_by_id=anchors,
            dependency_contracts={},
            contract_context={} if condition == "pertura_full" else None,
            contract_subset_record=(
                {"path": "task/capability_contracts/KANG-01.json"}
                if condition == "pertura_full"
                else None
            ),
        )

        assert "edger-v1" in prompt
        assert "$PERTURA_EDGER_ENV/bin/Rscript" in prompt
        assert "Do not install scientific packages" in prompt
        assert "load an alternative module or runtime" in prompt


def test_task_prompt_publishes_one_absolute_canonical_output_root() -> None:
    catalog = json.loads((ROOT / "benchmarks/paper_v1/agent_tasks.v2.json").read_text())

    for workflow in catalog["workflows"]:
        for task in workflow["turns"]:
            task_root = (
                PROMPT_WORKSPACE / "outputs" / "tasks" / task["task_id"]
            ).as_posix()
            anchors = {
                anchor_id: {"anchor_id": anchor_id}
                for anchor_id in task["paper_anchor_ids"]
            }
            for condition in ("pertura_full", "prompt_only", "free_codeact"):
                prompt = execution._task_prompt(
                    workflow=workflow,
                    task=task,
                    condition=condition,
                    workspace_root=PROMPT_WORKSPACE,
                    asset_paths={},
                    anchors_by_id=anchors,
                    dependency_contracts={},
                )

                assert f"Canonical task output root (absolute): {task_root}" in prompt
                assert "exact workspace-relative destinations" not in prompt
                assert (
                    "do not write to a similarly named project-level "
                    "outputs/tasks path"
                ) in prompt
                for relative in task["output_contract"]["artifact_paths"].values():
                    assert f"{task_root}/{relative}" in prompt


def test_task_asset_manifest_is_task_scoped_and_baseline_hides_asset_ids(
    tmp_path: Path,
) -> None:
    primary = tmp_path / "primary.h5ad"
    unrelated = tmp_path / "global_effect_reference_lock.json"
    primary.write_bytes(b"primary")
    unrelated.write_bytes(b"reference")
    paths = {
        "primary_h5ad": str(primary),
        "global_effect_reference_lock": str(unrelated),
    }
    registered = {
        role: {
            "asset_id": f"asset_{role}",
            "path": path,
            "content_sha256": file_sha256(Path(path)),
        }
        for role, path in paths.items()
    }

    full = execution._task_asset_manifest(
        workflow_id="WF-PAPA",
        dataset_id="papalexi_thp1_eccite",
        condition="pertura_full",
        roles=("primary_h5ad",),
        asset_paths=paths,
        registered_assets=registered,
    )
    baseline = execution._task_asset_manifest(
        workflow_id="WF-PAPA",
        dataset_id="papalexi_thp1_eccite",
        condition="prompt_only",
        roles=("primary_h5ad",),
        asset_paths=paths,
        registered_assets=registered,
    )

    assert [item["role"] for item in full["assets"]] == ["primary_h5ad"]
    assert full["assets"][0]["asset_id"] == "asset_primary_h5ad"
    assert [item["role"] for item in baseline["assets"]] == ["primary_h5ad"]
    assert "asset_id" not in baseline["assets"][0]
    assert "global_effect_reference_lock" not in json.dumps(full)
    assert "global_effect_reference_lock" not in json.dumps(baseline)


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
        self.invocation_bindings = {}

    def inspect_dataset(self, *args, **kwargs):
        raise AssertionError(
            "paper workflow must register the frozen partial contract instead of "
            "performing shallow dataset inspection"
        )

    def register_dataset_contract(self, contract):
        self.contract = DatasetContract.model_validate(contract)
        return {
            "contract_id": self.contract.contract_id,
            "contract_hash": self.contract.canonical_hash,
        }

    def planning_material(self, contract_id=None):
        assert contract_id == self.contract.contract_id
        return self.contract, ()

    def planning_commit_records(self):
        return ()

    def replace_invocation_bindings(self, *, task_id, bindings):
        self.invocation_bindings = {item.binding_id: item for item in bindings}

    def close(self, graceful=True):
        return None


class _Agent:
    def __init__(self, *, workspace, **kwargs):
        self.workspace = workspace
        self.product_runtime = _Runtime()
        self.turn_manager = None


def _asset_catalog(tmp_path: Path) -> Path:
    catalog = json.loads((ROOT / "benchmarks/paper_v1/agent_tasks.v2.json").read_text())
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
    catalog = json.loads((ROOT / "benchmarks/paper_v1/agent_tasks.v2.json").read_text())
    for candidate_workflow in catalog["workflows"]:
        for candidate_task in candidate_workflow["turns"]:
            candidate = execution._neutral_benchmark_result(
                workflow=candidate_workflow,
                task=candidate_task,
            )
            AgentBenchmarkResult.model_validate(candidate)
            assert candidate["artifact_roles"] == []
            assert candidate["status"] == "blocked"
            assert candidate["analysis_unit"] == "unresolved"

    workflow = next(
        item for item in catalog["workflows"] if item["workflow_id"] == "WF-KANG"
    )
    task = next(item for item in workflow["turns"] if item["task_id"] == "KANG-01")

    template = execution._neutral_benchmark_result(
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
    assert list(result.artifact_roles) == []
    assert isinstance(template["artifact_roles"], list)
    assert template["findings"] == []

    for condition in ("pertura_full", "prompt_only", "free_codeact"):
        prompt = execution._task_prompt(
            workflow=workflow,
            task=task,
            condition=condition,
            workspace_root=PROMPT_WORKSPACE,
            asset_paths={},
            anchors_by_id={
                anchor_id: {"anchor_id": anchor_id}
                for anchor_id in task["paper_anchor_ids"]
            },
            dependency_contracts={},
        )
        assert "standalone pertura-agent-benchmark-result-v1 JSON file" in prompt
        assert "separate pertura-turn-draft-v1 object" in prompt
        assert "mcp__benchmark_io__submit_task_bundle" in prompt
        assert "pertura-turn-draft-v1" in prompt
        assert "headline is required" in prompt
        assert "Do not put turn_index, case_id, dataset_id" in prompt
        assert "accepted=true with a non-null submission_id" in prompt
        assert "Never copy the TurnDraft object" in prompt
        assert "artifact_roles must be a JSON array" in prompt
        assert "hypotheses, questions_for_user, next_steps" in prompt
        assert "runner initialized" in prompt
        assert "Leaving it unchanged is a scored task failure" in prompt
        assert '"analysis_unit": "donor"' in prompt
        assert (
            "For benchmark_result.analysis_unit, use exactly one value from the "
            'task-scoped controlled vocabulary ["donor", "donor_pseudobulk"]'
        ) in prompt
        assert "required_text_patterns" not in prompt


def test_formal_runner_excludes_experimental_control_layers() -> None:
    source = inspect.getsource(execution.run_paper_agent_workflow)
    for forbidden in (
        "compile_capability_execution_brief",
        "build_codeact_handoff",
        "configure_completion_guard",
        "completion_guard_snapshot",
        "PERTURA_CAPABILITY_PLAN",
        "capability_plans",
    ):
        assert forbidden not in source


def test_neutral_checkpoint_update_gate_is_fail_closed(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(
        execution,
        "evaluate_paper_task",
        lambda *args, **kwargs: {"status": "passed", "problems": []},
    )
    task = {
        "task_id": "T-01",
        "required_artifact_roles": [],
        "required_input_roles": [],
        "task_reference_ids": [],
        "depends_on_tasks": [],
        "output_contract": {"artifact_roles": [], "artifact_paths": {}},
    }
    root = tmp_path / "workspace"
    result_path = root / "outputs/tasks/T-01/benchmark_result.json"
    neutral = {
        "schema_version": "pertura-agent-benchmark-result-v1",
        "case_id": "T-01",
        "dataset_id": "D-01",
        "result_type": "fixture",
        "analysis_unit": "unresolved",
        "status": "blocked",
        "findings": [],
        "metrics": {},
        "limitations": [
            "runner-initialized neutral checkpoint; provider has not submitted "
            "a scientific result"
        ],
        "artifact_roles": [],
    }
    execution._write(result_path, neutral)
    initial_hash = file_sha256(result_path)

    def evaluate():
        return execution._evaluate_task_outputs(
            task,
            workspace_root=root,
            dataset_id="D-01",
            paper_root=tmp_path,
            asset_paths={},
            references_by_id={},
            tasks_by_id={"T-01": task},
            initial_result_sha256=initial_hash,
        )

    _, problem, _, gates = evaluate()
    assert gates["provider_result_updated"] is False
    assert gates["benchmark_result_schema_valid"] is True
    assert gates["turn_output_schema_valid"] is False
    assert gates["typed_submission"] is False
    assert problem == "typed submission receipt is missing"

    execution._write(
        result_path,
        neutral
        | {
            "analysis_unit": "donor",
            "status": "completed",
            "limitations": ["provider-updated scientific result"],
        },
    )
    _, problem, _, gates = evaluate()
    assert gates["provider_result_updated"] is False
    assert gates["benchmark_result_schema_valid"] is True
    assert problem == "typed submission receipt is missing"

    service = TaskSubmissionService(root)
    service.bind_task(task_id="T-01", dataset_id="D-01")
    accepted = service.submit_task_bundle(
        {
            "benchmark_result": neutral
            | {
                "analysis_unit": "donor",
                "status": "completed",
                "limitations": ["provider-updated scientific result"],
            },
            "turn_draft": {
                "schema_version": "pertura-turn-draft-v1",
                "headline": "Fixture completed",
                "limitations": ["fixture"],
            },
        }
    )
    assert accepted["accepted"] is True
    _, problem, _, gates = evaluate()
    assert gates["typed_submission"] is True
    assert gates["provider_result_updated"] is True
    assert gates["benchmark_result_schema_valid"] is True
    assert gates["turn_output_schema_valid"] is True
    assert execution._provider_scientific_completion(gates) is True
    assert problem is None

    result_path.write_text("{invalid", encoding="utf-8")
    _, problem, _, gates = evaluate()
    assert gates["provider_result_updated"] is False
    assert gates["benchmark_result_schema_valid"] is False
    assert problem

    result_path.unlink()
    _, problem, _, gates = evaluate()
    assert gates["provider_result_updated"] is False
    assert gates["output_contract_present"] is False
    assert problem == "benchmark_result.json is missing"


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


def test_failed_benchmark_result_is_schema_valid_but_not_pass_eligible(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(
        execution,
        "evaluate_paper_task",
        lambda *args, **kwargs: {"status": "passed", "problems": []},
    )
    root = tmp_path / "workspace"
    service = TaskSubmissionService(root)
    service.bind_task(task_id="T-01", dataset_id="D-01")
    accepted = service.submit_task_bundle(
        {
            "benchmark_result": {
                "schema_version": "pertura-agent-benchmark-result-v1",
                "case_id": "T-01",
                "dataset_id": "D-01",
                "result_type": "fixture",
                "analysis_unit": "unresolved",
                "status": "failed",
                "findings": [],
                "metrics": {},
                "limitations": ["provider reported failure"],
                "artifact_roles": [],
            },
            "turn_draft": {
                "schema_version": "pertura-turn-draft-v1",
                "headline": "Task failed",
                "limitations": ["provider reported failure"],
            },
        }
    )
    assert accepted["accepted"] is True
    path = root / "outputs/tasks/T-01/benchmark_result.json"
    _, problem, _, gates = execution._evaluate_task_outputs(
        {
            "task_id": "T-01",
            "required_artifact_roles": [],
            "required_input_roles": [],
            "task_reference_ids": [],
            "depends_on_tasks": [],
            "output_contract": {"artifact_roles": [], "artifact_paths": {}},
        },
        workspace_root=root,
        dataset_id="D-01",
        paper_root=tmp_path,
        asset_paths={},
        references_by_id={},
        tasks_by_id={},
        initial_result_sha256="sha256:" + "0" * 64,
    )

    assert path.is_file()
    assert problem is None
    assert gates["benchmark_result_schema_valid"] is True
    assert gates["turn_output_schema_valid"] is True
    assert gates["benchmark_result_status_eligible"] is False
    assert execution._provider_scientific_completion(gates) is True


def test_paper_asset_kinds_adapt_to_product_registry() -> None:
    expected = {
        "observed": ("observed", "observed_metadata"),
        "derived": ("derived", "derived_artifact"),
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


def test_primary_h5ad_registers_a_strict_capability_alias(tmp_path: Path) -> None:
    project = execution.ProjectWorkspace.initialize(
        tmp_path / "project",
        logical_name="primary-alias",
    )
    run = project.create_run(logical_name="primary alias")
    registry = execution.DataAssetRegistry(
        project_id=project.project.project_id,
        store=project.store,
        object_root=project.objects_dir,
    )
    h5ad = tmp_path / "cache" / "screen.h5ad"
    h5ad.parent.mkdir(parents=True)
    h5ad.write_bytes(b"frozen-h5ad-fixture")

    registered, paths = execution._register_workflow_assets(
        registry,
        project=project,
        run_id=run.run_id,
        raw_assets=(
            {
                "role": "primary_h5ad",
                "root": "cache",
                "relative_path": h5ad.name,
                "content_sha256": file_sha256(h5ad),
                "kind": "observed",
            },
        ),
        cache=h5ad.parent,
        paper_root=tmp_path / "paper",
    )

    by_role = {asset.role: asset for asset in registered}
    assert set(by_role) == {"primary_h5ad", "primary_dataset"}
    assert by_role["primary_h5ad"].asset_id != by_role["primary_dataset"].asset_id
    assert (
        by_role["primary_h5ad"].content_sha256
        == by_role["primary_dataset"].content_sha256
    )
    assert paths == {
        "primary_h5ad": str(h5ad.resolve()),
        "primary_dataset": str(h5ad.resolve()),
    }
    assert (
        registry.resolve(
            by_role["primary_dataset"].asset_id,
            expected_role="primary_dataset",
        )
        == h5ad.resolve()
    )

    shared_manifest = execution._task_asset_manifest(
        workflow_id="WF-PAPA",
        dataset_id="papalexi_thp1_eccite",
        condition="prompt_only",
        roles=("primary_h5ad",),
        asset_paths=paths,
        registered_assets={
            role: {
                "asset_id": asset.asset_id,
                "content_sha256": asset.content_sha256,
            }
            for role, asset in by_role.items()
        },
    )
    assert [item["role"] for item in shared_manifest["assets"]] == ["primary_h5ad"]
    assert "asset_id" not in shared_manifest["assets"][0]


def test_donor_metadata_registers_a_hidden_strict_cell_metadata_alias(
    tmp_path: Path,
) -> None:
    project = execution.ProjectWorkspace.initialize(
        tmp_path / "project",
        logical_name="cell-metadata-alias",
    )
    run = project.create_run(logical_name="cell metadata alias")
    registry = execution.DataAssetRegistry(
        project_id=project.project.project_id,
        store=project.store,
        object_root=project.objects_dir,
    )
    metadata = tmp_path / "cache" / "cell_metadata.tsv"
    metadata.parent.mkdir(parents=True)
    metadata.write_text(
        "cell_id\tind\tstim\tstate\ncell-1\td1\tctrl\tA\n",
        encoding="utf-8",
    )

    registered, paths = execution._register_workflow_assets(
        registry,
        project=project,
        run_id=run.run_id,
        raw_assets=(
            {
                "role": "donor_metadata",
                "root": "cache",
                "relative_path": metadata.name,
                "content_sha256": file_sha256(metadata),
                "kind": "observed",
            },
        ),
        cache=metadata.parent,
        paper_root=tmp_path / "paper",
    )

    by_role = {asset.role: asset for asset in registered}
    assert set(by_role) == {"donor_metadata", "cell_metadata"}
    assert (
        by_role["donor_metadata"].content_sha256
        == by_role["cell_metadata"].content_sha256
    )
    assert paths["donor_metadata"] == paths["cell_metadata"]
    assert (
        registry.resolve(
            by_role["cell_metadata"].asset_id,
            expected_role="cell_metadata",
        )
        == metadata.resolve()
    )

    baseline_manifest = execution._task_asset_manifest(
        workflow_id="WF-KANG",
        dataset_id="kang18_8vs8_pbmc",
        condition="free_codeact",
        roles=("donor_metadata",),
        asset_paths=paths,
        registered_assets={
            role: {
                "asset_id": asset.asset_id,
                "content_sha256": asset.content_sha256,
            }
            for role, asset in by_role.items()
        },
    )
    assert [item["role"] for item in baseline_manifest["assets"]] == ["donor_metadata"]
    assert "asset_id" not in baseline_manifest["assets"][0]


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
            "requested_memory_gb": 48,
            "actual_memory_gb": 48,
            "cpu_count": 1,
            "n_jobs": 1,
            "timeout_seconds": 7200,
            "peak_rss_mb": 100,
            "thread_environment": {"OMP_NUM_THREADS": "1"},
        },
    )
    invoked = []
    prompts = []

    def execute(agent, prompt, timeout):
        invoked.append(re.search(r"task (REPL-\d+)", prompt).group(1))
        prompts.append(prompt)
        return SimpleNamespace(status="failed", error="fixture provider failure")

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
        paper_anchor_catalog_path=ROOT / "benchmarks/paper_v1/paper_anchors.v1.json",
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
    verdict = json.loads(
        (Path(result["execution_root"]) / "tasks/REPL-03/verdict.json").read_text()
    )
    assert verdict["provider_status"] == "failed"
    assert verdict["provider_error"] == "fixture provider failure"
    assert verdict["provider_clean_termination"] is False
    assert verdict["termination_reason"] == "provider_error"
    assert "provider_execution_completed" not in verdict["hard_gates"]
    manifest = json.loads(
        (Path(result["execution_root"]) / "input_manifest.json").read_text()
    )
    assert manifest["smoke_task_ids"] == ["REPL-03"]
    assert manifest["skill_bundle_hash"] is None
    assert manifest["task_skills"] == {}
    assert manifest["capability_contract_subsets"] == []
    serialized_manifest = json.dumps(manifest)
    assert "pertura_skills" not in serialized_manifest
    assert "execute-task-scoped-plan" not in serialized_manifest


def test_accepted_submission_survives_later_max_turn_termination(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(execution, "ClaudePerturaAgent", _Agent)
    monkeypatch.setattr(
        execution,
        "_resource_evidence",
        lambda path: {
            "mode": "scheduler",
            "scheduler_job_id": "fixture-job",
            "requested_memory_gb": 32,
            "actual_memory_gb": 32,
            "cpu_count": 1,
            "n_jobs": 1,
            "timeout_seconds": 9000,
            "peak_rss_mb": 100,
            "thread_environment": {
                "OMP_NUM_THREADS": "1",
                "OPENBLAS_NUM_THREADS": "1",
                "MKL_NUM_THREADS": "1",
                "NUMEXPR_NUM_THREADS": "1",
            },
        },
    )
    monkeypatch.setattr(
        execution,
        "evaluate_paper_task",
        lambda *args, **kwargs: {"status": "passed", "problems": []},
    )
    monkeypatch.setattr(
        execution,
        "_audit_capability_treatment_uptake",
        lambda *args, **kwargs: {
            "schema_version": "pertura-paper-capability-treatment-uptake-v2",
            "capability_first_status": "failed",
            "required_binding_coverage_status": "failed",
            "model_binding_retry_status": "failed",
            "runner_binding_integration_errors": [],
            "calls": [],
        },
    )

    def execute(agent, prompt, timeout):
        del prompt, timeout
        output = agent.workspace.root / "outputs/tasks/KANG-01"
        output.mkdir(parents=True, exist_ok=True)
        (output / "pseudobulk_counts.tsv").write_text(
            "gene\td1_ctrl\td1_stim\nG1\t10\t20\n", encoding="utf-8"
        )
        (output / "design_matrix.tsv").write_text(
            "sample\tind\tstim\nd1_ctrl\td1\tctrl\nd1_stim\td1\tstim\n",
            encoding="utf-8",
        )
        (output / "de_results.tsv").write_text(
            "gene\tlogFC\tF\tPValue\tFDR\nG1\t1\t2\t0.01\t0.02\n",
            encoding="utf-8",
        )
        (output / "null_calibration.tsv").write_text(
            "permutation_id\ttype1_rate\tnull_effect_bias\t"
            "exchangeability_violation_count\nswap_0001\t0.05\t0\t0\n",
            encoding="utf-8",
        )
        service = TaskSubmissionService(agent.workspace.root)
        service.bind_task(
            task_id="KANG-01",
            dataset_id="kang18_8vs8_pbmc",
            allowed_analysis_units=("donor", "donor_pseudobulk"),
        )
        accepted = service.submit_task_bundle(
            {
                "benchmark_result": {
                    "schema_version": "pertura-agent-benchmark-result-v1",
                    "case_id": "KANG-01",
                    "dataset_id": "kang18_8vs8_pbmc",
                    "result_type": "fixture",
                    "analysis_unit": "donor",
                    "status": "completed",
                    "findings": [],
                    "metrics": {},
                    "limitations": ["fixture"],
                    "artifact_roles": [
                        "pseudobulk_counts",
                        "design_matrix",
                        "de_results",
                        "null_calibration",
                    ],
                },
                "turn_draft": {
                    "schema_version": "pertura-turn-draft-v1",
                    "headline": "Scientific submission accepted",
                    "limitations": ["fixture"],
                },
            }
        )
        assert accepted["accepted"] is True
        agent.manifest = SimpleNamespace(
            result_subtype="error_max_turns",
            num_turns=64,
            message_count=120,
            total_cost_usd=1.0,
        )
        return SimpleNamespace(
            status="failed",
            error="Claude SDK result error: error_max_turns",
            result_subtype="error_max_turns",
        )

    result = execution.run_paper_agent_workflow(
        "WF-KANG",
        repo_root=ROOT,
        cache=tmp_path / "cache",
        paper_root=tmp_path / "paper",
        output=tmp_path / "runs",
        condition="pertura_full",
        repeat_index=1,
        task_catalog_path=ROOT / "benchmarks/paper_v1/agent_tasks.v2.json",
        task_reference_catalog_path=_bound_task_references(tmp_path),
        paper_anchor_catalog_path=ROOT / "benchmarks/paper_v1/paper_anchors.v1.json",
        asset_catalog_path=_asset_catalog(tmp_path),
        resource_evidence_path=tmp_path / "resource.json",
        smoke_task_ids=("KANG-01",),
        turn_executor=execute,
        verify_checkpoint=False,
    )

    verdict = json.loads(Path(result["task_records"][0]["verdict"]).read_text())
    assert result["status"] == "passed"
    assert verdict["status"] == "passed"
    assert verdict["provider_scientific_completion"] is True
    assert verdict["provider_clean_termination"] is False
    assert verdict["termination_reason"] == "max_turns"
    assert verdict["hard_gates"]["provider_scientific_completion"] is True
    assert verdict["hard_gates"]["independent_evaluation"] is True
    assert "capability_first_bound_invocation" not in verdict["hard_gates"]
    assert "required_binding_coverage" not in verdict["hard_gates"]
    assert "capability_binding_call_validity" not in verdict["hard_gates"]
    assert verdict["capability_process_checks"] == {
        "schema_version": "pertura-capability-process-checks-v1",
        "affects_task_status": False,
        "capability_first_bound_invocation": False,
        "advertised_binding_coverage": False,
        "capability_binding_call_validity": False,
    }


def test_provider_termination_reason_is_separate_from_submission_state() -> None:
    assert (
        execution._provider_termination_reason(
            provider_status="timeout",
            provider_error=None,
            provider_result_subtype=None,
            timed_out=True,
        )
        == "task_timeout"
    )
    assert (
        execution._provider_termination_reason(
            provider_status="failed",
            provider_error="Claude SDK result error: error_max_turns",
            provider_result_subtype="error_max_turns",
            timed_out=False,
        )
        == "max_turns"
    )
    assert (
        execution._provider_termination_reason(
            provider_status="completed",
            provider_error=None,
            provider_result_subtype="success",
            timed_out=False,
        )
        == "completed"
    )


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
    catalog = json.loads((ROOT / "benchmarks/paper_v1/agent_tasks.v2.json").read_text())
    workflow = next(
        item for item in catalog["workflows"] if item["workflow_id"] == "WF-PAPA"
    )
    task = next(item for item in workflow["turns"] if item["task_id"] == "PAPA-07")
    prompt = execution._task_prompt(
        workflow=workflow,
        task=task,
        condition="pertura_full",
        workspace_root=PROMPT_WORKSPACE,
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
    assert "Upstream repair contracts: {}" in prompt
    task_root = (PROMPT_WORKSPACE / "outputs/tasks/PAPA-07").as_posix()
    assert (
        '"global_effect_claims": ' f'"{task_root}/global_effect_claims.tsv"'
    ) in prompt
    assert (
        '"global_effect_limitations": ' f'"{task_root}/global_effect_limitations.json"'
    ) in prompt
    assert "exact absolute destinations" in prompt


def test_scientific_facts_are_shared_but_authority_context_is_pertura_only() -> None:
    catalog = json.loads((ROOT / "benchmarks/paper_v1/agent_tasks.v2.json").read_text())
    workflow = next(
        item for item in catalog["workflows"] if item["workflow_id"] == "WF-PAPA"
    )
    task = next(item for item in workflow["turns"] if item["task_id"] == "PAPA-01")
    common = {
        "workflow": workflow,
        "task": task,
        "workspace_root": PROMPT_WORKSPACE,
        "asset_paths": {},
        "anchors_by_id": {
            anchor_id: {"anchor_id": anchor_id}
            for anchor_id in task["paper_anchor_ids"]
        },
        "dependency_contracts": {},
        "scientific_contract_context": {
            "dataset_id": "papalexi_thp1_eccite",
            "design_facts": {
                "replicate": {
                    "status": "confirmed",
                    "value": {"column": "replicate"},
                }
            },
            "unresolved_facts": ["ambient_empty_droplet_evidence"],
            "asset_availability": {"empty_droplet_counts": "unavailable"},
            "task_design_protocol": {},
        },
        "contract_context": {
            "contract_id": "contract_fixture",
            "contract_hash": "sha256:" + "2" * 64,
            "confirmed_design_facts": {"control": {"value": ["NTC"]}},
            "conflicting_design_facts": {},
            "unresolved_design_facts": [],
            "registered_assets": {},
            "committed_results": [],
        },
        "contract_subset_record": {
            "path": "task/capability_contracts/PAPA-01.json",
            "advertised_capability_ids": [],
            "audited_codeact_fallback": True,
        },
    }

    full = execution._task_prompt(condition="pertura_full", **common)
    prompt = execution._task_prompt(condition="prompt_only", **common)
    free = execution._task_prompt(condition="free_codeact", **common)

    assert "contract_fixture" in full
    assert "task/capability_contracts/PAPA-01.json" in full
    assert "all_guide_matrix_cells" in full
    assert "raw empty-droplet evidence is unavailable" in full
    assert "registered_calibration_and_evaluation_selections" in full
    assert "do not call inspect_dataset again" in full
    assert (
        "exact SDK Skill tool names frozen for this task are "
        '["pertura:operate-pertura-workflow", '
        '"pertura:diagnose-perturb-seq-screen"]'
    ) in full
    assert "invoke each of those exact names once with the Skill tool" in full
    assert "in the listed order" in full
    assert "Do not repeat a successful Skill invocation" in full
    assert "do not probe rpy2" in full.lower()
    assert "explicit nonexecutions for this endpoint" in full
    assert "guide.assignment.nb_mixture.v1" in full
    assert "advertised no executable capability" in full
    assert "audited CodeAct fallback" in full
    assert "does not produce a capability receipt or measured authority" in full
    assert "contract_fixture" not in prompt
    assert "contract_fixture" not in free
    assert "guide.assignment.nb_mixture.v1" not in prompt
    assert "guide.assignment.nb_mixture.v1" not in free
    assert "pertura:diagnose-perturb-seq-screen" not in prompt
    assert "pertura:diagnose-perturb-seq-screen" not in free
    assert "exact SDK Skill tool names" not in prompt
    assert "exact SDK Skill tool names" not in free
    for text in (full, prompt, free):
        assert '"column": "replicate"' in text
        assert '"ambient_empty_droplet_evidence"' in text
        assert '"empty_droplet_counts": "unavailable"' in text
        assert "execution brief" not in text
        assert "CodeAct handoff" not in text
        assert "completion guard" not in text


def test_capability_or_codeact_prompt_preserves_scientific_blocks_but_exits_integration_dead_ends() -> (
    None
):
    catalog = json.loads((ROOT / "benchmarks/paper_v1/agent_tasks.v2.json").read_text())
    workflow = next(
        item for item in catalog["workflows"] if item["workflow_id"] == "WF-REPL"
    )
    task = next(item for item in workflow["turns"] if item["task_id"] == "REPL-01")
    prompt = execution._task_prompt(
        workflow=workflow,
        task=task,
        condition="pertura_full",
        workspace_root=PROMPT_WORKSPACE,
        asset_paths={},
        anchors_by_id={
            anchor_id: {"anchor_id": anchor_id}
            for anchor_id in task["paper_anchor_ids"]
        },
        dependency_contracts={},
        scientific_contract_context={},
        contract_context={},
        contract_subset_record={
            "path": "task/capability_contracts/REPL-01.json",
            "advertised_capability_ids": ["diagnostic.dataset_integrity.v1"],
            "audited_codeact_fallback": True,
        },
    )

    assert "optional treatment routes" in prompt
    assert "uptake without changing task pass/fail" in prompt
    assert "not required to invoke the first or every listed binding" in prompt
    assert "When you choose to invoke a bound capability" in prompt
    assert "genuine scientific applicability or evidence blocker" in prompt
    assert "preserve that block and do not bypass it" in prompt
    assert "integration or access boundary" in prompt
    assert "unavailable verified ancestor capability result" in prompt
    assert "does not produce a capability receipt or measured authority" in prompt
    assert "advertised no executable capability" not in prompt


def test_kang_full_prompt_requires_exact_frozen_skill_order() -> None:
    catalog = json.loads((ROOT / "benchmarks/paper_v1/agent_tasks.v2.json").read_text())
    workflow = next(
        item for item in catalog["workflows"] if item["workflow_id"] == "WF-KANG"
    )
    task = next(item for item in workflow["turns"] if item["task_id"] == "KANG-01")
    common = {
        "workflow": workflow,
        "task": task,
        "workspace_root": PROMPT_WORKSPACE,
        "asset_paths": {},
        "anchors_by_id": {
            anchor_id: {"anchor_id": anchor_id}
            for anchor_id in task["paper_anchor_ids"]
        },
        "dependency_contracts": {},
    }

    full = execution._task_prompt(condition="pertura_full", **common)
    prompt = execution._task_prompt(condition="prompt_only", **common)
    free = execution._task_prompt(condition="free_codeact", **common)

    expected = [f"pertura:{skill}" for skill in task["pertura_skills"]]
    assert f"frozen for this task are {json.dumps(expected)}" in full
    assert full.index(expected[0]) < full.index(expected[1]) < full.index(expected[2])
    assert "Before any task-scientific Read" in full
    assert "invoke each of those exact names once with the Skill tool" in full
    assert '"robust": false' in full
    for skill in expected:
        assert full.count(skill) == 1
        assert skill not in prompt
        assert skill not in free


def test_pertura_full_runner_writes_answer_free_static_contract_subset(
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
        paper_anchor_catalog_path=ROOT / "benchmarks/paper_v1/paper_anchors.v1.json",
        asset_catalog_path=_asset_catalog(tmp_path),
        resource_evidence_path=tmp_path / "resource.json",
        smoke_task_ids=("PAPA-01",),
        turn_executor=execute,
        verify_checkpoint=False,
    )

    root = Path(result["execution_root"])
    manifest = json.loads((root / "input_manifest.json").read_text())
    subset_record = manifest["capability_contract_subsets"][0]
    run_root = root / "project/.pertura/runs"
    generated = list(run_root.rglob("task/capability_contracts/PAPA-01.json"))
    assert len(generated) == 1
    subset = json.loads(generated[0].read_text())

    assert subset_record["task_id"] == "PAPA-01"
    assert subset_record["subset_hash"] == subset["subset_hash"]
    expected_task = next(
        task
        for workflow in json.loads(
            (ROOT / "benchmarks/paper_v1/agent_tasks.v2.json").read_text()
        )["workflows"]
        for task in workflow["turns"]
        if task["task_id"] == "PAPA-01"
    )
    assert subset["schema_version"] == ("pertura-paper-capability-contract-subset-v2")
    assert subset["candidate_capability_ids"] == []
    assert subset["advertised_capability_ids"] == []
    assert subset["conditional_capability_ids"] == []
    assert subset["structurally_excluded_capabilities"] == []
    assert subset["capabilities"] == []
    assert subset["audited_codeact_fallback"] is True
    assert subset_record["candidate_capability_ids"] == (
        expected_task["expected_capability_dag"]
    )
    assert subset_record["structurally_excluded_capabilities"] == []
    assert manifest["task_skills"]["PAPA-01"] == expected_task["pertura_skills"]
    assert manifest["skill_bundle_hash"].startswith("sha256:")
    serialized = json.dumps(subset).lower()
    for forbidden in (
        "grader",
        "task_reference",
        "reference_sources",
        "expected answer",
        "evaluation truth",
    ):
        assert forbidden not in serialized
    assert "asset_id" in prompts[0]
    assert "task/capability_contracts/PAPA-01.json" in prompts[0]
    assert "pertura:operate-pertura-workflow" in prompts[0]
    assert "pertura:diagnose-perturb-seq-screen" in prompts[0]
    assert "invoke each of those exact names once with the Skill tool" in prompts[0]
    assert "execution brief" not in prompts[0]
    assert "CodeAct handoff" not in prompts[0]
    verdict = json.loads((root / "tasks/PAPA-01/verdict.json").read_text())
    assert verdict["evaluation_domain"] == "scientific_fidelity"
    assert verdict["task_evaluation"] == verdict["scientific_evaluation"]


def test_accepted_papa01_artifact_is_available_to_papa02_binding(
    tmp_path: Path, monkeypatch
) -> None:
    """Exercise the real cross-turn submission-to-binding provenance bridge."""

    monkeypatch.setattr(execution, "ClaudePerturaAgent", _Agent)
    monkeypatch.setattr(
        execution,
        "_resource_evidence",
        lambda path: {
            "mode": "scheduler",
            "scheduler_job_id": "fixture-job",
            "requested_memory_gb": 32,
            "actual_memory_gb": 32,
            "cpu_count": 1,
            "n_jobs": 1,
            "timeout_seconds": 9000,
            "peak_rss_mb": 100,
            "thread_environment": {
                "OMP_NUM_THREADS": "1",
                "OPENBLAS_NUM_THREADS": "1",
                "MKL_NUM_THREADS": "1",
                "NUMEXPR_NUM_THREADS": "1",
            },
        },
    )
    monkeypatch.setattr(
        execution,
        "evaluate_paper_task",
        lambda *args, **kwargs: {"status": "passed", "problems": []},
    )

    observed_papa02_bindings = []

    def execute(agent, prompt, timeout):
        del timeout
        task_id = re.search(r"task (PAPA-\d+)", prompt).group(1)
        output = agent.workspace.root / "outputs" / "tasks" / task_id
        output.mkdir(parents=True, exist_ok=True)
        if task_id == "PAPA-01":
            (output / "alignment_audit.json").write_text("{}\n", encoding="utf-8")
            (output / "guide_assignment.tsv").write_text(
                "cell_id\tproxy_class\nc1\texternal_label_top_count_match\n",
                encoding="utf-8",
            )
            (output / "ambient_qc.json").write_text(
                '{"status":"unresolved","evidence_class":"external_label_proxy_only",'
                '"limitations":["fixture"]}\n',
                encoding="utf-8",
            )
            (output / "retained_cell_manifest.tsv").write_text(
                "dataset_id\tsplit\tcell_id\texpected_state\treason\n"
                "papalexi_thp1_eccite\tcalibration\tc1\t"
                "retain_for_external_label_proxy\tfixture\n",
                encoding="utf-8",
            )
            artifact_roles = [
                "alignment_audit",
                "guide_assignment",
                "ambient_qc",
                "retained_cell_manifest",
            ]
        else:
            observed_papa02_bindings.extend(
                agent.product_runtime.invocation_bindings.values()
            )
            (output / "state_reference_model").mkdir()
            (output / "state_reference_model" / "model.json").write_text(
                "{}\n", encoding="utf-8"
            )
            (output / "reference_cell_manifest.tsv").write_text(
                "cell_id\ttechnical_state_id\nc1\t0\n", encoding="utf-8"
            )
            (output / "reference_provenance.json").write_text("{}\n", encoding="utf-8")
            artifact_roles = [
                "state_reference_model",
                "reference_cell_manifest",
                "reference_provenance",
            ]

        service = TaskSubmissionService(agent.workspace.root)
        service.bind_task(
            task_id=task_id,
            dataset_id="papalexi_thp1_eccite",
            allowed_analysis_units=("cell",) if task_id == "PAPA-01" else (),
        )
        accepted = service.submit_task_bundle(
            {
                "benchmark_result": {
                    "schema_version": "pertura-agent-benchmark-result-v1",
                    "case_id": task_id,
                    "dataset_id": "papalexi_thp1_eccite",
                    "result_type": "provenance_bridge_fixture",
                    "analysis_unit": "cell",
                    "status": "completed",
                    "findings": [],
                    "metrics": {},
                    "limitations": ["fixture"],
                    "artifact_roles": artifact_roles,
                },
                "turn_draft": {
                    "schema_version": "pertura-turn-draft-v1",
                    "headline": f"Completed {task_id}",
                    "limitations": ["fixture"],
                },
            }
        )
        assert accepted["accepted"] is True
        agent.manifest = SimpleNamespace(
            result_subtype="success",
            num_turns=1,
            message_count=1,
            total_cost_usd=0.0,
        )
        return SimpleNamespace(status="completed", error=None, result_subtype="success")

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
        paper_anchor_catalog_path=ROOT / "benchmarks/paper_v1/paper_anchors.v1.json",
        asset_catalog_path=_asset_catalog(tmp_path),
        resource_evidence_path=tmp_path / "resource.json",
        smoke_task_ids=("PAPA-01", "PAPA-02"),
        turn_executor=execute,
        verify_checkpoint=False,
    )

    assert result["required_task_count"] == 2
    assert len(observed_papa02_bindings) == 1
    binding = observed_papa02_bindings[0]
    assert binding.capability_id == "state.reference.fit.v1"
    assert binding.readiness == "ready", binding.blockers
    retained = [
        item for item in binding.input_assets if item.role == "retained_cell_manifest"
    ]
    assert len(retained) == 1
    assert retained[0].content_sha256
    papa01_verdict = json.loads(
        Path(result["task_records"][0]["verdict"]).read_text(encoding="utf-8")
    )
    retained_records = [
        item
        for item in papa01_verdict["submitted_artifact_assets"]
        if item["role"] == "retained_cell_manifest"
    ]
    assert len(retained_records) == 1
    assert retained_records[0]["origin_task_id"] == "PAPA-01"
    assert retained_records[0]["submission_id"]
    assert retained_records[0]["schema_validation_status"] == "validated"
    assert retained_records[0]["content_sha256"] == retained[0].content_sha256


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
            "requested_memory_gb": 48,
            "actual_memory_gb": 48,
            "cpu_count": 1,
            "n_jobs": 1,
            "timeout_seconds": 7200,
            "peak_rss_mb": 100,
            "thread_environment": {"OMP_NUM_THREADS": "1"},
        },
    )
    catalog = json.loads((ROOT / "benchmarks/paper_v1/agent_tasks.v2.json").read_text())
    repl_tasks = {task["task_id"]: task for task in catalog["workflows"][0]["turns"]}
    canonical_result_paths: dict[str, Path] = {}
    agent_workspace_roots: set[Path] = set()

    def execute(agent, prompt, timeout):
        task_id = re.search(r"task (REPL-\d+)", prompt).group(1)
        task = repl_tasks[task_id]
        workspace_root = agent.workspace.root.resolve()
        task_root = workspace_root / "outputs" / "tasks" / task_id
        result_path = task_root / "benchmark_result.json"
        assert result_path.is_file()
        assert json.loads(result_path.read_text())["status"] == "blocked"
        assert result_path.resolve().is_relative_to(workspace_root)
        canonical_result_paths[task_id] = result_path.resolve()
        agent_workspace_roots.add(workspace_root)
        task_root.mkdir(parents=True, exist_ok=True)
        result_path.write_text(
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
    assert manifest["max_turns_per_task"] == 64
    assert len(result["task_records"]) == 4
    assert len({manifest["project_id"]}) == 1
    assert len({manifest["analysis_run_id"]}) == 1
    assert len({manifest["conversation_id"]}) == 1
    assert len(agent_workspace_roots) == 1
    workspace_root = next(iter(agent_workspace_roots))
    assert canonical_result_paths == {
        task_id: workspace_root
        / "outputs"
        / "tasks"
        / task_id
        / "benchmark_result.json"
        for task_id in repl_tasks
    }
    assert {
        path.parent.name for path in (root / "tasks").glob("*/benchmark_result.json")
    } == set(repl_tasks)
    for task_id, canonical_path in canonical_result_paths.items():
        scorer_copy = root / "tasks" / task_id / "benchmark_result.json"
        assert scorer_copy.resolve() != canonical_path
        assert scorer_copy.read_bytes() == canonical_path.read_bytes()
    assert all(
        (root / "tasks" / task_id / "verdict.json").is_file() for task_id in repl_tasks
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


def test_frozen_workflow_allocations_and_failure_classification() -> None:
    evidence = {
        "mode": "scheduler",
        "scheduler_job_id": "job-1",
        "requested_memory_gb": 48,
        "actual_memory_gb": 48,
        "cpu_count": 1,
        "n_jobs": 1,
        "thread_environment": {
            "OMP_NUM_THREADS": "1",
            "OPENBLAS_NUM_THREADS": "1",
            "MKL_NUM_THREADS": "1",
            "NUMEXPR_NUM_THREADS": "1",
        },
    }
    assert execution._workflow_resource_gate("WF-REPL", evidence) is True
    assert (
        execution._workflow_resource_gate(
            "WF-REPL", evidence | {"allocated_cpus_on_node": 7}
        )
        is True
    )
    assert execution._workflow_resource_gate("WF-PAPA", evidence) is False
    assert (
        execution._workflow_resource_gate(
            "WF-PAPA",
            evidence | {"requested_memory_gb": 32, "actual_memory_gb": 32},
        )
        is True
    )

    valid_oom = execution._classify_task_validity(
        workflow_id="WF-REPL",
        task_status="failed",
        termination_reason="provider_error",
        provider_error="MemoryError: out of memory",
        skill_leakage_audit={"status": "passed"},
        resource_evidence=evidence | {"oom_kill_events": 1},
    )
    assert valid_oom == ("valid", "scored_resource_failure")

    timed_out_after_scheduler_memory_binding = execution._classify_task_validity(
        workflow_id="WF-REPL",
        task_status="failed",
        termination_reason="task_timeout",
        provider_error=None,
        skill_leakage_audit={"status": "passed"},
        resource_evidence=evidence | {"allocated_cpus_on_node": 7},
    )
    assert timed_out_after_scheduler_memory_binding == (
        "valid",
        "scored_timeout",
    )

    preempted = execution._classify_task_validity(
        workflow_id="WF-REPL",
        task_status="failed",
        termination_reason="provider_error",
        provider_error=None,
        skill_leakage_audit={"status": "passed"},
        resource_evidence=evidence | {"scheduler_state": "PREEMPTED"},
    )
    assert preempted == (
        "invalid_infrastructure",
        "invalid_infrastructure",
    )

    network = execution._classify_task_validity(
        workflow_id="WF-REPL",
        task_status="failed",
        termination_reason="provider_error",
        provider_error="Connection reset by provider API",
        skill_leakage_audit={"status": "passed"},
        resource_evidence=evidence,
    )
    assert network == ("invalid_infrastructure", "invalid_infrastructure")

    runner_binding_incident = execution._classify_task_validity(
        workflow_id="WF-REPL",
        task_status="passed",
        termination_reason="completed",
        provider_error=None,
        capability_binding_incident=True,
        skill_leakage_audit={"status": "passed"},
        resource_evidence=evidence,
    )
    assert runner_binding_incident == (
        "invalid_infrastructure",
        "invalid_infrastructure",
    )

    for error in (
        "Authentication failed: invalid API key",
        "RuntimeError: SDK did not report the initialized skill surface",
        "Python environment preflight failed: environment is corrupt",
    ):
        assert execution._classify_task_validity(
            workflow_id="WF-REPL",
            task_status="failed",
            termination_reason="provider_error",
            provider_error=error,
            skill_leakage_audit={"status": "passed"},
            resource_evidence=evidence,
        ) == ("invalid_infrastructure", "invalid_infrastructure")

    timeout = execution._classify_task_validity(
        workflow_id="WF-REPL",
        task_status="failed",
        termination_reason="task_timeout",
        provider_error=None,
        skill_leakage_audit={"status": "passed"},
        resource_evidence=evidence,
    )
    assert timeout == ("valid", "scored_timeout")


def test_workflow_rejects_unreceipted_later_upstream_repair(
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
        paper_anchor_catalog_path=ROOT / "benchmarks/paper_v1/paper_anchors.v1.json",
        asset_catalog_path=_asset_catalog(tmp_path),
        resource_evidence_path=tmp_path / "resource.json",
        turn_executor=execute,
        verify_checkpoint=False,
    )
    root = Path(result["execution_root"])
    first = json.loads((root / "tasks/KANG-01/verdict.json").read_text())
    second = json.loads((root / "tasks/KANG-02/verdict.json").read_text())

    assert first["post_workflow_regraded"] is True
    assert first["evaluation_domain"] == "supplemental_scientific_fidelity"
    assert first["task_evaluation"] == first["scientific_evaluation"]
    assert first["repaired_after_turn"] is False
    assert first["result_problem"] == "typed submission receipt is missing"
    assert first["hard_gates"]["typed_submission"] is False
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
        item for item in task_catalog["workflows"] if item["workflow_id"] == "WF-KANG"
    )
    task = next(item for item in workflow["turns"] if item["task_id"] == "KANG-01")
    anchors_by_id = {item["anchor_id"]: item for item in anchor_catalog["anchors"]}

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
    (tmp_path / "result.tsv").write_text("cell_id\tstate\nc1\tkept\n", encoding="utf-8")
    (tmp_path / "summary.json").write_text(json.dumps({"count": 1}), encoding="utf-8")
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


def test_project_level_output_cannot_satisfy_canonical_run_output(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    canonical_root = (
        project_root
        / ".pertura"
        / "runs"
        / "run-fixture"
        / "outputs"
        / "tasks"
        / "REPL-01"
    )
    shallow_root = project_root / "outputs" / "tasks" / "REPL-01"
    shallow_root.mkdir(parents=True)
    (shallow_root / "dataset_profile.json").write_text(
        json.dumps({"status": "completed"}), encoding="utf-8"
    )
    contract = {
        "artifact_roles": ["dataset_profile"],
        "artifact_paths": {"dataset_profile": "dataset_profile.json"},
        "artifact_schemas": {"dataset_profile.json": ["status"]},
    }

    assert execution._artifact_paths_present(canonical_root, contract) is False
    canonical_root.mkdir(parents=True)
    (canonical_root / "dataset_profile.json").write_text(
        json.dumps({"status": "completed"}), encoding="utf-8"
    )
    assert execution._artifact_paths_present(canonical_root, contract) is True


def test_baseline_skill_access_audit_detects_tool_input(tmp_path: Path) -> None:
    events = tmp_path / "events.jsonl"
    safe = {
        "message_type": "AssistantMessage",
        "payload": {
            "content": [
                "ToolUseBlock(name='Bash', input={'command': 'python analysis.py'})"
            ]
        },
    }
    leaked = {
        "message_type": "AssistantMessage",
        "payload": {
            "content": [
                "ToolUseBlock(name='Read', input={'file_path': "
                "'/env/site-packages/pertura_runtime/agent_bundle/skills/"
                "operate-pertura-workflow/SKILL.md'})"
            ]
        },
    }
    events.write_text(
        json.dumps(safe) + "\n" + json.dumps(leaked) + "\n",
        encoding="utf-8",
    )

    audit = execution._audit_baseline_skill_access(
        events,
        start_offset=0,
        condition="prompt_only",
    )
    assert audit["status"] == "failed"
    assert audit["scanned_tool_events"] == 2
    assert audit["hits"]
    assert (
        execution._audit_baseline_skill_access(
            events,
            start_offset=events.stat().st_size,
            condition="free_codeact",
        )["status"]
        == "passed"
    )
    assert (
        execution._audit_baseline_skill_access(
            events,
            start_offset=0,
            condition="pertura_full",
        )["status"]
        == "not_applicable"
    )


def test_reference_truth_access_audit_detects_scoring_inputs(tmp_path: Path) -> None:
    events = tmp_path / "events.jsonl"
    events.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "message_type": "AssistantMessage",
                        "payload": {
                            "content": [
                                "ToolUseBlock(name='Read', input={'file_path': "
                                "'/paper/task_references/PAPA-06/reference.tsv'})"
                            ]
                        },
                    }
                ),
                json.dumps(
                    {
                        "message_type": "AssistantMessage",
                        "payload": {
                            "content": [
                                "ToolUseBlock(name='Read', input={'file_path': "
                                "'/workspace/outputs/tasks/PAPA-06/result.tsv'})"
                            ]
                        },
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    audit = execution._audit_reference_truth_access(events, start_offset=0)

    assert audit["status"] == "failed"
    assert audit["scanned_tool_events"] == 2
    assert audit["hits"][0]["matched_tokens"] == ["task_references/"]
    assert (
        execution._audit_reference_truth_access(
            events, start_offset=events.stat().st_size
        )["status"]
        == "passed"
    )


def test_reference_audit_ignores_non_path_metric_reference_identifier(
    tmp_path: Path,
) -> None:
    events = tmp_path / "events.jsonl"
    events.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "message_type": "AssistantMessage",
                        "payload": {
                            "content": [
                                "ToolUseBlock(name='Write', input={'file_path': "
                                "'/workspace/analysis.py', 'content': "
                                "'metric_reference = observed_metrics'})"
                            ]
                        },
                    }
                ),
                json.dumps(
                    {
                        "message_type": "AssistantMessage",
                        "payload": {
                            "content": [
                                "ToolUseBlock(name='Read', input={'file_path': "
                                "'/workspace/pertura_bench/cases/"
                                "metric_references.v1.json'})"
                            ]
                        },
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    audit = execution._audit_reference_truth_access(events, start_offset=0)

    assert audit["status"] == "failed"
    assert audit["scanned_tool_events"] == 2
    assert len(audit["hits"]) == 1
    assert audit["hits"][0]["matched_tokens"] == ["metric_references.v1.json"]


def test_reference_audit_allows_only_registered_provider_assets(
    tmp_path: Path,
) -> None:
    neutral = tmp_path / "paper/task_references/PAPA-06/neutral_inputs"
    neutral.mkdir(parents=True)
    genes = neutral / "genes.tsv"
    eligibility = neutral / "target_eligibility.tsv"
    genes.write_text("gene\nG1\n", encoding="utf-8")
    eligibility.write_text("target_uid\teligible\nT1\ttrue\n", encoding="utf-8")
    truth = tmp_path / "paper/task_references/PAPA-06/reference/trans_de.tsv"
    truth.parent.mkdir(parents=True)
    truth.write_text("answer\n", encoding="utf-8")
    assets = tuple(
        {
            "role": path.stem,
            "path": str(path),
            "content_sha256": file_sha256(path),
        }
        for path in (genes, eligibility)
    )

    safe_events = tmp_path / "safe-events.jsonl"
    safe_events.write_text(
        "\n".join(
            json.dumps(
                {
                    "message_type": "AssistantMessage",
                    "payload": {
                        "content": [
                            "ToolUseBlock(name='Bash', input={'command': "
                            f"'ls {neutral}; cat {path}'}})"
                        ]
                    },
                }
            )
            for path in (genes, eligibility)
        )
        + "\n",
        encoding="utf-8",
    )
    safe = execution._audit_reference_truth_access(
        safe_events,
        start_offset=0,
        allowed_provider_assets=assets,
    )
    assert safe["status"] == "passed"
    assert safe["scanned_tool_events"] == 2
    assert safe["hits"] == []

    mixed_events = tmp_path / "mixed-events.jsonl"
    mixed_events.write_text(
        json.dumps(
            {
                "message_type": "AssistantMessage",
                "payload": {
                    "content": [
                        "ToolUseBlock(name='Bash', input={'command': "
                        f"'cat {genes} {truth}'}})"
                    ]
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    mixed = execution._audit_reference_truth_access(
        mixed_events,
        start_offset=0,
        allowed_provider_assets=assets,
    )
    assert mixed["status"] == "failed"
    assert {
        token.replace("\\", "/") for token in mixed["hits"][0]["matched_tokens"]
    } == {"task_references/"}


def test_reference_audit_does_not_allow_unregistered_neutral_sibling(
    tmp_path: Path,
) -> None:
    neutral = tmp_path / "paper/task_references/PAPA-06/neutral_inputs"
    neutral.mkdir(parents=True)
    registered = neutral / "genes.tsv"
    unregistered = neutral / "hidden.tsv"
    registered.write_text("gene\nG1\n", encoding="utf-8")
    unregistered.write_text("hidden\n", encoding="utf-8")
    assets = (
        {
            "role": "genes",
            "path": str(registered),
            "content_sha256": file_sha256(registered),
        },
    )
    events = tmp_path / "events.jsonl"
    events.write_text(
        json.dumps(
            {
                "message_type": "AssistantMessage",
                "payload": {
                    "content": [
                        "ToolUseBlock(name='Bash', input={'command': "
                        f"'cat {registered}; ls {neutral}; cat {unregistered}'}})"
                    ]
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )

    audit = execution._audit_reference_truth_access(
        events,
        start_offset=0,
        allowed_provider_assets=assets,
    )
    assert audit["status"] == "failed"
    assert {
        token.replace("\\", "/") for token in audit["hits"][0]["matched_tokens"]
    } == {"task_references/"}


def test_reference_truth_leakage_is_invalid_infrastructure() -> None:
    status = execution._classify_task_validity(
        workflow_id="WF-KANG",
        task_status="failed",
        termination_reason="provider_error",
        provider_error=None,
        skill_leakage_audit={"status": "passed"},
        reference_leakage_audit={"status": "failed"},
        resource_evidence={
            "mode": "scheduler",
            "requested_memory_gb": 32,
            "actual_memory_gb": 32,
            "cpu_count": 1,
            "n_jobs": 1,
            "thread_environment": {
                "OMP_NUM_THREADS": "1",
                "OPENBLAS_NUM_THREADS": "1",
                "MKL_NUM_THREADS": "1",
                "NUMEXPR_NUM_THREADS": "1",
            },
        },
    )
    assert status == ("invalid_infrastructure", "invalid_infrastructure")


def test_baseline_skill_leakage_invalidates_workflow_infrastructure(
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

    def execute(agent, prompt, timeout):
        del prompt, timeout
        event = {
            "message_type": "AssistantMessage",
            "payload": {
                "content": [
                    "ToolUseBlock(name='Read', input={'file_path': "
                    "'/env/pertura_runtime/agent_bundle/skills/"
                    "operate-pertura-workflow/SKILL.md'})"
                ]
            },
        }
        events = agent.workspace.logs_dir / "events.jsonl"
        events.parent.mkdir(parents=True, exist_ok=True)
        events.write_text(json.dumps(event) + "\n", encoding="utf-8")

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
        paper_anchor_catalog_path=ROOT / "benchmarks/paper_v1/paper_anchors.v1.json",
        asset_catalog_path=_asset_catalog(tmp_path),
        resource_evidence_path=tmp_path / "resource.json",
        smoke_task_ids=("REPL-03",),
        turn_executor=execute,
        verify_checkpoint=False,
    )

    assert result["execution_status"] == "invalid_infrastructure"
    assert result["score_status"] == "not_scored"
    assert result["skill_leakage_detected"] is True
    verdict = json.loads(Path(result["task_records"][0]["verdict"]).read_text())
    assert verdict["hard_gates"]["no_skill_leakage"] is False
    assert verdict["skill_leakage_audit"]["status"] == "failed"


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
    assert result["task_records"] == [{"task_id": "T", "status": "judge_unavailable"}]


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


def test_dependency_gate_accepts_complete_codeact_artifacts_without_capability_receipt(
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
    assert not (output / "capability_receipt.json").exists()
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
