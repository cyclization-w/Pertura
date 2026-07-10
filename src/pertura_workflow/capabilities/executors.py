from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

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
    if result.request_id != request.request_id:
        raise ValueError("executor returned result for a different request")
    if result.capability_id != spec.capability_id or result.capability_version != spec.version:
        raise ValueError("executor returned the wrong capability identity")
    if result.contract_id != contract.contract_id or result.contract_hash != contract.canonical_hash:
        raise ValueError("executor returned the wrong contract identity")
    if result.source_class != spec.source_class:
        raise ValueError("executor source class exceeds or differs from its capability spec")


_EXECUTORS: dict[str, Executor] = {
    "contract_integrity": _contract_integrity,
    "guide_assignment_qc": __import__("pertura_workflow.capabilities.guide_assignment", fromlist=["run_guide_assignment_qc"]).run_guide_assignment_qc,
    "edger_pseudobulk": __import__("pertura_workflow.capabilities.edger", fromlist=["run_edger_pseudobulk"]).run_edger_pseudobulk,
    "target_reliability_v2": __import__("pertura_workflow.capabilities.target_reliability", fromlist=["run_target_reliability_v2"]).run_target_reliability_v2,
    "state_reference": __import__("pertura_workflow.capabilities.state_reference", fromlist=["run_state_reference"]).run_state_reference,
    "gmt_import": __import__("pertura_workflow.capabilities.modules", fromlist=["run_gmt_import"]).run_gmt_import,
    "nmf_modules": __import__("pertura_workflow.capabilities.modules", fromlist=["run_nmf_modules"]).run_nmf_modules,
    "replicate_null_calibration": __import__("pertura_workflow.capabilities.calibration", fromlist=["run_replicate_null_calibration"]).run_replicate_null_calibration,
    "intake_materialize": __import__("pertura_workflow.capabilities.intake_candidates", fromlist=["run_intake_materialize"]).run_intake_materialize,
    "dataset_integrity": __import__("pertura_workflow.capabilities.intake_candidates", fromlist=["run_dataset_integrity"]).run_dataset_integrity,
    "design_balance": __import__("pertura_workflow.capabilities.intake_candidates", fromlist=["run_design_balance"]).run_design_balance,
    "guide_integrity": __import__("pertura_workflow.capabilities.guide_candidates", fromlist=["run_guide_integrity"]).run_guide_integrity,
    "guide_nb_mixture": __import__("pertura_workflow.capabilities.guide_candidates", fromlist=["run_guide_nb_mixture"]).run_guide_nb_mixture,
    "guide_ambient": __import__("pertura_workflow.capabilities.guide_candidates", fromlist=["run_guide_ambient"]).run_guide_ambient,
    "moi_doublet": __import__("pertura_workflow.capabilities.guide_candidates", fromlist=["run_moi_doublet"]).run_moi_doublet,
    "retained_cells": __import__("pertura_workflow.capabilities.guide_candidates", fromlist=["run_retained_cells"]).run_retained_cells,
    "state_reference_fit": __import__("pertura_workflow.capabilities.state_candidates", fromlist=["run_state_reference_fit"]).run_state_reference_fit,
    "state_reference_map": __import__("pertura_workflow.capabilities.state_candidates", fromlist=["run_state_reference_map"]).run_state_reference_map,
    "state_annotation_candidates": __import__("pertura_workflow.capabilities.state_candidates", fromlist=["run_state_annotation_candidates"]).run_state_annotation_candidates,
    "control_nmf": __import__("pertura_workflow.capabilities.state_candidates", fromlist=["run_control_nmf"]).run_control_nmf,
    "mixscape_responder": __import__("pertura_workflow.capabilities.target_candidates", fromlist=["run_mixscape_responder"]).run_mixscape_responder,
    "guide_efficacy": __import__("pertura_workflow.capabilities.target_candidates", fromlist=["run_guide_efficacy"]).run_guide_efficacy,
    "target_reliability_aggregate": __import__("pertura_workflow.capabilities.target_candidates", fromlist=["run_target_reliability_aggregate"]).run_target_reliability_aggregate,
    "sceptre_association": __import__("pertura_workflow.capabilities.effect_candidates", fromlist=["run_sceptre_association"]).run_sceptre_association,
    "propeller_composition": __import__("pertura_workflow.capabilities.effect_candidates", fromlist=["run_propeller_composition"]).run_propeller_composition,
    "guide_target_sensitivity": __import__("pertura_workflow.capabilities.effect_candidates", fromlist=["run_guide_target_sensitivity"]).run_guide_target_sensitivity,
    "module_global_effect": __import__("pertura_workflow.capabilities.effect_candidates", fromlist=["run_module_global_effect"]).run_module_global_effect,
    "method_null_calibration": __import__("pertura_workflow.capabilities.effect_candidates", fromlist=["run_method_null_calibration"]).run_method_null_calibration,    "not_implemented": _not_implemented,
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


def execute_capability(
    spec: CapabilitySpec,
    request: CapabilityRunRequest,
    contract: DatasetContract,
    staging_dir: str | Path,
) -> ResultEnvelope:
    if not spec.implemented:
        executor = _not_implemented
    else:
        executor = _EXECUTORS[spec.executor]
    result = executor(spec, request, contract, Path(staging_dir))
    _VALIDATORS[spec.validator](spec, request, contract, result)
    return result
