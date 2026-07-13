from __future__ import annotations

import csv
import hashlib
import json
import os
import tempfile
from contextlib import contextmanager
from importlib import resources
from pathlib import Path
from typing import Any, Iterable

from pertura_bench.capability_models import (
    CapabilityBenchmarkCase,
    CapabilityBenchmarkMetric,
    CapabilityBenchmarkVerdict,
    ScientificResultDigest,
)
from pertura_core import (
    AnalysisStatus,
    CapabilityRunRequest,
    CapabilityTrust,
    DatasetContract,
    DependencyRef,
    DiagnosticStatus,
    ResultEnvelope,
    ScopeKey,
    VirtualStatus,
)
from pertura_core.hashing import canonical_hash, file_sha256
from pertura_workflow.capabilities import CapabilityRegistry

_RUNNER_MODULES = {
    "intake_materialize": "intake_candidates.py",
    "dataset_integrity": "intake_candidates.py",
    "design_balance": "intake_candidates.py",
    "guide_integrity": "guide_candidates.py",
    "guide_nb_mixture": "guide_candidates.py",
    "guide_ambient": "guide_candidates.py",
    "moi_doublet": "guide_candidates.py",
    "retained_cells": "guide_candidates.py",
    "state_reference_fit": "state_candidates.py",
    "state_reference_map": "state_candidates.py",
    "state_annotation_candidates": "state_candidates.py",
    "control_nmf": "state_candidates.py",
    "mixscape_responder": "target_candidates.py",
    "guide_efficacy": "target_candidates.py",
    "target_reliability_aggregate": "target_candidates.py",
    "sceptre_association": "effect_candidates.py",
    "propeller_composition": "effect_candidates.py",
    "guide_target_sensitivity": "effect_candidates.py",
    "module_global_effect": "effect_candidates.py",
    "method_null_calibration": "effect_candidates.py",
    "effect_matrix_assemble": "p4_candidates.py",
    "response_signed_nmf": "p4_candidates.py",
    "perturbation_cluster": "p4_candidates.py",
    "enrichment_ora": "p4_candidates.py",
    "enrichment_gsea_prerank": "p4_candidates.py",
    "regulator_activity_ulm": "p4_candidates.py",
    "perturbation_regulator_network": "p4_candidates.py",
    "literature_europepmc": "p4_candidates.py",
    "interpretation_evidence_map": "p4_candidates.py",
    "virtual_split_contract": "p5_candidates.py",
    "virtual_prediction_ingest": "p5_candidates.py",
    "virtual_leakage_audit": "p5_candidates.py",
    "virtual_baselines": "p5_candidates.py",
    "virtual_evaluate_comprehensive": "p5_candidates.py",
    "design_next_panel": "p5_candidates.py",
}


def scientific_result_digest(
    result: ResultEnvelope | dict[str, Any],
) -> ScientificResultDigest:
    payload = (
        result.model_dump(mode="json")
        if isinstance(result, ResultEnvelope)
        else dict(result)
    )
    scope = dict(payload.get("scope") or {})
    scope.pop("scope_id", None)
    scope.pop("canonical_hash", None)
    metadata_payload = dict(payload.get("metadata") or {})
    # Runtime dependency hashes bind one concrete execution and may include
    # run/result identifiers. Scientific dependency content is represented
    # separately below by benchmark_dependency_scientific_hashes.
    metadata_payload.pop("consumed_dependency_hashes", None)
    semantic_hashes = metadata_payload.pop(
        "benchmark_scientific_output_hashes", None
    )
    dependency_scientific_hashes = dict(
        metadata_payload.pop("benchmark_dependency_scientific_hashes", {}) or {}
    )
    output_hashes = (
        {str(name): str(value) for name, value in sorted(semantic_hashes.items())}
        if semantic_hashes
        else {
            Path(str(name)).name: str(value)
            for name, value in sorted((payload.get("output_hashes") or {}).items())
        }
    )
    dependencies = tuple(
        sorted(
            canonical_hash(
                {
                    "kind": item.get("kind"),
                    "role": item.get("role"),
                    "required": item.get("required", True),
                    "state": item.get("state", "current"),
                    "scientific_content_hash": dependency_scientific_hashes.get(
                        _dependency_binding_key(index, item)
                    )
                    or item.get("content_hash")
                    or item.get("object_hash"),
                }
            )
            for index, item in enumerate(payload.get("dependencies") or ())
        )
    )
    metadata = _stable_payload(metadata_payload)
    return ScientificResultDigest(
        capability_id=str(payload["capability_id"]),
        capability_version=str(payload["capability_version"]),
        status=enum_value(payload.get("status")),
        result_kind=str(payload.get("result_kind") or ""),
        source_class=enum_value(payload.get("source_class")),
        scope_payload=_stable_payload(scope),
        blockers=tuple(str(item) for item in payload.get("blockers") or ()),
        cautions=tuple(str(item) for item in payload.get("cautions") or ()),
        metrics=_stable_payload(dict(payload.get("metrics") or {})),
        output_content_hashes=output_hashes,
        dependency_content_hashes=dependencies,
        scientific_metadata=metadata,
    )


def _dependency_binding_key(index: int, item: dict[str, Any]) -> str:
    return f"{item.get('kind', '')}|{item.get('role', '')}|{index}"

def _stable_payload(value: Any) -> Any:
    volatile = {
        "run_id",
        "request_id",
        "result_id",
        "receipt_id",
        "contract_id",
        "contract_hash",
        "canonical_hash",
        "scope_id",
        "dependency_id",
        "created_at_utc",
        "completed_at_utc",
        "requested_at_utc",
        "signed_at_utc",
        "runtime_seconds",
        "local_output_paths",
        "output_paths",
        "path",
    }
    if isinstance(value, dict):
        return {
            str(key): _stable_payload(item)
            for key, item in sorted(value.items())
            if str(key) not in volatile
        }
    if isinstance(value, (list, tuple)):
        return [_stable_payload(item) for item in value]
    if isinstance(value, str):
        candidate = Path(value)
        if candidate.is_absolute():
            return candidate.name
    return value


def enum_value(value: Any) -> str:
    return str(getattr(value, "value", value or ""))


