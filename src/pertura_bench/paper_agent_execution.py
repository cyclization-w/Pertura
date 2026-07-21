from __future__ import annotations

import ast
import asyncio
import csv
import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Any, Callable, Mapping
from uuid import uuid4

from pertura_bench.agent_judge import grade_turn_final, project_judge_answer
from pertura_bench.agent_models import AgentBenchmarkResult
from pertura_bench.capability_availability import (
    availability_by_task,
    build_task_capability_availability,
)
from pertura_bench.paper_tasks import (
    PAPER_AGENT_MAX_TURNS,
    PAPER_CONDITIONS,
    PAPER_REPEATS,
    PAPER_WORKFLOW_MEMORY_GB,
    load_paper_task_catalog,
    validate_paper_anchor_catalog,
    validate_paper_asset_catalog,
    validate_task_reference_catalog,
)
from pertura_bench.paper_task_evaluation import evaluate_paper_task
from pertura_bench.paper_capability_bindings import (
    build_paper_task_invocation_bindings,
    provider_binding_contract,
)
from pertura_bench.real_execution import load_design_confirmation_catalog
from pertura_bench.task_submission import (
    SUBMISSION_ALLOWED_TOOL,
    SUBMISSION_SERVER_NAME,
    TaskSubmissionService,
    create_task_submission_mcp_server,
    validate_submission_receipt,
)
from pertura_core import DatasetContract
from pertura_core.hashing import canonical_hash, file_sha256
from pertura_runtime.agent_bundle import (
    BUNDLED_SKILL_NAMES,
    bundled_skill_manifest,
)
from pertura_runtime.claude.agent import ClaudePerturaAgent
from pertura_runtime.claude.options import ClaudeRuntimeOptions, describe_options
from pertura_runtime.project.assets import DataAssetRegistry
from pertura_runtime.project.models import AssetBinding, TurnDraft
from pertura_runtime.project.workspace import ProjectWorkspace
from pertura_workflow.capability_contracts import build_capability_contract_catalog


CAPABILITY_BINDING_QUALIFICATION_STATUSES = frozenset(
    {
        "executed",
        "expected_blocked_probe",
        "executed_terminal_diagnostic_block",
    }
)

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
    "derived": ("derived", "derived_artifact"),
    "exploratory": ("exploratory", "hypothesis"),
    "external_resource": ("external_resource", "curated_prior"),
    "environment_lock": ("external_resource", "curated_prior"),
    "executable": ("external_resource", "curated_prior"),
    "protocol": ("external_resource", "curated_prior"),
    "reference_lock": ("external_resource", "curated_prior"),
    "prior": ("external_resource", "curated_prior"),
}

