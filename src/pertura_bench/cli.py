from __future__ import annotations

import argparse
import json
from pathlib import Path
from pertura_core.hashing import canonical_hash, file_sha256

from pertura_bench.models import BenchmarkSubsetSpec
from pertura_bench.operations import (
    fetch_benchmark,
    require_repo_root,
    run_conversion,
    source_manifests,
    subset_h5ad,
    validate_repository,
    write_annotation_packet,
)
from pertura_bench.release_gate import audit_v020


_TIERS = ("unit", "synthetic_ci", "frozen_subset", "full_dataset")


def _repo(value: Path) -> Path:
    try:
        return require_repo_root(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def _contains_failed_verdict(value: object) -> bool:
    if isinstance(value, dict):
        if value.get("outcome") == "failed":
            return True
        return any(_contains_failed_verdict(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_contains_failed_verdict(item) for item in value)
    return False


def _exit_code(command: str, payload: object) -> int:
    if command == "validate" and isinstance(payload, dict):
        return 0 if payload.get("valid") is True else 1
    if command == "validate-cases" and isinstance(payload, dict):
        return 0 if payload.get("ok") is True else 1
    if command == "skills" and isinstance(payload, dict):
        ready = payload.get("ok", payload.get("skill_bundle_ready"))
        return 0 if ready is True else 1
    if command == "run-matrix" and isinstance(payload, dict) and "ready" in payload:
        return 0 if payload.get("ready") is True else 1
    if command == "agent" and isinstance(payload, dict) and "ready" in payload:
        return 0 if payload.get("ready") is True else 1
    if (
        command == "agent"
        and isinstance(payload, dict)
        and payload.get("schema_version")
        == "pertura-paper-workflow-execution-v1"
    ):
        return 0 if payload.get("execution_status") == "completed" else 1
    if command == "agent" and isinstance(payload, dict) and payload.get(
        "status"
    ) in {"failed", "not_available", "judge_unavailable"}:
        return 1
    if command in {"run", "run-matrix"}:
        if not payload or _contains_failed_verdict(payload):
            return 1
    return 0

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m pertura_bench")
    sub = parser.add_subparsers(dest="command", required=True)

    validate = sub.add_parser("validate")
    validate.add_argument("--repo", type=Path, default=Path.cwd())
    status = sub.add_parser("status")
    status.add_argument("--repo", type=Path, default=Path.cwd())

    fetch = sub.add_parser("fetch")
    fetch.add_argument("dataset_id")
    fetch.add_argument("--cache", type=Path, required=True)
    fetch.add_argument("--repo", type=Path, default=Path.cwd())

    annotation = sub.add_parser("annotation-packet")
    annotation.add_argument("--modality", choices=("crispri", "crispra"), required=True)
    annotation.add_argument("--output", type=Path, default=Path("benchmarks/annotations"))
    annotation.add_argument("--repo", type=Path, default=Path.cwd())

    convert = sub.add_parser("convert")
    convert.add_argument("dataset_id")
    convert.add_argument("--cache", type=Path, required=True)
    convert.add_argument("--repo", type=Path, default=Path.cwd())
    convert.add_argument("--rscript", default="Rscript")

    subset = sub.add_parser("subset")
    subset.add_argument("dataset_id")
    subset.add_argument("--split", choices=("calibration", "evaluation"), required=True)
    subset.add_argument("--cache", type=Path, required=True)
    subset.add_argument("--repo", type=Path, default=Path.cwd())
    subset.add_argument("--input", type=Path)
    subset.add_argument("--output", type=Path)
    subset.add_argument("--source-lock-hash")
    subset.add_argument("--label-column")
    subset.add_argument("--labels", nargs="+")
    subset.add_argument("--from-lock-chain", action="store_true")

    edger_golden = sub.add_parser("edger-golden")
    edger_golden.add_argument("--environment", default="edger-v1")
    edger_golden.add_argument("--repo", type=Path, default=Path.cwd())

    capabilities = sub.add_parser("capabilities")
    capability_sub = capabilities.add_subparsers(
        dest="capability_command", required=True
    )
    capability_sub.add_parser("list")
    matrix = capability_sub.add_parser("matrix")
    matrix.add_argument("--repo", type=Path, default=Path.cwd())

    skills = sub.add_parser("skills")
    skill_sub = skills.add_subparsers(dest="skill_command", required=True)
    skill_validate = skill_sub.add_parser("validate")
    skill_validate.add_argument("--repo", type=Path, default=Path.cwd())
    skill_matrix = skill_sub.add_parser("matrix")
    skill_matrix.add_argument("--repo", type=Path, default=Path.cwd())

    agent = sub.add_parser("agent", help="Run the provider-neutral agent workflow benchmark.")
    agent_sub = agent.add_subparsers(dest="agent_command", required=True)
    agent_sub.add_parser("list")
    agent_run = agent_sub.add_parser("run-local")
    agent_run.add_argument("--output", type=Path, required=True)
    agent_server = agent_sub.add_parser("run-server")
    agent_server.add_argument("case_id")
    agent_server.add_argument("--cache", type=Path, required=True)
    agent_server.add_argument("--output", type=Path, required=True)
    agent_server.add_argument("--repo", type=Path, default=Path.cwd())
    agent_server.add_argument(
        "--condition",
        choices=("pertura_full", "prompt_only", "free_codeact"),
        required=True,
    )
    agent_server.add_argument("--repeat-index", type=int, choices=(1, 2), required=True)
    agent_server.add_argument("--parameter-catalog", type=Path)
    agent_server.add_argument("--design-confirmations", type=Path)
    agent_server.add_argument("--metric-reference-catalog", type=Path)
    agent_server.add_argument(
        "--resource-enforcement",
        choices=("scheduler", "cgroup", "unverified"),
        default="unverified",
    )
    agent_server.add_argument("--enforced-memory-gb", type=float)
    agent_server.add_argument("--enforced-n-jobs", type=int)
    agent_server.add_argument("--resource-evidence", type=Path)
    paper_workflow = agent_sub.add_parser("run-paper-workflow")
    paper_workflow.add_argument("workflow_id")
    paper_workflow.add_argument("--cache", type=Path, required=True)
    paper_workflow.add_argument("--paper-root", type=Path, required=True)
    paper_workflow.add_argument("--output", type=Path, required=True)
    paper_workflow.add_argument("--repo", type=Path, default=Path.cwd())
    paper_workflow.add_argument(
        "--condition",
        choices=("pertura_full", "prompt_only", "free_codeact"),
        required=True,
    )
    paper_workflow.add_argument(
        "--repeat-index", type=int, choices=(1, 2), required=True
    )
    paper_workflow.add_argument("--task-catalog", type=Path, required=True)
    paper_workflow.add_argument(
        "--task-reference-catalog", type=Path, required=True
    )
    paper_workflow.add_argument(
        "--paper-anchor-catalog", type=Path, required=True
    )
    paper_workflow.add_argument("--asset-catalog", type=Path, required=True)
    paper_workflow.add_argument("--resource-evidence", type=Path, required=True)
    agent_regrade = agent_sub.add_parser("regrade")
    agent_regrade.add_argument("execution_root", type=Path)
    paper_regrade = agent_sub.add_parser("regrade-paper-workflow")
    paper_regrade.add_argument("execution_root", type=Path)
    paper_list = agent_sub.add_parser("paper-workflows")
    paper_list.add_argument("--task-catalog", type=Path, required=True)
    agent_sub.add_parser("server-cases")
    agent_run.add_argument("--write-frozen-verdicts", action="store_true")

    agent_run.add_argument("--repo", type=Path, default=Path.cwd())
    cases = sub.add_parser("validate-cases")
    cases.add_argument("--repo", type=Path, default=Path.cwd())

    run = sub.add_parser("run")
    run.add_argument("capability_id")
    run.add_argument("--tier", choices=_TIERS, default="synthetic_ci")
    run.add_argument("--dataset")
    run.add_argument("--split", choices=("calibration", "evaluation"))
    run.add_argument("--cache", type=Path)
    run.add_argument("--output", type=Path)
    run.add_argument("--repo", type=Path, default=Path.cwd())
    run.add_argument("--parameter-catalog", type=Path)
    run.add_argument("--design-confirmations", type=Path)
    run.add_argument("--metric-reference-catalog", type=Path)
    run.add_argument("--resource-evidence", type=Path)
    run.add_argument("--enforced-memory-gb", type=float)
    run.add_argument("--enforced-n-jobs", type=int)

    run_matrix = sub.add_parser("run-matrix")
    run_matrix.add_argument("--tier", choices=_TIERS, default="synthetic_ci")
    run_matrix.add_argument("--dataset")
    run_matrix.add_argument("--split", choices=("calibration", "evaluation"))
    run_matrix.add_argument("--cache", type=Path)
    run_matrix.add_argument("--output", type=Path)
    run_matrix.add_argument("--repo", type=Path, default=Path.cwd())
    run_matrix.add_argument("--parameter-catalog", type=Path)
    run_matrix.add_argument("--design-confirmations", type=Path)
    run_matrix.add_argument("--metric-reference-catalog", type=Path)
    run_matrix.add_argument("--resource-evidence", type=Path)
    run_matrix.add_argument("--enforced-memory-gb", type=float)
    run_matrix.add_argument("--enforced-n-jobs", type=int)
    run_matrix.add_argument(
        "--write-frozen-synthetic-verdicts", action="store_true"
    )

    server = sub.add_parser("export-server-plan")
    server.add_argument("--output", type=Path, required=True)
    server.add_argument("--repo", type=Path, default=Path.cwd())
    server.add_argument("--parameter-catalog", type=Path)
    server.add_argument("--design-confirmations", type=Path)
    server.add_argument("--metric-reference-catalog", type=Path)
    server.add_argument("--paper-task-catalog", type=Path)
    server.add_argument("--paper-task-reference-catalog", type=Path)
    server.add_argument("--paper-anchor-catalog", type=Path)
    server.add_argument("--paper-asset-catalog", type=Path)

    bind_plan = sub.add_parser("bind-server-plan")
    bind_plan.add_argument("--template", type=Path, required=True)
    bind_plan.add_argument("--git-commit", required=True)
    bind_plan.add_argument("--wheel", type=Path, required=True)
    bind_plan.add_argument("--resource-lock-manifest", type=Path, required=True)
    bind_plan.add_argument("--prediction-lock-manifest", type=Path)
    bind_plan.add_argument("--subset-catalog", type=Path, required=True)
    bind_plan.add_argument("--paper-task-catalog", type=Path)
    bind_plan.add_argument("--paper-task-reference-catalog", type=Path)
    bind_plan.add_argument("--paper-anchor-catalog", type=Path)
    bind_plan.add_argument("--paper-asset-catalog", type=Path)
    bind_plan.add_argument("--output", type=Path, required=True)

    references = sub.add_parser("references")
    reference_sub = references.add_subparsers(dest="reference_command", required=True)
    reference_generate = reference_sub.add_parser("generate")
    reference_generate.add_argument("--dataset", required=True)
    reference_generate.add_argument("--split", choices=("calibration", "evaluation"), required=True)
    reference_generate.add_argument("--subset-lock", type=Path, required=True)
    reference_generate.add_argument("--generator-script", type=Path, required=True)
    reference_generate.add_argument("--environment-lock", type=Path, required=True)
    reference_generate.add_argument("--parameters", type=Path, required=True)
    reference_generate.add_argument("--output", type=Path, required=True)
    reference_validate = reference_sub.add_parser("validate")
    reference_validate.add_argument("root", type=Path)
    reference_freeze = reference_sub.add_parser("freeze")
    reference_freeze.add_argument("root", type=Path)
    reference_freeze.add_argument("--output", type=Path, required=True)

    args, extra = parser.parse_known_args(argv)
    commands_with_repo = {
        "validate",
        "status",
        "fetch",
        "annotation-packet",
        "convert",
        "subset",
        "validate-cases",
        "run",
        "run-matrix",
        "export-server-plan",
        "edger-golden",
        "skills",
    }
    repo = None
    if args.command == "agent" and args.agent_command == "run-local":
        commands_with_repo.add("agent")
    if args.command in commands_with_repo or (
        args.command == "capabilities" and args.capability_command == "matrix"
    ):
        try:
            repo = require_repo_root(args.repo)
        except ValueError as exc:
            parser.error(str(exc))

    if args.command == "validate":
        payload = validate_repository(repo)
    elif args.command == "status":
        payload = audit_v020(repo)
    elif args.command == "fetch":
        manifests = source_manifests(repo)
        if args.dataset_id not in manifests:
            parser.error(f"unknown benchmark dataset: {args.dataset_id}")
        lock, path = fetch_benchmark(manifests[args.dataset_id][1], args.cache)
        payload = {"lock": lock.model_dump(mode="json"), "local_path": str(path)}
    elif args.command == "annotation-packet":
        output = args.output if args.output.is_absolute() else repo / args.output
        payload = write_annotation_packet(args.modality, output)
    elif args.command == "convert":
        manifests = source_manifests(repo)
        if args.dataset_id not in manifests:
            parser.error(f"unknown benchmark dataset: {args.dataset_id}")
        lock, path = run_conversion(
            manifests[args.dataset_id][1],
            repo_root=repo,
            cache=args.cache,
            rscript=args.rscript,
        )
        payload = {"lock": lock.model_dump(mode="json"), "local_path": str(path)}
    elif args.command == "subset":
        persist_lock_chain = bool(args.from_lock_chain)
        if args.from_lock_chain:
            from pertura_bench.real_execution import resolve_real_artifact_chain

            spec_path = (
                repo
                / "benchmarks"
                / "subsets"
                / f"{args.dataset_id}.{args.split}.json"
            )
            if not spec_path.is_file():
                parser.error(
                    "versioned subset spec is missing: "
                    + str(spec_path.relative_to(repo))
                )
            raw = json.loads(spec_path.read_text(encoding="utf-8"))
            input_path, lock_hashes = resolve_real_artifact_chain(
                repo,
                dataset_id=args.dataset_id,
                tier="full_dataset",
                split=args.split,
                cache=args.cache,
            )
            subset_root = (
                args.cache.resolve()
                / "datasets"
                / args.dataset_id
                / "subset"
                / args.split
            )
            output_path = subset_root / "artifact.h5ad"
            spec_payload = dict(raw.get("spec") or raw)
            for volatile in (
                "input",
                "output",
                "source_lock_hash",
                "subset_spec_id",
                "canonical_hash",
            ):
                spec_payload.pop(volatile, None)
            spec_payload.update(
                {
                    "dataset_id": args.dataset_id,
                    "split": args.split,
                    "source_lock_hash": lock_hashes["artifact_lock"],
                }
            )
            subset_spec = BenchmarkSubsetSpec.model_validate(spec_payload)
        else:
            missing = [
                name
                for name in (
                    "input",
                    "output",
                    "source_lock_hash",
                    "label_column",
                    "labels",
                )
                if getattr(args, name) in (None, [])
            ]
            if missing:
                parser.error(
                    "explicit subset execution is missing: " + ", ".join(missing)
                )
            input_path = args.input
            output_path = args.output
            subset_spec = BenchmarkSubsetSpec(
                schema_version="pertura-benchmark-subset-spec-v1",
                dataset_id=args.dataset_id,
                source_lock_hash=args.source_lock_hash,
                split=args.split,
                label_column=args.label_column,
                labels=tuple(args.labels),
            )
        lock = subset_h5ad(input_path, output_path, subset_spec)
        if persist_lock_chain:
            subset_root.mkdir(parents=True, exist_ok=True)
            (subset_root / "subset.lock.json").write_text(
                json.dumps(lock.model_dump(mode="json"), indent=2, sort_keys=True)
                + "\n",
                encoding="utf-8",
            )
            (subset_root / "subset.local.json").write_text(
                json.dumps(
                    {
                        "artifact_path": str(Path(output_path).resolve()),
                        "lock_id": lock.subset_lock_id,
                    },
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
            payload = {
                "lock": lock.model_dump(mode="json"),
                "local_path": str(Path(output_path).resolve()),
            }
        else:
            payload = lock.model_dump(mode="json")
    elif args.command == "capabilities":
        from pertura_bench.capability_bench import (
            CANDIDATE_CAPABILITIES,
            coverage_matrix,
        )

        payload = (
            {"capabilities": list(CANDIDATE_CAPABILITIES)}
            if args.capability_command == "list"
            else coverage_matrix(repo).model_dump(mode="json")
        )
    elif args.command == "skills":
        from pertura_bench.skill_bench import (
            skill_benchmark_matrix,
            validate_skill_bundle_static,
        )

        payload = (
            validate_skill_bundle_static(repo)
            if args.skill_command == "validate"
            else skill_benchmark_matrix(repo)
        )
    elif args.command == "agent":
        from importlib import resources
        from pertura_bench.agent_execution import (
            agent_execution_bundle_hash, load_agent_cases, run_local_agent_matrix,
        )
        if args.agent_command == "list":
            payload = {"cases": [item.model_dump(mode="json") for item in load_agent_cases()]}
        elif args.agent_command == "run-local":
            verdicts = run_local_agent_matrix(args.output)
            payload = {
                "benchmark": "agent_workflow",
                "ready": all(item.status == "passed" for item in verdicts),
                "case_catalog_hash": canonical_hash([item.model_dump(mode="json") for item in load_agent_cases()]),
                "execution_bundle_hash": agent_execution_bundle_hash(repo),
                "verdicts": [item.model_dump(mode="json") for item in verdicts],
            }
            if args.write_frozen_verdicts:
                if not payload["ready"]:
                    parser.error("refusing to freeze agent verdicts because one or more cases failed")
                frozen = {
                    "schema_version": "pertura-local-agent-workflow-verdicts-v1",
                    "case_catalog_hash": payload["case_catalog_hash"],
                    "execution_bundle_hash": payload["execution_bundle_hash"],
                    "ready": True,
                    "verdicts": payload["verdicts"],
                }
                destination = repo / "src/pertura_bench/cases/agent_workflow_verdicts.v1.json"
                with destination.open(
                    "w", encoding="utf-8", newline="\n"
                ) as handle:
                    handle.write(json.dumps(frozen, indent=2, sort_keys=True) + "\n")
                payload["frozen_verdict_path"] = str(destination)
        elif args.agent_command == "run-server":
            from pertura_bench.agent_server_execution import run_server_agent_case
            try:
                agent_repo = require_repo_root(args.repo)
            except ValueError as exc:
                parser.error(str(exc))
            payload = run_server_agent_case(
                args.case_id,
                repo_root=agent_repo,
                cache=args.cache,
                output=args.output,
                condition=args.condition,
                repeat_index=args.repeat_index,
                parameter_catalog_path=args.parameter_catalog,
                design_confirmations_path=args.design_confirmations,
                metric_reference_catalog_path=args.metric_reference_catalog,
                resource_enforcement=args.resource_enforcement,
                enforced_memory_gb=args.enforced_memory_gb,
                enforced_n_jobs=args.enforced_n_jobs,
                resource_evidence_path=args.resource_evidence,
            )
        elif args.agent_command == "run-paper-workflow":
            from pertura_bench.paper_agent_execution import (
                run_paper_agent_workflow,
            )

            try:
                agent_repo = require_repo_root(args.repo)
            except ValueError as exc:
                parser.error(str(exc))
            payload = run_paper_agent_workflow(
                args.workflow_id,
                repo_root=agent_repo,
                cache=args.cache,
                paper_root=args.paper_root,
                output=args.output,
                condition=args.condition,
                repeat_index=args.repeat_index,
                task_catalog_path=args.task_catalog,
                task_reference_catalog_path=args.task_reference_catalog,
                paper_anchor_catalog_path=args.paper_anchor_catalog,
                asset_catalog_path=args.asset_catalog,
                resource_evidence_path=args.resource_evidence,
            )
        elif args.agent_command == "regrade":
            from pertura_bench.agent_server_execution import regrade_server_agent_case
            payload = regrade_server_agent_case(args.execution_root)
        elif args.agent_command == "regrade-paper-workflow":
            from pertura_bench.paper_agent_execution import (
                regrade_paper_agent_workflow,
            )

            payload = regrade_paper_agent_workflow(args.execution_root)
        elif args.agent_command == "paper-workflows":
            from pertura_bench.paper_tasks import load_paper_task_catalog

            catalog = load_paper_task_catalog(args.task_catalog)
            payload = {
                "schema_version": catalog.payload["schema_version"],
                "catalog_sha256": catalog.sha256,
                "workflows": [
                    {
                        "workflow_id": item["workflow_id"],
                        "dataset_id": item["dataset_id"],
                        "role": item["role"],
                        "task_ids": [
                            task["task_id"] for task in item.get("turns") or ()
                        ],
                    }
                    for item in catalog.workflows
                ],
            }
        else:
            path = resources.files("pertura_bench").joinpath("cases/server_agent_cases.v1.json")
            payload = json.loads(path.read_text(encoding="utf-8"))
    elif args.command == "validate-cases":
        from pertura_bench.capability_bench import validate_cases

        payload = validate_cases()
    elif args.command == "run":
        from pertura_bench.capability_bench import run_protocol_cases

        payload = {
            "capability_id": args.capability_id,
            "tier": args.tier,
            "dataset_id": args.dataset,
            "split": args.split,
            "verdicts": run_protocol_cases(
                args.capability_id,
                tier=args.tier,
                repo_root=repo,
                dataset_id=args.dataset,
                split=args.split,
                cache=args.cache,
                output=args.output,
                parameter_catalog_path=args.parameter_catalog,
                design_confirmations_path=args.design_confirmations,
                metric_reference_catalog_path=args.metric_reference_catalog,
                resource_evidence_path=args.resource_evidence,
                enforced_memory_gb=args.enforced_memory_gb,
                enforced_n_jobs=args.enforced_n_jobs,
            ),
        }
    elif args.command == "run-matrix":
        from pertura_bench.capability_bench import (
            CANDIDATE_CAPABILITIES,
            run_protocol_cases,
            write_synthetic_verdicts,
        )

        if args.write_frozen_synthetic_verdicts:
            if args.tier != "synthetic_ci":
                parser.error("frozen local verdicts can only be written for synthetic_ci")
            payload = write_synthetic_verdicts(args.output)
        else:
            payload = {
                capability_id: run_protocol_cases(
                    capability_id,
                    tier=args.tier,
                    repo_root=repo,
                    dataset_id=args.dataset,
                    split=args.split,
                    cache=args.cache,
                    output=args.output,
                    parameter_catalog_path=args.parameter_catalog,
                    design_confirmations_path=args.design_confirmations,
                    metric_reference_catalog_path=args.metric_reference_catalog,
                    resource_evidence_path=args.resource_evidence,
                    enforced_memory_gb=args.enforced_memory_gb,
                    enforced_n_jobs=args.enforced_n_jobs,
                )
                for capability_id in CANDIDATE_CAPABILITIES
                if args.dataset is None
                or args.dataset
                in next(
                    item.required_real_datasets
                    for item in __import__(
                        "pertura_bench.capability_bench",
                        fromlist=["benchmark_specs"],
                    ).benchmark_specs()
                    if item.capability_id == capability_id
                )
            }
    elif args.command == "export-server-plan":
        from pertura_bench.capability_bench import write_server_plan

        payload = write_server_plan(
            args.output,
            repo_root=repo,
            parameter_catalog_path=args.parameter_catalog,
            design_confirmations_path=args.design_confirmations,
            metric_reference_catalog_path=args.metric_reference_catalog,
            paper_task_catalog_path=args.paper_task_catalog,
            paper_task_reference_catalog_path=args.paper_task_reference_catalog,
            paper_anchor_catalog_path=args.paper_anchor_catalog,
            paper_asset_catalog_path=args.paper_asset_catalog,
        )
    elif args.command == "bind-server-plan":
        from pertura_bench.capability_models import ServerBenchmarkPlan
        from pertura_bench.server_plan import bind_server_plan

        for source in (
            args.template,
            args.wheel,
            args.resource_lock_manifest,
            args.subset_catalog,
        ):
            if not source.is_file():
                parser.error(f"checkpoint input is missing: {source}")
        if args.prediction_lock_manifest is not None and not args.prediction_lock_manifest.is_file():
            parser.error(
                f"prediction lock manifest is missing: {args.prediction_lock_manifest}"
            )
        template = ServerBenchmarkPlan.model_validate_json(
            args.template.read_text(encoding="utf-8")
        )
        paper_catalog_arguments = {
            "paper_task_catalog_hash": args.paper_task_catalog,
            "paper_task_reference_catalog_hash": args.paper_task_reference_catalog,
            "paper_anchor_catalog_hash": args.paper_anchor_catalog,
            "paper_asset_catalog_hash": args.paper_asset_catalog,
        }
        configured_paper_catalogs = [
            path is not None for path in paper_catalog_arguments.values()
        ]
        if any(configured_paper_catalogs) and not all(configured_paper_catalogs):
            parser.error("all four paper benchmark catalogs must be supplied together")
        for field, path in paper_catalog_arguments.items():
            if path is None:
                continue
            if not path.is_file():
                parser.error(f"paper benchmark catalog is missing: {path}")
            observed_hash = file_sha256(path)
            expected_hash = template.checkpoint_binding.get(field)
            if observed_hash != expected_hash:
                parser.error(
                    f"paper benchmark catalog drift for {field}: "
                    f"expected {expected_hash}, observed {observed_hash}"
                )
        resource_manifest = json.loads(
            args.resource_lock_manifest.read_text(encoding="utf-8")
        )
        license_states: list[str] = []
        def collect_license_states(value: object) -> None:
            if isinstance(value, dict):
                if "license_status" in value:
                    license_states.append(str(value["license_status"]))
                for item in value.values():
                    collect_license_states(item)
            elif isinstance(value, list):
                for item in value:
                    collect_license_states(item)
        collect_license_states(resource_manifest)
        if not license_states or any(state != "reviewed" for state in license_states):
            parser.error(
                "all source locks in the resource manifest must have license_status=reviewed"
            )
        resource_lock_set_hash = file_sha256(args.resource_lock_manifest)
        subset_catalog_hash = file_sha256(args.subset_catalog)
        prediction_bundle_set_hash = (
            file_sha256(args.prediction_lock_manifest)
            if args.prediction_lock_manifest is not None
            else canonical_hash({"prediction_bundles": "not_configured"})
        )
        bound = bind_server_plan(
            template,
            git_commit=args.git_commit,
            wheel_sha256=file_sha256(args.wheel),
            resource_lock_set_hash=resource_lock_set_hash,
            prediction_bundle_set_hash=prediction_bundle_set_hash,
            subset_catalog_hash=subset_catalog_hash,
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(bound.model_dump(mode="json"), indent=2, sort_keys=True)
            + "\n",
            encoding="utf-8",
        )
        payload = {
            "output": str(args.output.resolve()),
            "checkpoint_binding": bound.checkpoint_binding,
            "executable": bound.executable,
        }
    elif args.command == "references":
        from pertura_bench.references import (
            freeze_reference,
            generate_reference,
            validate_reference,
        )

        if args.reference_command == "generate":
            payload = generate_reference(
                dataset_id=args.dataset,
                split=args.split,
                subset_lock=args.subset_lock,
                generator_script=args.generator_script,
                environment_lock=args.environment_lock,
                parameters=args.parameters,
                output=args.output,
            )
        elif args.reference_command == "validate":
            payload = validate_reference(args.root)
        else:
            payload = freeze_reference(args.root, args.output)
    elif args.command == "edger-golden":
        from pertura_bench.edger_golden import run_edger_golden

        payload = run_edger_golden(
            environment=args.environment,
            repo_root=repo,
        )
    else:
        from pertura_bench.scientific import dispatch_maintainer_command

        payload = dispatch_maintainer_command(args.command, args, extra)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return _exit_code(args.command, payload)