def run_synthetic_case(case: CapabilityBenchmarkCase, *, catalog: dict[str, Any]) -> CapabilityBenchmarkVerdict:
    spec = CapabilityRegistry.load_default(include_external=False).get(
        case.capability_id, case.capability_version
    )
    input_hashes = benchmark_input_hashes(case, spec, catalog)
    runner_digest = input_hashes["product_spine"]
    try:
        if case.execution_mode == "stale_audit":
            result = _run_stale_audit(case, spec)
        elif case.scenario == "determinism":
            execute = (
                _run_protocol_fake
                if case.execution_mode == "protocol_fake"
                else _run_product_once
            )
            first = execute(case, spec)
            second = execute(case, spec)
            first_digest = scientific_result_digest(first)
            second_digest = scientific_result_digest(second)
            if first_digest.canonical_hash != second_digest.canonical_hash:
                return make_verdict(
                    case,
                    outcome="failed",
                    observed_status=enum_value(first.get("status")),
                    blockers=tuple(first.get("blockers") or ()),
                    input_hashes=input_hashes,
                    runner_hash=runner_digest,
                    reasons=("scientific result digest changed across identical reruns",),
                    scientific_hash=first_digest.canonical_hash,
                )
            result = first
        elif case.execution_mode == "protocol_fake":
            result = _run_protocol_fake(case, spec)
        else:
            result = _run_product_once(case, spec)
        digest = scientific_result_digest(result)
        status = enum_value(result.get("status"))
        blockers = tuple(str(item) for item in result.get("blockers") or ())
        reasons: list[str] = []
        if case.expected_statuses and status not in case.expected_statuses:
            reasons.append(
                f"observed status {status!r} is not one of {list(case.expected_statuses)}"
            )
        for token in case.expected_blocker_contains:
            if not any(token in blocker for blocker in blockers):
                reasons.append(f"expected blocker token was not observed: {token}")
        for name in case.required_outputs:
            if name not in {
                Path(str(item)).name for item in result.get("output_paths") or ()
            }:
                reasons.append(f"required output is missing: {name}")
        observed_metrics = dict(result.get("metrics") or {})
        observed_metrics.setdefault(
            "failure_detected",
            int(status in {"blocked", "failed", "exception_blocked", "protocol_rejected"}),
        )
        observed_metrics.setdefault("protocol_rejected", int(status == "protocol_rejected"))
        evaluated_metrics = tuple(
            _evaluate_metric(metric, observed_metrics)
            for metric in case.metrics
        )
        if any(metric.passed is False for metric in evaluated_metrics):
            reasons.append("one or more benchmark metrics missed threshold")
        return make_verdict(
            case,
            outcome="failed" if reasons else "passed",
            observed_status=status,
            blockers=blockers,
            metrics=evaluated_metrics,
            input_hashes=input_hashes,
            output_hashes=dict(result.get("output_hashes") or {}),
            runner_hash=runner_digest,
            reasons=tuple(reasons),
            scientific_hash=digest.canonical_hash,
        )
    except Exception as exc:  # benchmark must record fail-closed behavior
        status = "exception_blocked"
        blockers = (str(exc),)
        token_match = bool(case.expected_blocker_contains) and all(
            any(token in blocker for blocker in blockers)
            for token in case.expected_blocker_contains
        )
        accepted = status in case.expected_statuses and token_match
        exception_result = {
            "capability_id": spec.capability_id,
            "capability_version": spec.version,
            "status": status,
            "result_kind": spec.output_kind,
            "source_class": spec.source_class.value,
            "scope": {"dataset_id": "synthetic"},
            "blockers": blockers,
            "cautions": (),
            "metrics": {"exception_blocked": True},
            "output_paths": (),
            "output_hashes": {},
            "dependencies": (),
            "metadata": {"execution_mode": case.execution_mode},
        }
        digest = scientific_result_digest(exception_result)
        evaluated_metrics = tuple(
            _evaluate_metric(metric, exception_result["metrics"])
            for metric in case.metrics
        )
        metrics_passed = bool(evaluated_metrics) and all(
            metric.passed is True for metric in evaluated_metrics
        )
        accepted = accepted and metrics_passed
        reasons = []
        if status not in case.expected_statuses:
            reasons.append(f"unexpected benchmark exception: {exc}")
        if not token_match:
            reasons.append("exception did not match an explicit planted blocker token")
        if not metrics_passed:
            reasons.append("exception benchmark metrics were absent or failed")
        return make_verdict(
            case,
            outcome="passed" if accepted else "failed",
            observed_status=status,
            blockers=blockers,
            metrics=evaluated_metrics,
            input_hashes=input_hashes,
            runner_hash=runner_digest,
            reasons=tuple(reasons),
            scientific_hash=digest.canonical_hash,
        )


def make_verdict(
    case: CapabilityBenchmarkCase,
    *,
    outcome: str,
    observed_status: str | None,
    blockers: tuple[str, ...] = (),
    metrics: tuple[CapabilityBenchmarkMetric, ...] = (),
    input_hashes: dict[str, str] | None = None,
    output_hashes: dict[str, str] | None = None,
    runner_hash: str | None = None,
    reasons: tuple[str, ...] = (),
    scientific_hash: str | None = None,
    environment_lock_hash: str | None = None,
    hard_gates: dict[str, bool] | None = None,
    scientific_metrics_status: str = "not_required",
    reference_hashes: dict[str, str] | None = None,
    continuous_metrics: dict[str, float | int | str | None] | None = None,
    limitations: tuple[str, ...] = (),
) -> CapabilityBenchmarkVerdict:
    return CapabilityBenchmarkVerdict(
        case_id=case.case_id,
        case_hash=case.canonical_hash,
        capability_id=case.capability_id,
        capability_version=case.capability_version,
        tier=case.tier,
        execution_mode=case.execution_mode,
        outcome=outcome,
        hard_gates=hard_gates or {},
        scientific_metrics_status=scientific_metrics_status,
        reference_hashes=reference_hashes or {},
        continuous_metrics=continuous_metrics or {},
        limitations=limitations,
        observed_status=observed_status,
        observed_blockers=blockers,
        metrics=metrics,
        input_hashes=input_hashes or {},
        output_hashes=output_hashes or {},
        scientific_result_hash=scientific_hash,
        runner_hash=runner_hash,
        environment_lock_hash=environment_lock_hash,
        reasons=reasons,
    )


def _evaluate_metric(
    metric: CapabilityBenchmarkMetric, observed: dict[str, Any]
) -> CapabilityBenchmarkMetric:
    value = observed.get(metric.name)
    if value is None:
        passed = False
    elif metric.operator == "eq":
        passed = value == metric.threshold
    elif metric.operator == "lte":
        passed = float(value) <= float(metric.threshold)
    else:
        passed = float(value) >= float(metric.threshold)
    payload = metric.model_dump(mode="json")
    payload.update({"metric_id": "", "canonical_hash": "", "observed": value, "passed": passed})
    return CapabilityBenchmarkMetric.model_validate(payload)


def runner_hash(executor: str) -> str:
    """Hash the complete executable product spine used by benchmark verdicts."""

    capability_name = _RUNNER_MODULES.get(executor, "executors.py")
    capability_root = Path(str(resources.files("pertura_workflow.capabilities")))
    workflow_root = Path(str(resources.files("pertura_workflow")))
    runtime_root = Path(str(resources.files("pertura_runtime")))
    core_root = Path(str(resources.files("pertura_core")))
    paths = {
        "capability_runner": capability_root / capability_name,
        "capability_executors": capability_root / "executors.py",
        "capability_registry": capability_root / "registry.py",
        "planner": workflow_root / "planner.py",
        "planner_routes": capability_root / "planner_routes.json",
        "method_router": workflow_root / "method_router.py",
        "product_runtime": runtime_root / "product.py",
        "verifier_broker": runtime_root / "verifier" / "broker.py",
        "authority_store": runtime_root / "verifier" / "store.py",
        "authority_session_store": runtime_root / "verifier" / "session_store.py",
        "authority_sessions": runtime_root / "verifier" / "sessions.py",
        "promotion_engine": core_root / "promotion.py",
        "core_models": core_root / "models.py",
        "benchmark_capability_kernel": Path(__file__).with_name("capability_bench.py"),
        "benchmark_synthetic_executor": Path(__file__),
        "benchmark_real_executor": Path(__file__).with_name("real_execution.py"),
    }
    r_runners = {
        "sceptre_association": "sceptre_association.R",
        "propeller_composition": "propeller_composition.R",
    }
    if executor in r_runners:
        paths["scientific_runner"] = (
            capability_root / "runners" / r_runners[executor]
        )
    missing = sorted(name for name, path in paths.items() if not path.is_file())
    if missing:
        raise ValueError("product-spine files are missing: " + ", ".join(missing))
    return canonical_hash(
        {name: file_sha256(path) for name, path in sorted(paths.items())}
    )


