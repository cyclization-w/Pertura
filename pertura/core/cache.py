"""Content-addressed response cache for deterministic replay.

ActiveGraph-style caching: every LLM request and tool result is keyed by
the content hash of its inputs. On replay, cached values are returned
without making fresh API calls, enabling:
  - Deterministic replay (strict mode: byte-level verification)
  - Replay with diff (loose mode: cache misses get fresh calls + recorded)
  - Cheap forking (shared prefix served from cache, zero cost)

Architecture:
  RequestHash = SHA256(model | system | user | tools | temperature | max_tokens)
  CacheEntry   = {request_hash, response_json, model, token_usage, timestamp}

The cache lives alongside the event store at <run_dir>/_response_cache.db
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path


class ResponseCache:
    """Content-addressed cache for LLM responses and tool results.

    One cache per run directory. Shared across all forks of the same run
    (replays and forks at different branch points reuse the same cache).
    """

    def __init__(self, run_dir: Path):
        self.db_path = run_dir / "_response_cache.db"
        self._init()

    def _init(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS responses (
                    request_hash TEXT PRIMARY KEY,
                    response_json TEXT NOT NULL,
                    model TEXT,
                    token_usage TEXT,
                    created_at TEXT DEFAULT (datetime('now'))
                );
                CREATE INDEX IF NOT EXISTS idx_responses_model
                    ON responses(model);
            """)

    # ── Public API ────────────────────────────────────────────────────

    def get(self, request_hash: str) -> dict | None:
        """Return cached response dict, or None on miss."""
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT response_json FROM responses WHERE request_hash = ?",
                (request_hash,),
            ).fetchone()
        if row:
            return json.loads(row[0])
        return None

    def put(self, request_hash: str, response: dict, *,
            model: str = "", token_usage: dict | None = None):
        """Store a response keyed by its request hash."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO responses(request_hash, response_json, model, token_usage) VALUES(?,?,?,?)",
                (request_hash, json.dumps(response, ensure_ascii=False),
                 model, json.dumps(token_usage or {})),
            )

    def contains(self, request_hash: str) -> bool:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT 1 FROM responses WHERE request_hash = ?",
                (request_hash,),
            ).fetchone()
        return row is not None

    def stats(self) -> dict:
        with sqlite3.connect(self.db_path) as conn:
            count = conn.execute("SELECT COUNT(*) FROM responses").fetchone()[0]
        return {"cached_responses": count, "db_path": str(self.db_path)}


# ── Hash utilities ────────────────────────────────────────────────────

def hash_llm_request(
    system: str,
    user_message: str,
    tools: list[dict],
    model: str,
    temperature: float = 0.1,
    max_tokens: int = 4096,
) -> str:
    """Content-address a complete LLM request. Same inputs → same hash."""
    canonical = json.dumps({
        "system": system,
        "user": user_message,
        "tools": tools,
        "model": model,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]


def hash_tool_call(tool_name: str, arguments: dict) -> str:
    """Content-address a tool invocation."""
    canonical = json.dumps({
        "tool": tool_name,
        "args": arguments,
    }, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]


def hash_code_execution(code: str, workspace: str) -> str:
    """Content-address a code execution request."""
    canonical = json.dumps({
        "code": code,
        "workspace": workspace,
    }, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]
