from __future__ import annotations

import json
import re
from importlib import resources
from pathlib import Path
from typing import Any

from pertura_bench.capability_models import (
    BenchmarkTier,
    CapabilityBenchmarkCase,
    CapabilityBenchmarkMatrix,
    CapabilityBenchmarkSpec,
    CapabilityBenchmarkVerdict,
    CapabilityCoverageEntry,
    ServerBenchmarkPlan,
)
from pertura_bench.real_execution import (
    _load_checkpoint_binding,
    _real_case,
    _real_identity_hashes,
    first_existing,
    load_design_confirmation_catalog,
    load_metric_reference_catalog,
    load_real_parameter_catalog,
    resolve_real_artifact_chain,
    run_real_tier,
)
from pertura_bench.server_plan import build_server_plan
from pertura_bench.synthetic_execution import (
    benchmark_input_hashes,
    run_synthetic_case,
    runner_hash,
    scientific_result_digest,
)
from pertura_core.hashing import canonical_hash
from pertura_workflow.capabilities import CapabilityRegistry
from pertura_workflow.capabilities.executors import has_executor, has_validator
from pertura_workflow.environment import doctor_environment


_SCENARIOS = (
    "happy",
    "caution_or_unresolved",
    "blocked",
    "planted_failure",
    "determinism",
    "stale_propagation",
)


def _catalog_payload() -> dict[str, Any]:
    resource = resources.files("pertura_bench").joinpath(
        "cases", "capability_cases.v1.json"
    )
    return json.loads(resource.read_text(encoding="utf-8"))


_CATALOG = _catalog_payload()
CANDIDATE_CAPABILITIES: tuple[str, ...] = tuple(
    item["capability_id"] for item in _CATALOG["capabilities"]
)


def _entry_for(capability_id: str) -> dict[str, Any]:
    for item in _CATALOG["capabilities"]:
        if item["capability_id"] == capability_id:
            return dict(item)
    raise ValueError(f"unknown benchmark capability: {capability_id}")


_EXPLICIT_CASE_FIELDS = frozenset(
    {
        "scenario",
        "fixture_id",
        "fixture_version",
        "execution_mode",
        "seed",
        "parameters",
        "expected_statuses",
        "expected_blocker_contains",
        "required_outputs",
        "metrics",
    }
)
_EARLY_BLOCK_STATUSES = frozenset({"blocked", "exception_blocked"})


def _explicit_case(entry: dict[str, Any], spec: Any, raw: Any) -> CapabilityBenchmarkCase:
    if not isinstance(raw, dict):
        raise ValueError(f"benchmark case for {spec.capability_id} must be an object")
    missing = sorted(_EXPLICIT_CASE_FIELDS - set(raw))
    if missing:
        raise ValueError(
            f"benchmark case for {spec.capability_id} is missing explicit fields: "
            + ", ".join(missing)
        )
    statuses = tuple(str(item) for item in raw["expected_statuses"])
    if len(statuses) != 1:
        raise ValueError(
            f"benchmark case {spec.capability_id}/{raw.get('scenario')} must name "
            "one exact expected status"
        )
    scenario = str(raw["scenario"])
    blocker_tokens = tuple(str(item) for item in raw["expected_blocker_contains"])
    if scenario in {"blocked", "planted_failure"} and not blocker_tokens:
        raise ValueError(
            f"benchmark case {spec.capability_id}/{scenario} must assert a planted blocker token"
        )
    if scenario not in {"blocked", "planted_failure"} and blocker_tokens:
        raise ValueError(
            f"non-blocking benchmark case {spec.capability_id}/{scenario} cannot expect blockers"
        )
    metrics = tuple(raw["metrics"])
    if not metrics:
        raise ValueError(
            f"benchmark case {spec.capability_id}/{scenario} must assert a metric"
        )
    if len({str(item.get("name")) for item in metrics}) != len(metrics):
        raise ValueError(
            f"benchmark case {spec.capability_id}/{scenario} repeats a metric name"
        )
    expected_mode = (
        "stale_audit" if scenario == "stale_propagation" else entry["execution_mode"]
    )
    if raw["execution_mode"] != expected_mode:
        raise ValueError(
            f"benchmark case {spec.capability_id}/{scenario} has execution mode "
            f"{raw['execution_mode']!r}; expected {expected_mode!r}"
        )
    return CapabilityBenchmarkCase.model_validate(
        {
            "capability_id": spec.capability_id,
            "capability_version": spec.version,
            "tier": "synthetic_ci",
            **raw,
            "environment_profile": entry.get("environment_profile"),
            "environment_required": bool(entry.get("environment_required")),
            "max_memory_gb": 4,
            "timeout_seconds": min(spec.timeout_seconds, 900),
        }
    )


