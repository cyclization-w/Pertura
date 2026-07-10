from __future__ import annotations

import json
from functools import lru_cache
from importlib import resources
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from pertura_core import (
    CapabilitySpec,
    CapabilityTrust,
    DatasetContract,
    DependencyRef,
    ResultEnvelope,
    ScopeComparison,
    ScopeKey,
    compare_scope_keys,
)
from pertura_workflow.capabilities.registry import CapabilityRegistry


_SUCCESS_STATUSES = {
    "screen_passed",
    "caution",
    "completed",
    "completed_with_caution",
    "supported",
    "limited",
}


@dataclass(frozen=True)
class CapabilityPlan:
    status: str
    objective: str
    capability_id: str | None
    blockers: tuple[str, ...] = ()
    required_upstream: tuple[str, ...] = ()
    design_facts: Mapping[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "pertura-capability-plan-v1",
            "status": self.status,
            "objective": self.objective,
            "capability_id": self.capability_id,
            "blockers": list(self.blockers),
            "required_upstream": list(self.required_upstream),
            "design_facts": dict(self.design_facts or {}),
        }


@dataclass(frozen=True)
class DependencyResolution:
    status: str
    dependencies: tuple[DependencyRef, ...]
    blockers: tuple[str, ...] = ()
    required_upstream: tuple[str, ...] = ()
    ambiguous_result_ids: tuple[str, ...] = ()
    candidate_result_ids: tuple[str, ...] = ()
    dependency_verdicts: tuple[Mapping[str, Any], ...] = ()

    @property
    def ok(self) -> bool:
        return self.status == "resolved"


def plan_analysis(
    objective: str,
    *,
    contract: DatasetContract,
    committed_results: Iterable[ResultEnvelope] = (),
    registry: CapabilityRegistry | None = None,
    requested_capability_id: str | None = None,
    environment_ready: Mapping[str, bool] | None = None,
) -> CapabilityPlan:
    registry = registry or CapabilityRegistry.load_default(include_external=False)
    results = tuple(committed_results)
    facts = design_facts(contract, results)
    normalized = _norm(objective)
    blockers: list[str] = []

    selected = _route_objective(normalized, facts, blockers)
    if requested_capability_id:
        requested_plan = plan_requested_capability(
            requested_capability_id,
            expected_kind="analysis",
            contract=contract,
            committed_results=results,
            registry=registry,
            objective=normalized,
            environment_ready=environment_ready,
        )
        requested = registry.get(requested_capability_id)
        blockers.extend(requested_plan.blockers)
        if selected and requested.capability_id != selected:
            blockers.append(
                f"requested capability {requested.capability_id} is incompatible with "
                f"the design-aware route {selected}"
            )
        else:
            selected = requested.capability_id

    if selected is None:
        if not blockers:
            blockers.append("objective does not map to a supported capability")
        return CapabilityPlan(
            status="blocked",
            objective=normalized,
            capability_id=None,
            blockers=tuple(blockers),
            design_facts=facts,
        )

    spec = registry.get(selected)
    if spec.kind != "analysis":
        blockers.append(
            f"routed capability {spec.capability_id} is {spec.kind}; "
            "use the matching product tool"
        )
    profile = str(spec.metadata.get("environment_profile") or "")
    if profile and environment_ready is not None and not environment_ready.get(profile, False):
        blockers.append(f"required environment is unavailable: {profile}")

    required = tuple(spec.depends_on)
    return CapabilityPlan(
        status="blocked" if blockers else "ready",
        objective=normalized,
        capability_id=selected,
        blockers=tuple(dict.fromkeys(blockers)),
        required_upstream=required,
        design_facts=facts,
    )


def plan_requested_capability(
    capability_id: str,
    *,
    expected_kind: str,
    contract: DatasetContract,
    committed_results: Iterable[ResultEnvelope] = (),
    registry: CapabilityRegistry | None = None,
    objective: str | None = None,
    environment_ready: Mapping[str, bool] | None = None,
) -> CapabilityPlan:
    """Validate an explicit capability against the design-aware route table.

    This helper never executes prerequisites and never selects a fallback. Its
    required_upstream field is guidance for the caller; authoritative result
    selection remains the responsibility of resolve_dependencies.
    """

    registry = registry or CapabilityRegistry.load_default(include_external=False)
    spec = registry.get(capability_id)
    results = tuple(committed_results)
    facts = design_facts(contract, results)
    normalized = _norm(objective or capability_id)
    blockers: list[str] = []

    if spec.kind != expected_kind:
        blockers.append(
            f"{capability_id} is a {spec.kind} capability, not {expected_kind}"
        )

    route = _route_for_capability(capability_id)
    if route:
        blockers.extend(
            _requirement_blockers(route, facts, include_route_condition=True)
        )

    profile = str(spec.metadata.get("environment_profile") or "")
    if (
        profile
        and environment_ready is not None
        and not environment_ready.get(profile, False)
    ):
        blockers.append(f"required environment is unavailable: {profile}")

    return CapabilityPlan(
        status="blocked" if blockers else "ready",
        objective=normalized,
        capability_id=capability_id,
        blockers=tuple(dict.fromkeys(blockers)),
        required_upstream=tuple(spec.depends_on),
        design_facts=facts,
    )