def benchmark_input_hashes(
    case: CapabilityBenchmarkCase,
    spec: Any,
    catalog: dict[str, Any],
) -> dict[str, str]:
    bindings = {
        "case": case.canonical_hash,
        "catalog": canonical_hash(catalog),
        "capability_spec": spec.canonical_hash,
        "fixture": canonical_hash(
            {
                "fixture_id": case.fixture_id,
                "fixture_version": case.fixture_version,
                "seed": case.seed,
                "parameters": case.parameters,
            }
        ),
        "product_spine": runner_hash(spec.executor),
    }
    if case.environment_profile:
        profile = Path(
            str(resources.files("pertura_workflow").joinpath(
                "environments", f"{case.environment_profile}.yml"
            ))
        )
        if not profile.is_file():
            raise ValueError(
                f"benchmark environment profile is missing: {case.environment_profile}"
            )
        bindings["environment_spec"] = file_sha256(profile)
    return bindings

def _protocol_request(
    spec: Any,
    contract: DatasetContract,
    parameters: dict[str, Any],
) -> CapabilityRunRequest:
    request = CapabilityRunRequest(
        run_id="synthetic-protocol-run",
        capability_id=spec.capability_id,
        capability_version=spec.version,
        contract_id=contract.contract_id,
        contract_hash=contract.canonical_hash,
        scope=ScopeKey(dataset_id=contract.dataset_id),
        parameters=parameters,
    )
    return CapabilityRunRequest.model_validate_json(request.model_dump_json())