# The paper protocol names the shared H5AD by its representation while the
# product capability protocol names the same object by its canonical role.
# Keep this adapter deliberately narrow: unlike a general role-alias layer, it
# cannot make semantically different inputs interchangeable.
PAPER_CAPABILITY_ASSET_ALIASES = {
    "primary_h5ad": "primary_dataset",
    "donor_metadata": "cell_metadata",
    "construct_metadata": "cell_metadata",
    "target_expression": "expression_table",
    "trans_de_results": "effect_table",
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
    capability_contract_catalog_path: Path | None = None,
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
                "unknown smoke task IDs for " f"{workflow_id}: {', '.join(unknown)}"
            )
        selected = set(requested)
        workflow_turns = tuple(
            item for item in workflow_turns if str(item["task_id"]) in selected
        )
    task_references = _load_json(task_reference_catalog_path)
    paper_anchors = _load_json(paper_anchor_catalog_path)
    asset_catalog = load_paper_asset_catalog(asset_catalog_path)
    design_catalog, design_catalog_sha256 = load_design_confirmation_catalog()
    dataset_design = dict(
        (design_catalog.get("datasets") or {}).get(str(workflow["dataset_id"])) or {}
    )
    partial_contract_template = dict(dataset_design.get("paper_contract") or {})
    if not partial_contract_template:
        raise ValueError(
            f"paper partial contract is unavailable for {workflow['dataset_id']}"
        )
    skill_manifest = bundled_skill_manifest() if condition == "pertura_full" else None
    if capability_contract_catalog_path is not None:
        contract_catalog = _load_json(capability_contract_catalog_path)
        if (
            contract_catalog.get("schema_version")
            != "pertura-capability-contract-catalog-v1"
            or int(contract_catalog.get("capability_count", 0)) != 44
            or int(contract_catalog.get("active_capability_count", 0)) != 40
            or not str(contract_catalog.get("catalog_hash") or "").startswith("sha256:")
        ):
            raise ValueError("a19 capability contract catalog is invalid")
    elif verify_checkpoint:
        raise FileNotFoundError(
            "formal paper execution requires a capability contract catalog"
        )
    else:
        contract_catalog = build_capability_contract_catalog()
    capability_availability = build_task_capability_availability(
        task_catalog.payload,
        contract_catalog,
    )
    capability_availability_by_task = availability_by_task(capability_availability)
    tasks = task_catalog.tasks()
    catalog_problems = [
        *validate_task_reference_catalog(task_references, tasks),
        *validate_paper_anchor_catalog(paper_anchors, tasks),
        *validate_paper_asset_catalog(asset_catalog, task_catalog),
    ]
    if catalog_problems:
        raise ValueError("invalid bound paper catalogs: " + "; ".join(catalog_problems))
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
            capability_contract_catalog_path=Path(capability_contract_catalog_path),
            capability_availability_hash=capability_availability["canonical_hash"],
        )
        if verify_checkpoint
        else {"test_only": "checkpoint_verification_disabled"}
    )
    if verify_checkpoint and (
        checkpoint.get("design_confirmation_catalog_hash") != design_catalog_sha256
    ):
        raise ValueError("paper checkpoint catalog drift: design confirmations")
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
    registered_assets = {
        asset.role: {
            "asset_id": asset.asset_id,
            "path": asset_paths[asset.role],
            "content_sha256": asset.content_sha256,
            "kind": str(asset.kind),
            "source_class": str(asset.source_class),
        }
        for asset in registered
    }
    primary_path = _primary_asset_path(asset_paths)
    workspace = project.run_workspace(run.run_id, input_source=primary_path)
    submission_service = TaskSubmissionService(workspace.root)

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
        python_exe=(_paper_science_python() if turn_executor is None else None),
        python_preflight_packages=list(PAPER_CODEACT_PACKAGES),
        additional_mcp_server_factories={
            SUBMISSION_SERVER_NAME: lambda: create_task_submission_mcp_server(
                submission_service
            )
        },
        additional_allowed_tools=(SUBMISSION_ALLOWED_TOOL,),
        final_output_provider=submission_service.submitted_turn_draft,
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
    paper_contract = _paper_dataset_contract(
        dataset_id=str(workflow["dataset_id"]),
        template=partial_contract_template,
        registered_assets=registered_assets,
    )
    registered_contract = agent.product_runtime.register_dataset_contract(
        paper_contract
    )

    anchors_by_id = {
        str(item["anchor_id"]): item for item in paper_anchors.get("anchors") or ()
    }
    references_by_id = {
        str(item["task_reference_id"]): item
        for item in task_references.get("bindings") or ()
    }
    tasks_by_id = {str(item["task_id"]): item for item in workflow.get("turns") or ()}
    task_records: list[dict[str, Any]] = []
    contract_subsets: list[dict[str, Any]] = []
    try:
        for task in workflow_turns:
            task = dict(task)
            task_id = str(task["task_id"])
            evaluation_domain = _task_evaluation_domain(task, references_by_id)
            if task.get("role") == "optional" and not _optional_configured(
                task, asset_paths
            ):
                record = _not_configured_task(
                    execution_root,
                    task=task,
                    workflow=workflow,
                    condition=condition,
                    repeat_index=repeat_index,
                    evaluation_domain=evaluation_domain,
                )
                task_records.append(record)
                continue

            task_output = workspace.root / "outputs" / "tasks" / task_id
            task_output.mkdir(parents=True, exist_ok=True)
            submission_service.bind_task(
                task_id=task_id,
                dataset_id=str(workflow["dataset_id"]),
                allowed_analysis_units=tuple(
                    str(item)
                    for item in (
                        (task.get("output_contract") or {}).get(
                            "allowed_analysis_units"
                        )
                        or ()
                    )
                ),
            )
            task_root = execution_root / "tasks" / task_id
            task_root.mkdir(parents=True, exist_ok=True)
            result_path = task_output / "benchmark_result.json"
            neutral_result = _neutral_benchmark_result(
                workflow=workflow,
                task=task,
            )
            _write(result_path, neutral_result)
            initial_result_sha256 = file_sha256(result_path)
            _write(task_root / "benchmark_result.initial.json", neutral_result)
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
            task_input_roles = tuple(
                str(item) for item in task.get("required_input_roles") or ()
            )
            task_registered_assets = {
                role: registered_assets[role]
                for role in task_input_roles
                if role in registered_assets
            }
            if condition == "pertura_full":
                for (
                    paper_role,
                    capability_role,
                ) in PAPER_CAPABILITY_ASSET_ALIASES.items():
                    if (
                        paper_role in task_input_roles
                        and capability_role in registered_assets
                    ):
                        task_registered_assets[capability_role] = registered_assets[
                            capability_role
                        ]
            task_asset_manifest = _task_asset_manifest(
                workflow_id=workflow_id,
                dataset_id=str(workflow["dataset_id"]),
                condition=condition,
                roles=task_input_roles,
                asset_paths=turn_asset_paths,
                registered_assets=task_registered_assets,
            )
            workspace.write_json(
                workspace.task_dir / "paper_benchmark_assets.json",
                task_asset_manifest,
            )
            scientific_contract_context = _shared_scientific_contract_context(
                contract=paper_contract,
                task=task,
            )
            contract_context = None
            contract_subset_record = None
            if condition == "pertura_full":
                contract, committed = agent.product_runtime.planning_material(
                    str(registered_contract["contract_id"])
                )
                committed_records = (
                    agent.product_runtime.planning_commit_records()
                    if hasattr(agent.product_runtime, "planning_commit_records")
                    else ()
                )
                contract_context = _dataset_contract_context(
                    contract=contract,
                    committed_results=committed,
                    registered_assets=task_registered_assets,
                )
                subset = _task_capability_contract_subset(
                    task=task,
                    contract_catalog=contract_catalog,
                    availability=capability_availability_by_task[task_id],
                )
                invocation_bindings = build_paper_task_invocation_bindings(
                    run_id=run.run_id,
                    task=task,
                    dataset_id=str(workflow["dataset_id"]),
                    contract=contract,
                    registry=agent.product_runtime.registry,
                    asset_registry=registry,
                    project=project,
                    registered_assets=task_registered_assets,
                    committed_results=committed,
                    committed_records=committed_records,
                    advertised_capability_ids=tuple(
                        subset["advertised_capability_ids"]
                    ),
                    verify_runtime_dependencies=turn_executor is None,
                )
                if hasattr(agent.product_runtime, "replace_invocation_bindings"):
                    agent.product_runtime.replace_invocation_bindings(
                        task_id=task_id,
                        bindings=invocation_bindings,
                    )
                subset["invocation_bindings"] = provider_binding_contract(
                    invocation_bindings
                )
                subset_without_hash = {
                    key: value for key, value in subset.items() if key != "subset_hash"
                }
                subset["subset_hash"] = canonical_hash(subset_without_hash)
                subset_path = (
                    workspace.task_dir / "capability_contracts" / f"{task_id}.json"
                )
                workspace.write_json(subset_path, subset)
                contract_subset_record = {
                    "task_id": task_id,
                    "subset_hash": subset["subset_hash"],
                    "audited_codeact_fallback": bool(
                        subset["audited_codeact_fallback"]
                    ),
                    "candidate_capability_ids": list(
                        capability_availability_by_task[task_id][
                            "candidate_capability_ids"
                        ]
                    ),
                    "advertised_capability_ids": list(
                        subset["advertised_capability_ids"]
                    ),
                    "conditional_capability_ids": list(
                        subset["conditional_capability_ids"]
                    ),
                    "structurally_excluded_capabilities": list(
                        capability_availability_by_task[task_id][
                            "structurally_excluded_capabilities"
                        ]
                    ),
                    "path": subset_path.relative_to(workspace.root).as_posix(),
                    "invocation_bindings": list(subset["invocation_bindings"]),
                    "required_binding_ids": [
                        item.binding_id for item in invocation_bindings
                    ],
                }
                contract_subsets.append(contract_subset_record)
            prompt = _task_prompt(
                workflow=workflow,
                task=task,
                condition=condition,
                workspace_root=workspace.root,
                asset_paths=turn_asset_paths,
                anchors_by_id=anchors_by_id,
                dependency_contracts={
                    dependency: tasks_by_id[dependency].get("output_contract") or {}
                    for dependency in ancestor_ids
                },
                isolated_smoke=smoke_task_ids is not None,
                scientific_contract_context=scientific_contract_context,
                contract_context=contract_context,
                contract_subset_record=contract_subset_record,
            )
            timeout_seconds = int(task["resources"]["timeout_seconds"])
            timed_out = False
            provider_run_result = None
            started = time.monotonic()
            event_log = workspace.logs_dir / "events.jsonl"
            event_offset = event_log.stat().st_size if event_log.is_file() else 0
            try:
                provider_run_result = (turn_executor or _run_with_timeout)(
                    agent, prompt, timeout_seconds
                )
            except TimeoutError:
                timed_out = True
                if (
                    agent.turn_manager is not None
                    and agent.turn_manager.turn is not None
                ):
                    try:
                        asyncio.run(agent.cancel_turn(agent.turn_manager.turn.turn_id))
                    except Exception:
                        agent.product_runtime.close(graceful=False)
            wall_seconds = time.monotonic() - started
            skill_leakage_audit = _audit_baseline_skill_access(
                event_log,
                start_offset=event_offset,
                condition=condition,
            )
            reference_leakage_audit = _audit_reference_truth_access(
                event_log,
                start_offset=event_offset,
                allowed_provider_assets=tuple(task_asset_manifest["assets"]),
            )
            capability_treatment_uptake = _audit_capability_treatment_uptake(
                event_log,
                start_offset=event_offset,
                advertised_capability_ids=(
                    tuple(contract_subset_record["advertised_capability_ids"])
                    if contract_subset_record is not None
                    else ()
                ),
                invocation_bindings=(
                    tuple(contract_subset_record["invocation_bindings"])
                    if contract_subset_record is not None
                    else ()
                ),
            )
            if contract_subset_record is not None:
                contract_subset_record["actual_mcp_capability_calls"] = list(
                    capability_treatment_uptake["calls"]
                )
            resource_evidence = observe_runtime_resources(
                resource_evidence, started_monotonic=resource_started
            )
            turns = project.store.list_turns(conversation.conversation_id)
            final = project.store.get_turn_final(turns[-1].turn_id) if turns else None
            provider_status = (
                "timeout"
                if timed_out
                else str(getattr(provider_run_result, "status", "completed"))
            )
            provider_error = getattr(provider_run_result, "error", None)
            provider_manifest = getattr(agent, "manifest", None)
            provider_result_subtype = getattr(
                provider_manifest, "result_subtype", None
            ) or getattr(provider_run_result, "result_subtype", None)
            termination_reason = _provider_termination_reason(
                provider_status=provider_status,
                provider_error=provider_error,
                provider_result_subtype=provider_result_subtype,
                timed_out=timed_out,
            )
            provider_clean_termination = termination_reason == "completed"
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
                initial_result_sha256=initial_result_sha256,
            )
            submitted_artifact_assets = []
            submission_receipt, _ = validate_submission_receipt(
                task_output,
                task_id=task_id,
                dataset_id=str(workflow["dataset_id"]),
            )
            if (
                submission_receipt is not None
                and output_gates.get("provider_result_updated") is True
                and output_gates.get("required_artifact_paths") is True
                and benchmark_result is not None
            ):
                submitted_artifact_assets = _register_submitted_task_artifacts(
                    registry,
                    project=project,
                    run_id=run.run_id,
                    task=task,
                    task_output=task_output,
                    submission_receipt=submission_receipt,
                    observed_roles=tuple(benchmark_result.artifact_roles),
                    turn_id=final.turn_id if final is not None else None,
                    input_asset_ids=tuple(
                        sorted(
                            {
                                str(item["asset_id"])
                                for item in task_registered_assets.values()
                                if item.get("asset_id")
                            }
                        )
                    ),
                )
                for artifact_asset in submitted_artifact_assets:
                    registered_assets[artifact_asset.role] = {
                        "asset_id": artifact_asset.asset_id,
                        "path": str(
                            registry.resolve(
                                artifact_asset.asset_id,
                                expected_role=artifact_asset.role,
                            )
                        ),
                        "content_sha256": artifact_asset.content_sha256,
                        "kind": str(artifact_asset.kind),
                        "source_class": str(artifact_asset.source_class),
                    }
            dependency_gate_status = "evaluated"
            if smoke_task_ids is not None and task.get("depends_on_tasks"):
                output_gates["dependencies_present"] = True
                dependency_gate_status = "not_applicable_smoke"
            mutation_free = all(
                _existing_files_unchanged(
                    previous,
                    _tree_hashes(workspace.root / "outputs" / "tasks" / dependency),
                )
                for dependency, previous in existing_prior_hashes.items()
            )
            provider_scientific_completion = _provider_scientific_completion(
                output_gates
            )
            capability_process_checks = {
                "schema_version": "pertura-capability-process-checks-v1",
                "affects_task_status": False,
                "capability_first_bound_invocation": (
                    capability_treatment_uptake.get("capability_first_status")
                    in {"passed", "not_applicable"}
                ),
                "advertised_binding_coverage": (
                    capability_treatment_uptake.get("required_binding_coverage_status")
                    in {"passed", "not_applicable"}
                ),
                "capability_binding_call_validity": (
                    capability_treatment_uptake.get("model_binding_retry_status")
                    in {"passed", "not_applicable"}
                ),
            }
            hard_gates = {
                **output_gates,
                "provider_scientific_completion": provider_scientific_completion,
                "prior_task_outputs_immutable": mutation_free,
                "resource_evidence": (
                    _task_resource_gate(task, resource_evidence)
                    and _workflow_resource_gate(workflow_id, resource_evidence)
                ),
                "no_skill_leakage": skill_leakage_audit["status"] != "failed",
                "no_reference_leakage": (reference_leakage_audit["status"] != "failed"),
            }
            status = "passed" if all(hard_gates.values()) else "failed"
            validity_status, failure_class = _classify_task_validity(
                workflow_id=workflow_id,
                task_status=status,
                termination_reason=termination_reason,
                provider_error=provider_error,
                capability_binding_incident=bool(
                    capability_treatment_uptake.get("runner_binding_integration_errors")
                ),
                skill_leakage_audit=skill_leakage_audit,
                reference_leakage_audit=reference_leakage_audit,
                resource_evidence=resource_evidence,
            )
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
                "validity_status": validity_status,
                "failure_class": failure_class,
                "dependency_gate_status": dependency_gate_status,
                "hard_gates": hard_gates,
                "result_problem": result_problem,
                "evaluation_domain": evaluation_domain,
                "task_evaluation": evaluation,
                "scientific_evaluation": evaluation,
                "project_id": project.project.project_id,
                "analysis_run_id": run.run_id,
                "conversation_id": conversation.conversation_id,
                "turn_id": final.turn_id if final else None,
                "provider_status": provider_status,
                "provider_error": provider_error,
                "provider_result_subtype": provider_result_subtype,
                "provider_scientific_completion": provider_scientific_completion,
                "provider_clean_termination": provider_clean_termination,
                "termination_reason": termination_reason,
                "provider_turns": getattr(provider_manifest, "num_turns", None),
                "provider_message_count": getattr(
                    provider_manifest, "message_count", None
                ),
                "provider_cost_usd": getattr(provider_manifest, "total_cost_usd", None),
                "wall_seconds": wall_seconds,
                "resource_evidence": resource_evidence,
                "initial_benchmark_result_sha256": initial_result_sha256,
                "pertura_skills": (
                    list(task.get("pertura_skills") or ())
                    if condition == "pertura_full"
                    else []
                ),
                "skill_bundle_hash": (
                    skill_manifest["bundle_hash"]
                    if skill_manifest is not None
                    else None
                ),
                "requested_skills": (
                    list(task.get("pertura_skills") or ())
                    if condition == "pertura_full"
                    else []
                ),
                "skill_leakage_audit": skill_leakage_audit,
                "reference_leakage_audit": reference_leakage_audit,
                "capability_contract_subset": contract_subset_record,
                "capability_treatment_uptake": capability_treatment_uptake,
                "capability_process_checks": capability_process_checks,
                "submitted_artifact_assets": [
                    item.model_dump(mode="json") for item in submitted_artifact_assets
                ],
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
                    "validity_status": validity_status,
                    "failure_class": failure_class,
                    "evaluation_domain": evaluation_domain,
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
            task for task in workflow_turns if task["task_id"] == item["task_id"]
        ).get("role")
        != "optional"
    ]
    workflow_status = (
        "passed"
        if required_records
        and all(item["status"] == "passed" for item in required_records)
        else "failed"
    )
    skill_leakage_detected = any(
        (_load_json(Path(item["verdict"])).get("skill_leakage_audit") or {}).get(
            "status"
        )
        == "failed"
        for item in required_records
    )
    reference_leakage_detected = any(
        (_load_json(Path(item["verdict"])).get("reference_leakage_audit") or {}).get(
            "status"
        )
        == "failed"
        for item in required_records
    )
    invalid_infrastructure_detected = (
        skill_leakage_detected
        or reference_leakage_detected
        or any(
            item.get("validity_status") == "invalid_infrastructure"
            for item in required_records
        )
    )
    resource_evidence = observe_runtime_resources(
        resource_evidence, started_monotonic=resource_started
    )
    _write(execution_root / "resource_evidence.observed.json", resource_evidence)
    manifest_workflow = (
        workflow
        if condition == "pertura_full"
        else dict(
            workflow,
            turns=[
                {key: value for key, value in task.items() if key != "pertura_skills"}
                for task in workflow.get("turns") or ()
            ],
        )
    )
    input_manifest = {
        "schema_version": "pertura-paper-workflow-input-manifest-v1",
        "workflow": manifest_workflow,
        "smoke_task_ids": (
            list(smoke_task_ids) if smoke_task_ids is not None else None
        ),
        "condition": condition,
        "repeat_index": repeat_index,
        "model": model,
        "max_turns_per_task": runtime_options.max_turns,
        "capability_contract_subsets": (
            contract_subsets if condition == "pertura_full" else []
        ),
        "skill_bundle_hash": (
            skill_manifest["bundle_hash"] if skill_manifest is not None else None
        ),
        "task_skills": (
            {
                str(task["task_id"]): list(task.get("pertura_skills") or ())
                for task in workflow_turns
            }
            if condition == "pertura_full"
            else {}
        ),
        "provider_config_hash": provider_config_hash,
        "task_catalog_sha256": task_catalog.sha256,
        "task_reference_catalog_sha256": file_sha256(Path(task_reference_catalog_path)),
        "paper_anchor_catalog_sha256": file_sha256(Path(paper_anchor_catalog_path)),
        "design_confirmation_catalog_sha256": design_catalog_sha256,
        "partial_scientific_contract": _shared_scientific_contract_context(
            contract=paper_contract,
            task={},
        ),
        "asset_catalog_sha256": asset_catalog["_catalog_sha256"],
        "capability_contract_catalog_hash": contract_catalog["catalog_hash"],
        "capability_availability_hash": capability_availability["canonical_hash"],
        "capability_contract_catalog_sha256": (
            file_sha256(Path(capability_contract_catalog_path))
            if capability_contract_catalog_path is not None
            else None
        ),
        "checkpoint_binding": checkpoint,
        "resource_evidence": resource_evidence,
        "resource_evidence_sha256": (
            file_sha256(Path(resource_evidence_path))
            if resource_evidence_path is not None
            and Path(resource_evidence_path).is_file()
            else None
        ),
        "resource_observation_hash": canonical_hash(resource_evidence),
        "asset_hashes": {asset.role: asset.content_sha256 for asset in registered},
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
        "execution_status": (
            "invalid_infrastructure" if invalid_infrastructure_detected else "completed"
        ),
        "validity_status": (
            "invalid_infrastructure" if invalid_infrastructure_detected else "valid"
        ),
        "score_status": (
            "not_scored" if invalid_infrastructure_detected else workflow_status
        ),
        "status": (
            "invalid_infrastructure"
            if invalid_infrastructure_detected
            else workflow_status
        ),
        "skill_leakage_detected": skill_leakage_detected,
        "reference_leakage_detected": reference_leakage_detected,
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
        evaluation_domain = _task_evaluation_domain(task, references_by_id)
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
                    "validity_status": "valid",
                    "failure_class": "not_applicable",
                    "evaluation_domain": evaluation_domain,
                    "verdict": str(verdict_path),
                }
            )
            continue

        task_output = workspace_root / "outputs" / "tasks" / task_id
        current_output_hashes = _tree_hashes(task_output)
        current_ancestor_hashes = {
            dependency: _tree_hashes(workspace_root / "outputs" / "tasks" / dependency)
            for dependency in _ancestor_task_ids(task, tasks_by_id)
        }
        changed_after_turn = (
            verdict.get("post_turn_output_hashes") != current_output_hashes
            or verdict.get("post_turn_ancestor_hashes") != current_ancestor_hashes
        )
        if not changed_after_turn:
            records.append(
                {
                    "task_id": task_id,
                    "status": str(verdict.get("status") or "failed"),
                    "validity_status": str(verdict.get("validity_status") or "valid"),
                    "failure_class": str(
                        verdict.get("failure_class") or "scored_agent_failure"
                    ),
                    "evaluation_domain": evaluation_domain,
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
            initial_result_sha256=str(
                verdict.get("initial_benchmark_result_sha256") or ""
            ),
        )
        if verdict.get("dependency_gate_status") == "not_applicable_smoke":
            output_gates["dependencies_present"] = True
        hard_gates = dict(verdict.get("hard_gates") or {})
        original_result_updated = bool(hard_gates.get("provider_result_updated"))
        hard_gates.update(output_gates)
        provider_scientific_completion = _provider_scientific_completion(output_gates)
        hard_gates["provider_scientific_completion"] = provider_scientific_completion
        status = "passed" if all(hard_gates.values()) else "failed"
        validity_status = str(verdict.get("validity_status") or "valid")
        failure_class = str(verdict.get("failure_class") or "none")
        if validity_status == "valid":
            failure_class = "none" if status == "passed" else failure_class
        verdict.update(
            {
                "status": status,
                "validity_status": validity_status,
                "failure_class": failure_class,
                "hard_gates": hard_gates,
                "result_problem": result_problem,
                "evaluation_domain": evaluation_domain,
                "task_evaluation": evaluation,
                "scientific_evaluation": evaluation,
                "provider_scientific_completion": provider_scientific_completion,
                "post_workflow_regraded": True,
                "repaired_after_turn": (
                    not original_result_updated
                    and bool(output_gates.get("provider_result_updated"))
                ),
                "post_workflow_output_hashes": current_output_hashes,
                "post_workflow_ancestor_hashes": current_ancestor_hashes,
            }
        )
        if result_path.is_file():
            (task_root / "benchmark_result.json").write_bytes(result_path.read_bytes())
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
                "validity_status": validity_status,
                "failure_class": failure_class,
                "evaluation_domain": evaluation_domain,
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
        records.append({"task_id": task_id, "status": grade.get("status", "failed")})
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
    raise FileNotFoundError(f"python-science-v1 interpreter is missing under {prefix}")


def _neutral_benchmark_result(
    *,
    workflow: Mapping[str, Any],
    task: Mapping[str, Any],
) -> dict[str, Any]:
    """Return the fail-closed checkpoint written before provider execution."""

    result_type = f"{str(task['task_id']).lower()}_scientific_result"
    return {
        "schema_version": "pertura-agent-benchmark-result-v1",
        "case_id": str(task["task_id"]),
        "dataset_id": str(workflow["dataset_id"]),
        "result_type": result_type,
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


def _paper_dataset_contract(
    *,
    dataset_id: str,
    template: Mapping[str, Any],
    registered_assets: Mapping[str, Mapping[str, Any]],
) -> DatasetContract:
    """Bind a curator-frozen partial contract to current registered assets."""

    def bind_matrix(raw: Any) -> dict[str, Any]:
        record = dict(raw or {})
        role = str(record.get("asset_role") or "")
        binding = registered_assets.get(role)
        if binding is not None:
            record["asset_binding"] = {
                "asset_id": binding["asset_id"],
                "path": binding["path"],
                "content_sha256": binding["content_sha256"],
            }
        return record

    availability = dict(template.get("asset_availability") or {})
    bound_assets = {
        role: {
            "asset_id": registered_assets[role]["asset_id"],
            "path": registered_assets[role]["path"],
            "content_sha256": registered_assets[role]["content_sha256"],
        }
        for role in sorted(availability)
        if role in registered_assets
    }
    return DatasetContract(
        dataset_id=dataset_id,
        source_paths=tuple(
            sorted({str(item["path"]) for item in bound_assets.values()})
        ),
        input_format=str(template["input_format"]),
        expression_matrix=bind_matrix(template.get("expression_matrix")),
        guide_matrix=bind_matrix(template.get("guide_matrix")),
        identity_fields={
            str(name): dict(value)
            for name, value in (template.get("identity_fields") or {}).items()
        },
        unresolved_fields=tuple(
            sorted(str(item) for item in template.get("unresolved_fields") or ())
        ),
        metadata={
            "paper_partial_contract": True,
            "asset_availability": availability,
            "asset_bindings": bound_assets,
            "provenance": dict(template.get("provenance") or {}),
        },
    )


def _shared_scientific_contract_context(
    *, contract: DatasetContract, task: Mapping[str, Any]
) -> dict[str, Any]:
    """Return answer-free scientific facts shared by all three conditions."""

    return {
        "dataset_id": contract.dataset_id,
        "expression_matrix": {
            key: value
            for key, value in contract.expression_matrix.items()
            if key != "asset_binding"
        },
        "guide_matrix": {
            key: value
            for key, value in contract.guide_matrix.items()
            if key != "asset_binding"
        },
        "design_facts": {
            name: {
                key: value for key, value in fact.items() if key in {"status", "value"}
            }
            for name, fact in sorted(contract.identity_fields.items())
        },
        "unresolved_facts": list(contract.unresolved_fields),
        "asset_availability": dict(contract.metadata.get("asset_availability") or {}),
        "task_design_protocol": dict(task.get("codeact_protocol") or {}),
    }


def _task_asset_manifest(
    *,
    workflow_id: str,
    dataset_id: str,
    condition: str,
    roles: tuple[str, ...],
    asset_paths: Mapping[str, str],
    registered_assets: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Render only the current task's legal provider-visible asset surface."""

    records: list[dict[str, Any]] = []
    for role in roles:
        raw_path = asset_paths.get(role)
        if raw_path is None:
            continue
        path = Path(raw_path)
        record: dict[str, Any] = {"role": role, "path": str(path)}
        registered = registered_assets.get(role)
        if registered is not None:
            record["content_sha256"] = registered["content_sha256"]
            if condition == "pertura_full":
                record["asset_id"] = registered["asset_id"]
        elif path.is_file():
            record["content_sha256"] = file_sha256(path)
        records.append(record)
    return {
        "schema_version": "pertura-paper-agent-task-assets-v1",
        "workflow_id": workflow_id,
        "dataset_id": dataset_id,
        "condition": condition,
        "assets": records,
    }


def _dataset_contract_context(
    *,
    contract: Any,
    committed_results: tuple[Any, ...],
    registered_assets: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    identity_fields = dict(contract.identity_fields)
    confirmed = {
        name: value
        for name, value in identity_fields.items()
        if str(value.get("status") or "").lower() == "confirmed"
    }
    conflicting = {
        name: value
        for name, value in identity_fields.items()
        if str(value.get("status") or "").lower() in {"conflict", "conflicting"}
    }
    return {
        "contract_id": contract.contract_id,
        "contract_hash": contract.canonical_hash,
        "contract_version": contract.contract_version,
        "expression_matrix": dict(contract.expression_matrix),
        "guide_matrix": dict(contract.guide_matrix),
        "confirmed_design_facts": confirmed,
        "conflicting_design_facts": conflicting,
        "unresolved_design_facts": list(contract.unresolved_fields),
        "asset_availability": dict(contract.metadata.get("asset_availability") or {}),
        "provenance": dict(contract.metadata.get("provenance") or {}),
        "registered_assets": {
            role: {
                "asset_id": record["asset_id"],
                "content_sha256": record["content_sha256"],
                "path": record["path"],
            }
            for role, record in sorted(registered_assets.items())
        },
        "committed_results": [
            {
                "result_id": result.result_id,
                "capability_id": result.capability_id,
                "status": str(result.status),
                "result_kind": result.result_kind,
                "source_class": str(result.source_class),
                "stale": bool(result.stale),
                "output_paths": list(result.output_paths),
            }
            for result in committed_results
        ],
    }


def _task_capability_contract_subset(
    *,
    task: Mapping[str, Any],
    contract_catalog: Mapping[str, Any],
    availability: Mapping[str, Any],
) -> dict[str, Any]:
    advertised = [
        str(item) for item in availability.get("advertised_capability_ids") or ()
    ]
    catalog = {
        str(item["capability_id"]): dict(item)
        for item in contract_catalog.get("capabilities") or ()
    }
    missing = [
        capability_id for capability_id in advertised if capability_id not in catalog
    ]
    if missing:
        raise ValueError(
            f"{task['task_id']}: capability contract subset is unbound: {missing}"
        )
    payload = {
        "schema_version": "pertura-paper-capability-contract-subset-v2",
        "task_id": str(task["task_id"]),
        "catalog_hash": str(contract_catalog["catalog_hash"]),
        "audited_codeact_fallback": bool(availability.get("audited_codeact_fallback")),
        # This is the provider-visible candidate surface. Excluded IDs and
        # reasons remain only in the checkpoint/verdict audit record.
        "candidate_capability_ids": advertised,
        "advertised_capability_ids": advertised,
        "conditional_capability_ids": [
            str(item) for item in availability.get("conditional_capability_ids") or ()
        ],
        "structurally_excluded_capabilities": [],
        "capabilities": [catalog[capability_id] for capability_id in advertised],
    }
    return payload | {"subset_hash": canonical_hash(payload)}


def _task_prompt(
    *,
    workflow: Mapping[str, Any],
    task: Mapping[str, Any],
    condition: str,
    workspace_root: Path,
    asset_paths: Mapping[str, str],
    anchors_by_id: Mapping[str, Mapping[str, Any]],
    dependency_contracts: Mapping[str, Mapping[str, Any]],
    isolated_smoke: bool = False,
    scientific_contract_context: Mapping[str, Any] | None = None,
    contract_context: Mapping[str, Any] | None = None,
    contract_subset_record: Mapping[str, Any] | None = None,
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
        anchors_by_id[anchor_id] for anchor_id in task.get("paper_anchor_ids") or ()
    ]
    surface = (
        "Use only the answer-free capability contracts advertised for this "
        "task, together with the frozen CodeAct method contract."
        if condition == "pertura_full"
        else "Use the available generic CodeAct tools under this benchmark condition."
    )
    task_output_root = (
        Path(workspace_root).expanduser().resolve()
        / "outputs"
        / "tasks"
        / str(task["task_id"])
    ).as_posix()
    allowed_analysis_units = tuple(
        str(item)
        for item in (
            (task.get("output_contract") or {}).get("allowed_analysis_units") or ()
        )
    )
    analysis_unit_policy = (
        "For benchmark_result.analysis_unit, use exactly one value from the "
        f"task-scoped controlled vocabulary {json.dumps(allowed_analysis_units)}. "
        "This vocabulary constrains the output field; it is not an evaluation "
        "answer or scientific result. "
        if allowed_analysis_units
        else ""
    )
    artifact_destinations = {
        str(role): f"{task_output_root}/{relative}"
        for role, relative in (
            (task.get("output_contract") or {}).get("artifact_paths") or {}
        ).items()
    }
    if isolated_smoke:
        dependency_payload: Mapping[str, Mapping[str, Any]] = {}
        repair_policy = (
            "This is an isolated non-formal smoke. Upstream task outputs are "
            "deliberately absent: do not recreate, repair, or inspect them. "
            "Use only the registered inputs for the current task. Do not "
            "install packages or inspect unrelated environments. The missing "
            "dependency gate is expected and is not the smoke objective. "
        )
    else:
        dependency_payload = dependency_contracts
        repair_policy = (
            "You may repair a missing upstream artifact by writing its "
            "previously missing file, but must not overwrite an existing "
            "prior-turn artifact. "
        )
    interpretation_policy = (
        "This is an evidence-interpretation task: do not recompute or refit "
        "the frozen evidence. Read the registered evidence and protocol, then "
        "write only the required current-task outputs. "
        if task.get("execution_mode") == "evidence_interpretation"
        else ""
    )
    codeact_protocol = task.get("codeact_protocol") or {}
    environment_variable = codeact_protocol.get("environment_variable")
    environment_entrypoint = codeact_protocol.get("entrypoint")
    locked_entrypoint = (
        f"${environment_variable}/bin/{environment_entrypoint}"
        if environment_variable and environment_entrypoint
        else str(environment_variable or "")
    )
    environment_policy = (
        "Use only the frozen scientific environment declared by the CodeAct "
        f"protocol ({codeact_protocol.get('environment_profile')}); invoke its "
        f"declared entrypoint as {locked_entrypoint}. Do not install "
        "scientific packages, load an alternative module or runtime, or rewrite "
        "a packaged method template when a bound skill supplies one. "
        if codeact_protocol.get("environment_profile")
        else ""
    )
    contract_policy = ""
    if condition == "pertura_full":
        advertised_capability_ids = [
            str(capability_id)
            for capability_id in (
                (contract_subset_record or {}).get("advertised_capability_ids") or ()
            )
        ]
        invocation_bindings = list(
            (contract_subset_record or {}).get("invocation_bindings") or ()
        )
        if task.get("execution_mode") == "capability_or_codeact":
            if advertised_capability_ids:
                capability_fallback_policy = (
                    "The advertised capabilities are optional treatment routes, not "
                    "task-completion requirements. You may invoke an advertised "
                    "capability or proceed directly with the audited CodeAct fallback; "
                    "the runner records uptake without changing task pass/fail. If you "
                    "invoke a capability, attempt it at most once. If it reports a genuine "
                    "scientific applicability or evidence blocker such as absent "
                    "observed data, unresolved design identity, incompatible scope, "
                    "or missing independent replicates, preserve that block and do "
                    "not bypass it. If the advertised capability instead cannot be "
                    "invoked because of an integration or access boundary, including "
                    "an unavailable verified ancestor capability result, stop retrying and "
                    "use the audited CodeAct fallback under the frozen task contract. "
                    "A CodeAct fallback may produce evaluator-scored files, but it "
                    "does not produce a capability receipt or measured authority and "
                    "must not be described as capability execution. "
                )
            else:
                capability_fallback_policy = (
                    "The static compiler advertised no executable capability for "
                    "this endpoint. Do not call an unadvertised capability or inspect "
                    "source code or YAML to find one. Proceed directly with the "
                    "audited CodeAct fallback under the frozen task contract. This "
                    "fallback may produce evaluator-scored files, but it does not "
                    "produce a capability receipt or measured authority and must not "
                    "be described as capability execution. "
                )
        else:
            capability_fallback_policy = ""
        qualified_task_skills = [
            skill if ":" in str(skill) else f"pertura:{skill}"
            for skill in (task.get("pertura_skills") or ())
        ]
        explicit_nonexecutions = [
            str(capability_id)
            for capability_id in (task.get("expected_nonexecutions") or ())
        ]
        nonexecution_policy = (
            "The following capability IDs are explicit nonexecutions for this "
            "endpoint because their required evidence or scored output is outside "
            f"the frozen task contract: {json.dumps(explicit_nonexecutions)}. "
            "Do not invoke them or recreate their methods with CodeAct. "
            if explicit_nonexecutions
            else ""
        )
        contract_policy = (
            "Pertura already registered the curator-frozen partial DatasetContract; "
            "do not call inspect_dataset again. The contract and committed-result "
            f"summary are {json.dumps(contract_context or {}, sort_keys=True)}. "
            "The answer-free static capability contracts for this task are at "
            f"{(contract_subset_record or {}).get('path', '')}; their bound IDs are "
            f"{json.dumps((contract_subset_record or {}).get('advertised_capability_ids', []))}. "
            f"{nonexecution_policy}"
            f"{capability_fallback_policy}"
            "The runner-prevalidated capability invocation bindings are "
            f"{json.dumps(invocation_bindings, sort_keys=True)}. "
            "Binding uptake is non-dispositive and is reported separately under an "
            "intention-to-treat analysis; you are not required to invoke the first or "
            "every listed binding before scientific work or task submission. When you "
            "choose to invoke a bound capability, use its listed minimal_call exactly "
            "and inspect the returned completed, caution, structured blocked, or "
            "integration-boundary outcome before deciding what to do next. "
            "Do not provide capability_id, contract_id, scope, dependencies, file "
            "paths, asset IDs, or parameters unless they appear under that "
            "binding's allowed_overrides. "
            "Do not hand-compose a capability call when a binding exists. The "
            "exact SDK Skill tool names frozen for this task are "
            f"{json.dumps(qualified_task_skills)}. Before any task-scientific Read, "
            "Glob, Grep, Bash, Notebook, or scientific MCP call, invoke each of "
            "those exact names once with the Skill tool, in the listed order. "
            "Treat the loaded instructions as the task method contract. Do not "
            "repeat a successful Skill invocation, substitute an unbound skill, "
            "or proceed by rediscovering the method when a bound invocation fails. "
            "Do not read repository source, capability YAML, tests, or scientific-"
            "environment directories to rediscover contracts. Do not probe rpy2, "
            "alternative package managers, or unrelated environments. "
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
        f"{json.dumps(dependency_payload, sort_keys=True)}. "
        f"Paper anchors (framing only, never measurements): "
        f"{json.dumps(anchors, sort_keys=True)}. "
        "Frozen partial scientific contract facts shared by every condition: "
        f"{json.dumps(scientific_contract_context or {}, sort_keys=True)}. "
        f"Required artifact roles: {json.dumps(task['required_artifact_roles'])}. "
        f"Output contract: {json.dumps(task['output_contract'], sort_keys=True)}. "
        "Every artifact_path in that contract is relative to the canonical task "
        f"output root. Canonical task output root (absolute): {task_output_root}. "
        "Write the required artifacts at these exact absolute destinations: "
        f"{json.dumps(artifact_destinations, sort_keys=True)}. "
        "Do not reinterpret that root relative to the repository or project root, "
        "and do not write to a similarly named project-level outputs/tasks path. "
        f"Hard gates: {json.dumps(task['task_hard_gates'])}. "
        f"Claim ceiling: {task['claim_ceiling']}. "
        "The following CodeAct protocol is a precommitted curator/user-confirmed "
        "design contract shared by all benchmark conditions; it is not an answer "
        f"or evaluation truth: {json.dumps(codeact_protocol, sort_keys=True)}. "
        f"{environment_policy}"
        f"{repair_policy}"
        f"{interpretation_policy}"
        f"{contract_policy}"
        f"The runner initialized {task_output_root}/benchmark_result.json "
        "with a schema-valid neutral blocked checkpoint. Before completing, update "
        "that standalone pertura-agent-benchmark-result-v1 JSON file from actual "
        "execution evidence. Leaving it unchanged is a scored task failure. "
        "The result file permits only these top-level fields: schema_version, "
        "case_id, dataset_id, result_type, analysis_unit, status, findings, "
        "metrics, limitations, artifact_roles. artifact_roles must be a JSON "
        "array of role-name strings. Each finding permits only finding_id, "
        "text, metric_ids, and artifact_roles. Do not put hypotheses, "
        "questions_for_user, next_steps, artifact_refs, declared_role, "
        "result_ids, or finding-level limitations in benchmark_result.json. "
        f"{analysis_unit_policy}"
        "The metrics object is a self-reported index only and cannot replace "
        "the required scientific artifacts. "
        f"Submit the benchmark result and separate pertura-turn-draft-v1 object "
        f"together with {SUBMISSION_ALLOWED_TOOL}. Direct Write/Edit of "
        "benchmark_result.json is not an accepted submission. The tool validates "
        "scalar-only metrics, artifact role strings, finding fields, and "
        "task/dataset identity before atomically writing the canonical files and "
        "receipt. The TurnDraft permits only schema_version, language, headline, "
        "findings, hypotheses, limitations, questions_for_user, next_steps, and "
        "artifact_refs; headline is required. Each TurnDraft finding permits only "
        "finding_id, text, declared_role, result_ids, and limitations, where "
        "declared_role is measured, derived, prediction, prior, contradiction, "
        "or hypothesis. Do not put turn_index, case_id, dataset_id, role, summary, "
        "artifacts, or hard_gates in the TurnDraft. If rejected, read the returned "
        "accepted=false validation errors, correct the reported fields, and "
        "resubmit. Only accepted=true with a non-null submission_id is acceptance. "
        "After acceptance, return no additional scientific prose. "
        "Never copy the TurnDraft object into benchmark_result.json. Independent "
        "evaluators, not "
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
    for paper_role, capability_role in PAPER_CAPABILITY_ASSET_ALIASES.items():
        if paper_role not in paths or capability_role in paths:
            continue
        source = next(item for item in registered if item.role == paper_role)
        path = Path(paths[paper_role])
        alias = registry.register(
            path,
            role=capability_role,
            kind=source.kind,
            source_class=source.source_class,
        )
        if alias.content_sha256 != source.content_sha256:
            raise ValueError(
                f"paper capability asset alias checksum mismatch: {capability_role}"
            )
        project.store.put_asset_binding(
            AssetBinding(run_id=run_id, asset_id=alias.asset_id, role=alias.role)
        )
        registered.append(alias)
        paths[capability_role] = str(path)
    if len(paths) != len(registered):
        raise ValueError("paper agent asset roles must be unique")
    return registered, paths


def _primary_asset_path(asset_paths: Mapping[str, str]) -> Path:
    for role in ("evaluation_split", "primary_h5ad"):
        if role in asset_paths:
            return Path(asset_paths[role]).resolve()
    raise FileNotFoundError("paper workflow requires evaluation_split or primary_h5ad")


def _optional_configured(
    task: Mapping[str, Any], asset_paths: Mapping[str, str]
) -> bool:
    if task.get("configuration_gate") == "prediction_manifest_present":
        return "prediction_manifest_optional" in asset_paths
    return True


def _register_submitted_task_artifacts(
    registry: DataAssetRegistry,
    *,
    project: ProjectWorkspace,
    run_id: str,
    task: Mapping[str, Any],
    task_output: Path,
    submission_receipt: Mapping[str, Any],
    observed_roles: tuple[str, ...],
    turn_id: str | None,
    input_asset_ids: tuple[str, ...],
) -> list[Any]:
    """Register accepted task artifacts as provenance-only data assets."""

    output_contract = dict(task.get("output_contract") or {})
    artifact_paths = dict(output_contract.get("artifact_paths") or {})
    declared = set(observed_roles)
    registered = []
    for role, relative in sorted(artifact_paths.items()):
        role = str(role)
        if role not in declared:
            continue
        path = (task_output / str(relative)).resolve()
        try:
            path.relative_to(task_output.resolve())
        except ValueError as exc:
            raise ValueError(f"submitted artifact escapes task output: {role}") from exc
        if not path.exists():
            continue
        dependencies = tuple(
            dict.fromkeys(
                (
                    *input_asset_ids,
                    f"submission:{submission_receipt['submission_id']}",
                )
            )
        )
        asset = registry.register(
            path,
            role=role,
            kind="derived",
            source_class="derived_artifact",
            created_by_turn=turn_id,
            dependencies=dependencies,
            origin_task_id=str(task["task_id"]),
            submission_id=str(submission_receipt["submission_id"]),
            schema_validation_status="validated",
        )
        project.store.put_asset_binding(
            AssetBinding(run_id=run_id, asset_id=asset.asset_id, role=role)
        )
        registered.append(asset)
    return registered


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
                workspace_root / "outputs" / "tasks" / str(dependency) / str(relative)
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
    evaluation_domain: str,
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
        "validity_status": "valid",
        "failure_class": "not_applicable",
        "evaluation_domain": evaluation_domain,
        "reason": "optional prediction manifest is absent",
        "hard_gates": {},
    }
    _write(task_root / "verdict.json", verdict)
    return {
        "task_id": task["task_id"],
        "status": "not_configured",
        "validity_status": "valid",
        "failure_class": "not_applicable",
        "evaluation_domain": evaluation_domain,
        "verdict": str(task_root / "verdict.json"),
    }


def _task_evaluation_domain(
    task: Mapping[str, Any],
    references_by_id: Mapping[str, Mapping[str, Any]],
) -> str:
    domains = {
        str(references_by_id[reference_id].get("evaluation_domain") or "")
        for reference_id in task.get("task_reference_ids") or ()
        if reference_id in references_by_id
    }
    if len(domains) != 1 or "" in domains:
        raise ValueError(f"{task.get('task_id')}: evaluation domain is not bound")
    return domains.pop()


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
    return result, None


def _load_submitted_turn_draft(
    path: Path,
) -> tuple[TurnDraft | None, str | None]:
    if not path.is_file():
        return None, "submitted TurnDraft is missing"
    try:
        return TurnDraft.model_validate_json(path.read_text(encoding="utf-8")), None
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return None, f"submitted TurnDraft is invalid: {exc}"


def _provider_scientific_completion(output_gates: Mapping[str, bool]) -> bool:
    """Return whether the provider crossed the typed scientific boundary."""

    return all(
        bool(output_gates.get(name))
        for name in (
            "typed_submission",
            "provider_result_updated",
            "benchmark_result_schema_valid",
            "turn_output_schema_valid",
        )
    )


def _provider_termination_reason(
    *,
    provider_status: str,
    provider_error: Any,
    provider_result_subtype: Any,
    timed_out: bool,
) -> str:
    """Normalize provider termination without changing scientific completion."""

    if timed_out:
        return "task_timeout"
    detail = " ".join(
        str(item or "").lower()
        for item in (provider_result_subtype, provider_error, provider_status)
    )
    if "max_turn" in detail or "max turns" in detail:
        return "max_turns"
    if provider_status == "completed":
        return "completed"
    return "provider_error"


def _classify_task_validity(
    *,
    workflow_id: str,
    task_status: str,
    termination_reason: str,
    provider_error: Any,
    capability_binding_incident: bool = False,
    skill_leakage_audit: Mapping[str, Any],
    resource_evidence: Mapping[str, Any],
    reference_leakage_audit: Mapping[str, Any] | None = None,
) -> tuple[str, str]:
    """Separate benchmark validity from a scored agent/task failure."""

    if (
        skill_leakage_audit.get("status") == "failed"
        or (reference_leakage_audit or {}).get("status") == "failed"
    ):
        return "invalid_infrastructure", "invalid_infrastructure"
    if capability_binding_incident:
        return "invalid_infrastructure", "invalid_infrastructure"
    if not _workflow_resource_gate(workflow_id, resource_evidence):
        return "invalid_infrastructure", "invalid_infrastructure"

    scheduler_state = str(resource_evidence.get("scheduler_state") or "").lower()
    if scheduler_state in {
        "preempted",
        "node_fail",
        "node_failure",
        "boot_fail",
    }:
        return "invalid_infrastructure", "invalid_infrastructure"

    error = str(provider_error or "").lower()
    infrastructure_markers = (
        "authenticationerror",
        "authentication failed",
        "invalid api key",
        "unauthorized",
        "sdk init",
        "sdk did not report the initialized",
        "python environment preflight failed",
        "environment is missing",
        "environment is corrupt",
        "connection reset",
        "network unavailable",
        "api outage",
        "service unavailable",
    )
    if any(marker in error for marker in infrastructure_markers):
        return "invalid_infrastructure", "invalid_infrastructure"

    oom_detected = (
        float(resource_evidence.get("oom_kill_events") or 0) > 0
        or scheduler_state in {"out_of_memory", "oom"}
        or "out of memory" in error
        or "memoryerror" in error
        or "oom_kill" in error
    )
    if oom_detected:
        return "valid", "scored_resource_failure"
    if task_status == "passed":
        return "valid", "none"
    if termination_reason == "task_timeout":
        return "valid", "scored_timeout"
    return "valid", "scored_agent_failure"


def _evaluate_task_outputs(
    task: Mapping[str, Any],
    *,
    workspace_root: Path,
    dataset_id: str,
    paper_root: Path,
    asset_paths: Mapping[str, str],
    references_by_id: Mapping[str, Mapping[str, Any]],
    tasks_by_id: Mapping[str, Mapping[str, Any]],
    initial_result_sha256: str,
) -> tuple[AgentBenchmarkResult | None, str | None, dict[str, Any], dict[str, bool],]:
    task_id = str(task["task_id"])
    task_output = workspace_root / "outputs" / "tasks" / task_id
    result_path = task_output / "benchmark_result.json"
    benchmark_result, result_problem = _load_task_result(
        result_path,
        task_id=task_id,
        dataset_id=dataset_id,
    )
    provider_result_updated = bool(
        result_path.is_file()
        and initial_result_sha256
        and file_sha256(result_path) != initial_result_sha256
    )
    submission_receipt, submission_problem = validate_submission_receipt(
        task_output,
        task_id=task_id,
        dataset_id=dataset_id,
    )
    submitted_turn_draft, turn_draft_problem = _load_submitted_turn_draft(
        task_output / "submitted_turn_draft.json"
    )
    provider_result_updated = bool(provider_result_updated and submission_receipt)
    if submission_problem is not None and result_problem is None:
        result_problem = submission_problem
    elif turn_draft_problem is not None and result_problem is None:
        result_problem = turn_draft_problem
    elif not provider_result_updated and result_problem is None:
        result_problem = "provider did not update runner-initialized benchmark result"
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
    observed_roles = set(benchmark_result.artifact_roles if benchmark_result else ())
    gates = {
        "typed_submission": submission_receipt is not None,
        "provider_result_updated": provider_result_updated,
        "output_contract_present": result_path.is_file(),
        "benchmark_result_schema_valid": benchmark_result is not None,
        "benchmark_result_status_eligible": bool(
            benchmark_result is not None and benchmark_result.status != "failed"
        ),
        "turn_output_schema_valid": submitted_turn_draft is not None,
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
            and set(task.get("required_input_roles") or ()).issubset(resolved_inputs)
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
        output_root = workspace_root / "outputs" / "tasks" / str(dependency_id)
        result, _ = _load_task_result(
            output_root / "benchmark_result.json",
            task_id=str(dependency_id),
            dataset_id=dataset_id,
        )
        if result is None:
            return False
        required_roles = set(dependency.get("required_artifact_roles") or ())
        if not required_roles.issubset(result.artifact_roles):
            return False
        if not _artifact_paths_present(
            output_root, dependency.get("output_contract") or {}
        ):
            return False
    return True


def _task_resource_gate(task: Mapping[str, Any], evidence: Mapping[str, Any]) -> bool:
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


def _workflow_resource_gate(workflow_id: str, evidence: Mapping[str, Any]) -> bool:
    """Require the checkpoint-frozen workflow allocation for every condition."""

    expected_memory = PAPER_WORKFLOW_MEMORY_GB.get(workflow_id)
    if expected_memory is None:
        return False
    try:
        requested_memory = float(evidence.get("requested_memory_gb", 0))
        actual_memory = float(evidence.get("actual_memory_gb", 0))
        cpu_count = int(evidence.get("cpu_count", 0))
        n_jobs = int(evidence.get("n_jobs", 0))
    except (TypeError, ValueError):
        return False
    threads = evidence.get("thread_environment") or {}
    controlled_threads = (
        all(str(value) == "1" for value in threads.values())
        if isinstance(threads, Mapping) and threads
        else False
    )
    return bool(
        requested_memory == expected_memory
        and actual_memory >= expected_memory
        and cpu_count == 1
        and n_jobs == 1
        and controlled_threads
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
        if path == base or base not in path.parents or not path.exists():
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
    capability_contract_catalog_path: Path,
    capability_availability_hash: str,
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
        "capability_contract_catalog_hash": Path(capability_contract_catalog_path),
    }
    for field, path in catalogs.items():
        if not path.is_file() or file_sha256(path) != binding[field]:
            raise ValueError(f"paper checkpoint catalog drift: {field}")
    expected_job_id = f"paper-agent:{workflow_id}:{condition}:repeat-{repeat_index}"
    matching = [job for job in plan.jobs if job.get("job_id") == expected_job_id]
    if len(matching) != 1:
        raise ValueError(f"bound plan lacks paper workflow job: {expected_job_id}")
    if matching[0].get("capability_availability_hash") != capability_availability_hash:
        raise ValueError("paper checkpoint capability availability drift")
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_root,
        text=True,
        capture_output=True,
        check=False,
    )
    if (
        completed.returncode
        or completed.stdout.strip().lower() != binding["git_commit"]
    ):
        raise ValueError("paper checkpoint checkout commit drift")
    wheel_value = os.environ.get("PERTURA_BENCH_WHEEL")
    if not wheel_value:
        raise FileNotFoundError("PERTURA_BENCH_WHEEL is not set")
    wheel = Path(wheel_value).expanduser().resolve()
    if not wheel.is_file() or file_sha256(wheel) != binding["wheel_sha256"]:
        raise ValueError("paper checkpoint wheel drift")

    qualification_path = plan_path.parent / (
        "capability-binding-qualification.a19.json"
    )
    _verify_capability_binding_qualification(
        qualification_path, checkpoint_binding=binding
    )
    return binding


def _verify_capability_binding_qualification(
    qualification_path: Path,
    *,
    checkpoint_binding: Mapping[str, str],
) -> dict[str, Any]:
    if not qualification_path.is_file():
        raise FileNotFoundError(
            "capability binding qualification is missing from the checkpoint: "
            f"{qualification_path}"
        )
    qualification = json.loads(qualification_path.read_text(encoding="utf-8"))
    if not isinstance(qualification, dict):
        raise ValueError("capability binding qualification must be a JSON object")
    qualification_without_hash = dict(qualification)
    observed_qualification_hash = qualification_without_hash.pop("canonical_hash", None)
    qualification_records = qualification.get("records")
    qualified_binding_count = qualification.get("qualified_binding_count")
    if (
        qualification.get("schema_version")
        != "pertura-capability-binding-qualification-v1"
        or qualification.get("passed") is not True
        or qualification.get("status") != "passed"
        or qualification.get("provider_schema_parity_passed") is not True
        or qualification.get("provider_result_visibility_passed") is not True
        or set((qualification.get("provider_tool_schema_hashes") or {}).keys())
        != {
            "inspect_dataset",
            "run_diagnostic",
            "run_analysis",
            "evaluate_virtual_model",
            "finalize_report",
        }
        or not isinstance(qualified_binding_count, int)
        or qualified_binding_count <= 0
        or not isinstance(qualification_records, list)
        or len(qualification_records) != qualified_binding_count
        or len(
            {
                str(item.get("binding_id") or "")
                for item in qualification_records
                if isinstance(item, Mapping)
            }
        )
        != qualified_binding_count
        or any(
            not isinstance(item, Mapping)
            or item.get("qualification_status")
            not in CAPABILITY_BINDING_QUALIFICATION_STATUSES
            or item.get("provider_schema_validation_status") != "passed"
            or item.get("provider_result_visibility_status") != "passed"
            for item in qualification_records
        )
        or observed_qualification_hash != canonical_hash(qualification_without_hash)
    ):
        raise ValueError("capability binding qualification is not valid")
    qualification_bindings = {
        "git_commit": checkpoint_binding["git_commit"],
        "wheel_sha256": checkpoint_binding["wheel_sha256"],
        "task_catalog_sha256": checkpoint_binding["paper_task_catalog_hash"],
        "task_reference_catalog_sha256": checkpoint_binding[
            "paper_task_reference_catalog_hash"
        ],
        "paper_asset_catalog_sha256": checkpoint_binding["paper_asset_catalog_hash"],
        "capability_contract_catalog_sha256": checkpoint_binding[
            "capability_contract_catalog_hash"
        ],
    }
    for field, expected in qualification_bindings.items():
        if str(qualification.get(field)) != expected:
            raise ValueError(f"capability binding qualification drift: {field}")
    return qualification


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


def _audit_capability_treatment_uptake(
    event_log: Path,
    *,
    start_offset: int,
    advertised_capability_ids: tuple[str, ...],
    invocation_bindings: tuple[Mapping[str, Any], ...] = (),
) -> dict[str, Any]:
    """Record binding uptake and enforce the frozen capability-first boundary."""

    calls: list[dict[str, Any]] = []
    required_binding_ids = [str(item["binding_id"]) for item in invocation_bindings]
    capability_by_binding = {
        str(item["binding_id"]): str(item["capability_id"])
        for item in invocation_bindings
    }
    tool_by_binding = {
        str(item["binding_id"]): str(item["tool"]) for item in invocation_bindings
    }
    allowed_overrides_by_binding = {
        str(item["binding_id"]): {
            str(name) for name in item.get("allowed_overrides") or ()
        }
        for item in invocation_bindings
    }
    minimal_arguments_by_binding = {
        str(item["binding_id"]): dict(
            ((item.get("minimal_call") or {}).get("arguments") or {})
        )
        for item in invocation_bindings
    }
    observed_binding_ids: list[str] = []
    bound_tool_use_ids: dict[str, dict[str, Any]] = {}
    binding_errors: list[dict[str, Any]] = []
    first_boundary_violation: dict[str, Any] | None = None
    boundary_release: dict[str, Any] | None = None
    if event_log.is_file():
        with event_log.open("rb") as handle:
            handle.seek(max(0, start_offset))
            text = handle.read().decode("utf-8", errors="replace")
        for line_number, line in enumerate(text.splitlines(), start=1):
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            content = (event.get("payload") or {}).get("content") or []
            if event.get("message_type") == "UserMessage":
                for item in content if isinstance(content, list) else [content]:
                    serialized = str(item)
                    result_id = re.search(
                        r"tool_use_id=['\"]([^'\"]+)['\"]", serialized
                    )
                    if not result_id or result_id.group(1) not in bound_tool_use_ids:
                        continue
                    call = bound_tool_use_ids[result_id.group(1)]
                    is_error = re.search(
                        r"is_error=(True|true)", serialized
                    ) or re.search(r"['\"]is_error['\"]\s*:\s*(true|True)", serialized)
                    if call.get("exact_minimal_call") and boundary_release is None:
                        boundary_release = {
                            "binding_id": call.get("binding_id"),
                            "capability_id": call.get("capability_id"),
                            "tool_name": call.get("tool_name"),
                            "call_event_line": call.get("event_line"),
                            "result_event_line": line_number,
                            "outcome": (
                                "integration_boundary_error"
                                if is_error
                                else "provider_result"
                            ),
                        }
                    if not is_error:
                        continue
                    binding_errors.append(
                        {
                            **call,
                            "result_event_line": line_number,
                            "error_excerpt": serialized[:1000],
                        }
                    )
                continue
            if event.get("message_type") != "AssistantMessage":
                continue
            for item in content if isinstance(content, list) else [content]:
                serialized = str(item)
                any_tool = re.search(r"name=['\"]([^'\"]+)['\"]", serialized)
                if not any_tool:
                    continue
                raw_tool_name = any_tool.group(1)
                tool = re.search(
                    r"name=['\"]mcp__pertura__(run_analysis|run_diagnostic|evaluate_virtual_model)['\"]",
                    serialized,
                )
                binding_match = re.search(
                    r"['\"]binding_id['\"]\s*:\s*['\"]([^'\"]+)['\"]",
                    serialized,
                )
                capability = re.search(
                    r"['\"]capability_id['\"]\s*:\s*['\"]([^'\"]+)['\"]",
                    serialized,
                )
                if tool:
                    parsed_input = _serialized_tool_input(serialized)
                    binding_id = binding_match.group(1) if binding_match else None
                    capability_id = capability_by_binding.get(binding_id or "") or (
                        capability.group(1) if capability else None
                    )
                    forbidden_bound_fields = [
                        field
                        for field in (
                            "capability_id",
                            "contract_id",
                            "scope",
                            "dependencies",
                        )
                        if re.search(rf"['\"]{field}['\"]\s*:", serialized)
                    ]
                    expected_arguments = minimal_arguments_by_binding.get(
                        str(binding_id or ""), {}
                    )
                    if "objective" in expected_arguments and (
                        not isinstance(parsed_input, Mapping)
                        or parsed_input.get("objective")
                        != expected_arguments["objective"]
                    ):
                        forbidden_bound_fields.append("objective")
                    supplied_override_fields: list[str] = []
                    if re.search(r"['\"]parameters['\"]\s*:", serialized):
                        supplied_parameters = (
                            parsed_input.get("parameters")
                            if isinstance(parsed_input, Mapping)
                            else None
                        )
                        allowed_overrides = allowed_overrides_by_binding.get(
                            str(binding_id or ""), set()
                        )
                        if not isinstance(supplied_parameters, Mapping):
                            forbidden_bound_fields.append("parameters")
                        else:
                            supplied_override_fields = sorted(
                                set(supplied_parameters) & allowed_overrides
                            )
                            forbidden_bound_fields.extend(
                                f"parameters.{name}"
                                for name in sorted(
                                    set(supplied_parameters) - allowed_overrides
                                )
                            )
                    exact_minimal_call = bool(
                        binding_id in required_binding_ids
                        and tool_by_binding.get(str(binding_id)) == tool.group(1)
                        and not forbidden_bound_fields
                    )
                    if exact_minimal_call:
                        observed_binding_ids.append(str(binding_id))
                    calls.append(
                        {
                            "event_line": line_number,
                            "tool_name": tool.group(1),
                            "binding_id": binding_id,
                            "capability_id": capability_id,
                            "advertised": capability_id in advertised_capability_ids,
                            "bound": binding_id in required_binding_ids,
                            "exact_minimal_call": exact_minimal_call,
                            "forbidden_bound_fields": forbidden_bound_fields,
                            "supplied_override_fields": supplied_override_fields,
                        }
                    )
                    tool_use = re.search(r"id=['\"]([^'\"]+)['\"]", serialized)
                    if binding_id in required_binding_ids and tool_use:
                        bound_tool_use_ids[tool_use.group(1)] = calls[-1]
                    continue
                if (
                    required_binding_ids
                    and boundary_release is None
                    and _is_prebinding_scientific_tool(raw_tool_name, serialized)
                    and first_boundary_violation is None
                ):
                    first_boundary_violation = {
                        "event_line": line_number,
                        "tool_name": raw_tool_name,
                        "missing_binding_ids": [
                            item
                            for item in required_binding_ids
                            if item not in observed_binding_ids
                        ],
                    }
    if not required_binding_ids:
        capability_first_status = "not_applicable"
    elif first_boundary_violation is None and boundary_release is not None:
        capability_first_status = "passed"
    else:
        capability_first_status = "failed"
    if not required_binding_ids:
        required_binding_coverage_status = "not_applicable"
    elif set(required_binding_ids).issubset(observed_binding_ids):
        required_binding_coverage_status = "passed"
    else:
        required_binding_coverage_status = "failed"
    caller_error_markers = (
        "attempted to override locked parameters",
        "cannot replace the bound",
        "requires run_",
        "unknown or inactive capability invocation binding",
        "outside the active task",
        "bound predecessor has not produced",
    )
    model_binding_errors = [
        item
        for item in binding_errors
        if (
            not item.get("exact_minimal_call")
            or bool(item.get("supplied_override_fields"))
            or any(
                marker in item["error_excerpt"].lower()
                for marker in caller_error_markers
            )
        )
    ]
    runner_binding_errors = [
        item for item in binding_errors if item not in model_binding_errors
    ]
    model_error_counts = {
        binding_id: sum(
            str(item.get("binding_id") or "") == binding_id
            for item in model_binding_errors
        )
        for binding_id in required_binding_ids
    }
    if not required_binding_ids:
        model_binding_retry_status = "not_applicable"
    elif any(count > 1 for count in model_error_counts.values()):
        model_binding_retry_status = "failed"
    else:
        model_binding_retry_status = "passed"
    return {
        "schema_version": "pertura-paper-capability-treatment-uptake-v2",
        "advertised_capability_ids": list(advertised_capability_ids),
        "required_binding_ids": required_binding_ids,
        "observed_binding_ids": list(dict.fromkeys(observed_binding_ids)),
        "capability_first_status": capability_first_status,
        "first_boundary_violation": first_boundary_violation,
        "boundary_release": boundary_release,
        "required_binding_coverage_status": required_binding_coverage_status,
        "missing_binding_ids": [
            item for item in required_binding_ids if item not in observed_binding_ids
        ],
        "actual_mcp_capability_ids": list(
            dict.fromkeys(
                item["capability_id"] for item in calls if item["capability_id"]
            )
        ),
        "calls": calls,
        "runner_binding_integration_errors": runner_binding_errors,
        "model_binding_errors": model_binding_errors,
        "model_binding_error_counts": model_error_counts,
        "model_binding_retry_status": model_binding_retry_status,
    }


def _serialized_tool_input(serialized: str) -> dict[str, Any] | None:
    """Parse the stable ToolUseBlock input repr without executing it."""

    match = re.search(r"\binput=(\{.*\})\)?$", serialized, flags=re.DOTALL)
    if not match:
        return None
    try:
        payload = ast.literal_eval(match.group(1))
    except (SyntaxError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _is_prebinding_scientific_tool(tool_name: str, serialized: str) -> bool:
    short = tool_name.rsplit("__", 1)[-1]
    if short in {"Skill", "submit_task_bundle"}:
        return False
    if short == "Read" and any(
        marker in serialized
        for marker in (
            "paper_benchmark_assets.json",
            "capability_contracts",
            "SKILL.md",
        )
    ):
        return False
    return short in {
        "Read",
        "Glob",
        "Grep",
        "Bash",
        "Notebook",
        "Write",
        "Edit",
        "run_analysis",
        "run_diagnostic",
        "evaluate_virtual_model",
    }


def _audit_baseline_skill_access(
    event_log: Path,
    *,
    start_offset: int,
    condition: str,
) -> dict[str, Any]:
    """Detect baseline tool calls that inspect the installed skill bundle."""

    if condition == "pertura_full":
        return {
            "schema_version": "pertura-benchmark-skill-leakage-audit-v1",
            "status": "not_applicable",
            "scanned_tool_events": 0,
            "hits": [],
        }
    if not event_log.is_file():
        return {
            "schema_version": "pertura-benchmark-skill-leakage-audit-v1",
            "status": "passed",
            "scanned_tool_events": 0,
            "hits": [],
        }

    forbidden = (
        "agent_bundle/skills",
        "agent_bundle\\skills",
        ".claude-plugin",
        "skill.md",
        *(name.lower() for name in BUNDLED_SKILL_NAMES),
    )
    with event_log.open("rb") as handle:
        handle.seek(max(0, start_offset))
        text = handle.read().decode("utf-8", errors="replace")
    hits: list[dict[str, Any]] = []
    scanned = 0
    for line_number, line in enumerate(text.splitlines(), start=1):
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("message_type") != "AssistantMessage":
            continue
        content = (event.get("payload") or {}).get("content") or []
        for item in content if isinstance(content, list) else [content]:
            serialized = json.dumps(item, sort_keys=True, default=str)
            lowered = serialized.lower()
            is_tool_use = (
                "tooluseblock" in lowered
                or '"type": "tool_use"' in lowered
                or '"type":"tool_use"' in lowered
            )
            if not is_tool_use:
                continue
            scanned += 1
            matched = sorted({token for token in forbidden if token in lowered})
            if matched:
                hits.append(
                    {
                        "event_line": line_number,
                        "matched_tokens": matched,
                        "tool_use_excerpt": serialized[:500],
                    }
                )
    return {
        "schema_version": "pertura-benchmark-skill-leakage-audit-v1",
        "status": "failed" if hits else "passed",
        "scanned_tool_events": scanned,
        "hits": hits[:20],
    }


def _audit_reference_truth_access(
    event_log: Path,
    *,
    start_offset: int,
    allowed_provider_assets: tuple[Mapping[str, Any], ...] = (),
) -> dict[str, Any]:
    """Reject provider tool access to post-provider scoring truth.

    Provider-visible assets are selected before the turn from the bound asset
    catalog and carry content hashes.  Their exact paths are legal even when a
    historical storage directory contains a forbidden reference token.  Any
    other path in that namespace remains forbidden.
    """

    if not event_log.is_file():
        return {
            "schema_version": "pertura-benchmark-reference-leakage-audit-v1",
            "status": "passed",
            "scanned_tool_events": 0,
            "hits": [],
        }
    forbidden = (
        "task-reference-catalog",
        "task_reference_catalog",
        "task_references/",
        "task_references\\",
        "/references/ref-",
        "\\references\\ref-",
        "metric_references.v1.json",
        "pertura_bench_metric_references",
        "paper_task_evaluation.py",
        "metric_evaluators.py",
        "/grader",
        "\\grader",
    )
    with event_log.open("rb") as handle:
        handle.seek(max(0, start_offset))
        text = handle.read().decode("utf-8", errors="replace")
    allowed_paths = _registered_provider_audit_paths(allowed_provider_assets)
    hits: list[dict[str, Any]] = []
    scanned = 0
    for line_number, line in enumerate(text.splitlines(), start=1):
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("message_type") != "AssistantMessage":
            continue
        content = (event.get("payload") or {}).get("content") or []
        for item in content if isinstance(content, list) else [content]:
            serialized = json.dumps(item, sort_keys=True, default=str)
            searchable = item if isinstance(item, str) else serialized
            lowered = str(searchable).lower()
            if not (
                "tooluseblock" in lowered
                or '"type": "tool_use"' in lowered
                or '"type":"tool_use"' in lowered
            ):
                continue
            scanned += 1
            redacted = _redact_registered_provider_paths(lowered, allowed_paths)
            matched = sorted({token for token in forbidden if token in redacted})
            if matched:
                hits.append(
                    {
                        "event_line": line_number,
                        "matched_tokens": matched,
                        "tool_use_excerpt": serialized[:500],
                    }
                )
    return {
        "schema_version": "pertura-benchmark-reference-leakage-audit-v1",
        "status": "failed" if hits else "passed",
        "scanned_tool_events": scanned,
        "hits": hits[:20],
    }


def _registered_provider_audit_paths(
    assets: tuple[Mapping[str, Any], ...],
) -> tuple[tuple[str, bool], ...]:
    """Return exact hash-bound provider paths and safe complete directories."""

    records: dict[Path, str] = {}
    for asset in assets:
        raw_path = str(asset.get("path") or "")
        content_sha256 = str(asset.get("content_sha256") or "")
        if not raw_path or not content_sha256.startswith("sha256:"):
            continue
        path = Path(raw_path).expanduser().resolve()
        records[path] = raw_path

    allowed: dict[str, bool] = {}
    for path, raw_path in records.items():
        allowed[raw_path] = path.is_dir()
        allowed[str(path)] = path.is_dir()

    files_by_parent: dict[Path, set[Path]] = {}
    for path in records:
        if path.is_file():
            files_by_parent.setdefault(path.parent, set()).add(path)
    for parent, registered_children in files_by_parent.items():
        try:
            visible_children = {child.resolve() for child in parent.iterdir()}
        except OSError:
            continue
        if visible_children == registered_children:
            allowed[str(parent)] = True

    return tuple(sorted(allowed.items(), key=lambda item: (-len(item[0]), item[0])))


def _redact_registered_provider_paths(
    text: str,
    allowed_paths: tuple[tuple[str, bool], ...],
) -> str:
    """Redact exact registered paths without hiding sibling/traversal access."""

    redacted = text
    boundary = r"(?=$|[\s\"'`,;:)\]}]|\\[nrt\"'])"
    for raw_path, is_directory in allowed_paths:
        variants = {
            raw_path.lower(),
            raw_path.replace("\\", "/").lower(),
            raw_path.replace("/", "\\").lower(),
        }
        for variant in sorted(variants, key=len, reverse=True):
            if not variant:
                continue
            separator = r"[\\/]*" if is_directory else ""
            redacted = re.sub(
                re.escape(variant) + separator + boundary,
                "<registered-provider-asset>",
                redacted,
            )
    return redacted


def _load_json(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _write(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(dict(payload), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
