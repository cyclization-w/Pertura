from __future__ import annotations

import asyncio
import csv
import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any, Callable, Mapping
from uuid import uuid4

from pertura_bench.agent_judge import grade_turn_final, project_judge_answer
from pertura_bench.agent_models import AgentBenchmarkResult
from pertura_bench.paper_tasks import (
    PAPER_AGENT_MAX_TURNS,
    PAPER_CONDITIONS,
    PAPER_REPEATS,
    load_paper_task_catalog,
    validate_paper_anchor_catalog,
    validate_paper_asset_catalog,
    validate_task_reference_catalog,
)
from pertura_bench.paper_task_evaluation import evaluate_paper_task
from pertura_core.hashing import canonical_hash, file_sha256
from pertura_runtime.claude.agent import ClaudePerturaAgent
from pertura_runtime.claude.options import ClaudeRuntimeOptions, describe_options
from pertura_runtime.project.assets import DataAssetRegistry
from pertura_runtime.project.models import AssetBinding
from pertura_runtime.project.workspace import ProjectWorkspace


TurnExecutor = Callable[[ClaudePerturaAgent, str, int], Any]

PAPER_CODEACT_PACKAGES = (
    "anndata",
    "scanpy",
    "numpy",
    "pandas",
    "scipy",
    "sklearn",
    "pyarrow",
)

