"""Recorded LLM fixtures for offline demos, CI, and reproducible runs."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any


class FixtureMiss(RuntimeError):
    """Raised when replay mode requires a recorded fixture that is absent."""


class RecordedLLMFixtures:
    """Append-only JSONL fixture store keyed by content hash."""

    def __init__(self, path: str | Path | None = None, *, mode: str | None = None):
        self.mode = (mode or os.getenv("PETURA_LLM_FIXTURE_MODE", "off")).lower()
        self.path = Path(path or os.getenv("PETURA_LLM_FIXTURE_PATH", "llm_fixtures.jsonl"))
        self._items: dict[str, dict] | None = None

    @property
    def enabled(self) -> bool:
        return self.mode in {"record", "replay", "strict"}

    def get(self, request_hash: str) -> dict | None:
        if not self.enabled:
            return None
        self._load()
        return (self._items or {}).get(request_hash)

    def require(self, request_hash: str) -> dict:
        item = self.get(request_hash)
        if item is None:
            raise FixtureMiss(f"Recorded LLM fixture not found: {request_hash}")
        return item

    def put(self, request_hash: str, response: dict, *, metadata: dict | None = None) -> None:
        if self.mode != "record":
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        item = {
            "request_hash": request_hash,
            "response": response,
            "metadata": metadata or {},
        }
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(item, ensure_ascii=False, default=str) + "\n")
        if self._items is not None:
            self._items[request_hash] = item

    def _load(self) -> None:
        if self._items is not None:
            return
        self._items = {}
        if not self.path.exists():
            return
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            request_hash = item.get("request_hash", "")
            if request_hash:
                self._items[request_hash] = item


def llm_fixture_hash(kind: str, payload: dict[str, Any]) -> str:
    canonical = json.dumps({"kind": kind, **payload}, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]


def fixture_mode() -> str:
    return os.getenv("PETURA_LLM_FIXTURE_MODE", "off").lower()
