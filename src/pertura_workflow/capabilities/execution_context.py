from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Iterator


_CONTEXT: ContextVar[dict[str, Any]] = ContextVar(
    "pertura_capability_execution_context", default={}
)


def execution_context() -> dict[str, Any]:
    return dict(_CONTEXT.get())


def mark_dependency_consumed(*dependency_hashes: str) -> None:
    consumed = _CONTEXT.get().get("consumed_dependency_hashes")
    if not isinstance(consumed, set):
        return
    consumed.update(str(item) for item in dependency_hashes if str(item))


def consumed_dependency_hashes() -> tuple[str, ...]:
    consumed = _CONTEXT.get().get("consumed_dependency_hashes")
    if not isinstance(consumed, set):
        return ()
    return tuple(sorted(str(item) for item in consumed))


@contextmanager
def bind_execution_context(payload: dict[str, Any] | None) -> Iterator[None]:
    token = _CONTEXT.set(dict(payload or {}))
    try:
        yield
    finally:
        _CONTEXT.reset(token)
