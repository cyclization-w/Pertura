from __future__ import annotations

import json
import re
from pathlib import Path

from pertura_bench import paper_agent_execution as execution
from pertura_core.hashing import file_sha256


ROOT = Path(__file__).resolve().parents[2]


class _Runtime:
    def inspect_dataset(self, *args, **kwargs):
        return {"contract_id": "fixture"}

    def close(self, graceful=True):
        return None


class _Agent:
    def __init__(self, *, workspace, **kwargs):
        self.workspace = workspace
        self.product_runtime = _Runtime()
        self.turn_manager = None


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
