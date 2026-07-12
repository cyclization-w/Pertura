from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from pertura_core import (
    DatasetContract,
    DependencyRef,
    DesignConfirmation,
    PromotionDecision,
    ResultEnvelope,
    RunReceipt,
    ScientificStatement,
)


class AuthorityStore:
    """Broker-owned append-oriented authority database.

    The database belongs outside the CodeAct workspace. Workspace JSON exports
    are projections and never become authority on re-import.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS contracts (
                    contract_id TEXT PRIMARY KEY,
                    contract_hash TEXT NOT NULL,
                    dataset_id TEXT NOT NULL,
                    parent_contract_id TEXT,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS results (
                    result_id TEXT PRIMARY KEY,
                    request_id TEXT NOT NULL UNIQUE,
                    run_id TEXT NOT NULL,
                    capability_id TEXT NOT NULL,
                    result_hash TEXT NOT NULL,
                    status TEXT NOT NULL,
                    stale INTEGER NOT NULL DEFAULT 0,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS receipts (
                    receipt_id TEXT PRIMARY KEY,
                    result_id TEXT NOT NULL UNIQUE REFERENCES results(result_id),
                    receipt_hash TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS dependencies (
                    result_id TEXT NOT NULL REFERENCES results(result_id),
                    dependency_id TEXT NOT NULL,
                    object_id TEXT NOT NULL,
                    object_hash TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    required INTEGER NOT NULL,
                    PRIMARY KEY (result_id, dependency_id)
                );
                CREATE TABLE IF NOT EXISTS runtime_objects (
                    object_id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    object_hash TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS statements (
                    statement_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    statement_hash TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS decisions (
                    decision_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    statement_id TEXT NOT NULL REFERENCES statements(statement_id),
                    decision_hash TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS confirmations (
                    confirmation_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    contract_id TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    object_id TEXT,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS run_roots (
                    run_id TEXT PRIMARY KEY,
                    root_digest TEXT NOT NULL,
                    signature TEXT NOT NULL,
                    public_key TEXT NOT NULL,
                    sealed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                """
            )

    def put_contract(self, contract: DatasetContract) -> None:
        payload = _json(contract)
        with self._connect() as db:
            current = db.execute(
                "SELECT contract_hash FROM contracts WHERE contract_id = ?", (contract.contract_id,)
            ).fetchone()
            if current and current["contract_hash"] != contract.canonical_hash:
                raise ValueError("contract ID replayed with different content")
            db.execute(
                "INSERT OR IGNORE INTO contracts VALUES (?, ?, ?, ?, ?, ?)",
                (
                    contract.contract_id,
                    contract.canonical_hash,
                    contract.dataset_id,
                    contract.parent_contract_id,
                    payload,
                    contract.created_at_utc,
                ),
            )
            if contract.parent_contract_id:
                parent = db.execute(
                    "SELECT contract_hash FROM contracts WHERE contract_id = ?", (contract.parent_contract_id,)
                ).fetchone()
                if parent:
                    self._mark_stale_in_transaction(db, contract.parent_contract_id, contract.canonical_hash)
            self._event(db, contract.dataset_id, "contract_registered", contract.contract_id, contract.model_dump(mode="json"))

    def get_contract(self, contract_id: str) -> DatasetContract | None:
        with self._connect() as db:
            row = db.execute("SELECT payload_json FROM contracts WHERE contract_id = ?", (contract_id,)).fetchone()
        return DatasetContract.model_validate_json(row["payload_json"]) if row else None

    def put_runtime_object(self, *, kind: str, object_id: str, object_hash: str, payload: dict[str, Any]) -> int:
        with self._connect() as db:
            current = db.execute("SELECT object_hash FROM runtime_objects WHERE object_id = ?", (object_id,)).fetchone()
            stale = 0
            if current and current["object_hash"] != object_hash:
                stale = self._mark_stale_in_transaction(db, object_id, object_hash)
            db.execute(
                "INSERT OR REPLACE INTO runtime_objects(object_id, kind, object_hash, payload_json) VALUES (?, ?, ?, ?)",
                (object_id, kind, object_hash, json.dumps(payload, sort_keys=True, separators=(",", ":"))),
            )
            return stale

    def get_runtime_object(self, object_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT kind, object_hash, payload_json FROM runtime_objects WHERE object_id = ?",
                (object_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "kind": row["kind"],
            "object_id": object_id,
            "object_hash": row["object_hash"],
            "payload": json.loads(row["payload_json"]),
        }

    def validate_dependencies(self, dependencies: tuple[DependencyRef, ...]) -> list[str]:
        issues: list[str] = []
        with self._connect() as db:
            for dependency in dependencies:
                if dependency.state != "current":
                    issues.append(f"dependency is not current: {dependency.object_id}")
                    continue
                if dependency.kind == "contract":
                    row = db.execute(
                        "SELECT contract_hash FROM contracts WHERE contract_id = ?", (dependency.object_id,)
                    ).fetchone()
                    if row is None:
                        issues.append(f"dependency does not exist in authority store: {dependency.object_id}")
                    elif row["contract_hash"] != dependency.object_hash:
                        issues.append(f"dependency hash mismatch: {dependency.object_id}")
                elif dependency.kind in {"environment", "knowledge_resource"}:
                    row = db.execute(
                        "SELECT object_hash FROM runtime_objects WHERE object_id = ?", (dependency.object_id,)
                    ).fetchone()
                    if row is None:
                        issues.append(f"runtime dependency does not exist in authority store: {dependency.object_id}")
                    elif row["object_hash"] != dependency.object_hash:
                        issues.append(f"runtime dependency hash mismatch: {dependency.object_id}")
                else:
                    row = db.execute(
                        "SELECT result_hash, stale FROM results WHERE result_id = ?", (dependency.object_id,)
                    ).fetchone()
                    if row is None:
                        issues.append(f"dependency does not exist in authority store: {dependency.object_id}")
                    elif bool(row["stale"]):
                        issues.append(f"dependency result is stale: {dependency.object_id}")
                    elif row["result_hash"] != dependency.object_hash:
                        issues.append(f"dependency hash mismatch: {dependency.object_id}")
        return issues

    def commit_result(self, result: ResultEnvelope, receipt: RunReceipt | None = None) -> None:
        with self._connect() as db:
            existing = db.execute(
                "SELECT result_hash, payload_json FROM results WHERE request_id = ?", (result.request_id,)
            ).fetchone()
            if existing:
                if existing["result_hash"] != result.canonical_hash:
                    raise ValueError("request ID replayed with a different result")
                return
            db.execute(
                "INSERT INTO results "
                "(result_id, request_id, run_id, capability_id, result_hash, status, stale, payload_json, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?)",
                (
                    result.result_id,
                    result.request_id,
                    result.run_id,
                    result.capability_id,
                    result.canonical_hash,
                    str(result.status.value),
                    _json(result),
                    result.completed_at_utc,
                ),
            )
            for dependency in result.dependencies:
                db.execute(
                    "INSERT INTO dependencies VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        result.result_id,
                        dependency.dependency_id,
                        dependency.object_id,
                        dependency.object_hash,
                        dependency.kind,
                        int(dependency.required),
                    ),
                )
            if receipt is not None:
                db.execute(
                    "INSERT INTO receipts VALUES (?, ?, ?, ?, ?)",
                    (receipt.receipt_id, result.result_id, receipt.canonical_hash, _json(receipt), receipt.signed_at_utc),
                )
            self._event(db, result.run_id, "result_committed", result.result_id, {
                "result_hash": result.canonical_hash,
                "receipt_id": receipt.receipt_id if receipt else None,
                "verification_state": "trusted_receipt" if receipt else "validated_untrusted",
                "status": result.status.value,
            })

    def get_result(self, result_id: str) -> ResultEnvelope | None:
        with self._connect() as db:
            row = db.execute("SELECT payload_json, stale FROM results WHERE result_id = ?", (result_id,)).fetchone()
        if not row:
            return None
        payload = json.loads(row["payload_json"])
        payload["stale"] = bool(row["stale"])
        payload["canonical_hash"] = ""
        return ResultEnvelope.model_validate(payload)

    def get_result_for_request(self, request_id: str) -> ResultEnvelope | None:
        with self._connect() as db:
            row = db.execute("SELECT result_id FROM results WHERE request_id = ?", (request_id,)).fetchone()
        return self.get_result(row["result_id"]) if row else None

    def get_receipt_for_result(self, result_id: str) -> RunReceipt | None:
        with self._connect() as db:
            row = db.execute("SELECT payload_json FROM receipts WHERE result_id = ?", (result_id,)).fetchone()
        return RunReceipt.model_validate_json(row["payload_json"]) if row else None

    def list_results(self, run_id: str) -> list[ResultEnvelope]:
        with self._connect() as db:
            rows = db.execute("SELECT result_id FROM results WHERE run_id = ? ORDER BY created_at", (run_id,)).fetchall()
        return [item for row in rows if (item := self.get_result(row["result_id"])) is not None]

    def put_statement(self, statement: ScientificStatement) -> None:
        with self._connect() as db:
            db.execute(
                "INSERT OR IGNORE INTO statements VALUES (?, ?, ?, ?, ?)",
                (statement.statement_id, statement.run_id, statement.canonical_hash, _json(statement), statement.model_dump(mode="json").get("created_at_utc", "")),
            )
            self._event(db, statement.run_id, "statement_registered", statement.statement_id, {})

    def put_decision(self, decision: PromotionDecision) -> None:
        with self._connect() as db:
            db.execute(
                "INSERT OR IGNORE INTO decisions VALUES (?, ?, ?, ?, ?, ?)",
                (decision.decision_id, decision.run_id, decision.statement_id, decision.canonical_hash, _json(decision), decision.decided_at_utc),
            )
            self._event(db, decision.run_id, "statement_decided", decision.decision_id, {"status": decision.status})

    def put_confirmation(self, confirmation: DesignConfirmation) -> None:
        with self._connect() as db:
            db.execute(
                "INSERT OR IGNORE INTO confirmations VALUES (?, ?, ?, ?, ?)",
                (confirmation.confirmation_id, confirmation.run_id, confirmation.contract_id, _json(confirmation), confirmation.created_at_utc),
            )
            self._event(db, confirmation.run_id, "design_confirmed", confirmation.confirmation_id, {"field": confirmation.field})

    def mark_stale_for_dependency(self, object_id: str, current_hash: str) -> int:
        with self._connect() as db:
            return self._mark_stale_in_transaction(db, object_id, current_hash)

    def _mark_stale_in_transaction(self, db: sqlite3.Connection, object_id: str, current_hash: str) -> int:
        rows = db.execute(
            "SELECT DISTINCT result_id FROM dependencies WHERE object_id = ? AND object_hash != ?",
            (object_id, current_hash),
        ).fetchall()
        queue = [row["result_id"] for row in rows]
        stale: set[str] = set()
        while queue:
            result_id = queue.pop(0)
            if result_id in stale:
                continue
            stale.add(result_id)
            db.execute("UPDATE results SET stale = 1 WHERE result_id = ?", (result_id,))
            downstream = db.execute(
                "SELECT DISTINCT result_id FROM dependencies WHERE object_id = ?",
                (result_id,),
            ).fetchall()
            queue.extend(row["result_id"] for row in downstream if row["result_id"] not in stale)
        return len(stale)

    def receipt_hashes(self, run_id: str) -> list[str]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT receipts.receipt_hash FROM receipts JOIN results USING(result_id) WHERE results.run_id = ? ORDER BY receipts.receipt_id",
                (run_id,),
            ).fetchall()
        return [row["receipt_hash"] for row in rows]

    def seal_run(self, run_id: str, root_digest: str, signature: str, public_key: str) -> None:
        with self._connect() as db:
            db.execute("INSERT OR REPLACE INTO run_roots(run_id, root_digest, signature, public_key) VALUES (?, ?, ?, ?)", (run_id, root_digest, signature, public_key))
            self._event(db, run_id, "run_sealed", run_id, {"root_digest": root_digest})

    def list_events(self, run_id: str, after: int = 0) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM events WHERE run_id = ? AND sequence > ? ORDER BY sequence", (run_id, after)
            ).fetchall()
        return [dict(row) | {"payload": json.loads(row["payload_json"])} for row in rows]

    @staticmethod
    def _event(db: sqlite3.Connection, run_id: str, event_type: str, object_id: str | None, payload: dict[str, Any]) -> None:
        db.execute(
            "INSERT INTO events(run_id, event_type, object_id, payload_json) VALUES (?, ?, ?, ?)",
            (run_id, event_type, object_id, json.dumps(payload, sort_keys=True, separators=(",", ":"))),
        )


def _json(model: Any) -> str:
    return json.dumps(model.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
