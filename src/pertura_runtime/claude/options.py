from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pertura_runtime.claude.hooks import build_audit_hooks
from pertura_runtime.claude.tools.evidence_tools import create_evidence_mcp_server
from pertura_runtime.claude.permissions import build_input_readonly_guard
from pertura_runtime.claude.workspace import ClaudeRunWorkspace


DEFAULT_CODEACT_ALLOWED_TOOLS = [
    "Read",
    "Glob",
    "Grep",
    "Bash",
    "Write",
    "Edit",
    "MultiEdit",
    "NotebookEdit",
    "mcp__pertura_evidence__register_perturbation_design_manifest",
    "mcp__pertura_evidence__register_experiment_design_artifact",
    "mcp__pertura_evidence__register_guide_assignment_artifact",
    "mcp__pertura_evidence__register_target_qc_artifact",
    "mcp__pertura_evidence__register_measured_de_artifact",
    "mcp__pertura_evidence__register_predicted_effect_artifact",
    "mcp__pertura_evidence__register_curated_prior_artifact",
    "mcp__pertura_evidence__register_perturbation_efficiency_artifact",
    "mcp__pertura_evidence__register_curated_enrichment_artifact",
    "mcp__pertura_evidence__register_module_effect_artifact",
    "mcp__pertura_evidence__register_global_effect_artifact",
    "mcp__pertura_evidence__register_cell_state_reference_artifact",
    "mcp__pertura_evidence__register_cell_qc_artifact",
    "mcp__pertura_evidence__register_replication_artifact",
    "mcp__pertura_evidence__evaluate_claims",
    "mcp__pertura_evidence__render_evidence_report",
]


@dataclass(frozen=True)
class ClaudeRuntimeOptions:
    model: str | None = None
    permission_mode: str = "default"
    max_turns: int | None = 20
    max_budget_usd: float | None = None
    enable_audit_hooks: bool = True
    include_hook_events: bool = False
    setting_sources: list[str] = field(default_factory=list)
    disallowed_tools: list[str] = field(default_factory=lambda: ["WebFetch", "WebSearch"])
    allowed_tools: list[str] = field(default_factory=lambda: list(DEFAULT_CODEACT_ALLOWED_TOOLS))
    env: dict[str, str] = field(default_factory=dict)
    python_exe: str | None = None
    python_preflight_timeout_s: float = 240.0
    interaction_mode: str = "benchmark"
    stage_id: str | None = None


def build_agent_options(
    *,
    workspace: ClaudeRunWorkspace,
    system_prompt: str,
    config: ClaudeRuntimeOptions,
):
    """Build ClaudeAgentOptions with all future extension points in one place."""

    from claude_agent_sdk import ClaudeAgentOptions

    hooks = build_audit_hooks(workspace) if config.enable_audit_hooks else None
    env = {
        "CLAUDE_CODE_MAX_RETRIES": "2",
        "CLAUDE_ASYNC_AGENT_STALL_TIMEOUT_MS": "120000",
        "PERTURA_REPO_ROOT": str(Path(__file__).resolve().parents[3]),
    }
    env.update(config.env)
    requested = {
        "cwd": str(workspace.root),
        "model": config.model,
        "system_prompt": system_prompt,
        "tools": {"type": "preset", "preset": "claude_code"},
        "allowed_tools": list(config.allowed_tools),
        "disallowed_tools": list(config.disallowed_tools),
        "mcp_servers": {"pertura_evidence": create_evidence_mcp_server(workspace)},
        "strict_mcp_config": True,
        "permission_mode": config.permission_mode,
        "max_turns": config.max_turns,
        "max_budget_usd": config.max_budget_usd,
        "can_use_tool": build_input_readonly_guard(workspace),
        "hooks": hooks,
        "include_hook_events": config.include_hook_events,
        "setting_sources": list(config.setting_sources),
        "env": env,
    }
    return ClaudeAgentOptions(**_supported_options(ClaudeAgentOptions, requested))


def _supported_options(options_cls: type, requested: dict[str, Any]) -> dict[str, Any]:
    """Filter ClaudeAgentOptions kwargs to the installed SDK version."""

    if dataclasses.is_dataclass(options_cls):
        field_names = {field.name for field in dataclasses.fields(options_cls)}
    else:
        field_names = set(getattr(options_cls, "__annotations__", {}).keys())
    if not field_names:
        return {key: value for key, value in requested.items() if value is not None}
    return {
        key: value
        for key, value in requested.items()
        if key in field_names and value is not None
    }


def describe_options(config: ClaudeRuntimeOptions) -> dict[str, Any]:
    return {
        "model": config.model,
        "permission_mode": config.permission_mode,
        "max_turns": config.max_turns,
        "max_budget_usd": config.max_budget_usd,
        "enable_audit_hooks": config.enable_audit_hooks,
        "include_hook_events": config.include_hook_events,
        "setting_sources": list(config.setting_sources),
        "disallowed_tools": list(config.disallowed_tools),
        "allowed_tools": list(config.allowed_tools),
        "python_exe": config.python_exe,
        "python_preflight_timeout_s": config.python_preflight_timeout_s,
        "interaction_mode": config.interaction_mode,
        "stage_id": config.stage_id,
        "env_keys": sorted(config.env),
    }