def resolve_dependencies(
    spec: CapabilitySpec,
    *,
    contract: DatasetContract,
    required_scope: ScopeKey,
    committed_results: Iterable[ResultEnvelope],
    dependency_hints: Iterable[DependencyRef | Mapping[str, Any]] = (),
    trusted_receipt_result_ids: Iterable[str] = (),
    registry: CapabilityRegistry | None = None,
) -> DependencyResolution:
    registry = registry or CapabilityRegistry.load_default(include_external=False)
    results = tuple(committed_results)
    trusted_ids = set(trusted_receipt_result_ids)
    hints = tuple(dependency_hints)
    hint_ids = {
        str(item.object_id if isinstance(item, DependencyRef) else item.get("object_id") or "")
        for item in hints
        if str(item.object_id if isinstance(item, DependencyRef) else item.get("object_id") or "")
    }
    by_id = {item.result_id: item for item in results}
    blockers: list[str] = []
    ambiguous: list[str] = []
    candidate_ids: set[str] = set()
    dependency_verdicts: list[Mapping[str, Any]] = []
    required_missing: list[str] = []
    resolved: list[DependencyRef] = [
        DependencyRef(
            kind="contract",
            object_id=contract.contract_id,
            object_hash=contract.canonical_hash,
            role="dataset_contract",
        )
    ]

    for hint in hints:
        hint_id = str(hint.object_id if isinstance(hint, DependencyRef) else hint.get("object_id") or "")
        stored = by_id.get(hint_id)
        if stored is None:
            blockers.append(f"dependency hint does not reference a committed result: {hint_id}")
            continue
        supplied_hash = str(
            hint.object_hash if isinstance(hint, DependencyRef) else hint.get("object_hash") or ""
        )
        supplied_kind = str(hint.kind if isinstance(hint, DependencyRef) else hint.get("kind") or "")
        supplied_state = str(
            hint.state if isinstance(hint, DependencyRef) else hint.get("state") or ""
        )
        if supplied_hash and supplied_hash != stored.canonical_hash:
            blockers.append(f"dependency hint hash mismatch: {hint_id}")
        if supplied_kind and supplied_kind != stored.result_kind:
            blockers.append(f"dependency hint kind mismatch: {hint_id}")
        if supplied_state and supplied_state != ("stale" if stored.stale else "current"):
            blockers.append(f"dependency hint state mismatch: {hint_id}")

    policy = dict(spec.metadata.get("dependency_policy") or {})
    for dependency_capability in spec.depends_on:
        upstream_spec = registry.get(dependency_capability)
        all_candidates = [
            item for item in results if item.capability_id == dependency_capability
        ]
        candidate_ids.update(item.result_id for item in all_candidates)
        dependency_policy = dict(policy.get(dependency_capability) or {})
        expected_kind = str(
            dependency_policy.get("result_kind") or upstream_spec.output_kind
        )
        candidates: list[ResultEnvelope] = []
        for item in all_candidates:
            issues = _candidate_issues(
                item,
                downstream_spec=spec,
                upstream_kind=upstream_spec.kind,
                upstream_version=upstream_spec.version,
                contract=contract,
                required_scope=required_scope,
                expected_kind=expected_kind,
                trusted_ids=trusted_ids,
                dependency_policy=dependency_policy,
            )
            dependency_verdicts.append(
                {
                    "capability_id": dependency_capability,
                    "result_id": item.result_id,
                    "result_kind": item.result_kind,
                    "status": _status(item),
                    "scope_id": item.scope.scope_id,
                    "trust_level": item.capability_trust.value,
                    "usable": not issues,
                    "reasons": list(issues),
                }
            )
            if not issues:
                candidates.append(item)

        hinted = [item for item in candidates if item.result_id in hint_ids]
        if hinted:
            candidates = hinted
        if not candidates:
            required_missing.append(dependency_capability)
            blockers.append(
                f"required dependency is missing or unusable: {dependency_capability}"
            )
            continue
        if len(candidates) > 1:
            ids = sorted(item.result_id for item in candidates)
            ambiguous.extend(ids)
            blockers.append(
                f"dependency is ambiguous for {dependency_capability}: {', '.join(ids)}"
            )
            continue

        result = candidates[0]
        _append_dependency(
            resolved,
            DependencyRef(
                kind=result.result_kind,
                object_id=result.result_id,
                object_hash=result.canonical_hash,
                role=dependency_capability,
            ),
        )
        for transitive in result.dependencies:
            if transitive.required and transitive.state == "current":
                _append_dependency(resolved, transitive)
        for provided_kind in upstream_spec.metadata.get(
            "provides_dependency_kinds", ()
        ):
            _append_dependency(
                resolved,
                DependencyRef(
                    kind=str(provided_kind),
                    object_id=result.result_id,
                    object_hash=result.canonical_hash,
                    role=f"{dependency_capability}:provided",
                ),
            )

    return DependencyResolution(
        status="blocked" if blockers else "resolved",
        dependencies=tuple(resolved),
        blockers=tuple(blockers),
        required_upstream=tuple(required_missing),
        ambiguous_result_ids=tuple(sorted(set(ambiguous))),
        candidate_result_ids=tuple(sorted(candidate_ids)),
        dependency_verdicts=tuple(dependency_verdicts),
    )