def _run_r_protocol_adapter(
    case: CapabilityBenchmarkCase,
    spec: Any,
    root: Path,
) -> ResultEnvelope:
    import subprocess
    from unittest.mock import patch

    from pertura_workflow.capabilities import effect_candidates
    from pertura_workflow.capabilities.executors import _VALIDATORS

    files = _write_fixture_files(root / "fixture")
    contract = DatasetContract(
        dataset_id="synthetic",
        input_format="csv",
        source_paths=(str(root),),
        expression_matrix={"raw_counts_confirmed": True},
    )
    staging = root / "staging"
    staging.mkdir(parents=True, exist_ok=True)
    malformed = case.scenario in {"blocked", "planted_failure"}

    if spec.capability_id == "association.sceptre.v1":
        inputs = {}
        for name in (
            "response_matrix_path",
            "guide_matrix_path",
            "guide_target_map_path",
            "discovery_pairs_path",
        ):
            path = root / f"{name}.csv"
            path.write_text("id,value\na,1\n", encoding="utf-8", newline="\n")
            inputs[name] = str(path)
        request = _protocol_request(spec, contract, {"moi": "high"} | inputs)

        def fake_profile(
            profile: str, runner: Any, config_path: Path, *, timeout: int
        ) -> subprocess.CompletedProcess[str]:
            config = json.loads(Path(config_path).read_text(encoding="utf-8"))
            if profile != "sceptre-v1" or config.get("schema_version") != "pertura-sceptre-run-config-v1":
                raise ValueError("SCEPTRE serializer emitted an invalid config")
            output = Path(config["output_dir"])
            calibration_passed = case.scenario != "blocked"
            (output / "sceptre_metadata.json").write_text(
                json.dumps(
                    {
                        "calibration_passed": calibration_passed,
                        "calibration_type1_rate": 0.04 if calibration_passed else 0.50,
                        "discovery_executed": calibration_passed,
                    },
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
                newline="\n",
            )
            (output / "sceptre_calibration.csv").write_text(
                "pair,p_value\nnull,0.5\n", encoding="utf-8", newline="\n"
            )
            columns = (
                "response_id,grna_target,p_value,fold_change,se_fold_change\n"
                if case.scenario == "planted_failure"
                else "response_id,grna_target,p_value,fold_change,se_fold_change,FDR\n"
            )
            values = "G1,T1,0.01,1.2,0.2\n" if case.scenario == "planted_failure" else "G1,T1,0.01,1.2,0.2,0.02\n"
            (output / "sceptre_results.csv").write_text(
                columns + values, encoding="utf-8", newline="\n"
            )
            return subprocess.CompletedProcess([str(runner)], 0, "", "")

        with patch.object(effect_candidates, "_run_r_profile", fake_profile):
            result = effect_candidates.run_sceptre_association(
                spec, request, contract, staging
            )
    else:
        request = _protocol_request(
            spec,
            contract,
            {
                "metadata_path": str(files["design.csv"]),
                "sample_column": "replicate",
                "state_column": "state",
                "condition_column": "condition",
                "batch_column": "batch",
            },
        )

        def fake_profile(
            profile: str, runner: Any, config_path: Path, *, timeout: int
        ) -> subprocess.CompletedProcess[str]:
            config = json.loads(Path(config_path).read_text(encoding="utf-8"))
            if profile != "composition-v1" or config.get("schema_version") != "pertura-propeller-run-config-v1":
                raise ValueError("Propeller serializer emitted an invalid config")
            output = Path(config["output_dir"])
            header = (
                "cluster,PropMean\n"
                if case.scenario == "planted_failure"
                else "cluster,PropMean,FDR\n"
            )
            rows = (
                "S1,0.5\n"
                if case.scenario == "planted_failure"
                else "S1,0.5,1.2\n"
                if case.scenario == "blocked"
                else "S1,0.5,0.04\nS2,0.5,0.08\n"
            )
            (output / "propeller_results.csv").write_text(
                header + rows, encoding="utf-8", newline="\n"
            )
            (output / "sample_state_proportions.csv").write_text(
                "sample,state,proportion\nA1,S1,0.5\n",
                encoding="utf-8",
                newline="\n",
            )
            (output / "propeller_metadata.json").write_text(
                json.dumps({"method": "protocol_fake"}, sort_keys=True) + "\n",
                encoding="utf-8",
                newline="\n",
            )
            return subprocess.CompletedProcess([str(runner)], 0, "", "")

        with patch.object(effect_candidates, "_run_r_profile", fake_profile):
            result = effect_candidates.run_propeller_composition(
                spec, request, contract, staging
            )
    parsed = ResultEnvelope.model_validate_json(result.model_dump_json())
    _VALIDATORS[spec.validator](spec, request, contract, parsed)
    return parsed


def _run_p4_external_protocol_adapter(
    case: CapabilityBenchmarkCase,
    spec: Any,
    root: Path,
) -> ResultEnvelope:
    import subprocess
    from unittest.mock import patch

    import numpy as np

    from pertura_workflow.capabilities import p4_candidates
    from pertura_workflow.capabilities.executors import _VALIDATORS

    contract = DatasetContract(
        dataset_id="synthetic",
        input_format="npz",
        source_paths=(str(root),),
        expression_matrix={"raw_counts_confirmed": True},
    )
    staging = root / "staging"
    staging.mkdir(parents=True, exist_ok=True)
    matrix = root / "effect_matrix.npz"
    np.savez_compressed(
        matrix,
        effects=np.asarray([[1.0, -1.0, 0.5], [-0.5, 0.8, -1.2]]),
        observed_mask=np.asarray([[True, True, True], [True, True, True]]),
        perturbations=np.asarray(["P1", "P2"]),
        features=np.asarray(["G1", "G2", "G3"]),
    )
    effect_result = {
        "result_id": "effect_matrix_fixture",
        "result_kind": "effect_matrix",
        "local_output_paths": [str(matrix)],
    }
    request = _protocol_request(
        spec,
        contract,
        {
            "permutation_num": 100,
            "min_gene_set_size": 2,
            "max_gene_set_size": 10,
            "minimum_targets": 2,
        },
    )
    if spec.capability_id == "enrichment.gsea_prerank.v1":
        module = root / "gmt_modules.json"
        module.write_text(
            json.dumps({"modules": {"SET1": ["G1", "G2", "G3"]}}),
            encoding="utf-8",
        )
        effect_result["local_output_paths"].append(str(module))

        def fake_profile(runner_name: str, config_path: Path, timeout: int):
            config = json.loads(Path(config_path).read_text(encoding="utf-8"))
            if runner_name != "gsea_prerank_runner.py":
                raise ValueError("wrong GSEA runner")
            output = Path(config["output_path"])
            if case.scenario == "blocked":
                text = "perturbation_id,gene_set,NES,PValue\nP1,SET1,1.2,0.01\n"
            elif case.scenario == "planted_failure":
                text = "perturbation_id,gene_set,NES,PValue,FDR\nP1,SET1,nan,0.01,0.02\n"
            else:
                text = (
                    "perturbation_id,gene_set,ES,NES,PValue,FDR\n"
                    "P1,SET1,0.8,1.2,0.01,0.02\n"
                )
            output.write_text(text, encoding="utf-8")
            return subprocess.CompletedProcess([runner_name], 0, "", "")

        target = p4_candidates.run_enrichment_gsea_prerank
    else:
        resource_dir = root / "resource"
        resource_dir.mkdir()
        network = resource_dir / "collectri_human.csv"
        network.write_text(
            "source,target,weight\nTF1,G1,1\nTF1,G2,-1\n",
            encoding="utf-8",
        )
        (staging / "_runtime_dependencies.json").write_text(
            json.dumps({
                "dependencies": [{
                    "kind": "knowledge_resource",
                    "payload": {
                        "resource_dir": str(resource_dir),
                        "artifacts": [{
                            "artifact_id": "collectri_human",
                            "relative_path": network.name,
                        }],
                    },
                }]
            }),
            encoding="utf-8",
        )

        def fake_profile(runner_name: str, config_path: Path, timeout: int):
            config = json.loads(Path(config_path).read_text(encoding="utf-8"))
            if runner_name != "ulm_runner.py":
                raise ValueError("wrong ULM runner")
            output = Path(config["output_path"])
            if case.scenario == "blocked":
                text = "perturbation_id,regulator,activity\nP1,TF1,2.0\n"
            elif case.scenario == "planted_failure":
                text = "perturbation_id,regulator,activity,FDR\nP1,TF1,2.0,2.0\n"
            else:
                text = (
                    "perturbation_id,regulator,activity,statistic,PValue,FDR,n_targets\n"
                    "P1,TF1,2.0,2.0,,0.02,2\n"
                )
            output.write_text(text, encoding="utf-8")
            return subprocess.CompletedProcess([runner_name], 0, "", "")

        target = p4_candidates.run_regulator_activity_ulm
    (staging / "_dependency_results.json").write_text(
        json.dumps({"results": [effect_result]}),
        encoding="utf-8",
    )
    with patch.object(p4_candidates, "_run_profile", fake_profile):
        result = target(spec, request, contract, staging)
    parsed = ResultEnvelope.model_validate_json(result.model_dump_json())
    _VALIDATORS[spec.validator](spec, request, contract, parsed)
    return parsed


def _run_generic_protocol_adapter(
    case: CapabilityBenchmarkCase,
    spec: Any,
    root: Path,
) -> tuple[ResultEnvelope | None, str | None]:
    from pertura_workflow.capabilities.candidate_common import write_json
    from pertura_workflow.capabilities.executors import _VALIDATORS

    contract = DatasetContract(
        dataset_id="synthetic",
        input_format="json",
        source_paths=(str(root),),
        expression_matrix={"raw_counts_confirmed": True},
    )
    request = _protocol_request(spec, contract, {"seed": case.seed})
    output = write_json(
        root,
        "protocol_output.json",
        {
            "schema_version": "pertura-protocol-adapter-v1",
            "capability_id": spec.capability_id,
            "case_hash": case.canonical_hash,
        },
    )
    malformed = case.scenario in {"blocked", "planted_failure"}
    result = ResultEnvelope(
        run_id=request.run_id,
        request_id=request.request_id,
        capability_id=spec.capability_id,
        capability_version=spec.version,
        capability_trust=spec.trust_level,
        contract_id=contract.contract_id,
        contract_hash=contract.canonical_hash,
        scope=request.scope,
        status=(
            DiagnosticStatus.caution
            if spec.kind == "diagnostic"
            else VirtualStatus.limited
            if spec.kind == "virtual"
            else AnalysisStatus.completed_with_caution
        ),
        result_kind=("planted_wrong_kind" if malformed else spec.output_kind),
        source_class=spec.source_class,
        summary="Protocol adapter roundtrip.",
        cautions=("external scientific environment was not executed",),
        metrics={"protocol_roundtrip": True},
        output_paths=(str(output),),
        output_hashes={str(output): file_sha256(output)},
        dependencies=request.dependencies,
        metadata={"validation_status": "synthetic_only"},
    )
    parsed = ResultEnvelope.model_validate_json(result.model_dump_json())
    try:
        _VALIDATORS[spec.validator](spec, request, contract, parsed)
    except ValueError as exc:
        return None, str(exc)
    return parsed, None


def _run_protocol_fake(case: CapabilityBenchmarkCase, spec: Any) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="pertura-protocol-adapter-") as directory:
        root = Path(directory)
        if spec.capability_id in {
            "association.sceptre.v1",
            "composition.propeller.v1",
        }:
            parsed = _run_r_protocol_adapter(case, spec, root)
            rejected = enum_value(parsed.status) == "blocked"
            blocker_text = tuple(str(item) for item in parsed.blockers)
            output_paths = tuple(parsed.output_paths)
            output_hashes = _semantic_output_hashes(
                parsed.model_dump(mode="json"), root / "staging"
            )
            parser_name = "production_r_config_and_output_parser"
        elif spec.capability_id in {
            "enrichment.gsea_prerank.v1",
            "regulator.activity.ulm.v1",
        }:
            parsed = _run_p4_external_protocol_adapter(case, spec, root)
            rejected = enum_value(parsed.status) == "blocked"
            blocker_text = tuple(str(item) for item in parsed.blockers)
            output_paths = tuple(parsed.output_paths)
            output_hashes = _semantic_output_hashes(
                parsed.model_dump(mode="json"), root / "staging"
            )
            parser_name = "production_python_config_and_output_parser"
        else:
            parsed, error = _run_generic_protocol_adapter(case, spec, root)
            rejected = parsed is None
            blocker_text = (error,) if error else ()
            output_paths = () if parsed is None else tuple(parsed.output_paths)
            output_hashes = (
                {}
                if parsed is None
                else _semantic_output_hashes(parsed.model_dump(mode="json"), root)
            )
            parser_name = "production_request_result_validator"
        status = "protocol_rejected" if rejected else "protocol_validated"
        return {
            "capability_id": spec.capability_id,
            "capability_version": spec.version,
            "status": status,
            "result_kind": spec.output_kind,
            "source_class": spec.source_class.value,
            "scope": {"dataset_id": "synthetic"},
            "blockers": blocker_text,
            "cautions": (
                f"{case.environment_profile} integration was not executed on this machine",
            ),
            "metrics": {
                "protocol_adapter_executed": True,
                "actual_serializer_parser": True,
                "malformed_output_rejected": rejected,
                "external_environment_executed": False,
            },
            "output_paths": output_paths,
            "output_hashes": output_hashes,
            "dependencies": (),
            "metadata": {
                "validation_status": "synthetic_only",
                "environment_profile": case.environment_profile,
                "execution_mode": "protocol_fake",
                "adapter": parser_name,
                "benchmark_scientific_output_hashes": output_hashes,
            },
        }


