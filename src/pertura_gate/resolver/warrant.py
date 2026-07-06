from __future__ import annotations

from pertura_gate.core.schema import ResolvedStrength, StrengthCeiling

STRENGTH_RANK: dict[StrengthCeiling, int] = {
    StrengthCeiling.unsupported: 0,
    StrengthCeiling.observation: 1,
    StrengthCeiling.curated_prior_support: 2,
    StrengthCeiling.predicted_effect: 2,
    StrengthCeiling.measured_target_engagement: 3,
    StrengthCeiling.measured_association: 4,
    StrengthCeiling.replicated_measured_association: 5,
    StrengthCeiling.validated_mechanism_disabled: 6,
}


def strength_rank(strength: StrengthCeiling) -> int:
    return STRENGTH_RANK[strength]


def best_resolution(candidates: list[ResolvedStrength]) -> ResolvedStrength:
    return max(candidates, key=lambda item: strength_rank(item.ceiling))