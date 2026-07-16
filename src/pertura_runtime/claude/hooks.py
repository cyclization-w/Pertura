from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pertura_runtime.claude.permissions import decide_tool_permission
from pertura_runtime.claude.workspace import ClaudeRunWorkspace


def _append_hook_event(
    workspace: ClaudeRunWorkspace,
    input_data: dict[str, Any],
    tool_use_id: str | None,
) -> None:
    workspace.append_jsonl(
        workspace.logs_dir / "hooks.jsonl",
        {
            "time_utc": datetime.now(timezone.utc).isoformat(),
            "tool_use_id": tool_use_id,
            "hook_event_name": input_data.get("hook_event_name"),
            "tool_name": input_data.get("tool_name"),
            "cwd": input_data.get("cwd"),
        },
    )


def _pre_tool_permission_output(
    workspace: ClaudeRunWorkspace,
    input_data: dict[str, Any],
) -> dict[str, Any]:
    tool_input = input_data.get("tool_input")
    decision = decide_tool_permission(
        workspace=workspace,
        tool_name=str(input_data.get("tool_name") or ""),
        input_data=dict(tool_input) if isinstance(tool_input, dict) else {},
    )
    if decision.allowed:
        return {}
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": decision.reason,
        }
    }


def build_audit_hooks(
    workspace: ClaudeRunWorkspace,
    *,
    log_events: bool = True,
):
    """Build mandatory permission hooks with optional audit logging."""

    from claude_agent_sdk import HookMatcher

    async def pre_tool_hook(
        input_data: dict[str, Any],
        tool_use_id: str | None,
        context: Any,
    ) -> dict[str, Any]:
        if log_events:
            _append_hook_event(workspace, input_data, tool_use_id)
        return _pre_tool_permission_output(workspace, input_data)

    async def post_tool_hook(
        input_data: dict[str, Any],
        tool_use_id: str | None,
        context: Any,
    ) -> dict[str, Any]:
        if log_events:
            _append_hook_event(workspace, input_data, tool_use_id)
        return {}

    hooks = {
        "PreToolUse": [HookMatcher(hooks=[pre_tool_hook])],
    }
    if log_events:
        hooks["PostToolUse"] = [HookMatcher(hooks=[post_tool_hook])]
    return hooks
