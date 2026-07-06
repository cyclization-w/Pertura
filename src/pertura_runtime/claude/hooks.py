from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pertura_runtime.claude.workspace import ClaudeRunWorkspace


def build_audit_hooks(workspace: ClaudeRunWorkspace):
    """Build lightweight SDK hooks for v0 audit logging."""

    from claude_agent_sdk import HookMatcher

    async def log_hook(input_data: dict[str, Any], tool_use_id: str | None, context: Any) -> dict[str, Any]:
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
        return {}

    return {
        "PreToolUse": [HookMatcher(hooks=[log_hook])],
        "PostToolUse": [HookMatcher(hooks=[log_hook])],
    }