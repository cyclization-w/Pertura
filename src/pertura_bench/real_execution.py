from __future__ import annotations

import json
import os
import tempfile
from importlib import resources
from pathlib import Path
from typing import Any, Iterable, Mapping

from pertura_bench.capability_models import (
    BenchmarkTier,
    CapabilityBenchmarkCase,
    CapabilityBenchmarkSpec,
    CapabilityBenchmarkVerdict,
)
from pertura_bench.models import BenchmarkArtifactLock, BenchmarkSubsetLock
from pertura_bench.synthetic_execution import (
    enum_value,
    force_loopback_transport,
    make_verdict,
    runner_hash,
    scientific_result_digest,
    temporary_environment,
)
from pertura_core.hashing import canonical_hash, file_sha256
from pertura_workflow.capabilities import CapabilityRegistry


_REAL_PARAMETER_RESOURCE = "real_parameters.v1.json"
_CHECKPOINT_ENV = "PERTURA_BENCH_CHECKPOINT_BINDING"


class RealParametersNotConfigured(RuntimeError):
    pass


class RealCapabilityExecutionError(RuntimeError):
    def __init__(
        self,
        status: str,
        message: str,
        *,
        blockers: Iterable[str] = (),
    ) -> None:
        super().__init__(message)
        self.status = status
        self.blockers = tuple(str(item) for item in blockers)


def run_real_tier(
    spec: CapabilityBenchmarkSpec,
    *,
    tier: BenchmarkTier,
    repo_root: str | Path | None,
    dataset_id: str | None,
    split: str | None,
    cache: str | Path | None,
    output: str | Path | None,
) -> list[CapabilityBenchmarkVerdict]:
    if not dataset_id:
        raise ValueError("real-data benchmark execution requires --dataset")
    if dataset_id not in spec.required_real_datasets:
        raise ValueError(f"{dataset_id} is not declared for {spec.capability_id}")
    if not cache:
        raise ValueError("real-data benchmark execution requires --cache")
    if split not in {"calibration", "evaluation"}:
        raise ValueError("real-data benchmark execution requires --split")
    if tier not in {"frozen_subset", "full_dataset"}:
        raise ValueError("real-data execution requires frozen_subset or full_dataset tier")

    root = _default_repo_root() if repo_root is None else Path(repo_root).resolve()
    registry = CapabilityRegistry.load_default(include_external=False)
    capability = registry.get(spec.capability_id, spec.capability_version)
    parameter_catalog, parameter_catalog_hash = load_real_parameter_catalog()
    case = _real_case(
        spec,
        tier,
        dataset_id,
        split=split,
        parameter_catalog=parameter_catalog,
        parameter_catalog_hash=parameter_catalog_hash,
    )
    input_hashes = _real_identity_hashes(
        root,
        case=case,
        spec=capability,
        dataset_id=dataset_id,
        split=split,
        tier=tier,
        parameter_catalog_hash=parameter_catalog_hash,
    )
    try:
        artifact, lock_hashes = resolve_real_artifact_chain(
            root,
            dataset_id=dataset_id,
            tier=tier,
            split=split,
            cache=cache,
        )
    except FileNotFoundError as exc:
        return [
            make_verdict(
                case,
                outcome="not_available",
                observed_status=None,
                reasons=(str(exc),),
                input_hashes=input_hashes,
                runner_hash=runner_hash(capability.executor),
            )
        ]
    except ValueError as exc:
        return [
            make_verdict(
                case,
                outcome="failed",
                observed_status="artifact_lock_invalid",
                blockers=(str(exc),),
                reasons=(str(exc),),
                input_hashes=input_hashes,
                runner_hash=runner_hash(capability.executor),
            )
        ]
    input_hashes.update(lock_hashes)
    verdict = _invoke_locked_product_case(
        case,
        artifact,
        input_hashes,
        repo_root=root,
        split=split,
        parameter_catalog=parameter_catalog,
        parameter_catalog_hash=parameter_catalog_hash,
    )
    if output:
        destination = Path(output).expanduser().resolve()
        destination.mkdir(parents=True, exist_ok=True)
        path = destination / (
            f"{dataset_id}__{spec.capability_id}__{tier}__{split}.json"
        )
        path.write_text(
            json.dumps(verdict.model_dump(mode="json"), indent=2, sort_keys=True)
            + "\n",
            encoding="utf-8",
            newline="\n",
        )
    return [verdict]


