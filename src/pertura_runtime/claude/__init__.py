"""Claude CodeAct integration with lazy runtime exports."""

from __future__ import annotations

from importlib import import_module
from typing import Any

_EXPORTS = {
    "ClaudePerturaAgent": ("pertura_runtime.claude.agent", "ClaudePerturaAgent"),
    "ClaudeRunResult": ("pertura_runtime.claude.agent", "ClaudeRunResult"),
    "ClaudeRunWorkspace": ("pertura_runtime.claude.workspace", "ClaudeRunWorkspace"),
}

__all__ = list(_EXPORTS)


def __getattr__(name: str) -> Any:
    try:
        module_name, attribute = _EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(name) from exc
    value = getattr(import_module(module_name), attribute)
    globals()[name] = value
    return value
