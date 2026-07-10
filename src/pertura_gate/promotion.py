from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Iterable

from pertura_core import (
    AnalysisStatus,
    CapabilitySpec,
    CapabilityTrust,
    PromotionDecision,
    ResultEnvelope,
    RunReceipt,
    ScientificStatement,
    SourceClass,
    scope_can_support,
    verify_receipt,
)
from pertura_core.hashing import canonical_hash


@dataclass(frozen=True)
class PromotionPolicy:
    profile: str = "strict"
    version: str = "pertura-promotion-v2"
    minimum_independent_units_per_arm: int = 3
    require_current_dependencies: bool = True
    require_receipt_for_measured: bool = True
    required_measured_dependency_kinds: tuple[str, ...] = (
        "contract",
        "retained_cell_manifest",
        "target_reliability",
    )
    require_calibration: bool = True

    @property
    def policy_hash(self) -> str:
        return canonical_hash(asdict(self))


_NON_MEASURED_CEILINGS = {
    SourceClass.observed_metadata: "observation",
    SourceClass.prediction: "prediction",
    SourceClass.curated_prior: "curated_prior",
    SourceClass.hypothesis: "hypothesis",
}


def decide_promotion(
    statement: ScientificStatement,
    *,
    results: Iterable[ResultEnvelope],
    receipts: Iterable[RunReceipt],
    capability_specs: Iterable[CapabilitySpec],
    authoritative_public_key: str,
    policy: PromotionPolicy = PromotionPolicy(),
) -> PromotionDecision:
    """Promote from structured result state only; statement text is never parsed."""

    result_by_id = {item.result_id: item for item in results}
    receipt_by_result = {item.result_id: item for item in receipts}
    spec_by_identity = {(item.capability_id, item.version): item for item in capability_specs}
    selected: list[ResultEnvelope] = []
    reasons: list[str] = []

    if not statement.result_ids:
        reasons.append("statement has no explicit committed result dependencies")
    for result_id in statement.result_ids:
        result = result_by_id.get(result_id)
        if result is None:
            reasons.append(f"committed result is missing: {result_id}")
        else:
            selected.append(result)

    if statement.source_class != SourceClass.measured_result:
        ceiling = _NON_MEASURED_CEILINGS.get(statement.source_class, "hypothesis")
        status = "promoted" if not reasons and statement.requested_strength == ceiling else "downgraded"
        return _decision(statement, status, ceiling, selected, (), reasons, policy)

    receipt_ids: list[str] = []
    for result in selected:
        spec = spec_by_identity.get((result.capability_id, result.capability_version))
        if spec is None:
            reasons.append(f"capability spec is missing for {result.capability_id}@{result.capability_version}")
            continue
        if spec.trust_level != CapabilityTrust.builtin_trusted or result.capability_trust != CapabilityTrust.builtin_trusted:
            reasons.append(f"capability is not bundled trusted: {result.capability_id}")
        if statement.requested_strength not in spec.claim_permissions:
            reasons.append(f"capability does not permit {statement.requested_strength}: {result.capability_id}")
        if result.source_class != SourceClass.measured_result:
            reasons.append(f"result source class is not measured_result: {result.result_id}")
        if result.stale:
            reasons.append(f"result is stale: {result.result_id}")
        if result.blockers:
            reasons.append(f"result has blockers: {result.result_id}")
        if result.status != AnalysisStatus.completed:
            reasons.append(f"result status is not strict-complete: {result.status.value}")
        if not scope_can_support(statement.scope, result.scope):
            reasons.append(f"result scope is not exact or explicitly compatible: {result.result_id}")
        if policy.require_current_dependencies:
            for dependency in result.dependencies:
                if dependency.required and dependency.state != "current":
                    reasons.append(f"required dependency is {dependency.state}: {dependency.object_id}")
        available_kinds = {item.kind for item in result.dependencies if item.required and item.state == "current"}
        for kind in policy.required_measured_dependency_kinds:
            if kind not in available_kinds:
                reasons.append(f"required measured dependency is missing: {kind}")
        if policy.require_calibration and "calibration" not in available_kinds:
            reasons.append("required measured dependency is missing: calibration")

        independent = result.metrics.get("n_paired_units")
        if independent is None:
            independent = result.metrics.get("n_independent_units_per_arm")
        try:
            independent_count = int(independent)
        except (TypeError, ValueError):
            independent_count = 0
        if independent_count < policy.minimum_independent_units_per_arm:
            reasons.append(
                f"independent units {independent_count} are below strict minimum {policy.minimum_independent_units_per_arm}"
            )

        receipt = receipt_by_result.get(result.result_id)
        if policy.require_receipt_for_measured:
            if receipt is None:
                reasons.append(f"trusted receipt is missing: {result.result_id}")
            elif not verify_receipt(
                receipt,
                authoritative_public_key=authoritative_public_key,
                expected_result=result,
                expected_policy_hash=policy.policy_hash,
            ):
                reasons.append(f"trusted receipt is invalid: {result.result_id}")
            else:
                receipt_ids.append(receipt.receipt_id)

    status = "promoted" if selected and not reasons else ("blocked" if not selected else "downgraded")
    ceiling = statement.requested_strength if status == "promoted" else "observation"
    return _decision(statement, status, ceiling, selected, tuple(receipt_ids), reasons, policy)


def _decision(
    statement: ScientificStatement,
    status: str,
    ceiling: str,
    results: Iterable[ResultEnvelope],
    receipt_ids: tuple[str, ...],
    reasons: Iterable[str],
    policy: PromotionPolicy,
) -> PromotionDecision:
    selected = list(results)
    return PromotionDecision(
        run_id=statement.run_id,
        statement_id=statement.statement_id,
        status=status,
        max_strength=ceiling,
        source_class=statement.source_class,
        result_ids=tuple(item.result_id for item in selected),
        receipt_ids=receipt_ids,
        dependency_ids=tuple(
            dependency.dependency_id for result in selected for dependency in result.dependencies
        ),
        reasons=tuple(dict.fromkeys(reasons)),
        limitations=statement.limitations,
        policy_hash=policy.policy_hash,
    )
