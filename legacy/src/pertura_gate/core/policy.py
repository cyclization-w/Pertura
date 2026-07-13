from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any


PolicyProfile = str


@dataclass(frozen=True)
class GatePolicy:
    """Deterministic PerturaGate policy used for claim decisions."""

    profile: PolicyProfile = "smoke"
    version: str = "pertura-gate-v1"
    resolver_version: str = "pertura-gate-resolver-v1"
    validated_mechanism_enabled: bool = False
    minimum_measured_n: int = 1
    require_measured_eligibility_for_claims: bool = True
    require_cell_qc_for_measured_claims: bool = False
    minimum_qc_cells: int | None = None
    cell_qc_fail_blocks_measured: bool = True
    required_measured_fields: tuple[str, ...] = (
        "contrast_left",
        "contrast_baseline",
        "n_left",
        "n_baseline",
        "method",
        "multiple_testing",
        "has_padj",
    )
    guide_based_modalities: tuple[str, ...] = (
        "guide_based_perturb_seq",
        "crispr",
        "crispri",
        "crispra",
        "crispr_ko",
        "guide_capture",
    )
    chemical_modalities: tuple[str, ...] = (
        "chemical",
        "drug",
        "compound",
        "treatment",
    )
    high_moi_values: tuple[str, ...] = ("high", "multi", "multiple", "pooled_high")
    allowed_high_moi_estimands: tuple[str, ...] = (
        "single_target_conditional",
        "combinatorial",
        "guide_set_effect",
    )
    replication_min_artifacts: int = 2
    allowed_replication_types: tuple[str, ...] = (
        "independent_dataset_replication",
        "biological_replicate_replication",
        "donor_replication",
    )
    upgrade_guide_consistency_to_replication: bool = False
    require_trusted_method_for_measured_claims: bool = False
    trusted_runner_methods: tuple[str, ...] = (
        "sceptre",
        "wilcoxon",
        "scanpy_wilcoxon",
        "mixscape",
        "milo",
        "sccoda",
        "fisher_exact",
        "chi_square",
        "permutation_test",
        "logistic_regression",
    )
    trusted_runner_requires_execution_hash: bool = True
    trusted_runner_requires_ledger_entry: bool = False
    trusted_calibration_methods: tuple[str, ...] = (
        "basic_control_calibration_v1",
        "basic_ntc_vs_ntc_v1",
        "basic_label_permutation_null_v1",
    )
    trusted_calibration_requires_execution_hash: bool = True
    require_trusted_calibration_for_required_checks: bool = False
    require_replicate_scope_for_measured_claims: bool = False
    allow_method_internal_replicate_handling: bool = True
    batch_confounding_fail_blocks_measured: bool = False
    require_control_calibration_for_measured_claims: bool = False
    control_calibration_fail_blocks_measured: bool = True
    require_ntc_vs_ntc_check_for_measured_claims: bool = False
    require_label_permutation_check_for_measured_claims: bool = False
    minimum_guides_per_target: int | None = None
    minimum_cells_per_guide: int | None = None
    guide_consistency_fail_blocks_measured: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_canonical_dict(self) -> dict[str, Any]:
        return {
            "profile": self.profile,
            "version": self.version,
            "resolver_version": self.resolver_version,
            "validated_mechanism_enabled": self.validated_mechanism_enabled,
            "minimum_measured_n": self.minimum_measured_n,
            "require_measured_eligibility_for_claims": self.require_measured_eligibility_for_claims,
            "require_cell_qc_for_measured_claims": self.require_cell_qc_for_measured_claims,
            "minimum_qc_cells": self.minimum_qc_cells,
            "cell_qc_fail_blocks_measured": self.cell_qc_fail_blocks_measured,
            "required_measured_fields": list(self.required_measured_fields),
            "guide_based_modalities": list(self.guide_based_modalities),
            "chemical_modalities": list(self.chemical_modalities),
            "high_moi_values": list(self.high_moi_values),
            "allowed_high_moi_estimands": list(self.allowed_high_moi_estimands),
            "replication_min_artifacts": self.replication_min_artifacts,
            "allowed_replication_types": list(self.allowed_replication_types),
            "upgrade_guide_consistency_to_replication": self.upgrade_guide_consistency_to_replication,
            "require_trusted_method_for_measured_claims": self.require_trusted_method_for_measured_claims,
            "trusted_runner_methods": list(self.trusted_runner_methods),
            "trusted_runner_requires_execution_hash": self.trusted_runner_requires_execution_hash,
            "trusted_runner_requires_ledger_entry": self.trusted_runner_requires_ledger_entry,
            "trusted_calibration_methods": list(self.trusted_calibration_methods),
            "trusted_calibration_requires_execution_hash": self.trusted_calibration_requires_execution_hash,
            "require_trusted_calibration_for_required_checks": self.require_trusted_calibration_for_required_checks,
            "require_replicate_scope_for_measured_claims": self.require_replicate_scope_for_measured_claims,
            "allow_method_internal_replicate_handling": self.allow_method_internal_replicate_handling,
            "batch_confounding_fail_blocks_measured": self.batch_confounding_fail_blocks_measured,
            "require_control_calibration_for_measured_claims": self.require_control_calibration_for_measured_claims,
            "control_calibration_fail_blocks_measured": self.control_calibration_fail_blocks_measured,
            "require_ntc_vs_ntc_check_for_measured_claims": self.require_ntc_vs_ntc_check_for_measured_claims,
            "require_label_permutation_check_for_measured_claims": self.require_label_permutation_check_for_measured_claims,
            "minimum_guides_per_target": self.minimum_guides_per_target,
            "minimum_cells_per_guide": self.minimum_cells_per_guide,
            "guide_consistency_fail_blocks_measured": self.guide_consistency_fail_blocks_measured,
            "metadata": _canonicalize(self.metadata),
        }

    @property
    def policy_hash(self) -> str:
        payload = json.dumps(self.to_canonical_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def policy_for_profile(profile: PolicyProfile) -> GatePolicy:
    normalized = str(profile).strip().lower().replace("-", "_")
    if normalized == "smoke":
        return GatePolicy(profile="smoke")
    if normalized == "strict":
        return GatePolicy(
            profile="strict",
            require_trusted_method_for_measured_claims=True,
            require_replicate_scope_for_measured_claims=True,
            trusted_runner_requires_ledger_entry=True,
            batch_confounding_fail_blocks_measured=True,
            require_control_calibration_for_measured_claims=False,
            control_calibration_fail_blocks_measured=True,
            guide_consistency_fail_blocks_measured=True,
        )
    if normalized == "paper":
        return GatePolicy(
            profile="paper",
            minimum_measured_n=20,
            require_cell_qc_for_measured_claims=True,
            minimum_qc_cells=20,
            require_trusted_method_for_measured_claims=True,
            require_replicate_scope_for_measured_claims=True,
            trusted_runner_requires_ledger_entry=True,
            batch_confounding_fail_blocks_measured=True,
            require_control_calibration_for_measured_claims=True,
            control_calibration_fail_blocks_measured=True,
            require_ntc_vs_ntc_check_for_measured_claims=True,
            require_label_permutation_check_for_measured_claims=True,
            require_trusted_calibration_for_required_checks=True,
            minimum_guides_per_target=2,
            minimum_cells_per_guide=10,
            guide_consistency_fail_blocks_measured=True,
        )
    raise ValueError(f"unknown policy profile: {profile}")


DEFAULT_POLICY = policy_for_profile("smoke")


def _canonicalize(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _canonicalize(value[key]) for key in sorted(value)}
    if isinstance(value, (list, tuple)):
        return [_canonicalize(item) for item in value]
    return value

