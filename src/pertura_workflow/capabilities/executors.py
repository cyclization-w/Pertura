from __future__ import annotations

from collections.abc import Callable
import json
import os
from pathlib import Path
import subprocess
from typing import Any

from pertura_core.hashing import canonical_hash
from pertura_workflow.capabilities.registry import capability_scientific_hash

from pertura_core import (
    AnalysisStatus,
    CapabilityRunRequest,
    CapabilitySpec,
    DatasetContract,
    DiagnosticStatus,
    ResultEnvelope,
    SourceClass,
    VirtualStatus,
)


Executor = Callable[[CapabilitySpec, CapabilityRunRequest, DatasetContract, Path], ResultEnvelope]
Validator = Callable[[CapabilitySpec, CapabilityRunRequest, DatasetContract, ResultEnvelope], None]


def _base_envelope(
    spec: CapabilitySpec,
    request: CapabilityRunRequest,
    *,
    status: DiagnosticStatus | AnalysisStatus | VirtualStatus,
    summary: str,
    blockers: tuple[str, ...] = (),
    cautions: tuple[str, ...] = (),
    metrics: dict[str, Any] | None = None,
    output_paths: tuple[str, ...] = (),
    output_hashes: dict[str, str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> ResultEnvelope:
    return ResultEnvelope(
        run_id=request.run_id,
        request_id=request.request_id,
        capability_id=spec.capability_id,
        capability_version=spec.version,
        capability_trust=spec.trust_level,
        contract_id=request.contract_id,
        contract_hash=request.contract_hash,
        scope=request.scope,
        status=status,
        result_kind=spec.output_kind,
        source_class=spec.source_class,
        summary=summary,
        blockers=blockers,
        cautions=cautions,
        metrics=metrics or {},
        output_paths=output_paths,
        output_hashes=output_hashes or {},
        dependencies=request.dependencies,
        metadata=metadata or {},
    )


def _contract_integrity(spec: CapabilitySpec, request: CapabilityRunRequest, contract: DatasetContract, staging: Path) -> ResultEnvelope:
    blockers = tuple(f"unresolved dataset field: {field}" for field in contract.unresolved_fields)
    if contract.contract_id != request.contract_id or contract.canonical_hash != request.contract_hash:
        blockers += ("request contract identity does not match authoritative contract",)
    status = DiagnosticStatus.blocked if blockers else DiagnosticStatus.screen_passed
    return _base_envelope(
        spec,
        request,
        status=status,
        summary="Dataset contract is internally consistent." if not blockers else "Dataset contract requires confirmation.",
        blockers=blockers,
        metrics={"unresolved_field_count": len(contract.unresolved_fields)},
    )


def _not_implemented(spec: CapabilitySpec, request: CapabilityRunRequest, contract: DatasetContract, staging: Path) -> ResultEnvelope:
    if spec.kind == "diagnostic":
        status: DiagnosticStatus | AnalysisStatus | VirtualStatus = DiagnosticStatus.unresolved
    elif spec.kind == "virtual":
        status = VirtualStatus.out_of_scope
    else:
        status = AnalysisStatus.blocked
    return _base_envelope(
        spec,
        request,
        status=status,
        summary=f"{spec.capability_id} is declared but not implemented in this build.",
        blockers=("capability_not_implemented",),
    )


def _validate_standard(spec: CapabilitySpec, request: CapabilityRunRequest, contract: DatasetContract, result: ResultEnvelope) -> None:
    if result.run_id != request.run_id or result.request_id != request.request_id:
        raise ValueError("executor returned result for a different request")
    if result.capability_id != spec.capability_id or result.capability_version != spec.version:
        raise ValueError("executor returned the wrong capability identity")
    if result.capability_trust != spec.trust_level:
        raise ValueError("executor returned the wrong capability trust")
    if result.contract_id != contract.contract_id or result.contract_hash != contract.canonical_hash:
        raise ValueError("executor returned the wrong contract identity")
    if result.scope.canonical_hash != request.scope.canonical_hash:
        raise ValueError("executor returned the wrong analysis scope")
    if result.result_kind != spec.output_kind:
        raise ValueError("executor returned the wrong result kind")
    if result.source_class != spec.source_class:
        raise ValueError("executor source class exceeds or differs from its capability spec")
    allowed_statuses = {
        "diagnostic": {item.value for item in DiagnosticStatus},
        "analysis": {item.value for item in AnalysisStatus},
        "virtual": {item.value for item in VirtualStatus},
        "report": {item.value for item in AnalysisStatus},
    }[spec.kind]
    status = str(getattr(result.status, "value", result.status))
    if status not in allowed_statuses:
        raise ValueError("executor returned a status outside the capability kind")
    if tuple(item.canonical_hash for item in result.dependencies) != tuple(
        item.canonical_hash for item in request.dependencies
    ):
        raise ValueError("executor changed or omitted authoritative dependencies")
    if set(result.output_paths) != set(result.output_hashes):
        raise ValueError("executor output paths and hashes do not match")
    enforce_consumption = bool(result.metadata.get("dependency_consumption_enforced"))
    successful = status in {
        "screen_passed", "caution", "completed", "completed_with_caution",
        "supported", "limited",
    }
    if not enforce_consumption or not successful:
        return
    policy = dict(spec.metadata.get("dependency_policy") or {})
    consumed = set(result.metadata.get("consumed_dependency_hashes") or ())
    records = tuple(result.metadata.get("dependency_consumption_records") or ())
    direct_dependencies = {
        item.role: item
        for item in request.dependencies
        if item.role in policy
    }
    for dependency, rules in policy.items():
        usage = str(rules.get("usage") or "")
        if usage not in {"scientific_input", "row_filter", "parameter_source"}:
            continue
        binding = direct_dependencies.get(dependency)
        if binding is None:
            raise ValueError(f"required consumed dependency is missing: {dependency}")
        if binding.object_hash not in consumed:
            raise ValueError(
                f"executor did not consume declared {usage} dependency: {dependency}; expected {binding.object_hash}; observed {sorted(consumed)}"
            )
        matching = [
            item
            for item in records
            if item.get("dependency_result_hash") == binding.object_hash
            and item.get("usage") == usage
            and str(item.get("dependency_artifact_hash") or "").startswith("sha256:")
        ]
        if not matching:
            raise ValueError(
                f"executor did not record artifact-level {usage} consumption: {dependency}"
            )
        if usage == "row_filter" and any(
            item.get("rows_consumed") is None
            or int(item.get("rows_consumed") or 0) <= 0
            or item.get("rows_available") is None
            for item in matching
        ):
            raise ValueError(
                f"row-filter dependency lacks nonzero before/after counts: {dependency}"
            )


def _lazy_executor(target: str) -> Executor:
    module_name, attribute = target.rsplit(":", 1)

    def invoke(spec, request, contract, staging):
        module = __import__(module_name, fromlist=[attribute])
        return getattr(module, attribute)(spec, request, contract, staging)

    invoke.__name__ = f"lazy_{attribute}"
    return invoke


_EXECUTOR_TARGETS = {
    "guide_assignment_qc": "pertura_workflow.capabilities.guide_assignment:run_guide_assignment_qc",
    "edger_pseudobulk": "pertura_workflow.capabilities.edger:run_edger_pseudobulk",
    "target_reliability_v2": "pertura_workflow.capabilities.target_reliability:run_target_reliability_v2",
    "state_reference": "pertura_workflow.capabilities.state_reference:run_state_reference",
    "gmt_import": "pertura_workflow.capabilities.modules:run_gmt_import",
    "nmf_modules": "pertura_workflow.capabilities.modules:run_nmf_modules",
    "replicate_null_calibration": "pertura_workflow.capabilities.calibration:run_replicate_null_calibration",
    "intake_materialize": "pertura_workflow.capabilities.intake_candidates:run_intake_materialize",
    "dataset_integrity": "pertura_workflow.capabilities.intake_candidates:run_dataset_integrity",
    "design_balance": "pertura_workflow.capabilities.intake_candidates:run_design_balance",
    "guide_integrity": "pertura_workflow.capabilities.guide_candidates:run_guide_integrity",
    "guide_nb_mixture": "pertura_workflow.capabilities.guide_candidates:run_guide_nb_mixture",
    "guide_ambient": "pertura_workflow.capabilities.guide_candidates:run_guide_ambient",
    "moi_doublet": "pertura_workflow.capabilities.guide_candidates:run_moi_doublet",
    "retained_cells": "pertura_workflow.capabilities.guide_candidates:run_retained_cells",
    "state_reference_fit": "pertura_workflow.capabilities.state_candidates:run_state_reference_fit",
    "state_reference_map": "pertura_workflow.capabilities.state_candidates:run_state_reference_map",
    "state_annotation_candidates": "pertura_workflow.capabilities.state_candidates:run_state_annotation_candidates",
    "control_nmf": "pertura_workflow.capabilities.state_candidates:run_control_nmf",
    "mixscape_responder": "pertura_workflow.capabilities.target_candidates:run_mixscape_responder",
    "guide_efficacy": "pertura_workflow.capabilities.target_candidates:run_guide_efficacy",
    "target_reliability_aggregate": "pertura_workflow.capabilities.target_candidates:run_target_reliability_aggregate",
    "sceptre_association": "pertura_workflow.capabilities.effect_candidates:run_sceptre_association",
    "propeller_composition": "pertura_workflow.capabilities.effect_candidates:run_propeller_composition",
    "guide_target_sensitivity": "pertura_workflow.capabilities.effect_candidates:run_guide_target_sensitivity",
    "module_global_effect": "pertura_workflow.capabilities.effect_candidates:run_module_global_effect",
    "method_null_calibration": "pertura_workflow.capabilities.effect_candidates:run_method_null_calibration",
    "effect_matrix_assemble": "pertura_workflow.capabilities.p4_candidates:run_effect_matrix_assemble",
    "response_signed_nmf": "pertura_workflow.capabilities.p4_candidates:run_response_signed_nmf",
    "perturbation_cluster": "pertura_workflow.capabilities.p4_candidates:run_perturbation_cluster",
    "enrichment_ora": "pertura_workflow.capabilities.p4_candidates:run_enrichment_ora",
    "enrichment_gsea_prerank": "pertura_workflow.capabilities.p4_candidates:run_enrichment_gsea_prerank",
    "regulator_activity_ulm": "pertura_workflow.capabilities.p4_candidates:run_regulator_activity_ulm",
    "perturbation_regulator_network": "pertura_workflow.capabilities.p4_candidates:run_perturbation_regulator_network",
    "literature_europepmc": "pertura_workflow.capabilities.p4_candidates:run_literature_europepmc",
    "interpretation_evidence_map": "pertura_workflow.capabilities.p4_candidates:run_interpretation_evidence_map",
    "virtual_split_contract": "pertura_workflow.capabilities.p5_candidates:run_virtual_split_contract",
    "virtual_prediction_ingest": "pertura_workflow.capabilities.p5_candidates:run_virtual_prediction_ingest",
    "virtual_leakage_audit": "pertura_workflow.capabilities.p5_candidates:run_virtual_leakage_audit",
    "virtual_baselines": "pertura_workflow.capabilities.p5_candidates:run_virtual_baselines",
    "virtual_evaluate_comprehensive": "pertura_workflow.capabilities.p5_candidates:run_virtual_evaluate_comprehensive",
    "design_next_panel": "pertura_workflow.capabilities.p5_candidates:run_design_next_panel",
}

_EXECUTORS: dict[str, Executor] = {
    "contract_integrity": _contract_integrity,
    **{name: _lazy_executor(target) for name, target in _EXECUTOR_TARGETS.items()},
    "not_implemented": _not_implemented,

}
def _validate_candidate(
    spec: CapabilitySpec,
    request: CapabilityRunRequest,
    contract: DatasetContract,
    result: ResultEnvelope,
) -> None:
    _validate_standard(spec, request, contract, result)
    if spec.trust_level.value != "exploratory" or spec.claim_permissions:
        raise ValueError("candidate validator only accepts non-authoritative exploratory specs")
    if result.capability_trust.value != "exploratory":
        raise ValueError("candidate result cannot claim bundled trust")
    if result.receipt_id is not None:
        raise ValueError("candidate executor cannot attach a receipt")
    if result.metadata.get("validation_status") != "synthetic_only":
        raise ValueError("candidate result must expose synthetic-only validation status")


_VALIDATORS: dict[str, Validator] = {
    "standard": _validate_standard,
    "candidate_standard": _validate_candidate,
}


def has_executor(name: str) -> bool:
    return name in _EXECUTORS


def has_validator(name: str) -> bool:
    return name in _VALIDATORS


def _profile_failure(
    spec: CapabilitySpec,
    request: CapabilityRunRequest,
    message: str,
) -> ResultEnvelope:
    if spec.kind == "diagnostic":
        status: DiagnosticStatus | AnalysisStatus | VirtualStatus = DiagnosticStatus.blocked
    elif spec.kind == "virtual":
        status = VirtualStatus.out_of_scope
    else:
        status = AnalysisStatus.blocked
    return _base_envelope(
        spec,
        request,
        status=status,
        summary=f"{spec.capability_id} could not execute in its locked environment.",
        blockers=(message,),
        metadata={
            "validation_status": str(spec.metadata.get("validation_status") or ""),
            "candidate": spec.trust_level.value == "exploratory",
            "environment_execution": "isolated_profile_failed",
        },
    )


def _execute_in_profile(
    spec: CapabilitySpec,
    request: CapabilityRunRequest,
    contract: DatasetContract,
    staging: Path,
    runtime_context: dict[str, Any],
) -> ResultEnvelope:
    from pertura_workflow.environment import environment_prefix, micromamba_path

    profile = str(spec.metadata.get("environment_profile") or "")
    binary, prefix = micromamba_path(), environment_prefix(profile)
    if not binary.is_file() or not prefix.is_dir():
        return _profile_failure(spec, request, f"required environment is unavailable: {profile}")
    runner = Path(__file__).with_name("runners") / "environment_worker.py"
    result_path = staging / "_environment_result.json"
    config_path = staging / "_environment_request.json"
    config = {
        "schema_version": "pertura-environment-worker-v1",
        "spec": spec.model_dump(mode="json"),
        "request": request.model_dump(mode="json"),
        "contract": contract.model_dump(mode="json"),
        "staging_dir": str(staging.resolve()),
        "result_path": str(result_path.resolve()),
        "runtime_context": {
            key: value
            for key, value in runtime_context.items()
            if key != "consumed_dependency_hashes"
        },
    }
    config_path.write_text(json.dumps(config, sort_keys=True), encoding="utf-8")
    env = {
        key: os.environ[key]
        for key in ("SYSTEMROOT", "WINDIR", "TEMP", "TMP", "HOME", "USERPROFILE", "PATH")
        if key in os.environ
    }
    source_root = str(Path(__file__).resolve().parents[2])
    env["PYTHONPATH"] = source_root
    try:
        completed = subprocess.run(
            [
                str(binary), "run", "--prefix", str(prefix),
                "python", str(runner), str(config_path),
            ],
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=spec.timeout_seconds,
            check=False,
            env=env,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return _profile_failure(spec, request, f"{profile} worker failed: {exc}")
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout)[-2000:]
        return _profile_failure(spec, request, f"{profile} worker failed: {detail}")
    if not result_path.is_file():
        return _profile_failure(spec, request, f"{profile} worker returned no result")
    try:
        result = ResultEnvelope.model_validate_json(result_path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        return _profile_failure(spec, request, f"{profile} worker result is invalid: {exc}")
    metadata = dict(result.metadata)
    metadata["environment_execution"] = "isolated_profile"
    metadata["environment_profile"] = profile
    return result.model_copy(update={"metadata": metadata})


def execute_capability(
    spec: CapabilitySpec,
    request: CapabilityRunRequest,
    contract: DatasetContract,
    staging_dir: str | Path,
    *,
    runtime_context: dict[str, Any] | None = None,
) -> ResultEnvelope:
    from pertura_workflow.capabilities.execution_context import bind_execution_context

    if not spec.implemented:
        executor = _not_implemented
    else:
        executor = _EXECUTORS[spec.executor]
    context = dict(runtime_context or {})
    isolated = spec.metadata.get("execution_mode") == "isolated_python"
    inside_worker = bool(context.get("inside_environment_worker"))
    enforce_isolation = bool(context.get("enforce_environment_execution"))
    context.setdefault("consumed_dependency_hashes", set())
    context.setdefault("dependency_consumption_records", [])
    context.setdefault("consumer_capability_id", spec.capability_id)
    with bind_execution_context(context):
        if isolated and enforce_isolation and not inside_worker:
            result = _execute_in_profile(
                spec, request, contract, Path(staging_dir), context,
            )
        else:
            result = executor(spec, request, contract, Path(staging_dir))
            if isolated:
                metadata = dict(result.metadata)
                metadata["environment_execution"] = (
                    "isolated_profile" if inside_worker else "in_process_test_only"
                )
                metadata["environment_profile"] = str(
                    spec.metadata.get("environment_profile") or ""
                )
                result = result.model_copy(update={"metadata": metadata})
        from pertura_workflow.capabilities.execution_context import (
            consumed_dependency_hashes,
            dependency_consumption_records,
        )
        context_consumed = consumed_dependency_hashes()
        context_records = dependency_consumption_records()
    metadata = dict(result.metadata)
    existing_consumed = set(metadata.get("consumed_dependency_hashes") or ())
    existing_consumed.update(context_consumed)
    existing_records = list(metadata.get("dependency_consumption_records") or ())
    for record in context_records:
        if record not in existing_records:
            existing_records.append(record)
    metadata.update({
        "capability_spec_hash": capability_scientific_hash(spec),
        "dependency_consumption_enforced": bool(
            context.get("enforce_dependency_consumption")
        ),
        "dependency_policy_hash": canonical_hash(
            dict(spec.metadata.get("dependency_policy") or {})
        ),
        "consumed_dependency_hashes": sorted(existing_consumed),
        "dependency_consumption_records": existing_records,
    })
    payload = result.model_dump(mode="json")
    payload["canonical_hash"] = ""
    payload["metadata"] = metadata
    result = ResultEnvelope.model_validate(payload)
    _VALIDATORS[spec.validator](spec, request, contract, result)
    return result