def _real_case(
    spec: CapabilityBenchmarkSpec,
    tier: BenchmarkTier,
    dataset_id: str,
    *,
    split: str,
    parameter_catalog: Mapping[str, Any],
    parameter_catalog_hash: str,
) -> CapabilityBenchmarkCase:
    template = spec.cases[0]
    case_override = template.parameters.get("real_execution")
    return CapabilityBenchmarkCase(
        capability_id=spec.capability_id,
        capability_version=spec.capability_version,
        tier=tier,
        scenario="happy",
        fixture_id=f"locked/{dataset_id}/{tier}/{split}",
        execution_mode="product_path",
        dataset_id=dataset_id,
        parameters={
            "split": split,
            "real_parameter_catalog_version": parameter_catalog["catalog_version"],
            "real_parameter_catalog_hash": parameter_catalog_hash,
            "real_execution": dict(case_override) if isinstance(case_override, Mapping) else {},
        },
        expected_statuses=(
            "screen_passed",
            "caution",
            "completed",
            "completed_with_caution",
        ),
        environment_profile=template.environment_profile,
        environment_required=template.environment_required,
    )


def load_real_parameter_catalog(
    path: str | Path | None = None,
) -> tuple[dict[str, Any], str]:
    if path is None:
        resource = resources.files("pertura_bench").joinpath(
            "cases", _REAL_PARAMETER_RESOURCE
        )
        with resources.as_file(resource) as packaged:
            source = Path(packaged)
            payload = json.loads(source.read_text(encoding="utf-8"))
            digest = file_sha256(source)
    else:
        source = Path(path).expanduser().resolve()
        payload = json.loads(source.read_text(encoding="utf-8"))
        digest = file_sha256(source)
    if payload.get("schema_version") != "pertura-real-parameter-catalog-v1":
        raise ValueError("unsupported real parameter catalog schema")
    if not payload.get("catalog_version") or not isinstance(payload.get("datasets"), dict):
        raise ValueError("real parameter catalog lacks versioned dataset mappings")
    for dataset_id, dataset in payload["datasets"].items():
        if not isinstance(dataset, dict) or not isinstance(
            dataset.get("capabilities"), dict
        ):
            raise ValueError(
                f"real parameter catalog dataset mapping is invalid: {dataset_id}"
            )
    return payload, digest


