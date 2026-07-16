from pathlib import Path

from pertura_runtime.claude.hooks import _pre_tool_permission_output
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
