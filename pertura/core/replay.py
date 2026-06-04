"""Replay, fork, and diff primitives for event-sourced scientific runs."""

from __future__ import annotations

import json
import hashlib
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from pertura.models import Event, Snapshot, _model_dump
from pertura.core.errors import PerturaError
from pertura.core.graph import build_graph
from pertura.core.reducer import reduce
from pertura.core.store import Store


class ReplayError(PerturaError, RuntimeError):
    """Raised when replay/fork invariants fail."""

    default_code = "replay.error"
    default_doc_path = "errors/replay"


@dataclass(frozen=True)
class ReplayResult:
    run_id: str
    event_count: int
    snapshot: Snapshot
    graph: dict
    snapshot_matches_store: bool
    graph_matches_store: bool

    def as_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "event_count": self.event_count,
            "snapshot_matches_store": self.snapshot_matches_store,
            "graph_matches_store": self.graph_matches_store,
            "snapshot": _model_dump(self.snapshot),
            "graph": self.graph,
        }


@dataclass(frozen=True)
class ForkResult:
    run_id: str
    run_dir: Path
    store: Store
    event_count: int
    copied_cache: bool

    def as_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "run_dir": str(self.run_dir),
            "event_count": self.event_count,
            "copied_cache": self.copied_cache,
        }


def replay_store(store_or_path: Store | Path | str, *, strict: bool = True) -> ReplayResult:
    """Replay an event store from scratch and verify stored projections.

    This is a projection check, not an LLM/tool replay. It answers whether
    `events.db` can regenerate the current snapshot and graph byte-equivalently.
    """
    store = _coerce_store(store_or_path)
    events = store.read_events()
    if not events:
        raise ReplayError("Cannot replay an empty event log")
    snapshot = reduce(events)
    graph = build_graph(snapshot)
    stored_snapshot = store.read_snapshot()
    stored_graph = store.read_graph()
    snapshot_matches = _canonical(snapshot) == _canonical(stored_snapshot)
    graph_matches = _canonical(graph) == _canonical(stored_graph)
    if strict and (not snapshot_matches or not graph_matches):
        raise ReplayError(
            "Stored projections do not match replayed projections "
            f"(snapshot={snapshot_matches}, graph={graph_matches})"
        )
    return ReplayResult(
        run_id=snapshot.run_id,
        event_count=len(events),
        snapshot=snapshot,
        graph=graph,
        snapshot_matches_store=snapshot_matches,
        graph_matches_store=graph_matches,
    )


def fork_store(
    source_run_dir: Path | str,
    at_event_id: str,
    *,
    new_run_dir: Path | str | None = None,
    new_run_id: str | None = None,
) -> ForkResult:
    """Fork a run by replaying an event prefix into a new run directory.

    The fork keeps event ids from the shared prefix but rewrites `run_id` so
    the new store is a first-class run. Response cache is copied when present.
    """
    source_dir = Path(source_run_dir)
    source_store = Store(source_dir)
    source_events = source_store.read_events()
    if not source_events:
        raise ReplayError(f"Cannot fork empty run: {source_dir}")

    prefix: list[Event] = []
    found = False
    for event in source_events:
        prefix.append(event)
        if event.event_id == at_event_id:
            found = True
            break
    if not found:
        raise ReplayError(f"Fork event not found: {at_event_id}")

    parent_run_id = source_events[0].run_id
    fork_id = new_run_id or f"{parent_run_id}_fork_{uuid4().hex[:6]}"
    fork_dir = Path(new_run_dir) if new_run_dir is not None else source_dir.parent / fork_id
    fork_dir = fork_dir.resolve()
    fork_store_obj = Store(fork_dir)
    rewritten = [_rewrite_event_run_id(event, fork_id) for event in prefix]
    fork_store_obj.append(rewritten)

    copied_cache = False
    source_cache = source_dir / "_response_cache.db"
    if source_cache.exists():
        _copy_sqlite_database(source_cache, fork_dir / "_response_cache.db")
        copied_cache = True

    return ForkResult(
        run_id=fork_id,
        run_dir=fork_dir,
        store=fork_store_obj,
        event_count=len(rewritten),
        copied_cache=copied_cache,
    )


def diff_stores(run_a_dir: Path | str, run_b_dir: Path | str) -> dict:
    """Return graph, observation, and conclusion diffs between two runs."""
    store_a = Store(Path(run_a_dir))
    store_b = Store(Path(run_b_dir))
    snap_a = store_a.read_snapshot()
    snap_b = store_b.read_snapshot()
    graph_a = store_a.read_graph() or {"nodes": [], "edges": []}
    graph_b = store_b.read_graph() or {"nodes": [], "edges": []}

    return {
        "run_a": snap_a.run_id if snap_a else str(run_a_dir),
        "run_b": snap_b.run_id if snap_b else str(run_b_dir),
        "graph": _diff_graph(graph_a, graph_b),
        "observations": _diff_observations(snap_a, snap_b),
        "conclusions": _diff_conclusions(snap_a, snap_b),
    }


