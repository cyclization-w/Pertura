"""The rows of Pertura's subagents in the agent panel below the prompt (subagentStatusLine).

Claude Code passes every visible subagent row on stdin ({columns, tasks: [{id, agentType, status, description,
startTime, ...}]}) and draws, for each line printed as {"id", "content"}, that content in place of its default
row. Only Pertura's own agents (agentType pertura:<name>) get a row here; every other agent keeps its default row.

A Pertura row says which check it is in plain words, what it was asked, how long it has run and how it ended:
    ⠹ skeptic · the three findings that lead result 1 · 4 min
"""
import json
import sys
import time

ROLES = {"worker": "worker", "skeptic": "skeptic", "sufficiency-check": "sufficiency check",
         "design-auditor": "design auditor"}
PREFIX = "pertura:"
SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
GREEN, YELLOW, RED, DIM, RESET = "\033[32m", "\033[33m", "\033[31m", "\033[2m", "\033[0m"


def ago(seconds):
    s = max(0, int(seconds))
    if s < 60:
        return f"{s} s"
    m = s // 60
    if m < 60:
        return f"{m} min"
    h, m = divmod(m, 60)
    return f"{h} h {m} min" if m else f"{h} h"


def role(agent_type):
    t = str(agent_type or "")
    return ROLES.get(t[len(PREFIX):]) if t.startswith(PREFIX) else None


def visible_len(text):
    """The width of text as drawn: without its colour codes."""
    out, i = 0, 0
    while i < len(text):
        if text[i] == "\033":
            j = text.find("m", i)
            i = len(text) if j < 0 else j + 1
            continue
        out += 1
        i += 1
    return out


def row(task, now, columns=80):
    """One row's content, or None for an agent that is not Pertura's."""
    name = role(task.get("agentType"))
    if name is None:
        return None
    status = task.get("status")
    started = (task.get("startTime") or now * 1000) / 1000.0
    if status == "running":
        head = f"{YELLOW}{SPINNER[int(now) % len(SPINNER)]}{RESET}"
        tail = ago(now - started)
    elif status == "completed":
        head, tail = f"{GREEN}✓{RESET}", "done"
    elif status == "failed":
        head, tail = f"{RED}✗{RESET}", "failed"
    else:
        head, tail = f"{DIM}■{RESET}", "stopped"
    what = " ".join(str(task.get("description") or task.get("label") or "").split())
    fixed = visible_len(f"{head} {name} ·  · {tail}")
    room = max(0, int(columns or 80) - fixed)
    if len(what) > room:
        what = what[:max(0, room - 1)] + "…" if room > 1 else ""
    middle = f" · {DIM}{what}{RESET}" if what else ""
    return f"{head} {name}{middle} · {tail}"


def render(payload, now=None):
    """The lines to print: one JSON object per Pertura row."""
    now = time.time() if now is None else now
    out = []
    for task in payload.get("tasks") or []:
        if not isinstance(task, dict) or not task.get("id"):
            continue
        content = row(task, now, payload.get("columns") or 80)
        if content is not None:
            out.append(json.dumps({"id": task["id"], "content": content}))
    return out


def main():
    try:
        payload = json.loads(sys.stdin.buffer.read().decode("utf-8") or "{}")
        lines = render(payload if isinstance(payload, dict) else {})
    except Exception:  # every row then keeps its default
        lines = []
    sys.stdout.buffer.write(("\n".join(lines) + ("\n" if lines else "")).encode("utf-8"))


if __name__ == "__main__":
    main()
