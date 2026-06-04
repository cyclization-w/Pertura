"""SQLite-backed background job runner for workbench execution.

The queue is deliberately small: SQLite is the durable job ledger, while the
current FastAPI process supplies handlers and worker threads. This gives v1
restart-visible lifecycle state without committing to Redis/Celery yet.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable
from uuid import uuid4


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_dt(value: str | None):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except Exception:
        return None


class Job:
    """Compatibility wrapper returned by JobRunner.submit()."""

    def __init__(self, runner: "JobRunner", job_id: str):
        self._runner = runner
        self.job_id = job_id

    @property
    def status(self) -> str:
        return self.to_dict().get("status", "unknown")

    def cancel(self):
        self._runner.cancel(self.job_id)

    def to_dict(self) -> dict:
        return self._runner.get(self.job_id) or {"job_id": self.job_id, "status": "unknown"}


class JobRunner:
    """Persistent single-machine job queue with cooperative cancellation."""

    def __init__(self, db_path: str | Path | None = None, *,
                 max_workers: int = 2, stale_seconds: int = 300):
        self.db_path = Path(db_path or Path("runs") / "workbench_jobs.db")
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.stale_seconds = stale_seconds
        self._lock = threading.Lock()
        self._semaphore = threading.BoundedSemaphore(max_workers)
        self._cancel_events: dict[str, threading.Event] = {}
        self._handlers: dict[str, Callable] = {}
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS workbench_jobs (
                    job_id TEXT PRIMARY KEY,
                    job_type TEXT,
                    run_id TEXT,
                    status TEXT,
                    payload TEXT,
                    created_at TEXT,
                    queued_at TEXT,
                    started_at TEXT,
                    heartbeat_at TEXT,
                    cancel_requested_at TEXT,
                    finished_at TEXT,
                    result TEXT,
                    error TEXT,
                    attempt INTEGER DEFAULT 0
                )
            """)

    def register_handler(self, job_type: str, handler):
        """Register a restart-safe handler: handler(payload, cancel_event)."""
        self._handlers[job_type] = handler

    def submit(self, run_fn=None, *, job_type: str = "adhoc",
               payload: dict | None = None, run_id: str = "") -> Job:
        job_id = f"job_{uuid4().hex[:10]}"
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT INTO workbench_jobs(
                    job_id, job_type, run_id, status, payload,
                    created_at, queued_at, attempt
                ) VALUES(?,?,?,?,?,?,?,?)
            """, (
                job_id, job_type, run_id, "queued",
                json.dumps(payload or {}, ensure_ascii=False),
                _now(), _now(), 0,
            ))
        if run_fn is not None:
            self._start(job_id, run_fn)
        elif job_type in self._handlers:
            self._start(job_id)
        return Job(self, job_id)

    def retry(self, job_id: str, run_fn=None) -> Job | None:
        row = self._row(job_id)
        if not row or not self._is_retryable(row):
            return None
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                UPDATE workbench_jobs
                SET status='queued', queued_at=?, started_at=NULL,
                    heartbeat_at=NULL, cancel_requested_at=NULL,
                    finished_at=NULL, result=NULL, error=NULL,
                    attempt=attempt+1
                WHERE job_id=?
            """, (_now(), job_id))
        if run_fn is not None:
            self._start(job_id, run_fn)
        elif row["job_type"] in self._handlers:
            self._start(job_id)
        return Job(self, job_id)

    def _start(self, job_id: str, run_fn=None):
        cancel_event = threading.Event()
        with self._lock:
            self._cancel_events[job_id] = cancel_event
        thread = threading.Thread(
            target=self._execute,
            args=(job_id, run_fn, cancel_event),
            daemon=True,
        )
        thread.start()

    def _execute(self, job_id: str, run_fn, cancel_event: threading.Event):
        self._semaphore.acquire()
        heartbeat_stop = threading.Event()
        heartbeat_thread = None
        try:
            row = self._row(job_id)
            if not row or row["status"] == "cancelled" or row["cancel_requested_at"]:
                self._mark_cancelled(job_id)
                return
            payload = json.loads(row["payload"] or "{}")
            if run_fn is None:
                handler = self._handlers.get(row["job_type"])
                if handler is None:
                    self._finish(job_id, "failed", error=f"No handler for job_type={row['job_type']}")
                    return
                run_fn = lambda ev: handler(payload, ev)

            self._mark_running(job_id)
            heartbeat_thread = threading.Thread(
                target=self._heartbeat_loop,
                args=(job_id, heartbeat_stop),
                daemon=True,
            )
            heartbeat_thread.start()
            result = run_fn(cancel_event)
            status = "cancelled" if cancel_event.is_set() else "succeeded"
            self._finish(job_id, status, result=result)
        except Exception as exc:
            self._finish(job_id, "failed", error=str(exc))
        finally:
            heartbeat_stop.set()
            if heartbeat_thread:
                heartbeat_thread.join(timeout=1)
            with self._lock:
                self._cancel_events.pop(job_id, None)
            self._semaphore.release()

    def _heartbeat_loop(self, job_id: str, stop: threading.Event):
        while not stop.is_set():
            self._heartbeat(job_id)
            stop.wait(1.0)

    def get(self, job_id: str) -> dict | None:
        row = self._row(job_id)
        return self._decorate(row) if row else None

    def list_jobs(self) -> list[dict]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM workbench_jobs ORDER BY created_at DESC"
            ).fetchall()
        return [self._decorate(dict(r)) for r in rows]

    def cancel(self, job_id: str) -> bool:
        row = self._row(job_id)
        if not row or row["status"] not in {"queued", "running"}:
            return False
        with sqlite3.connect(self.db_path) as conn:
            if row["status"] == "queued":
                conn.execute("""
                    UPDATE workbench_jobs
                    SET status='cancelled', cancel_requested_at=?, finished_at=?
                    WHERE job_id=?
                """, (_now(), _now(), job_id))
            else:
                conn.execute("""
                    UPDATE workbench_jobs
                    SET cancel_requested_at=?
                    WHERE job_id=?
                """, (_now(), job_id))
        with self._lock:
            ev = self._cancel_events.get(job_id)
            if ev:
                ev.set()
        return True

    def _row(self, job_id: str) -> dict | None:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM workbench_jobs WHERE job_id=?",
                (job_id,),
            ).fetchone()
        return dict(row) if row else None

    def _mark_running(self, job_id: str):
        now = _now()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                UPDATE workbench_jobs
                SET status='running', started_at=COALESCE(started_at, ?),
                    heartbeat_at=?
                WHERE job_id=?
            """, (now, now, job_id))

    def _heartbeat(self, job_id: str):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "UPDATE workbench_jobs SET heartbeat_at=? WHERE job_id=? AND status='running'",
                (_now(), job_id),
            )

    def _mark_cancelled(self, job_id: str):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                UPDATE workbench_jobs
                SET status='cancelled', cancel_requested_at=COALESCE(cancel_requested_at, ?),
                    finished_at=COALESCE(finished_at, ?)
                WHERE job_id=?
            """, (_now(), _now(), job_id))

    def _finish(self, job_id: str, status: str, *, result=None, error: str | None = None):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                UPDATE workbench_jobs
                SET status=?, finished_at=?, result=?, error=?
                WHERE job_id=?
            """, (
                status, _now(),
                json.dumps(result or {}, ensure_ascii=False, default=str),
                error,
                job_id,
            ))

    def _decorate(self, row: dict) -> dict:
        if not row:
            return {}
        result = dict(row)
        try:
            result["payload"] = json.loads(result.get("payload") or "{}")
        except Exception:
            result["payload"] = {}
        try:
            result["result"] = json.loads(result.get("result") or "{}")
        except Exception:
            result["result"] = {}
        stale = self._is_stale(row)
        result["stale"] = stale
        result["retryable"] = self._is_retryable(row)
        return result

    def _is_stale(self, row: dict) -> bool:
        if row.get("status") != "running":
            return False
        heartbeat = _parse_dt(row.get("heartbeat_at") or row.get("started_at"))
        if not heartbeat:
            return True
        return datetime.now(timezone.utc) - heartbeat > timedelta(seconds=self.stale_seconds)

    def _is_retryable(self, row: dict) -> bool:
        return row.get("status") in {"failed", "cancelled"} or self._is_stale(row)
