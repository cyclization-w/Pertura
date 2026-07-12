from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

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


class ProviderSessionAdapter(Protocol):
    """Continuation contract shared by Claude and future OpenAI adapters."""

    async def start_or_resume_turn(self, user_message: str, **context: Any) -> Any: ...

    async def repair_turn_draft(self, raw_output: str, error: str, **context: Any) -> str: ...

    async def cancel_turn(self, turn_id: str) -> None: ...

    async def close(self) -> None: ...