def design_facts(
    contract: DatasetContract,
    results: Iterable[ResultEnvelope],
) -> dict[str, Any]:
    committed = tuple(results)
    identity = contract.identity_fields
    controls_defined = _confirmed(identity.get("control"))
    replicate_field = identity.get("replicate")
    replicate_value = (replicate_field or {}).get("value")
    replicate_count = _count_values(replicate_value) if _confirmed(replicate_field) else 0
    moi = str(contract.metadata.get("design_moi") or "").strip().lower()

    for result in committed:
        if (
            result.contract_id != contract.contract_id
            or result.contract_hash != contract.canonical_hash
            or result.stale
            or _status(result) not in _SUCCESS_STATUSES
        ):
            continue
        if result.capability_id == "diagnostic.design_balance.v1":
            replicate_count = max(
                replicate_count,
                int(result.metrics.get("minimum_units_per_condition") or 0),
            )
        elif result.capability_id == "screen.retained_cells.v1":
            moi = str(result.metrics.get("design_moi") or moi).strip().lower()

    assignment_ids = {
        "diagnostic.guide_assignment.v1",
        "guide.assignment.nb_mixture.v1",
        "screen.retained_cells.v1",
    }
    state_ids = {
        "reference.state.control_pca_leiden.v1",
        "state.reference.fit.v1",
        "state.reference.map_knn.v1",
    }
    return {
        "moi": moi or "unknown",
        "n_replicates": replicate_count,
        "controls_defined": controls_defined,
        "guide_assignment_validated": any(
            item.contract_id == contract.contract_id
            and item.contract_hash == contract.canonical_hash
            and item.capability_id in assignment_ids
            and not item.stale
            and _status(item) in _SUCCESS_STATUSES
            for item in committed
        ),
        "guide_counts_available": bool(contract.guide_matrix),
        "state_reference_available": any(
            item.contract_id == contract.contract_id
            and item.contract_hash == contract.canonical_hash
            and item.capability_id in state_ids
            and not item.stale
            and _status(item) in _SUCCESS_STATUSES
            for item in committed
        ),
    }


def _route_objective(
    objective: str,
    facts: Mapping[str, Any],
    blockers: list[str],
) -> str | None:
    for route in _planner_routes():
        if not _objective_matches(route, objective):
            continue
        if not _route_applies(route, facts):
            continue
        blockers.extend(_requirement_blockers(route, facts))
        return str(route["capability_id"])
    return None


@lru_cache(maxsize=1)
def _planner_routes() -> tuple[dict[str, Any], ...]:
    resource = resources.files("pertura_workflow.capabilities").joinpath(
        "planner_routes.json"
    )
    payload = json.loads(resource.read_text(encoding="utf-8"))
    if payload.get("schema_version") != "pertura-capability-planner-routes-v1":
        raise ValueError("unsupported capability planner route schema")
    routes = tuple(dict(item) for item in payload.get("routes") or ())
    capability_ids = [str(item.get("capability_id") or "") for item in routes]
    if not routes or any(not item for item in capability_ids):
        raise ValueError("planner routes must declare capability_id")
    if len(capability_ids) != len(set(capability_ids)):
        raise ValueError("planner routes contain duplicate capability_id")
    return tuple(
        sorted(
            routes,
            key=lambda item: (
                -int(item.get("priority") or 0),
                str(item["capability_id"]),
            ),
        )
    )


def _route_for_capability(capability_id: str) -> Mapping[str, Any] | None:
    return next(
        (
            route
            for route in _planner_routes()
            if route["capability_id"] == capability_id
        ),
        None,
    )


def _objective_matches(route: Mapping[str, Any], objective: str) -> bool:
    objectives = {_norm(item) for item in route.get("objectives") or ()}
    contains = tuple(_norm(item) for item in route.get("contains_any") or ())
    return objective in objectives or any(
        token and token in objective for token in contains
    )


