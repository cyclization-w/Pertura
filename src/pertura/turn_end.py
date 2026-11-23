"""Where the analysis stands, for pertura_status and the user's page: which modules' runs on this build failed, were
stopped or still run, which measured signals and threads one level up have no answer in the record, and which findings
rest on choices with no alternative tried. It is information, not a list to finish: nothing counts it, nothing waits
on it, and it names no next step; what comes next is the agent's plan (record.plan_next).

    cov = coverage(st, waits_on_user)   # the lists, as pertura_status gives them and the status file keeps
    text = line(cov)                    # the same in a line, or "" when nothing is listed

A module counts as run on this build when one of its runs on the build, on every target, ended done; one with a run
in flight counts as running; one whose runs all failed, or were stopped, is listed so. Between the build and the
user's answer to a design key the build reopened (the cells' quality rule), the first look waits by design, and the
coverage says only that.
"""
from . import record as rc

FROM_CODE = ("first look", "one level up")  # what raised a thread whose answer the line follows, besides a run
SCREEN_KINDS = rc.SCREEN_THREADS  # the first look's threads about the whole screen
NAMES = 5  # names a list gives before "and N more"
LISTED = 20  # entries a list of the coverage keeps; the rest are counted


def unanswered(st):
    """The open threads raised by a run, the first look or one level up, with no change of status since they were
    opened: one level up first, then the whole screen's, then the rest in the record's order."""
    out = []
    for tid, th in (st.get("threads") or {}).items():
        by = th.get("raised_by")
        from_code = by in FROM_CODE or (isinstance(by, dict) and "design" not in by)  # a run, the cells overview
        if rc.unanswered(th) and from_code:
            out.append({"thread_id": tid, "kind": th.get("kind"), "raised_by": by if isinstance(by, str) else "a run",
                        "about": str(th.get("about") or "")[:160]})
    rank = {"one level up": 0}
    out.sort(key=lambda t: (rank.get(t["raised_by"], 1), 0 if t["kind"] in SCREEN_KINDS else 1))
    return out


def coverage(st, waits_on_user=()):
    """{failed, stopped, running, unanswered, waits_on_user}: the modules whose runs on this build all
    failed, or were stopped, and those running, every module as the overview reads its runs."""
    if waits_on_user:
        return {"waits_on_user": list(waits_on_user), "failed": [], "stopped": [], "running": [], "unanswered": []}
    done, running, failed, stopped = set(), set(), set(), set()
    for m, runs in rc.module_runs(st).items():  # on this build, on every target: as the overview reads them
        for r in runs:
            {"done": done, "running": running, "stopped": stopped}.get(r.get("state"), failed).add(m)
    return {"failed": sorted(failed - done - running - stopped), "stopped": sorted(stopped - done - running),
            "running": sorted(running - done), "unanswered": unanswered(st),
            "waits_on_user": []}


def kept(cov, n=LISTED):
    """The coverage as a reply carries it: each list cut to n entries, with how many more there are."""
    out = {}
    for k, v in cov.items():
        if isinstance(v, list) and len(v) > n:
            out[k], out[f"{k}_more"] = v[:n], len(v) - n
        else:
            out[k] = v
    return out


def _names(items, n=NAMES):
    """Up to n names joined in words, then how many more."""
    items = list(items)
    if len(items) > n:
        return ", ".join(items[:n]) + f" and {len(items) - n} more"
    if len(items) > 1:
        return ", ".join(items[:-1]) + " and " + items[-1]
    return items[0] if items else ""


THREAD_WORDS = {"past the calibration": "past the control groups' line", "state shift": "state shifts",
                "responders": "responders", "replicates": "the replicates' agreement",
                "guides": "the guides' agreement", "cells": "the cells' quality"}


def line(cov):
    """The turn-end line: what the coverage holds, in plain words, or "" when it holds nothing."""
    if cov.get("waits_on_user"):
        return "The first look waits on the user's answer to " + _names(cov["waits_on_user"]) + "."
    parts = []
    if cov.get("failed"):
        parts.append("Modules whose runs on this build failed: " + _names(cov["failed"]) + ".")
    if cov.get("stopped"):
        parts.append("Modules whose runs on this build were stopped: " + _names(cov["stopped"]) + ".")
    if cov.get("running"):
        parts.append("Running: " + _names(cov["running"]) + ".")
    ths = cov.get("unanswered") or []
    up = [t["thread_id"] for t in ths if t["raised_by"] == "one level up"]
    screen = [f"{t['thread_id']} ({THREAD_WORDS.get(t['kind'], t['kind'])})" for t in ths
              if t["raised_by"] != "one level up" and t["kind"] in SCREEN_KINDS]
    counts = {}
    for t in ths:
        if t["raised_by"] != "one level up" and t["kind"] not in SCREEN_KINDS:
            counts[t["kind"]] = counts.get(t["kind"], 0) + 1
    signals = screen + [f"{n} {THREAD_WORDS.get(k, k)}" for k, n in counts.items()]
    if signals:
        parts.append("Measured signals with no answer in the record: " + _names(signals) + ".")
    if up:
        parts.append("Threads one level up with no answer: " + _names(up) + ".")
    return " ".join(parts)
