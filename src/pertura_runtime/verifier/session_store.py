from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from pertura_core import ResultEnvelope, RunReceipt, verify_receipt
from pertura_core.hashing import canonical_json
from pertura_runtime.verifier.receipts import verify_detached_signature
from pertura_runtime.verifier.sessions import (
    AuthoritySessionRecord,
    RunAggregateProjection,
    aggregate_root_digest,
    session_result_bindings,
    session_root_digest,
)


class AuthoritySessionStore:
    """SQLite-backed session metadata layered over the v0.2 authority store.

    Keeping this internal layer separate avoids changing frozen public models or
    reinterpreting historical receipts. Rows created before session support
    remain visible, but are projected as ``legacy_unverified``.
    """

    def __init__(self, path: str | Path, *, read_only: bool = False) -> None:
        self.path = Path(path).resolve()
        self.read_only = read_only
        if read_only:
            if not self.path.is_file():
                raise FileNotFoundError(self.path)
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._initialize()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        if self.read_only:
            connection = sqlite3.connect(f"file:{self.path.as_posix()}?mode=ro", uri=True, timeout=30)
        else:
            connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            if not self.read_only:
                connection.commit()
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS authority_sessions (
                    session_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    broker_instance_id TEXT NOT NULL,
                    public_key TEXT NOT NULL,
                    policy_hash TEXT NOT NULL,
                    status TEXT NOT NULL,
                    root_digest TEXT,
                    signature TEXT,
                    started_at_utc TEXT NOT NULL,
                    sealed_at_utc TEXT
                );
                CREATE INDEX IF NOT EXISTS authority_sessions_run_idx
                    ON authority_sessions(run_id, started_at_utc);
                """
            )
            columns = {row["name"] for row in db.execute("PRAGMA table_info(results)").fetchall()}
            if "session_id" not in columns:
                db.execute("ALTER TABLE results ADD COLUMN session_id TEXT")
            db.execute(
                "CREATE INDEX IF NOT EXISTS results_session_idx ON results(session_id, created_at)"
            )

    def start_session(
        self,
        *,
        session_id: str,
        run_id: str,
        broker_instance_id: str,
        public_key: str,
        policy_hash: str,
    ) -> AuthoritySessionRecord:
        if self.read_only:
            raise RuntimeError("read-only authority session store")
        now = _utc_now()
        with self._connect() as db:
            stale = db.execute(
                "SELECT session_id FROM authority_sessions WHERE run_id = ? AND status = 'open'",
                (run_id,),
            ).fetchall()
            db.execute(
                "UPDATE authority_sessions SET status = 'aborted', sealed_at_utc = ? "
                "WHERE run_id = ? AND status = 'open'",
                (now, run_id),
            )
            db.execute(
                "INSERT INTO authority_sessions "
                "(session_id, run_id, broker_instance_id, public_key, policy_hash, status, started_at_utc) "
                "VALUES (?, ?, ?, ?, ?, 'open', ?)",
                (session_id, run_id, broker_instance_id, public_key, policy_hash, now),
            )
            for row in stale:
                self._event(db, run_id, "authority_session_aborted", row["session_id"], {})
            self._event(
                db,
                run_id,
                "authority_session_started",
                session_id,
                {"broker_instance_id": broker_instance_id, "policy_hash": policy_hash},
            )
        return AuthoritySessionRecord(
            session_id=session_id,
            run_id=run_id,
            broker_instance_id=broker_instance_id,
            public_key=public_key,
            policy_hash=policy_hash,
            status="open",
            started_at_utc=now,
        )

    def bind_result(self, *, result_id: str, run_id: str, session_id: str) -> None:
        if self.read_only:
            raise RuntimeError("read-only authority session store")
        with self._connect() as db:
            session = db.execute(
                "SELECT run_id, status FROM authority_sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            if session is None or session["run_id"] != run_id:
                raise ValueError("result cannot be bound to an unknown or different-run authority session")
            if session["status"] != "open":
                raise ValueError("result cannot be bound to a closed authority session")
            row = db.execute(
                "SELECT run_id, session_id FROM results WHERE result_id = ?", (result_id,)
            ).fetchone()
            if row is None or row["run_id"] != run_id:
                raise ValueError("authority result is missing or belongs to another run")
            if row["session_id"] not in (None, session_id):
                raise ValueError("result is already bound to another authority session")
            db.execute("UPDATE results SET session_id = ? WHERE result_id = ?", (session_id, result_id))

    def get_session(self, session_id: str) -> AuthoritySessionRecord | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM authority_sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
        return _session_from_row(row) if row else None

    def session_for_result(self, result_id: str) -> AuthoritySessionRecord | None:
        with self._connect() as db:
            if not _has_column(db, "results", "session_id"):
                return None
            row = db.execute(
                "SELECT authority_sessions.* FROM results "
                "JOIN authority_sessions USING(session_id) WHERE result_id = ?",
                (result_id,),
            ).fetchone()
        return _session_from_row(row) if row else None

    def bindings(self, session_id: str) -> tuple[dict[str, str | None], ...]:
        with self._connect() as db:
            if not _has_column(db, "results", "session_id"):
                return ()
            rows = db.execute(
                "SELECT results.result_hash, receipts.receipt_hash FROM results "
                "LEFT JOIN receipts USING(result_id) WHERE results.session_id = ?",
                (session_id,),
            ).fetchall()
        return session_result_bindings(
            (row["result_hash"], row["receipt_hash"]) for row in rows
        )

    def signing_record(self, session_id: str) -> AuthoritySessionRecord:
        session = self.get_session(session_id)
        if session is None:
            raise ValueError("unknown authority session")
        if session.status not in {"open", "sealed"}:
            raise ValueError("aborted authority session cannot be sealed")
        return replace(session, root_digest=session_root_digest(self.bindings(session_id)))

    def seal_session(
        self, *, session_id: str, root_digest: str, signature: str
    ) -> AuthoritySessionRecord:
        if self.read_only:
            raise RuntimeError("read-only authority session store")
        now = _utc_now()
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM authority_sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
            if row is None:
                raise ValueError("unknown authority session")
            current = _session_from_row(row)
            expected = session_root_digest(self._bindings_in_transaction(db, session_id))
            if root_digest != expected:
                raise ValueError("authority session root changed before seal")
            if current.status == "sealed":
                if current.root_digest != root_digest or current.signature != signature:
                    raise ValueError("authority session is already sealed with different content")
                return current
            if current.status != "open":
                raise ValueError("only an open authority session can be sealed")
            db.execute(
                "UPDATE authority_sessions SET status = 'sealed', root_digest = ?, signature = ?, "
                "sealed_at_utc = ? WHERE session_id = ?",
                (root_digest, signature, now, session_id),
            )
            self._event(
                db,
                current.run_id,
                "authority_session_sealed",
                session_id,
                {"root_digest": root_digest},
            )
        return replace(
            current,
            status="sealed",
            root_digest=root_digest,
            signature=signature,
            sealed_at_utc=now,
        )

    def abort_session(self, session_id: str) -> None:
        if self.read_only:
            raise RuntimeError("read-only authority session store")
        now = _utc_now()
        with self._connect() as db:
            row = db.execute(
                "SELECT run_id FROM authority_sessions WHERE session_id = ? AND status = 'open'",
                (session_id,),
            ).fetchone()
            if row is None:
                return
            db.execute(
                "UPDATE authority_sessions SET status = 'aborted', sealed_at_utc = ? "
                "WHERE session_id = ? AND status = 'open'",
                (now, session_id),
            )
            self._event(db, row["run_id"], "authority_session_aborted", session_id, {})

    def abort_open_sessions(
        self,
        *,
        run_id: str | None = None,
        broker_instance_id: str | None = None,
        reason: str = "broker_unavailable",
    ) -> tuple[str, ...]:
        """Close open sessions owned by a broker that can no longer seal them."""

        if self.read_only:
            raise RuntimeError("read-only authority session store")
        if not run_id and not broker_instance_id:
            raise ValueError("run_id or broker_instance_id is required")
        clauses = ["status = 'open'"]
        parameters: list[str] = []
        if run_id:
            clauses.append("run_id = ?")
            parameters.append(run_id)
        if broker_instance_id:
            clauses.append("broker_instance_id = ?")
            parameters.append(broker_instance_id)
        where = " AND ".join(clauses)
        now = _utc_now()
        with self._connect() as db:
            rows = db.execute(
                f"SELECT session_id, run_id FROM authority_sessions WHERE {where}",
                tuple(parameters),
            ).fetchall()
            for row in rows:
                db.execute(
                    "UPDATE authority_sessions SET status = 'aborted', sealed_at_utc = ? "
                    "WHERE session_id = ? AND status = 'open'",
                    (now, row["session_id"]),
                )
                self._event(
                    db,
                    row["run_id"],
                    "authority_session_aborted",
                    row["session_id"],
                    {"reason": reason},
                )
        return tuple(sorted(row["session_id"] for row in rows))

    def list_events(self, run_id: str, *, after: int = 0) -> list[dict[str, Any]]:
        with self._connect() as db:
            try:
                rows = db.execute(
                    "SELECT * FROM events WHERE run_id = ? AND sequence > ? ORDER BY sequence",
                    (run_id, after),
                ).fetchall()
            except sqlite3.OperationalError:
                return []
        return [
            dict(row) | {"payload": json.loads(row["payload_json"])} for row in rows
        ]

    def project_run(
        self, run_id: str, *, expected_policy_hash: str | None = None
    ) -> RunAggregateProjection:
        with self._connect() as db:
            session_rows = _select_sessions(db, run_id)
            result_rows = _select_results(db, run_id)

        sessions = [_session_from_row(row) for row in session_rows]
        rows_by_session: dict[str, list[sqlite3.Row]] = {}
        for row in result_rows:
            if row["session_id"]:
                rows_by_session.setdefault(row["session_id"], []).append(row)

        projected_sessions: list[dict[str, Any]] = []
        session_validity: dict[str, bool] = {}
        invalid_session_ids: list[str] = []
        for session in sessions:
            bindings = session_result_bindings(
                (row["result_hash"], row["receipt_hash"]) for row in rows_by_session.get(session.session_id, [])
            )
            expected_root = session_root_digest(bindings)
            policy_matches = expected_policy_hash is None or session.policy_hash == expected_policy_hash
            signature_valid = bool(
                session.status == "sealed"
                and session.root_digest == expected_root
                and session.signature
                and verify_detached_signature(
                    public_key_b64=session.public_key,
                    signature_b64=session.signature,
                    payload=canonical_json(session.signing_payload()).encode("utf-8"),
                )
            )
            valid = bool(signature_valid and policy_matches)
            session_validity[session.session_id] = valid
            if session.status == "sealed" and not valid:
                invalid_session_ids.append(session.session_id)
            projected_sessions.append(
                session.to_dict()
                | {
                    "binding_count": len(bindings),
                    "signature_valid": signature_valid,
                    "policy_matches": policy_matches,
                    "verified": valid,
                }
            )

        session_by_id = {item.session_id: item for item in sessions}
        committed: list[dict[str, Any]] = []
        legacy: list[str] = []
        for row in result_rows:
            payload = json.loads(row["result_payload_json"])
            payload["stale"] = bool(row["stale"])
            payload["canonical_hash"] = ""
            result = ResultEnvelope.model_validate(payload)
            receipt = (
                RunReceipt.model_validate_json(row["receipt_payload_json"])
                if row["receipt_payload_json"]
                else None
            )
            session_id = row["session_id"]
            session = session_by_id.get(session_id) if session_id else None
            if session is None:
                verification_state = "legacy_unverified"
                legacy.append(result.result_id)
            elif session.status == "aborted":
                verification_state = "session_aborted_untrusted"
            elif session.status != "sealed":
                verification_state = "authority_session_unsealed"
            elif not session_validity.get(session.session_id, False):
                verification_state = "invalid_authority_session"
            elif receipt is None:
                verification_state = "validated_untrusted"
            elif verify_receipt(
                receipt,
                authoritative_public_key=session.public_key,
                expected_result=result,
                expected_policy_hash=expected_policy_hash or session.policy_hash,
            ):
                verification_state = "trusted_receipt"
            else:
                verification_state = "invalid_receipt"
            committed.append(
                {
                    "result": result.model_dump(mode="json"),
                    "receipt": receipt.model_dump(mode="json") if receipt else None,
                    "authority_session": session.to_dict() if session else None,
                    "verification_state": verification_state,
                }
            )

        projected_sessions.sort(key=lambda item: (item["started_at_utc"], item["session_id"]))
        committed.sort(
            key=lambda item: (
                item["result"].get("completed_at_utc", ""),
                item["result"]["result_id"],
            )
        )
        return RunAggregateProjection(
            run_id=run_id,
            sessions=tuple(projected_sessions),
            committed=tuple(committed),
            aggregate_digest=aggregate_root_digest(projected_sessions),
            legacy_unverified_result_ids=tuple(sorted(legacy)),
            invalid_session_ids=tuple(sorted(invalid_session_ids)),
        )

    @staticmethod
    def _bindings_in_transaction(
        db: sqlite3.Connection, session_id: str
    ) -> tuple[dict[str, str | None], ...]:
        rows = db.execute(
            "SELECT results.result_hash, receipts.receipt_hash FROM results "
            "LEFT JOIN receipts USING(result_id) WHERE results.session_id = ?",
            (session_id,),
        ).fetchall()
        return session_result_bindings(
            (row["result_hash"], row["receipt_hash"]) for row in rows
        )

    @staticmethod
    def _event(
        db: sqlite3.Connection,
        run_id: str,
        event_type: str,
        object_id: str,
        payload: dict[str, Any],
    ) -> None:
        try:
            db.execute(
                "INSERT INTO events(run_id, event_type, object_id, payload_json) VALUES (?, ?, ?, ?)",
                (run_id, event_type, object_id, json.dumps(payload, sort_keys=True, separators=(",", ":"))),
            )
        except sqlite3.OperationalError:
            # Very old authority stores may predate events. Session validity
            # does not depend on the convenience audit stream.
            pass


def _select_sessions(db: sqlite3.Connection, run_id: str) -> list[sqlite3.Row]:
    try:
        return db.execute(
            "SELECT * FROM authority_sessions WHERE run_id = ? ORDER BY started_at_utc, session_id",
            (run_id,),
        ).fetchall()
    except sqlite3.OperationalError:
        return []


def _select_results(db: sqlite3.Connection, run_id: str) -> list[sqlite3.Row]:
    has_session = _has_column(db, "results", "session_id")
    session_expression = "results.session_id" if has_session else "NULL"
    return db.execute(
        f"SELECT results.payload_json AS result_payload_json, results.result_hash, results.stale, "
        f"{session_expression} AS session_id, receipts.payload_json AS receipt_payload_json, "
        f"receipts.receipt_hash FROM results LEFT JOIN receipts USING(result_id) "
        f"WHERE results.run_id = ? ORDER BY results.created_at, results.result_id",
        (run_id,),
    ).fetchall()


def _has_column(db: sqlite3.Connection, table: str, column: str) -> bool:
    return column in {row["name"] for row in db.execute(f"PRAGMA table_info({table})").fetchall()}


def _session_from_row(row: sqlite3.Row) -> AuthoritySessionRecord:
    return AuthoritySessionRecord(
        session_id=row["session_id"],
        run_id=row["run_id"],
        broker_instance_id=row["broker_instance_id"],
        public_key=row["public_key"],
        policy_hash=row["policy_hash"],
        status=row["status"],
        root_digest=row["root_digest"],
        signature=row["signature"],
        started_at_utc=row["started_at_utc"],
        sealed_at_utc=row["sealed_at_utc"],
    )


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
