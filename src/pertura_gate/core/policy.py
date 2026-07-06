from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class GatePolicy:
    """Deterministic PerturaGate policy used for claim decisions."""

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
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_canonical_dict(self) -> dict[str, Any]:
        return {
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
            "metadata": _canonicalize(self.metadata),
        }

    @property
    def policy_hash(self) -> str:
        payload = json.dumps(self.to_canonical_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


DEFAULT_POLICY = GatePolicy()


def _canonicalize(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _canonicalize(value[key]) for key in sorted(value)}
    if isinstance(value, (list, tuple)):
        return [_canonicalize(item) for item in value]
    return value

