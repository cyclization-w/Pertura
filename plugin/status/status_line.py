"""Pertura's status line: the analysis's stage and its running jobs, below Claude Code's prompt.

Claude Code runs it on each new message and every second (refreshInterval), with the session's JSON on stdin. It
reads only the line file the hooks compose (status_file.line_file), never the server, and imports no json: the
first line comes composed, and only the spinner and the jobs' times are worked out here. In a session that has not
used Pertura it prints the model and the folder, as a plain status line would.

    "statusLine": {"type": "command", "command": "python -S \"$HOME/.claude/pertura/status_line.py\"",
                   "refreshInterval": 1}

Line 1: the stages, each done (●), under way (◐) or not reached (○), then the open items and what is pending, for the agent and for you.
Line 2, while jobs run: each running job and how long it has run, with a small spinner; a job not checked for two
minutes is not called running, but said to be not checked since a time of day; for a few minutes after a job ends,
that it finished or failed.
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import status_words as words  # noqa: E402

HOME_VARIABLE = "PERTURA_STATUS_HOME"


def field(text, key):
    """A string field of the session JSON, read without the json module: "key": "value", with \\ and \" undone."""
    i = text.find(f'"{key}"')
    if i < 0:
        return None
    i = text.find(":", i + len(key) + 2)
    j = i + 1
    while j < len(text) and text[j] in " \t\r\n":
        j += 1
    if i < 0 or j >= len(text) or text[j] != '"':
        return None
    out, j = [], j + 1
    while j < len(text) and text[j] != '"':
        if text[j] == "\\" and j + 1 < len(text):
            j += 1
        out.append(text[j])
        j += 1
    return "".join(out)


def _read(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return None


def _safe(text):
    return "".join(c if c.isalnum() or c in "_-" else "-" for c in str(text))[:120]


def render(stdin_text, root=None, now=None):
    """The status line's text for the session JSON Claude Code passes."""
    now = time.time() if now is None else now
    root = root or os.environ.get(HOME_VARIABLE) or os.path.join(os.path.expanduser("~"), ".claude", "pertura")
    key = (_read(os.path.join(root, "sessions", _safe(field(stdin_text, "session_id") or "") + ".key")) or "").strip()
    lines = (_read(os.path.join(root, "projects", _safe(key) + ".line")) or "").split("\n") if key else []
    if not lines or not lines[0].startswith("pertura-line/"):
        folder = (field(stdin_text, "current_dir") or field(stdin_text, "cwd") or "").rstrip("/\\")
        folder = folder.replace("\\", "/").rsplit("/", 1)[-1]
        return " · ".join(x for x in (field(stdin_text, "display_name"), folder) if x)
    first, running, ended, failed_at = "Pertura", [], [], None
    for row in lines[1:]:
        cells = row.split("\t")
        try:
            if cells[0] == "1":
                first = cells[1]
            elif cells[0] == "J":
                running.append((float(cells[1]), float(cells[2]), cells[3], cells[4] if len(cells) > 4 else ""))
            elif cells[0] == "E":
                ended.append((float(cells[1]), cells[2], cells[3]))
            elif cells[0] == "X":
                failed_at = float(cells[1])
        except (IndexError, ValueError):
            continue
    second = words.job_line(running, ended, failed_at, now, clock=lambda t: time.strftime("%H:%M", time.localtime(t)))
    return first + ("\n" + second if second else "")


def main():
    try:
        text = render(sys.stdin.buffer.read().decode("utf-8", "replace"))
    except Exception:  # a status line that fails shows nothing at all; a short word is better
        text = "Pertura"
    sys.stdout.buffer.write(text.encode("utf-8"))


if __name__ == "__main__":
    main()