def _enrich_benchmark_result(
    result: dict[str, Any],
    contract: DatasetContract,
    known_results: dict[str, dict[str, Any]],
    workspace_root: Path,
) -> dict[str, Any]:
    payload = dict(result)
    metadata = dict(payload.get("metadata") or {})
    metadata["benchmark_scientific_output_hashes"] = _semantic_output_hashes(
        payload, workspace_root
    )
    result_by_id = {
        item["result_id"]: item
        for item in known_results.values()
        if item.get("result_id")
    }
    dependency_hashes: dict[str, str] = {}
    contract_payload = contract.model_dump(mode="json")
    for volatile in (
        "canonical_hash",
        "contract_id",
        "parent_contract_id",
        "created_at_utc",
        "source_paths",
    ):
        contract_payload.pop(volatile, None)
    stable_contract_hash = canonical_hash(_stable_payload(contract_payload))
    for index, dependency in enumerate(payload.get("dependencies") or ()):
        binding_key = _dependency_binding_key(index, dependency)
        if dependency.get("kind") == "contract":
            dependency_hashes[binding_key] = stable_contract_hash
            continue
        upstream = result_by_id.get(dependency.get("object_id"))
        if upstream is not None:
            dependency_hashes[binding_key] = (
                scientific_result_digest(upstream).canonical_hash
            )
    metadata["benchmark_dependency_scientific_hashes"] = dependency_hashes
    payload["metadata"] = metadata
    return payload

def _execute_product_capability(
    runtime: Any,
    contract: DatasetContract,
    files: dict[str, Path],
    results: dict[str, dict[str, Any]],
    workspace_root: Path,
    capability_id: str,
    scenario: str,
    *,
    pipeline_root: str | None = None,
) -> dict[str, Any]:
    if pipeline_root is None:
        pipeline_root = capability_id
    if capability_id in results:
        return results[capability_id]
    capability = runtime.registry.get(capability_id)
    dependencies = []
    for upstream_id in capability.depends_on:
        upstream = _execute_product_capability(
            runtime,
            contract,
            files,
            results,
            workspace_root,
            upstream_id,
            "happy",
            pipeline_root=pipeline_root,
        )
        dependencies.append(
            {
                "kind": upstream["result_kind"],
                "object_id": upstream["result_id"],
                "object_hash": upstream["canonical_hash"],
                "role": f"benchmark_upstream:{upstream_id}",
            }
        )
    parameters = _fixture_parameters(
        capability_id,
        scenario,
        files,
        results,
        workspace_root,
        pipeline_root=pipeline_root,
    )
    if capability.kind == "diagnostic":
        compact = runtime.run_diagnostic(
            capability_id,
            contract_id=contract.contract_id,
            parameters=parameters,
            dependencies=dependencies,
        )
    else:
        compact = runtime.run_analysis(
            capability_id,
            capability_id=capability_id,
            contract_id=contract.contract_id,
            parameters=parameters,
            dependencies=dependencies,
        )
    result_id = str(compact.get("result_id") or "")
    matching = [
        dict(item)
        for item in runtime.broker.list_results(workspace_root.name)
        if item.get("result_id") == result_id
    ]
    if len(matching) != 1:
        raise RuntimeError(
            f"product path did not commit exactly one {capability_id} result"
        )
    result = _enrich_benchmark_result(matching[0], contract, results, workspace_root)
    if (result.get("metadata") or {}).get("verification_state") != "validated_untrusted":
        raise RuntimeError(
            f"candidate result did not carry validated_untrusted state: {capability_id}"
        )
    results[capability_id] = result
    return result


def _assert_finalized_candidate_semantics(
    runtime: Any,
    report: dict[str, Any],
    run_id: str,
    expected_result_ids: set[str],
) -> None:
    committed = runtime.broker.list_committed(run_id)
    by_id = {item["result"]["result_id"]: item for item in committed}
    if not expected_result_ids.issubset(by_id):
        missing = sorted(expected_result_ids - set(by_id))
        raise RuntimeError(f"finalizer omitted committed candidate results: {missing}")
    for result_id in sorted(expected_result_ids):
        row = by_id[result_id]
        if row.get("verification_state") != "validated_untrusted":
            raise RuntimeError(
                f"sealed candidate result is not validated_untrusted: {result_id}"
            )
        if row.get("receipt") is not None:
            raise RuntimeError(f"exploratory candidate received a receipt: {result_id}")
    exploratory_ids = {
        item["result_id"] for item in report.get("exploratory_results") or ()
    }
    if not expected_result_ids.issubset(exploratory_ids):
        raise RuntimeError("final report did not place candidates in exploratory results")
    trusted_ids = {item["result_id"] for item in report.get("trusted_results") or ()}
    if expected_result_ids & trusted_ids:
        raise RuntimeError("candidate result appeared in the trusted report section")
    promoted_strong = [
        item
        for item in report.get("promotion_decisions") or ()
        if item.get("status") == "promoted"
        and item.get("max_strength") not in {None, "observation"}
    ]
    if promoted_strong:
        raise RuntimeError("candidate result produced a promoted strong statement")


