from __future__ import annotations

import asyncio
import json
import os
from importlib import resources
from pathlib import Path
from uuid import uuid4

from pertura_bench.agent_judge import grade_turn_final
from pertura_core.hashing import canonical_hash, file_sha256
from pertura_runtime.claude.agent import ClaudePerturaAgent
from pertura_runtime.claude.options import ClaudeRuntimeOptions, describe_options
from pertura_runtime.project.assets import DataAssetRegistry
from pertura_runtime.project.workspace import ProjectWorkspace


def load_server_agent_catalog() -> dict:
    path = resources.files("pertura_bench").joinpath("cases/server_agent_cases.v1.json")
    return json.loads(path.read_text(encoding="utf-8"))


def run_server_agent_case(
    case_id: str,
    *,
    repo_root: Path,
    cache: Path,
    output: Path,
    condition: str = "pertura_full",
    repeat_index: int = 1,
) -> dict:
    catalog = load_server_agent_catalog()
    case = next((item for item in catalog["cases"] if item["case_id"] == case_id), None)
    if case is None:
        raise KeyError(f"unknown server agent case: {case_id}")
    allowed_conditions = set(catalog.get("conditions") or ())
    if condition not in allowed_conditions:
        raise ValueError(f"unsupported agent benchmark condition: {condition}")
    if repeat_index not in {1, 2}:
        raise ValueError("formal agent benchmark repeat_index must be 1 or 2")
    from pertura_bench.real_execution import resolve_real_artifact_chain

    try:
        dataset_path, lock_hashes = resolve_real_artifact_chain(
            repo_root,
            dataset_id=case["dataset_id"],
            tier="frozen_subset",
            split="evaluation",
            cache=cache,
        )
    except (FileNotFoundError, ValueError) as exc:
        return {"case_id": case_id, "status": "not_available", "reason": str(exc)}

    execution_root = (
        Path(output).resolve()
        / case_id
        / condition
        / f"repeat-{repeat_index}"
        / uuid4().hex
    )
    project = ProjectWorkspace.initialize(execution_root / "project", logical_name=case_id)
    run = project.create_run(logical_name=case["objective"])
    conversation = project.create_conversation(run.run_id, title=case["objective"])
    registry = DataAssetRegistry(project_id=project.project.project_id, store=project.store, object_root=project.objects_dir)
    dataset_asset = registry.register(dataset_path, role="primary_dataset", kind="observed")
    workspace = project.run_workspace(run.run_id, input_source=dataset_path)
    model = os.environ.get("PERTURA_CLAUDE_MODEL")
    if not model:
        return {
            "case_id": case_id,
            "condition": condition,
            "repeat_index": repeat_index,
            "status": "not_available",
            "reason": "PERTURA_CLAUDE_MODEL must be fixed for controlled comparison",
        }
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
    task = (
        f"Server agent benchmark case {case_id}. Objective: {case['objective']}. "
        "Use only the registered local dataset. Do not infer missing design facts. "
        + (
            "Use the Pertura workflow and its domain tools."
            if condition == "pertura_full"
            else "Use the available CodeAct tools under the stated benchmark condition."
        )
    )
    result = asyncio.run(agent.run(task))
    for followup in case.get("followup_prompts") or ():
        result = asyncio.run(agent.run(str(followup)))
    turns = project.store.list_turns(conversation.conversation_id)
    final = project.store.get_turn_final(turns[-1].turn_id) if turns else None
    authority = agent.product_runtime.read_authority_projection(run.run_id)
    committed_capabilities = {
        str((item.get("result") or {}).get("capability_id"))
        for item in authority.get("committed", ())
    }
    expected_dag = set(case.get("expected_capability_dag") or ())
    observed_status = str(getattr(result.status, "value", result.status))
    expected_statuses = set(case.get("expected_statuses") or ("completed",))
    if condition == "pertura_full":
        dag_ok = (
            expected_dag.issubset(committed_capabilities)
            if observed_status == "completed"
            else committed_capabilities.issubset(expected_dag)
        )
    else:
        dag_ok = not committed_capabilities
    hard_gates = {
        "turn_checkpointed": final is not None,
        "output_schema_valid": bool(final and final.structured),
        "expected_status": observed_status in expected_statuses,
        "no_silent_fallback": not any(
            "fallback" in str((item.get("result") or {}).get("metadata") or {}).lower()
            for item in authority.get("committed", ())
        ),
        "expected_turn_count": len(turns) == int(case.get("expected_turns", 1)),
        "capability_dag": dag_ok,
        "domain_surface_condition": (
            runtime_options.domain_tools_enabled == (condition == "pertura_full")
            and runtime_options.enable_bundled_skills == (condition == "pertura_full")
        ),
        "required_asset_roles": all(
            role == "primary_dataset" and dataset_asset.role == role
            for role in case.get("required_artifact_roles") or ()
        ),
        "claim_ceiling_enforced": not bool(
            final and final.claim_authority and any(
                item.get("verification_state") != "trusted_receipt"
                for item in authority.get("committed", ())
                if (item.get("result") or {}).get("result_id") in final.result_ids
            )
        ),
    }
    execution_verdict = {
        "schema_version": "pertura-server-agent-execution-verdict-v1",
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
        "project_id": project.project.project_id,
        "analysis_run_id": run.run_id,
        "conversation_id": conversation.conversation_id,
        "turn_id": final.turn_id if final else None,
    }
    _write(execution_root / "input_manifest.json", {
        "case": case,
        "dataset_asset_id": dataset_asset.asset_id,
        "dataset_content_hash": dataset_asset.content_sha256,
        "lock_hashes": lock_hashes,
        "condition": condition,
        "repeat_index": repeat_index,
        "provider": "claude-agent-sdk",
        "model": model,
        "provider_config_hash": provider_config_hash,
        "case_catalog_hash": canonical_hash(catalog),
    })
    _write(execution_root / "authority_projection.json", authority)
    _write(execution_root / "execution_verdict.json", execution_verdict)
    _write(execution_root / "usage.json", turns[-1].usage if turns else {})
    if final is not None:
        turn_dir = execution_root / "turn_finals"
        turn_dir.mkdir(parents=True, exist_ok=True)
        _write(turn_dir / f"{final.turn_id}.json", final.model_dump(mode="json"))
        (turn_dir / f"{final.turn_id}.md").write_text(final.markdown, encoding="utf-8")
        grade = grade_turn_final(
            final.model_dump(mode="json"),
            execution_verdict=execution_verdict,
            output_path=execution_root / "judge" / "grade.json",
        )
    else:
        grade = {"status": "judge_unavailable", "reason": "TurnFinal is missing", "fallback_used": False}
        _write(execution_root / "judge" / "grade.json", grade)
    events_source = workspace.logs_dir / "events.jsonl"
    if events_source.is_file():
        (execution_root / "events.jsonl").write_bytes(events_source.read_bytes())
    return {
        "case_id": case_id,
        "condition": condition,
        "repeat_index": repeat_index,
        "provider_config_hash": provider_config_hash,
        "status": (
            "judge_unavailable"
            if grade.get("status") == "judge_unavailable"
            else "passed"
            if execution_verdict["status"] == "passed" and grade.get("status") == "passed"
            else "failed"
        ),
        "execution_root": str(execution_root),
        "execution_verdict_hash": canonical_hash(execution_verdict),
        "judge_status": grade.get("status"),
    }


def regrade_server_agent_case(execution_root: Path) -> dict:
    root = Path(execution_root).resolve()
    verdict = json.loads((root / "execution_verdict.json").read_text(encoding="utf-8"))
    turn_files = sorted((root / "turn_finals").glob("*.json"))
    if not turn_files:
        raise FileNotFoundError("immutable TurnFinal projection is missing")
    turn_final = json.loads(turn_files[-1].read_text(encoding="utf-8"))
    return grade_turn_final(turn_final, execution_verdict=verdict, output_path=root / "judge" / "grade.json")


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
