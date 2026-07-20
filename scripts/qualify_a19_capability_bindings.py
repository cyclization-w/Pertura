from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping

from pertura_bench.capability_availability import (
    availability_by_task,
    build_task_capability_availability,
)
from pertura_bench.paper_agent_execution import run_paper_agent_workflow
from pertura_bench.paper_tasks import load_paper_task_catalog
from pertura_bench.task_submission import TaskSubmissionService
from pertura_core.hashing import canonical_hash, file_sha256
from pertura_runtime.project.models import TurnStatus
from pertura_workflow.capability_contracts import build_capability_contract_catalog

from qualify_a19_evaluators import (
    _fill_protocol_outputs,
    _materialize_generic_positive,
    _materialize_global_effect_positive,
    _materialize_trans_de_positive,
    _positive_result,
)


_TASK_PATTERN = re.compile(r"task ([A-Z]+-[0-9]+) \(turn")


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def _task_map(catalog: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    for workflow in catalog.get("workflows") or ():
        for raw in workflow.get("turns") or ():
            task = dict(raw)
            task["dataset_id"] = str(workflow["dataset_id"])
            task["workflow_id"] = str(workflow["workflow_id"])
            records[str(task["task_id"])] = task
    return records


def _materialize_submission(
    *,
    task: Mapping[str, Any],
    reference_binding: Mapping[str, Any],
    output: Path,
    paper_root: Path,
) -> dict[str, Any]:
    evaluator_id = str(reference_binding.get("evaluator_id") or "")
    if evaluator_id == "task.trans_de_edger.v1":
        _materialize_trans_de_positive(reference_binding, output, paper_root)
    elif evaluator_id == "task.global_effect_claims.v1":
        _materialize_global_effect_positive(reference_binding, output, paper_root)
    elif reference_binding.get("evaluators"):
        _materialize_generic_positive(reference_binding, output, paper_root)
    _fill_protocol_outputs(task, reference_binding, output)

    # Protocol-only endpoints can lack evaluator-backed files.  Their frozen
    # artifact schemas are sufficient for this execution qualification because
    # the qualification measures binding executability, not scientific scores.
    contract = dict(task.get("output_contract") or {})
    schemas = dict(contract.get("artifact_schemas") or {})
    for relative in (contract.get("artifact_paths") or {}).values():
        path = output / str(relative)
        if path.exists():
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.suffix.lower() == ".json":
            path.write_text(
                json.dumps(
                    {
                        "schema_version": "pertura-binding-qualification-artifact-v1",
                        "qualification_only": True,
                    },
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
        else:
            columns = list(schemas.get(str(relative)) or ("qualification_id",))
            path.write_text("\t".join(columns) + "\n", encoding="utf-8")

    result = _positive_result(task, reference_binding)
    result["dataset_id"] = str(task["dataset_id"])
    result["result_type"] = "capability_binding_qualification"
    result["limitations"] = [
        "Internal execution qualification; not a scientific benchmark result."
    ]
    return result


class _BindingQualificationExecutor:
    def __init__(
        self,
        *,
        tasks: Mapping[str, Mapping[str, Any]],
        references: Mapping[str, Mapping[str, Any]],
        paper_root: Path,
    ) -> None:
        self.tasks = tasks
        self.references = references
        self.paper_root = paper_root
        self.records: list[dict[str, Any]] = []

    def __call__(self, agent, prompt: str, timeout: int):
        del timeout
        match = _TASK_PATTERN.search(prompt)
        if not match:
            raise RuntimeError("qualification callback could not identify the task")
        task_id = match.group(1)
        task = dict(self.tasks[task_id])
        expected_probes = {
            str(item)
            for item in task.get("expected_probe_capabilities") or ()
        }
        runtime = agent.product_runtime
        project = runtime.project_workspace
        if project is None:
            raise RuntimeError("binding qualification requires a project workspace")
        turn = project.store.begin_turn(agent.conversation_id, prompt)
        result_ids: list[str] = []
        task_records: list[dict[str, Any]] = []
        try:
            for binding in runtime._invocation_bindings.values():
                try:
                    if binding.tool_name == "run_diagnostic":
                        response = runtime.run_diagnostic(
                            binding_id=binding.binding_id
                        )
                    elif binding.tool_name == "run_analysis":
                        response = runtime.run_analysis(
                            f"Qualify {binding.capability_id} through its frozen binding",
                            binding_id=binding.binding_id,
                        )
                    elif binding.tool_name == "evaluate_virtual_model":
                        response = runtime.evaluate_virtual_model(
                            binding_id=binding.binding_id
                        )
                    else:
                        raise RuntimeError(
                            f"unsupported bound tool: {binding.tool_name}"
                        )
                except Exception as exc:
                    raise RuntimeError(
                        f"{task_id}/{binding.capability_id}/"
                        f"{binding.binding_id}: bound execution failed: {exc}"
                    ) from exc

                result_id = str(response.get("result_id") or "")
                if binding.readiness == "blocked_probe":
                    if binding.capability_id not in expected_probes:
                        raise RuntimeError(
                            f"{task_id}/{binding.capability_id}: unexpected "
                            "preflight blocker on an advertised non-probe binding: "
                            f"{binding.blockers}"
                        )
                    if response.get("status") != "blocked" or result_id:
                        raise RuntimeError(
                            f"{task_id}/{binding.capability_id}: blocked probe executed"
                        )
                    qualification_status = "expected_blocked_probe"
                    result_hash = None
                    output_hashes: Mapping[str, str] = {}
                else:
                    if not result_id:
                        raise RuntimeError(
                            f"{task_id}/{binding.capability_id}: ready binding did not "
                            "produce a committed result; "
                            f"status={response.get('status')}, "
                            f"blockers={response.get('blockers')}"
                        )
                    result = next(
                        (
                            item
                            for item in runtime.planning_material(
                                binding.contract_id
                            )[1]
                            if item.result_id == result_id
                        ),
                        None,
                    )
                    if result is None:
                        raise RuntimeError(
                            f"{task_id}/{binding.capability_id}: result was not committed"
                        )
                    if result.capability_trust.value == "exploratory":
                        if response.get("receipt_id") is not None:
                            raise RuntimeError(
                                f"{task_id}/{binding.capability_id}: exploratory result "
                                "incorrectly received a trusted receipt"
                            )
                    elif not response.get("receipt_id"):
                        raise RuntimeError(
                            f"{task_id}/{binding.capability_id}: trusted result lacks receipt"
                        )
                    result_ids.append(result_id)
                    qualification_status = "executed"
                    result_hash = result.canonical_hash
                    output_hashes = dict(result.output_hashes)

                task_records.append(
                    {
                        "task_id": task_id,
                        "binding_id": binding.binding_id,
                        "binding_hash": binding.binding_hash,
                        "capability_id": binding.capability_id,
                        "capability_scientific_hash": (
                            binding.capability_scientific_hash
                        ),
                        "tool_name": binding.tool_name,
                        "contract_id": binding.contract_id,
                        "contract_hash": binding.contract_hash,
                        "scope": dict(binding.scope),
                        "bound_parameters_hash": canonical_hash(
                            binding.bound_parameters
                        ),
                        "input_assets": [
                            item.model_dump(mode="json")
                            for item in binding.input_assets
                        ],
                        "dependency_result_ids": list(
                            binding.dependency_result_ids
                        ),
                        "dependency_verification_states": list(
                            binding.dependency_verification_states
                        ),
                        "dependency_receipt_ids": list(
                            binding.dependency_receipt_ids
                        ),
                        "dependency_binding_ids": list(
                            binding.dependency_binding_ids
                        ),
                        "output_mapping": dict(binding.output_mapping),
                        "readiness": binding.readiness,
                        "qualification_status": qualification_status,
                        "result_id": result_id or None,
                        "result_hash": result_hash,
                        "receipt_id": response.get("receipt_id"),
                        "result_status": response.get("status"),
                        "output_hashes": output_hashes,
                    }
                )

            output = (
                agent.workspace.root / "outputs" / "tasks" / task_id
            )
            output.mkdir(parents=True, exist_ok=True)
            result = _materialize_submission(
                task=task,
                reference_binding=self.references.get(task_id, {}),
                output=output,
                paper_root=self.paper_root,
            )
            allowed_units = tuple(
                str(item)
                for item in (
                    (task.get("output_contract") or {}).get(
                        "allowed_analysis_units"
                    )
                    or ()
                )
            )
            service = TaskSubmissionService(agent.workspace.root)
            service.bind_task(
                task_id=task_id,
                dataset_id=str(task["dataset_id"]),
                allowed_analysis_units=allowed_units,
            )
            accepted = service.submit_task_bundle(
                {
                    "benchmark_result": result,
                    "turn_draft": {
                        "schema_version": "pertura-turn-draft-v1",
                        "headline": f"Qualified bound capability surface for {task_id}",
                        "limitations": [
                            "Internal execution qualification; no scientific claim."
                        ],
                    },
                }
            )
            if accepted.get("accepted") is not True:
                raise RuntimeError(
                    f"{task_id}: qualification submission failed: "
                    f"{accepted.get('errors')}"
                )
            project.store.complete_turn(
                turn.turn_id,
                status=TurnStatus.completed,
                provider_final="binding qualification completed",
                result_ids=tuple(result_ids),
                trace={"binding_qualification": True},
            )
            self.records.extend(task_records)
            agent.manifest = SimpleNamespace(
                result_subtype="success",
                num_turns=1,
                message_count=1,
                total_cost_usd=0.0,
            )
            return SimpleNamespace(
                status="completed", error=None, result_subtype="success"
            )
        except Exception:
            project.store.complete_turn(
                turn.turn_id,
                status=TurnStatus.failed,
                provider_final=None,
                result_ids=tuple(result_ids),
                trace={"binding_qualification": True, "failed": True},
            )
            raise


def qualify(
    *,
    repo: Path,
    wheel: Path,
    task_catalog_path: Path,
    task_reference_catalog_path: Path,
    paper_anchor_catalog_path: Path,
    asset_catalog_path: Path,
    capability_contract_catalog_path: Path,
    paper_root: Path,
    cache: Path,
    resource_lock_path: Path,
    work_root: Path,
) -> dict[str, Any]:
    raw_catalog = _read_json(task_catalog_path)
    loaded_catalog = load_paper_task_catalog(task_catalog_path)
    tasks = _task_map(raw_catalog)
    references_payload = _read_json(task_reference_catalog_path)
    references = {
        str(item["task_id"]): item
        for item in references_payload.get("bindings") or ()
    }
    availability = availability_by_task(
        build_task_capability_availability(
            raw_catalog, build_capability_contract_catalog()
        )
    )
    configured_tasks = {
        task_id
        for task_id, record in availability.items()
        if record["advertised_capability_ids"]
        and tasks[task_id].get("role") != "optional"
    }

    records: list[dict[str, Any]] = []
    workflow_runs: list[dict[str, Any]] = []
    work_root.mkdir(parents=True, exist_ok=True)
    for workflow in loaded_catalog.workflows:
        workflow_id = str(workflow["workflow_id"])
        if not any(
            str(task["task_id"]) in configured_tasks
            for task in workflow.get("turns") or ()
        ):
            continue
        workflow_work = work_root / workflow_id
        workflow_work.mkdir(parents=True, exist_ok=True)
        memory_gb = 48 if workflow_id == "WF-REPL" else 32
        resource_path = workflow_work / "resource-evidence.json"
        resource_path.write_text(
            json.dumps(
                {
                    "schema_version": "pertura-resource-evidence-v1",
                    "mode": "scheduler",
                    "scheduler_job_id": (
                        os.environ.get("SLURM_JOB_ID")
                        or "a19-binding-qualification"
                    ),
                    "requested_memory_gb": memory_gb,
                    "actual_memory_gb": memory_gb,
                    "cpu_count": 1,
                    "n_jobs": 1,
                    "timeout_seconds": 14400,
                    "peak_rss_mb": 0,
                    "wall_clock_seconds": 0,
                    "thread_environment": {
                        "OMP_NUM_THREADS": "1",
                        "OPENBLAS_NUM_THREADS": "1",
                        "MKL_NUM_THREADS": "1",
                        "NUMEXPR_NUM_THREADS": "1",
                    },
                },
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        executor = _BindingQualificationExecutor(
            tasks=tasks,
            references=references,
            paper_root=paper_root,
        )
        workflow_result = run_paper_agent_workflow(
            workflow_id,
            repo_root=repo,
            cache=cache,
            paper_root=paper_root,
            output=workflow_work / "runs",
            condition="pertura_full",
            repeat_index=1,
            task_catalog_path=task_catalog_path,
            task_reference_catalog_path=task_reference_catalog_path,
            paper_anchor_catalog_path=paper_anchor_catalog_path,
            asset_catalog_path=asset_catalog_path,
            capability_contract_catalog_path=capability_contract_catalog_path,
            resource_evidence_path=resource_path,
            turn_executor=executor,
            verify_checkpoint=False,
        )
        records.extend(executor.records)
        workflow_runs.append(
            {
                "workflow_id": workflow_id,
                "analysis_run_id": workflow_result["analysis_run_id"],
                "execution_status": workflow_result["execution_status"],
                "record_count": len(executor.records),
            }
        )

    expected_binding_count = sum(
        len(availability[task_id]["advertised_capability_ids"])
        for task_id in configured_tasks
    )
    expected_pairs = {
        (task_id, capability_id)
        for task_id in configured_tasks
        for capability_id in availability[task_id][
            "advertised_capability_ids"
        ]
    }
    observed_pairs = [
        (str(item["task_id"]), str(item["capability_id"]))
        for item in records
    ]
    if len(records) != expected_binding_count:
        raise RuntimeError(
            "binding qualification coverage mismatch: "
            f"expected {expected_binding_count}, observed {len(records)}"
        )
    if len(set(observed_pairs)) != len(observed_pairs) or set(
        observed_pairs
    ) != expected_pairs:
        raise RuntimeError(
            "binding qualification task/capability coverage drifted: "
            f"missing={sorted(expected_pairs - set(observed_pairs))}, "
            f"extra={sorted(set(observed_pairs) - expected_pairs)}"
        )
    if any(
        item["qualification_status"]
        not in {"executed", "expected_blocked_probe"}
        for item in records
    ):
        raise RuntimeError("one or more capability bindings were not qualified")

    commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repo, text=True
    ).strip()
    payload = {
        "schema_version": "pertura-capability-binding-qualification-v1",
        "status": "passed",
        "passed": True,
        "git_commit": commit,
        "wheel_sha256": file_sha256(wheel),
        "task_catalog_sha256": file_sha256(task_catalog_path),
        "task_reference_catalog_sha256": file_sha256(
            task_reference_catalog_path
        ),
        "paper_asset_catalog_sha256": file_sha256(asset_catalog_path),
        "capability_contract_catalog_sha256": file_sha256(
            capability_contract_catalog_path
        ),
        "resource_lock_sha256": file_sha256(resource_lock_path),
        "qualified_task_count": len(
            {str(item["task_id"]) for item in records}
        ),
        "qualified_binding_count": len(records),
        "expected_blocked_probe_count": sum(
            item["qualification_status"] == "expected_blocked_probe"
            for item in records
        ),
        "optional_unconfigured_task_ids": sorted(
            task_id
            for task_id, task in tasks.items()
            if task.get("role") == "optional"
        ),
        "workflow_runs": workflow_runs,
        "records": records,
    }
    payload["canonical_hash"] = canonical_hash(payload)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--wheel", type=Path, required=True)
    parser.add_argument("--task-catalog", type=Path, required=True)
    parser.add_argument("--task-reference-catalog", type=Path, required=True)
    parser.add_argument("--paper-anchor-catalog", type=Path, required=True)
    parser.add_argument("--asset-catalog", type=Path, required=True)
    parser.add_argument("--capability-contract-catalog", type=Path, required=True)
    parser.add_argument("--paper-root", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--resource-lock", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--work-root", type=Path)
    args = parser.parse_args()

    if args.work_root is None:
        temporary_parent = args.paper_root.parent / "tmp"
        temporary_parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="a19-binding-qualification-",
            dir=temporary_parent,
        ) as temporary:
            payload = qualify(
                repo=args.repo.resolve(),
                wheel=args.wheel.resolve(),
                task_catalog_path=args.task_catalog.resolve(),
                task_reference_catalog_path=args.task_reference_catalog.resolve(),
                paper_anchor_catalog_path=args.paper_anchor_catalog.resolve(),
                asset_catalog_path=args.asset_catalog.resolve(),
                capability_contract_catalog_path=(
                    args.capability_contract_catalog.resolve()
                ),
                paper_root=args.paper_root.resolve(),
                cache=args.cache.resolve(),
                resource_lock_path=args.resource_lock.resolve(),
                work_root=Path(temporary),
            )
    else:
        payload = qualify(
            repo=args.repo.resolve(),
            wheel=args.wheel.resolve(),
            task_catalog_path=args.task_catalog.resolve(),
            task_reference_catalog_path=args.task_reference_catalog.resolve(),
            paper_anchor_catalog_path=args.paper_anchor_catalog.resolve(),
            asset_catalog_path=args.asset_catalog.resolve(),
            capability_contract_catalog_path=args.capability_contract_catalog.resolve(),
            paper_root=args.paper_root.resolve(),
            cache=args.cache.resolve(),
            resource_lock_path=args.resource_lock.resolve(),
            work_root=args.work_root.resolve(),
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