def _run_stale_audit(case: CapabilityBenchmarkCase, spec: Any) -> dict[str, Any]:
    from pertura_runtime.claude.workspace import ClaudeRunWorkspace
    from pertura_runtime.product import PerturaProductRuntime

    with tempfile.TemporaryDirectory(prefix="pertura-bench-stale-") as directory:
        root = Path(directory)
        workspace = ClaudeRunWorkspace.create(
            root=root / "runs", run_id="synthetic-stale-run"
        )
        files = _write_fixture_files(workspace.root / "fixture")
        contract = DatasetContract(
            dataset_id="synthetic",
            input_format="csv",
            source_paths=(str(workspace.root),),
            expression_matrix={"raw_counts_confirmed": True},
            identity_fields={
                "control": {"status": "confirmed", "value": "NTC"},
                "replicate": {"status": "confirmed", "value": "replicate"},
                "guide_target": {"status": "confirmed", "value": "guide_map.csv"},
                "design_moi": {"status": "confirmed", "value": "low"},
                "guide_design": {"status": "confirmed", "value": "single"},
            },
        )
        with temporary_environment(
            "PERTURA_AUTHORITY_ROOT", str(root / "authority")
        ):
            runtime = PerturaProductRuntime(workspace)
            force_loopback_transport(runtime)
            results: dict[str, dict[str, Any]] = {}
            protocol_target: dict[str, Any] | None = None
            try:
                runtime._persist_contract(contract)
                runtime.broker.register_contract(contract)
                if case.environment_profile:
                    protocol_target = _run_protocol_fake(case, spec)
                    if protocol_target.get("status") != "protocol_validated":
                        raise RuntimeError(
                            "protocol adapter did not validate before stale audit"
                        )
                else:
                    _execute_product_capability(
                        runtime,
                        contract,
                        files,
                        results,
                        workspace.root,
                        spec.capability_id,
                        "happy",
                    )
                parent = _execute_product_capability(
                    runtime,
                    contract,
                    files,
                    results,
                    workspace.root,
                    "guide.assignment.nb_mixture.v1",
                    "happy",
                )
                child = _execute_product_capability(
                    runtime,
                    contract,
                    files,
                    results,
                    workspace.root,
                    "screen.moi_doublet.v1",
                    "happy",
                )
                if parent["result_id"] not in {
                    item["object_id"] for item in child.get("dependencies") or ()
                }:
                    raise RuntimeError("product descendant is not bound to its upstream")
                runtime.broker.register_runtime_object(
                    kind=parent["result_kind"],
                    object_id=parent["result_id"],
                    object_hash=parent["canonical_hash"],
                    payload=parent,
                )
                changed_hash = canonical_hash(
                    {
                        "previous": parent["canonical_hash"],
                        "benchmark_change": case.case_id,
                    }
                )
                changed = runtime.broker.register_runtime_object(
                    kind=parent["result_kind"],
                    object_id=parent["result_id"],
                    object_hash=changed_hash,
                    payload={"supersedes": parent["canonical_hash"]},
                )
                observed = {
                    item["result_id"]: item
                    for item in runtime.broker.list_results(workspace.root.name)
                }
                stale = bool(
                    changed >= 1
                    and observed.get(child["result_id"], {}).get("stale") is True
                )
                runtime.finalize_report(workspace.root.name)
                report = json.loads(
                    (workspace.reports_dir / "capability_report.json").read_text(
                        encoding="utf-8"
                    )
                )
                expected_ids = {item["result_id"] for item in results.values()}
                _assert_finalized_candidate_semantics(
                    runtime, report, workspace.root.name, expected_ids
                )
                report_results = {
                    item["result_id"]: item for item in report.get("results") or ()
                }
                finalizer_saw_stale = bool(
                    report_results.get(child["result_id"], {}).get("stale") is True
                )
                stale = stale and finalizer_saw_stale
            finally:
                runtime.close(graceful=True)
    return {
        "capability_id": spec.capability_id,
        "capability_version": spec.version,
        "status": "stale" if stale else "current",
        "result_kind": spec.output_kind,
        "source_class": spec.source_class.value,
        "scope": {"dataset_id": "synthetic"},
        "blockers": (
            ()
            if stale
            else ("product-grounded upstream hash change did not propagate stale",)
        ),
        "cautions": (),
        "metrics": {
            "stale_results": changed,
            "stale_detected": stale,
            "benchmark_product_path_executed": True,
            "benchmark_finalizer_verified": finalizer_saw_stale,
            "benchmark_candidate_semantics_verified": True,
            "protocol_adapter_executed": protocol_target is not None,
        },
        "output_paths": (),
        "output_hashes": {},
        "dependencies": (
            {
                "kind": parent["result_kind"],
                "object_id": parent["result_id"],
                "object_hash": parent["canonical_hash"],
                "content_hash": scientific_result_digest(parent).canonical_hash,
                "role": "stale_audit_upstream",
                "required": True,
                "state": "current",
            },
        ),
        "metadata": {
            "execution_mode": "stale_audit",
            "product_grounded": True,
            "finalizer_verified": finalizer_saw_stale,
            "benchmark_dependency_scientific_hashes": {
                f"{parent['result_kind']}|stale_audit_upstream|0": scientific_result_digest(parent).canonical_hash
            },
        },
    }


def _run_product_once(case: CapabilityBenchmarkCase, spec: Any) -> dict[str, Any]:
    from pertura_runtime.claude.workspace import ClaudeRunWorkspace
    from pertura_runtime.product import PerturaProductRuntime

    with tempfile.TemporaryDirectory(prefix="pertura-bench-product-") as directory:
        root = Path(directory)
        workspace = ClaudeRunWorkspace.create(
            root=root / "runs", run_id="synthetic-product-run"
        )
        files = _write_fixture_files(workspace.root / "fixture")
        contract = DatasetContract(
            dataset_id="synthetic",
            input_format="csv",
            source_paths=(str(workspace.root),),
            expression_matrix={"raw_counts_confirmed": True},
            identity_fields={
                "control": {"status": "confirmed", "value": "NTC"},
                "replicate": {"status": "confirmed", "value": "replicate"},
                "guide_target": {"status": "confirmed", "value": "guide_map.csv"},
                "design_moi": {
                    "status": "confirmed",
                    "value": "high" if case.scenario == "caution_or_unresolved" else "low",
                },
                "guide_design": {
                    "status": "confirmed",
                    "value": "combinatorial" if case.scenario == "caution_or_unresolved" else "single",
                },
            },
        )
        with temporary_environment(
            "PERTURA_AUTHORITY_ROOT", str(root / "authority")
        ):
            runtime = PerturaProductRuntime(workspace)
            force_loopback_transport(runtime)
            results: dict[str, dict[str, Any]] = {}
            try:
                runtime._persist_contract(contract)
                runtime.broker.register_contract(contract)
                result = _execute_product_capability(
                    runtime,
                    contract,
                    files,
                    results,
                    workspace.root,
                    spec.capability_id,
                    case.scenario,
                )
                runtime.finalize_report(workspace.root.name)
                report = json.loads(
                    (workspace.reports_dir / "capability_report.json").read_text(
                        encoding="utf-8"
                    )
                )
                expected_ids = {item["result_id"] for item in results.values()}
                _assert_finalized_candidate_semantics(
                    runtime, report, workspace.root.name, expected_ids
                )
                payload = dict(result)
                payload["metrics"] = dict(payload.get("metrics") or {}) | {
                    "benchmark_product_path_executed": True,
                    "benchmark_finalizer_verified": True,
                    "benchmark_candidate_semantics_verified": True,
                }
                payload["metadata"] = dict(payload.get("metadata") or {}) | {
                    "benchmark_scientific_output_hashes": _semantic_output_hashes(
                        payload, workspace.root
                    ),
                    "benchmark_product_path": {
                        "broker_commit": True,
                        "validated_untrusted": True,
                        "receipt_absent": True,
                        "finalizer_exploratory_projection": True,
                        "promoted_strong_statements": 0,
                    },
                }
                return payload
            finally:
                runtime.close(graceful=True)

