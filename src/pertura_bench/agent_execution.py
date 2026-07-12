from __future__ import annotations

import asyncio
import json
import os
from importlib import resources
from pathlib import Path
from typing import Any

from pertura_bench.agent_models import (
    AgentHardGateResult,
    AgentWorkflowCase,
    AgentWorkflowVerdict,
)
from pertura_core.hashing import canonical_hash, file_sha256
from pertura_runtime.product import PerturaProductRuntime
from pertura_runtime.project.assets import DataAssetRegistry
from pertura_runtime.project.lifecycle import TurnCheckpointManager
from pertura_runtime.project.models import TurnStatus
from pertura_runtime.project.workspace import ProjectWorkspace


AGENT_EXECUTION_FILES = (
    "src/pertura_core/hashing.py",
    "src/pertura_bench/agent_execution.py",
    "src/pertura_runtime/product.py",
    "src/pertura_runtime/project/assets.py",
    "src/pertura_runtime/project/lifecycle.py",
    "src/pertura_runtime/project/models.py",
    "src/pertura_runtime/project/store.py",
    "src/pertura_runtime/project/turns.py",
    "src/pertura_runtime/project/workspace.py",
    "src/pertura_workflow/capabilities/executors.py",
    "src/pertura_workflow/capabilities/registry.py",
    "src/pertura_runtime/verifier/broker.py",
    "src/pertura_runtime/verifier/session_store.py",
    "src/pertura_runtime/verifier/store.py",
    "src/pertura_workflow/planner.py",
)


def agent_execution_bundle_hash(repo_root: Path) -> str:
    root = Path(repo_root).resolve()
    files = {relative: file_sha256(root / relative) for relative in AGENT_EXECUTION_FILES}
    return canonical_hash({"schema_version": "pertura-agent-execution-bundle-v1", "files": files})


def load_agent_cases() -> tuple[AgentWorkflowCase, ...]:
    path = resources.files("pertura_bench").joinpath("cases/agent_workflow_cases.v1.json")
    payload = json.loads(path.read_text(encoding="utf-8"))
    return tuple(AgentWorkflowCase.model_validate(item) for item in payload["cases"])


