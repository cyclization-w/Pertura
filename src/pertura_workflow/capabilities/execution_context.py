from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Iterator


_CONTEXT: ContextVar[dict[str, Any]] = ContextVar(
    "pertura_capability_execution_context", default={}
)


def execution_context() -> dict[str, Any]:
    return dict(_CONTEXT.get())


@contextmanager
def bind_execution_context(payload: dict[str, Any] | None) -> Iterator[None]:
    token = _CONTEXT.set(dict(payload or {}))
    try:
        yield
    finally:
        _CONTEXT.reset(token)