def _semantic_output_hashes(
    result: dict[str, Any], workspace_root: Path
) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for value in result.get("output_paths") or ():
        path = Path(str(value))
        path = path if path.is_absolute() else workspace_root / path
        if not path.is_file():
            continue
        name = path.name
        try:
            if path.suffix.lower() == ".json":
                hashes[name] = canonical_hash(
                    _stable_payload(json.loads(path.read_text(encoding="utf-8")))
                )
            elif path.suffix.lower() in {".csv", ".tsv"}:
                delimiter = "\t" if path.suffix.lower() == ".tsv" else ","
                with path.open("r", encoding="utf-8-sig", newline="") as handle:
                    reader = csv.DictReader(handle, delimiter=delimiter)
                    hashes[name] = canonical_hash(
                        {
                            "columns": reader.fieldnames or [],
                            "rows": [dict(row) for row in reader],
                        }
                    )
            elif path.suffix.lower() == ".parquet":
                import pandas as pd

                frame = pd.read_parquet(path)
                hashes[name] = canonical_hash(
                    {
                        "columns": list(frame.columns),
                        "rows": frame.to_dict(orient="records"),
                    }
                )
            elif path.suffix.lower() == ".npz":
                import numpy as np

                with np.load(path, allow_pickle=False) as archive:
                    arrays = {
                        key: {
                            "dtype": str(archive[key].dtype),
                            "shape": list(archive[key].shape),
                            "content_sha256": "sha256:"
                            + hashlib.sha256(
                                archive[key].tobytes(order="C")
                            ).hexdigest(),
                        }
                        for key in sorted(archive.files)
                    }
                hashes[name] = canonical_hash(arrays)
            else:
                hashes[name] = file_sha256(path)
        except (OSError, ValueError, TypeError, ImportError):
            hashes[name] = file_sha256(path)
    return hashes


def force_loopback_transport(runtime: Any) -> None:
    """Avoid noisy denied named-pipe probes in restricted Windows CI."""

    if os.name != "nt":
        return
    from pertura_runtime.verifier.broker import _new_loopback_address

    runtime._broker._address, runtime._broker._family = _new_loopback_address()


@contextmanager
def temporary_environment(name: str, value: str) -> Iterable[None]:
    original = os.environ.get(name)
    os.environ[name] = value
    try:
        yield
    finally:
        if original is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = original


def _write_fixture_files(root: Path) -> dict[str, Path]:
    root.mkdir(parents=True, exist_ok=True)
    files = {name: root / name for name in (
        "expression.csv",
        "fractional.csv",
        "collision.csv",
        "negative.csv",
        "design.csv",
        "design_two.csv",
        "confounded.csv",
        "missing_design.csv",
        "guide_counts.csv",
        "guide_counts_zero.csv",
        "raw_guide_counts.csv",
        "raw_guide_bad.csv",
        "rna_barcodes.csv",
        "guide_map.csv",
        "guide_map_bad.csv",
        "target_expression.csv",
        "target_metadata.csv",
        "target_metadata_two.csv",
        "target_guide_counts.csv",
        "target_raw_guide_counts.csv",
        "target_rna_barcodes.csv",
        "guide_effects.csv",
        "guide_effects_bad.csv",
        "nulls.csv",
        "nulls_bad.csv",
        "bad.json",
    )}
    files["expression.csv"].write_text(
        "cell_id,G1,G2\nAAAC-1,1,0\nAAAG-1,0,2\nAACC-1,3,1\n",
        encoding="utf-8",
    )
    files["fractional.csv"].write_text(
        "cell_id,G1\nAAAC-1,1.5\nAAAG-1,2.25\n", encoding="utf-8"
    )
    files["collision.csv"].write_text(
        "cell_id,G1\nAAAC-1,1\nAAAC-2,2\n", encoding="utf-8"
    )
    files["negative.csv"].write_text(
        "cell_id,G1\nAAAC-1,-1\n", encoding="utf-8"
    )
    files["design.csv"].write_text(
        "cell_id,condition,replicate,batch,state,donor\n"
        "a1,A,A1,B1,S1,D1\n"
        "a2,A,A2,B2,S1,D2\n"
        "a3,A,A3,B1,S2,D3\n"
        "b1,B,B1,B1,S1,D4\n"
        "b2,B,B2,B2,S2,D5\n"
        "b3,B,B3,B1,S2,D6\n",
        encoding="utf-8",
    )
    files["design_two.csv"].write_text(
        "cell_id,condition,replicate,batch\n"
        "a1,A,A1,B1\n"
        "a2,A,A2,B2\n"
        "b1,B,B1,B1\n"
        "b2,B,B2,B2\n",
        encoding="utf-8",
    )
    files["confounded.csv"].write_text(
        "cell_id,condition,replicate,batch\n"
        "a1,A,A1,BA\n"
        "a2,A,A2,BA\n"
        "b1,B,B1,BB\n"
        "b2,B,B2,BB\n",
        encoding="utf-8",
    )
    files["missing_design.csv"].write_text(
        "cell_id,condition\na1,A\nb1,B\n", encoding="utf-8"
    )
    guide_text = (
        "barcode,g1,g2\n"
        "AAAA-1,12,0\n"
        "AAAC-1,11,0\n"
        "AAAG-1,10,0\n"
        "AACA-1,0,13\n"
        "AACC-1,0,12\n"
        "AACG-1,0,11\n"
        "AAGA-1,9,9\n"
        "AAGC-1,0,0\n"
    )
    files["guide_counts.csv"].write_text(guide_text, encoding="utf-8")
    files["guide_counts_zero.csv"].write_text(
        "barcode,g1,g2\nAAAA-1,0,0\nAAAC-1,0,0\n", encoding="utf-8"
    )
    files["raw_guide_counts.csv"].write_text(
        guide_text + "TTTT-1,1,0\nTTTC-1,0,1\n", encoding="utf-8"
    )
    files["raw_guide_bad.csv"].write_text(
        "barcode,g1,g3\nAAAA-1,12,0\nTTTT-1,1,1\n", encoding="utf-8"
    )
    files["rna_barcodes.csv"].write_text(
        "barcode\n"
        "AAAA-1\nAAAC-1\nAAAG-1\nAACA-1\nAACC-1\nAACG-1\nAAGA-1\nAAGC-1\n",
        encoding="utf-8",
    )
    files["guide_map.csv"].write_text(
        "guide,target\ng1,T1\ng2,T2\n", encoding="utf-8"
    )
    files["guide_map_bad.csv"].write_text(
        "guide,target\ng1,T1\n", encoding="utf-8"
    )
    expression = ["cell_id,TG,S1,S2"]
    metadata = ["cell_id,perturbation_uid,guide,replicate,batch"]
    metadata_two = ["cell_id,perturbation_uid,guide,replicate,batch"]
    for replicate in range(1, 4):
        for index in range(10):
            target = f"t{replicate}_{index}"
            control = f"c{replicate}_{index}"
            expression.extend((f"{target},1,1,1", f"{control},5,0,0"))
            metadata.extend(
                (
                    f"{target},TARGET,g{(index % 3) + 1},r{replicate},b{replicate}",
                    f"{control},NTC,NTC,r{replicate},b{replicate}",
                )
            )
            if replicate <= 2:
                metadata_two.extend(
                    (
                        f"{target},TARGET,g{(index % 3) + 1},r{replicate},b{replicate}",
                        f"{control},NTC,NTC,r{replicate},b{replicate}",
                    )
                )
    files["target_expression.csv"].write_text(
        "\n".join(expression) + "\n", encoding="utf-8"
    )
    files["target_metadata.csv"].write_text(
        "\n".join(metadata) + "\n", encoding="utf-8"
    )
    files["target_metadata_two.csv"].write_text(
        "\n".join(metadata_two) + "\n", encoding="utf-8"
    )
    target_barcodes = [row.split(",", 1)[0] for row in metadata[1:]]
    target_guide_rows = ["barcode,g1,g2"] + [
        f"{cell},12,0" if index % 2 == 0 else f"{cell},0,12"
        for index, cell in enumerate(target_barcodes)
    ]
    files["target_guide_counts.csv"].write_text(
        "\n".join(target_guide_rows) + "\n", encoding="utf-8"
    )
    files["target_raw_guide_counts.csv"].write_text(
        "\n".join(target_guide_rows + ["empty_1,1,0", "empty_2,0,1"]) + "\n",
        encoding="utf-8",
    )
    files["target_rna_barcodes.csv"].write_text(
        "barcode\n" + "\n".join(target_barcodes) + "\n", encoding="utf-8"
    )
    files["guide_effects.csv"].write_text(
        "guide,target,effect\n"
        "g1,T1,-1.0\n"
        "g2,T1,-0.8\n"
        "g3,T2,0.5\n"
        "g4,T2,-0.5\n",
        encoding="utf-8",
    )
    files["guide_effects_bad.csv"].write_text(
        "guide,target\ng1,T1\n", encoding="utf-8"
    )
    files["nulls.csv"].write_text(
        "p_value\n0.2\n0.4\n0.6\n0.8\n0.9\n", encoding="utf-8"
    )
    files["nulls_bad.csv"].write_text(
        "p_value\n-1\nnan\n", encoding="utf-8"
    )
    files["bad.json"].write_text("{not-json", encoding="utf-8")
    return files


