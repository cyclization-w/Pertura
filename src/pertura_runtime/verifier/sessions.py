from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Iterable

from pertura_core.hashing import canonical_hash


SESSION_SCHEMA_VERSION = "pertura-authority-session-v1"
AGGREGATE_SCHEMA_VERSION = "pertura-run-aggregate-v1"


@dataclass(frozen=True)
class AuthoritySessionRecord:
    """Internal authority-session state; deliberately outside the v0.2 API."""

    session_id: str
    run_id: str
    broker_instance_id: str
    public_key: str
    policy_hash: str
    status: str
    root_digest: str | None = None
    signature: str | None = None
    started_at_utc: str = ""
    sealed_at_utc: str | None = None
    schema_version: str = SESSION_SCHEMA_VERSION

    def signing_payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "session_id": self.session_id,
            "run_id": self.run_id,
            "broker_instance_id": self.broker_instance_id,
            "public_key": self.public_key,
            "policy_hash": self.policy_hash,
            "root_digest": self.root_digest,
        }

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RunAggregateProjection:
    """Read-only, historically verifiable view of one logical product run."""

    run_id: str
    sessions: tuple[dict[str, Any], ...] = ()
    committed: tuple[dict[str, Any], ...] = ()
    aggregate_digest: str = ""
    legacy_unverified_result_ids: tuple[str, ...] = ()
    invalid_session_ids: tuple[str, ...] = ()
    schema_version: str = AGGREGATE_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def session_result_bindings(
    rows: Iterable[tuple[str, str | None]],
) -> tuple[dict[str, str | None], ...]:
    bindings = [
        {"result_hash": result_hash, "receipt_hash": receipt_hash}
        for result_hash, receipt_hash in rows
    ]
    return tuple(
        sorted(
            bindings,
            key=lambda item: (item["result_hash"], item["receipt_hash"] or ""),
        )
    )


def session_root_digest(bindings: Iterable[dict[str, str | None]]) -> str:
    return canonical_hash(
        {
            "schema_version": "pertura-authority-session-root-v1",
            "bindings": list(bindings),
        }
    )


def aggregate_root_digest(sessions: Iterable[dict[str, Any]]) -> str:
    roots = sorted(
        (
            {
                "session_id": str(item["session_id"]),
                "root_digest": item.get("root_digest"),
                "status": str(item["status"]),
            }
            for item in sessions
        ),
        key=lambda item: item["session_id"],
    )
    return canonical_hash(
        {
            "schema_version": AGGREGATE_SCHEMA_VERSION,
            "sessions": roots,
        }
    )
