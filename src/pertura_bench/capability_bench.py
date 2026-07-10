from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pertura_bench.capability_models import (
    CapabilityBenchmarkCase,
    CapabilityBenchmarkMatrix,
    CapabilityBenchmarkSpec,
    CapabilityBenchmarkVerdict,
    CapabilityCoverageEntry,
    ServerBenchmarkPlan,
)
from pertura_workflow.capabilities import CapabilityRegistry
from pertura_workflow.capabilities.executors import has_executor, has_validator


CANDIDATE_CAPABILITIES: tuple[str, ...] = (
    "intake.materialize.v1",
    "diagnostic.dataset_integrity.v1",
    "diagnostic.design_balance.v1",
    "guide.integrity.v1",
    "guide.assignment.nb_mixture.v1",
    "guide.ambient.v1",
    "screen.moi_doublet.v1",
    "screen.retained_cells.v1",
    "state.reference.fit.v1",
    "state.reference.map_knn.v1",
    "state.annotation_candidates.v1",
    "module.learn.control_nmf.v1",
    "target.responder.mixscape.v1",
    "target.guide_efficacy.v1",
    "target.reliability.aggregate.v1",
    "association.sceptre.v1",
    "composition.propeller.v1",
    "effect.guide_target_sensitivity.v1",
    "effect.module_global.v1",
    "calibration.method_null.v1",
)

_REAL_DATASETS: dict[str, tuple[str, ...]] = {
    "intake.materialize.v1": ("replogle_k562_essential_2022",),
    "diagnostic.dataset_integrity.v1": ("replogle_k562_essential_2022",),
    "diagnostic.design_balance.v1": ("kang18_8vs8_pbmc",),
    "guide.integrity.v1": ("replogle_k562_essential_2022",),
    "guide.assignment.nb_mixture.v1": ("replogle_k562_essential_2022",),
    "guide.ambient.v1": ("replogle_k562_essential_2022",),
    "screen.moi_doublet.v1": ("replogle_k562_essential_2022",),
    "screen.retained_cells.v1": ("replogle_k562_essential_2022",),
    "state.reference.fit.v1": ("papalexi_thp1_eccite",),
    "state.reference.map_knn.v1": ("papalexi_thp1_eccite",),
    "state.annotation_candidates.v1": ("papalexi_thp1_eccite",),
    "module.learn.control_nmf.v1": ("papalexi_thp1_eccite",),
    "target.responder.mixscape.v1": ("papalexi_thp1_eccite",),
    "target.guide_efficacy.v1": (
        "replogle_k562_essential_2022",
        "norman_k562_crispra_2019",
    ),
    "target.reliability.aggregate.v1": (
        "replogle_k562_essential_2022",
        "norman_k562_crispra_2019",
    ),
    "association.sceptre.v1": ("norman_k562_crispra_2019",),
    "composition.propeller.v1": ("kang18_8vs8_pbmc",),
    "effect.guide_target_sensitivity.v1": (
        "replogle_k562_essential_2022",
        "norman_k562_crispra_2019",
    ),
    "effect.module_global.v1": ("replogle_k562_essential_2022",),
    "calibration.method_null.v1": (
        "replogle_k562_essential_2022",
        "norman_k562_crispra_2019",
        "kang18_8vs8_pbmc",
    ),
}

_SCENARIOS = (
    ("happy_path", "completed"),
    ("caution_or_unresolved", "caution"),
    ("blocked_design", "blocked"),
    ("planted_failure", "failed"),
    ("deterministic_rerun", "completed"),
    ("stale_upstream", "blocked"),
)


def benchmark_specs() -> tuple[CapabilityBenchmarkSpec, ...]:
    specs: list[CapabilityBenchmarkSpec] = []
    for capability_id in CANDIDATE_CAPABILITIES:
        cases = tuple(
            CapabilityBenchmarkCase(
                capability_id=capability_id,
                capability_version="0.1.0",
                tier="synthetic_ci",
                scenario=scenario,
                fixture_id=f"synthetic/{capability_id}/{scenario}",
                expected_status=status,
                max_memory_gb=4.0,
                timeout_seconds=900,
            )
            for scenario, status in _SCENARIOS
        )
        specs.append(
            CapabilityBenchmarkSpec(
                capability_id=capability_id,
                capability_version="0.1.0",
                cases=cases,
                required_real_datasets=_REAL_DATASETS[capability_id],
            )
        )
    return tuple(specs)


def validate_cases() -> dict[str, Any]:
    registry = CapabilityRegistry.load_default(include_external=False)
    problems: list[str] = []
    seen: set[str] = set()
    for spec in benchmark_specs():
        if spec.capability_id in seen:
            problems.append(f"duplicate benchmark spec: {spec.capability_id}")
        seen.add(spec.capability_id)
        try:
            capability = registry.get(spec.capability_id, spec.capability_version)
        except ValueError as exc:
            problems.append(str(exc))
            continue
        if capability.trust_level.value != "exploratory":
            problems.append(f"candidate is not exploratory: {spec.capability_id}")
        if capability.claim_permissions:
            problems.append(f"candidate carries claim permissions: {spec.capability_id}")
        if not has_executor(capability.executor):
            problems.append(f"candidate executor is missing: {spec.capability_id}")
        if not has_validator(capability.validator):
            problems.append(f"candidate validator is missing: {spec.capability_id}")
        if len(spec.cases) != len(_SCENARIOS):
            problems.append(f"candidate does not have six local cases: {spec.capability_id}")
    return {
        "schema_version": "pertura-capability-case-validation-v1",
        "ok": not problems,
        "candidate_count": len(seen),
        "problems": problems,
    }


