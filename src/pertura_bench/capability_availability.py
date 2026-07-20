from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pertura_core.hashing import canonical_hash


_PAPER_ROLE_ALIASES = {
    "primary_h5ad": "primary_dataset",
    # The frozen Kang asset catalog binds donor_metadata and
    # cell_state_metadata to the same cell-level table.  Capabilities use the
    # canonical cell_metadata role for that exact object.
    "donor_metadata": "cell_metadata",
}
_NON_CAPABILITY_MODES = {"codeact_scientific", "evidence_interpretation"}


def build_task_capability_availability(
    task_catalog: Mapping[str, Any],
    contract_catalog: Mapping[str, Any],
) -> dict[str, Any]:
    """Compile the answer-free executable capability surface for paper tasks.

    This is an applicability compiler, not a planner: it never selects or runs a
    capability.  A conditional capability is visible because a compatible
    receipt from a declared ancestor task could make it executable at runtime.
    """

    capabilities = {
        str(item["capability_id"]): dict(item)
        for item in contract_catalog.get("capabilities") or ()
    }
    records: list[dict[str, Any]] = []
    records_by_task: dict[str, dict[str, Any]] = {}
    tasks_by_id: dict[str, Mapping[str, Any]] = {
        str(task["task_id"]): task
        for workflow in task_catalog.get("workflows") or ()
        for task in workflow.get("turns") or ()
    }

    for workflow in task_catalog.get("workflows") or ():
        workflow_id = str(workflow["workflow_id"])
        for task in workflow.get("turns") or ():
            task_id = str(task["task_id"])
            candidates = [
                str(item) for item in task.get("expected_capability_dag") or ()
            ]
            missing = [item for item in candidates if item not in capabilities]
            if missing:
                raise ValueError(
                    f"{task_id}: capability candidates are unbound: {sorted(missing)}"
                )
            if task.get("execution_mode") in _NON_CAPABILITY_MODES and candidates:
                raise ValueError(
                    f"{task_id}: non-capability execution mode advertises candidates"
                )

            ancestor_ids = _ancestor_task_ids(task, tasks_by_id)
            available_roles = set(str(item) for item in task.get("required_input_roles") or ())
            available_roles.update(
                _PAPER_ROLE_ALIASES[role]
                for role in tuple(available_roles)
                if role in _PAPER_ROLE_ALIASES
            )
            for ancestor_id in ancestor_ids:
                ancestor = tasks_by_id[ancestor_id]
                available_roles.update(
                    str(item)
                    for item in (
                        (ancestor.get("output_contract") or {}).get("artifact_paths")
                        or {}
                    )
                )

            explicit_nonexecutions = set(
                str(item) for item in task.get("expected_nonexecutions") or ()
            )
            ancestor_nonexecutions = {
                str(item)
                for ancestor_id in ancestor_ids
                for item in tasks_by_id[ancestor_id].get("expected_nonexecutions") or ()
            }
            states: dict[str, str] = {}
            exclusion_reasons: dict[str, list[str]] = {}
            for capability_id in candidates:
                spec = capabilities[capability_id]
                reasons = []
                if bool(spec.get("deprecated")):
                    reasons.append(
                        "capability is deprecated or compatibility-only"
                    )
                if capability_id in explicit_nonexecutions:
                    reasons.append("capability is an explicit nonexecution for this task")
                missing_roles = sorted(
                    _required_asset_roles(spec) - available_roles
                )
                if missing_roles:
                    reasons.append(
                        "required asset roles are unavailable: " + ", ".join(missing_roles)
                    )
                if reasons:
                    states[capability_id] = "structurally_excluded"
                    exclusion_reasons[capability_id] = reasons

            unresolved = {
                item for item in candidates if item not in states
            }
            while unresolved:
                progressed = False
                for capability_id in candidates:
                    if capability_id not in unresolved:
                        continue
                    outcome = _dependency_outcome(
                        capability_id=capability_id,
                        candidates=candidates,
                        capabilities=capabilities,
                        states=states,
                        ancestor_ids=ancestor_ids,
                        records_by_task=records_by_task,
                        explicit_nonexecutions=explicit_nonexecutions,
                        ancestor_nonexecutions=ancestor_nonexecutions,
                    )
                    if outcome[0] == "wait":
                        continue
                    state, reasons = outcome
                    states[capability_id] = state
                    if reasons:
                        exclusion_reasons[capability_id] = reasons
                    unresolved.remove(capability_id)
                    progressed = True
                if progressed:
                    continue
                for capability_id in candidates:
                    if capability_id in unresolved:
                        states[capability_id] = "structurally_excluded"
                        exclusion_reasons[capability_id] = [
                            "dependency closure is cyclic or cannot be satisfied"
                        ]
                unresolved.clear()

            direct = [item for item in candidates if states.get(item) == "advertised_direct"]
            conditional = [
                item for item in candidates if states.get(item) == "advertised_conditional"
            ]
            advertised = [item for item in candidates if item in set(direct + conditional)]
            excluded = [
                {
                    "capability_id": item,
                    "reasons": exclusion_reasons.get(item, ["structurally unavailable"]),
                }
                for item in candidates
                if states.get(item) == "structurally_excluded"
            ]
            record = {
                "workflow_id": workflow_id,
                "task_id": task_id,
                "audited_codeact_fallback": (
                    task.get("execution_mode") == "capability_or_codeact"
                ),
                "candidate_capability_ids": candidates,
                "advertised_direct_capability_ids": direct,
                "advertised_conditional_capability_ids": conditional,
                "advertised_capability_ids": advertised,
                "conditional_capability_ids": conditional,
                "structurally_excluded_capabilities": excluded,
            }
            record["record_hash"] = canonical_hash(record)
            records.append(record)
            records_by_task[task_id] = record

    payload = {
        "schema_version": "pertura-paper-task-capability-availability-v1",
        "task_catalog_hash": canonical_hash(task_catalog),
        "capability_contract_catalog_hash": str(contract_catalog["catalog_hash"]),
        "task_count": len(records),
        "records": records,
    }
    return payload | {"canonical_hash": canonical_hash(payload)}