def benchmark_specs() -> tuple[CapabilityBenchmarkSpec, ...]:
    registry = CapabilityRegistry.load_default(include_external=False)
    specs: list[CapabilityBenchmarkSpec] = []
    for entry in _CATALOG["capabilities"]:
        spec = registry.get(entry["capability_id"], _CATALOG["capability_version"])
        cases = [
            _explicit_case(entry, spec, raw)
            for raw in entry.get("cases") or ()
        ]
        specs.append(
            CapabilityBenchmarkSpec(
                catalog_version=_CATALOG["catalog_version"],
                capability_id=spec.capability_id,
                capability_version=spec.version,
                cases=tuple(cases),
                required_real_datasets=tuple(entry["required_real_datasets"]),
            )
        )
    return tuple(specs)


def validate_cases() -> dict[str, Any]:
    registry = CapabilityRegistry.load_default(include_external=False)
    problems: list[str] = []
    seen: set[str] = set()
    if tuple(_CATALOG.get("scenarios") or ()) != _SCENARIOS:
        problems.append("case catalog scenario order or membership drifted")
    for bench_spec in benchmark_specs():
        if bench_spec.capability_id in seen:
            problems.append(f"duplicate benchmark spec: {bench_spec.capability_id}")
        seen.add(bench_spec.capability_id)
        try:
            capability = registry.get(
                bench_spec.capability_id, bench_spec.capability_version
            )
        except ValueError as exc:
            problems.append(str(exc))
            continue
        if capability.trust_level.value != "exploratory":
            problems.append(f"candidate is not exploratory: {bench_spec.capability_id}")
        if capability.claim_permissions:
            problems.append(
                f"candidate carries claim permissions: {bench_spec.capability_id}"
            )
        if not has_executor(capability.executor):
            problems.append(
                f"candidate executor is missing: {bench_spec.capability_id}"
            )
        if not has_validator(capability.validator):
            problems.append(
                f"candidate validator is missing: {bench_spec.capability_id}"
            )
        if len(bench_spec.cases) != 6:
            problems.append(
                f"candidate does not have six local cases: {bench_spec.capability_id}"
            )
    if set(seen) != set(CANDIDATE_CAPABILITIES):
        problems.append("case catalog and exported candidate list disagree")
    return {
        "schema_version": "pertura-capability-case-validation-v2",
        "ok": not problems,
        "catalog_version": _CATALOG["catalog_version"],
        "catalog_hash": canonical_hash(_CATALOG),
        "candidate_count": len(seen),
        "case_count": sum(len(item.cases) for item in benchmark_specs()),
        "problems": problems,
    }



def run_protocol_cases(
    capability_id: str,
    *,
    tier: BenchmarkTier = "synthetic_ci",
    repo_root: str | Path | None = None,
    dataset_id: str | None = None,
    split: str | None = None,
    cache: str | Path | None = None,
    output: str | Path | None = None,
    parameter_catalog_path: str | Path | None = None,
    design_confirmations_path: str | Path | None = None,
    metric_reference_catalog_path: str | Path | None = None,
) -> list[dict[str, Any]]:
    matching = [item for item in benchmark_specs() if item.capability_id == capability_id]
    if not matching:
        raise ValueError(f"unknown benchmark capability: {capability_id}")
    spec = matching[0]
    if tier in {"frozen_subset", "full_dataset"}:
        verdicts = run_real_tier(
            spec,
            tier=tier,
            repo_root=repo_root,
            dataset_id=dataset_id,
            split=split,
            cache=cache,
            output=output,
            parameter_catalog_path=parameter_catalog_path,
            design_confirmations_path=design_confirmations_path,
            metric_reference_catalog_path=metric_reference_catalog_path,
        )
    elif tier in {"unit", "synthetic_ci"}:
        verdicts = [
            run_synthetic_case(case, catalog=_CATALOG)
            for case in spec.cases
        ]
    else:
        raise ValueError(f"unknown benchmark tier: {tier}")
    return [item.model_dump(mode="json") for item in verdicts]


_SHA256_VALUE = re.compile(r"^sha256:[0-9a-f]{64}$")


