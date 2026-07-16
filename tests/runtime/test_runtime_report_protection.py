from pathlib import Path

from pertura_runtime.claude.hooks import (
    CompletionGuard,
    _pre_tool_permission_output,
)
from pertura_runtime.claude.permissions import decide_tool_permission
from pertura_runtime.claude.workspace import ClaudeRunWorkspace


def test_runtime_blocks_arbitrary_agent_written_report_paths(tmp_path: Path) -> None:
    workspace = ClaudeRunWorkspace.create(root=tmp_path / "runs", run_id="run1")
    write = decide_tool_permission(
        workspace=workspace,
        tool_name="Write",
        input_data={"file_path": "reports/agent_forged_evidence_report.md"},
    )
    bash = decide_tool_permission(
        workspace=workspace,
        tool_name="Bash",
        input_data={"command": "echo '## Runtime-calibrated findings' > reports/forged.md"},
    )
    assert write.allowed is False
    assert bash.allowed is False


def test_pre_tool_hook_enforces_input_readonly_policy(tmp_path: Path) -> None:
    workspace = ClaudeRunWorkspace.create(root=tmp_path / "runs", run_id="hook")
    protected = workspace.input_dir / "dataset.h5ad"
    protected.parent.mkdir(parents=True, exist_ok=True)
    protected.write_bytes(b"fixture")

    denied = _pre_tool_permission_output(
        workspace,
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "Write",
            "tool_input": {
                "file_path": str(protected),
                "content": "changed",
            },
        },
    )
    hook_output = denied["hookSpecificOutput"]
    assert hook_output["permissionDecision"] == "deny"
    assert "read-only input" in hook_output["permissionDecisionReason"]

    allowed = _pre_tool_permission_output(
        workspace,
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "Write",
            "tool_input": {
                "file_path": str(workspace.root / "outputs" / "result.tsv"),
                "content": "ok",
            },
        },
    )
    assert allowed == {}


def test_benchmark_pre_tool_hook_requires_synchronous_bash(
    tmp_path: Path,
) -> None:
    workspace = ClaudeRunWorkspace.create(
        root=tmp_path / "runs", run_id="synchronous"
    )
    denied = _pre_tool_permission_output(
        workspace,
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {
                "command": "Rscript analysis.R",
                "run_in_background": True,
            },
        },
        allow_background_bash=False,
    )
    assert (
        denied["hookSpecificOutput"]["permissionDecision"] == "deny"
    )
    assert "synchronously" in denied["hookSpecificOutput"][
        "permissionDecisionReason"
    ]

    shell_background = _pre_tool_permission_output(
        workspace,
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "Rscript analysis.R &"},
        },
        allow_background_bash=False,
    )
    assert shell_background["hookSpecificOutput"][
        "permissionDecision"
    ] == "deny"

    synchronous = _pre_tool_permission_output(
        workspace,
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "Rscript analysis.R"},
        },
        allow_background_bash=False,
    )
    assert synchronous == {}


def test_completion_guard_reserves_task_scoped_closeout(tmp_path: Path) -> None:
    workspace = ClaudeRunWorkspace.create(
        root=tmp_path / "runs", run_id="completion-guard"
    )
    task_root = workspace.root / "outputs" / "tasks" / "PAPA-01"
    task_root.mkdir(parents=True)
    guard = CompletionGuard(
        workspace=workspace,
        output_root=task_root,
        exploration_limit=2,
        completion_read_limit=2,
    )

    def invoke(tool_name: str, tool_input: dict) -> dict:
        return _pre_tool_permission_output(
            workspace,
            {
                "hook_event_name": "PreToolUse",
                "tool_name": tool_name,
                "tool_input": tool_input,
            },
            allow_background_bash=False,
            completion_guard=guard,
        )

    assert invoke("Bash", {"command": "echo first"}) == {}
    assert invoke("mcp__pertura__run_diagnostic", {}) == {}
    trigger = invoke("Read", {"file_path": "existing.tsv"})
    assert trigger["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert guard.triggered is True

    assert invoke("Read", {"file_path": "existing.tsv"}) == {}
    assert invoke("Grep", {"pattern": "x", "path": "."}) == {}
    extra_read = invoke("Glob", {"pattern": "*.tsv"})
    assert extra_read["hookSpecificOutput"]["permissionDecision"] == "deny"

    inside = invoke(
        "Write",
        {
            "file_path": str(task_root / "benchmark_result.json"),
            "content": "{}",
        },
    )
    outside = invoke(
        "Write",
        {
            "file_path": str(workspace.root / "outputs" / "wrong.json"),
            "content": "{}",
        },
    )
    scientific = invoke("mcp__pertura__run_analysis", {})

    assert inside == {}
    assert outside["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert scientific["hookSpecificOutput"]["permissionDecision"] == "deny"
    snapshot = guard.to_dict()
    assert snapshot["expensive_calls"] == 2
    assert snapshot["completion_reads"] == 2
    assert snapshot["closure_calls"] == 3
    assert snapshot["trigger_tool"] == "Read"
