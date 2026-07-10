from pathlib import Path

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
