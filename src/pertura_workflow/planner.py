from __future__ import annotations

import json
from functools import lru_cache
from importlib import resources
from dataclasses import dataclass
from pathlib import PurePosixPath
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
from pertura_core.hashing import canonical_hash
from pertura_workflow.capabilities.registry import (
    CapabilityRegistry,
    capability_scientific_hash,
)


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


_PLAN_NODE_STATUS_ORDER = {
    "ready": 0,
    "planned": 1,
    "blocked": 2,
    "completed": 3,
}


@lru_cache(maxsize=1)
def _codeact_protocols() -> Mapping[str, Mapping[str, Any]]:
    resource = resources.files("pertura_workflow.capabilities").joinpath(
        "codeact_protocols.v1.json"
    )
    payload = json.loads(resource.read_text(encoding="utf-8"))
    if payload.get("schema_version") != "pertura-codeact-protocol-catalog-v1":
        raise ValueError("unsupported CodeAct protocol catalog schema")
    protocols = {
        str(item.get("protocol_id") or ""): dict(item)
        for item in payload.get("protocols") or ()
    }
    if not protocols or "" in protocols:
        raise ValueError("CodeAct protocols must declare protocol_id")
    if len(protocols) != len(tuple(payload.get("protocols") or ())):
        raise ValueError("CodeAct protocol ids must be unique")
    return dict(sorted(protocols.items()))


def codeact_protocol_ids() -> tuple[str, ...]:
    """Return the frozen deterministic CodeAct protocol identifiers."""

    return tuple(_codeact_protocols())


def codeact_protocol_environment_profile(
    binding: Mapping[str, Any] | None,
) -> str | None:
    if not binding:
        return None
    protocol_id = str(binding.get("protocol_id") or "")
    try:
        protocol = _codeact_protocols()[protocol_id]
    except KeyError as exc:
        raise ValueError(f"unknown CodeAct protocol: {protocol_id}") from exc
    return str(protocol["environment_profile"])