def _synthetic_verdict_current(
    verdict: CapabilityBenchmarkVerdict | None,
    case: CapabilityBenchmarkCase,
    capability: Any,
) -> bool:
    if verdict is None or verdict.outcome != "passed":
        return False
    if (
        verdict.case_hash != case.canonical_hash
        or verdict.capability_id != case.capability_id
        or verdict.capability_version != case.capability_version
        or verdict.tier != case.tier
        or verdict.execution_mode != case.execution_mode
        or verdict.observed_status not in case.expected_statuses
    ):
        return False
    expected_bindings = benchmark_input_hashes(case, capability, _CATALOG)
    if verdict.input_hashes != expected_bindings:
        return False
    if verdict.runner_hash != expected_bindings["product_spine"]:
        return False
    if not verdict.scientific_result_hash or not _SHA256_VALUE.fullmatch(
        verdict.scientific_result_hash
    ):
        return False
    if any(not _SHA256_VALUE.fullmatch(value) for value in verdict.output_hashes.values()):
        return False
    output_names = {Path(str(name)).name for name in verdict.output_hashes}
    if not set(case.required_outputs).issubset(output_names):
        return False
    if any(
        not any(token in blocker for blocker in verdict.observed_blockers)
        for token in case.expected_blocker_contains
    ):
        return False
    if len(verdict.metrics) != len(case.metrics):
        return False
    observed_metrics = {item.name: item for item in verdict.metrics}
    for expected in case.metrics:
        observed = observed_metrics.get(expected.name)
        if (
            observed is None
            or observed.operator != expected.operator
            or observed.threshold != expected.threshold
            or observed.observed is None
            or observed.passed is not True
        ):
            return False
    return True

def coverage_matrix(
    repo_root: str | Path | None = None,
) -> CapabilityBenchmarkMatrix:
    root = _default_repo_root() if repo_root is None else Path(repo_root).resolve()
    registry = CapabilityRegistry.load_default(include_external=False)
    persisted = _load_synthetic_verdicts(root)
    entries: list[CapabilityCoverageEntry] = []
    for bench_spec in benchmark_specs():
        blockers: list[str] = []
        try:
            capability = registry.get(
                bench_spec.capability_id, bench_spec.capability_version
            )
            code_ready = (
                capability.implemented
                and capability.trust_level.value == "exploratory"
                and not capability.claim_permissions
                and has_executor(capability.executor)
                and has_validator(capability.validator)
            )
        except ValueError:
            capability = None
            code_ready = False
        if not code_ready:
            blockers.append("candidate implementation or protocol is incomplete")
        current = [
            verdict
            for case in bench_spec.cases
            if (
                (verdict := persisted.get(case.case_id)) is not None
                and capability is not None
                and _synthetic_verdict_current(verdict, case, capability)
            )
        ]
        local_fixture_ready = len(current) == len(bench_spec.cases)
        if not local_fixture_ready:
            blockers.append(
                f"current synthetic verdicts: {len(current)}/{len(bench_spec.cases)}"
            )
        entry = _entry_for(bench_spec.capability_id)
        environment_profile = entry.get("environment_profile")
        environment_ready = None
        if environment_profile:
            try:
                environment_ready = bool(
                    doctor_environment(environment_profile).get("ok")
                )
            except (KeyError, ValueError):
                environment_ready = False
            if not environment_ready:
                blockers.append(
                    f"optional environment is unavailable: {environment_profile}"
                )
        real_ready = _real_verdict_current(root, bench_spec)
        if not real_ready:
            blockers.append("real-data benchmark has not been executed")
        entries.append(
            CapabilityCoverageEntry(
                capability_id=bench_spec.capability_id,
                capability_version=bench_spec.capability_version,
                code_ready=code_ready,
                local_fixture_ready=local_fixture_ready,
                environment_ready=environment_ready,
                real_benchmark_ready=real_ready,
                synthetic_case_ids=tuple(case.case_id for case in bench_spec.cases),
                current_verdict_ids=tuple(item.verdict_id for item in current),
                required_real_datasets=bench_spec.required_real_datasets,
                blockers=tuple(blockers),
            )
        )
    known_environment = [
        item.environment_ready
        for item in entries
        if item.environment_ready is not None
    ]
    return CapabilityBenchmarkMatrix(
        entries=tuple(entries),
        code_ready=all(item.code_ready for item in entries),
        local_fixture_ready=all(item.local_fixture_ready for item in entries),
        optional_environment_ready=(
            all(known_environment) if known_environment else None
        ),
        real_benchmark_ready=all(item.real_benchmark_ready for item in entries),
        release_ready=False,
    )


