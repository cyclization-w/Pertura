from __future__ import annotations

import re
import json
import math
from importlib import resources
from pathlib import Path
from typing import Any, Mapping

from pertura_bench.capability_models import CapabilityBenchmarkSpec, ServerBenchmarkPlan
from pertura_bench.real_run_policy import real_runs_for_spec, validate_real_run_policy
from pertura_core.hashing import canonical_hash, file_sha256
from pertura_workflow.capabilities import CapabilityRegistry


_BINDING_FIELDS = (
    "git_commit",
    "wheel_sha256",
    "case_catalog_hash",
    "agent_case_catalog_hash",
    "skill_bundle_hash",
    "capability_spec_hash",
    "parameter_schema_hash",
    "judge_manifest_hash",
    "report_turn_schema_hash",
    "template_digest",
    "resource_lock_set_hash",
    "subset_catalog_hash",
    "prediction_bundle_set_hash",
    "server_plan_hash",
    "parameter_catalog_hash",
    "design_confirmation_catalog_hash",
    "metric_reference_catalog_hash",
    "paper_task_catalog_hash",
    "paper_task_reference_catalog_hash",
    "paper_anchor_catalog_hash",
    "paper_asset_catalog_hash",
)
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_GIT_COMMIT = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")


def build_server_plan(
    specs: tuple[CapabilityBenchmarkSpec, ...],
    repo_root: str | Path | None = None,
    *,
    parameter_catalog_path: str | Path | None = None,
    design_confirmations_path: str | Path | None = None,
    metric_reference_catalog_path: str | Path | None = None,
    paper_task_catalog_path: str | Path | None = None,
    paper_task_reference_catalog_path: str | Path | None = None,
    paper_anchor_catalog_path: str | Path | None = None,
    paper_asset_catalog_path: str | Path | None = None,
) -> ServerBenchmarkPlan:
    root = (
        Path(__file__).resolve().parents[2]
        if repo_root is None
        else Path(repo_root).resolve()
    )
    validate_real_run_policy(specs)
    try:
        from pertura_bench.operations import source_manifests

        manifests = source_manifests(root)
    except (OSError, ValueError):
        manifests = {}
    datasets = tuple(
        sorted(
            {
                dataset
                for item in specs
                for dataset in item.required_real_datasets
            }
        )
    )
    artifacts: list[dict[str, Any]] = []
    jobs: list[dict[str, Any]] = []
    preparation_roots: dict[str, str] = {}
    subset_jobs: dict[tuple[str, str], str] = {}
    artifact_ids_by_dataset: dict[str, dict[str, str]] = {}

    for dataset_id in datasets:
        manifest_entry = manifests.get(dataset_id)
        if manifest_entry is None:
            raise ValueError(f"server plan lacks a source manifest for {dataset_id}")
        manifest = manifest_entry[1]
        if not manifest.file and not manifest.conversion:
            raise ValueError(f"server plan lacks a preparation route for {dataset_id}")

        artifact_ids = {
            "source": f"artifact:{dataset_id}:source",
            "converted": f"artifact:{dataset_id}:converted",
            "calibration": f"artifact:{dataset_id}:subset:calibration",
            "evaluation": f"artifact:{dataset_id}:subset:evaluation",
        }
        artifact_ids_by_dataset[dataset_id] = artifact_ids
        mixed_source_conversion = bool(manifest.file and manifest.conversion)
        source_relative = (
            f"datasets/{dataset_id}/source/{manifest.file['name']}"
            if mixed_source_conversion
            else str(manifest.file["name"])
            if manifest.file
            else f"external-sources/{dataset_id}.json"
        )
        converted_relative = (
            f"datasets/{dataset_id}/converted/artifact.h5ad"
            if mixed_source_conversion
            else f"{dataset_id}.h5ad"
            if manifest.conversion
            else source_relative
        )
        source_lock_relative = (
            f"datasets/{dataset_id}/source/artifact.lock.json"
            if mixed_source_conversion
            else f"{dataset_id}.lock.json"
        )
        converted_lock_relative = (
            f"datasets/{dataset_id}/converted/artifact.lock.json"
            if mixed_source_conversion
            else f"{dataset_id}.lock.json"
        )
        artifacts.extend(
            (
                {
                    "artifact_id": artifact_ids["source"],
                    "kind": "source" if manifest.file else "external_source",
                    "dataset_id": dataset_id,
                    "relative_path": source_relative,
                    "lock_relative_path": (
                        source_lock_relative if manifest.file else None
                    ),
                },
                {
                    "artifact_id": artifact_ids["converted"],
                    "kind": "converted" if manifest.conversion else "source_ready",
                    "dataset_id": dataset_id,
                    "relative_path": converted_relative,
                    "lock_relative_path": converted_lock_relative,
                },
                {
                    "artifact_id": artifact_ids["calibration"],
                    "kind": "subset",
                    "split": "calibration",
                    "dataset_id": dataset_id,
                    "relative_path": f"datasets/{dataset_id}/subset/calibration/artifact.h5ad",
                    "lock_relative_path": f"datasets/{dataset_id}/subset/calibration/subset.lock.json",
                },
                {
                    "artifact_id": artifact_ids["evaluation"],
                    "kind": "subset",
                    "split": "evaluation",
                    "dataset_id": dataset_id,
                    "relative_path": f"datasets/{dataset_id}/subset/evaluation/artifact.h5ad",
                    "lock_relative_path": f"datasets/{dataset_id}/subset/evaluation/subset.lock.json",
                },
            )
        )

        preparation_job_id = ""
        if manifest.file:
            produces = [artifact_ids["source"]]
            if not manifest.conversion:
                produces.append(artifact_ids["converted"])
            preparation_job_id = f"prepare:{dataset_id}:fetch"
            jobs.append(
                _preparation_job(
                    job_id=preparation_job_id,
                    kind="fetch",
                    depends_on=[],
                    argv=[
                        "python",
                        "-m",
                        "pertura_bench",
                        "fetch",
                        dataset_id,
                        "--cache",
                        "$PERTURA_BENCH_CACHE",
                        "--repo",
                        "$PERTURA_REPO",
                    ],
                    consumes=[],
                    produces=produces,
                    cpus=1,
                    memory_gb=4,
                    walltime_minutes=180,
                )
            )

        if manifest.conversion:
            conversion_job_id = f"prepare:{dataset_id}:convert"
            jobs.append(
                _preparation_job(
                    job_id=conversion_job_id,
                    kind="convert",
                    depends_on=([preparation_job_id] if preparation_job_id else []),
                    argv=[
                        "python",
                        "-m",
                        "pertura_bench",
                        "convert",
                        dataset_id,
                        "--cache",
                        "$PERTURA_BENCH_CACHE",
                        "--repo",
                        "$PERTURA_REPO",
                    ],
                    consumes=([artifact_ids["source"]] if preparation_job_id else []),
                    produces=[artifact_ids["converted"]],
                    cpus=4,
                    memory_gb=32,
                    walltime_minutes=720,
                )
            )
            preparation_job_id = conversion_job_id
        if not preparation_job_id:
            raise ValueError(
                f"server plan cannot materialize converted artifact for {dataset_id}"
            )
        preparation_roots[dataset_id] = preparation_job_id

        for split in ("calibration", "evaluation"):
            subset_job_id = f"prepare:{dataset_id}:subset:{split}"
            subset_jobs[(dataset_id, split)] = subset_job_id
            jobs.append(
                _preparation_job(
                    job_id=subset_job_id,
                    kind="subset",
                    depends_on=[preparation_job_id],
                    argv=[
                        "python",
                        "-m",
                        "pertura_bench",
                        "subset",
                        dataset_id,
                        "--split",
                        split,
                        "--cache",
                        "$PERTURA_BENCH_CACHE",
                        "--repo",
                        "$PERTURA_REPO",
                        "--from-lock-chain",
                    ],
                    consumes=[artifact_ids["converted"]],
                    produces=[artifact_ids[split]],
                    cpus=4,
                    memory_gb=32,
                    walltime_minutes=360,
                )
            )

    registry = CapabilityRegistry.load_default(include_external=False)
    from pertura_bench.real_execution import (
        load_design_confirmation_catalog,
        load_metric_reference_catalog,
        load_real_parameter_catalog,
    )

    real_parameter_catalog, real_parameter_catalog_hash = load_real_parameter_catalog(
        parameter_catalog_path
    )
    design_catalog, design_catalog_hash = load_design_confirmation_catalog(
        design_confirmations_path
    )
    metric_catalog, metric_catalog_hash = load_metric_reference_catalog(
        metric_reference_catalog_path
    )
    from pertura_bench.real_execution import load_reference_generator_catalog

    generator_catalog, _ = load_reference_generator_catalog()
    reference_jobs: dict[tuple[str, str], list[str]] = {}
    for dataset_id in datasets:
        for split in ("calibration", "evaluation"):
            for generator_id in _configured_reference_generators(
                metric_catalog, dataset_id=dataset_id, split=split
            ):
                generator = generator_catalog["generators"][generator_id]
                if generator.get("kind") != "script":
                    continue
                job_id = f"reference:{dataset_id}:{split}:{generator_id}"
                reference_jobs.setdefault((dataset_id, split), []).append(job_id)
                output_id = f"reference:{dataset_id}:{split}:{generator_id}:frozen"
                artifacts.append(
                    {
                        "artifact_id": output_id,
                        "kind": "scientific_reference",
                        "dataset_id": dataset_id,
                        "split": split,
                        "generator_id": generator_id,
                        "relative_path": (
                            f"references/{dataset_id}/{split}/{generator_id}"
                        ),
                        "lock_relative_path": (
                            f"references/{dataset_id}/{split}/{generator_id}/"
                            "reference-generation-manifest.json"
                        ),
                    }
                )
                jobs.append(
                    _preparation_job(
                        job_id=job_id,
                        kind="reference_generation",
                        depends_on=[subset_jobs[(dataset_id, split)]],
                        argv=[
                            "python", "-m", "pertura_bench", "references", "generate",
                            "--dataset", dataset_id,
                            "--split", split,
                            "--subset-lock",
                            f"$PERTURA_BENCH_CACHE/datasets/{dataset_id}/subset/{split}/subset.lock.json",
                            "--generator-script",
                            f"$PERTURA_REPO/src/pertura_bench/{generator['script']}",
                            "--environment-lock",
                            (
                                "$PERTURA_BENCH_ENVIRONMENT_LOCK_ROOT/"
                                f"{generator['environment_profile']}.json"
                            ),
                            "--parameters",
                            (
                                "$PERTURA_BENCH_REFERENCE_PARAMETER_ROOT/"
                                f"{dataset_id}/{split}/{generator_id}.json"
                            ),
                            "--output",
                            (
                                "$PERTURA_BENCH_REFERENCE_ROOT/"
                                f"{dataset_id}/{split}/{generator_id}"
                            ),
                        ],
                        consumes=[artifact_ids_by_dataset[dataset_id][split]],
                        produces=[output_id],
                        cpus=1,
                        memory_gb=4,
                        walltime_minutes=720,
                    )
                )
    agent_asset_ids_by_dataset_split: dict[
        tuple[str, str], dict[str, str]
    ] = {}
    from pertura_bench.real_execution import select_real_parameter_run

    for dataset_id in real_parameter_catalog["datasets"]:
        for split in ("calibration", "evaluation"):
            try:
                dataset_mapping = select_real_parameter_run(
                    real_parameter_catalog,
                    dataset_id=dataset_id,
                    tier="frozen_subset",
                    split=split,
                )
            except RuntimeError:
                continue
            for asset in dataset_mapping.get("agent_assets") or ():
                role = str(asset["role"])
                artifact_id = f"artifact:{dataset_id}:{split}:agent:{role}"
                agent_asset_ids_by_dataset_split.setdefault(
                    (dataset_id, split), {}
                )[role] = artifact_id
                artifacts.append(
                    {
                        "artifact_id": artifact_id,
                        "kind": "registered_agent_asset",
                        "dataset_id": dataset_id,
                        "split": split,
                        "role": role,
                        "relative_path": str(asset["relative_path"]),
                        "content_sha256": str(asset["content_sha256"]),
                        "subset_lock_hash": str(asset.get("subset_lock_hash") or ""),
                        "lock_relative_path": None,
                    }
                )
    for bench_spec in specs:
        capability = registry.get(
            bench_spec.capability_id, bench_spec.capability_version
        )
        runtime_dag = list(_capability_runtime_dag(registry, capability.capability_id))
        environment_profile = str(
            capability.metadata.get("environment_profile")
            or next(
                (
                    case.environment_profile
                    for case in bench_spec.cases
                    if case.environment_profile
                ),
                "python-science-v1",
            )
        )
        for real_run in real_runs_for_spec(bench_spec):
            dataset_id = real_run["dataset_id"]
            tier = real_run["tier"]
            split = real_run["split"]
            track = real_run["track"]
            artifact_ids = artifact_ids_by_dataset[dataset_id]
            parameter_coverage = _real_parameter_coverage(
                registry,
                real_parameter_catalog,
                dataset_id=dataset_id,
                tier=tier,
                split=split,
                runtime_dag=runtime_dag,
                target_capability_id=bench_spec.capability_id,
                target_case=bench_spec.cases[0],
            )
            metric_configuration_state = _metric_configuration_state(
                metric_catalog,
                dataset_id=dataset_id,
                identity=(
                    f"{bench_spec.capability_id}@{bench_spec.capability_version}"
                ),
                tier=tier,
                split=split,
                namespace="capabilities",
            )
            resource_request = _capability_resource_request(
                real_parameter_catalog,
                dataset_id=dataset_id,
                tier=tier,
                split=split,
                capability_identity=(
                    f"{bench_spec.capability_id}@{bench_spec.capability_version}"
                ),
            )
            if tier == "frozen_subset":
                preparation_dependency = subset_jobs[(dataset_id, split)]
                artifact_id = artifact_ids[split]
            else:
                preparation_dependency = preparation_roots[dataset_id]
                artifact_id = artifact_ids["converted"]
            job_id = (
                f"benchmark:{dataset_id}:{bench_spec.capability_id}:"
                f"{tier}:{split}"
            )
            jobs.append(
                {
                    "job_id": job_id,
                    "kind": "capability",
                    "dataset_id": dataset_id,
                    "capability_id": bench_spec.capability_id,
                    "capability_version": bench_spec.capability_version,
                    "tier": tier,
                    "split": split,
                    "benchmark_track": track,
                    # Scheduler ordering materializes only the locked input.
                    # Scientific upstreams execute below in one persistent runtime.
                    "depends_on": [
                        preparation_dependency,
                        *reference_jobs.get((dataset_id, split), ()),
                    ],
                    "runtime_execution": {
                        "scope": "single_persistent_pertura_runtime",
                        "dependency_resolution": "runtime_owned",
                        "capability_dag": runtime_dag,
                        "authoritative_commit_store": True,
                    },
                    "benchmark_catalogs": {
                        "parameters": {
                            "version": real_parameter_catalog["catalog_version"],
                            "hash": real_parameter_catalog_hash,
                        },
                        "design_confirmations": {
                            "version": design_catalog["catalog_version"],
                            "hash": design_catalog_hash,
                        },
                        "metric_references": {
                            "version": metric_catalog["catalog_version"],
                            "hash": metric_catalog_hash,
                        },
                    },
                    "real_parameter_catalog": {
                        "version": real_parameter_catalog["catalog_version"],
                        "hash": real_parameter_catalog_hash,
                    },
                    "real_parameter_coverage": [
                        dict(item) | {"tier": tier, "split": split}
                        for item in parameter_coverage
                    ],
                    "metric_configuration_state": metric_configuration_state,
                    "configuration_state": (
                        "configured"
                        if all(item["configured"] for item in parameter_coverage)
                        and metric_configuration_state == "frozen_reference"
                        else "reported_only"
                        if all(item["configured"] for item in parameter_coverage)
                        and metric_configuration_state == "reported_only"
                        else "not_configured"
                    ),
                    "optional_execution_gate": (
                        {
                            "required_binding": "prediction_bundle_set_hash",
                            "required_configuration_state": "configured",
                            "missing_status": "not_configured",
                            "release_blocking": False,
                        }
                        if bench_spec.capability_id
                        == "virtual.evaluate.comprehensive.v1"
                        else None
                    ),
                    "checkpoint_requirement": {
                        "required": True,
                        "binding_fields": list(_BINDING_FIELDS),
                        "binding_source": "server_plan.checkpoint_binding",
                    },
                    "environment": _bound_job_environment(),
                    "environment_profile": environment_profile,
                    "command": {
                        "argv": [
                            "python",
                            "-m",
                            "pertura_bench",
                            "run",
                            bench_spec.capability_id,
                            "--tier",
                            tier,
                            "--dataset",
                            dataset_id,
                            "--split",
                            split,
                            "--cache",
                            "$PERTURA_BENCH_CACHE",
                            "--output",
                            "$PERTURA_BENCH_OUTPUT",
                            "--repo",
                            "$PERTURA_REPO",
                            "--parameter-catalog",
                            "$PERTURA_BENCH_PARAMETER_CATALOG",
                            "--design-confirmations",
                            "$PERTURA_BENCH_DESIGN_CONFIRMATIONS",
                            "--metric-reference-catalog",
                            "$PERTURA_BENCH_METRIC_REFERENCES",
                            "--resource-evidence",
                            "$PERTURA_BENCH_RESOURCE_EVIDENCE",
                            "--enforced-memory-gb",
                            str(resource_request["max_memory_gb"]),
                            "--enforced-n-jobs",
                            "1",
                        ]
                    },
                    "consumes": [artifact_id],
                    "produces": [
                        f"verdict:{dataset_id}:{bench_spec.capability_id}:{tier}:{split}"
                    ],
                    "resources": {
                        "cpus": 1,
                        "memory_gb": resource_request["max_memory_gb"],
                        "walltime_minutes": 720,
                    },
                    "failure_policy": {
                        "missing_lock": "not_available",
                        "missing_environment": "not_run_environment_missing",
                        "missing_real_parameters": "failed_not_configured",
                        "timeout": "failed_no_fallback",
                    },
                }
            )

    agent_catalog_path = root / "src" / "pertura_bench" / "cases" / "server_agent_cases.v1.json"
    agent_catalog = json.loads(agent_catalog_path.read_text(encoding="utf-8"))
    conditions = tuple(str(item) for item in agent_catalog.get("conditions") or ())
    if conditions != ("pertura_full", "prompt_only", "free_codeact"):
        raise ValueError("agent benchmark catalog must declare the three controlled conditions")
    for case in agent_catalog["cases"]:
        benchmark_track = str(case.get("benchmark_track") or "primary")
        dataset_id = case["dataset_id"]
        metric_configuration_state = _metric_configuration_state(
            metric_catalog,
            dataset_id=dataset_id,
            identity=str(case["case_id"]),
            tier="frozen_subset",
            split="evaluation",
            namespace="agent_cases",
        )
        if dataset_id not in artifact_ids_by_dataset:
            raise ValueError(f"agent case lacks prepared dataset: {dataset_id}")
        for condition in conditions:
            for repeat_index in (1, 2):
                job_id = f"agent:{case['case_id']}:{condition}:repeat-{repeat_index}"
                jobs.append({
                    "job_id": job_id,
                    "kind": "agent_workflow",
                    "dataset_id": dataset_id,
                    "case_id": case["case_id"],
                    "objective": case["objective"],
                    "benchmark_condition": condition,
                    "repeat_index": repeat_index,
                    "benchmark_track": benchmark_track,
                    "provider": "claude-agent-sdk",
                    "model_source": "PERTURA_CLAUDE_MODEL",
                    "metric_configuration_state": metric_configuration_state,
                    "configuration_state": (
                        "configured"
                        if metric_configuration_state == "frozen_reference"
                        else "not_configured"
                    ),
                    "controlled_comparison": {
                        "same_dataset_split": "evaluation",
                        "same_objective": True,
                        "same_model": True,
                        "same_context_budget": True,
                        "same_timeout": True,
                        "same_resource_budget": True,
                    },
                    "depends_on": [
                        subset_jobs[(dataset_id, "evaluation")],
                        *reference_jobs.get((dataset_id, "evaluation"), ()),
                    ],
                    "fresh_namespace": {
                        "project_id": True,
                        "analysis_run_id": True,
                        "conversation_id": True,
                        "provider_session": True,
                        "authority_namespace": True,
                        "workspace_binding": True,
                    },
                    "command": {
                        "argv": [
                            "python", "-m", "pertura_bench", "agent", "run-server",
                            case["case_id"], "--cache", "$PERTURA_BENCH_CACHE",
                            "--output", "$PERTURA_BENCH_OUTPUT", "--repo", "$PERTURA_REPO",
                            "--condition", condition,
                            "--repeat-index", str(repeat_index),
                            "--parameter-catalog", "$PERTURA_BENCH_PARAMETER_CATALOG",
                            "--design-confirmations", "$PERTURA_BENCH_DESIGN_CONFIRMATIONS",
                            "--metric-reference-catalog", "$PERTURA_BENCH_METRIC_REFERENCES",
                            "--resource-enforcement", "scheduler",
                            "--resource-evidence", "$PERTURA_BENCH_RESOURCE_EVIDENCE",
                            "--enforced-memory-gb", str(float(case.get("max_memory_gb", 4.0))),
                            "--enforced-n-jobs", "1",
                        ]
                    },
                    "consumes": [
                        artifact_ids_by_dataset[dataset_id]["evaluation"],
                        *[
                            artifact_id
                            for role, artifact_id in
                            agent_asset_ids_by_dataset_split.get(
                                (dataset_id, "evaluation"), {}
                            ).items()
                            if role in set(
                                case.get("required_artifact_roles") or ()
                            )
                        ],
                    ],
                    "produces": [
                        f"agent-verdict:{case['case_id']}:{condition}:repeat-{repeat_index}",
                        f"agent-grade:{case['case_id']}:{condition}:repeat-{repeat_index}",
                    ],
                    "judge": {
                        "provider": "deepseek",
                        "model": "deepseek-v4-pro",
                        "fallback_allowed": False,
                        "unavailable_status": "judge_unavailable",
                    },
                    "checkpoint_requirement": {
                        "required": True,
                        "binding_fields": list(_BINDING_FIELDS),
                        "binding_source": "server_plan.checkpoint_binding",
                    },
                    "environment": _bound_job_environment(),
                    "resource_evidence": {
                        "schema_version": "pertura-resource-evidence-v1",
                        "path": "$PERTURA_BENCH_RESOURCE_EVIDENCE",
                        "generated_by": "bound_plan_scheduler_wrapper",
                        "required": True,
                    },
                    "resources": {
                        "cpus": 1,
                        "memory_gb": float(case.get("max_memory_gb", 4.0)),
                        "walltime_minutes": max(1, int(case.get("timeout_seconds", 1800)) // 60),
                    },
                    "failure_policy": {
                        "missing_lock": "not_available",
                        "judge_unavailable": "failed_no_fallback",
                        "timeout": "failed_no_fallback",
                    },
                })

    paper_catalog_hashes = {
        "paper_task_catalog_hash": canonical_hash({"paper_tasks": "not_configured"}),
        "paper_task_reference_catalog_hash": canonical_hash({"paper_task_references": "not_configured"}),
        "paper_anchor_catalog_hash": canonical_hash({"paper_anchors": "not_configured"}),
        "paper_asset_catalog_hash": canonical_hash({"paper_assets": "not_configured"}),
    }
    if paper_task_catalog_path is not None:
        required_paper_catalogs = {
            "paper_task_catalog_hash": paper_task_catalog_path,
            "paper_task_reference_catalog_hash": paper_task_reference_catalog_path,
            "paper_anchor_catalog_hash": paper_anchor_catalog_path,
            "paper_asset_catalog_hash": paper_asset_catalog_path,
        }
        missing_catalogs = [
            name for name, path in required_paper_catalogs.items() if path is None
        ]
        if missing_catalogs:
            raise ValueError(
                "paper server plan is missing catalogs: "
                + ", ".join(missing_catalogs)
            )
        resolved_catalogs = {
            name: Path(path).resolve()
            for name, path in required_paper_catalogs.items()
            if path is not None
        }
        for name, path in resolved_catalogs.items():
            if not path.is_file():
                raise FileNotFoundError(f"{name}: {path}")
            paper_catalog_hashes[name] = file_sha256(path)
        from pertura_bench.paper_tasks import load_paper_task_catalog
        from pertura_bench.paper_tasks import (
            validate_paper_asset_catalog,
            validate_paper_anchor_catalog,
            validate_task_reference_catalog,
        )

        paper_catalog = load_paper_task_catalog(
            resolved_catalogs["paper_task_catalog_hash"]
        )
        task_references = json.loads(
            resolved_catalogs[
                "paper_task_reference_catalog_hash"
            ].read_text(encoding="utf-8")
        )
        if (
            task_references.get("schema_version")
            != "pertura-paper-task-reference-catalog-bound-v1"
            or task_references.get("status") != "bound"
            or task_references.get("passed") is not True
        ):
            raise ValueError(
                "paper task-reference catalog is not bound and validated"
            )
        reference_problems = validate_task_reference_catalog(
            task_references, paper_catalog.tasks()
        )
        if reference_problems:
            raise ValueError(
                "invalid bound paper task-reference catalog: "
                + "; ".join(reference_problems)
            )
        paper_anchors = json.loads(
            resolved_catalogs["paper_anchor_catalog_hash"].read_text(
                encoding="utf-8"
            )
        )
        anchor_problems = validate_paper_anchor_catalog(
            paper_anchors, paper_catalog.tasks()
        )
        if anchor_problems:
            raise ValueError(
                "invalid paper-anchor catalog: "
                + "; ".join(anchor_problems)
            )
        paper_assets = json.loads(
            resolved_catalogs["paper_asset_catalog_hash"].read_text(
                encoding="utf-8"
            )
        )
        if (
            paper_assets.get("schema_version")
            != "pertura-paper-agent-assets-v1"
            or paper_assets.get("status") != "bound"
            or paper_assets.get("passed") is not True
        ):
            raise ValueError("paper asset catalog is not bound and validated")
        asset_problems = validate_paper_asset_catalog(
            paper_assets, paper_catalog
        )
        if asset_problems:
            raise ValueError(
                "invalid bound paper asset catalog: "
                + "; ".join(asset_problems)
            )
        jobs = [item for item in jobs if item.get("kind") != "agent_workflow"]
        for workflow in paper_catalog.workflows:
            dataset_id = str(workflow["dataset_id"])
            if dataset_id not in artifact_ids_by_dataset:
                raise ValueError(
                    f"paper workflow lacks prepared dataset: {dataset_id}"
                )
            required_tasks = [
                task
                for task in workflow.get("turns") or ()
                if task.get("role") != "optional"
            ]
            maximum_memory = max(
                float(task["resources"]["max_memory_gb"])
                for task in required_tasks
            )
            total_timeout = sum(
                int(task["resources"]["timeout_seconds"])
                for task in required_tasks
            )
            for condition in paper_catalog.payload["execution_protocol"]["conditions"]:
                for repeat_index in (1, 2):
                    workflow_id = str(workflow["workflow_id"])
                    jobs.append(
                        {
                            "job_id": f"paper-agent:{workflow_id}:{condition}:repeat-{repeat_index}",
                            "kind": "paper_agent_workflow",
                            "workflow_id": workflow_id,
                            "dataset_id": dataset_id,
                            "benchmark_condition": condition,
                            "repeat_index": repeat_index,
                            "benchmark_track": workflow["role"],
                            "provider": "claude-agent-sdk",
                            "model_source": "PERTURA_CLAUDE_MODEL",
                            "task_ids": [
                                task["task_id"]
                                for task in workflow.get("turns") or ()
                            ],
                            "required_task_count": len(required_tasks),
                            "session_scope": {
                                "shared_project": True,
                                "shared_analysis_run": True,
                                "shared_conversation": True,
                                "shared_provider_session": True,
                                "condition_repeat_isolated": True,
                            },
                            "depends_on": [
                                subset_jobs[(dataset_id, "calibration")],
                                subset_jobs[(dataset_id, "evaluation")],
                            ],
                            "command": {
                                "argv": [
                                    "python", "-m", "pertura_bench", "agent",
                                    "run-paper-workflow", workflow_id,
                                    "--cache", "$PERTURA_BENCH_CACHE",
                                    "--paper-root", "$PERTURA_PAPER_ROOT",
                                    "--output", "$PERTURA_BENCH_OUTPUT",
                                    "--repo", "$PERTURA_REPO",
                                    "--condition", condition,
                                    "--repeat-index", str(repeat_index),
                                    "--task-catalog", "$PERTURA_PAPER_TASK_CATALOG",
                                    "--task-reference-catalog", "$PERTURA_PAPER_TASK_REFERENCES",
                                    "--paper-anchor-catalog", "$PERTURA_PAPER_ANCHORS",
                                    "--asset-catalog", "$PERTURA_PAPER_ASSETS",
                                    "--resource-evidence", "$PERTURA_BENCH_RESOURCE_EVIDENCE",
                                ]
                            },
                            "consumes": [
                                artifact_ids_by_dataset[dataset_id]["calibration"],
                                artifact_ids_by_dataset[dataset_id]["evaluation"],
                            ],
                            "produces": [
                                f"paper-workflow-verdict:{workflow_id}:{condition}:repeat-{repeat_index}"
                            ],
                            "checkpoint_requirement": {
                                "required": True,
                                "binding_fields": list(_BINDING_FIELDS),
                                "binding_source": "server_plan.checkpoint_binding",
                            },
                            "environment": _bound_job_environment(),
                            "resources": {
                                "cpus": 1,
                                "memory_gb": maximum_memory,
                                "walltime_minutes": max(1, math.ceil(total_timeout / 60)),
                            },
                            "failure_policy": {
                                "missing_catalog": "failed_not_configured",
                                "missing_reference": "failed_not_available",
                                "judge_unavailable": "failed_no_fallback",
                                "timeout": "failed_no_fallback",
                            },
                        }
                    )

    case_catalog_hash = _case_catalog_hash(root)
    template_digest = _template_digest(
        artifacts=artifacts,
        jobs=jobs,
        datasets=datasets,
        case_catalog_hash=case_catalog_hash,
    )
    from pertura_runtime.agent_bundle.bundle import bundled_skill_manifest
    from pertura_runtime.project.models import ReportRevision, TurnDraft, TurnFinal
    agent_case_catalog_hash = (
        paper_catalog_hashes["paper_task_catalog_hash"]
        if paper_task_catalog_path is not None
        else file_sha256(agent_catalog_path)
    )
    skill_bundle_hash = bundled_skill_manifest()["bundle_hash"]
    capability_spec_hash = canonical_hash([
        item.model_dump(mode="json") for item in registry.specs()
    ])
    parameter_schema_hash = canonical_hash(
        {
            f"{item.capability_id}@{item.version}": item.parameters_schema
            for item in registry.specs()
        }
    )
    judge_manifest_hash = canonical_hash(agent_catalog["judge"])
    report_turn_schema_hash = canonical_hash({
        "TurnDraft": TurnDraft.model_json_schema(),
        "TurnFinal": TurnFinal.model_json_schema(),
        "ReportRevision": ReportRevision.model_json_schema(),
    })
    return ServerBenchmarkPlan(
        artifacts=tuple(artifacts),
        jobs=tuple(jobs),
        datasets=datasets,
        checkpoint_binding={
            "git_commit": None,
            "wheel_sha256": None,
            "case_catalog_hash": case_catalog_hash,
            "agent_case_catalog_hash": agent_case_catalog_hash,
            "skill_bundle_hash": skill_bundle_hash,
            "capability_spec_hash": capability_spec_hash,
            "parameter_schema_hash": parameter_schema_hash,
            "judge_manifest_hash": judge_manifest_hash,
            "report_turn_schema_hash": report_turn_schema_hash,
            "template_digest": template_digest,
            "resource_lock_set_hash": None,
            "subset_catalog_hash": None,
            "prediction_bundle_set_hash": None,
            "server_plan_hash": None,
            "parameter_catalog_hash": real_parameter_catalog_hash,
            "design_confirmation_catalog_hash": design_catalog_hash,
            "metric_reference_catalog_hash": metric_catalog_hash,
            **paper_catalog_hashes,
        },
        executable=False,
    )


def bind_server_plan(
    template: ServerBenchmarkPlan,
    *,
    git_commit: str,
    wheel_sha256: str,
    resource_lock_set_hash: str,
    prediction_bundle_set_hash: str,
    subset_catalog_hash: str | None = None,
) -> ServerBenchmarkPlan:
    """Bind an immutable checkpoint without hashing a field into itself.

    ``template_digest`` identifies the scheduler-neutral artifact/job graph.
    ``server_plan_hash`` identifies that template plus the three external
    checkpoint identities while deliberately excluding server_plan_hash itself.
    """

    binding = dict(template.checkpoint_binding)
    binding.update(
        {
            "git_commit": git_commit.lower(),
            "wheel_sha256": wheel_sha256.lower(),
            "resource_lock_set_hash": resource_lock_set_hash.lower(),
            "subset_catalog_hash": (
                subset_catalog_hash
                or canonical_hash({"subset_catalog": "not_configured"})
            ).lower(),
            "prediction_bundle_set_hash": prediction_bundle_set_hash.lower(),
        }
    )
    binding["server_plan_hash"] = _bound_plan_digest(binding)
    payload = template.model_dump(mode="json")
    payload.update(
        {
            "plan_id": "",
            "canonical_hash": "",
            "checkpoint_binding": binding,
            "executable": True,
        }
    )
    bound = ServerBenchmarkPlan.model_validate(payload)
    assert_server_plan_executable(bound)
    return bound


def assert_server_plan_executable(plan: ServerBenchmarkPlan) -> None:
    binding = plan.checkpoint_binding
    if not plan.executable:
        missing = [name for name in _BINDING_FIELDS if not binding.get(name)]
        raise ValueError(
            "server benchmark plan is not checkpoint-bound; missing: "
            + ", ".join(missing)
        )
    git_commit = str(binding.get("git_commit") or "")
    if not _GIT_COMMIT.fullmatch(git_commit):
        raise ValueError("checkpoint git_commit must be a 40- or 64-character lowercase hex digest")
    for name in (
        "wheel_sha256", "case_catalog_hash", "agent_case_catalog_hash",
        "skill_bundle_hash", "capability_spec_hash", "judge_manifest_hash",
        "parameter_schema_hash", "subset_catalog_hash",
        "report_turn_schema_hash", "template_digest", "resource_lock_set_hash",
        "prediction_bundle_set_hash", "server_plan_hash",
        "parameter_catalog_hash", "design_confirmation_catalog_hash",
        "metric_reference_catalog_hash",
        "paper_task_catalog_hash", "paper_task_reference_catalog_hash",
        "paper_anchor_catalog_hash", "paper_asset_catalog_hash",
    ):
        value = str(binding.get(name) or "")
        if not _SHA256.fullmatch(value):
            raise ValueError(f"checkpoint {name} must be a canonical sha256 digest")
    expected_template = _template_digest(
        artifacts=list(plan.artifacts),
        jobs=list(plan.jobs),
        datasets=plan.datasets,
        case_catalog_hash=str(binding["case_catalog_hash"]),
    )
    if binding["template_digest"] != expected_template:
        raise ValueError("server benchmark template digest drift")
    if binding["server_plan_hash"] != _bound_plan_digest(binding):
        raise ValueError("server benchmark bound plan digest drift")


def validate_checkpoint_binding(
    template: ServerBenchmarkPlan,
    binding: Mapping[str, Any],
) -> dict[str, str]:
    expected = bind_server_plan(
        template,
        git_commit=str(binding.get("git_commit") or ""),
        wheel_sha256=str(binding.get("wheel_sha256") or ""),
        resource_lock_set_hash=str(binding.get("resource_lock_set_hash") or ""),
        prediction_bundle_set_hash=str(binding.get("prediction_bundle_set_hash") or ""),
        subset_catalog_hash=str(binding.get("subset_catalog_hash") or ""),
    )
    expected_binding = expected.checkpoint_binding
    for name in _BINDING_FIELDS:
        if binding.get(name) != expected_binding.get(name):
            raise ValueError(f"checkpoint binding mismatch: {name}")
    return {name: str(expected_binding[name]) for name in _BINDING_FIELDS}


def _preparation_job(
    *,
    job_id: str,
    kind: str,
    depends_on: list[str],
    argv: list[str],
    consumes: list[str],
    produces: list[str],
    cpus: int,
    memory_gb: int,
    walltime_minutes: int,
) -> dict[str, Any]:
    return {
        "job_id": job_id,
        "kind": kind,
        "depends_on": depends_on,
        "command": {"argv": argv},
        "consumes": consumes,
        "produces": produces,
        "checkpoint_requirement": {
            "required": True,
            "binding_fields": list(_BINDING_FIELDS),
            "binding_source": "server_plan.checkpoint_binding",
        },
        "environment": _bound_job_environment(),
        "resources": {
            "cpus": cpus,
            "memory_gb": memory_gb,
            "walltime_minutes": walltime_minutes,
        },
    }


def _bound_job_environment() -> dict[str, str]:
    return {
        "PERTURA_BENCH_CHECKPOINT_BINDING": "$PERTURA_BENCH_BOUND_PLAN",
        "PERTURA_BENCH_WHEEL": "$PERTURA_BENCH_WHEEL",
        "OMP_NUM_THREADS": "1",
        "OPENBLAS_NUM_THREADS": "1",
        "MKL_NUM_THREADS": "1",
        "NUMEXPR_NUM_THREADS": "1",
    }


def _metric_configuration_state(
    catalog: Mapping[str, Any],
    *,
    dataset_id: str,
    identity: str,
    tier: str,
    split: str,
    namespace: str,
) -> str:
    dataset = dict(catalog.get("datasets", {}).get(dataset_id) or {})
    entry = dict(dataset.get(namespace, {}).get(identity) or {})
    variants = entry.get("runs")
    if isinstance(variants, Mapping):
        entry = dict(variants.get(f"{tier}:{split}") or {})
    if entry.get("metrics") or entry.get("evaluators"):
        return "frozen_reference"
    if entry.get("reported_metrics"):
        return "reported_only"
    return "not_configured"


def _configured_reference_generators(
    catalog: Mapping[str, Any], *, dataset_id: str, split: str
) -> tuple[str, ...]:
    dataset = dict(catalog.get("datasets", {}).get(dataset_id) or {})
    generator_ids: set[str] = set()
    for namespace in ("capabilities", "agent_cases"):
        for raw_entry in dict(dataset.get(namespace) or {}).values():
            if not isinstance(raw_entry, Mapping):
                continue
            variants = raw_entry.get("runs")
            entries = (
                [variants.get(f"frozen_subset:{split}")]
                if isinstance(variants, Mapping)
                else [raw_entry]
            )
            for entry in entries:
                if not isinstance(entry, Mapping):
                    continue
                for metric in (
                    *(entry.get("metrics") or ()),
                    *(entry.get("evaluators") or ()),
                ):
                    if isinstance(metric, Mapping) and metric.get(
                        "reference_generator_id"
                    ):
                        generator_ids.add(str(metric["reference_generator_id"]))
    return tuple(sorted(generator_ids))

def _capability_runtime_dag(
    registry: CapabilityRegistry, target_capability_id: str
) -> tuple[str, ...]:
    ordered: list[str] = []
    active: set[str] = set()
    visited: set[str] = set()

    def visit(capability_id: str) -> None:
        if capability_id in active:
            raise ValueError(f"capability dependency cycle at {capability_id}")
        if capability_id in visited:
            return
        active.add(capability_id)
        spec = registry.get(capability_id)
        for dependency in spec.depends_on:
            visit(dependency)
        active.remove(capability_id)
        visited.add(capability_id)
        ordered.append(capability_id)

    visit(target_capability_id)
    return tuple(ordered)


def _real_parameter_coverage(
    registry: CapabilityRegistry,
    catalog: Mapping[str, Any],
    *,
    dataset_id: str,
    tier: str,
    split: str,
    runtime_dag: list[str],
    target_capability_id: str,
    target_case: Any,
) -> list[dict[str, Any]]:
    from pertura_bench.real_execution import (
        RealParametersNotConfigured,
        select_real_parameter_run,
    )

    try:
        dataset = select_real_parameter_run(
            catalog,
            dataset_id=dataset_id,
            tier=tier,
            split=split,
        )
    except RealParametersNotConfigured:
        dataset = {}
    entries = dict(dataset.get("capabilities") or {})
    target_override = target_case.parameters.get("real_execution")
    target_override_configured = (
        isinstance(target_override, Mapping)
        and isinstance(target_override.get("parameters"), Mapping)
    )
    coverage: list[dict[str, Any]] = []
    for capability_id in runtime_dag:
        spec = registry.get(capability_id)
        key = f"{spec.capability_id}@{spec.version}"
        configured = key in entries or (
            capability_id == target_capability_id and target_override_configured
        )
        coverage.append(
            {
                "capability_id": spec.capability_id,
                "capability_version": spec.version,
                "configured": configured,
                "reason": (
                    None
                    if configured
                    else (
                        "not_configured: dataset-specific real parameters are absent "
                        f"for {dataset_id}/{tier}:{split}/{key}"
                    )
                ),
            }
        )
    return coverage


def _capability_resource_request(
    catalog: Mapping[str, Any],
    *,
    dataset_id: str,
    tier: str,
    split: str,
    capability_identity: str,
) -> dict[str, float | int]:
    from pertura_bench.real_execution import (
        RealParametersNotConfigured,
        select_real_parameter_run,
    )

    try:
        mapping = select_real_parameter_run(
            catalog,
            dataset_id=dataset_id,
            tier=tier,
            split=split,
        )
    except RealParametersNotConfigured:
        mapping = {}
    entry = dict(mapping.get("capabilities", {}).get(capability_identity) or {})
    parameters = dict(entry.get("parameters") or {})
    memory = float(parameters.get("max_memory_gb", 4.0))
    n_jobs = int(parameters.get("n_jobs", 1))
    if memory <= 0 or n_jobs != 1:
        raise ValueError(
            "formal benchmark resources require max_memory_gb > 0 and n_jobs=1: "
            f"{dataset_id}/{tier}:{split}/{capability_identity}"
        )
    return {"max_memory_gb": memory, "n_jobs": n_jobs}


def _case_catalog_hash(root: Path) -> str:
    source_path = root / "src" / "pertura_bench" / "cases" / "capability_cases.v1.json"
    if source_path.is_file():
        return file_sha256(source_path)
    resource = resources.files("pertura_bench").joinpath(
        "cases", "capability_cases.v1.json"
    )
    with resources.as_file(resource) as packaged:
        return file_sha256(packaged)


def _template_digest(
    *,
    artifacts: list[dict[str, Any]],
    jobs: list[dict[str, Any]],
    datasets: tuple[str, ...],
    case_catalog_hash: str,
) -> str:
    return canonical_hash(
        {
            "schema_version": "pertura-server-benchmark-template-v1",
            "artifacts": artifacts,
            "jobs": jobs,
            "datasets": datasets,
            "scheduler": "neutral",
            "cache_layout": "datasets/{dataset_id}/{artifact_kind}/{artifact_hash}",
            "retry_policy": {
                "max_attempts": 2,
                "retry_on": ["timeout", "worker_lost"],
            },
            "case_catalog_hash": case_catalog_hash,
        }
    )


def _bound_plan_digest(binding: Mapping[str, Any]) -> str:
    return canonical_hash(
        {
            "schema_version": "pertura-bound-server-benchmark-plan-v1",
            "git_commit": binding.get("git_commit"),
            "wheel_sha256": binding.get("wheel_sha256"),
            "case_catalog_hash": binding.get("case_catalog_hash"),
            "agent_case_catalog_hash": binding.get("agent_case_catalog_hash"),
            "skill_bundle_hash": binding.get("skill_bundle_hash"),
            "capability_spec_hash": binding.get("capability_spec_hash"),
            "parameter_schema_hash": binding.get("parameter_schema_hash"),
            "judge_manifest_hash": binding.get("judge_manifest_hash"),
            "report_turn_schema_hash": binding.get("report_turn_schema_hash"),
            "template_digest": binding.get("template_digest"),
            "resource_lock_set_hash": binding.get("resource_lock_set_hash"),
            "subset_catalog_hash": binding.get("subset_catalog_hash"),
            "prediction_bundle_set_hash": binding.get("prediction_bundle_set_hash"),
            "parameter_catalog_hash": binding.get("parameter_catalog_hash"),
            "design_confirmation_catalog_hash": binding.get("design_confirmation_catalog_hash"),
            "metric_reference_catalog_hash": binding.get("metric_reference_catalog_hash"),
            "paper_task_catalog_hash": binding.get("paper_task_catalog_hash"),
            "paper_task_reference_catalog_hash": binding.get("paper_task_reference_catalog_hash"),
            "paper_anchor_catalog_hash": binding.get("paper_anchor_catalog_hash"),
            "paper_asset_catalog_hash": binding.get("paper_asset_catalog_hash"),
        }
    )
