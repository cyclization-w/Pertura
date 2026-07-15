from __future__ import annotations

import hashlib
import json
import os
import re
import time
from uuid import uuid4
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
_DESIGN_CONFIRMATION_RESOURCE = "design_confirmations.v1.json"
_METRIC_REFERENCE_RESOURCE = "metric_references.v1.json"
_REFERENCE_GENERATOR_RESOURCE = "reference_generators.v1.json"
_CHECKPOINT_ENV = "PERTURA_BENCH_CHECKPOINT_BINDING"
_WHEEL_ENV = "PERTURA_BENCH_WHEEL"
_PARAMETER_CATALOG_ENV = "PERTURA_BENCH_PARAMETER_CATALOG"
_DESIGN_CATALOG_ENV = "PERTURA_BENCH_DESIGN_CONFIRMATIONS"
_METRIC_CATALOG_ENV = "PERTURA_BENCH_METRIC_REFERENCES"


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
    parameter_catalog_path: str | Path | None = None,
    design_confirmations_path: str | Path | None = None,
    metric_reference_catalog_path: str | Path | None = None,
    resource_evidence_path: str | Path | None = None,
    enforced_memory_gb: float | None = None,
    enforced_n_jobs: int | None = None,
) -> list[CapabilityBenchmarkVerdict]:
    if not dataset_id:
        raise ValueError("real-data benchmark execution requires --dataset")
    from pertura_bench.real_run_policy import real_runs_for_spec

    declared_runs = {
        (item["dataset_id"], item["tier"], item["split"])
        for item in real_runs_for_spec(spec)
    }
    if (dataset_id, str(tier), str(split)) not in declared_runs:
        raise ValueError(
            "requested real benchmark run is not declared by the frozen run policy: "
            f"{dataset_id}/{spec.capability_id}/{tier}/{split}"
        )
    if not cache:
        raise ValueError("real-data benchmark execution requires --cache")
    if split not in {"calibration", "evaluation"}:
        raise ValueError("real-data benchmark execution requires --split")
    if tier not in {"frozen_subset", "full_dataset"}:
        raise ValueError("real-data execution requires frozen_subset or full_dataset tier")
    if tier == "full_dataset" and split != "evaluation":
        raise ValueError("full_dataset is an evaluation-only benchmark tier")
    if output is None:
        raise ValueError(
            "real-data benchmark execution requires --output so the product workspace is preserved"
        )

    root = _default_repo_root() if repo_root is None else Path(repo_root).resolve()
    registry = CapabilityRegistry.load_default(include_external=False)
    capability = registry.get(spec.capability_id, spec.capability_version)
    parameter_catalog, parameter_catalog_hash = load_real_parameter_catalog(parameter_catalog_path)
    design_catalog, design_catalog_hash = load_design_confirmation_catalog(design_confirmations_path)
    metric_catalog, metric_catalog_hash = load_metric_reference_catalog(metric_reference_catalog_path)
    case = _real_case(
        spec,
        tier,
        dataset_id,
        split=split,
        parameter_catalog=parameter_catalog,
        parameter_catalog_hash=parameter_catalog_hash,
        design_catalog_hash=design_catalog_hash,
        metric_catalog_hash=metric_catalog_hash,
    )
    metric_entry = _select_metric_entry(
        metric_catalog,
        dataset_id=dataset_id,
        identity=f"{spec.capability_id}@{spec.capability_version}",
        tier=str(tier),
        split=str(split),
        namespace="capabilities",
    )
    input_hashes = _real_identity_hashes(
        root,
        case=case,
        spec=capability,
        dataset_id=dataset_id,
        split=split,
        tier=tier,
        parameter_catalog_hash=parameter_catalog_hash,
        design_catalog_hash=design_catalog_hash,
        metric_catalog_hash=metric_catalog_hash,
    )
    if not metric_entry:
        return [
            make_verdict(
                case,
                outcome="not_configured",
                observed_status=None,
                reasons=(
                    "frozen metric references are not configured for "
                    f"{dataset_id}/{spec.capability_id}@{spec.capability_version}/"
                    f"{tier}:{split}",
                ),
                input_hashes=input_hashes,
                runner_hash=runner_hash(capability.executor),
                scientific_metrics_status="not_available",
                reference_hashes={
                    "metric_reference_catalog": metric_catalog_hash
                },
            )
        ]
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
    execution_root = (
        Path(output).expanduser().resolve()
        / dataset_id
        / spec.capability_id
        / str(tier)
        / str(split)
        / uuid4().hex
    )
    execution_root.mkdir(parents=True, exist_ok=False)
    verdict = _invoke_locked_product_case(
        case,
        artifact,
        input_hashes,
        repo_root=root,
        split=split,
        parameter_catalog=parameter_catalog,
        parameter_catalog_hash=parameter_catalog_hash,
        design_catalog=design_catalog,
        metric_catalog=metric_catalog,
        metric_catalog_hash=metric_catalog_hash,
        parameter_catalog_path=parameter_catalog_path,
        design_confirmations_path=design_confirmations_path,
        metric_reference_catalog_path=metric_reference_catalog_path,
        cache_root=Path(cache).expanduser().resolve(),
        execution_root=execution_root,
        resource_evidence_path=resource_evidence_path,
        enforced_memory_gb=enforced_memory_gb,
        enforced_n_jobs=enforced_n_jobs,
    )
    (execution_root / "execution_verdict.json").write_text(
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
    design_catalog_hash: str,
    metric_catalog_hash: str,
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
            "design_confirmation_catalog_hash": design_catalog_hash,
            "metric_reference_catalog_hash": metric_catalog_hash,
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
    if path is None and os.environ.get(_PARAMETER_CATALOG_ENV):
        path = os.environ[_PARAMETER_CATALOG_ENV]
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
    if payload.get("schema_version") not in {
        "pertura-real-parameter-catalog-v1",
        "pertura-real-parameter-catalog-v2",
    }:
        raise ValueError("unsupported real parameter catalog schema")
    if not payload.get("catalog_version") or not isinstance(payload.get("datasets"), dict):
        raise ValueError("real parameter catalog lacks versioned dataset mappings")
    catalog_v2 = payload.get("schema_version") == "pertura-real-parameter-catalog-v2"
    registry = None
    if catalog_v2:
        from jsonschema import Draft202012Validator
        from pertura_workflow.capabilities import CapabilityRegistry

        registry = CapabilityRegistry.load_default(include_external=False)
    for dataset_id, dataset in payload["datasets"].items():
        if not isinstance(dataset, dict):
            raise ValueError(
                f"real parameter catalog dataset mapping is invalid: {dataset_id}"
            )
        if catalog_v2:
            tiers = dataset.get("tiers")
            if not isinstance(tiers, dict) or not tiers:
                raise ValueError(
                    f"v2 real parameter catalog requires tiers: {dataset_id}"
                )
            mappings = []
            for tier, tier_payload in tiers.items():
                splits = (
                    tier_payload.get("splits")
                    if isinstance(tier_payload, dict)
                    else None
                )
                if not isinstance(splits, dict) or not splits:
                    raise ValueError(
                        f"v2 real parameter tier requires splits: {dataset_id}/{tier}"
                    )
                mappings.extend(
                    (f"{tier}:{split}", mapping)
                    for split, mapping in splits.items()
                )
        else:
            runs = dataset.get("runs")
            if runs is not None:
                if not isinstance(runs, dict) or not runs:
                    raise ValueError(f"real parameter runs are invalid: {dataset_id}")
                mappings = list(runs.items())
            else:
                mappings = [("legacy", dataset)]
        for run_key, mapping in mappings:
            if not isinstance(mapping, dict) or not isinstance(
                mapping.get("capabilities"), dict
            ):
                raise ValueError(
                    f"real parameter run mapping is invalid: {dataset_id}/{run_key}"
                )
            _validate_agent_assets(
                mapping.get("agent_assets") or [],
                context=f"{dataset_id}/{run_key}",
            )
            if catalog_v2:
                assert registry is not None
                for identity, entry in mapping["capabilities"].items():
                    try:
                        capability_id, version = str(identity).rsplit("@", 1)
                        spec = registry.get(capability_id, version)
                    except (KeyError, ValueError) as exc:
                        raise ValueError(
                            f"unknown capability mapping: {dataset_id}/{run_key}/{identity}"
                        ) from exc
                    parameters = entry.get("parameters") if isinstance(entry, dict) else None
                    if not isinstance(parameters, dict):
                        raise ValueError(
                            f"capability parameters are missing: {dataset_id}/{run_key}/{identity}"
                        )
                    materialized = _catalog_schema_projection(parameters, spec.parameters_schema)
                    errors = sorted(
                        Draft202012Validator(spec.parameters_schema).iter_errors(materialized),
                        key=lambda item: list(item.path),
                    )
                    if errors:
                        detail = "; ".join(error.message for error in errors)
                        raise ValueError(
                            f"invalid real parameters: {dataset_id}/{run_key}/{identity}: {detail}"
                        )
    return payload, digest


def _catalog_schema_projection(value: Any, schema: Mapping[str, Any] | None = None) -> Any:
    """Replace lock-bound catalog references with schema-valid inert identities."""

    if isinstance(value, Mapping):
        if set(value) in ({"artifact_ref"}, {"asset_ref"}, {"upstream_output"}):
            return "asset_catalog_validation"
        properties = dict((schema or {}).get("properties") or {})
        return {
            key: _catalog_schema_projection(item, properties.get(key))
            for key, item in value.items()
        }
    if isinstance(value, list):
        item_schema = dict((schema or {}).get("items") or {})
        return [_catalog_schema_projection(item, item_schema) for item in value]
    return value


def _validate_agent_assets(assets: Any, *, context: str) -> None:
    if not isinstance(assets, list):
        raise ValueError(f"agent_assets must be a list: {context}")
    roles: set[str] = set()
    for asset in assets:
        if not isinstance(asset, dict):
            raise ValueError(f"agent asset mapping is invalid: {context}")
        role = str(asset.get("role") or "")
        relative_text = str(asset.get("relative_path") or "")
        relative = Path(relative_text)
        digest_value = str(asset.get("content_sha256") or "")
        subset_lock_hash = str(asset.get("subset_lock_hash") or "")
        if (
            not role
            or re.fullmatch(r"[a-z][a-z0-9_.-]*", role) is None
            or role == "primary_dataset"
            or role in roles
            or not relative_text
            or "\\" in relative_text
            or ":" in relative_text
            or relative.is_absolute()
            or ".." in relative.parts
            or re.fullmatch(r"sha256:[0-9a-f]{64}", digest_value) is None
            or (
                subset_lock_hash
                and re.fullmatch(r"sha256:[0-9a-f]{64}", subset_lock_hash) is None
            )
        ):
            raise ValueError(
                f"agent asset identity is invalid: {context}/{role or 'unknown'}"
            )
        roles.add(role)


def select_real_parameter_run(
    catalog: Mapping[str, Any],
    *,
    dataset_id: str,
    tier: str,
    split: str,
) -> dict[str, Any]:
    dataset = dict(catalog.get("datasets", {}).get(dataset_id) or {})
    if catalog.get("schema_version") == "pertura-real-parameter-catalog-v2":
        selected = (
            dataset.get("tiers", {}).get(tier, {}).get("splits", {}).get(split)
        )
        if not isinstance(selected, Mapping):
            raise RealParametersNotConfigured(
                f"split-scoped real parameters are not configured: {dataset_id}/{tier}/{split}"
            )
        return dict(selected)
    runs = dataset.get("runs")
    if not isinstance(runs, Mapping):
        return dataset
    key = f"{tier}:{split}"
    selected = runs.get(key)
    if not isinstance(selected, Mapping):
        raise RealParametersNotConfigured(
            f"split-scoped real parameters are not configured: {dataset_id}/{key}"
        )
    merged = {
        key: value
        for key, value in dataset.items()
        if key not in {"runs", "capabilities", "agent_assets"}
    }
    merged.update(dict(selected))
    return merged


def _resolve_catalog_assets(
    catalog: Mapping[str, Any],
    *,
    dataset_id: str,
    cache_root: Path,
    tier: str = "frozen_subset",
    split: str = "evaluation",
    expected_subset_lock_hash: str | None = None,
) -> dict[str, tuple[Path, str]]:
    dataset = select_real_parameter_run(
        catalog, dataset_id=dataset_id, tier=tier, split=split
    )
    resolved: dict[str, tuple[Path, str]] = {}
    cache_root = cache_root.resolve()
    for raw in dataset.get("agent_assets") or ():
        role = str(raw.get("role") or "")
        relative_path = str(raw.get("relative_path") or "")
        expected_hash = str(raw.get("content_sha256") or "")
        bound_subset = str(raw.get("subset_lock_hash") or "")
        if expected_subset_lock_hash and bound_subset != expected_subset_lock_hash:
            raise RealParametersNotConfigured(
                f"auxiliary asset is not bound to the active subset: {dataset_id}/{role}"
            )
        candidate = (cache_root / relative_path).resolve()
        if candidate != cache_root and cache_root not in candidate.parents:
            raise RealParametersNotConfigured(
                f"auxiliary asset escapes benchmark cache: {relative_path}"
            )
        if not candidate.exists():
            raise RealParametersNotConfigured(
                f"auxiliary asset is missing: {dataset_id}/{role}"
            )
        observed_hash = _portable_path_hash(candidate)
        if observed_hash != expected_hash:
            raise RealParametersNotConfigured(
                f"auxiliary asset checksum mismatch: {dataset_id}/{role}"
            )
        resolved[role] = (candidate, observed_hash)
    return resolved


def _portable_path_hash(path: Path) -> str:
    digest = hashlib.sha256()
    paths = [path] if path.is_file() else sorted(
        item for item in path.rglob("*") if item.is_file()
    )
    for item in paths:
        if path.is_dir():
            digest.update(item.relative_to(path).as_posix().encode("utf-8") + b"\0")
        with item.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    return "sha256:" + digest.hexdigest()

def _load_packaged_or_external_json(
    resource_name: str,
    path: str | Path | None,
    *,
    environment_variable: str,
) -> tuple[dict[str, Any], str]:
    if path is None and os.environ.get(environment_variable):
        path = os.environ[environment_variable]
    if path is None:
        resource = resources.files("pertura_bench").joinpath("cases", resource_name)
        with resources.as_file(resource) as packaged:
            source = Path(packaged)
            return json.loads(source.read_text(encoding="utf-8")), file_sha256(source)
    source = Path(path).expanduser().resolve()
    return json.loads(source.read_text(encoding="utf-8")), file_sha256(source)


def load_design_confirmation_catalog(
    path: str | Path | None = None,
) -> tuple[dict[str, Any], str]:
    payload, digest = _load_packaged_or_external_json(
        _DESIGN_CONFIRMATION_RESOURCE,
        path,
        environment_variable=_DESIGN_CATALOG_ENV,
    )
    if payload.get("schema_version") != "pertura-design-confirmation-catalog-v1":
        raise ValueError("unsupported design confirmation catalog schema")
    datasets = payload.get("datasets")
    if not payload.get("catalog_version") or not isinstance(datasets, dict):
        raise ValueError("design confirmation catalog lacks versioned datasets")
    for dataset_id, dataset in datasets.items():
        if not isinstance(dataset, dict):
            raise ValueError(f"invalid design confirmation dataset: {dataset_id}")
        confirmations = dataset.get("confirmations")
        provenance = dataset.get("provenance")
        if not isinstance(confirmations, dict) or not isinstance(provenance, dict):
            raise ValueError(f"design confirmations/provenance are invalid: {dataset_id}")
        for name in confirmations:
            record = provenance.get(name)
            if not isinstance(record, dict) or not record.get("source") or not record.get("confirmed_by"):
                raise ValueError(
                    f"confirmed design fact lacks source/confirmed_by: {dataset_id}/{name}"
                )
        staged = dataset.get("staged_confirmations") or {}
        staged_provenance = dataset.get("staged_provenance") or {}
        if not isinstance(staged, dict) or not isinstance(staged_provenance, dict):
            raise ValueError(f"staged design confirmations are invalid: {dataset_id}")
        for name, values in staged.items():
            record = staged_provenance.get(name)
            if (
                not isinstance(values, dict)
                or not values
                or not isinstance(record, dict)
                or not record.get("source")
                or not record.get("confirmed_by")
            ):
                raise ValueError(
                    f"staged design confirmation lacks values/provenance: {dataset_id}/{name}"
                )
    return payload, digest


def load_reference_generator_catalog() -> tuple[dict[str, Any], str]:
    resource = resources.files("pertura_bench").joinpath(
        "cases", _REFERENCE_GENERATOR_RESOURCE
    )
    payload = json.loads(resource.read_text(encoding="utf-8"))
    if payload.get("schema_version") != "pertura-reference-generator-catalog-v1":
        raise ValueError("unsupported reference generator catalog schema")
    generators = payload.get("generators")
    if not payload.get("catalog_version") or not isinstance(generators, dict):
        raise ValueError("reference generator catalog lacks versioned generators")
    script_hashes: dict[str, str] = {}
    package_root = resources.files("pertura_bench")
    for generator_id, generator in generators.items():
        if not isinstance(generator, dict):
            raise ValueError(f"invalid reference generator: {generator_id}")
        kind = generator.get("kind")
        if kind == "script":
            script_name = str(generator.get("script") or "")
            script = package_root.joinpath(*Path(script_name).parts)
            script_path = Path(str(script)).resolve()
            if (
                not script_name.startswith("runners/")
                or not script_path.is_file()
                or not generator.get("environment_profile")
                or generator.get("independent_from_capability_runner") is not True
            ):
                raise ValueError(f"invalid script reference generator: {generator_id}")
            script_hashes[generator_id] = file_sha256(script_path)
        elif kind == "curated_external":
            required = generator.get("required_provenance")
            if not isinstance(required, list) or not required:
                raise ValueError(
                    f"curated reference generator lacks provenance: {generator_id}"
                )
        else:
            raise ValueError(f"unknown reference generator kind: {generator_id}")
    digest = canonical_hash(
        {
            "catalog": payload,
            "script_hashes": script_hashes,
        }
    )
    return payload, digest


def load_metric_reference_catalog(
    path: str | Path | None = None,
) -> tuple[dict[str, Any], str]:
    payload, _ = _load_packaged_or_external_json(
        _METRIC_REFERENCE_RESOURCE,
        path,
        environment_variable=_METRIC_CATALOG_ENV,
    )
    generators, generator_digest = load_reference_generator_catalog()
    if payload.get("schema_version") != "pertura-metric-reference-catalog-v1":
        raise ValueError("unsupported metric reference catalog schema")
    datasets = payload.get("datasets")
    if not payload.get("catalog_version") or not isinstance(datasets, dict):
        raise ValueError("metric reference catalog lacks versioned datasets")
    for dataset_id, dataset in datasets.items():
        if not isinstance(dataset, dict) or not isinstance(dataset.get("capabilities"), dict):
            raise ValueError(f"invalid metric reference dataset: {dataset_id}")
        agent_cases = dataset.get("agent_cases") or {}
        if not isinstance(agent_cases, dict):
            raise ValueError(f"invalid agent metric references: {dataset_id}")
        for case_id, entry in agent_cases.items():
            if not isinstance(entry, dict):
                raise ValueError(
                    f"invalid agent metric reference: {dataset_id}/{case_id}"
                )
            _validate_metric_reference_variants(
                entry,
                context=f"{dataset_id}/agent/{case_id}",
                generators=generators,
            )
        for capability_key, entry in dataset["capabilities"].items():
            if not isinstance(entry, dict):
                raise ValueError(
                    f"invalid metric reference capability: {dataset_id}/{capability_key}"
                )
            _validate_metric_reference_variants(
                entry,
                context=f"{dataset_id}/{capability_key}",
                generators=generators,
            )
    digest = canonical_hash(
        {
            "metric_reference_catalog": payload,
            "reference_generator_catalog": generator_digest,
        }
    )
    return payload, digest


def _validate_metric_reference_variants(
    entry: Mapping[str, Any],
    *,
    context: str,
    generators: Mapping[str, Any],
) -> None:
    variants = entry.get("runs")
    if variants is not None:
        if not isinstance(variants, dict) or not variants:
            raise ValueError(f"metric run variants are invalid: {context}")
        for variant_key, variant in variants.items():
            if variant_key not in {
                "frozen_subset:calibration",
                "frozen_subset:evaluation",
                "full_dataset:evaluation",
            } or not isinstance(variant, dict):
                raise ValueError(
                    f"invalid metric run variant: {context}/{variant_key}"
                )
            _validate_metric_reference_entry(
                variant,
                context=f"{context}/{variant_key}",
                generators=generators,
            )
    else:
        _validate_metric_reference_entry(
            entry,
            context=context,
            generators=generators,
        )

def _validate_metric_reference_entry(
    entry: Mapping[str, Any],
    *,
    context: str,
    generators: Mapping[str, Any],
) -> None:
    from pertura_bench.metric_evaluators import validate_artifact_evaluator

    known = generators["generators"]
    scalar_metrics = entry.get("metrics") or ()
    if scalar_metrics:
        generator_id = str(entry.get("reference_generator_id") or "")
        if generator_id not in known:
            raise ValueError(
                f"scalar metric reference generator is missing or unknown: {context}"
            )
        _validate_reference_provenance(
            known[generator_id],
            entry.get("reference_provenance"),
            context=context,
        )
        for metric in scalar_metrics:
            if (
                not isinstance(metric, Mapping)
                or not metric.get("name")
                or "reference" not in metric
            ):
                raise ValueError(f"invalid scalar metric reference: {context}")
    for evaluator in entry.get("evaluators") or ():
        if not isinstance(evaluator, Mapping):
            raise ValueError(f"invalid artifact evaluator: {context}")
        validate_artifact_evaluator(evaluator, context=context)
        generator_id = str(evaluator.get("reference_generator_id") or "")
        if generator_id not in known:
            raise ValueError(
                f"metric reference generator is missing or unknown: {context}"
            )
        _validate_reference_provenance(
            known[generator_id],
            evaluator.get("reference_provenance"),
            context=context,
        )


def _validate_reference_provenance(
    generator: Mapping[str, Any],
    provenance: Any,
    *,
    context: str,
) -> None:
    if generator["kind"] != "curated_external":
        return
    required = set(generator["required_provenance"])
    if not isinstance(provenance, Mapping) or not required.issubset(provenance):
        raise ValueError(
            f"curated metric reference provenance is incomplete: {context}"
        )

def _metric_reference_root(path: str | Path | None) -> Path:
    selected = path or os.environ.get(_METRIC_CATALOG_ENV)
    if selected:
        return Path(selected).expanduser().resolve().parent
    packaged = resources.files("pertura_bench").joinpath(
        "cases", _METRIC_REFERENCE_RESOURCE
    )
    return Path(str(packaged)).resolve().parent


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
    if manifest.file and manifest.conversion:
        source_root = dataset_root / "source"
        source_lock_path = source_root / "artifact.lock.json"
        source_sidecar_path = source_root / "artifact.local.json"
        if not source_lock_path.is_file() or not source_sidecar_path.is_file():
            raise FileNotFoundError(
                f"not_available: source artifact lock chain is missing for {dataset_id}"
            )
        source_lock = BenchmarkArtifactLock.model_validate_json(
            source_lock_path.read_text(encoding="utf-8")
        )
        if source_lock.dataset_id != dataset_id:
            raise ValueError("source artifact lock dataset mismatch")
        if source_lock.source_manifest_hash != manifest.canonical_hash:
            raise ValueError("source artifact lock manifest hash drift")
        source_artifact = _sidecar_artifact(
            source_sidecar_path,
            cache_root,
            expected_lock_id=source_lock.lock_id,
        )
        _validate_locked_file(
            source_artifact,
            source_lock.artifact_sha256,
            expected_size=source_lock.size_bytes,
        )
        if artifact_lock.upstream_lock_hash != source_lock.canonical_hash:
            raise ValueError("converted artifact is not bound to the current source lock")
        hashes.update(
            {
                "source_artifact_lock": source_lock.canonical_hash,
                "source_artifact": source_lock.artifact_sha256,
            }
        )
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
    if subset_lock.schema_version != "pertura-benchmark-subset-lock-v2":
        raise ValueError(
            "formal real benchmark execution requires a subset v2 lock"
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
    _validate_subset_split_disjointness(
        cache_root,
        dataset_id=dataset_id,
        split=split,
        lock_path=subset_lock_path,
        lock=subset_lock,
    )
    hashes.update(
        {
            "subset_lock": subset_lock.canonical_hash,
            "subset": subset_lock.output_sha256,
            "subset_spec": subset_lock.subset_spec_hash,
            "subset_selected_ids": str(subset_lock.selected_ids_sha256),
            "subset_selection_manifest": str(subset_lock.selection_manifest_sha256),
        }
    )
    return subset_path, hashes


def first_existing(candidates: Iterable[Path]) -> Path | None:
    return next((path for path in candidates if path.is_file()), None)


def _selection_ids(
    lock_path: Path,
    lock: BenchmarkSubsetLock,
) -> set[str]:
    manifest = lock_path.parent / "selection.ids.json"
    if (
        not lock.selected_ids_sha256
        or not lock.selection_manifest_sha256
        or not manifest.is_file()
    ):
        raise ValueError("subset lock lacks a bound selection identity manifest")
    if file_sha256(manifest) != lock.selection_manifest_sha256:
        raise ValueError("subset selection identity manifest hash drift")
    values = json.loads(manifest.read_text(encoding="utf-8"))
    if not isinstance(values, list) or not values or any(
        not isinstance(item, str) or not item for item in values
    ):
        raise ValueError("subset selection identity manifest is invalid")
    if len(values) != len(set(values)):
        raise ValueError("subset selection identity manifest contains duplicates")
    if canonical_hash(values) != lock.selected_ids_sha256:
        raise ValueError("subset selected ID digest drift")
    return set(values)


def _validate_subset_split_disjointness(
    cache_root: Path,
    *,
    dataset_id: str,
    split: str,
    lock_path: Path,
    lock: BenchmarkSubsetLock,
) -> None:
    current_ids = _selection_ids(lock_path, lock)
    other_split = "evaluation" if split == "calibration" else "calibration"
    other_lock_path = (
        cache_root
        / "datasets"
        / dataset_id
        / "subset"
        / other_split
        / "subset.lock.json"
    )
    if not other_lock_path.is_file():
        raise FileNotFoundError(
            f"not_available: paired {other_split} subset lock is missing for {dataset_id}"
        )
    other_lock = BenchmarkSubsetLock.model_validate_json(
        other_lock_path.read_text(encoding="utf-8")
    )
    if other_lock.dataset_id != dataset_id:
        raise ValueError("paired subset lock dataset mismatch")
    other_ids = _selection_ids(other_lock_path, other_lock)
    overlap = current_ids & other_ids
    if overlap:
        raise ValueError(
            f"calibration/evaluation subsets overlap by {len(overlap)} cell identities"
        )
    if lock.schema_version.endswith("v2") or other_lock.schema_version.endswith("v2"):
        current_groups = set(lock.selection_summary.get("selected_groups") or ())
        other_groups = set(other_lock.selection_summary.get("selected_groups") or ())
        group_overlap = current_groups & other_groups
        if group_overlap:
            raise ValueError(
                "calibration/evaluation subsets overlap by "
                f"{len(group_overlap)} group identities"
            )
        current_controls = set(
            lock.selection_summary.get("selected_control_units") or ()
        )
        other_controls = set(
            other_lock.selection_summary.get("selected_control_units") or ()
        )
        control_overlap = current_controls & other_controls
        if control_overlap:
            raise ValueError(
                "calibration/evaluation subsets overlap by "
                f"{len(control_overlap)} control unit identities"
            )

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
    design_catalog: Mapping[str, Any],
    metric_catalog: Mapping[str, Any],
    metric_catalog_hash: str,
    parameter_catalog_path: str | Path | None,
    design_confirmations_path: str | Path | None,
    metric_reference_catalog_path: str | Path | None,
    cache_root: Path,
    execution_root: Path,
    resource_evidence_path: str | Path | None,
    enforced_memory_gb: float | None,
    enforced_n_jobs: int | None,
) -> CapabilityBenchmarkVerdict:
    from pertura_runtime.product import PerturaProductRuntime
    from pertura_runtime.project.assets import DataAssetRegistry
    from pertura_runtime.project.models import AssetBinding
    from pertura_runtime.project.workspace import ProjectWorkspace

    registry = CapabilityRegistry.load_default(include_external=False)
    spec = registry.get(case.capability_id, case.capability_version)
    from pertura_bench.resource_evidence import (
        load_resource_evidence,
        validate_resource_request,
    )

    try:
        target_parameters = _real_parameter_mapping(
            parameter_catalog,
            dataset_id=str(case.dataset_id),
            spec=spec,
            case=case,
        )
        requested_memory_gb = float(target_parameters.get("max_memory_gb", 4.0))
        requested_n_jobs = int(target_parameters.get("n_jobs", 1))
        if (
            enforced_memory_gb is None
            or enforced_n_jobs is None
            or float(enforced_memory_gb) != requested_memory_gb
            or int(enforced_n_jobs) != requested_n_jobs
        ):
            raise ValueError(
                "scheduler resource request is absent or disagrees with capability parameters"
            )
        resource_evidence = load_resource_evidence(resource_evidence_path)
        validate_resource_request(
            resource_evidence,
            memory_gb=requested_memory_gb,
            n_jobs=requested_n_jobs,
        )
    except (FileNotFoundError, RealParametersNotConfigured, ValueError) as exc:
        return make_verdict(
            case,
            outcome="failed",
            observed_status="resource_evidence_invalid",
            blockers=(str(exc),),
            reasons=("formal capability execution requires bound scheduler/cgroup evidence",),
            input_hashes=input_hashes,
            runner_hash=runner_hash(spec.executor),
        )
    try:
        checkpoint = _load_checkpoint_binding(
            repo_root,
            parameter_catalog_path=parameter_catalog_path,
            design_confirmations_path=design_confirmations_path,
            metric_reference_catalog_path=metric_reference_catalog_path,
        )
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
    auxiliary_assets = _resolve_catalog_assets(
        parameter_catalog,
        dataset_id=str(case.dataset_id),
        cache_root=cache_root,
        tier=str(case.tier),
        split=split,
        expected_subset_lock_hash=input_hashes.get("subset_lock"),
    )
    input_hashes = dict(input_hashes)
    input_hashes.update(
        {
            f"asset:{role}": item[1]
            for role, item in sorted(auxiliary_assets.items())
        }
    )
    input_hashes.update(
        {
            "checkpoint_git": checkpoint["git_commit"],
            "checkpoint_wheel": checkpoint["wheel_sha256"],
            "checkpoint_plan": checkpoint["server_plan_hash"],
        }
    )

    root = Path(execution_root).resolve()
    project = ProjectWorkspace.initialize(
        root / "project", logical_name=str(case.case_id)
    )
    run = project.create_run(
        logical_name=f"{case.capability_id}:{case.tier}:{split}"
    )
    asset_registry = DataAssetRegistry(
        project_id=project.project.project_id,
        store=project.store,
        object_root=project.objects_dir,
    )
    primary_asset = asset_registry.register(
        artifact, role="primary_dataset", kind="observed"
    )
    project.store.put_asset_binding(
        AssetBinding(
            run_id=run.run_id,
            asset_id=primary_asset.asset_id,
            role=primary_asset.role,
        )
    )
    registered_asset_ids: dict[str, str] = {
        "primary": primary_asset.asset_id,
        "primary_dataset": primary_asset.asset_id,
    }
    for role, (asset_path, expected_hash) in sorted(auxiliary_assets.items()):
        registered = asset_registry.register(
            asset_path,
            role=role,
            kind="external_resource",
        )
        if registered.content_sha256 != expected_hash:
            raise RealParametersNotConfigured(
                f"registered auxiliary asset checksum mismatch: {role}"
            )
        project.store.put_asset_binding(
            AssetBinding(
                run_id=run.run_id,
                asset_id=registered.asset_id,
                role=role,
            )
        )
        registered_asset_ids[role] = registered.asset_id

    workspace = project.run_workspace(run.run_id, input_source=artifact)
    (root / "input_manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "pertura-capability-input-manifest-v1",
                "case_id": case.case_id,
                "dataset_id": case.dataset_id,
                "tier": case.tier,
                "split": split,
                "input_hashes": input_hashes,
                "registered_asset_ids": registered_asset_ids,
                "resource_evidence": resource_evidence,
                "resource_evidence_hash": canonical_hash(resource_evidence),
                "requested_resources": {
                    "max_memory_gb": requested_memory_gb,
                    "n_jobs": requested_n_jobs,
                },
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    started = time.perf_counter()
    peak_before = _peak_rss_mb()
    with temporary_environment("PERTURA_AUTHORITY_ROOT", str(root / "authority")):
            runtime = PerturaProductRuntime(
                workspace,
                project_workspace=project,
                run_id=run.run_id,
            )
            force_loopback_transport(runtime)
            try:
                confirmations = _dataset_confirmations(
                    design_catalog,
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
                    auxiliary_assets=auxiliary_assets,
                    registered_asset_ids=registered_asset_ids,
                    case=case,
                )
                report = runtime.finalize_report(workspace.root.name)
                if report.get("result_count", 0) < len(execution_order):
                    raise RealCapabilityExecutionError(
                        "authority_projection_incomplete",
                        "final report omitted one or more committed DAG results",
                    )
                metric_evaluation = _evaluate_metric_references(
                    result,
                    dataset_id=str(case.dataset_id),
                    capability_id=case.capability_id,
                    capability_version=case.capability_version,
                    catalog=metric_catalog,
                    catalog_hash=metric_catalog_hash,
                    output_root=workspace.root,
                    reference_root=_metric_reference_root(
                        metric_reference_catalog_path
                    ),
                    tier=str(case.tier),
                    split=split,
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
    runtime_seconds = time.perf_counter() - started
    peak_memory_mb = max(peak_before, _peak_rss_mb())
    try:
        validate_resource_request(
            resource_evidence,
            memory_gb=requested_memory_gb,
            n_jobs=requested_n_jobs,
            observed_peak_rss_mb=peak_memory_mb,
        )
        resource_evidence_valid = True
    except ValueError:
        resource_evidence_valid = False
    digest = scientific_result_digest(result)
    status = enum_value(result["status"])
    accepted = status in case.expected_statuses

    required_outputs = set(metric_evaluation["required_outputs"])
    output_hashes = dict(result.get("output_hashes") or {})
    hard_gates = {
        "status_accepted": accepted,
        "result_schema_valid": True,
        "authority_projection_complete": True,
        "required_outputs_present": required_outputs.issubset(output_hashes),
        "resource_evidence_valid": resource_evidence_valid,
    }
    execution_passed = all(hard_gates.values())
    return make_verdict(
        case,
        outcome="passed" if execution_passed else "failed",
        observed_status=status,
        blockers=tuple(result.get("blockers") or ()),
        input_hashes=input_hashes,
        output_hashes=output_hashes,
        runner_hash=runner_hash(spec.executor),
        reasons=() if execution_passed else ("one or more execution hard gates failed",),
        scientific_hash=digest.canonical_hash,
        environment_lock_hash=input_hashes.get("environment_lock"),
        hard_gates=hard_gates,
        scientific_metrics_status=metric_evaluation["status"],
        reference_hashes=metric_evaluation["reference_hashes"],
        continuous_metrics=metric_evaluation["continuous_metrics"],
        metric_bindings=metric_evaluation["metric_bindings"],
        limitations=metric_evaluation["limitations"],
        runtime_seconds=runtime_seconds,
        peak_memory_mb=peak_memory_mb,
    )


def _peak_rss_mb() -> float:
    """Best-effort process peak RSS without introducing a psutil dependency."""

    if os.name != "nt":
        import resource

        value = float(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
        # Linux reports KiB; macOS reports bytes.
        return value / (1024.0 * 1024.0) if value > 1024**3 else value / 1024.0
    try:
        import ctypes
        from ctypes import wintypes

        class ProcessMemoryCounters(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        counters = ProcessMemoryCounters()
        counters.cb = ctypes.sizeof(counters)
        handle = ctypes.windll.kernel32.GetCurrentProcess()
        if ctypes.windll.psapi.GetProcessMemoryInfo(
            handle, ctypes.byref(counters), counters.cb
        ):
            return float(counters.PeakWorkingSetSize) / 1024**2
    except (AttributeError, OSError):
        pass
    return 0.0


def _evaluate_metric_references(
    result: Mapping[str, Any],
    *,
    dataset_id: str,
    capability_id: str,
    capability_version: str,
    catalog: Mapping[str, Any],
    catalog_hash: str,
    output_root: Path | None = None,
    reference_root: Path | None = None,
    tier: str | None = None,
    split: str | None = None,
) -> dict[str, Any]:
    key = f"{capability_id}@{capability_version}"
    entry = _select_metric_entry(
        catalog,
        dataset_id=dataset_id,
        identity=key,
        tier=tier,
        split=split,
        namespace="capabilities",
    )
    return _evaluate_metric_entry(
        result,
        entry=entry,
        context=f"{dataset_id}/{key}/{tier or 'unspecified'}:{split or 'unspecified'}",
        catalog_hash=catalog_hash,
        output_root=output_root,
        reference_root=reference_root,
    )


def evaluate_agent_metric_references(
    result: Mapping[str, Any],
    *,
    dataset_id: str,
    case_id: str,
    catalog: Mapping[str, Any],
    catalog_hash: str,
    output_root: Path | None = None,
    reference_root: Path | None = None,
) -> dict[str, Any]:
    entry = _select_metric_entry(
        catalog,
        dataset_id=dataset_id,
        identity=case_id,
        tier="frozen_subset",
        split="evaluation",
        namespace="agent_cases",
    )
    return _evaluate_metric_entry(
        result,
        entry=entry,
        context=f"{dataset_id}/agent/{case_id}/frozen_subset:evaluation",
        catalog_hash=catalog_hash,
        output_root=output_root,
        reference_root=reference_root,
    )


def _select_metric_entry(
    catalog: Mapping[str, Any],
    *,
    dataset_id: str,
    identity: str,
    tier: str | None,
    split: str | None,
    namespace: str,
) -> dict[str, Any]:
    dataset = dict(catalog.get("datasets", {}).get(dataset_id) or {})
    selected = dict(dataset.get(namespace, {}).get(identity) or {})
    variants = selected.get("runs")
    if isinstance(variants, Mapping):
        return dict(variants.get(f"{tier}:{split}") or {})
    if tier is not None or split is not None:
        return {}
    return selected

def _evaluate_metric_entry(
    result: Mapping[str, Any],
    *,
    entry: Mapping[str, Any],
    context: str,
    catalog_hash: str,
    output_root: Path | None,
    reference_root: Path | None,
) -> dict[str, Any]:
    if not entry:
        return {
            "status": "not_available",
            "required_outputs": (),
            "reference_hashes": {"metric_reference_catalog": catalog_hash},
            "continuous_metrics": {},
            "metric_bindings": (),
            "limitations": (
                f"metric references are not configured for {context}",
            ),
        }
    required_outputs = tuple(
        str(item) for item in entry.get("required_outputs") or ()
    )
    references = tuple(entry.get("metrics") or ())
    evaluators = tuple(entry.get("evaluators") or ())
    reported = tuple(str(item) for item in entry.get("reported_metrics") or ())
    continuous: dict[str, float | int | str | None] = {}
    comparisons: list[bool] = []
    metrics_payload = dict(result.get("metrics") or {})
    for name in reported:
        continuous[name] = _metric_value(metrics_payload, name)
    for raw in references:
        if not isinstance(raw, Mapping):
            raise ValueError(f"invalid metric reference entry for {context}")
        name = str(raw.get("name") or "")
        source = str(raw.get("result_metric") or name)
        if not name or "reference" not in raw:
            raise ValueError(
                f"metric reference lacks name/reference for {context}"
            )
        observed = _metric_value(metrics_payload, source)
        reference = raw.get("reference")
        continuous[name] = observed
        continuous[f"{name}__reference"] = reference
        if observed is None:
            continue
        mode = str(raw.get("comparison") or "absolute_error")
        tolerance = float(raw.get("tolerance", 0.0))
        if mode == "equal":
            passed = observed == reference
            error = 0.0 if passed else 1.0
        else:
            try:
                absolute = abs(float(observed) - float(reference))
                error = (
                    absolute
                    if mode == "absolute_error"
                    else absolute / max(abs(float(reference)), 1e-12)
                )
                passed = error <= tolerance
            except (TypeError, ValueError):
                passed, error = False, float("inf")
        continuous[f"{name}__error"] = error
        # Scalar values emitted by a runner are retained as an index for
        # inspection only.  They never decide scientific correctness; only an
        # evaluator that reloads a hash-bound artifact may do that.
    reference_hashes = {"metric_reference_catalog": catalog_hash}
    for name, digest in dict(entry.get("reference_hashes") or {}).items():
        value = str(digest)
        if not value.startswith("sha256:"):
            raise ValueError(f"reference hash is not canonical: {name}")
        reference_hashes[str(name)] = value
    from pertura_bench.metric_evaluators import evaluate_artifact_metrics

    artifact_evaluation = evaluate_artifact_metrics(
        result,
        evaluators,
        output_root=output_root,
        reference_root=reference_root,
    )
    comparisons.extend(artifact_evaluation["comparisons"])
    continuous.update(artifact_evaluation["continuous_metrics"])
    reference_hashes.update(artifact_evaluation["reference_hashes"])
    required_outputs = tuple(
        sorted(
            set(required_outputs)
            | set(artifact_evaluation["required_outputs"])
        )
    )
    artifact_limitations = tuple(artifact_evaluation["limitations"])
    if evaluators:
        status = "passed" if comparisons and all(comparisons) else "failed"
        limitations: tuple[str, ...] = (
            artifact_limitations
            if status == "passed"
            else artifact_limitations
            + ("one or more frozen scientific reference comparisons failed",)
        )
    elif references or reported:
        status = "reported_only"
        limitations = (
            "runner-emitted scalar metrics are reported for indexing only; "
            "an artifact evaluator is required for scientific validation",
        )
    else:
        status = "not_available"
        limitations = ("metric reference entry contains no metrics",)
    return {
        "status": status,
        "required_outputs": required_outputs,
        "reference_hashes": reference_hashes,
        "continuous_metrics": continuous,
        "metric_bindings": artifact_evaluation["metric_bindings"],
        "limitations": limitations,
    }

def _metric_value(payload: Mapping[str, Any], dotted: str) -> Any:
    value: Any = payload
    for part in dotted.split("."):
        if not isinstance(value, Mapping) or part not in value:
            return None
        value = value[part]
    return value if isinstance(value, (str, int, float)) or value is None else str(value)

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
    auxiliary_assets: Mapping[str, tuple[Path, str]] | None = None,
    registered_asset_ids: Mapping[str, str] | None = None,
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
            auxiliary_assets=auxiliary_assets or {},
            registered_asset_ids=registered_asset_ids or {},
        )
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
        elif spec.kind == "virtual":
            compact = runtime.evaluate_virtual_model(
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
    tier = str(case.tier) if case is not None else "frozen_subset"
    split = str((case.parameters.get("split") if case is not None else None) or "evaluation")
    dataset = select_real_parameter_run(
        catalog, dataset_id=dataset_id, tier=tier, split=split
    )
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
    auxiliary_assets: Mapping[str, tuple[Path, str]],
    registered_asset_ids: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    return {
        key: _materialize_parameter_value(
            item,
            artifact=artifact,
            workspace_root=workspace_root,
            committed_by_capability=committed_by_capability,
            auxiliary_assets=auxiliary_assets,
            registered_asset_ids=registered_asset_ids or {},
        )
        for key, item in value.items()
    }


def _materialize_parameter_value(
    value: Any,
    *,
    artifact: Path,
    workspace_root: Path,
    committed_by_capability: Mapping[str, Mapping[str, Any]],
    auxiliary_assets: Mapping[str, tuple[Path, str]],
    registered_asset_ids: Mapping[str, str],
) -> Any:
    if isinstance(value, Mapping):
        if set(value) == {"artifact_ref"}:
            if value["artifact_ref"] != "primary":
                raise RealParametersNotConfigured(
                    f"unsupported benchmark artifact_ref: {value['artifact_ref']}"
                )
            return registered_asset_ids.get("primary", str(artifact))
        if set(value) == {"asset_ref"}:
            role = str(value["asset_ref"] or "")
            selected = auxiliary_assets.get(role)
            if selected is None:
                raise RealParametersNotConfigured(
                    f"hash-bound auxiliary asset is not configured: {role or 'unknown'}"
                )
            return registered_asset_ids.get(role, str(selected[0]))
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
            return (
                str(matches[0].relative_to(workspace_root.resolve()))
                if registered_asset_ids
                else str(matches[0])
            )
        return {
            str(key): _materialize_parameter_value(
                item,
                artifact=artifact,
                workspace_root=workspace_root,
                committed_by_capability=committed_by_capability,
                auxiliary_assets=auxiliary_assets,
                registered_asset_ids=registered_asset_ids,
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
                auxiliary_assets=auxiliary_assets,
                registered_asset_ids=registered_asset_ids,
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
    del case
    dataset = dict(catalog.get("datasets", {}).get(dataset_id) or {})
    return dict(dataset.get("confirmations") or {})


def _load_checkpoint_binding(
    repo_root: Path,
    *,
    parameter_catalog_path: str | Path | None = None,
    design_confirmations_path: str | Path | None = None,
    metric_reference_catalog_path: str | Path | None = None,
) -> dict[str, str]:
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

    # A full bound plan validates its own immutable job graph and every bound
    # catalog identity. The legacy binding-only form is retained for older
    # checkpoint fixtures.
    if "jobs" in payload and "artifacts" in payload:
        from pertura_bench.capability_models import ServerBenchmarkPlan
        from pertura_bench.server_plan import assert_server_plan_executable

        plan = ServerBenchmarkPlan.model_validate(payload)
        assert_server_plan_executable(plan)
        validated = {
            str(key): str(value)
            for key, value in plan.checkpoint_binding.items()
        }
        _, parameter_hash = load_real_parameter_catalog(parameter_catalog_path)
        _, design_hash = load_design_confirmation_catalog(
            design_confirmations_path
        )
        _, metric_hash = load_metric_reference_catalog(
            metric_reference_catalog_path
        )
        current_catalogs = {
            "parameter_catalog_hash": parameter_hash,
            "design_confirmation_catalog_hash": design_hash,
            "metric_reference_catalog_hash": metric_hash,
        }
        for field, observed in current_catalogs.items():
            if validated.get(field) != observed:
                raise ValueError(f"checkpoint catalog drift: {field}")
    else:
        # Local imports avoid a module cycle while capability_bench imports this file.
        from pertura_bench.capability_bench import benchmark_specs
        from pertura_bench.server_plan import (
            build_server_plan,
            validate_checkpoint_binding,
        )

        template = build_server_plan(
            benchmark_specs(),
            repo_root,
            parameter_catalog_path=parameter_catalog_path,
            design_confirmations_path=design_confirmations_path,
            metric_reference_catalog_path=metric_reference_catalog_path,
        )
        validated = validate_checkpoint_binding(template, binding)
    import subprocess

    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_root,
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0 or completed.stdout.strip().lower() != validated["git_commit"]:
        raise ValueError("checkpoint checkout commit does not match the bound plan")
    wheel_value = os.environ.get(_WHEEL_ENV)
    if not wheel_value:
        raise FileNotFoundError(
            f"{_WHEEL_ENV} is not set to the loaded benchmark wheel"
        )
    wheel = Path(wheel_value).expanduser().resolve()
    if not wheel.is_file() or file_sha256(wheel) != validated["wheel_sha256"]:
        raise ValueError("checkpoint wheel identity does not match the bound plan")
    return validated


def _real_identity_hashes(
    repo_root: Path,
    *,
    case: CapabilityBenchmarkCase,
    spec: Any,
    dataset_id: str,
    split: str,
    tier: BenchmarkTier,
    parameter_catalog_hash: str,
    design_catalog_hash: str,
    metric_catalog_hash: str,
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
        "design_confirmation_catalog": design_catalog_hash,
        "metric_reference_catalog": metric_catalog_hash,
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