def _result_output(
    results: dict[str, dict[str, Any]],
    capability_id: str,
    name: str,
    workspace_root: Path,
) -> str:
    result = results.get(capability_id)
    if not result:
        raise ValueError(f"missing benchmark upstream: {capability_id}")
    for value in result.get("output_paths") or ():
        path = Path(str(value))
        if path.name == name:
            return str(path if path.is_absolute() else workspace_root / path)
    raise ValueError(f"{capability_id} did not publish {name}")


def _fixture_parameters(
    capability_id: str,
    scenario: str,
    files: dict[str, Path],
    results: dict[str, dict[str, Any]],
    workspace_root: Path,
    *,
    pipeline_root: str,
) -> dict[str, Any]:
    bad = scenario in {"blocked", "planted_failure"}
    target_pipeline = pipeline_root in {
        "target.guide_efficacy.v1",
        "effect.guide_target_sensitivity.v1",
    }
    if capability_id == "diagnostic.contract_integrity.v1":
        return {}
    if capability_id == "intake.materialize.v1":
        return {
            "input_path": str(files["expression.csv"]),
            "max_memory_gb": 1e-20 if bad else 4,
            "n_jobs": 1,
        }
    if capability_id == "diagnostic.dataset_integrity.v1":
        selected = (
            "collision.csv"
            if bad
            else "fractional.csv"
            if scenario == "caution_or_unresolved"
            else "expression.csv"
        )
        return {"input_path": str(files[selected])}
    if capability_id == "diagnostic.design_balance.v1":
        selected = (
            "missing_design.csv"
            if scenario == "planted_failure"
            else "confounded.csv"
            if scenario == "blocked"
            else "design_two.csv"
            if scenario == "caution_or_unresolved"
            else "design.csv"
        )
        return {"metadata_path": str(files[selected])}
    if capability_id == "guide.integrity.v1":
        guide_counts = "target_guide_counts.csv" if target_pipeline else "guide_counts.csv"
        rna_barcodes = "target_rna_barcodes.csv" if target_pipeline else "rna_barcodes.csv"
        return {
            "guide_counts_path": str(files[guide_counts]),
            "rna_barcodes_path": str(files[rna_barcodes]),
            "guide_map_path": str(
                files["guide_map_bad.csv"] if bad else files["guide_map.csv"]
            ),
        }
    if capability_id == "guide.assignment.nb_mixture.v1":
        guide_counts = "target_guide_counts.csv" if target_pipeline else "guide_counts.csv"
        return {
            "guide_counts_path": str(
                files["negative.csv"] if bad else files[guide_counts]
            ),
            "posterior_threshold": 0.90,
            "max_iterations": 200,
            "tolerance": 1e-6,
        }
    if capability_id == "guide.ambient.v1":
        filtered = "target_guide_counts.csv" if target_pipeline else "guide_counts.csv"
        raw = "target_raw_guide_counts.csv" if target_pipeline else "raw_guide_counts.csv"
        if scenario == "caution_or_unresolved":
            return {"filtered_guide_counts_path": str(files[filtered])}
        return {
            "raw_guide_counts_path": str(
                files["raw_guide_bad.csv"] if bad else files[raw]
            ),
            "filtered_guide_counts_path": str(files[filtered]),
        }
    if capability_id == "screen.moi_doublet.v1":
        return {
            "assignment_path": (
                str(files["bad.json"])
                if bad
                else _result_output(
                    results,
                    "guide.assignment.nb_mixture.v1",
                    "guide_assignments.json",
                    workspace_root,
                )
            )
        }
    if capability_id == "screen.retained_cells.v1":
        return {
            "assignment_path": (
                str(files["bad.json"])
                if bad
                else _result_output(
                    results,
                    "guide.assignment.nb_mixture.v1",
                    "guide_assignments.json",
                    workspace_root,
                )
            ),
            "moi_doublet_path": _result_output(
                results,
                "screen.moi_doublet.v1",
                "moi_doublet.json",
                workspace_root,
            ),
        }
    if capability_id == "target.guide_efficacy.v1":
        return {
            "expression_path": str(files["target_expression.csv"]),
            "metadata_path": str(
                files[
                    "target_metadata_two.csv"
                    if scenario == "caution_or_unresolved"
                    else "target_metadata.csv"
                ]
            ),
            "target_uid": "TARGET",
            "control_uid": "NTC",
            "target_gene": "MISSING" if bad else "TG",
            "expected_direction": "down",
            "bootstrap_iterations": 25,
            "guide_bootstrap_iterations": 15,
        }
    if capability_id == "effect.guide_target_sensitivity.v1":
        return {
            "effect_table_path": str(
                files["guide_effects_bad.csv"] if bad else files["guide_effects.csv"]
            )
        }
    if capability_id == "virtual.split.contract.v1":
        if scenario == "blocked":
            return {
                "axes": {
                    "perturbation": {
                        "train": ["P1", "P2"],
                        "validation": ["P3"],
                        "test": ["P2", "P4"],
                    }
                },
                "heldout_axes": ["perturbation"],
            }
        if scenario == "planted_failure":
            return {
                "axes": {
                    "context": {
                        "train": ["C1"],
                        "validation": [],
                        "test": ["C2"],
                    }
                },
                "heldout_axes": ["context"],
            }
        if scenario == "caution_or_unresolved":
            return {
                "axes": {
                    "perturbation": {
                        "train": ["P1", "P2"],
                        "validation": [],
                        "test": [],
                    }
                },
                "heldout_axes": [],
            }
        return {
            "axes": {
                "perturbation": {
                    "train": ["P1", "P2"],
                    "validation": ["P3"],
                    "test": ["P4", "P5"],
                },
                "context": {
                    "train": ["C1"],
                    "validation": [],
                    "test": ["C2"],
                },
            },
            "heldout_axes": ["perturbation", "context"],
        }
    if capability_id == "calibration.method_null.v1":
        return {
            "null_results_path": str(
                files["nulls_bad.csv"] if scenario == "planted_failure" else files["nulls.csv"]
            ),
            "permutation_unit": "cell" if scenario == "blocked" else "replicate_label",
        }
    return {}