def availability_by_task(manifest: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(item["task_id"]): dict(item)
        for item in manifest.get("records") or ()
    }


def _ancestor_task_ids(
    task: Mapping[str, Any], tasks_by_id: Mapping[str, Mapping[str, Any]]
) -> tuple[str, ...]:
    ordered: list[str] = []

    def visit(task_id: str) -> None:
        if task_id in ordered:
            return
        ancestor = tasks_by_id.get(task_id)
        if ancestor is None:
            raise ValueError(f"unknown ancestor task: {task_id}")
        for parent in ancestor.get("depends_on_tasks") or ():
            visit(str(parent))
        ordered.append(task_id)

    for dependency in task.get("depends_on_tasks") or ():
        visit(str(dependency))
    return tuple(ordered)


def _required_asset_roles(spec: Mapping[str, Any]) -> set[str]:
    schema = spec.get("parameters_schema") or {}
    properties = schema.get("properties") or {}
    required = set(str(item) for item in schema.get("required") or ())
    return {
        str(field["x-pertura-asset-role"])
        for name, field in properties.items()
        if name in required
        and isinstance(field, Mapping)
        and field.get("x-pertura-asset-role")
    }


def _dependency_outcome(
    *,
    capability_id: str,
    candidates: list[str],
    capabilities: Mapping[str, Mapping[str, Any]],
    states: Mapping[str, str],
    ancestor_ids: tuple[str, ...],
    records_by_task: Mapping[str, Mapping[str, Any]],
    explicit_nonexecutions: set[str],
    ancestor_nonexecutions: set[str],
) -> tuple[str, list[str]]:
    spec = capabilities[capability_id]
    conditional = False
    reasons: list[str] = []
    ancestor_advertised = {
        item
        for task_id in ancestor_ids
        for item in records_by_task[task_id].get("advertised_capability_ids") or ()
    }

    for dependency in spec.get("depends_on") or ():
        dependency = str(dependency)
        if dependency in candidates:
            state = states.get(dependency)
            if state is None:
                return "wait", []
            if state == "structurally_excluded":
                reasons.append(f"required capability is unavailable: {dependency}")
            elif state == "advertised_conditional":
                conditional = True
        elif dependency in ancestor_advertised:
            conditional = True
        else:
            qualifier = (
                " (explicit nonexecution)"
                if dependency in explicit_nonexecutions | ancestor_nonexecutions
                else ""
            )
            reasons.append(f"required capability has no legal producer: {dependency}{qualifier}")

    for group in spec.get("dependency_sets") or ():
        minimum = int(group.get("min_count", 1))
        same_ready: list[str] = []
        same_waiting: list[str] = []
        ancestor_ready: list[str] = []
        for producer_id in candidates:
            if not _matches_dependency_group(capabilities[producer_id], group):
                continue
            state = states.get(producer_id)
            if state in {"advertised_direct", "advertised_conditional"}:
                same_ready.append(producer_id)
                conditional = conditional or state == "advertised_conditional"
            elif state is None:
                same_waiting.append(producer_id)
        for task_id in ancestor_ids:
            record = records_by_task[task_id]
            for producer_id in record.get("advertised_capability_ids") or ():
                if _matches_dependency_group(capabilities[producer_id], group):
                    ancestor_ready.append(producer_id)
        available = len(set(same_ready + ancestor_ready))
        possible = available + len(set(same_waiting) - set(same_ready))
        if available < minimum and possible >= minimum:
            return "wait", []
        if available < minimum:
            reasons.append(
                f"dependency set {group.get('name', 'unnamed')} requires "
                f"{minimum} compatible result receipt(s); {available} can be produced"
            )
        elif ancestor_ready:
            conditional = True

    if reasons:
        return "structurally_excluded", reasons
    return (
        "advertised_conditional" if conditional else "advertised_direct",
        [],
    )


def _matches_dependency_group(
    producer: Mapping[str, Any], group: Mapping[str, Any]
) -> bool:
    result_kinds = set(str(item) for item in group.get("result_kinds") or ())
    source_classes = set(str(item) for item in group.get("source_classes") or ())
    return (
        (not result_kinds or str(producer.get("output_kind")) in result_kinds)
        and (
            not source_classes
            or str(producer.get("source_class")) in source_classes
        )
    )