def run_local_agent_matrix(output_root: Path) -> tuple[AgentWorkflowVerdict, ...]:
    output_root = Path(output_root).resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    verdicts = []
    for case in load_agent_cases():
        case_root = output_root / case.case_id
        case_root.mkdir(parents=True, exist_ok=True)
        try:
            observed = asyncio.run(_run_case(case, case_root))
            gates = tuple(
                AgentHardGateResult(
                    gate_id=gate,
                    passed=bool(observed.get(gate)),
                    detail=str(observed.get(f"{gate}_detail") or observed.get(gate)),
                )
                for gate in case.expected_hard_gates
            )
            failures = tuple(item.gate_id for item in gates if not item.passed)
            verdict = AgentWorkflowVerdict(
                case_id=case.case_id,
                case_hash=case.case_hash,
                status="failed" if failures else "passed",
                hard_gates=gates,
                output_hash=canonical_hash(observed),
                failure_reasons=failures,
            )
        except Exception as exc:
            verdict = AgentWorkflowVerdict(
                case_id=case.case_id,
                case_hash=case.case_hash,
                status="failed",
                hard_gates=(),
                failure_reasons=(f"{type(exc).__name__}: {exc}",),
            )
        (case_root / "execution_verdict.json").write_text(
            json.dumps(verdict.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        verdicts.append(verdict)
    return tuple(verdicts)


async def _run_case(case: AgentWorkflowCase, root: Path) -> dict[str, Any]:
    project = ProjectWorkspace.initialize(root / "project")
    run = project.create_run(logical_name=case.case_id)
    conversation = project.create_conversation(run.run_id)
    workspace = project.run_workspace(run.run_id)
    authority_root = root / "authority"
    previous = os.environ.get("PERTURA_AUTHORITY_ROOT")
    os.environ["PERTURA_AUTHORITY_ROOT"] = str(authority_root)
    runtime = PerturaProductRuntime(workspace, project_workspace=project, run_id=run.run_id)
    registry = DataAssetRegistry(project_id=project.project.project_id, store=project.store, object_root=project.objects_dir)
    try:
        if case.scenario == "project_h5ad":
            source = root / "fixture.h5ad"
            source.write_bytes(b"synthetic-h5ad-placeholder")
            asset = registry.register(source, role="primary_dataset", kind="observed")
            reopened = ProjectWorkspace.open(project.root)
            return {"project_persisted": reopened.project.project_id == project.project.project_id, "asset_registered": reopened.store.get_asset(asset.asset_id) is not None}
        if case.scenario in {"gmt_asset", "prediction_asset"}:
            suffix = ".gmt" if case.scenario == "gmt_asset" else ".npz"
            source = root / f"external{suffix}"
            source.write_bytes(b"fixture")
            role = "gene_modules" if case.scenario == "gmt_asset" else "prediction_bundle"
            asset = registry.register(source, role=role, kind="external_resource")
            if case.scenario == "gmt_asset":
                return {"asset_role_preserved": asset.role == role, "path_excluded_from_identity": str(root) not in asset.identity_hash}
            return {"prediction_class_preserved": asset.source_class == "prediction"}
        if case.scenario == "needs_input":
            manager = _manager(project, run.run_id, conversation.conversation_id)
            manager.begin("analyze without replicate metadata")
            final = await manager.finish(status=TurnStatus.needs_input, raw_output=_draft(questions=["Which column is the independent replicate?"]), resolve_result=lambda _: None)
            return {"needs_input_checkpoint": project.store.get_turn(final.turn_id).status == TurnStatus.needs_input, "no_strong_claim": not final.claim_authority}
        if case.scenario == "resume":
            first = _manager(project, run.run_id, conversation.conversation_id)
            first.begin("first message")
            first.bind_provider_session("claude-session-1")
            await first.finish(status=TurnStatus.completed, raw_output=_draft(), resolve_result=lambda _: None)
            second = _manager(project, run.run_id, conversation.conversation_id)
            resume = second.continuation_session_id()
            second.begin("second message")
            await second.finish(status=TurnStatus.completed, raw_output=_draft(), resolve_result=lambda _: None)
            turns = project.store.list_turns(conversation.conversation_id)
            return {"resume_binding_reused": resume == "claude-session-1", "history_not_duplicated": [item.user_input for item in turns] == ["first message", "second message"]}
        if case.scenario == "contract_stale":
            source = root / "expression.csv"
            source.write_text("cell_id,condition,G1\nc1,NTC,1\nc2,KLF1,2\n", encoding="utf-8")
            first_contract = runtime.inspect_dataset(source)
            first = runtime.finalize_report()
            runtime.confirm_design(first_contract["contract_id"], {"replicate": ["r1", "r2"]})
            second = runtime.finalize_report()
            return {"revision_changes_digest": first["report_digest"] != second["report_digest"] and second["revision"] == first["revision"] + 1}
        if case.scenario == "blocked_no_fallback":
            source = root / "expression.csv"
            source.write_text("cell_id,condition,G1\nc1,NTC,1\nc2,KLF1,2\n", encoding="utf-8")
            inspected = runtime.inspect_dataset(source)
            result = runtime.run_analysis("replicated low-MOI expression", contract_id=inspected["contract_id"])
            return {"blocked_without_result": result["status"] == "blocked" and result["result_id"] is None, "no_fallback": result.get("selected_capability") != "exploratory_normal_approximation"}
        if case.scenario == "candidate_language":
            manager = _manager(project, run.run_id, conversation.conversation_id)
            manager.begin("interpret candidate")
            candidate = {"result_id":"candidate","source_class":"measured_result","verification_state":"validated_untrusted","status":"completed","scope":{"scope_id":"s"}}
            final = await manager.finish(status=TurnStatus.completed, raw_output=_draft(findings=[{"finding_id":"f","text":"candidate estimate","declared_role":"measured","result_ids":["candidate"],"limitations":[]}]), resolve_result=lambda rid: candidate if rid == "candidate" else None)
            return {"candidate_ceiling": final.findings[0]["ceiling"] == "exploratory_measured", "no_strong_claim": not final.claim_authority}
        if case.scenario == "report_idempotent":
            first, second = runtime.finalize_report(), runtime.finalize_report()
            return {"same_revision": first["revision"] == second["revision"] and first["report_digest"] == second["report_digest"]}
        if case.scenario == "report_increment":
            first = runtime.finalize_report()
            manager = _manager(project, run.run_id, conversation.conversation_id)
            manager.begin("new interpretation")
            await manager.finish(status=TurnStatus.completed, raw_output=_draft(hypotheses=["new hypothesis"]), resolve_result=lambda _: None)
            second = runtime.finalize_report()
            return {"new_revision": second["revision"] == first["revision"] + 1}
        if case.scenario == "repair_fallback":
            manager = _manager(project, run.run_id, conversation.conversation_id)
            manager.begin("malformed")
            calls = 0
            async def repair(_raw: str, _error: str) -> str:
                nonlocal calls
                calls += 1
                return "still invalid"
            final = await manager.finish(status=TurnStatus.failed, raw_output="not json", resolve_result=lambda _: None, repair=repair)
            return {"repair_once": calls == 1, "fallback_no_authority": not final.structured and not final.claim_authority}
        if case.scenario == "cancel_recover":
            first = _manager(project, run.run_id, conversation.conversation_id)
            turn = first.begin("cancel me")
            await first.finish(status=TurnStatus.cancelled, raw_output="cancelled", resolve_result=lambda _: None)
            second = _manager(project, run.run_id, conversation.conversation_id)
            next_turn = second.begin("resume after cancel")
            await second.finish(status=TurnStatus.completed, raw_output=_draft(), resolve_result=lambda _: None)
            return {"cancel_checkpoint": project.store.get_turn(turn.turn_id).status == TurnStatus.cancelled, "lock_released": project.store.get_run(run.run_id).active_turn_id is None, "next_turn_started": next_turn.sequence == 2}
        raise ValueError(f"unknown scenario: {case.scenario}")
    finally:
        runtime.close()
        if previous is None:
            os.environ.pop("PERTURA_AUTHORITY_ROOT", None)
        else:
            os.environ["PERTURA_AUTHORITY_ROOT"] = previous


def _manager(project: ProjectWorkspace, run_id: str, conversation_id: str) -> TurnCheckpointManager:
    return TurnCheckpointManager(project=project, run_id=run_id, conversation_id=conversation_id, provider_id="fake-provider", model="fake", tool_hash="sha256:" + "1" * 64, skill_bundle_hash="sha256:" + "2" * 64, configuration_hash="sha256:" + "3" * 64)


def _draft(*, findings: list[dict[str, Any]] | None = None, hypotheses: list[str] | None = None, questions: list[str] | None = None) -> str:
    return json.dumps({"schema_version":"pertura-turn-draft-v1","language":"en","headline":"Checkpoint","findings":findings or [],"hypotheses":hypotheses or [],"limitations":[],"questions_for_user":questions or [],"next_steps":[],"artifact_refs":[]})
