from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, TypeVar

from pydantic import BaseModel

from pertura_runtime.project.models import (
    AnalysisRunRecord,
    AssetBinding,
    AssetLocation,
    ConversationRecord,
    DataAssetRef,
    ProjectRecord,
    ProviderSessionBinding,
    ReportRevision,
    TurnFinal,
    TurnRecord,
    TurnStatus,
    utc_now,
)

T = TypeVar("T", bound=BaseModel)


class ProjectStore:
    """Product-state store. It cannot create scientific results or receipts."""

    def __init__(self, database: Path) -> None:
        self.database = Path(database)
        self.database.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def _connect(self, *, immediate: bool = False) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        if immediate:
            connection.execute("BEGIN IMMEDIATE")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS projects (
                    project_id TEXT PRIMARY KEY, payload_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY, project_id TEXT NOT NULL,
                    active_turn_id TEXT, payload_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS conversations (
                    conversation_id TEXT PRIMARY KEY, project_id TEXT NOT NULL,
                    run_id TEXT NOT NULL, payload_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS provider_bindings (
                    binding_id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL,
                    active INTEGER NOT NULL, payload_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS turns (
                    turn_id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL,
                    run_id TEXT NOT NULL, sequence INTEGER NOT NULL,
                    status TEXT NOT NULL, payload_json TEXT NOT NULL,
                    UNIQUE(conversation_id, sequence)
                );
                CREATE TABLE IF NOT EXISTS turn_events (
                    event_id TEXT PRIMARY KEY, turn_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL, payload_json TEXT NOT NULL,
                    UNIQUE(turn_id, sequence)
                );
                CREATE TABLE IF NOT EXISTS turn_finals (
                    turn_id TEXT PRIMARY KEY, payload_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS assets (
                    asset_id TEXT PRIMARY KEY, project_id TEXT NOT NULL,
                    role TEXT NOT NULL, status TEXT NOT NULL, payload_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS asset_locations (
                    location_id TEXT PRIMARY KEY, asset_id TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS asset_bindings (
                    binding_id TEXT PRIMARY KEY, run_id TEXT NOT NULL,
                    asset_id TEXT NOT NULL, role TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS report_revisions (
                    report_id TEXT PRIMARY KEY, run_id TEXT NOT NULL,
                    revision INTEGER NOT NULL, digest TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    UNIQUE(run_id, revision), UNIQUE(run_id, digest)
                );
                """
            )

    @staticmethod
    def _dump(model: BaseModel) -> str:
        return json.dumps(model.model_dump(mode="json"), sort_keys=True, ensure_ascii=False)

    @staticmethod
    def _load(row: sqlite3.Row | None, model: type[T]) -> T | None:
        return model.model_validate_json(row["payload_json"]) if row else None

    def put_project(self, record: ProjectRecord) -> None:
        with self._connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO projects(project_id,payload_json) VALUES (?,?)",
                (record.project_id, self._dump(record)),
            )

    def get_project(self, project_id: str) -> ProjectRecord | None:
        with self._connect() as db:
            return self._load(db.execute("SELECT payload_json FROM projects WHERE project_id=?", (project_id,)).fetchone(), ProjectRecord)

    def list_projects(self) -> tuple[ProjectRecord, ...]:
        with self._connect() as db:
            rows = db.execute("SELECT payload_json FROM projects ORDER BY project_id").fetchall()
        return tuple(ProjectRecord.model_validate_json(row["payload_json"]) for row in rows)

    def put_run(self, record: AnalysisRunRecord) -> None:
        with self._connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO runs(run_id,project_id,active_turn_id,payload_json) VALUES (?,?,?,?)",
                (record.run_id, record.project_id, record.active_turn_id, self._dump(record)),
            )

    def get_run(self, run_id: str) -> AnalysisRunRecord | None:
        with self._connect() as db:
            return self._load(db.execute("SELECT payload_json FROM runs WHERE run_id=?", (run_id,)).fetchone(), AnalysisRunRecord)

    def list_runs(self, project_id: str) -> tuple[AnalysisRunRecord, ...]:
        with self._connect() as db:
            rows = db.execute("SELECT payload_json FROM runs WHERE project_id=? ORDER BY rowid", (project_id,)).fetchall()
        return tuple(AnalysisRunRecord.model_validate_json(row["payload_json"]) for row in rows)

    def put_conversation(self, record: ConversationRecord) -> None:
        with self._connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO conversations(conversation_id,project_id,run_id,payload_json) VALUES (?,?,?,?)",
                (record.conversation_id, record.project_id, record.run_id, self._dump(record)),
            )

    def get_conversation(self, conversation_id: str) -> ConversationRecord | None:
        with self._connect() as db:
            return self._load(db.execute("SELECT payload_json FROM conversations WHERE conversation_id=?", (conversation_id,)).fetchone(), ConversationRecord)

    def list_conversations(self, project_id: str) -> tuple[ConversationRecord, ...]:
        with self._connect() as db:
            rows = db.execute("SELECT payload_json FROM conversations WHERE project_id=? ORDER BY rowid", (project_id,)).fetchall()
        return tuple(ConversationRecord.model_validate_json(row["payload_json"]) for row in rows)

    def put_provider_binding(self, binding: ProviderSessionBinding) -> None:
        with self._connect() as db:
            if binding.active:
                db.execute("UPDATE provider_bindings SET active=0 WHERE conversation_id=?", (binding.conversation_id,))
            db.execute(
                "INSERT OR REPLACE INTO provider_bindings(binding_id,conversation_id,active,payload_json) VALUES (?,?,?,?)",
                (binding.binding_id, binding.conversation_id, int(binding.active), self._dump(binding)),
            )

    def active_provider_binding(self, conversation_id: str) -> ProviderSessionBinding | None:
        with self._connect() as db:
            row = db.execute("SELECT payload_json FROM provider_bindings WHERE conversation_id=? AND active=1 ORDER BY rowid DESC LIMIT 1", (conversation_id,)).fetchone()
        return self._load(row, ProviderSessionBinding)

    def begin_turn(self, conversation_id: str, user_input: str, *, provider_binding_id: str | None = None) -> TurnRecord:
        with self._connect(immediate=True) as db:
            conversation_row = db.execute("SELECT payload_json FROM conversations WHERE conversation_id=?", (conversation_id,)).fetchone()
            if not conversation_row:
                raise KeyError(f"unknown conversation: {conversation_id}")
            conversation = ConversationRecord.model_validate_json(conversation_row["payload_json"])
            run_row = db.execute("SELECT payload_json,active_turn_id FROM runs WHERE run_id=?", (conversation.run_id,)).fetchone()
            if not run_row:
                raise KeyError(f"unknown analysis run: {conversation.run_id}")
            if run_row["active_turn_id"]:
                active_id = str(run_row["active_turn_id"])
                active_row = db.execute("SELECT payload_json FROM turns WHERE turn_id=?", (active_id,)).fetchone()
                active = TurnRecord.model_validate_json(active_row["payload_json"]) if active_row else None
                owner_pid = int((active.trace or {}).get("owner_pid") or 0) if active else 0
                if active is not None and active.status == TurnStatus.running and not _process_alive(owner_pid):
                    recovered = active.model_copy(update={
                        "status": TurnStatus.failed,
                        "provider_final": None,
                        "completed_at": utc_now(),
                        "trace": dict(active.trace) | {"recovered_after_interruption": True},
                    })
                    fallback = TurnFinal(
                        turn_id=active.turn_id,
                        status=TurnStatus.failed,
                        headline="Interrupted provider turn",
                        markdown=("# Interrupted provider turn\n\nThe provider process ended before a structured final was available. This checkpoint has no claim authority.\n"),
                        structured=False,
                        claim_authority=False,
                        format_error="provider_process_interrupted",
                    )
                    db.execute("UPDATE turns SET status=?,payload_json=? WHERE turn_id=?", (TurnStatus.failed.value, self._dump(recovered), active.turn_id))
                    db.execute("INSERT OR REPLACE INTO turn_finals(turn_id,payload_json) VALUES (?,?)", (active.turn_id, self._dump(fallback)))
                    db.execute("UPDATE runs SET active_turn_id=NULL WHERE run_id=?", (conversation.run_id,))
                else:
                    raise RuntimeError(f"analysis run already has active turn: {active_id}")
            sequence = int(db.execute("SELECT COALESCE(MAX(sequence),0)+1 AS n FROM turns WHERE conversation_id=?", (conversation_id,)).fetchone()["n"])
            turn = TurnRecord(
                conversation_id=conversation_id,
                run_id=conversation.run_id,
                sequence=sequence,
                user_input=user_input,
                provider_binding_id=provider_binding_id,
                trace={"owner_pid": os.getpid()},
            )
            run = AnalysisRunRecord.model_validate_json(run_row["payload_json"]).model_copy(update={"active_turn_id": turn.turn_id})
            db.execute("UPDATE runs SET active_turn_id=?,payload_json=? WHERE run_id=?", (turn.turn_id, self._dump(run), run.run_id))
            db.execute("INSERT INTO turns(turn_id,conversation_id,run_id,sequence,status,payload_json) VALUES (?,?,?,?,?,?)", (turn.turn_id, turn.conversation_id, turn.run_id, turn.sequence, turn.status.value, self._dump(turn)))
        return turn

    def assign_turn_binding(self, turn_id: str, binding_id: str) -> None:
        with self._connect() as db:
            row = db.execute("SELECT payload_json FROM turns WHERE turn_id=?", (turn_id,)).fetchone()
            if not row:
                raise KeyError(f"unknown turn: {turn_id}")
            record = TurnRecord.model_validate_json(row["payload_json"]).model_copy(
                update={"provider_binding_id": binding_id}
            )
            db.execute("UPDATE turns SET payload_json=? WHERE turn_id=?", (self._dump(record), turn_id))

    def append_event(self, turn_id: str, event_id: str, payload: dict[str, Any]) -> bool:
        with self._connect() as db:
            sequence = int(db.execute("SELECT COALESCE(MAX(sequence),0)+1 AS n FROM turn_events WHERE turn_id=?", (turn_id,)).fetchone()["n"])
            cursor = db.execute("INSERT OR IGNORE INTO turn_events(event_id,turn_id,sequence,payload_json) VALUES (?,?,?,?)", (event_id, turn_id, sequence, json.dumps(payload, sort_keys=True, ensure_ascii=False)))
            return bool(cursor.rowcount)

    def complete_turn(
        self,
        turn_id: str,
        *,
        status: TurnStatus,
        provider_final: str | None,
        result_ids: tuple[str, ...] = (),
        artifact_ids: tuple[str, ...] = (),
        usage: dict[str, Any] | None = None,
        trace: dict[str, Any] | None = None,
        final: TurnFinal | None = None,
    ) -> TurnRecord:
        if status == TurnStatus.running:
            raise ValueError("a completed turn cannot remain running")
        with self._connect() as db:
            row = db.execute("SELECT payload_json FROM turns WHERE turn_id=?", (turn_id,)).fetchone()
            if not row:
                raise KeyError(f"unknown turn: {turn_id}")
            previous = TurnRecord.model_validate_json(row["payload_json"])
            record = previous.model_copy(update={
                "status": status,
                "provider_final": provider_final,
                "result_ids": result_ids,
                "artifact_ids": artifact_ids,
                "usage": usage or {},
                "trace": trace or {},
                "completed_at": utc_now(),
            })
            db.execute("UPDATE turns SET status=?,payload_json=? WHERE turn_id=?", (status.value, self._dump(record), turn_id))
            run_row = db.execute("SELECT payload_json FROM runs WHERE run_id=?", (record.run_id,)).fetchone()
            if run_row:
                run = AnalysisRunRecord.model_validate_json(run_row["payload_json"])
                if run.active_turn_id == turn_id:
                    run = run.model_copy(update={"active_turn_id": None})
                    db.execute("UPDATE runs SET active_turn_id=NULL,payload_json=? WHERE run_id=?", (self._dump(run), run.run_id))
            if final:
                db.execute("INSERT OR REPLACE INTO turn_finals(turn_id,payload_json) VALUES (?,?)", (turn_id, self._dump(final)))
        return record

    def get_turn(self, turn_id: str) -> TurnRecord | None:
        with self._connect() as db:
            return self._load(db.execute("SELECT payload_json FROM turns WHERE turn_id=?", (turn_id,)).fetchone(), TurnRecord)

    def list_turns(self, conversation_id: str) -> tuple[TurnRecord, ...]:
        with self._connect() as db:
            rows = db.execute("SELECT payload_json FROM turns WHERE conversation_id=? ORDER BY sequence", (conversation_id,)).fetchall()
        return tuple(TurnRecord.model_validate_json(row["payload_json"]) for row in rows)

    def get_turn_final(self, turn_id: str) -> TurnFinal | None:
        with self._connect() as db:
            return self._load(db.execute("SELECT payload_json FROM turn_finals WHERE turn_id=?", (turn_id,)).fetchone(), TurnFinal)

    def put_asset(self, asset: DataAssetRef, location: AssetLocation) -> None:
        with self._connect() as db:
            db.execute("INSERT OR REPLACE INTO assets(asset_id,project_id,role,status,payload_json) VALUES (?,?,?,?,?)", (asset.asset_id, asset.project_id, asset.role, asset.status, self._dump(asset)))
            db.execute("INSERT OR REPLACE INTO asset_locations(location_id,asset_id,payload_json) VALUES (?,?,?)", (location.location_id, location.asset_id, self._dump(location)))

    def update_asset(self, asset: DataAssetRef) -> None:
        with self._connect() as db:
            db.execute("UPDATE assets SET status=?,payload_json=? WHERE asset_id=?", (asset.status, self._dump(asset), asset.asset_id))

    def get_asset(self, asset_id: str) -> DataAssetRef | None:
        with self._connect() as db:
            return self._load(db.execute("SELECT payload_json FROM assets WHERE asset_id=?", (asset_id,)).fetchone(), DataAssetRef)

    def list_assets(self, project_id: str) -> tuple[DataAssetRef, ...]:
        with self._connect() as db:
            rows = db.execute("SELECT payload_json FROM assets WHERE project_id=? ORDER BY role,asset_id", (project_id,)).fetchall()
        return tuple(DataAssetRef.model_validate_json(row["payload_json"]) for row in rows)

    def asset_locations(self, asset_id: str) -> tuple[AssetLocation, ...]:
        with self._connect() as db:
            rows = db.execute("SELECT payload_json FROM asset_locations WHERE asset_id=? ORDER BY rowid DESC", (asset_id,)).fetchall()
        return tuple(AssetLocation.model_validate_json(row["payload_json"]) for row in rows)

    def put_asset_binding(self, binding: AssetBinding) -> None:
        with self._connect() as db:
            db.execute("INSERT OR REPLACE INTO asset_bindings(binding_id,run_id,asset_id,role,payload_json) VALUES (?,?,?,?,?)", (binding.binding_id, binding.run_id, binding.asset_id, binding.role, self._dump(binding)))

    def list_asset_bindings(self, run_id: str) -> tuple[AssetBinding, ...]:
        with self._connect() as db:
            rows = db.execute("SELECT payload_json FROM asset_bindings WHERE run_id=? ORDER BY role,asset_id", (run_id,)).fetchall()
        return tuple(AssetBinding.model_validate_json(row["payload_json"]) for row in rows)

    def put_report_revision(self, revision: ReportRevision) -> None:
        with self._connect() as db:
            db.execute("INSERT INTO report_revisions(report_id,run_id,revision,digest,payload_json) VALUES (?,?,?,?,?)", (revision.report_id, revision.run_id, revision.revision, revision.digest, self._dump(revision)))

    def report_for_digest(self, run_id: str, digest: str) -> ReportRevision | None:
        with self._connect() as db:
            return self._load(db.execute("SELECT payload_json FROM report_revisions WHERE run_id=? AND digest=?", (run_id, digest)).fetchone(), ReportRevision)

    def list_report_revisions(self, run_id: str) -> tuple[ReportRevision, ...]:
        with self._connect() as db:
            rows = db.execute("SELECT payload_json FROM report_revisions WHERE run_id=? ORDER BY revision", (run_id,)).fetchall()
        return tuple(ReportRevision.model_validate_json(row["payload_json"]) for row in rows)


def _process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        return _windows_process_alive(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _windows_process_alive(pid: int) -> bool:
    """Query process state without sending a Windows console event or signal."""

    import ctypes
    from ctypes import wintypes

    process_query_limited_information = 0x1000
    still_active = 259
    error_access_denied = 5
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    open_process = kernel32.OpenProcess
    open_process.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    open_process.restype = wintypes.HANDLE
    get_exit_code_process = kernel32.GetExitCodeProcess
    get_exit_code_process.argtypes = (
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.DWORD),
    )
    get_exit_code_process.restype = wintypes.BOOL
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = (wintypes.HANDLE,)
    close_handle.restype = wintypes.BOOL

    ctypes.set_last_error(0)
    handle = open_process(process_query_limited_information, False, pid)
    if not handle:
        return ctypes.get_last_error() == error_access_denied
    try:
        exit_code = wintypes.DWORD()
        ctypes.set_last_error(0)
        if not get_exit_code_process(handle, ctypes.byref(exit_code)):
            return ctypes.get_last_error() == error_access_denied
        return exit_code.value == still_active
    finally:
        close_handle(handle)