def run_protocol_cases(capability_id: str, *, tier: str = "synthetic_ci") -> list[dict[str, Any]]:
    matching = [item for item in benchmark_specs() if item.capability_id == capability_id]
    if not matching:
        raise ValueError(f"unknown benchmark capability: {capability_id}")
    spec = matching[0]
    registry = CapabilityRegistry.load_default(include_external=False)
    capability = registry.get(capability_id, spec.capability_version)
    verdicts: list[CapabilityBenchmarkVerdict] = []
    for case in spec.cases:
        if tier in {"frozen_subset", "full_dataset"}:
            outcome = "not_available"
            reasons = ("real benchmark artifact lock is not available on this machine",)
        elif tier not in {"unit", "synthetic_ci"}:
            raise ValueError(f"unknown benchmark tier: {tier}")
        else:
            ok = (
                capability.trust_level.value == "exploratory"
                and not capability.claim_permissions
                and capability.implemented
                and has_executor(capability.executor)
                and has_validator(capability.validator)
            )
            outcome = "passed" if ok else "failed"
            reasons = () if ok else ("candidate protocol validation failed",)
        verdicts.append(
            CapabilityBenchmarkVerdict(
                case_id=case.case_id,
                capability_id=capability_id,
                capability_version=spec.capability_version,
                tier=tier,
                outcome=outcome,
                observed_status="protocol_validated" if outcome == "passed" else None,
                input_hashes={"case": case.canonical_hash},
                reasons=reasons,
            )
        )
    return [item.model_dump(mode="json") for item in verdicts]


def coverage_matrix() -> CapabilityBenchmarkMatrix:
    registry = CapabilityRegistry.load_default(include_external=False)
    entries: list[CapabilityCoverageEntry] = []
    for bench_spec in benchmark_specs():
        blockers: list[str] = []
        try:
            capability = registry.get(bench_spec.capability_id, bench_spec.capability_version)
            code_ready = (
                capability.implemented
                and capability.trust_level.value == "exploratory"
                and not capability.claim_permissions
                and has_executor(capability.executor)
                and has_validator(capability.validator)
            )
        except ValueError:
            code_ready = False
        if not code_ready:
            blockers.append("candidate implementation or protocol is incomplete")
        blockers.append("real-data benchmark has not been executed")
        entries.append(
            CapabilityCoverageEntry(
                capability_id=bench_spec.capability_id,
                capability_version=bench_spec.capability_version,
                code_ready=code_ready,
                local_fixture_ready=code_ready,
                environment_ready=None,
                real_benchmark_ready=False,
                synthetic_case_ids=tuple(case.case_id for case in bench_spec.cases),
                required_real_datasets=bench_spec.required_real_datasets,
                blockers=tuple(blockers),
            )
        )
    return CapabilityBenchmarkMatrix(
        entries=tuple(entries),
        code_ready=all(item.code_ready for item in entries),
        local_fixture_ready=all(item.local_fixture_ready for item in entries),
        real_benchmark_ready=False,
        release_ready=False,
    )


def server_benchmark_plan() -> ServerBenchmarkPlan:
    jobs: list[dict[str, Any]] = []
    profiles = {
        "association.sceptre.v1": "sceptre-v1",
        "composition.propeller.v1": "composition-v1",
        "target.responder.mixscape.v1": "perturbseq-python-v1",
    }
    registry = CapabilityRegistry.load_default(include_external=False)
    for spec in benchmark_specs():
        capability = registry.get(spec.capability_id, spec.capability_version)
        for dataset_id in spec.required_real_datasets:
            dependency_jobs = [
                f"{dataset_id}::{dependency}"
                for dependency in capability.depends_on
                if dependency in _REAL_DATASETS and dataset_id in _REAL_DATASETS[dependency]
            ]
            jobs.append(
                {
                    "job_id": f"{dataset_id}::{spec.capability_id}",
                    "dataset_id": dataset_id,
                    "capability_id": spec.capability_id,
                    "capability_version": spec.capability_version,
                    "depends_on": sorted(dependency_jobs),
                    "environment_profile": profiles.get(spec.capability_id, "python-science-v1"),
                    "prepare_commands": [
                        f"python -m pertura_bench fetch {dataset_id} --cache $PERTURA_BENCH_CACHE",
                        f"python -m pertura_bench convert {dataset_id} --cache $PERTURA_BENCH_CACHE",
                        (
                            f"python -m pertura_bench subset {dataset_id} --split evaluation "
                            "--cache $PERTURA_BENCH_CACHE --input <converted> --output <subset> "
                            "--source-lock-hash <lock> --label-column <column> --labels <labels>"
                        ),
                    ],
                    "command": (
                        "python -m pertura_bench run "
                        f"{spec.capability_id} --tier full_dataset --dataset {dataset_id}"
                    ),
                    "resources": {
                        "cpus": 8 if spec.capability_id == "association.sceptre.v1" else 4,
                        "memory_gb": 64 if dataset_id == "replogle_k562_essential_2022" else 32,
                        "walltime_minutes": 720,
                    },
                    "split": "evaluation",
                    "expected_locks": ["source", "converted", "subset", "verdict"],
                    "failure_policy": {
                        "missing_lock": "blocked",
                        "missing_environment": "blocked",
                        "timeout": "failed_no_fallback",
                    },
                }
            )
    return ServerBenchmarkPlan(
        jobs=tuple(jobs),
        datasets=tuple(sorted({dataset for values in _REAL_DATASETS.values() for dataset in values})),
    )

def write_server_plan(path: str | Path) -> dict[str, Any]:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    plan = server_benchmark_plan()
    destination.write_text(
        json.dumps(plan.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {
        "plan_id": plan.plan_id,
        "plan_hash": plan.canonical_hash,
        "path": str(destination),
        "job_count": len(plan.jobs),
    }
