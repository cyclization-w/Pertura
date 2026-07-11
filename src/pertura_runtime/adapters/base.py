from __future__ import annotations

from dataclasses import dataclass

from pertura_runtime.product_tools import ProductToolSpec


@dataclass(frozen=True)
class ProviderSurface:
    """Import-safe description of an agent-provider integration."""

    provider_id: str
    implemented: bool
    skill_names: tuple[str, ...]
    tool_specs: tuple[ProductToolSpec, ...]
    codeact_capabilities: tuple[str, ...]
    required_extra: str | None = None
