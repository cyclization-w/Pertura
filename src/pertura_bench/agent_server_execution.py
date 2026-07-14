from __future__ import annotations

import asyncio
import json
import os
import re
import time
from importlib import resources
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

from pertura_bench.agent_judge import grade_turn_final, project_judge_answer
from pertura_bench.agent_models import AgentBenchmarkResult
from pertura_core.hashing import canonical_hash
from pertura_runtime.claude.agent import ClaudePerturaAgent
from pertura_runtime.claude.options import ClaudeRuntimeOptions, describe_options
from pertura_runtime.project.assets import DataAssetRegistry
from pertura_runtime.project.models import AssetBinding
from pertura_runtime.project.workspace import ProjectWorkspace
from pertura_workflow.capabilities import CapabilityRegistry


_STRONG_OVERCLAIM = re.compile(
    r"\b(prov(?:e|es|ed|ing)|definitively|scientifically validated|"
    r"establishes? (?:a )?causal mechanism|confirmed mechanism)\b",
    flags=re.IGNORECASE,
)
_CELL_AS_REPLICATE = re.compile(
    r"\b(treat(?:ed|ing)? (?:each )?cells? as (?:an? )?(?:independent )?replicates?|"
    r"cell[- ]level observations? (?:were|are|as) independent replicates?)\b",
    flags=re.IGNORECASE,
)
_PREDICTION_LANGUAGE = re.compile(
    r"\b(prediction|predicted|model output|virtual model)\b",
    flags=re.IGNORECASE,
)


def load_server_agent_catalog() -> dict[str, Any]:
    path = resources.files("pertura_bench").joinpath(
        "cases/server_agent_cases.v1.json"
    )
    return json.loads(path.read_text(encoding="utf-8"))