def resolve_real_artifact_chain(
    repo_root: str | Path,
    *,
    dataset_id: str,
    tier: BenchmarkTier,
    split: str,
    cache: str | Path,
) -> tuple[Path, dict[str, str]]:
    from pertura_bench.operations import source_manifests

    root = Path(repo_root).resolve()
    manifests = source_manifests(root)
    if dataset_id not in manifests:
        raise ValueError(f"unknown benchmark dataset: {dataset_id}")
    manifest = manifests[dataset_id][1]
    cache_root = Path(cache).expanduser().resolve()
    dataset_root = cache_root / "datasets" / dataset_id
    artifact_lock_path = first_existing(
        (
            dataset_root / "converted" / "artifact.lock.json",
            cache_root / f"{dataset_id}.lock.json",
        )
    )
    artifact_sidecar_path = first_existing(
        (
            dataset_root / "converted" / "artifact.local.json",
            cache_root / f"{dataset_id}.local.json",
        )
    )
    if artifact_lock_path is None or artifact_sidecar_path is None:
        raise FileNotFoundError(
            f"not_available: converted artifact lock chain is missing for {dataset_id}"
        )
    artifact_lock = BenchmarkArtifactLock.model_validate_json(
        artifact_lock_path.read_text(encoding="utf-8")
    )
    if artifact_lock.dataset_id != dataset_id:
        raise ValueError("artifact lock dataset mismatch")
    if artifact_lock.source_manifest_hash != manifest.canonical_hash:
        raise ValueError("artifact lock source manifest hash drift")
    artifact_path = _sidecar_artifact(
        artifact_sidecar_path,
        cache_root,
        expected_lock_id=artifact_lock.lock_id,
    )
    if manifest.conversion:
        conversion_script = (root / manifest.conversion).resolve()
        if root not in conversion_script.parents or not conversion_script.is_file():
            raise ValueError(
                "benchmark conversion script is missing or escapes the repository"
            )
        if artifact_lock.conversion_script_hash != file_sha256(conversion_script):
            raise ValueError("artifact lock conversion script hash drift")
    _validate_locked_file(
        artifact_path,
        artifact_lock.artifact_sha256,
        expected_size=artifact_lock.size_bytes,
    )
    hashes = {
        "source_manifest": manifest.canonical_hash,
        "artifact_lock": artifact_lock.canonical_hash,
        "artifact": artifact_lock.artifact_sha256,
    }
    if tier == "full_dataset":
        return artifact_path, hashes
    subset_root = dataset_root / "subset" / split
    subset_lock_path = first_existing(
        (
            subset_root / "subset.lock.json",
            cache_root / f"{dataset_id}.{split}.subset.lock.json",
        )
    )
    subset_sidecar_path = first_existing(
        (
            subset_root / "subset.local.json",
            cache_root / f"{dataset_id}.{split}.subset.local.json",
        )
    )
    if subset_lock_path is None or subset_sidecar_path is None:
        raise FileNotFoundError(
            f"not_available: {split} subset lock chain is missing for {dataset_id}"
        )
    subset_lock = BenchmarkSubsetLock.model_validate_json(
        subset_lock_path.read_text(encoding="utf-8")
    )
    if subset_lock.dataset_id != dataset_id:
        raise ValueError("subset lock dataset mismatch")
    if subset_lock.source_lock_hash != artifact_lock.canonical_hash:
        raise ValueError("subset lock is not bound to the current artifact lock")
    subset_path = _sidecar_artifact(
        subset_sidecar_path,
        cache_root,
        expected_lock_id=subset_lock.subset_lock_id,
    )
    from pertura_bench import operations as benchmark_operations

    if subset_lock.subset_script_hash != file_sha256(
        Path(benchmark_operations.__file__)
    ):
        raise ValueError("subset lock script hash drift")
    _validate_locked_file(subset_path, subset_lock.output_sha256)
    hashes.update(
        {
            "subset_lock": subset_lock.canonical_hash,
            "subset": subset_lock.output_sha256,
            "subset_spec": subset_lock.subset_spec_hash,
        }
    )
    return subset_path, hashes


def first_existing(candidates: Iterable[Path]) -> Path | None:
    return next((path for path in candidates if path.is_file()), None)


def _sidecar_artifact(
    path: Path,
    cache_root: Path,
    *,
    expected_lock_id: str | None = None,
) -> Path:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if expected_lock_id is not None and payload.get("lock_id") != expected_lock_id:
        raise ValueError("local benchmark sidecar lock identity mismatch")
    value = payload.get("artifact_path")
    if not value:
        raise ValueError(f"local benchmark sidecar lacks artifact_path: {path}")
    artifact = Path(str(value)).expanduser().resolve()
    if cache_root != artifact and cache_root not in artifact.parents:
        raise ValueError("local benchmark sidecar escapes the declared cache")
    if not artifact.is_file():
        raise FileNotFoundError(f"not_available: locked artifact is missing: {artifact}")
    return artifact