def build_codeact_handoff(
    *,
    task_id: str,
    binding: Mapping[str, Any],
    asset_bindings: Mapping[str, Mapping[str, Any]],
    output_contract: Mapping[str, Any],
    environment_ready: Mapping[str, bool],
    skill_plan: Mapping[str, Iterable[str]] | None = None,
    skill_resources: Mapping[str, Iterable[str]] | None = None,
) -> dict[str, Any]:
    """Instantiate an answer-free CodeAct handoff from one frozen protocol.

    The task binds scientific roles to a protocol id.  Runtime-owned assets,
    output paths, and environment readiness are compiled without asking the
    provider to discover packages or inspect the repository.
    """

    protocol_id = str(binding.get("protocol_id") or "")
    try:
        protocol = _codeact_protocols()[protocol_id]
    except KeyError as exc:
        raise ValueError(f"unknown CodeAct protocol: {protocol_id}") from exc
    required = tuple(
        str(item) for item in protocol.get("required_binding_fields") or ()
    )
    missing = [name for name in required if not binding.get(name)]
    if missing:
        raise ValueError(
            f"{task_id}: CodeAct protocol binding is missing {sorted(missing)}"
        )

    benchmark_result = PurePosixPath(str(output_contract.get("benchmark_result") or ""))
    if (
        not str(benchmark_result)
        or benchmark_result.is_absolute()
        or ".." in benchmark_result.parts
    ):
        raise ValueError(f"{task_id}: invalid CodeAct benchmark result path")
    environment_variable = str(protocol["environment_variable"])
    entrypoint = str(protocol["entrypoint"])
    profile = str(protocol["environment_profile"])
    skill_plan = skill_plan or {}
    skill_resources = skill_resources or {}
    outputs = {
        str(role): (benchmark_result.parent / str(relative)).as_posix()
        for role, relative in sorted(
            dict(output_contract.get("artifact_paths") or {}).items()
        )
    }
    outputs["benchmark_result"] = benchmark_result.as_posix()
    inputs = {
        str(role): {
            key: value
            for key, value in dict(asset).items()
            if key
            in (
                "asset_id",
                "path",
                "content_sha256",
                "kind",
                "source_class",
            )
        }
        for role, asset in sorted(asset_bindings.items())
    }
    frozen_binding = {
        str(key): value
        for key, value in sorted(binding.items())
        if key != "protocol_id"
    }
    blockers = []
    if not environment_ready.get(profile, False):
        blockers.append(f"frozen scientific environment is not ready: {profile}")
    method_skills = tuple(skill_plan.get("method") or ())
    bound_skills = [
        (str(phase), str(skill))
        for phase in ("startup", "method", "closure")
        for skill in skill_plan.get(phase) or ()
    ]
    if method_skills:
        execution = {
            "mode": "bound_skill_pipeline",
            "single_script_wrapper_required": False,
            "steps": [
                {
                    "step_index": index,
                    "phase": phase,
                    "skill_id": skill,
                    "resources": [
                        str(resource)
                        for resource in skill_resources.get(skill) or ()
                    ],
                }
                for index, (phase, skill) in enumerate(bound_skills, start=1)
            ],
        }
        invocation = None
    else:
        script_path = benchmark_result.parent / str(protocol["script_name"])
        execution = {
            "mode": "single_script",
            "single_script_wrapper_required": True,
            "steps": [],
        }
        invocation = {
            "script_path": script_path.as_posix(),
            "command": (
                f'"${{{environment_variable}}}/bin/{entrypoint}" '
                f'"{script_path.as_posix()}"'
            ),
        }
    payload = {
        "schema_version": "pertura-codeact-handoff-v1",
        "task_id": task_id,
        "protocol_id": protocol_id,
        "protocol_hash": canonical_hash(protocol),
        "status": "blocked" if blockers else "ready",
        "blockers": blockers,
        "method_family": protocol["method_family"],
        "binding": frozen_binding,
        "environment": {
            "profile": profile,
            "variable": environment_variable,
            "ready": not blockers,
        },
        "execution": execution,
        "inputs": inputs,
        "outputs": outputs,
        "authority": {
            "source_class": "exploratory",
            "capability_receipt": False,
            "claim_rule": (
                "CodeAct outputs cannot be represented as verifier-signed "
                "capability results."
            ),
        },
    }
    if invocation is not None:
        payload["invocation"] = invocation
    return {**payload, "handoff_hash": canonical_hash(payload)}


def build_capability_contract_view(
    spec: CapabilitySpec,
    *,
    contract: DatasetContract,
    asset_bindings: Mapping[str, Mapping[str, Any]] | None = None,
    objective: str = "",
) -> dict[str, Any]:
    """Render one registry-owned spec for progressive agent disclosure.

    The view contains only information already enforced by the capability
    registry and runtime.  It deliberately excludes benchmark references,
    evaluators, score thresholds, and task answers.
    """

    asset_bindings = asset_bindings or {}
    schema = dict(spec.parameters_schema or {})
    properties = dict(schema.get("properties") or {})
    required = tuple(str(item) for item in schema.get("required") or ())
    defaults = {
        str(name): field["default"]
        for name, field in properties.items()
        if isinstance(field, Mapping) and "default" in field
    }
    role_parameters = {
        str(name): str(field["x-pertura-asset-role"])
        for name, field in properties.items()
        if isinstance(field, Mapping) and field.get("x-pertura-asset-role")
    }
    parameters = dict(defaults)
    for name, role in role_parameters.items():
        binding = asset_bindings.get(role) or {}
        asset_id = str(binding.get("asset_id") or "")
        if asset_id:
            parameters[name] = asset_id

    tool_name = {
        "diagnostic": "run_diagnostic",
        "analysis": "run_analysis",
        "virtual": "evaluate_virtual_model",
    }.get(spec.kind)
    call: dict[str, Any] | None = None
    if tool_name:
        arguments: dict[str, Any] = {
            "capability_id": spec.capability_id,
            "contract_id": contract.contract_id,
            "scope": {"dataset_id": contract.dataset_id},
            "parameters": parameters,
        }
        if spec.kind == "analysis":
            arguments["objective"] = objective or spec.capability_id
            arguments["dependencies"] = []
        elif spec.kind == "diagnostic":
            arguments["dependencies"] = []
        call = {"tool": tool_name, "arguments": arguments}

    return {
        "schema_version": "pertura-capability-contract-view-v1",
        "capability_id": spec.capability_id,
        "version": spec.version,
        "kind": spec.kind,
        "summary": spec.summary,
        "scientific_hash": capability_scientific_hash(spec),
        "input_requirements": list(spec.input_requirements),
        "parameter_schema": schema,
        "parameter_defaults": defaults,
        "parameter_examples": list(schema.get("examples") or ()),
        "required_parameters": list(required),
        "asset_role_parameters": role_parameters,
        "depends_on": list(spec.depends_on),
        "dependency_kinds": list(spec.dependency_kinds),
        "dependency_policy": dict(spec.metadata.get("dependency_policy") or {}),
        "environment_profile": spec.metadata.get("environment_profile"),
        "timeout_seconds": spec.timeout_seconds,
        "output_kind": spec.output_kind,
        "source_class": spec.source_class.value,
        "claim_permissions": list(spec.claim_permissions),
        "minimal_call": call,
    }