def _default_repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _load_synthetic_verdicts(
    root: Path,
) -> dict[str, CapabilityBenchmarkVerdict]:
    candidates = (
        root / "src" / "pertura_bench" / "cases" / "synthetic_verdicts.v1.json",
        root / "benchmarks" / "verdicts" / "synthetic_ci.v1.json",
    )
    path = first_existing(candidates)
    if path is None:
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("catalog_hash") != canonical_hash(_CATALOG):
            return {}
        verdicts = [
            CapabilityBenchmarkVerdict.model_validate(item)
            for item in payload.get("verdicts") or ()
        ]
    except (ValueError, OSError, json.JSONDecodeError):
        return {}
    return {item.case_id: item for item in verdicts}


def _frozen_real_lock_bindings(
    root: Path,
    dataset_id: str,
    tier: BenchmarkTier,
    split: str,
) -> dict[str, str] | None:
    from pertura_bench.models import BenchmarkArtifactLock, BenchmarkSubsetLock
    from pertura_bench.operations import source_manifests

    artifact = None
    subset = None
    for path in sorted((root / "benchmarks" / "locks").rglob("*.json")):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        schema = str(raw.get("schema_version") or "")
        try:
            if schema == "pertura-benchmark-artifact-lock-v1":
                candidate = BenchmarkArtifactLock.model_validate(raw)
                if candidate.dataset_id == dataset_id:
                    if artifact is not None:
                        return None
                    artifact = candidate
            elif schema == "pertura-benchmark-subset-lock-v1":
                candidate = BenchmarkSubsetLock.model_validate(raw)
                declared_split = str(raw.get("split") or "")
                if (
                    candidate.dataset_id == dataset_id
                    and (declared_split == split or split in path.stem)
                ):
                    if subset is not None:
                        return None
                    subset = candidate
        except ValueError:
            return None
    if artifact is None:
        return None
    manifests = source_manifests(root)
    if dataset_id not in manifests:
        return None
    bindings = {
        "source_manifest": manifests[dataset_id][1].canonical_hash,
        "artifact_lock": artifact.canonical_hash,
        "artifact": artifact.artifact_sha256,
    }
    if tier == "frozen_subset":
        if (
            subset is None
            or subset.source_lock_hash != artifact.canonical_hash
            or not subset.selected_ids_sha256
            or not subset.selection_manifest_sha256
        ):
            return None
        bindings.update(
            {
                "subset_lock": subset.canonical_hash,
                "subset": subset.output_sha256,
                "subset_spec": subset.subset_spec_hash,
                "subset_selected_ids": subset.selected_ids_sha256,
                "subset_selection_manifest": subset.selection_manifest_sha256,
            }
        )
    return bindings


def _real_verdict_current(root: Path, spec: CapabilityBenchmarkSpec) -> bool:
    return _real_verdict_state(root, spec, require_validation=True)


def _real_verdict_complete(root: Path, spec: CapabilityBenchmarkSpec) -> bool:
    return _real_verdict_state(root, spec, require_validation=False)


