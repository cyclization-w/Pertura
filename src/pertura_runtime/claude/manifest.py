from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pertura_runtime.claude.workspace import ClaudeRunWorkspace


def _safe_payload(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _safe_payload(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe_payload(item) for item in value]
    return repr(value)


@dataclass
class ClaudeRunManifest:
    workspace: ClaudeRunWorkspace
    result_text: str = ""
    session_id: str | None = None
    total_cost_usd: float | None = None
    num_turns: int | None = None
    message_count: int = 0
    models: set[str] = field(default_factory=set)
    is_error: bool = False
    result_subtype: str | None = None
    terminal_result_seen: bool = False
    init_seen: bool = False
    init_skills: tuple[str, ...] = ()
    init_plugins: tuple[str, ...] = ()

    def reset(self) -> None:
        """Clear provider-owned state before starting the next task turn."""

        self.result_text = ""
        self.session_id = None
        self.total_cost_usd = None
        self.num_turns = None
        self.message_count = 0
        self.models.clear()
        self.is_error = False
        self.result_subtype = None
        self.terminal_result_seen = False
        self.init_seen = False
        self.init_skills = ()
        self.init_plugins = ()

    def capture(self, message: Any) -> None:
        self.message_count += 1
        msg_type = type(message).__name__
        if msg_type in {"ResultMessage", "SDKResultMessage"}:
            self.terminal_result_seen = True
        if hasattr(message, "session_id") and getattr(message, "session_id"):
            self.session_id = str(getattr(message, "session_id"))
        if hasattr(message, "model") and getattr(message, "model"):
            self.models.add(str(getattr(message, "model")))
        if (
            hasattr(message, "total_cost_usd")
            and getattr(message, "total_cost_usd") is not None
        ):
            self.total_cost_usd = float(getattr(message, "total_cost_usd"))
        if hasattr(message, "num_turns") and getattr(message, "num_turns") is not None:
            self.num_turns = int(getattr(message, "num_turns"))
        if hasattr(message, "is_error") and getattr(message, "is_error") is not None:
            self.is_error = bool(getattr(message, "is_error"))
        if hasattr(message, "subtype") and getattr(message, "subtype") is not None:
            self.result_subtype = str(getattr(message, "subtype"))
        if hasattr(message, "result") and getattr(message, "result") is not None:
            self.result_text = str(getattr(message, "result"))
            self.workspace.write_text(
                self.workspace.logs_dir / "claude_final.md",
                self.result_text,
            )

        data = getattr(message, "data", None)
        if isinstance(data, dict) and str(data.get("subtype") or "") == "init":
            self._capture_init(data)
        elif (
            isinstance(data, dict)
            and str(getattr(message, "subtype", "")) == "init"
        ):
            self._capture_init(data)

        event = {
            "time_utc": datetime.now(timezone.utc).isoformat(),
            "message_type": msg_type,
            "session_id": self.session_id,
            "payload": _message_payload(message),
        }
        self.workspace.append_jsonl(self.workspace.logs_dir / "events.jsonl", event)

    def _capture_init(self, data: dict[str, Any]) -> None:
        self.init_seen = True
        self.init_skills = tuple(sorted(str(item) for item in data.get("skills", [])))
        plugins = []
        for item in data.get("plugins", []):
            if isinstance(item, dict):
                plugins.append(str(item.get("name") or item.get("path") or ""))
            else:
                plugins.append(str(item))
        self.init_plugins = tuple(sorted(item for item in plugins if item))

    def flush(self, *, status: str = "completed") -> None:
        self.workspace.update_manifest(
            {
                "status": status,
                "session_id": self.session_id,
                "total_cost_usd": self.total_cost_usd,
                "num_turns": self.num_turns,
                "message_count": self.message_count,
                "models": sorted(self.models),
                "is_error": self.is_error,
                "result_subtype": self.result_subtype,
                "sdk_terminal_result_seen": self.terminal_result_seen,
                "sdk_init_seen": self.init_seen,
                "sdk_available_skills": list(self.init_skills),
                "sdk_plugins": list(self.init_plugins),
            }
        )


def _message_payload(message: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for attr in [
        "content",
        "data",
        "result",
        "subtype",
        "is_error",
        "usage",
        "total_cost_usd",
        "num_turns",
        "session_id",
        "model",
    ]:
        if hasattr(message, attr):
            payload[attr] = _safe_payload(getattr(message, attr))
    return payload
