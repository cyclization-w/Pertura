"""SQLite-backed event store with append, snapshot, graph, and lease management."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

from pertura.core.event_schema import EventSchemaError, validate_event_payload
from pertura.core.graph import build_graph
from pertura.core.reducer import reduce, reduce_incremental
from pertura.models import Event, Snapshot, _model_dump


class Store:
    def __init__(self, run_dir: Path):
        self.run_dir = run_dir
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = run_dir / "events.db"
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS events (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT UNIQUE, event_type TEXT, run_id TEXT,
                    timestamp TEXT, actor TEXT, payload TEXT
                );
                CREATE TABLE IF NOT EXISTS snapshots (
                    id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT, updated TEXT
                );
                CREATE TABLE IF NOT EXISTS graph (
                    id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT, updated TEXT
                );
            """)

    def append(self, events: list[Event], *, unsafe: bool = False) -> list[Event]:
        """Append events and update projections in one write transaction.

        Store-level validation prevents callers from bypassing GraphController
        with unknown events. `unsafe=True` is reserved for controlled replay or
        migration internals.
        """
        if not events:
            return events
        if not unsafe:
            self._validate_append(events)

        with sqlite3.connect(self.db_path, isolation_level=None) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                existing_events = self._read_events_conn(conn)
                prev_snap = self._read_snapshot_conn(conn)
                if prev_snap is not None:
                    snap = reduce_incremental(prev_snap, events)
                else:
                    snap = reduce(existing_events + events)
                graph = build_graph(snap)
                updated_meta = _projection_meta(snap, graph)
                for event in events:
                    conn.execute(
                        "INSERT INTO events(event_id,event_type,run_id,timestamp,actor,payload) VALUES(?,?,?,?,?,?)",
                        (
                            event.event_id,
                            event.event_type,
                            event.run_id,
                            event.timestamp.isoformat(),
                            event.actor,
                            json.dumps(event.payload, ensure_ascii=False),
                        ),
                    )
                conn.execute(
                    "INSERT OR REPLACE INTO snapshots(id,payload,updated) VALUES(1,?,?)",
                    (json.dumps(_model_dump(snap), ensure_ascii=False, default=str), updated_meta),
                )
                conn.execute(
                    "INSERT OR REPLACE INTO graph(id,payload,updated) VALUES(1,?,?)",
                    (json.dumps(_model_dump(graph), ensure_ascii=False, default=str), updated_meta),
                )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        return events

    def append_unsafe(self, events: list[Event]) -> list[Event]:
        """Append without schema validation for controlled replay/migration internals."""
        return self.append(events, unsafe=True)

    def _validate_append(self, events: list[Event]) -> None:
        existing = self.read_events()
        if not existing and events[0].event_type != "run_started":
            raise EventSchemaError("First event must be run_started")
        run_id = existing[0].run_id if existing else events[0].run_id
        for event in events:
            validate_event_payload(event.event_type, event.payload)
            if event.run_id != run_id:
                raise EventSchemaError(
                    f"Event run_id does not match store run_id: {event.run_id} != {run_id}"
                )

    def _read_events_conn(self, conn) -> list[Event]:
        rows = conn.execute("SELECT * FROM events ORDER BY seq").fetchall()
        return [_event_from_row(row) for row in rows]

    def _read_snapshot_conn(self, conn) -> Snapshot | None:
        row = conn.execute("SELECT payload FROM snapshots WHERE id=1").fetchone()
        if row:
            return Snapshot(**json.loads(row[0]))
        return None

    def read_events(self) -> list[Event]:
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute("SELECT * FROM events ORDER BY seq").fetchall()
        return [_event_from_row(row) for row in rows]

    def read_snapshot(self) -> Snapshot | None:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute("SELECT payload FROM snapshots WHERE id=1").fetchone()
        if row:
            return Snapshot(**json.loads(row[0]))
        return None

    def read_graph(self) -> dict | None:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute("SELECT payload FROM graph WHERE id=1").fetchone()
        return json.loads(row[0]) if row else None

    def export_jsonl(self) -> Path:
        events = self.read_events()
        path = self.run_dir / "events.jsonl"
        lines = [json.dumps(_model_dump(event), ensure_ascii=False, default=str) for event in events]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path

    def acquire_lease(self, owner: str, ttl_seconds: int = 900) -> bool:
        from datetime import datetime, timedelta, timezone

        lease_path = self.run_dir / ".lease"
        now = datetime.now(timezone.utc)
        if lease_path.exists():
            try:
                data = json.loads(lease_path.read_text())
                expires = datetime.fromisoformat(data["expires"])
                if expires > now and data["owner"] != owner:
                    return False
            except Exception:
                pass
        lease_path.write_text(json.dumps({
            "owner": owner,
            "expires": (now + timedelta(seconds=ttl_seconds)).isoformat(),
            "acquired": now.isoformat(),
        }))
        return True

    def release_lease(self, owner: str):
        lease_path = self.run_dir / ".lease"
        if lease_path.exists():
            try:
                data = json.loads(lease_path.read_text())
                if data.get("owner") == owner:
                    lease_path.unlink()
            except Exception:
                pass


def _event_from_row(row) -> Event:
    return Event(
        event_id=row[1],
        event_type=row[2],
        run_id=row[3],
        timestamp=row[4],
        actor=row[5],
        payload=json.loads(row[6]),
    )


def _projection_meta(snap: Snapshot, graph: dict) -> str:
    from datetime import datetime, timezone

    payload = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "run_id": snap.run_id,
        "snapshot_hash": _sha256(_model_dump(snap)),
        "graph_hash": _sha256(_model_dump(graph)),
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _sha256(value) -> str:
    data = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(data.encode("utf-8")).hexdigest()
