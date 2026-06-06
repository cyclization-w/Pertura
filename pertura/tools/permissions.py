"""Permission model for tools.

Tiers:
  local_read    - inspect local run/workspace state only.
  external_read - network, web, VLM, or other outside-context reads.
  execute       - runs code or kernel operations. Requires safety check.
  state_change  - modifies graph state. Requires gate.
  privileged    - reserved for administrative operations.

Maps each tool to its tier. The agent loop enforces:
  - read tools: free to call
  - execute tools: go through safety wrapper
  - state_change tools: go through dispatch gate
"""

from __future__ import annotations

from enum import Enum


class ToolPermission(str, Enum):
    local_read = "local_read"
    read = "local_read"  # backwards-compatible alias
    external_read = "external_read"
    execute = "execute"
    state_change = "state_change"
    privileged = "privileged"


# Tool name -> permission tier mapping
_TOOL_TIERS: dict[str, ToolPermission] = {
    # Read tools: inspect state, no mutation
    "query_observations": ToolPermission.local_read,
    "query_observation_memory": ToolPermission.local_read,
    "read_file": ToolPermission.local_read,
    "view_plot": ToolPermission.external_read,
    "search_web": ToolPermission.external_read,
    "list_artifacts": ToolPermission.local_read,
    "compare_branches": ToolPermission.local_read,
    "inspect_artifact_summary": ToolPermission.local_read,
    "trace_upstream": ToolPermission.local_read,
    "review_evidence_chain": ToolPermission.local_read,
    "plan_rethinking": ToolPermission.local_read,
    "impact_of_change": ToolPermission.local_read,
    "list_analysis_nodes": ToolPermission.local_read,
    "list_capabilities": ToolPermission.local_read,
    "get_node_contract": ToolPermission.local_read,
    "get_context_review": ToolPermission.local_read,
    "get_audit_toolbox": ToolPermission.local_read,
    "get_harness_manifest": ToolPermission.local_read,
    "audit_run": ToolPermission.local_read,
    "get_capability_template": ToolPermission.local_read,
    "evaluate_node_conditions": ToolPermission.local_read,
    "load_dataset": ToolPermission.local_read,

    # Execute tools: run code or kernel operations
    "execute_code": ToolPermission.execute,
    "submit_job": ToolPermission.execute,
    "retry": ToolPermission.execute,
    "sweep_thresholds": ToolPermission.local_read,
    "compare_methods": ToolPermission.local_read,

    # State-change tools: modify graph topology
    "open_branch": ToolPermission.state_change,
    "close_branch": ToolPermission.state_change,
    "switch_branch": ToolPermission.state_change,
    "ask_user": ToolPermission.state_change,
    "finish": ToolPermission.state_change,
    "request_node_transition": ToolPermission.state_change,
    "update_design": ToolPermission.state_change,
    "complete_node": ToolPermission.state_change,
    "skip_node": ToolPermission.state_change,
}


def tool_permission(tool_name: str) -> ToolPermission:
    """Return the permission tier for a core runtime tool."""
    return _TOOL_TIERS.get(tool_name, ToolPermission.local_read)


def tool_catalog(*, readonly: bool = False) -> list[dict]:
    """Return a developer-facing catalog of core runtime tools.

    This catalog intentionally describes tools, not capabilities. Capabilities
    are domain actions such as `run_de`; tools are runtime primitives such as
    `execute_code` or `get_context_review`.
    """
    from pertura.tools.registry import TOOLS

    items = []
    for name in sorted(TOOLS):
        tier = tool_permission(name)
        if readonly and tier != ToolPermission.local_read:
            continue
        spec = TOOLS[name]
        items.append({
            "tool_id": name,
            "permission": tier.value,
            "description": spec.get("description", ""),
            "required": list((spec.get("parameters", {}) or {}).get("required", []) or []),
        })
    return items


def check_permission(tool_name: str, target_tier: ToolPermission) -> bool:
    """Check whether a tool is allowed at the given permission tier.

    read tools can be called at any tier.
    execute tools can be called at execute or state_change tiers.
    state_change tools can only be called at state_change tier.
    """
    tool_tier = _TOOL_TIERS.get(tool_name, ToolPermission.local_read)
    tiers = list(ToolPermission)
    return tiers.index(tool_tier) <= tiers.index(target_tier)