def _real_verdict_state(
    root: Path,
    spec: CapabilityBenchmarkSpec,
    *,
    require_validation: bool,
) -> bool:
    from pertura_bench.real_run_policy import real_runs_for_spec

    directory = root / "benchmarks" / "verdicts" / "real"
    registry = CapabilityRegistry.load_default(include_external=False)
    capability = registry.get(spec.capability_id, spec.capability_version)
    parameter_catalog, parameter_catalog_hash = load_real_parameter_catalog()
    _, design_catalog_hash = load_design_confirmation_catalog()
    _, metric_catalog_hash = load_metric_reference_catalog()
    try:
        checkpoint = _load_checkpoint_binding(root)
    except (FileNotFoundError, ValueError, OSError):
        return False
    runs = real_runs_for_spec(spec)
    if not runs:
        return True
    for run in runs:
        dataset_id = run["dataset_id"]
        tier = run["tier"]
        split = run["split"]
        path = directory / (
            f"{dataset_id}__{spec.capability_id}__{tier}__{split}.json"
        )
        if not path.is_file():
            return False
        try:
            verdict = CapabilityBenchmarkVerdict.model_validate_json(
                path.read_text(encoding="utf-8")
            )
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
            expected = _real_identity_hashes(
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
        except (ValueError, OSError, json.JSONDecodeError):
            return False
        lock_bindings = _frozen_real_lock_bindings(
            root, dataset_id, tier, split
        )
        if lock_bindings is None:
            return False
        expected.update(lock_bindings)
        expected.update(
            {
                "checkpoint_git": checkpoint["git_commit"],
                "checkpoint_wheel": checkpoint["wheel_sha256"],
                "checkpoint_plan": checkpoint["server_plan_hash"],
            }
        )
        dataset_mapping = dict(
            parameter_catalog.get("datasets", {}).get(dataset_id) or {}
        )
        expected.update(
            {
                f"asset:{item['role']}": str(item["content_sha256"])
                for item in dataset_mapping.get("agent_assets") or ()
            }
        )
        identity_current = (
            verdict.case_hash == case.canonical_hash
            and verdict.capability_id == spec.capability_id
            and verdict.capability_version == spec.capability_version
            and verdict.tier == tier
            and verdict.execution_mode == "product_path"
            and verdict.runner_hash == runner_hash(capability.executor)
            and verdict.input_hashes == expected
            and verdict.environment_lock_hash == expected.get("environment_lock")
        )
        if not identity_current:
            return False
        if not require_validation:
            if verdict.outcome in {"not_available", "not_configured", "not_run_environment_missing"}:
                return False
            if verdict.outcome == "passed" and verdict.scientific_metrics_status == "not_available":
                return False
            continue
        if (
            verdict.outcome != "passed"
            or not verdict.scientific_result_hash
            or not _SHA256_VALUE.fullmatch(verdict.scientific_result_hash)
            or not verdict.hard_gates
            or not all(verdict.hard_gates.values())
            or verdict.scientific_metrics_status != "passed"
            or not verdict.reference_hashes
            or not verdict.continuous_metrics
        ):
            return False
    return True

def _write_text_lf(path: Path, text: str) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def write_synthetic_verdicts(
    output: str | Path | None = None,
) -> dict[str, Any]:
    if output is not None and Path(output).exists() and Path(output).is_dir():
        raise ValueError("synthetic verdict output must be a file path, not a directory")
    verdicts = []
    for capability_id in CANDIDATE_CAPABILITIES:
        verdicts.extend(
            CapabilityBenchmarkVerdict.model_validate(item)
            for item in run_protocol_cases(capability_id, tier="synthetic_ci")
        )
    destination = (
        Path(output).resolve()
        if output
        else _default_repo_root()
        / "src"
        / "pertura_bench"
        / "cases"
        / "synthetic_verdicts.v1.json"
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": "pertura-synthetic-verdict-set-v1",
        "catalog_hash": canonical_hash(_CATALOG),
        "case_count": len(verdicts),
        "passed_count": sum(item.outcome == "passed" for item in verdicts),
        "verdicts": [item.model_dump(mode="json") for item in verdicts],
    }
    _write_text_lf(destination, json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return {
        "path": str(destination),
        "case_count": len(verdicts),
        "passed_count": payload["passed_count"],
        "ready": payload["passed_count"] == len(verdicts),
    }



def server_benchmark_plan(
    repo_root: str | Path | None = None,
    *,
    parameter_catalog_path: str | Path | None = None,
    design_confirmations_path: str | Path | None = None,
    metric_reference_catalog_path: str | Path | None = None,
) -> ServerBenchmarkPlan:
    return build_server_plan(
        benchmark_specs(),
        repo_root,
        parameter_catalog_path=parameter_catalog_path,
        design_confirmations_path=design_confirmations_path,
        metric_reference_catalog_path=metric_reference_catalog_path,
    )


def write_server_plan(
    path: str | Path,
    *,
    repo_root: str | Path | None = None,
    parameter_catalog_path: str | Path | None = None,
    design_confirmations_path: str | Path | None = None,
    metric_reference_catalog_path: str | Path | None = None,
) -> dict[str, Any]:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    plan = server_benchmark_plan(
        repo_root,
        parameter_catalog_path=parameter_catalog_path,
        design_confirmations_path=design_confirmations_path,
        metric_reference_catalog_path=metric_reference_catalog_path,
    )
    _write_text_lf(
        destination,
        json.dumps(plan.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
    )
    return {
        "plan_id": plan.plan_id,
        "plan_hash": plan.canonical_hash,
        "path": str(destination),
        "job_count": len(plan.jobs),
        "artifact_count": len(plan.artifacts),
    }
