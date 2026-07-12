from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from pertura_core import PromotionPolicy
from pertura_runtime.agent_bundle import resolve_skill_configuration
from pertura_runtime.claude.hooks import build_audit_hooks
from pertura_runtime.claude.permissions import build_input_readonly_guard
from pertura_runtime.claude.tools.product_tools import (
    PRODUCT_TOOL_NAMES,
    create_product_mcp_server,
)
from pertura_runtime.claude.workspace import ClaudeRunWorkspace
from pertura_runtime.network_policy import NetworkAccessPolicy
from pertura_runtime.product import PerturaProductRuntime


DEFAULT_CODEACT_ALLOWED_TOOLS = [
    "Read",
    "Glob",
    "Grep",
    "Bash",
    "Write",
    "Edit",
    "MultiEdit",
    "NotebookEdit",
    *(f"mcp__pertura__{name}" for name in PRODUCT_TOOL_NAMES),
]


@dataclass(frozen=True)
class ClaudeRuntimeOptions:
    model: str | None = None
    resume_session_id: str | None = None
    permission_mode: str = "default"
    max_turns: int | None = 20
    max_budget_usd: float | None = None
    enable_audit_hooks: bool = True
    include_hook_events: bool = False
    setting_sources: list[str] = field(default_factory=list)
    disallowed_tools: list[str] = field(
        default_factory=lambda: ["WebFetch", "WebSearch"]
    )
    allowed_tools: list[str] = field(
        default_factory=lambda: list(DEFAULT_CODEACT_ALLOWED_TOOLS)
    )
    enable_bundled_skills: bool = True
    additional_skill_plugins: tuple[Path, ...] = ()
    allow_literature_network: bool = False
    env: dict[str, str] = field(default_factory=dict)
    python_exe: str | None = None
    python_preflight_timeout_s: float = 240.0
    python_preflight_packages: list[str] | None = None
    interaction_mode: str = "benchmark"
    stage_id: str | None = None
    tool_surface: str = "capability"
    # The claim policy is a run-level trust decision. It is deliberately not
    # exposed as an MCP tool argument, so CodeAct cannot weaken it mid-run.
    policy_profile: str = "strict"


def build_agent_options(
    *,
    workspace: ClaudeRunWorkspace,
    system_prompt: str,
    config: ClaudeRuntimeOptions,
    product_runtime: PerturaProductRuntime | None = None,
):
    """Build ClaudeAgentOptions with Pertura's bundled provider adapter."""

    from claude_agent_sdk import ClaudeAgentOptions

    hooks = build_audit_hooks(workspace) if config.enable_audit_hooks else None
    policy = runtime_policy(config)
    env = {
        "CLAUDE_CODE_MAX_RETRIES": "2",
        "CLAUDE_ASYNC_AGENT_STALL_TIMEOUT_MS": "120000",
        "PERTURA_REPO_ROOT": str(Path(__file__).resolve().parents[3]),
    }
    env.update(config.env)
    if config.tool_surface != "capability":
        raise ValueError(
            "the legacy tool surface is read-only and unavailable in the "
            "production Claude runtime"
        )
    if (
        product_runtime is not None
        and product_runtime.promotion_policy.policy_hash != policy.policy_hash
    ):
        raise ValueError(
            "Claude runtime policy conflicts with the workspace-bound product policy"
        )
    runtime = product_runtime or PerturaProductRuntime(
        workspace,
        policy=policy,
        network_policy=(
            NetworkAccessPolicy.literature_europepmc()
            if config.allow_literature_network
            else NetworkAccessPolicy.offline()
        ),
    )
    mcp_servers = {"pertura": create_product_mcp_server(runtime)}
    skill_config = resolve_skill_configuration(
        enable_bundled=config.enable_bundled_skills,
        additional_plugin_paths=config.additional_skill_plugins,
    )
    workspace.update_manifest(skill_config.provenance)
    requested = {
        "cwd": str(workspace.root),
        "model": config.model,
        "resume": config.resume_session_id,
        "system_prompt": system_prompt,
        "tools": {"type": "preset", "preset": "claude_code"},
        "allowed_tools": list(config.allowed_tools),
        "disallowed_tools": list(config.disallowed_tools),
        "mcp_servers": mcp_servers,
        "strict_mcp_config": True,
        "permission_mode": config.permission_mode,
        "max_turns": config.max_turns,
        "max_budget_usd": config.max_budget_usd,
        "can_use_tool": build_input_readonly_guard(workspace),
        "hooks": hooks,
        "include_hook_events": config.include_hook_events,
        "setting_sources": list(config.setting_sources),
        "plugins": list(skill_config.plugins) if skill_config.plugins else None,
        "skills": list(skill_config.skill_names) if skill_config.skill_names else [],
        "env": env,
    }
    required = {"plugins", "skills"} if skill_config.skill_names else set()
    return ClaudeAgentOptions(
        **_supported_options(
            ClaudeAgentOptions,
            requested,
            required_fields=required,
        )
    )


