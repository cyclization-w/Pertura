from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pertura_runtime.claude.workspace import ClaudeRunWorkspace


WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}
WRITE_PATH_FIELDS = {
    "Write": ["file_path", "path"],
    "Edit": ["file_path", "path"],
    "MultiEdit": ["file_path", "path"],
    "NotebookEdit": ["notebook_path", "file_path", "path"],
}
BASH_MUTATION_PATTERNS = [
    r"\brm\s+",
    r"\brmdir\b",
    r"\bdel\s+",
    r"\bmove\s+",
    r"\bmv\s+",
    r"\bRemove-Item\b",
    r"\bMove-Item\b",
    r">",
    r">>",
    r"\btee\b",
    r"\bOut-File\b",
    r"\bSet-Content\b",
    r"\bAdd-Content\b",
    r"\bNew-Item\b",
    r"\bCopy-Item\b",
    r"\bcp\s+",
    r"\bpython\b.*\bopen\s*\(",
]

# Claude may create source artifacts and claims, while only runtime/MCP code
# may mutate these trust-bearing files and calibrated final surfaces.
RUNTIME_OWNED_RELATIVE_PATHS = (
    Path("manifest.json"),
    Path("artifacts/evidence_artifacts.jsonl"),
    Path("artifacts/execution_ledger.jsonl"),
    Path("artifacts/claim_decisions.json"),
    Path("artifacts/analysis_state_manifest.json"),
    Path("artifacts/turn_final.json"),
    Path("reports/evidence_report.md"),
    Path("reports/turn_final.md"),
    Path("reports/pertura_final.md"),
)
RUNTIME_OWNED_RELATIVE_DIRECTORIES = (Path("reports"),)


@dataclass(frozen=True)
class PermissionDecision:
    allowed: bool
    reason: str = ""
    updated_input: dict[str, Any] | None = None


def decide_tool_permission(
    *,
    workspace: ClaudeRunWorkspace,
    tool_name: str,
    input_data: dict[str, Any],
    allow_background_bash: bool = True,
) -> PermissionDecision:
    """Pure permission decision used by the SDK callback and unit tests."""

    if tool_name in WRITE_TOOLS:
        for field in WRITE_PATH_FIELDS.get(tool_name, []):
            raw_path = input_data.get(field)
            if raw_path and _is_runtime_owned_path(workspace, Path(str(raw_path))):
                return PermissionDecision(
                    allowed=False,
                    reason=f"{tool_name} cannot write runtime-owned trust state: {raw_path}",
                )
            if raw_path and _is_protected_input_path(workspace, Path(str(raw_path))):
                return PermissionDecision(
                    allowed=False,
                    reason=f"{tool_name} cannot write under read-only input path: {raw_path}",
                )
    if tool_name == "Bash":
        command = str(input_data.get("command") or "")
        if not allow_background_bash and _requests_background_bash(
            input_data, command
        ):
            return PermissionDecision(
                allowed=False,
                reason=(
                    "Background Bash execution is disabled for controlled "
                    "benchmark turns; run the command synchronously."
                ),
            )
        if _looks_like_runtime_state_mutation_command(workspace, command):
            return PermissionDecision(
                allowed=False,
                reason="Bash command appears to mutate runtime-owned trust state.",
            )
        if _looks_like_input_mutation_command(workspace, command):
            return PermissionDecision(
                allowed=False,
                reason="Bash command appears to mutate the read-only input path.",
            )
    return PermissionDecision(allowed=True, updated_input=input_data)


def _requests_background_bash(
    input_data: dict[str, Any], command: str
) -> bool:
    if input_data.get("run_in_background") is True:
        return True
    if re.search(r"\b(?:nohup|disown|setsid)\b", command):
        return True
    return bool(re.search(r"(?<!&)\&\s*(?:$|;)", command))