def compile_capability_execution_brief(
    *,
    task_id: str,
    objective: str,
    execution_mode: str,
    candidate_capability_ids: Iterable[str],
    contract: DatasetContract,
    committed_results: Iterable[ResultEnvelope] = (),
    asset_bindings: Mapping[str, Mapping[str, Any]] | None = None,
    environment_ready: Mapping[str, bool] | None = None,
    codeact_protocol_binding: Mapping[str, Any] | None = None,
    output_contract: Mapping[str, Any] | None = None,
    registry: CapabilityRegistry | None = None,
    completion_checklist: Iterable[str] = (),
    skill_plan: Mapping[str, Iterable[str]] | None = None,
    skill_bundle_hash: str | None = None,
    skill_resources: Mapping[str, Iterable[str]] | None = None,
    active_window_size: int = 5,
) -> dict[str, Any]:
    """Compile a deterministic P0 brief from an explicit candidate allowlist.

    This is intentionally not a natural-language resolver.  Missing scientific
    dependencies outside the allowlist are reported and never auto-inserted.
    """

    if active_window_size < 1 or active_window_size > 5:
        raise ValueError("active_window_size must be between 1 and 5")
    asset_bindings = asset_bindings or {}
    environment_ready = environment_ready or {}
    output_contract = output_contract or {}
    skill_plan = skill_plan or {}
    skill_resources = skill_resources or {}
    registry = registry or CapabilityRegistry.load_default(include_external=False)
    candidates = tuple(dict.fromkeys(str(item) for item in candidate_capability_ids))
    results = tuple(committed_results)
    current_ids = {
        item.capability_id
        for item in results
        if item.contract_id == contract.contract_id
        and item.contract_hash == contract.canonical_hash
        and not item.stale
        and _status(item) in _SUCCESS_STATUSES
    }
    candidate_set = set(candidates)
    nodes: list[dict[str, Any]] = []

    for index, capability_id in enumerate(candidates):
        spec = registry.get(capability_id)
        blockers: list[str] = []
        applicability = plan_requested_capability(
            capability_id,
            expected_kind=spec.kind,
            contract=contract,
            committed_results=results,
            registry=registry,
            objective=objective,
            environment_ready=environment_ready,
        )
        blockers.extend(applicability.blockers)
        missing_external = [
            dependency
            for dependency in spec.depends_on
            if dependency not in candidate_set and dependency not in current_ids
        ]
        pending_internal = [
            dependency
            for dependency in spec.depends_on
            if dependency in candidate_set and dependency not in current_ids
        ]
        if spec.metadata.get("deprecated", False):
            blockers.append("capability is deprecated or compatibility-only")
        if missing_external:
            blockers.append(
                "required dependencies are outside the frozen candidate plan: "
                + ", ".join(missing_external)
            )
        blockers = list(dict.fromkeys(blockers))

        if capability_id in current_ids:
            status = "completed"
        elif blockers:
            status = "blocked"
        elif pending_internal:
            status = "planned"
        else:
            status = "ready"
        nodes.append(
            {
                "node_id": f"node_{index + 1:02d}",
                "capability_id": capability_id,
                "status": status,
                "blockers": blockers,
                "pending_plan_dependencies": pending_internal,
                "missing_plan_dependencies": missing_external,
                "contract": build_capability_contract_view(
                    spec,
                    contract=contract,
                    asset_bindings=asset_bindings,
                    objective=objective,
                ),
            }
        )

    if not nodes:
        route = (
            "evidence_interpretation"
            if execution_mode == "evidence_interpretation"
            else "codeact"
        )
    elif any(node["status"] in {"ready", "planned", "completed"} for node in nodes):
        route = "capability_or_codeact"
    else:
        route = "codeact"

    incomplete = [node for node in nodes if node["status"] != "completed"]
    active = sorted(
        incomplete,
        key=lambda item: (
            _PLAN_NODE_STATUS_ORDER[str(item["status"])],
            candidates.index(str(item["capability_id"])),
        ),
    )[:active_window_size]
    facts = design_facts(contract, results)
    assets = {
        str(role): {
            key: value
            for key, value in dict(binding).items()
            if key in {"asset_id", "path", "content_sha256", "kind", "source_class"}
        }
        for role, binding in sorted(asset_bindings.items())
    }
    codeact_handoff = None
    if route in {"codeact", "capability_or_codeact"} and codeact_protocol_binding:
        codeact_handoff = build_codeact_handoff(
            task_id=task_id,
            binding=codeact_protocol_binding,
            asset_bindings=asset_bindings,
            output_contract=output_contract,
            environment_ready=environment_ready,
            skill_plan=skill_plan,
            skill_resources=skill_resources,
        )
        if route == "codeact" and codeact_handoff["status"] == "blocked":
            route = "blocked"
    payload = {
        "schema_version": "pertura-capability-execution-brief-v1",
        "task_id": task_id,
        "objective": objective,
        "execution_mode": execution_mode,
        "route": route,
        "candidate_source": "frozen_explicit_allowlist",
        "candidate_capability_ids": list(candidates),
        "dataset_contract": {
            "contract_id": contract.contract_id,
            "contract_hash": contract.canonical_hash,
            "dataset_id": contract.dataset_id,
            "unresolved_fields": list(contract.unresolved_fields),
            "design_facts": facts,
        },
        "assets": assets,
        "skill_plan": {
            str(phase): [str(item) for item in items]
            for phase, items in skill_plan.items()
        },
        "skill_bundle_hash": skill_bundle_hash,
        "skill_resources": {
            str(skill): [str(item) for item in resources]
            for skill, resources in skill_resources.items()
        },
        "codeact_handoff": codeact_handoff,
        "nodes": nodes,
        "active_window": [
            {
                "node_id": node["node_id"],
                "capability_id": node["capability_id"],
                "status": node["status"],
                "blockers": node["blockers"],
                "tool": (node["contract"].get("minimal_call") or {}).get("tool"),
                "required_upstream": node["contract"]["depends_on"],
            }
            for node in active
        ],
        "completion_checklist": list(completion_checklist),
        "stop_conditions": [
            "Do not inspect repository source, capability YAML, tests, or environment directories to rediscover contracts.",
            "Do not repeat a blocked capability call without new dependency evidence.",
            "CodeAct outputs remain exploratory and cannot be represented as verifier-signed capability results.",
            "Write the task benchmark_result.json before returning the provider TurnDraft.",
        ],
    }
    plan_hash = canonical_hash(payload)
    return {
        **payload,
        "plan_id": f"plan_{plan_hash.removeprefix('sha256:')[:20]}",
        "plan_hash": plan_hash,
    }