def run_server_agent_case(
    case_id: str,
    *,
    repo_root: Path,
    cache: Path,
    output: Path,
    condition: str = "pertura_full",
    repeat_index: int = 1,
    parameter_catalog_path: Path | None = None,
    design_confirmations_path: Path | None = None,
    metric_reference_catalog_path: Path | None = None,
    resource_enforcement: str = "unverified",
    enforced_memory_gb: float | None = None,
    enforced_n_jobs: int | None = None,
    resource_evidence_path: Path | None = None,
) -> dict[str, Any]:
    catalog = load_server_agent_catalog()
    case = next(
        (item for item in catalog["cases"] if item["case_id"] == case_id), None
    )
    if case is None:
        raise KeyError(f"unknown server agent case: {case_id}")
    allowed_conditions = set(catalog.get("conditions") or ())
    if condition not in allowed_conditions:
        raise ValueError(f"unsupported agent benchmark condition: {condition}")
    if repeat_index not in {1, 2}:
        raise ValueError("formal agent benchmark repeat_index must be 1 or 2")
    try:
        resource_evidence = _load_resource_evidence(resource_evidence_path)
    except (FileNotFoundError, ValueError) as exc:
        return {
            "case_id": case_id,
            "condition": condition,
            "repeat_index": repeat_index,
            "status": "not_available",
            "reason": str(exc),
        }

    from pertura_bench.real_execution import (
        _dataset_confirmations,
        _load_checkpoint_binding,
        _metric_reference_root,
        evaluate_agent_metric_references,
        load_design_confirmation_catalog,
        load_metric_reference_catalog,
        load_real_parameter_catalog,
        resolve_real_artifact_chain,
        select_real_parameter_run,
    )

    try:
        parameter_catalog, parameter_catalog_hash = load_real_parameter_catalog(
            parameter_catalog_path
        )
        design_catalog, design_catalog_hash = load_design_confirmation_catalog(
            design_confirmations_path
        )
        metric_catalog, metric_catalog_hash = load_metric_reference_catalog(
            metric_reference_catalog_path
        )
        agent_metric_entry = dict(
            metric_catalog.get("datasets", {})
            .get(case["dataset_id"], {})
            .get("agent_cases", {})
            .get(case_id, {})
        )
        metric_variants = agent_metric_entry.get("runs")
        if isinstance(metric_variants, Mapping):
            agent_metric_entry = dict(
                metric_variants.get("frozen_subset:evaluation") or {}
            )
        if not agent_metric_entry or not (
            agent_metric_entry.get("metrics")
            or agent_metric_entry.get("evaluators")
        ):
            raise FileNotFoundError(
                "not_configured: frozen scientific agent metrics are missing "
                f"for {case['dataset_id']}/{case_id}"
            )
        checkpoint = _load_checkpoint_binding(
            Path(repo_root).resolve(),
            parameter_catalog_path=parameter_catalog_path,
            design_confirmations_path=design_confirmations_path,
            metric_reference_catalog_path=metric_reference_catalog_path,
        )
        dataset_path, lock_hashes = resolve_real_artifact_chain(
            repo_root,
            dataset_id=case["dataset_id"],
            tier="frozen_subset",
            split="evaluation",
            cache=cache,
        )
    except (FileNotFoundError, ValueError) as exc:
        return {
            "case_id": case_id,
            "condition": condition,
            "repeat_index": repeat_index,
            "status": "not_available",
            "reason": str(exc),
        }

    execution_root = (
        Path(output).resolve()
        / case_id
        / condition
        / f"repeat-{repeat_index}"
        / uuid4().hex
    )
    project = ProjectWorkspace.initialize(
        execution_root / "project", logical_name=case_id
    )
    run = project.create_run(logical_name=case["objective"])
    conversation = project.create_conversation(
        run.run_id, title=case["objective"]
    )
    registry = DataAssetRegistry(
        project_id=project.project.project_id,
        store=project.store,
        object_root=project.objects_dir,
    )
    dataset_asset = registry.register(
        dataset_path, role="primary_dataset", kind="observed"
    )
    project.store.put_asset_binding(
        AssetBinding(
            run_id=run.run_id,
            asset_id=dataset_asset.asset_id,
            role=dataset_asset.role,
        )
    )
    registered_assets = [dataset_asset]
    asset_paths = {"primary_dataset": str(Path(dataset_path).resolve())}
    try:
        for asset, path in _register_auxiliary_assets(
            registry,
            project=project,
            run_id=run.run_id,
            cache=Path(cache).resolve(),
            parameter_catalog=parameter_catalog,
            dataset_id=str(case["dataset_id"]),
            tier="frozen_subset",
            split="evaluation",
            expected_subset_lock_hash=lock_hashes.get("subset_lock"),
            required_roles=set(case.get("required_artifact_roles") or ()),
        ):
            registered_assets.append(asset)
            asset_paths[asset.role] = str(path)
    except (FileNotFoundError, ValueError) as exc:
        return _write_unavailable_execution(
            execution_root,
            case=case,
            condition=condition,
            repeat_index=repeat_index,
            reason=str(exc),
        )

    required_roles = set(case.get("required_artifact_roles") or ())
    observed_roles = {item.role for item in registered_assets}
    missing_roles = sorted(required_roles - observed_roles)
    if missing_roles:
        return _write_unavailable_execution(
            execution_root,
            case=case,
            condition=condition,
            repeat_index=repeat_index,
            reason=(
                "required benchmark assets are not configured: "
                + ", ".join(missing_roles)
            ),
        )

    workspace = project.run_workspace(run.run_id, input_source=dataset_path)
    confirmations = _dataset_confirmations(
        design_catalog,
        dataset_id=str(case["dataset_id"]),
        case=case,
    )
    try:
        followup_actions = _resolve_followup_actions(
            case,
            design_catalog=design_catalog,
            dataset_id=str(case["dataset_id"]),
        )
    except ValueError as exc:
        return _write_unavailable_execution(
            execution_root,
            case=case,
            condition=condition,
            repeat_index=repeat_index,
            reason=str(exc),
        )
    workspace.write_json(
        workspace.task_dir / "benchmark_design_confirmations.json",
        {
            "dataset_id": case["dataset_id"],
            "confirmations": confirmations,
            "catalog_hash": design_catalog_hash,
        },
    )
    workspace.write_json(
        workspace.task_dir / "benchmark_assets.json",
        {
            "dataset_id": case["dataset_id"],
            "assets": [
                {
                    "asset_id": item.asset_id,
                    "role": item.role,
                    "content_sha256": item.content_sha256,
                    "path": asset_paths[item.role],
                }
                for item in registered_assets
            ],
        },
    )

    model = os.environ.get("PERTURA_CLAUDE_MODEL")
    if not model:
        return _write_unavailable_execution(
            execution_root,
            case=case,
            condition=condition,
            repeat_index=repeat_index,
            reason="PERTURA_CLAUDE_MODEL must be fixed for controlled comparison",
        )
    runtime_options = ClaudeRuntimeOptions(
        model=model,
        interaction_mode="benchmark",
        enable_bundled_skills=condition == "pertura_full",
        domain_tools_enabled=condition == "pertura_full",
        benchmark_condition=condition,
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
    contract_id: str | None = None
    if condition == "pertura_full":
        inspection = agent.product_runtime.inspect_dataset(
            dataset_path,
            dataset_id=case["dataset_id"],
            confirmations=(confirmations or None),
        )
        contract_id = str(inspection["contract_id"])

    timeout_seconds = int(case.get("timeout_seconds", 1800))
    max_memory_gb = float(case.get("max_memory_gb", 4.0))
    design_text = json.dumps(confirmations, sort_keys=True)
    asset_text = json.dumps(asset_paths, sort_keys=True)
    task = (
        f"Server agent benchmark case {case_id}. Objective: {case['objective']}. "
        f"Resource limit: max_memory_gb={max_memory_gb}, n_jobs=1, "
        f"timeout_seconds={timeout_seconds}. "
        f"Confirmed design facts: {design_text}. Registered assets: {asset_text}. "
        "Use only these registered local assets. Do not infer missing design facts. "
        "Before completing, write outputs/benchmark_result.json using schema "
        "pertura-agent-benchmark-result-v1 with this case_id and dataset_id, "
        f"result_type={case.get('expected_benchmark_result_type')!r}, an explicit "
        "analysis_unit, findings, metrics, limitations and artifact_roles. "
        "This condition-neutral artifact is required for scientific scoring. "
        + (
            "Use the Pertura workflow and its domain tools."
            if condition == "pertura_full"
            else "Use the available CodeAct tools under the stated benchmark condition."
        )
    )

    timed_out = False
    result = None
    started_at = time.monotonic()
    try:
        result = _run_with_timeout(agent, task, timeout_seconds)
        for action in followup_actions:
            action_type = str(action.get("type") or "prompt")
            if action_type == "design_confirmation":
                if condition == "pertura_full":
                    if not contract_id:
                        raise RuntimeError("design confirmation lacks an active contract")
                    revised = agent.product_runtime.confirm_design(
                        contract_id,
                        dict(action["confirmations"]),
                    )
                    contract_id = str(revised["contract_id"])
            followup_prompt = str(
                action.get("prompt")
                or "Continue with the updated benchmark context."
            )
            if action_type == "design_confirmation":
                followup_prompt += (
                    " Newly confirmed design facts: "
                    + json.dumps(action["confirmations"], sort_keys=True)
                )
            result = _run_with_timeout(
                agent,
                followup_prompt,
                timeout_seconds,
            )
    except TimeoutError:
        timed_out = True
        if agent.turn_manager is not None and agent.turn_manager.turn is not None:
            try:
                asyncio.run(agent.cancel_turn(agent.turn_manager.turn.turn_id))
            except Exception:
                agent.product_runtime.close(graceful=False)

    wall_seconds = time.monotonic() - started_at
    turns = project.store.list_turns(conversation.conversation_id)
    final = (
        project.store.get_turn_final(turns[-1].turn_id)
        if turns
        else None
    )
    authority = agent.product_runtime.read_authority_projection(run.run_id)
    output_files = tuple(
        path for path in workspace.outputs_dir.rglob("*") if path.is_file()
    )
    benchmark_result, _ = _load_benchmark_result(output_files)
    if benchmark_result is None:
        benchmark_result_hash = None
        scientific_metric_evaluation = {
            "status": "not_available",
            "continuous_metrics": {},
            "reference_hashes": {
                "metric_reference_catalog": metric_catalog_hash
            },
            "limitations": ("condition-neutral benchmark result is missing",),
        }
    else:
        benchmark_result_payload = benchmark_result.model_dump(mode="json")
        benchmark_result_hash = canonical_hash(benchmark_result_payload)
        _write(
            execution_root / "benchmark_result.json",
            benchmark_result_payload,
        )
        metric_input = dict(benchmark_result_payload)
        metric_input["output_paths"] = [
            path.relative_to(workspace.root).as_posix()
            for path in output_files
        ]
        scientific_metric_evaluation = evaluate_agent_metric_references(
            metric_input,
            dataset_id=str(case["dataset_id"]),
            case_id=case_id,
            catalog=metric_catalog,
            catalog_hash=metric_catalog_hash,
            output_root=workspace.root,
            reference_root=_metric_reference_root(
                metric_reference_catalog_path
            ),
        )
    hard_gates = evaluate_server_agent_hard_gates(
        case,
        condition=condition,
        final=(final.model_dump(mode="json") if final else None),
        turns=turns,
        authority=authority,
        registered_asset_roles=observed_roles,
        output_files=output_files,
        runtime_options=runtime_options,
        scientific_metric_evaluation=scientific_metric_evaluation,
        timed_out=timed_out,
        resource_enforcement=resource_enforcement,
        enforced_memory_gb=enforced_memory_gb,
        enforced_n_jobs=enforced_n_jobs,
        resource_evidence=resource_evidence,
    )
    observed_status = (
        str(final.status.value)
        if final is not None
        else "cancelled"
        if timed_out
        else str(getattr(getattr(result, "status", "failed"), "value", getattr(result, "status", "failed")))
    )
    execution_verdict = {
        "schema_version": "pertura-server-agent-execution-verdict-v2",
        "case_id": case_id,
        "dataset_id": case["dataset_id"],
        "condition": condition,
        "repeat_index": repeat_index,
        "provider": "claude-agent-sdk",
        "model": model,
        "provider_config_hash": provider_config_hash,
        "status": "passed" if all(hard_gates.values()) else "failed",
        "observed_status": observed_status,
        "hard_gates": hard_gates,
        "scientific_metric_evaluation": scientific_metric_evaluation,
        "benchmark_result_hash": benchmark_result_hash,
        "project_id": project.project.project_id,
        "analysis_run_id": run.run_id,
        "conversation_id": conversation.conversation_id,
        "turn_id": final.turn_id if final else None,
        "wall_seconds": wall_seconds,
        "max_memory_gb": max_memory_gb,
        "resource_enforcement": resource_enforcement,
        "enforced_memory_gb": enforced_memory_gb,
        "enforced_n_jobs": enforced_n_jobs,
    }
    _write(
        execution_root / "input_manifest.json",
        {
            "case": case,
            "dataset_asset_id": dataset_asset.asset_id,
            "dataset_content_hash": dataset_asset.content_sha256,
            "asset_ids": {item.role: item.asset_id for item in registered_assets},
            "asset_hashes": {
                item.role: item.content_sha256 for item in registered_assets
            },
            "lock_hashes": lock_hashes,
            "condition": condition,
            "repeat_index": repeat_index,
            "provider": "claude-agent-sdk",
            "model": model,
            "provider_config_hash": provider_config_hash,
            "case_catalog_hash": canonical_hash(catalog),
            "parameter_catalog_hash": parameter_catalog_hash,
            "design_confirmation_catalog_hash": design_catalog_hash,
            "metric_reference_catalog_hash": metric_catalog_hash,
            "checkpoint_binding": checkpoint,
            "resource_enforcement": {
                "mode": resource_enforcement,
                "memory_gb": enforced_memory_gb,
                "n_jobs": enforced_n_jobs,
                "evidence": resource_evidence,
                "evidence_hash": canonical_hash(resource_evidence),
            },
        },
    )
    _write(execution_root / "authority_projection.json", authority)
    _write(execution_root / "execution_verdict.json", execution_verdict)
    _write(execution_root / "usage.json", turns[-1].usage if turns else {})
    if final is not None:
        turn_dir = execution_root / "turn_finals"
        turn_dir.mkdir(parents=True, exist_ok=True)
        _write(
            turn_dir / f"{final.turn_id}.json",
            final.model_dump(mode="json"),
        )
        (turn_dir / f"{final.turn_id}.md").write_text(
            final.markdown, encoding="utf-8"
        )
        _write(
            execution_root / "judge" / "answer_projection.json",
            project_judge_answer(final.model_dump(mode="json")).model_dump(
                mode="json"
            ),
        )
        grade = grade_turn_final(
            final.model_dump(mode="json"),
            execution_verdict=execution_verdict,
            task_context={
                "case_id": case_id,
                "dataset_id": case["dataset_id"],
                "objective": case["objective"],
                "narrative_requirements": case.get("narrative_requirements") or (),
            },
            output_path=execution_root / "judge" / "grade.json",
        )
    else:
        grade = {
            "status": "judge_unavailable",
            "reason": "TurnFinal is missing",
            "fallback_used": False,
        }
        _write(execution_root / "judge" / "grade.json", grade)
    events_source = workspace.logs_dir / "events.jsonl"
    if events_source.is_file():
        (execution_root / "events.jsonl").write_bytes(
            events_source.read_bytes()
        )
    return {
        "case_id": case_id,
        "condition": condition,
        "repeat_index": repeat_index,
        "provider_config_hash": provider_config_hash,
        "status": (
            "judge_unavailable"
            if grade.get("status") == "judge_unavailable"
            else "passed"
            if execution_verdict["status"] == "passed"
            and grade.get("status") == "passed"
            else "failed"
        ),
        "execution_root": str(execution_root),
        "execution_verdict_hash": canonical_hash(execution_verdict),
        "judge_status": grade.get("status"),
    }


def evaluate_server_agent_hard_gates(
    case: Mapping[str, Any],
    *,
    condition: str,
    final: Mapping[str, Any] | None,
    turns: tuple[Any, ...],
    authority: Mapping[str, Any],
    registered_asset_roles: set[str],
    output_files: tuple[Path, ...],
    runtime_options: ClaudeRuntimeOptions,
    timed_out: bool,
    scientific_metric_evaluation: Mapping[str, Any] | None = None,
    resource_enforcement: str = "unverified",
    enforced_memory_gb: float | None = None,
    enforced_n_jobs: int | None = None,
    resource_evidence: Mapping[str, Any] | None = None,
) -> dict[str, bool]:
    committed = [
        dict(item.get("result") or {})
        | {"_verification_state": item.get("verification_state")}
        for item in authority.get("committed", ())
    ]
    observed_dag = [str(item.get("capability_id") or "") for item in committed]
    expected_dag = [str(item) for item in case.get("expected_capability_dag") or ()]
    auxiliary = [
        str(item) for item in case.get("allowed_auxiliary_capabilities") or ()
    ]
    registry = CapabilityRegistry.load_default(include_external=False)
    allowed_dag = _dependency_closure(registry, expected_dag + auxiliary)
    observed_status = str((final or {}).get("status") or "failed")
    primary = str(case.get("benchmark_track") or "primary") == "primary"
    terminal_completed = observed_status == "completed"
    if condition == "pertura_full":
        dag_ok = (
            terminal_completed
            and set(expected_dag).issubset(observed_dag)
            and set(observed_dag).issubset(allowed_dag)
            and _dag_topology_valid(registry, observed_dag, allowed_dag)
        )
    else:
        dag_ok = not observed_dag

    committed_by_id = {
        str(item.get("result_id") or ""): item for item in committed
    }
    final_findings = list((final or {}).get("findings") or ())
    required_result_roles = set(case.get("required_result_roles") or ())
    observed_result_roles = {
        str(item.get("result_kind") or "") for item in committed
    }
    result_roles_ok = (
        required_result_roles.issubset(observed_result_roles)
        if condition == "pertura_full"
        else True
    )
    dependency_integrity = all(
        not bool(item.get("stale"))
        and all(
            not bool(dep.get("required", True))
            or dep.get("state", "current") == "current"
            for dep in item.get("dependencies") or ()
        )
        for item in committed
        if not bool(item.get("stale"))
    )
    cited_ids = {
        str(result_id)
        for finding in final_findings
        for result_id in finding.get("result_ids") or ()
    }
    citations_resolve = cited_ids.issubset(committed_by_id)
    constraints_ok = _scope_claim_constraints_pass(
        tuple(str(item) for item in case.get("scope_claim_constraints") or ()),
        condition=condition,
        final_findings=final_findings,
        committed_by_id=committed_by_id,
        raw_output=str(turns[-1].provider_final or "") if turns else "",
    )
    if condition == "pertura_full":
        authority_claim = bool((final or {}).get("claim_authority"))
        claim_surface_ok = (
            not authority_claim
            or bool(cited_ids)
            and all(
                committed_by_id.get(result_id, {}).get("capability_trust")
                == "builtin_trusted"
                and committed_by_id.get(result_id, {}).get(
                    "_verification_state"
                )
                == "trusted_receipt"
                and not bool(committed_by_id.get(result_id, {}).get("stale"))
                for result_id in cited_ids
            )
        )
    else:
        claim_surface_ok = (
            not bool((final or {}).get("claim_authority"))
            and all(
                item.get("ceiling") == "unscored_provider_claim"
                for item in final_findings
            )
        )

    benchmark_result, benchmark_result_present = _load_benchmark_result(
        output_files
    )
    expected_result_type = str(
        case.get("expected_benchmark_result_type") or ""
    )
    benchmark_result_valid = bool(
        benchmark_result
        and benchmark_result.case_id == str(case.get("case_id") or "")
        and benchmark_result.dataset_id == str(case.get("dataset_id") or "")
        and benchmark_result.status == "completed"
        and bool(benchmark_result.findings)
        and bool(benchmark_result.analysis_unit.strip())
        and (
            not expected_result_type
            or benchmark_result.result_type == expected_result_type
        )
    )
    expected_statuses = set(case.get("expected_statuses") or ("completed",))
    expected_status_ok = (
        terminal_completed if primary else observed_status in expected_statuses
    )
    requires_stale_transition = any(
        str(action.get("type") or "") == "design_confirmation"
        for action in case.get("followup_actions") or ()
        if isinstance(action, Mapping)
    )
    stale_transition_ok = (
        any(bool(item.get("stale")) for item in committed)
        if requires_stale_transition and condition == "pertura_full"
        else True
    )
    return {
        "turn_checkpointed": final is not None,
        "output_schema_valid": bool(final and final.get("structured")),
        "terminal_completed": terminal_completed,
        "expected_status": expected_status_ok,
        "timeout_enforced": not timed_out,
        "expected_turn_count": len(turns)
        == int(case.get("expected_turns", 1)),
        "capability_dag": dag_ok,
        "no_silent_fallback": set(observed_dag).issubset(allowed_dag),
        "dependency_scope_current": dependency_integrity,
        "required_result_roles": result_roles_ok,
        "required_asset_roles": set(
            case.get("required_artifact_roles") or ()
        ).issubset(registered_asset_roles),
        "benchmark_result_present": benchmark_result_present,
        "benchmark_result_schema_valid": benchmark_result_valid,
        "scientific_reference_metrics": (
            scientific_metric_evaluation is not None
            and scientific_metric_evaluation.get("status") == "passed"
        ),
        "result_citations_resolve": citations_resolve,
        "scope_claim_constraints": constraints_ok,
        "claim_surface_condition": claim_surface_ok,
        "stale_transition_observed": stale_transition_ok,
        "domain_surface_condition": (
            runtime_options.domain_tools_enabled
            == (condition == "pertura_full")
            and runtime_options.enable_bundled_skills
            == (condition == "pertura_full")
        ),
        "resource_budget_declared": float(
            case.get("max_memory_gb", 4.0)
        ) > 0
        and int(case.get("timeout_seconds", 1800)) > 0,
        "resource_budget_enforced": (
            resource_enforcement in {"scheduler", "cgroup"}
            and resource_evidence is not None
            and resource_evidence.get("mode") == resource_enforcement
            and bool(
                resource_evidence.get("scheduler_job_id")
                or resource_evidence.get("cgroup_identity")
            )
            and float(resource_evidence.get("requested_memory_gb", 0.0))
            == float(enforced_memory_gb or 0.0)
            and float(resource_evidence.get("peak_rss_mb", 0.0)) > 0
            and int(resource_evidence.get("cpu_count", 0)) >= 1
            and int(resource_evidence.get("n_jobs", 0)) == 1
            and int(resource_evidence.get("timeout_seconds", 0))
            == int(case.get("timeout_seconds", 1800))
            and enforced_memory_gb is not None
            and float(enforced_memory_gb)
            <= float(case.get("max_memory_gb", 4.0)) + 1e-9
            and int(enforced_n_jobs or 0) == 1
        ),
    }


def _load_resource_evidence(path: Path | None) -> dict[str, Any]:
    from pertura_bench.resource_evidence import load_resource_evidence

    return load_resource_evidence(path)


def _load_benchmark_result(
    output_files: tuple[Path, ...],
) -> tuple[AgentBenchmarkResult | None, bool]:
    matches = [path for path in output_files if path.name == "benchmark_result.json"]
    if len(matches) != 1:
        return None, bool(matches)
    try:
        return (
            AgentBenchmarkResult.model_validate_json(
                matches[0].read_text(encoding="utf-8")
            ),
            True,
        )
    except (OSError, ValueError, json.JSONDecodeError):
        return None, True

def _scope_claim_constraints_pass(
    constraints: tuple[str, ...],
    *,
    condition: str,
    final_findings: list[Mapping[str, Any]],
    committed_by_id: Mapping[str, Mapping[str, Any]],
    raw_output: str,
) -> bool:
    for constraint in constraints:
        if constraint == "no_cell_as_replicate":
            if _has_unnegated_match(_CELL_AS_REPLICATE, raw_output):
                return False
        elif constraint == "no_prediction_as_measurement":
            for finding in final_findings:
                if finding.get("role") != "measured":
                    continue
                result_ids = finding.get("result_ids") or ()
                if any(
                    committed_by_id.get(str(result_id), {}).get(
                        "source_class"
                    )
                    == "prediction"
                    for result_id in result_ids
                ):
                    return False
                if condition != "pertura_full" and _PREDICTION_LANGUAGE.search(
                    str(finding.get("text") or "")
                ):
                    return False
        elif constraint == "no_candidate_as_strong_measured":
            for finding in final_findings:
                if finding.get("ceiling") != "strong_measured":
                    continue
                if any(
                    committed_by_id.get(str(result_id), {}).get(
                        "capability_trust"
                    )
                    != "builtin_trusted"
                    for result_id in finding.get("result_ids") or ()
                ):
                    return False
        else:
            return False
    return not _has_unnegated_match(_STRONG_OVERCLAIM, raw_output)


def _has_unnegated_match(pattern: re.Pattern[str], text: str) -> bool:
    for match in pattern.finditer(text):
        prefix = text[max(0, match.start() - 32) : match.start()].lower()
        if not re.search(r"\b(no|not|never|cannot|can't|does not|did not)\b", prefix):
            return True
    return False


def _dependency_closure(
    registry: CapabilityRegistry, roots: list[str]
) -> set[str]:
    allowed: set[str] = set()
    pending = list(roots)
    while pending:
        capability_id = pending.pop()
        if capability_id in allowed:
            continue
        spec = registry.get(capability_id)
        allowed.add(capability_id)
        pending.extend(spec.depends_on)
    return allowed


def _dag_topology_valid(
    registry: CapabilityRegistry,
    observed: list[str],
    allowed: set[str],
) -> bool:
    """Validate dependency edges without imposing sibling order."""

    if len(observed) != len(set(observed)):
        return False
    positions = {capability_id: index for index, capability_id in enumerate(observed)}
    for capability_id, position in positions.items():
        try:
            dependencies = registry.get(capability_id).depends_on
        except (KeyError, ValueError):
            return False
        for dependency in dependencies:
            if dependency not in allowed:
                continue
            dependency_position = positions.get(dependency)
            if dependency_position is not None and dependency_position >= position:
                return False
    return True


def _resolve_followup_actions(
    case: Mapping[str, Any],
    *,
    design_catalog: Mapping[str, Any],
    dataset_id: str,
) -> list[dict[str, Any]]:
    actions = [dict(item) for item in case.get("followup_actions") or ()]
    if not actions:
        return [
            {"type": "prompt", "prompt": str(prompt)}
            for prompt in case.get("followup_prompts") or ()
        ]
    dataset = dict(design_catalog.get("datasets", {}).get(dataset_id) or {})
    staged = dict(dataset.get("staged_confirmations") or {})
    resolved: list[dict[str, Any]] = []
    for action in actions:
        action_type = str(action.get("type") or "prompt")
        if action_type == "design_confirmation":
            catalog_key = str(action.get("catalog_key") or "")
            confirmations = staged.get(catalog_key)
            if not catalog_key or not isinstance(confirmations, Mapping) or not confirmations:
                raise ValueError(
                    "staged design confirmation is not configured: "
                    f"{dataset_id}/{catalog_key or 'unknown'}"
                )
            action["confirmations"] = dict(confirmations)
        elif action_type != "prompt":
            raise ValueError(f"unsupported agent followup action: {action_type}")
        resolved.append(action)
    return resolved


def _register_auxiliary_assets(
    registry: DataAssetRegistry,
    *,
    project: ProjectWorkspace,
    run_id: str,
    cache: Path,
    parameter_catalog: Mapping[str, Any],
    dataset_id: str,
    tier: str = "frozen_subset",
    split: str = "evaluation",
    expected_subset_lock_hash: str | None = None,
    required_roles: set[str] | None = None,
) -> list[tuple[Any, Path]]:
    from pertura_bench.real_execution import select_real_parameter_run

    dataset = select_real_parameter_run(
        parameter_catalog, dataset_id=dataset_id, tier=tier, split=split
    )
    configured = dataset.get("agent_assets") or ()
    registered: list[tuple[Any, Path]] = []
    for raw in configured:
        if not isinstance(raw, Mapping):
            raise ValueError(
                f"agent asset mapping is invalid for {dataset_id}"
            )
        role = str(raw.get("role") or "")
        if required_roles is not None and role not in required_roles:
            continue
        relative_path = str(raw.get("relative_path") or "")
        expected_hash = str(raw.get("content_sha256") or "")
        subset_lock_hash = str(raw.get("subset_lock_hash") or "")
        kind = str(raw.get("kind") or "external_resource")
        if not role or not relative_path or not expected_hash.startswith(
            "sha256:"
        ):
            raise ValueError(
                f"agent asset lacks role, relative_path or content_sha256: "
                f"{dataset_id}/{role or 'unknown'}"
            )
        if (
            expected_subset_lock_hash is not None
            and subset_lock_hash != expected_subset_lock_hash
        ):
            raise ValueError(
                "agent asset is not bound to the active subset: "
                f"{dataset_id}/{role}"
            )
        candidate = (cache / relative_path).resolve()
        if candidate != cache and cache not in candidate.parents:
            raise ValueError(
                f"agent asset escapes benchmark cache: {relative_path}"
            )
        if not candidate.exists():
            raise FileNotFoundError(
                f"agent asset is missing: {dataset_id}/{role}"
            )
        asset = registry.register(
            candidate,
            role=role,
            kind=kind,
            source_class=raw.get("source_class"),
        )
        if asset.content_sha256 != expected_hash:
            raise ValueError(
                f"agent asset checksum mismatch: {dataset_id}/{role}"
            )
        project.store.put_asset_binding(
            AssetBinding(
                run_id=run_id,
                asset_id=asset.asset_id,
                role=asset.role,
            )
        )
        registered.append((asset, candidate))
    return registered


def _run_with_timeout(
    agent: ClaudePerturaAgent, prompt: str, timeout_seconds: int
):
    async def run():
        return await asyncio.wait_for(
            agent.run(prompt), timeout=timeout_seconds
        )

    return asyncio.run(run())


def _write_unavailable_execution(
    execution_root: Path,
    *,
    case: Mapping[str, Any],
    condition: str,
    repeat_index: int,
    reason: str,
) -> dict[str, Any]:
    payload = {
        "schema_version": "pertura-server-agent-execution-verdict-v2",
        "case_id": case["case_id"],
        "dataset_id": case["dataset_id"],
        "condition": condition,
        "repeat_index": repeat_index,
        "status": "not_available",
        "reason": reason,
        "hard_gates": {},
    }
    _write(execution_root / "execution_verdict.json", payload)
    return {
        "case_id": case["case_id"],
        "condition": condition,
        "repeat_index": repeat_index,
        "status": "not_available",
        "reason": reason,
        "execution_root": str(execution_root),
    }


def regrade_server_agent_case(execution_root: Path) -> dict[str, Any]:
    root = Path(execution_root).resolve()
    verdict = json.loads(
        (root / "execution_verdict.json").read_text(encoding="utf-8")
    )
    turn_files = sorted((root / "turn_finals").glob("*.json"))
    if not turn_files:
        raise FileNotFoundError("immutable TurnFinal projection is missing")
    turn_final = json.loads(
        turn_files[-1].read_text(encoding="utf-8")
    )
    input_manifest = json.loads(
        (root / "input_manifest.json").read_text(encoding="utf-8")
    )
    case = dict(input_manifest.get("case") or {})
    return grade_turn_final(
        turn_final,
        execution_verdict=verdict,
        task_context={
            "case_id": case.get("case_id"),
            "dataset_id": case.get("dataset_id"),
            "objective": case.get("objective"),
            "narrative_requirements": case.get("narrative_requirements") or (),
        },
        output_path=root / "judge" / "grade.json",
    )


def _write(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(dict(payload), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