PAPER_ASSET_KIND_ADAPTER = {
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


def load_paper_asset_catalog(path: str | Path) -> dict[str, Any]:
    resolved = Path(path).resolve()
    payload = json.loads(resolved.read_text(encoding="utf-8"))
    if payload.get("schema_version") != "pertura-paper-agent-assets-v1":
        raise ValueError("unsupported paper agent asset catalog")
    if not isinstance(payload.get("workflows"), Mapping):
        raise ValueError("paper agent asset catalog lacks workflows")
    payload["_catalog_path"] = str(resolved)
    payload["_catalog_sha256"] = file_sha256(resolved)
    return payload


def run_paper_agent_workflow(
    workflow_id: str,
    *,
    repo_root: Path,
    cache: Path,
    paper_root: Path,
    output: Path,
    condition: str,
    repeat_index: int,
    task_catalog_path: Path,
    task_reference_catalog_path: Path,
    paper_anchor_catalog_path: Path,
    asset_catalog_path: Path,
    resource_evidence_path: Path | None = None,
    smoke_task_ids: tuple[str, ...] | None = None,
    turn_executor: TurnExecutor | None = None,
    verify_checkpoint: bool = True,
) -> dict[str, Any]:
    if condition not in PAPER_CONDITIONS:
        raise ValueError(f"unsupported paper benchmark condition: {condition}")
    if repeat_index not in PAPER_REPEATS:
        raise ValueError("formal paper benchmark repeat_index must be 1 or 2")
    repo_root = Path(repo_root).resolve()
    cache = Path(cache).resolve()
    paper_root = Path(paper_root).resolve()
    task_catalog = load_paper_task_catalog(task_catalog_path)
    workflow = dict(task_catalog.workflow(workflow_id))
    workflow_turns = tuple(workflow.get("turns") or ())
    if smoke_task_ids is not None:
        requested = tuple(dict.fromkeys(smoke_task_ids))
        available = {str(item["task_id"]) for item in workflow_turns}
        unknown = sorted(set(requested) - available)
        if not requested:
            raise ValueError("smoke_task_ids cannot be empty")
        if unknown:
            raise ValueError(
                "unknown smoke task IDs for "
                f"{workflow_id}: {', '.join(unknown)}"
            )
        selected = set(requested)
        workflow_turns = tuple(
            item
            for item in workflow_turns
            if str(item["task_id"]) in selected
        )
    task_references = _load_json(task_reference_catalog_path)
    paper_anchors = _load_json(paper_anchor_catalog_path)
    asset_catalog = load_paper_asset_catalog(asset_catalog_path)
    tasks = task_catalog.tasks()
    catalog_problems = [
        *validate_task_reference_catalog(task_references, tasks),
        *validate_paper_anchor_catalog(paper_anchors, tasks),
        *validate_paper_asset_catalog(asset_catalog, task_catalog),
    ]
    if catalog_problems:
        raise ValueError(
            "invalid bound paper catalogs: " + "; ".join(catalog_problems)
        )
    from pertura_bench.resource_evidence import (
        enforce_runtime_resource_budget,
        observe_runtime_resources,
    )

    resource_started = time.monotonic()
    resource_evidence = enforce_runtime_resource_budget(
        _resource_evidence(resource_evidence_path)
    )
    checkpoint = (
        _verify_paper_checkpoint(
            repo_root=repo_root,
            workflow_id=workflow_id,
            condition=condition,
            repeat_index=repeat_index,
            task_catalog_path=task_catalog_path,
            task_reference_catalog_path=task_reference_catalog_path,
            paper_anchor_catalog_path=paper_anchor_catalog_path,
            asset_catalog_path=asset_catalog_path,
        )
        if verify_checkpoint
        else {"test_only": "checkpoint_verification_disabled"}
    )
    workflow_assets = dict(
        (asset_catalog.get("workflows") or {}).get(workflow_id) or {}
    )
    if not workflow_assets:
        raise FileNotFoundError(
            f"paper agent assets are not configured for {workflow_id}"
        )

    execution_root = (
        Path(output).resolve()
        / workflow_id
        / condition
        / f"repeat-{repeat_index}"
        / uuid4().hex
    )
    project = ProjectWorkspace.initialize(
        execution_root / "project", logical_name=workflow_id
    )
    run = project.create_run(logical_name=f"{workflow_id} paper benchmark")
    conversation = project.create_conversation(
        run.run_id, title=f"{workflow_id} paper benchmark"
    )
    registry = DataAssetRegistry(
        project_id=project.project.project_id,
        store=project.store,
        object_root=project.objects_dir,
    )
    registered, asset_paths = _register_workflow_assets(
        registry,
        project=project,
        run_id=run.run_id,
        raw_assets=tuple(workflow_assets.get("assets") or ()),
        cache=cache,
        paper_root=paper_root,
    )
    primary_path = _primary_asset_path(asset_paths)
    workspace = project.run_workspace(run.run_id, input_source=primary_path)
    workspace.write_json(
        workspace.task_dir / "paper_benchmark_assets.json",
        {
            "schema_version": "pertura-paper-agent-registered-assets-v1",
            "workflow_id": workflow_id,
            "dataset_id": workflow["dataset_id"],
            "assets": [
                {
                    "asset_id": asset.asset_id,
                    "role": asset.role,
                    "content_sha256": asset.content_sha256,
                    "path": asset_paths[asset.role],
                }
                for asset in registered
            ],
        },
    )

    model = os.environ.get("PERTURA_CLAUDE_MODEL")
    if not model and turn_executor is None:
        raise RuntimeError(
            "PERTURA_CLAUDE_MODEL must be fixed for controlled comparison"
        )
    runtime_options = ClaudeRuntimeOptions(
        model=model or "fixture-model",
        max_turns=PAPER_AGENT_MAX_TURNS,
        interaction_mode="benchmark",
        enable_bundled_skills=condition == "pertura_full",
        domain_tools_enabled=condition == "pertura_full",
        benchmark_condition=condition,
        python_exe=(
            _paper_science_python()
            if turn_executor is None
            else None
        ),
        python_preflight_packages=list(PAPER_CODEACT_PACKAGES),
    )
    provider_config_hash = canonical_hash(describe_options(runtime_options))
    agent = ClaudePerturaAgent(
        workspace=workspace,
        config=runtime_options,
        project_workspace=project,
        run_id=run.run_id,
        conversation_id=conversation.conversation_id,
        verbose=False,
    )
    if condition == "pertura_full":
        confirmations = dict(workflow_assets.get("design_confirmations") or {})
        agent.product_runtime.inspect_dataset(
            primary_path,
            dataset_id=str(workflow["dataset_id"]),
            confirmations=confirmations or None,
        )

    anchors_by_id = {
        str(item["anchor_id"]): item
        for item in paper_anchors.get("anchors") or ()
    }
    references_by_id = {
        str(item["task_reference_id"]): item
        for item in task_references.get("bindings") or ()
    }
    tasks_by_id = {
        str(item["task_id"]): item for item in workflow.get("turns") or ()
    }
    task_records: list[dict[str, Any]] = []
    try:
        for task in workflow_turns:
            task = dict(task)
            task_id = str(task["task_id"])
            if task.get("role") == "optional" and not _optional_configured(
                task, asset_paths
            ):
                record = _not_configured_task(
                    execution_root,
                    task=task,
                    workflow=workflow,
                    condition=condition,
                    repeat_index=repeat_index,
                )
                task_records.append(record)
                continue

            task_output = workspace.root / "outputs" / "tasks" / task_id
            task_output.mkdir(parents=True, exist_ok=True)
            ancestor_ids = _ancestor_task_ids(task, tasks_by_id)
            existing_prior_hashes = {
                dependency: _tree_hashes(
                    workspace.root / "outputs" / "tasks" / dependency
                )
                for dependency in ancestor_ids
            }
            turn_asset_paths = dict(asset_paths)
            turn_asset_paths.update(
                _dependency_asset_paths(
                    workspace.root,
                    task=task,
                    tasks_by_id=tasks_by_id,
                )
            )
            prompt = _task_prompt(
                workflow=workflow,
                task=task,
                condition=condition,
                asset_paths=turn_asset_paths,
                anchors_by_id=anchors_by_id,
                dependency_contracts={
                    dependency: tasks_by_id[dependency].get("output_contract") or {}
                    for dependency in ancestor_ids
                },
            )
            timeout_seconds = int(task["resources"]["timeout_seconds"])
            timed_out = False
            started = time.monotonic()
            try:
                (turn_executor or _run_with_timeout)(
                    agent, prompt, timeout_seconds
                )
            except TimeoutError:
                timed_out = True
                if (
                    agent.turn_manager is not None
                    and agent.turn_manager.turn is not None
                ):
                    try:
                        asyncio.run(
                            agent.cancel_turn(agent.turn_manager.turn.turn_id)
                        )
                    except Exception:
                        agent.product_runtime.close(graceful=False)
            wall_seconds = time.monotonic() - started
            resource_evidence = observe_runtime_resources(
                resource_evidence, started_monotonic=resource_started
            )
            turns = project.store.list_turns(conversation.conversation_id)
            final = project.store.get_turn_final(turns[-1].turn_id) if turns else None
            result_path = task_output / "benchmark_result.json"
            (
                benchmark_result,
                result_problem,
                evaluation,
                output_gates,
            ) = _evaluate_task_outputs(
                task,
                workspace_root=workspace.root,
                dataset_id=str(workflow["dataset_id"]),
                paper_root=paper_root,
                asset_paths=asset_paths,
                references_by_id=references_by_id,
                tasks_by_id=tasks_by_id,
            )
            mutation_free = all(
                _existing_files_unchanged(
                    previous,
                    _tree_hashes(
                        workspace.root / "outputs" / "tasks" / dependency
                    ),
                )
                for dependency, previous in existing_prior_hashes.items()
            )
            hard_gates = {
                "turn_checkpointed": final is not None,
                **output_gates,
                "prior_task_outputs_immutable": mutation_free,
                "timeout_enforced": not timed_out,
                "resource_evidence": _task_resource_gate(
                    task, resource_evidence
                ),
            }
            status = "passed" if all(hard_gates.values()) else "failed"
            task_root = execution_root / "tasks" / task_id
            task_root.mkdir(parents=True, exist_ok=True)
            if result_path.is_file():
                (task_root / "benchmark_result.json").write_bytes(
                    result_path.read_bytes()
                )
            verdict = {
                "schema_version": "pertura-paper-task-execution-verdict-v1",
                "workflow_id": workflow_id,
                "task_id": task_id,
                "dataset_id": workflow["dataset_id"],
                "condition": condition,
                "repeat_index": repeat_index,
                "status": status,
                "hard_gates": hard_gates,
                "result_problem": result_problem,
                "scientific_evaluation": evaluation,
                "project_id": project.project.project_id,
                "analysis_run_id": run.run_id,
                "conversation_id": conversation.conversation_id,
                "turn_id": final.turn_id if final else None,
                "wall_seconds": wall_seconds,
                "resource_evidence": resource_evidence,
                "post_turn_output_hashes": _tree_hashes(task_output),
                "post_turn_ancestor_hashes": {
                    dependency: _tree_hashes(
                        workspace.root / "outputs" / "tasks" / dependency
                    )
                    for dependency in ancestor_ids
                },
                "post_workflow_regraded": False,
                "repaired_after_turn": False,
            }
            _write(task_root / "verdict.json", verdict)
            if final is not None:
                final_payload = final.model_dump(mode="json")
                _write(task_root / "turn_final.json", final_payload)
                (task_root / "turn_final.md").write_text(
                    final.markdown, encoding="utf-8"
                )
                _write(
                    task_root / "judge" / "answer_projection.json",
                    project_judge_answer(final_payload).model_dump(mode="json"),
                )
                grade_turn_final(
                    final_payload,
                    execution_verdict=verdict,
                    task_context=_judge_task_context(
                        workflow=workflow,
                        task=task,
                        anchors_by_id=anchors_by_id,
                    ),
                    output_path=task_root / "judge" / "grade.json",
                )
            task_records.append(
                {
                    "task_id": task_id,
                    "status": status,
                    "verdict": str(task_root / "verdict.json"),
                }
            )
    finally:
        agent.product_runtime.close(graceful=True)

    task_records = _refresh_workflow_task_verdicts(
        execution_root=execution_root,
        workspace_root=workspace.root,
        workflow=dict(workflow, turns=list(workflow_turns)),
        condition=condition,
        repeat_index=repeat_index,
        paper_root=paper_root,
        asset_paths=asset_paths,
        references_by_id=references_by_id,
        anchors_by_id=anchors_by_id,
        tasks_by_id=tasks_by_id,
    )

    required_records = [
        item
        for item in task_records
        if next(
            task
            for task in workflow_turns
            if task["task_id"] == item["task_id"]
        ).get("role")
        != "optional"
    ]
    workflow_status = (
        "passed"
        if required_records
        and all(item["status"] == "passed" for item in required_records)
        else "failed"
    )
    resource_evidence = observe_runtime_resources(
        resource_evidence, started_monotonic=resource_started
    )
    _write(execution_root / "resource_evidence.observed.json", resource_evidence)
    input_manifest = {
        "schema_version": "pertura-paper-workflow-input-manifest-v1",
        "workflow": workflow,
        "smoke_task_ids": (
            list(smoke_task_ids) if smoke_task_ids is not None else None
        ),
        "condition": condition,
        "repeat_index": repeat_index,
        "model": model,
        "max_turns_per_task": runtime_options.max_turns,
        "provider_config_hash": provider_config_hash,
        "task_catalog_sha256": task_catalog.sha256,
        "task_reference_catalog_sha256": file_sha256(
            Path(task_reference_catalog_path)
        ),
        "paper_anchor_catalog_sha256": file_sha256(
            Path(paper_anchor_catalog_path)
        ),
        "asset_catalog_sha256": asset_catalog["_catalog_sha256"],
        "checkpoint_binding": checkpoint,
        "resource_evidence": resource_evidence,
        "resource_evidence_sha256": (
            file_sha256(Path(resource_evidence_path))
            if resource_evidence_path is not None
            and Path(resource_evidence_path).is_file()
            else None
        ),
        "resource_observation_hash": canonical_hash(resource_evidence),
        "asset_hashes": {
            asset.role: asset.content_sha256 for asset in registered
        },
        "project_id": project.project.project_id,
        "analysis_run_id": run.run_id,
        "conversation_id": conversation.conversation_id,
    }
    _write(execution_root / "input_manifest.json", input_manifest)
    summary = {
        "schema_version": "pertura-paper-workflow-execution-v1",
        "workflow_id": workflow_id,
        "dataset_id": workflow["dataset_id"],
        "condition": condition,
        "repeat_index": repeat_index,
        "smoke_task_ids": (
            list(smoke_task_ids) if smoke_task_ids is not None else None
        ),
        "execution_status": "completed",
        "score_status": workflow_status,
        "status": workflow_status,
        "task_records": task_records,
        "required_task_count": len(required_records),
        "passed_required_task_count": sum(
            item["status"] == "passed" for item in required_records
        ),
        "project_id": project.project.project_id,
        "analysis_run_id": run.run_id,
        "conversation_id": conversation.conversation_id,
    }
    _write(execution_root / "workflow_verdict.json", summary)
    return dict(summary, execution_root=str(execution_root))


def _refresh_workflow_task_verdicts(
    *,
    execution_root: Path,
    workspace_root: Path,
    workflow: Mapping[str, Any],
    condition: str,
    repeat_index: int,
    paper_root: Path,
    asset_paths: Mapping[str, str],
    references_by_id: Mapping[str, Mapping[str, Any]],
    anchors_by_id: Mapping[str, Mapping[str, Any]],
    tasks_by_id: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Re-evaluate outputs after later turns may have repaired missing files."""

    records: list[dict[str, Any]] = []
    dataset_id = str(workflow["dataset_id"])
    for raw_task in workflow.get("turns") or ():
        task = dict(raw_task)
        task_id = str(task["task_id"])
        task_root = execution_root / "tasks" / task_id
        verdict_path = task_root / "verdict.json"
        if not verdict_path.is_file():
            continue
        verdict = _load_json(verdict_path)
        if verdict.get("status") == "not_configured":
            records.append(
                {
                    "task_id": task_id,
                    "status": "not_configured",
                    "verdict": str(verdict_path),
                }
            )
            continue

        task_output = workspace_root / "outputs" / "tasks" / task_id
        current_output_hashes = _tree_hashes(task_output)
        current_ancestor_hashes = {
            dependency: _tree_hashes(
                workspace_root / "outputs" / "tasks" / dependency
            )
            for dependency in _ancestor_task_ids(task, tasks_by_id)
        }
        changed_after_turn = (
            verdict.get("post_turn_output_hashes") != current_output_hashes
            or verdict.get("post_turn_ancestor_hashes")
            != current_ancestor_hashes
        )
        if not changed_after_turn:
            records.append(
                {
                    "task_id": task_id,
                    "status": str(verdict.get("status") or "failed"),
                    "verdict": str(verdict_path),
                }
            )
            continue

        result_path = task_output / "benchmark_result.json"
        (
            benchmark_result,
            result_problem,
            evaluation,
            output_gates,
        ) = _evaluate_task_outputs(
            task,
            workspace_root=workspace_root,
            dataset_id=dataset_id,
            paper_root=paper_root,
            asset_paths=asset_paths,
            references_by_id=references_by_id,
            tasks_by_id=tasks_by_id,
        )
        hard_gates = dict(verdict.get("hard_gates") or {})
        original_output_present = bool(
            hard_gates.get("output_contract_present")
        )
        hard_gates.update(output_gates)
        status = "passed" if all(hard_gates.values()) else "failed"
        verdict.update(
            {
                "status": status,
                "hard_gates": hard_gates,
                "result_problem": result_problem,
                "scientific_evaluation": evaluation,
                "post_workflow_regraded": True,
                "repaired_after_turn": (
                    not original_output_present and result_path.is_file()
                ),
                "post_workflow_output_hashes": current_output_hashes,
                "post_workflow_ancestor_hashes": current_ancestor_hashes,
            }
        )
        if result_path.is_file():
            (task_root / "benchmark_result.json").write_bytes(
                result_path.read_bytes()
            )
        _write(verdict_path, verdict)
        final_path = task_root / "turn_final.json"
        if final_path.is_file():
            grade_turn_final(
                _load_json(final_path),
                execution_verdict=verdict,
                task_context=_judge_task_context(
                    workflow=workflow,
                    task=task,
                    anchors_by_id=anchors_by_id,
                ),
                output_path=task_root / "judge" / "grade.json",
            )
        records.append(
            {
                "task_id": task_id,
                "status": status,
                "verdict": str(verdict_path),
            }
        )
    return records


def regrade_paper_agent_workflow(execution_root: str | Path) -> dict[str, Any]:
    root = Path(execution_root).resolve()
    input_manifest = _load_json(root / "input_manifest.json")
    records: list[dict[str, Any]] = []
    for task in input_manifest["workflow"].get("turns") or ():
        task_id = str(task["task_id"])
        task_root = root / "tasks" / task_id
        if not task_root.is_dir():
            continue
        verdict = _load_json(task_root / "verdict.json")
        final_path = task_root / "turn_final.json"
        if not final_path.is_file():
            records.append({"task_id": task_id, "status": "judge_unavailable"})
            continue
        grade = grade_turn_final(
            _load_json(final_path),
            execution_verdict=verdict,
            task_context={
                "case_id": task_id,
                "dataset_id": input_manifest["workflow"]["dataset_id"],
                "objective": task["objective"],
                "claim_ceiling": task["claim_ceiling"],
                "paper_anchor_ids": task["paper_anchor_ids"],
            },
            output_path=task_root / "judge" / "grade.json",
        )
        records.append(
            {"task_id": task_id, "status": grade.get("status", "failed")}
        )
    payload = {
        "schema_version": "pertura-paper-workflow-regrade-v1",
        "workflow_id": input_manifest["workflow"]["workflow_id"],
        "provider_invoked": False,
        "task_records": records,
    }
    _write(root / "regrade.json", payload)
    return payload


def _paper_science_python() -> str:
    """Resolve the frozen general-purpose CodeAct Python environment."""

    raw_prefix = os.environ.get("PERTURA_PYTHON_SCIENCE_ENV", "").strip()
    if not raw_prefix:
        raise RuntimeError(
            "PERTURA_PYTHON_SCIENCE_ENV must identify the frozen "
            "python-science-v1 environment"
        )
    prefix = Path(raw_prefix).expanduser().resolve()
    candidates = (prefix / "bin" / "python", prefix / "python.exe")
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    raise FileNotFoundError(
        f"python-science-v1 interpreter is missing under {prefix}"
    )


def _benchmark_result_template(
    *,
    workflow: Mapping[str, Any],
    task: Mapping[str, Any],
) -> dict[str, Any]:
    """Return a schema-valid structural template for the task result file."""

    artifact_roles = list(task["required_artifact_roles"])
    result_type = f"{str(task['task_id']).lower()}_scientific_result"
    return {
        "schema_version": "pertura-agent-benchmark-result-v1",
        "case_id": str(task["task_id"]),
        "dataset_id": str(workflow["dataset_id"]),
        "result_type": result_type,
        "analysis_unit": "REPLACE_WITH_ACTUAL_ANALYSIS_UNIT",
        "status": "completed",
        "findings": [
            {
                "finding_id": "finding_1",
                "text": "REPLACE_WITH_ARTIFACT_GROUNDED_FINDING",
                "metric_ids": [],
                "artifact_roles": artifact_roles,
            }
        ],
        "metrics": {},
        "limitations": ["REPLACE_WITH_MATERIAL_LIMITATION"],
        "artifact_roles": artifact_roles,
    }


def _task_prompt(
    *,
    workflow: Mapping[str, Any],
    task: Mapping[str, Any],
    condition: str,
    asset_paths: Mapping[str, str],
    anchors_by_id: Mapping[str, Mapping[str, Any]],
    dependency_contracts: Mapping[str, Mapping[str, Any]],
) -> str:
    relevant_assets = {
        role: asset_paths[role]
        for role in task.get("required_input_roles") or ()
        if role in asset_paths
    }
    missing_assets = sorted(
        set(task.get("required_input_roles") or ()) - set(relevant_assets)
    )
    anchors = [
        anchors_by_id[anchor_id]
        for anchor_id in task.get("paper_anchor_ids") or ()
    ]
    surface = (
        "Use the Pertura workflow and domain tools where the task declares "
        "capabilities. Generic CodeAct remains available for explicitly "
        "non-capability scientific tasks."
        if condition == "pertura_full"
        else "Use the available generic CodeAct tools under this benchmark condition."
    )
    result_template = _benchmark_result_template(
        workflow=workflow,
        task=task,
    )
    return (
        f"Paper benchmark workflow {workflow['workflow_id']}, task {task['task_id']} "
        f"(turn {task['turn_index']}). Objective: {task['objective']} "
        f"Execution mode: {task['execution_mode']}. {surface} "
        "Run Bash commands synchronously; background or detached execution "
        "is disabled so task completion and grading share one boundary. "
        f"Registered task assets: {json.dumps(relevant_assets, sort_keys=True)}. "
        f"Missing registered roles: {json.dumps(missing_assets)}. "
        "Upstream repair contracts: "
        f"{json.dumps(dependency_contracts, sort_keys=True)}. "
        f"Paper anchors (framing only, never measurements): "
        f"{json.dumps(anchors, sort_keys=True)}. "
        f"Required artifact roles: {json.dumps(task['required_artifact_roles'])}. "
        f"Output contract: {json.dumps(task['output_contract'], sort_keys=True)}. "
        f"Hard gates: {json.dumps(task['task_hard_gates'])}. "
        f"Claim ceiling: {task['claim_ceiling']}. "
        "You may repair a missing upstream artifact by writing its previously "
        "missing file, but must not overwrite an existing prior-turn artifact. "
        f"Before completing, write {task['output_contract']['benchmark_result']} "
        "as a standalone pertura-agent-benchmark-result-v1 JSON file. Replace "
        "the REPLACE_WITH values in this exact structural template: "
        f"{json.dumps(result_template, sort_keys=True)}. "
        "The result file permits only these top-level fields: schema_version, "
        "case_id, dataset_id, result_type, analysis_unit, status, findings, "
        "metrics, limitations, artifact_roles. artifact_roles must be a JSON "
        "array of role-name strings. Each finding permits only finding_id, "
        "text, metric_ids, and artifact_roles. Do not put hypotheses, "
        "questions_for_user, next_steps, artifact_refs, declared_role, "
        "result_ids, or finding-level limitations in benchmark_result.json. "
        "The metrics object is a self-reported index only and cannot replace "
        "the required scientific artifacts. "
        "After writing that file, return the separate provider response using "
        "the existing pertura-turn-draft-v1 contract. Never copy the TurnDraft "
        "object into benchmark_result.json. Independent evaluators, not "
        "self-reported metrics, determine scientific correctness."
    )


def _judge_task_context(
    *,
    workflow: Mapping[str, Any],
    task: Mapping[str, Any],
    anchors_by_id: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    return {
        "case_id": str(task["task_id"]),
        "dataset_id": str(workflow["dataset_id"]),
        "objective": str(task["objective"]),
        "claim_ceiling": str(task["claim_ceiling"]),
        "paper_anchors": [
            dict(anchors_by_id[anchor_id])
            for anchor_id in task.get("paper_anchor_ids") or ()
        ],
    }


def _register_workflow_assets(
    registry: DataAssetRegistry,
    *,
    project: ProjectWorkspace,
    run_id: str,
    raw_assets: tuple[Mapping[str, Any], ...],
    cache: Path,
    paper_root: Path,
) -> tuple[list[Any], dict[str, str]]:
    registered: list[Any] = []
    paths: dict[str, str] = {}
    roots = {
        "cache": cache,
        "paper_root": paper_root,
        "benchmark_root": paper_root.parent,
    }
    for raw in raw_assets:
        role = str(raw.get("role") or "")
        root_name = str(raw.get("root") or "")
        relative = str(raw.get("relative_path") or "")
        expected_hash = str(raw.get("content_sha256") or "")
        if not role or root_name not in roots or not relative:
            raise ValueError("paper agent asset lacks role/root/relative_path")
        base = roots[root_name].resolve()
        path = (base / relative).resolve()
        if path != base and base not in path.parents:
            raise ValueError(f"paper agent asset escapes {root_name}: {role}")
        if not path.exists():
            raise FileNotFoundError(f"paper agent asset is missing: {role}")
        if not expected_hash.startswith("sha256:"):
            raise ValueError(f"paper agent asset hash is invalid: {role}")
        catalog_kind = str(raw.get("kind") or "external_resource")
        try:
            registered_kind, default_source_class = PAPER_ASSET_KIND_ADAPTER[
                catalog_kind
            ]
        except KeyError as exc:
            raise ValueError(
                f"unsupported paper agent asset kind: {catalog_kind}"
            ) from exc
        asset = registry.register(
            path,
            role=role,
            kind=registered_kind,
            source_class=raw.get("source_class") or default_source_class,
        )
        if asset.content_sha256 != expected_hash:
            raise ValueError(f"paper agent asset checksum mismatch: {role}")
        project.store.put_asset_binding(
            AssetBinding(run_id=run_id, asset_id=asset.asset_id, role=asset.role)
        )
        registered.append(asset)
        paths[role] = str(path)
    if len(paths) != len(registered):
        raise ValueError("paper agent asset roles must be unique")
    return registered, paths


def _primary_asset_path(asset_paths: Mapping[str, str]) -> Path:
    for role in ("evaluation_split", "primary_h5ad"):
        if role in asset_paths:
            return Path(asset_paths[role]).resolve()
    raise FileNotFoundError(
        "paper workflow requires evaluation_split or primary_h5ad"
    )


def _optional_configured(
    task: Mapping[str, Any], asset_paths: Mapping[str, str]
) -> bool:
    if task.get("configuration_gate") == "prediction_manifest_present":
        return "prediction_manifest_optional" in asset_paths
    return True


def _dependency_asset_paths(
    workspace_root: Path,
    *,
    task: Mapping[str, Any],
    tasks_by_id: Mapping[str, Mapping[str, Any]],
) -> dict[str, str]:
    paths: dict[str, str] = {}
    for dependency in _ancestor_task_ids(task, tasks_by_id):
        dependency_task = tasks_by_id.get(str(dependency))
        if dependency_task is None:
            continue
        contract = dependency_task.get("output_contract") or {}
        for role, relative in (contract.get("artifact_paths") or {}).items():
            path = (
                workspace_root
                / "outputs"
                / "tasks"
                / str(dependency)
                / str(relative)
            ).resolve()
            if path.exists():
                paths[str(role)] = str(path)
    return paths


def _ancestor_task_ids(
    task: Mapping[str, Any],
    tasks_by_id: Mapping[str, Mapping[str, Any]],
) -> tuple[str, ...]:
    pending = [str(item) for item in task.get("depends_on_tasks") or ()]
    visited: set[str] = set()
    ordered: list[str] = []
    while pending:
        dependency = pending.pop()
        if dependency in visited:
            continue
        visited.add(dependency)
        ordered.append(dependency)
        pending.extend(
            str(item)
            for item in tasks_by_id.get(dependency, {}).get("depends_on_tasks") or ()
        )
    return tuple(ordered)


def _existing_files_unchanged(
    before: Mapping[str, str], after: Mapping[str, str]
) -> bool:
    """Allow additive repair while protecting every prior-turn file."""

    return all(after.get(relative) == digest for relative, digest in before.items())


def _not_configured_task(
    execution_root: Path,
    *,
    task: Mapping[str, Any],
    workflow: Mapping[str, Any],
    condition: str,
    repeat_index: int,
) -> dict[str, Any]:
    task_root = execution_root / "tasks" / str(task["task_id"])
    verdict = {
        "schema_version": "pertura-paper-task-execution-verdict-v1",
        "workflow_id": workflow["workflow_id"],
        "task_id": task["task_id"],
        "dataset_id": workflow["dataset_id"],
        "condition": condition,
        "repeat_index": repeat_index,
        "status": "not_configured",
        "reason": "optional prediction manifest is absent",
        "hard_gates": {},
    }
    _write(task_root / "verdict.json", verdict)
    return {
        "task_id": task["task_id"],
        "status": "not_configured",
        "verdict": str(task_root / "verdict.json"),
    }


def _load_task_result(
    path: Path, *, task_id: str, dataset_id: str
) -> tuple[AgentBenchmarkResult | None, str | None]:
    if not path.is_file():
        return None, "benchmark_result.json is missing"
    try:
        result = AgentBenchmarkResult.model_validate_json(
            path.read_text(encoding="utf-8")
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return None, str(exc)
    if result.case_id != task_id or result.dataset_id != dataset_id:
        return None, "benchmark result task or dataset identity mismatch"
    if result.status not in {"completed", "blocked"}:
        return None, f"benchmark result status is {result.status}"
    return result, None


def _evaluate_task_outputs(
    task: Mapping[str, Any],
    *,
    workspace_root: Path,
    dataset_id: str,
    paper_root: Path,
    asset_paths: Mapping[str, str],
    references_by_id: Mapping[str, Mapping[str, Any]],
    tasks_by_id: Mapping[str, Mapping[str, Any]],
) -> tuple[
    AgentBenchmarkResult | None,
    str | None,
    dict[str, Any],
    dict[str, bool],
]:
    task_id = str(task["task_id"])
    task_output = workspace_root / "outputs" / "tasks" / task_id
    result_path = task_output / "benchmark_result.json"
    benchmark_result, result_problem = _load_task_result(
        result_path,
        task_id=task_id,
        dataset_id=dataset_id,
    )
    bindings = [
        references_by_id[reference_id]
        for reference_id in task.get("task_reference_ids") or ()
        if reference_id in references_by_id
    ]
    evaluation = evaluate_paper_task(
        task,
        benchmark_result=(
            benchmark_result.model_dump(mode="json")
            if benchmark_result is not None
            else None
        ),
        task_output_root=task_output,
        paper_root=paper_root,
        bindings=bindings,
    )
    resolved_inputs = dict(asset_paths)
    resolved_inputs.update(
        _dependency_asset_paths(
            workspace_root,
            task=task,
            tasks_by_id=tasks_by_id,
        )
    )
    required_roles = set(task.get("required_artifact_roles") or ())
    observed_roles = set(
        benchmark_result.artifact_roles if benchmark_result else ()
    )
    gates = {
        "output_contract_present": result_path.is_file(),
        "benchmark_result_schema_valid": benchmark_result is not None,
        "required_artifact_roles": required_roles.issubset(observed_roles),
        "required_artifact_paths": _artifact_paths_present(
            task_output, task.get("output_contract") or {}
        ),
        "dependencies_present": (
            _dependency_outputs_complete(
                workspace_root,
                task=task,
                tasks_by_id=tasks_by_id,
                dataset_id=dataset_id,
            )
            and set(task.get("required_input_roles") or ()).issubset(
                resolved_inputs
            )
        ),
        "task_reference_bound": len(bindings)
        == len(task.get("task_reference_ids") or ()),
        "independent_evaluation": evaluation.get("status") == "passed",
    }
    return benchmark_result, result_problem, evaluation, gates


def _dependency_outputs_complete(
    workspace_root: Path,
    *,
    task: Mapping[str, Any],
    tasks_by_id: Mapping[str, Mapping[str, Any]],
    dataset_id: str,
) -> bool:
    for dependency_id in task.get("depends_on_tasks") or ():
        dependency = tasks_by_id.get(str(dependency_id))
        if dependency is None:
            return False
        output_root = (
            workspace_root / "outputs" / "tasks" / str(dependency_id)
        )
        result, _ = _load_task_result(
            output_root / "benchmark_result.json",
            task_id=str(dependency_id),
            dataset_id=dataset_id,
        )
        if result is None:
            return False
        required_roles = set(
            dependency.get("required_artifact_roles") or ()
        )
        if not required_roles.issubset(result.artifact_roles):
            return False
        if not _artifact_paths_present(
            output_root, dependency.get("output_contract") or {}
        ):
            return False
    return True


def _task_resource_gate(
    task: Mapping[str, Any], evidence: Mapping[str, Any]
) -> bool:
    resources = task.get("resources") or {}
    requested_memory_gb = float(evidence.get("requested_memory_gb", 0))
    actual_memory_gb = float(evidence.get("actual_memory_gb", 0))
    task_memory_gb = float(resources.get("max_memory_gb", 0))
    # A paper workflow is one scheduler job containing several turns.  The
    # scheduler request is therefore the maximum requirement across the
    # workflow, not the exact budget of every individual turn.  It must cover
    # the current turn, while the observed peak must still fit inside the
    # allocation proved by the selected resource-enforcement evidence.
    return bool(
        evidence
        and evidence.get("mode") in {"scheduler", "cgroup", "rlimit"}
        and _resource_identity_is_valid(evidence)
        and requested_memory_gb >= task_memory_gb > 0
        and actual_memory_gb >= requested_memory_gb
        and int(evidence.get("n_jobs", 0)) == int(resources.get("n_jobs", 1))
        and int(evidence.get("timeout_seconds", 0))
        >= int(resources.get("timeout_seconds", 0))
        and float(evidence.get("peak_rss_mb", 0)) > 0
        and float(evidence.get("peak_rss_mb", 0)) <= actual_memory_gb * 1024.0
    )


def _resource_identity_is_valid(evidence: Mapping[str, Any]) -> bool:
    mode = evidence.get("mode")
    if mode == "scheduler":
        return bool(evidence.get("scheduler_job_id"))
    if mode == "cgroup":
        return bool(evidence.get("cgroup_identity"))
    if mode == "rlimit":
        return bool(
            evidence.get("enforcement_active") is True
            and evidence.get("rlimit_identity")
            and int(evidence.get("rlimit_as_bytes", 0)) > 0
        )
    return False


def _artifact_paths_present(
    task_output_root: Path, output_contract: Mapping[str, Any]
) -> bool:
    base = task_output_root.resolve()
    artifact_paths = dict(output_contract.get("artifact_paths") or {})
    artifact_roles = set(output_contract.get("artifact_roles") or ())
    if not artifact_roles or set(artifact_paths) != artifact_roles:
        return False
    for relative in artifact_paths.values():
        path = (base / str(relative)).resolve()
        if (
            path == base
            or base not in path.parents
            or not path.exists()
        ):
            return False
    for relative, required_columns in (
        output_contract.get("artifact_schemas") or {}
    ).items():
        path = (base / str(relative)).resolve()
        suffix = path.suffix.lower()
        try:
            if suffix in {".tsv", ".txt", ".csv"}:
                delimiter = "\t" if suffix in {".tsv", ".txt"} else ","
                with path.open("r", encoding="utf-8", newline="") as handle:
                    observed = set(
                        csv.DictReader(handle, delimiter=delimiter).fieldnames or ()
                    )
            elif suffix == ".json":
                payload = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(payload, Mapping):
                    return False
                observed = set(payload)
            else:
                return False
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            return False
        if not set(str(item) for item in required_columns).issubset(observed):
            return False
    return True


def _resource_evidence(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    from pertura_bench.resource_evidence import load_resource_evidence

    return load_resource_evidence(path)


def _verify_paper_checkpoint(
    *,
    repo_root: Path,
    workflow_id: str,
    condition: str,
    repeat_index: int,
    task_catalog_path: Path,
    task_reference_catalog_path: Path,
    paper_anchor_catalog_path: Path,
    asset_catalog_path: Path,
) -> dict[str, str]:
    import subprocess

    plan_path_value = os.environ.get("PERTURA_BENCH_CHECKPOINT_BINDING")
    if not plan_path_value:
        raise FileNotFoundError(
            "PERTURA_BENCH_CHECKPOINT_BINDING is not set to the bound plan"
        )
    plan_path = Path(plan_path_value).expanduser().resolve()
    if not plan_path.is_file():
        raise FileNotFoundError(f"bound paper plan is missing: {plan_path}")
    from pertura_bench.capability_models import ServerBenchmarkPlan
    from pertura_bench.server_plan import assert_server_plan_executable

    plan = ServerBenchmarkPlan.model_validate_json(
        plan_path.read_text(encoding="utf-8")
    )
    assert_server_plan_executable(plan)
    binding = {str(key): str(value) for key, value in plan.checkpoint_binding.items()}
    catalogs = {
        "paper_task_catalog_hash": Path(task_catalog_path),
        "paper_task_reference_catalog_hash": Path(task_reference_catalog_path),
        "paper_anchor_catalog_hash": Path(paper_anchor_catalog_path),
        "paper_asset_catalog_hash": Path(asset_catalog_path),
    }
    for field, path in catalogs.items():
        if not path.is_file() or file_sha256(path) != binding[field]:
            raise ValueError(f"paper checkpoint catalog drift: {field}")
    expected_job_id = (
        f"paper-agent:{workflow_id}:{condition}:repeat-{repeat_index}"
    )
    matching = [job for job in plan.jobs if job.get("job_id") == expected_job_id]
    if len(matching) != 1:
        raise ValueError(f"bound plan lacks paper workflow job: {expected_job_id}")
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_root,
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode or completed.stdout.strip().lower() != binding["git_commit"]:
        raise ValueError("paper checkpoint checkout commit drift")
    wheel_value = os.environ.get("PERTURA_BENCH_WHEEL")
    if not wheel_value:
        raise FileNotFoundError("PERTURA_BENCH_WHEEL is not set")
    wheel = Path(wheel_value).expanduser().resolve()
    if not wheel.is_file() or file_sha256(wheel) != binding["wheel_sha256"]:
        raise ValueError("paper checkpoint wheel drift")
    return binding


def _run_with_timeout(agent: ClaudePerturaAgent, prompt: str, timeout: int):
    async def run():
        return await asyncio.wait_for(agent.run(prompt), timeout=timeout)

    return asyncio.run(run())


def _tree_hashes(root: Path) -> dict[str, str]:
    if not root.is_dir():
        return {}
    return {
        path.relative_to(root).as_posix(): file_sha256(path)
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _load_json(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _write(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(dict(payload), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
