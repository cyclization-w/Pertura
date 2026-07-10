from __future__ import annotations

import re
from importlib import resources
from pathlib import Path
from typing import Any, Mapping

from pertura_bench.capability_models import CapabilityBenchmarkSpec, ServerBenchmarkPlan
from pertura_core.hashing import canonical_hash, file_sha256
from pertura_workflow.capabilities import CapabilityRegistry


_BINDING_FIELDS = (
    "git_commit",
    "wheel_sha256",
    "case_catalog_hash",
    "template_digest",
    "server_plan_hash",
)
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_GIT_COMMIT = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")


def build_server_plan(
    specs: tuple[CapabilityBenchmarkSpec, ...],
    repo_root: str | Path | None = None,
) -> ServerBenchmarkPlan:
    root = (
        Path(__file__).resolve().parents[2]
        if repo_root is None
        else Path(repo_root).resolve()
    )
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
        source_relative = (
            str(manifest.file["name"])
            if manifest.file
            else f"external-sources/{dataset_id}.json"
        )
        converted_relative = (
            f"{dataset_id}.h5ad" if manifest.conversion else source_relative
        )
        artifact_lock_relative = f"{dataset_id}.lock.json"
        artifacts.extend(
            (
                {
                    "artifact_id": artifact_ids["source"],
                    "kind": "source" if manifest.file else "external_source",
                    "dataset_id": dataset_id,
                    "relative_path": source_relative,
                    "lock_relative_path": (
                        artifact_lock_relative if manifest.file else None
                    ),
                },
                {
                    "artifact_id": artifact_ids["converted"],
                    "kind": "converted" if manifest.conversion else "source_ready",
                    "dataset_id": dataset_id,
                    "relative_path": converted_relative,
                    "lock_relative_path": artifact_lock_relative,
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
    from pertura_bench.real_execution import load_real_parameter_catalog

    real_parameter_catalog, real_parameter_catalog_hash = load_real_parameter_catalog()
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
        for dataset_id in bench_spec.required_real_datasets:
            artifact_ids = artifact_ids_by_dataset[dataset_id]
            parameter_coverage = _real_parameter_coverage(
                registry,
                real_parameter_catalog,
                dataset_id=dataset_id,
                runtime_dag=runtime_dag,
                target_capability_id=bench_spec.capability_id,
                target_case=bench_spec.cases[0],
            )
            for tier in ("frozen_subset", "full_dataset"):
                for split in ("calibration", "evaluation"):
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
                            # Scheduler ordering materializes only the locked input.
                            # Scientific upstreams execute below in one persistent runtime.
                            "depends_on": [preparation_dependency],
                            "runtime_execution": {
                                "scope": "single_persistent_pertura_runtime",
                                "dependency_resolution": "runtime_owned",
                                "capability_dag": runtime_dag,
                                "authoritative_commit_store": True,
                            },
                            "real_parameter_catalog": {
                                "version": real_parameter_catalog["catalog_version"],
                                "hash": real_parameter_catalog_hash,
                            },
                            "real_parameter_coverage": [
                                dict(item) | {"tier": tier, "split": split}
                                for item in parameter_coverage
                            ],
                            "configuration_state": (
                                "configured"
                                if all(item["configured"] for item in parameter_coverage)
                                else "not_configured"
                            ),
                            "checkpoint_requirement": {
                                "required": True,
                                "binding_fields": list(_BINDING_FIELDS),
                                "binding_source": "server_plan.checkpoint_binding",
                            },
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
                                ]
                            },
                            "consumes": [artifact_id],
                            "produces": [
                                f"verdict:{dataset_id}:{bench_spec.capability_id}:{tier}:{split}"
                            ],
                            "resources": {
                                "cpus": (
                                    8
                                    if bench_spec.capability_id
                                    == "association.sceptre.v1"
                                    else 4
                                ),
                                "memory_gb": (
                                    64
                                    if dataset_id == "replogle_k562_essential_2022"
                                    else 32
                                ),
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

    case_catalog_hash = _case_catalog_hash(root)
    template_digest = _template_digest(
        artifacts=artifacts,
        jobs=jobs,
        datasets=datasets,
        case_catalog_hash=case_catalog_hash,
    )
    return ServerBenchmarkPlan(
        artifacts=tuple(artifacts),
        jobs=tuple(jobs),
        datasets=datasets,
        checkpoint_binding={
            "git_commit": None,
            "wheel_sha256": None,
            "case_catalog_hash": case_catalog_hash,
            "template_digest": template_digest,
            "server_plan_hash": None,
        },
        executable=False,
    )


def bind_server_plan(
    template: ServerBenchmarkPlan,
    *,
    git_commit: str,
    wheel_sha256: str,
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
    for name in ("wheel_sha256", "case_catalog_hash", "template_digest", "server_plan_hash"):
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
        "resources": {
            "cpus": cpus,
            "memory_gb": memory_gb,
            "walltime_minutes": walltime_minutes,
        },
    }


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
    runtime_dag: list[str],
    target_capability_id: str,
    target_case: Any,
) -> list[dict[str, Any]]:
    dataset = dict(catalog.get("datasets", {}).get(dataset_id) or {})
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
                        f"for {dataset_id}/{key}"
                    )
                ),
            }
        )
    return coverage


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
            "template_digest": binding.get("template_digest"),
        }
    )
