from __future__ import annotations

from enum import Enum

from pertura_core.models import ScopeKey


class ScopeComparison(str, Enum):
    exact = "exact"
    compatible_by_declared_rule = "compatible_by_declared_rule"
    broader = "broader"
    narrower = "narrower"
    mismatch = "mismatch"
    unresolved = "unresolved"


_SET_AXES = (
    "perturbation_ids",
    "control_ids",
    "state_ids",
    "donor_ids",
    "replicate_ids",
    "batch_ids",
)
_SCALAR_AXES = ("dose", "timepoint", "contrast_id", "estimand")


def compare_scope_keys(required: ScopeKey, candidate: ScopeKey) -> ScopeComparison:
    """Compare a candidate evidence scope against a required statement scope."""

    if required.unresolved_fields or candidate.unresolved_fields:
        return ScopeComparison.unresolved
    if not required.dataset_id or not candidate.dataset_id:
        return ScopeComparison.unresolved
    if required.dataset_id != candidate.dataset_id:
        return ScopeComparison.mismatch
    if required.canonical_hash == candidate.canonical_hash:
        return ScopeComparison.exact

    directions: set[ScopeComparison] = set()
    for axis in _SET_AXES:
        expected = set(getattr(required, axis))
        observed = set(getattr(candidate, axis))
        if expected == observed:
            continue
        if not observed and expected:
            directions.add(ScopeComparison.broader)
        elif not expected and observed:
            directions.add(ScopeComparison.narrower)
        elif observed.issuperset(expected):
            directions.add(ScopeComparison.broader)
        elif observed.issubset(expected):
            directions.add(ScopeComparison.narrower)
        else:
            return ScopeComparison.mismatch

    for axis in _SCALAR_AXES:
        expected = getattr(required, axis)
        observed = getattr(candidate, axis)
        if expected == observed:
            continue
        if observed is None and expected is not None:
            directions.add(ScopeComparison.broader)
        elif expected is None and observed is not None:
            directions.add(ScopeComparison.narrower)
        else:
            return ScopeComparison.mismatch

    if not directions:
        return ScopeComparison.exact
    if len(directions) > 1:
        return ScopeComparison.mismatch
    direction = next(iter(directions))
    rule = f"{required.scope_id}->{candidate.scope_id}"
    if rule in required.declared_compatibility_rules:
        return ScopeComparison.compatible_by_declared_rule
    return direction


def scope_can_support(required: ScopeKey, candidate: ScopeKey) -> bool:
    return compare_scope_keys(required, candidate) in {
        ScopeComparison.exact,
        ScopeComparison.compatible_by_declared_rule,
    }
