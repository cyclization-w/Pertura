from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


OutputFn = Callable[[str], None]


@dataclass
class ClaudeStreamRenderer:
    output_fn: OutputFn = print
    verbose: bool = True
    raw_stream: bool = False
    _seen_system_messages: set[str] = field(default_factory=set, init=False, repr=False)
    _shown_session_start: bool = field(default=False, init=False, repr=False)

    def render(self, message: Any) -> None:
        if not self.verbose:
            return
        msg_type = type(message).__name__
        if msg_type in {"SystemMessage", "SDKSystemMessage"}:
            session_id = str(getattr(message, "session_id", "") or "")
            fingerprint = session_id or _preview(getattr(message, "__dict__", {}), limit=240)
            if fingerprint in self._seen_system_messages:
                return
            self._seen_system_messages.add(fingerprint)
            if self._shown_session_start:
                return
            self._shown_session_start = True
            suffix = f" session={session_id[:12]}" if session_id else ""
            self.output_fn(f"[session] started{suffix}")
            return
        if msg_type == "AssistantMessage":
            self._render_assistant(message)
            return
        if msg_type == "UserMessage":
            self._render_user(message)
            return
        if msg_type in {"ResultMessage", "SDKResultMessage"}:
            self._render_result(message)
            return
        self.output_fn(f"[sdk] {msg_type}")

    def _render_assistant(self, message: Any) -> None:
        for block in list(getattr(message, "content", []) or []):
            block_type = type(block).__name__
            name = getattr(block, "name", None)
            if name:
                summary = _tool_summary(name, getattr(block, "input", None))
                suffix = f" {summary}" if summary else ""
                self.output_fn(f"[tool] {name}{suffix}")
                continue
            text = getattr(block, "text", None)
            if text:
                if self.raw_stream:
                    self.output_fn(f"[assistant] {_preview(text, limit=300)}")
                continue
            if block_type == "ThinkingBlock":
                if self.raw_stream:
                    thinking = getattr(block, "thinking", "")
                    self.output_fn(f"[thinking] {_preview(thinking, limit=160)}")

    def _render_user(self, message: Any) -> None:
        for block in list(getattr(message, "content", []) or []):
            content = getattr(block, "content", None)
            if content is not None:
                is_error = bool(getattr(block, "is_error", False))
                if self.raw_stream:
                    label = "tool-error" if is_error else "tool-result"
                    self.output_fn(f"[{label}] {_preview(content, limit=400)}")
                elif is_error:
                    self.output_fn(f"[tool-error] {_preview(content, limit=300)}")
                else:
                    self.output_fn("[tool-result] ok")

    def _render_result(self, message: Any) -> None:
        is_error = bool(getattr(message, "is_error", False))
        label = "failed" if is_error else "done"
        cost = getattr(message, "total_cost_usd", None)
        turns = getattr(message, "num_turns", None)
        suffix = []
        if cost is not None:
            suffix.append(f"cost=${float(cost):.4f}")
        if turns is not None:
            suffix.append(f"turns={turns}")
        self.output_fn(f"[result] {label} {' '.join(suffix)}".rstrip())


def _tool_summary(name: str, value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    parts: list[str] = []
    for key in ["file_path", "path", "pattern", "report_filename", "description"]:
        if key in value and value[key]:
            parts.append(f"{key}={_preview(value[key], limit=80)}")
    if not parts and name == "Bash" and value.get("command"):
        parts.append("command=<hidden>")
    return " ".join(parts)


def _preview(value: Any, *, limit: int = 200) -> str:
    text = str(value).replace("\r", " ").replace("\n", " ")
    if len(text) > limit:
        return text[: limit - 3] + "..."
    return text
