from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pertura_runtime.claude.permissions import decide_tool_permission
from pertura_runtime.claude.workspace import ClaudeRunWorkspace


_EXPENSIVE_TOOLS = {
    "Bash",
    "NotebookEdit",
    "Read",
    "Glob",
    "Grep",
}
_CLOSURE_READ_TOOLS = {"Read", "Glob", "Grep"}
_CLOSURE_WRITE_TOOLS = {"Write", "Edit", "MultiEdit"}


@dataclass
class CompletionGuard:
    """Task-scoped benchmark guard that reserves a deterministic closeout."""

    workspace: ClaudeRunWorkspace
    output_root: Path
    exploration_limit: int = 24
    completion_read_limit: int = 2
    expensive_calls: int = 0
    closure_calls: int = 0
    completion_reads: int = 0
    denied_calls: int = 0
    triggered: bool = False
    trigger_tool: str | None = None

    def inspect(self, tool_name: str, tool_input: dict[str, Any]) -> str | None:
        if self.triggered:
            return self._closure_decision(tool_name, tool_input)
        if not _is_expensive_tool(tool_name):
            return None
        if self.expensive_calls >= self.exploration_limit:
            self.triggered = True
            self.trigger_tool = tool_name
            self.denied_calls += 1
            return self._finish_reason()
        self.expensive_calls += 1
        return None

    def _closure_decision(
        self, tool_name: str, tool_input: dict[str, Any]
    ) -> str | None:
        if tool_name in _CLOSURE_WRITE_TOOLS:
            if not _writes_under(
                tool_name,
                tool_input,
                self.output_root,
                self.workspace.root,
            ):
                self.denied_calls += 1
                return (
                    "Completion mode permits writes only under the current task "
                    f"output directory: {self.output_root}"
                )
            self.closure_calls += 1
            return None
        if tool_name in _CLOSURE_READ_TOOLS:
            if self.completion_reads < self.completion_read_limit:
                self.completion_reads += 1
                self.closure_calls += 1
                return None
            self.denied_calls += 1
            return self._finish_reason()
        self.denied_calls += 1
        return self._finish_reason()

    def _finish_reason(self) -> str:
        return (
            "The controlled exploration budget is exhausted. Stop scientific "
            "execution and repository/environment inspection. Use existing task "
            "artifacts, write or update benchmark_result.json under the current "
            "task output directory, then return the required TurnDraft JSON."
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "pertura-benchmark-completion-guard-v1",
            "enabled": True,
            "exploration_limit": self.exploration_limit,
            "completion_read_limit": self.completion_read_limit,
            "expensive_calls": self.expensive_calls,
            "closure_calls": self.closure_calls,
            "completion_reads": self.completion_reads,
            "denied_calls": self.denied_calls,
            "triggered": self.triggered,
            "trigger_tool": self.trigger_tool,
            "output_root": str(self.output_root),
        }


def _is_expensive_tool(tool_name: str) -> bool:
    return tool_name in _EXPENSIVE_TOOLS or tool_name.startswith("mcp__pertura__")


def _writes_under(
    tool_name: str,
    tool_input: dict[str, Any],
    output_root: Path,
    workspace_root: Path,
) -> bool:
    fields = {
        "Write": ("file_path", "path"),
        "Edit": ("file_path", "path"),
        "MultiEdit": ("file_path", "path"),
    }.get(tool_name, ())
    raw = next(
        (tool_input.get(field) for field in fields if tool_input.get(field)),
        None,
    )
    if not raw:
        return False
    path = Path(str(raw)).expanduser()
    resolved = (
        path.resolve()
        if path.is_absolute()
        else (workspace_root / path).resolve()
    )
    root = output_root.expanduser().resolve()
    try:
        resolved.relative_to(root)
        return resolved != root
    except ValueError:
        return False


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
    *,
    allow_background_bash: bool = True,
    completion_guard: CompletionGuard | None = None,
) -> dict[str, Any]:
    tool_input = input_data.get("tool_input")
    decision = decide_tool_permission(
        workspace=workspace,
        tool_name=str(input_data.get("tool_name") or ""),
        input_data=dict(tool_input) if isinstance(tool_input, dict) else {},
        allow_background_bash=allow_background_bash,
    )
    if decision.allowed:
        if completion_guard is not None:
            reason = completion_guard.inspect(
                str(input_data.get("tool_name") or ""),
                dict(tool_input) if isinstance(tool_input, dict) else {},
            )
            if reason:
                return {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "deny",
                        "permissionDecisionReason": reason,
                    }
                }
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
    allow_background_bash: bool = True,
    completion_guard: CompletionGuard | None = None,
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
        return _pre_tool_permission_output(
            workspace,
            input_data,
            allow_background_bash=allow_background_bash,
            completion_guard=completion_guard,
        )

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