def build_input_readonly_guard(workspace: ClaudeRunWorkspace):
    """Build a Claude Agent SDK can_use_tool callback."""

    async def can_use_tool(tool_name: str, input_data: dict[str, Any], context: Any):
        from claude_agent_sdk.types import PermissionResultAllow, PermissionResultDeny

        decision = decide_tool_permission(
            workspace=workspace,
            tool_name=tool_name,
            input_data=input_data,
        )
        if decision.allowed:
            return PermissionResultAllow(updated_input=decision.updated_input or input_data)
        return PermissionResultDeny(message=decision.reason, interrupt=True)

    return can_use_tool


def _is_protected_input_path(workspace: ClaudeRunWorkspace, path: Path) -> bool:
    candidates = [workspace.input_dir]
    if workspace.input_source is not None:
        candidates.append(workspace.input_source)
    resolved = _resolve_user_path(workspace.root, path)
    for candidate in candidates:
        if _is_relative_to(resolved, candidate):
            return True
    return False


def _is_runtime_owned_path(workspace: ClaudeRunWorkspace, path: Path) -> bool:
    resolved = _resolve_user_path(workspace.root, path)
    if any(resolved == (workspace.root / relative).resolve() for relative in RUNTIME_OWNED_RELATIVE_PATHS):
        return True
    return any(
        _is_relative_to(resolved, workspace.root / relative)
        for relative in RUNTIME_OWNED_RELATIVE_DIRECTORIES
    )


def _resolve_user_path(cwd: Path, path: Path) -> Path:
    if path.is_absolute():
        return path.expanduser().resolve()
    return (cwd / path).expanduser().resolve()


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent.expanduser().resolve())
        return True
    except ValueError:
        return False


def _looks_like_input_mutation_command(workspace: ClaudeRunWorkspace, command: str) -> bool:
    if not any(re.search(pattern, command, flags=re.IGNORECASE | re.DOTALL) for pattern in BASH_MUTATION_PATTERNS):
        return False
    for path_text in _extract_command_paths(command):
        try:
            if _is_protected_input_path(workspace, Path(path_text)):
                return True
        except OSError:
            continue
    return _mentions_protected_path(workspace, command)


def _looks_like_runtime_state_mutation_command(workspace: ClaudeRunWorkspace, command: str) -> bool:
    if not any(re.search(pattern, command, flags=re.IGNORECASE | re.DOTALL) for pattern in BASH_MUTATION_PATTERNS):
        return False
    for path_text in _extract_command_paths(command):
        try:
            if _is_runtime_owned_path(workspace, Path(path_text)):
                return True
        except OSError:
            continue
    normalized = command.lower().replace("\\", "/")
    if any(str(path).lower().replace("\\", "/") + "/" in normalized for path in RUNTIME_OWNED_RELATIVE_DIRECTORIES):
        return True
    return any(
        str(path).lower().replace("\\", "/") in normalized
        for path in RUNTIME_OWNED_RELATIVE_PATHS
    )


def _extract_command_paths(command: str) -> list[str]:
    paths: list[str] = []
    try:
        parts = shlex.split(command, posix=False)
    except ValueError:
        parts = command.split()
    for index, part in enumerate(parts):
        token = part.strip().strip("'\"")
        if token in {">", ">>"} and index + 1 < len(parts):
            paths.append(parts[index + 1].strip().strip("'\""))
            continue
        if token.startswith(">") and len(token) > 1:
            paths.append(token.lstrip(">").strip().strip("'\""))
            continue
        if "\\" in token or "/" in token:
            paths.append(token)
    return [path for path in paths if path]


def _mentions_protected_path(workspace: ClaudeRunWorkspace, command: str) -> bool:
    protected_tokens = [
        str(workspace.input_dir),
        "input/",
        "input\\",
        "input/project",
        "input\\project",
    ]
    if workspace.input_source is not None:
        protected_tokens.append(str(workspace.input_source))
    lower_command = command.lower()
    return any(token and token.lower() in lower_command for token in protected_tokens)