def _validate_locked_file(
    path: Path, expected_hash: str, *, expected_size: int | None = None
) -> None:
    if expected_size is not None and path.stat().st_size != expected_size:
        raise ValueError("locked benchmark artifact size mismatch")
    if file_sha256(path) != expected_hash:
        raise ValueError("locked benchmark artifact checksum mismatch")


def _invoke_locked_product_case(
    case: CapabilityBenchmarkCase,
    artifact: Path,
    input_hashes: dict[str, str],
    *,
    repo_root: Path,
    split: str,
    parameter_catalog: Mapping[str, Any],
    parameter_catalog_hash: str,
) -> CapabilityBenchmarkVerdict:
    from pertura_runtime.claude.workspace import ClaudeRunWorkspace
    from pertura_runtime.product import PerturaProductRuntime

    registry = CapabilityRegistry.load_default(include_external=False)
    spec = registry.get(case.capability_id, case.capability_version)
    try:
        checkpoint = _load_checkpoint_binding(repo_root)
    except (FileNotFoundError, ValueError) as exc:
        return make_verdict(
            case,
            outcome="failed",
            observed_status="checkpoint_not_bound",
            blockers=(str(exc),),
            reasons=("real-data execution is fail-closed until the server plan is checkpoint-bound",),
            input_hashes=input_hashes,
            runner_hash=runner_hash(spec.executor),
        )
    input_hashes = dict(input_hashes)
    input_hashes.update(
        {
            "checkpoint_git": checkpoint["git_commit"],
            "checkpoint_wheel": checkpoint["wheel_sha256"],
            "checkpoint_plan": checkpoint["server_plan_hash"],
        }
    )

    with tempfile.TemporaryDirectory(prefix="pertura-real-bench-") as directory:
        root = Path(directory)
        workspace = ClaudeRunWorkspace.create(
            root=root / "runs",
            input_source=artifact,
            run_id=f"real-{case.dataset_id}-{case.tier}-{split}",
        )
        with temporary_environment("PERTURA_AUTHORITY_ROOT", str(root / "authority")):
            runtime = PerturaProductRuntime(workspace)
            force_loopback_transport(runtime)
            try:
                confirmations = _dataset_confirmations(
                    parameter_catalog,
                    dataset_id=str(case.dataset_id),
                    case=case,
                )
                summary = runtime.inspect_dataset(
                    artifact,
                    dataset_id=case.dataset_id,
                    confirmations=(confirmations or None),
                )
                result, execution_order = execute_capability_dag(
                    runtime,
                    registry=registry,
                    target_capability_id=case.capability_id,
                    target_capability_version=case.capability_version,
                    contract_id=str(summary["contract_id"]),
                    artifact=artifact,
                    dataset_id=str(case.dataset_id),
                    tier=case.tier,
                    split=split,
                    lock_hashes=input_hashes,
                    parameter_catalog=parameter_catalog,
                    parameter_catalog_hash=parameter_catalog_hash,
                    case=case,
                )
                report = runtime.finalize_report(workspace.root.name)
                if report.get("result_count", 0) < len(execution_order):
                    raise RealCapabilityExecutionError(
                        "authority_projection_incomplete",
                        "final report omitted one or more committed DAG results",
                    )
            except RealParametersNotConfigured as exc:
                return make_verdict(
                    case,
                    outcome="failed",
                    observed_status="not_configured",
                    blockers=(str(exc),),
                    reasons=("dataset-specific real capability parameters are not configured",),
                    input_hashes=input_hashes,
                    runner_hash=runner_hash(spec.executor),
                )
            except RealCapabilityExecutionError as exc:
                outcome = (
                    "not_run_environment_missing"
                    if exc.status == "environment_missing"
                    else "failed"
                )
                return make_verdict(
                    case,
                    outcome=outcome,
                    observed_status=exc.status,
                    blockers=exc.blockers or (str(exc),),
                    reasons=(str(exc),),
                    input_hashes=input_hashes,
                    runner_hash=runner_hash(spec.executor),
                )
            except Exception as exc:
                return make_verdict(
                    case,
                    outcome="failed",
                    observed_status="runner_failed",
                    blockers=(str(exc),),
                    reasons=(
                        "locked artifact reached the persistent product runtime but execution failed",
                    ),
                    input_hashes=input_hashes,
                    runner_hash=runner_hash(spec.executor),
                )
            finally:
                runtime.close(graceful=True)
    digest = scientific_result_digest(result)
    status = enum_value(result["status"])
    accepted = status in case.expected_statuses
    return make_verdict(
        case,
        outcome="passed" if accepted else "failed",
        observed_status=status,
        blockers=tuple(result.get("blockers") or ()),
        input_hashes=input_hashes,
        output_hashes=dict(result.get("output_hashes") or {}),
        runner_hash=runner_hash(spec.executor),
        reasons=() if accepted else (f"unexpected real-data status: {status}",),
        scientific_hash=digest.canonical_hash,
        environment_lock_hash=input_hashes.get("environment_lock"),
    )


