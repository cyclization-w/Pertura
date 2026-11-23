"""A job's progress, for its run's state, the replies that wait on it and the status line: progress.json in the job's
folder (PERTURA_JOB_DIR, which the runner sets), {done, of, unit, started, updated}.

A long step says what it will do (expect) and what it has done (advance); several steps of one job add to one count,
so a module's targets and then its calibration read as one "40 of 50 comparisons". Only the job's main process
counts (a step's worker processes report to it); outside a job nothing is written.

    progress.expect(len(comparisons))
    progress.advance()
    progress.words(progress.read(job_dir))   # "7 of 12 comparisons, about 9 minutes left"
"""
import json
import os
import threading
import time

FILE = "progress.json"
VARIABLE = "PERTURA_JOB_DIR"
_lock = threading.Lock()
_state = {"done": 0, "of": 0, "unit": "comparisons", "started": None}


def _write():
    folder = os.environ.get(VARIABLE)
    if not folder:
        return
    path = os.path.join(folder, FILE)
    tmp = f"{path}.{os.getpid()}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({**_state, "updated": time.time()}, fh)
        os.replace(tmp, path)
    except OSError:  # progress is a courtesy: a folder that cannot be written changes nothing else
        pass


def expect(n, unit="comparisons"):
    """n more parts of the job's work, in the unit given."""
    with _lock:
        if _state["started"] is None:
            _state["started"] = time.time()
        _state["of"] += int(n)
        _state["unit"] = unit
        _write()


def advance(n=1):
    """n parts done."""
    with _lock:
        _state["done"] += int(n)
        _write()


def read(job_dir):
    """A job's progress, or None where it reports none."""
    try:
        with open(os.path.join(job_dir, FILE), encoding="utf-8") as fh:
            p = json.load(fh)
    except (OSError, ValueError):
        return None
    return p if isinstance(p, dict) and p.get("of") else None


def _span(seconds):
    m = max(1, int(round(seconds / 60)))
    if m < 60:
        return f"{m} minute{'s' if m != 1 else ''}"
    h, m = divmod(m, 60)
    return f"{h} h {m} min" if m else f"{h} h"


def words(p, now=None):
    """'7 of 12 comparisons, about 9 minutes left' (the time left from the pace so far), or None."""
    if not p or not p.get("of"):
        return None
    done, of = int(p.get("done") or 0), int(p["of"])
    text = f"{done} of {of} {p.get('unit') or 'parts'}"
    now = time.time() if now is None else now
    if 0 < done < of and p.get("started"):
        text += f", about {_span((now - float(p['started'])) / done * (of - done))} left"
    return text