def _supported_options(
    options_cls: type,
    requested: dict[str, Any],
    *,
    required_fields: Iterable[str] = (),
) -> dict[str, Any]:
    """Filter optional SDK kwargs and fail when a requested feature is absent."""

    if dataclasses.is_dataclass(options_cls):
        field_names = {item.name for item in dataclasses.fields(options_cls)}
    else:
        field_names = set(getattr(options_cls, "__annotations__", {}).keys())
    required = set(required_fields)
    if field_names:
        missing = sorted(required.difference(field_names))
        if missing:
            raise RuntimeError(
                "installed claude-agent-sdk lacks required Pertura skill "
                f"options: {', '.join(missing)}; install "
                "'claude-agent-sdk>=0.1.62,<0.3'"
            )
        return {
            key: value
            for key, value in requested.items()
            if key in field_names and value is not None
        }
    return {key: value for key, value in requested.items() if value is not None}


def describe_options(config: ClaudeRuntimeOptions) -> dict[str, Any]:
    policy = runtime_policy(config)
    skill_config = resolve_skill_configuration(
        enable_bundled=config.enable_bundled_skills,
        additional_plugin_paths=config.additional_skill_plugins,
    )
    return {
        "model": config.model,
        "resume_session_id": config.resume_session_id,
        "permission_mode": config.permission_mode,
        "max_turns": config.max_turns,
        "max_budget_usd": config.max_budget_usd,
        "enable_audit_hooks": config.enable_audit_hooks,
        "include_hook_events": config.include_hook_events,
        "setting_sources": list(config.setting_sources),
        "disallowed_tools": list(config.disallowed_tools),
        "allowed_tools": list(config.allowed_tools),
        "enable_bundled_skills": config.enable_bundled_skills,
        "allow_literature_network": config.allow_literature_network,
        "available_skills": list(skill_config.skill_names),
        "skill_bundle_hash": skill_config.provenance["skill_bundle_hash"],
        "additional_skill_plugin_hashes": skill_config.provenance[
            "additional_skill_plugin_hashes"
        ],
        "python_exe": config.python_exe,
        "python_preflight_timeout_s": config.python_preflight_timeout_s,
        "python_preflight_packages": (
            list(config.python_preflight_packages)
            if config.python_preflight_packages is not None
            else None
        ),
        "interaction_mode": config.interaction_mode,
        "stage_id": config.stage_id,
        "tool_surface": config.tool_surface,
        "policy_profile": policy.profile,
        "policy_hash": policy.policy_hash,
        "env_keys": sorted(config.env),
    }


def runtime_policy(config: ClaudeRuntimeOptions) -> PromotionPolicy:
    """Resolve and validate the immutable policy selected for one SDK run."""

    return PromotionPolicy(profile=config.policy_profile)