def execute_capability_dag(
    runtime: Any,
    *,
    registry: CapabilityRegistry,
    target_capability_id: str,
    target_capability_version: str,
    contract_id: str,
    artifact: Path,
    dataset_id: str,
    tier: BenchmarkTier,
    split: str,
    lock_hashes: Mapping[str, str],
    parameter_catalog: Mapping[str, Any],
    parameter_catalog_hash: str,
    case: CapabilityBenchmarkCase,
) -> tuple[dict[str, Any], tuple[str, ...]]:
    """Execute the complete scientific DAG in one runtime and authority store.

    Dependencies are never supplied by the benchmark harness. Each product call
    asks Pertura's resolver to select authoritative committed upstream results.
    """

    from pertura_bench.server_plan import _capability_runtime_dag

    execution_order = _capability_runtime_dag(registry, target_capability_id)
    target_spec = registry.get(target_capability_id, target_capability_version)
    if execution_order[-1] != target_spec.capability_id:
        raise RealCapabilityExecutionError(
            "dag_invalid", "target capability is not the terminal DAG node"
        )

    # Fail before scientific execution if any node lacks an explicit mapping.
    mappings = {
        capability_id: _real_parameter_mapping(
            parameter_catalog,
            dataset_id=dataset_id,
            spec=registry.get(capability_id),
            case=(case if capability_id == target_capability_id else None),
        )
        for capability_id in execution_order
    }
    committed_by_capability: dict[str, dict[str, Any]] = {}
    for capability_id in execution_order:
        spec = registry.get(capability_id)
        parameters = _materialize_parameters(
            mappings[capability_id],
            artifact=artifact,
            workspace_root=Path(runtime.workspace.root),
            committed_by_capability=committed_by_capability,
        )
        parameters["benchmark_context"] = {
            "schema_version": "pertura-real-benchmark-context-v1",
            "dataset_id": dataset_id,
            "capability_id": spec.capability_id,
            "capability_version": spec.version,
            "tier": tier,
            "split": split,
            "artifact_lock_hashes": dict(sorted(lock_hashes.items())),
            "real_parameter_catalog_hash": parameter_catalog_hash,
        }
        if spec.kind == "diagnostic":
            compact = runtime.run_diagnostic(
                capability_id,
                contract_id=contract_id,
                parameters=parameters,
            )
        elif spec.kind == "analysis":
            compact = runtime.run_analysis(
                capability_id,
                capability_id=capability_id,
                contract_id=contract_id,
                parameters=parameters,
            )
        else:
            raise RealCapabilityExecutionError(
                "unsupported_capability_kind",
                f"real benchmark cannot execute {spec.kind} capability {capability_id}",
            )
        result_id = compact.get("result_id")
        if not result_id:
            blockers = tuple(str(item) for item in compact.get("blockers") or ())
            joined = " ".join(blockers).lower()
            status = (
                "environment_missing"
                if "environment" in joined and "unavailable" in joined
                else "capability_blocked"
            )
            raise RealCapabilityExecutionError(
                status,
                f"capability DAG node did not commit a result: {capability_id}",
                blockers=blockers,
            )
        committed = runtime.broker.list_committed(runtime.workspace.root.name)
        authoritative = [
            item["result"]
            for item in committed
            if item["result"].get("result_id") == result_id
        ]
        if len(authoritative) != 1:
            raise RealCapabilityExecutionError(
                "authoritative_result_missing",
                f"broker commit store did not contain exactly one result {result_id}",
            )
        result = authoritative[0]
        if (
            result.get("capability_id") != spec.capability_id
            or result.get("capability_version") != spec.version
        ):
            raise RealCapabilityExecutionError(
                "authoritative_result_mismatch",
                f"committed result identity does not match {spec.capability_id}@{spec.version}",
            )
        committed_by_capability[capability_id] = result
    return committed_by_capability[target_capability_id], execution_order


