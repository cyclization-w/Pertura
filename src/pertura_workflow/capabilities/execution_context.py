from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Iterator

from pertura_core import DatasetContract


_CONTEXT: ContextVar[dict[str, Any]] = ContextVar(
    "pertura_capability_execution_context", default={}
)


def execution_context() -> dict[str, Any]:
    return dict(_CONTEXT.get())


def authoritative_input_roots(contract: DatasetContract) -> tuple[Path, ...]:
    """Return contract sources plus broker-authorized registered asset paths."""

    values = (*contract.source_paths, *execution_context().get("authorized_asset_paths", ()))
    return tuple(Path(item).expanduser().resolve() for item in values)


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


def record_dependency_consumption(
    *,
    dependency_result_id: str,
    dependency_result_hash: str,
    dependency_artifact_hash: str,
    usage: str,
    consumer_capability_id: str | None = None,
    rows_consumed: int | None = None,
    rows_available: int | None = None,
    columns_consumed: int | None = None,
    derived_output_hashes: tuple[str, ...] = (),
) -> None:
    """Record scientific use of a dependency, not mere metadata access."""

    context = _CONTEXT.get()
    records = context.get("dependency_consumption_records")
    if not isinstance(records, list):
        return
    record = {
        "dependency_result_id": str(dependency_result_id),
        "dependency_result_hash": str(dependency_result_hash),
        "dependency_artifact_hash": str(dependency_artifact_hash),
        "usage": str(usage),
        "consumer_capability_id": str(
            consumer_capability_id or context.get("consumer_capability_id") or ""
        ),
        "rows_consumed": rows_consumed,
        "rows_available": rows_available,
        "columns_consumed": columns_consumed,
        "derived_output_hashes": list(derived_output_hashes),
    }
    if record not in records:
        records.append(record)
    mark_dependency_consumed(dependency_result_hash)


def dependency_consumption_records() -> tuple[dict[str, Any], ...]:
    records = _CONTEXT.get().get("dependency_consumption_records")
    if not isinstance(records, list):
        return ()
    return tuple(dict(item) for item in records)


@contextmanager
def bind_execution_context(payload: dict[str, Any] | None) -> Iterator[None]:
    token = _CONTEXT.set(dict(payload or {}))
    try:
        yield
    finally:
        _CONTEXT.reset(token)