def _route_applies(route: Mapping[str, Any], facts: Mapping[str, Any]) -> bool:
    condition = dict(route.get("when") or {})
    moi = str(facts.get("moi") or "").lower()
    allowed = {str(item).lower() for item in condition.get("moi_in") or ()}
    denied = {str(item).lower() for item in condition.get("moi_not_in") or ()}
    return (not allowed or moi in allowed) and (not denied or moi not in denied)


def _requirement_blockers(
    route: Mapping[str, Any],
    facts: Mapping[str, Any],
    *,
    include_route_condition: bool = False,
) -> list[str]:
    blockers: list[str] = []
    if include_route_condition and not _route_applies(route, facts):
        blockers.append(
            str(
                route.get("when_blocker")
                or "confirmed design is incompatible with capability"
            )
        )
    messages = {
        "controls_defined": "control definition is missing",
        "guide_assignment_validated": "guide assignment is not validated",
        "guide_counts_available": "high-MOI association requires guide counts",
        "state_reference_available": "cell-state reference is missing",
    }
    for requirement in route.get("requires") or ():
        requirement = str(requirement)
        if not facts.get(requirement):
            blockers.append(
                messages.get(
                    requirement,
                    f"required design fact is missing: {requirement}",
                )
            )
    minimum = int(route.get("min_replicates") or 0)
    if minimum and int(facts.get("n_replicates") or 0) < minimum:
        blockers.append(
            str(
                route.get("min_replicates_blocker")
                or f"replicate-aware analysis requires at least {minimum} units"
            )
        )
    return blockers



def _candidate_issues(
    result: ResultEnvelope,
    *,
    downstream_spec: CapabilitySpec,
    upstream_kind: str,
    upstream_version: str,
    contract: DatasetContract,
    required_scope: ScopeKey,
    expected_kind: str | None,
    trusted_ids: set[str],
    dependency_policy: Mapping[str, Any],
) -> tuple[str, ...]:
    issues: list[str] = []
    if result.capability_version != upstream_version:
        issues.append("capability_version_mismatch")
    if (
        result.contract_id != contract.contract_id
        or result.contract_hash != contract.canonical_hash
    ):
        issues.append("contract_mismatch")
    if result.stale:
        issues.append("stale")
    accepted = set(
        dependency_policy.get("accepted_statuses")
        or _accepted_statuses(upstream_kind)
    )
    if _status(result) not in accepted:
        issues.append("status_not_accepted")
    if expected_kind and result.result_kind != expected_kind:
        issues.append("wrong_result_kind")
    if any(
        item.required and item.state != "current" for item in result.dependencies
    ):
        issues.append("upstream_dependency_not_current")
    if (
        downstream_spec.phase <= 3
        and required_scope.canonical_hash == result.scope.canonical_hash
    ):
        comparison = ScopeComparison.exact
    else:
        comparison = compare_scope_keys(required_scope, result.scope)
    scope_mode = str(dependency_policy.get("scope") or "exact")
    if scope_mode == "exact" and comparison != ScopeComparison.exact:
        issues.append("scope_mismatch")
    if scope_mode == "dataset" and comparison not in {
        ScopeComparison.exact,
        ScopeComparison.broader,
        ScopeComparison.compatible_by_declared_rule,
    }:
        issues.append("scope_mismatch")
    if downstream_spec.trust_level == CapabilityTrust.builtin_trusted:
        if result.capability_trust != CapabilityTrust.builtin_trusted:
            issues.append("untrusted_dependency")
        if result.result_id not in trusted_ids:
            issues.append("missing_trusted_receipt")
    return tuple(dict.fromkeys(issues))


def _accepted_statuses(kind: str) -> tuple[str, ...]:
    return {
        "diagnostic": ("screen_passed", "caution"),
        "analysis": ("completed", "completed_with_caution"),
        "virtual": ("supported", "limited"),
        "report": ("completed", "completed_with_caution"),
    }.get(kind, ())


def _append_dependency(
    dependencies: list[DependencyRef],
    dependency: DependencyRef,
) -> None:
    identity = (dependency.kind, dependency.object_id)
    if any((item.kind, item.object_id) == identity for item in dependencies):
        return
    dependencies.append(dependency)




def _confirmed(value: Mapping[str, Any] | None) -> bool:
    return bool(value) and str(value.get("status") or "") == "confirmed"


def _count_values(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, (list, tuple, set)):
        return len({str(item) for item in value if str(item)})
    if isinstance(value, Mapping):
        return len(value)
    return 1 if str(value) else 0


def _status(result: ResultEnvelope) -> str:
    return str(getattr(result.status, "value", result.status))


def _norm(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")

\n