def _real_parameter_mapping(
    catalog: Mapping[str, Any],
    *,
    dataset_id: str,
    spec: Any,
    case: CapabilityBenchmarkCase | None,
) -> dict[str, Any]:
    dataset = dict(catalog.get("datasets", {}).get(dataset_id) or {})
    key = f"{spec.capability_id}@{spec.version}"
    entry = dict(dataset.get("capabilities", {}).get(key) or {})
    parameters = entry.get("parameters")
    override: Mapping[str, Any] | None = None
    if case is not None:
        raw = case.parameters.get("real_execution")
        if isinstance(raw, Mapping):
            override = raw
    if parameters is None and not (override and "parameters" in override):
        raise RealParametersNotConfigured(
            "real parameter mapping is not configured for "
            f"dataset={dataset_id}, capability={key}, "
            f"catalog={catalog.get('catalog_version')}"
        )
    merged = dict(parameters or {})
    if override and isinstance(override.get("parameters"), Mapping):
        merged.update(dict(override["parameters"]))
    return merged


def _materialize_parameters(
    value: Mapping[str, Any],
    *,
    artifact: Path,
    workspace_root: Path,
    committed_by_capability: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    return {
        key: _materialize_parameter_value(
            item,
            artifact=artifact,
            workspace_root=workspace_root,
            committed_by_capability=committed_by_capability,
        )
        for key, item in value.items()
    }


def _materialize_parameter_value(
    value: Any,
    *,
    artifact: Path,
    workspace_root: Path,
    committed_by_capability: Mapping[str, Mapping[str, Any]],
) -> Any:
    if isinstance(value, Mapping):
        if set(value) == {"artifact_ref"}:
            if value["artifact_ref"] != "primary":
                raise RealParametersNotConfigured(
                    f"unsupported benchmark artifact_ref: {value['artifact_ref']}"
                )
            return str(artifact)
        if set(value) == {"upstream_output"}:
            reference = value["upstream_output"]
            if not isinstance(reference, Mapping):
                raise RealParametersNotConfigured("upstream_output must be a structured mapping")
            capability_id = str(reference.get("capability_id") or "")
            filename = str(reference.get("filename") or "")
            if not capability_id or not filename:
                raise RealParametersNotConfigured(
                    "upstream_output requires capability_id and filename"
                )
            result = committed_by_capability.get(capability_id)
            if result is None:
                raise RealCapabilityExecutionError(
                    "upstream_result_missing",
                    f"parameter references an uncommitted upstream result: {capability_id}",
                )
            matches: list[Path] = []
            for item in result.get("output_paths") or ():
                output = Path(str(item))
                if not output.is_absolute():
                    output = workspace_root / output
                output = output.resolve()
                if output.name == filename:
                    matches.append(output)
            if len(matches) != 1 or not matches[0].is_file():
                raise RealCapabilityExecutionError(
                    "upstream_output_missing",
                    f"expected exactly one committed output {capability_id}/{filename}",
                )
            if workspace_root.resolve() not in matches[0].parents:
                raise RealCapabilityExecutionError(
                    "upstream_output_escaped_workspace",
                    f"upstream output escaped the run workspace: {matches[0]}",
                )
            return str(matches[0])
        return {
            str(key): _materialize_parameter_value(
                item,
                artifact=artifact,
                workspace_root=workspace_root,
                committed_by_capability=committed_by_capability,
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [
            _materialize_parameter_value(
                item,
                artifact=artifact,
                workspace_root=workspace_root,
                committed_by_capability=committed_by_capability,
            )
            for item in value
        ]
    return value


def _dataset_confirmations(
    catalog: Mapping[str, Any],
    *,
    dataset_id: str,
    case: CapabilityBenchmarkCase,
) -> dict[str, Any]:
    dataset = dict(catalog.get("datasets", {}).get(dataset_id) or {})
    confirmations = dict(dataset.get("contract_confirmations") or {})
    override = case.parameters.get("real_execution")
    if isinstance(override, Mapping) and isinstance(
        override.get("contract_confirmations"), Mapping
    ):
        confirmations.update(dict(override["contract_confirmations"]))
    return confirmations


def _load_checkpoint_binding(repo_root: Path) -> dict[str, str]:
    binding_path = os.environ.get(_CHECKPOINT_ENV)
    if not binding_path:
        raise FileNotFoundError(
            f"{_CHECKPOINT_ENV} is not set to a bound server-plan JSON file"
        )
    path = Path(binding_path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"checkpoint binding file is missing: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    binding = payload.get("checkpoint_binding", payload)
    if not isinstance(binding, Mapping):
        raise ValueError("checkpoint binding payload is invalid")

    # Local imports avoid a module cycle while capability_bench imports this file.
    from pertura_bench.capability_bench import benchmark_specs
    from pertura_bench.server_plan import build_server_plan, validate_checkpoint_binding

    template = build_server_plan(benchmark_specs(), repo_root)
    return validate_checkpoint_binding(template, binding)


def _real_identity_hashes(
    repo_root: Path,
    *,
    case: CapabilityBenchmarkCase,
    spec: Any,
    dataset_id: str,
    split: str,
    tier: BenchmarkTier,
    parameter_catalog_hash: str,
) -> dict[str, str]:
    hashes = {
        "case": case.canonical_hash,
        "catalog": _case_catalog_hash(repo_root),
        "capability_spec": spec.canonical_hash,
        "product_spine": _product_spine_hash(),
        "dataset": canonical_hash({"dataset_id": dataset_id}),
        "split": canonical_hash({"split": split}),
        "tier": canonical_hash({"tier": tier}),
        "real_parameter_catalog": parameter_catalog_hash,
    }
    profile = str(spec.metadata.get("environment_profile") or "")
    if profile:
        try:
            from pertura_workflow.environment import environment_lock

            lock = environment_lock(profile)
        except RuntimeError:
            lock = None
        if lock and lock.get("lock_hash"):
            hashes["environment_lock"] = str(lock["lock_hash"])
    return hashes


def _case_catalog_hash(repo_root: Path) -> str:
    path = repo_root / "src" / "pertura_bench" / "cases" / "capability_cases.v1.json"
    if path.is_file():
        return file_sha256(path)
    resource = resources.files("pertura_bench").joinpath(
        "cases", "capability_cases.v1.json"
    )
    with resources.as_file(resource) as packaged:
        return file_sha256(packaged)


def _product_spine_hash() -> str:
    import pertura_runtime.product as product_module
    import pertura_runtime.verifier.broker as broker_module
    import pertura_workflow.planner as planner_module

    return canonical_hash(
        {
            "product": file_sha256(Path(product_module.__file__)),
            "planner": file_sha256(Path(planner_module.__file__)),
            "broker": file_sha256(Path(broker_module.__file__)),
            "real_execution": file_sha256(Path(__file__)),
        }
    )


def _default_repo_root() -> Path:
    return Path(__file__).resolve().parents[2]