def build_capability_contract_catalog(
    registry: CapabilityRegistry | None = None,
) -> dict[str, Any]:
    """Return the hash-bound, answer-free a19 capability contract catalog."""

    registry = registry or CapabilityRegistry.load_default(include_external=False)
    capabilities = []
    for spec in registry.specs():
        capabilities.append(
            {
                "capability_id": spec.capability_id,
                "version": spec.version,
                "kind": spec.kind,
                "summary": spec.summary,
                "scientific_hash": capability_scientific_hash(spec),
                "deprecated": bool(spec.metadata.get("deprecated", False)),
                "parameters_schema": dict(spec.parameters_schema or {}),
                "input_requirements": list(spec.input_requirements),
                "depends_on": list(spec.depends_on),
                "dependency_kinds": list(spec.dependency_kinds),
                "dependency_policy": dict(spec.metadata.get("dependency_policy") or {}),
                "environment_profile": spec.metadata.get("environment_profile"),
                "output_kind": spec.output_kind,
                "source_class": spec.source_class.value,
                "claim_permissions": list(spec.claim_permissions),
            }
        )
    payload = {
        "schema_version": "pertura-capability-contract-catalog-v1",
        "capability_count": len(capabilities),
        "active_capability_count": sum(not item["deprecated"] for item in capabilities),
        "capabilities": capabilities,
    }
    return {**payload, "catalog_hash": canonical_hash(payload)}


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

    selected: str | None = None
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
        blockers.extend(requested_plan.blockers)
        selected = requested_capability_id
    else:
        selected = _route_objective(normalized, facts, blockers)

    if selected is None:
        if not blockers:
            aliases = sorted(
                {
                    _norm(alias)
                    for route in _planner_routes()
                    for alias in route.get("objectives") or ()
                }
            )
            blockers.append(
                "objective does not exactly match a supported alias; choose a "
                "capability explicitly or use one of: " + ", ".join(aliases)
            )
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
    if (
        profile
        and environment_ready is not None
        and not environment_ready.get(profile, False)
    ):
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
        str(
            item.object_id
            if isinstance(item, DependencyRef)
            else item.get("object_id") or ""
        )
        for item in hints
        if str(
            item.object_id
            if isinstance(item, DependencyRef)
            else item.get("object_id") or ""
        )
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
        hint_id = str(
            hint.object_id
            if isinstance(hint, DependencyRef)
            else hint.get("object_id") or ""
        )
        stored = by_id.get(hint_id)
        if stored is None:
            blockers.append(
                f"dependency hint does not reference a committed result: {hint_id}"
            )
            continue
        supplied_hash = str(
            hint.object_hash
            if isinstance(hint, DependencyRef)
            else hint.get("object_hash") or ""
        )
        supplied_kind = str(
            hint.kind if isinstance(hint, DependencyRef) else hint.get("kind") or ""
        )
        supplied_state = str(
            hint.state if isinstance(hint, DependencyRef) else hint.get("state") or ""
        )
        if supplied_hash and supplied_hash != stored.canonical_hash:
            blockers.append(f"dependency hint hash mismatch: {hint_id}")
        if supplied_kind and supplied_kind != stored.result_kind:
            blockers.append(f"dependency hint kind mismatch: {hint_id}")
        if supplied_state and supplied_state != (
            "stale" if stored.stale else "current"
        ):
            blockers.append(f"dependency hint state mismatch: {hint_id}")

    policy = dict(spec.metadata.get("dependency_policy") or {})
    for dependency_capability in spec.depends_on:
        upstream_spec = registry.get(dependency_capability)
        all_candidates = [
            item for item in results if item.capability_id == dependency_capability
        ]
        candidate_ids.update(item.result_id for item in all_candidates)
        dependency_policy = dict(policy.get(dependency_capability) or {})
        metadata = dict(spec.metadata)
        metadata.setdefault("upstream_spec_hashes", {})[
            dependency_capability
        ] = capability_scientific_hash(upstream_spec)
        metadata.setdefault("upstream_dependency_policy_hashes", {})[
            dependency_capability
        ] = canonical_hash(dict(upstream_spec.metadata.get("dependency_policy") or {}))
        effective_spec = spec.model_copy(update={"metadata": metadata})
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

    for group in spec.metadata.get("dependency_sets", ()):
        if not isinstance(group, Mapping):
            blockers.append("capability dependency set metadata is invalid")
            continue
        name = str(group.get("name") or "").strip()
        if not name:
            blockers.append("capability dependency set is missing a name")
            continue
        accepted_kinds = {str(item) for item in group.get("result_kinds") or ()}
        accepted_capabilities = {
            str(item) for item in group.get("capability_ids") or ()
        }
        accepted_sources = {str(item) for item in group.get("source_classes") or ()}
        accepted_statuses = {
            str(item) for item in group.get("accepted_statuses") or _SUCCESS_STATUSES
        }
        scope_rule = str(group.get("scope_rule") or "exact")
        minimum = int(group.get("min_count", 1 if group.get("required", True) else 0))
        maximum = int(group.get("max_count") or 0)
        required_group = bool(group.get("required", True))
        selection = str(group.get("selection") or "explicit_result_ids")
        compatible: list[ResultEnvelope] = []
        for item in results:
            issues: list[str] = []
            if accepted_kinds and item.result_kind not in accepted_kinds:
                continue
            if (
                accepted_capabilities
                and item.capability_id not in accepted_capabilities
            ):
                continue
            if accepted_sources and item.source_class.value not in accepted_sources:
                continue
            if (
                item.contract_id != contract.contract_id
                or item.contract_hash != contract.canonical_hash
            ):
                issues.append("contract_mismatch")
            if item.stale:
                issues.append("stale")
            if _status(item) not in accepted_statuses:
                issues.append("status_not_accepted")
            if any(
                dependency.required and dependency.state != "current"
                for dependency in item.dependencies
            ):
                issues.append("upstream_dependency_not_current")
            if not _dependency_set_scope_ok(
                required_scope,
                item.scope,
                scope_rule,
            ):
                issues.append("scope_mismatch")
            if spec.trust_level == CapabilityTrust.builtin_trusted:
                if item.capability_trust != CapabilityTrust.builtin_trusted:
                    issues.append("untrusted_dependency")
                if item.result_id not in trusted_ids:
                    issues.append("missing_trusted_receipt")
            dependency_verdicts.append(
                {
                    "dependency_set": name,
                    "result_id": item.result_id,
                    "result_kind": item.result_kind,
                    "status": _status(item),
                    "scope_id": item.scope.scope_id,
                    "trust_level": item.capability_trust.value,
                    "usable": not issues,
                    "reasons": list(issues),
                }
            )
            candidate_ids.add(item.result_id)
            if not issues:
                compatible.append(item)

        selected = [item for item in compatible if item.result_id in hint_ids]
        if selection == "all_compatible" and not selected:
            selected = compatible
        elif selection == "auto_single" and not selected and len(compatible) == 1:
            selected = compatible
        elif selection == "explicit_result_ids" and compatible and not selected:
            blockers.append(
                f"dependency set {name} requires explicit committed result IDs"
            )

        if len(selected) < minimum:
            if required_group or selected:
                blockers.append(
                    f"dependency set {name} requires at least {minimum} usable results"
                )
            continue
        if maximum and len(selected) > maximum:
            blockers.append(
                f"dependency set {name} accepts at most {maximum} usable results"
            )
            ambiguous.extend(item.result_id for item in selected)
            continue
        for item in sorted(selected, key=lambda value: value.result_id):
            _append_dependency(
                resolved,
                DependencyRef(
                    kind=item.result_kind,
                    object_id=item.result_id,
                    object_hash=item.canonical_hash,
                    role=f"dependency_set:{name}",
                ),
            )
            for transitive in item.dependencies:
                if transitive.required and transitive.state == "current":
                    _append_dependency(resolved, transitive)

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
    replicate_count = (
        _count_values(replicate_value) if _confirmed(replicate_field) else 0
    )
    moi_field = identity.get("design_moi")
    guide_design_field = identity.get("guide_design")
    moi = _confirmed_enum(moi_field, {"low", "high"})
    guide_design = _confirmed_enum(
        guide_design_field,
        {"single", "combinatorial", "mixed"},
    )

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
        "moi": moi,
        "guide_design": guide_design,
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
    condition_blockers: list[str] = []
    for route in _planner_routes():
        if not _objective_matches(route, objective):
            continue
        if not _route_applies(route, facts):
            condition_blockers.append(
                str(
                    route.get("when_blocker")
                    or "confirmed design is incompatible with capability"
                )
            )
            continue
        blockers.extend(_requirement_blockers(route, facts))
        return str(route["capability_id"])
    blockers.extend(dict.fromkeys(condition_blockers))
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
    if any("contains_any" in item for item in routes):
        raise ValueError("planner routes cannot use substring matching")
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
    return objective in objectives


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
        dependency_policy.get("accepted_statuses") or _accepted_statuses(upstream_kind)
    )
    if _status(result) not in accepted:
        issues.append("status_not_accepted")
    if expected_kind and result.result_kind != expected_kind:
        issues.append("wrong_result_kind")
    if any(item.required and item.state != "current" for item in result.dependencies):
        issues.append("upstream_dependency_not_current")
    scope_mode = str(dependency_policy["scope"])
    if not _dependency_set_scope_ok(required_scope, result.scope, scope_mode):
        issues.append("scope_mismatch")
    if result.capability_version == upstream_version:
        expected_spec_hash = downstream_spec.metadata.get(
            "upstream_spec_hashes", {}
        ).get(result.capability_id)
        # The registry-owned caller supplies current hashes below; absence in old
        # result metadata makes the result historical-only.
        actual_spec_hash = str(result.metadata.get("capability_spec_hash") or "")
        actual_policy_hash = str(result.metadata.get("dependency_policy_hash") or "")
        if expected_spec_hash and actual_spec_hash != expected_spec_hash:
            issues.append("capability_spec_hash_mismatch")
        expected_policy_hash = downstream_spec.metadata.get(
            "upstream_dependency_policy_hashes", {}
        ).get(result.capability_id)
        if expected_policy_hash and actual_policy_hash != expected_policy_hash:
            issues.append("dependency_policy_hash_mismatch")
    if downstream_spec.trust_level == CapabilityTrust.builtin_trusted:
        if result.capability_trust != CapabilityTrust.builtin_trusted:
            issues.append("untrusted_dependency")
        if result.result_id not in trusted_ids:
            issues.append("missing_trusted_receipt")
    return tuple(dict.fromkeys(issues))


def _dependency_set_scope_ok(
    required: ScopeKey,
    candidate: ScopeKey,
    mode: str,
) -> bool:
    if required.unresolved_fields or candidate.unresolved_fields:
        return False
    if mode == "dataset":
        return required.dataset_id == candidate.dataset_id
    if mode == "same_dataset_context":
        return (
            required.dataset_id == candidate.dataset_id
            and required.control_ids == candidate.control_ids
            and required.state_ids == candidate.state_ids
            and required.donor_ids == candidate.donor_ids
            and required.replicate_ids == candidate.replicate_ids
            and required.batch_ids == candidate.batch_ids
            and required.dose == candidate.dose
            and required.timepoint == candidate.timepoint
            and required.estimand == candidate.estimand
        )
    comparison = compare_scope_keys(required, candidate)
    if mode == "compatible":
        return comparison in {
            ScopeComparison.exact,
            ScopeComparison.compatible_by_declared_rule,
        }
    return comparison == ScopeComparison.exact


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


def _confirmed_enum(
    value: Mapping[str, Any] | None,
    allowed: set[str],
) -> str:
    if not _confirmed(value):
        return "unknown"
    normalized = _norm(value.get("value"))
    return normalized if normalized in allowed else "unknown"


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