def run_integrity(store_or_path: Store | Path | str) -> dict:
    """Return deterministic hashes that bind a run to its event log/projections."""
    store = _coerce_store(store_or_path)
    events = store.read_events()
    snapshot = store.read_snapshot()
    graph = store.read_graph() or {"nodes": [], "edges": []}
    projection_checks = {
        "snapshot_matches_store": None,
        "graph_matches_store": None,
    }
    replay_hashes = {
        "replayed_snapshot_sha256": "",
        "replayed_graph_sha256": "",
    }
    replay_error = ""
    if events:
        try:
            replay = replay_store(store, strict=False)
            projection_checks = {
                "snapshot_matches_store": replay.snapshot_matches_store,
                "graph_matches_store": replay.graph_matches_store,
            }
            replay_hashes = {
                "replayed_snapshot_sha256": stable_json_sha256(replay.snapshot),
                "replayed_graph_sha256": stable_json_sha256(replay.graph),
            }
        except ReplayError as exc:
            replay_error = str(exc)
    return {
        "integrity_type": "run_integrity",
        "version": "sha256-stable-json-v1",
        "run_id": snapshot.run_id if snapshot else events[0].run_id if events else "",
        "event_count": len(events),
        "event_log_sha256": stable_json_sha256(events),
        "snapshot_sha256": stable_json_sha256(snapshot) if snapshot else "",
        "graph_sha256": stable_json_sha256(graph),
        "analysis_spec_sha256": stable_json_sha256(snapshot.analysis_spec or {}) if snapshot else "",
        **projection_checks,
        **replay_hashes,
        "replay_error": replay_error,
    }


def _coerce_store(store_or_path: Store | Path | str) -> Store:
    if isinstance(store_or_path, Store):
        return store_or_path
    return Store(Path(store_or_path))


def _copy_sqlite_database(source: Path, target: Path) -> None:
    """Copy SQLite content safely even when WAL mode is enabled."""
    target.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(source) as src, sqlite3.connect(target) as dst:
        src.backup(dst)


def _rewrite_event_run_id(event: Event, run_id: str) -> Event:
    payload = json.loads(json.dumps(event.payload, ensure_ascii=False, default=str))
    if event.event_type == "run_started":
        config = payload.setdefault("config", {})
        config["run_id"] = run_id
    return Event(
        event_id=event.event_id,
        event_type=event.event_type,
        run_id=run_id,
        timestamp=event.timestamp,
        actor=event.actor,
        payload=payload,
    )


def _diff_graph(graph_a: dict, graph_b: dict) -> dict:
    nodes_a = {n["node_id"]: n for n in graph_a.get("nodes", [])}
    nodes_b = {n["node_id"]: n for n in graph_b.get("nodes", [])}
    edges_a = {_edge_key(e): e for e in graph_a.get("edges", [])}
    edges_b = {_edge_key(e): e for e in graph_b.get("edges", [])}
    shared_nodes = set(nodes_a) & set(nodes_b)
    return {
        "nodes": {
            "added": [nodes_b[node_id] for node_id in sorted(set(nodes_b) - set(nodes_a))],
            "removed": [nodes_a[node_id] for node_id in sorted(set(nodes_a) - set(nodes_b))],
            "changed": [
                {"id": node_id, "a": nodes_a[node_id], "b": nodes_b[node_id]}
                for node_id in sorted(shared_nodes)
                if _canonical(nodes_a[node_id]) != _canonical(nodes_b[node_id])
            ],
        },
        "edges": {
            "added": [edges_b[key] for key in sorted(set(edges_b) - set(edges_a))],
            "removed": [edges_a[key] for key in sorted(set(edges_a) - set(edges_b))],
        },
    }


def _diff_observations(snap_a: Snapshot | None, snap_b: Snapshot | None) -> dict:
    by_id_a = {o.observation_id: _model_dump(o) for o in (snap_a.observations if snap_a else [])}
    by_id_b = {o.observation_id: _model_dump(o) for o in (snap_b.observations if snap_b else [])}
    by_var_a = _observations_by_variable(snap_a)
    by_var_b = _observations_by_variable(snap_b)
    return {
        "by_id": _diff_mapping(by_id_a, by_id_b),
        "by_variable": _diff_mapping(by_var_a, by_var_b),
    }


def _diff_conclusions(snap_a: Snapshot | None, snap_b: Snapshot | None) -> dict:
    items_a = {c.conclusion_id: _model_dump(c) for c in (snap_a.conclusions if snap_a else [])}
    items_b = {c.conclusion_id: _model_dump(c) for c in (snap_b.conclusions if snap_b else [])}
    return _diff_mapping(items_a, items_b)


def _observations_by_variable(snap: Snapshot | None) -> dict:
    if not snap:
        return {}
    grouped: dict[str, list[dict]] = {}
    for obs in snap.observations:
        payload = _model_dump(obs)
        key = obs.variable_key or "|".join([
            obs.target or "",
            obs.metric or "",
            obs.contrast or "",
            obs.method or "",
            obs.branch_id or "",
        ])
        grouped.setdefault(key, []).append(payload)
    return {key: sorted(values, key=lambda item: item.get("observation_id", ""))
            for key, values in grouped.items()}


def _diff_mapping(items_a: dict, items_b: dict) -> dict:
    shared = set(items_a) & set(items_b)
    return {
        "added": [items_b[key] for key in sorted(set(items_b) - set(items_a))],
        "removed": [items_a[key] for key in sorted(set(items_a) - set(items_b))],
        "changed": [
            {"id": key, "a": items_a[key], "b": items_b[key]}
            for key in sorted(shared)
            if _canonical(items_a[key]) != _canonical(items_b[key])
        ],
    }


def _edge_key(edge: dict) -> str:
    return f"{edge.get('source_id', '')}|{edge.get('target_id', '')}|{edge.get('edge_type', '')}"


def stable_json_sha256(value) -> str:
    """Hash a value using the replay canonical JSON encoding."""
    canonical = stable_json(value)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def stable_json(value) -> str:
    return json.dumps(
        _canonical_value(value),
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    )


def _canonical_value(value):
    value = _model_dump(value)
    if isinstance(value, dict):
        return {str(key): _canonical_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canonical_value(item) for item in value]
    return value


def _canonical(value) -> str:
    return stable_json(value)
