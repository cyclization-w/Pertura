"""Generic harness capability references.

These are domain-neutral action references that belong to the Pertura harness
itself. Domain-specific actions such as Perturb-seq DE, guide assignment, QC,
or state-reference building live in their domain pack.
"""

from __future__ import annotations

from pertura.capabilities import CapabilityRef, capability_ref


inspect_workspace = capability_ref(
    "inspect_workspace",
    title="Inspect workspace",
    description="List files and identify likely inputs and prior analysis artifacts.",
    stage="workspace_inspection",
    kind="read",
)
query_observation_memory = capability_ref(
    "query_observation_memory",
    title="Query observation memory",
    description="Retrieve compact variable-level scientific observations from prior attempts.",
    kind="read",
)
trace_upstream = capability_ref(
    "trace_upstream",
    title="Trace upstream",
    description="Trace an observation, artifact, conclusion, or attempt to upstream dependencies.",
    kind="read",
)
compare_branches = capability_ref(
    "compare_branches",
    title="Compare branches",
    description="Compare observations, artifacts, conclusions, or graph changes across branches.",
    kind="read",
)
generate_report = capability_ref(
    "generate_report",
    title="Generate report",
    description="Assemble a report from registered conclusions, evidence paths, artifacts, and limitations.",
    stage="report",
    kind="report",
)


ALL: tuple[CapabilityRef, ...] = (
    inspect_workspace,
    query_observation_memory,
    trace_upstream,
    compare_branches,
    generate_report,
)


def ids() -> list[str]:
    return [item.id for item in ALL]


def by_id(capability_id: str) -> CapabilityRef:
    for item in ALL:
        if item.id == capability_id:
            return item
    raise KeyError(capability_id)


__all__ = [
    "CapabilityRef",
    "ALL",
    "ids",
    "by_id",
    *(item.id for item in ALL),
]
