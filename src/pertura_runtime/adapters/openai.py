from __future__ import annotations

from dataclasses import dataclass
from importlib.util import find_spec
from pathlib import Path
from typing import Any, Iterable

from pertura_runtime.adapters.base import ProviderSurface
from pertura_runtime.agent_bundle import BUNDLED_SKILL_NAMES, agent_bundle_root
from pertura_runtime.product_tools import PRODUCT_TOOL_SPECS


OPENAI_PROVIDER_ID = "openai-agents-sdk"


@dataclass(frozen=True)
class OpenAIAdapterDescriptor:
    """Future OpenAI Agents SDK adapter contract.

    This release intentionally provides schemas and instructions only. It does
    not construct an Agent, start an MCP transport, or call the Responses API.
    """

    provider_id: str = OPENAI_PROVIDER_ID
    responses_api: bool = True
    planned_tool_transport: str = "stdio-or-streamable-http-mcp"
    implemented: bool = False
    required_extra: str = "openai-agents"


def provider_surface() -> ProviderSurface:
    return ProviderSurface(
        provider_id=OPENAI_PROVIDER_ID,
        implemented=False,
        skill_names=BUNDLED_SKILL_NAMES,
        tool_specs=PRODUCT_TOOL_SPECS,
        codeact_capabilities=(
            "workspace-file-read",
            "workspace-file-write",
            "shell",
            "python",
            "r",
            "notebook",
        ),
        required_extra="openai-agents",
    )


def openai_function_schemas() -> tuple[dict[str, Any], ...]:
    """Project the frozen five-tool surface into Responses function schemas."""

    return tuple(
        {
            "type": "function",
            "name": spec.name,
            "description": spec.description,
            "parameters": spec.json_input_schema(),
        }
        for spec in PRODUCT_TOOL_SPECS
    )


def _skill_body(skill_file: Path) -> str:
    text = skill_file.read_text(encoding="utf-8")
    parts = text.split("---", 2)
    if len(parts) != 3:
        raise ValueError(f"invalid skill frontmatter: {skill_file}")
    return parts[2].strip()


def build_openai_dynamic_instructions(
    selected_skills: Iterable[str],
) -> str:
    """Load selected provider-neutral skill bodies for a future Agent callback."""

    requested = tuple(dict.fromkeys(str(name) for name in selected_skills))
    unknown = sorted(set(requested).difference(BUNDLED_SKILL_NAMES))
    if unknown:
        raise ValueError("unknown Pertura skills: " + ", ".join(unknown))
    sections = []
    skill_root = agent_bundle_root() / "skills"
    for name in requested:
        body = _skill_body(skill_root / name / "SKILL.md")
        sections.append(
            f"## Loaded Pertura skill: {name}\n\n{body}"
        )
    return "\n\n".join(sections)


def openai_adapter_status() -> dict[str, Any]:
    dependency_available = find_spec("agents") is not None
    return {
        "provider_id": OPENAI_PROVIDER_ID,
        "implemented": False,
        "environment_ready": False,
        "dependency_available": dependency_available,
        "required_extra": "openai-agents",
        "tool_names": [spec.name for spec in PRODUCT_TOOL_SPECS],
        "skill_names": list(BUNDLED_SKILL_NAMES),
        "reason": (
            "OpenAI Agents SDK execution is intentionally not implemented in "
            "Pertura 0.2.0a5"
        ),
    }
