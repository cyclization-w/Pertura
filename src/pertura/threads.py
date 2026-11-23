"""The threads in the record as replies and the page show them (docs/adr/0035): each with a sentence and its numbers
(about), what raised it, its target, its evidence, its strength and its moves. The agent opens them (open_thread),
and the reader one level up; the threads code once opened by itself are in
pertura_research.retired.threads_opened_by_code. Strengths of two kinds are two measures: the open threads are shown
kind by kind, each kind in its own order, and a target once, with its threads of the other kinds beside it.

    shown = open_threads(state, base)                  # the open ones, strongest first
"""

KINDS = ("past the calibration", "state shift", "responders")  # the order the kinds take turns in a reply
SHOWN = 5  # the open measured threads a reply gives, strongest first; the rest are counted


def target_of(th):
    """A measured thread's target: its own field, or the second part of its ID (module:target[:state])."""
    if th.get("target"):
        return str(th["target"])
    parts = str(th.get("thread_id") or "").split(":")
    return parts[1] if len(parts) >= 2 and parts[0] != "design" else None


def view(th, base=None):
    """A thread as a reply gives it: its sentence, status and moves (each with what it runs)."""
    from . import knowledge
    out = {"thread_id": th["thread_id"], "about": th["about"], "status": th["status"]}
    if th.get("reason"):
        out["why"] = th["reason"]
    if th.get("findings"):
        out["findings"] = list(th["findings"])
    for k in ("matters", "tells_apart", "joins", "test"):
        if th.get(k):
            out[k] = th[k]
    moves = []
    for mid in th.get("moves") or []:
        entry = (base or {}).get(mid) if base is not None else None
        run = knowledge.fields(entry["text"]).get("Run", "") if entry else ""
        moves.append({"id": mid, **({"run": str(run)[:200]} if run else {})})
    if moves:
        out["moves"] = moves
    return out


def measured_order(ths):
    """The measured threads in the order a reply shows them: the kinds take turns (KINDS), each in its own measure's
    order, strongest first; a target comes once, its other measured threads after it as `also`. Returns [(thread,
    [its target's other threads])]."""
    by_kind = {}
    for t in ths:
        by_kind.setdefault(t.get("kind") if t.get("kind") in KINDS else "other", []).append(t)
    queues = [sorted(by_kind.get(k, []), key=lambda t: -float(t["strength"])) for k in (*KINDS, "other")]
    seen, out = set(), []
    while any(queues):
        for q in queues:
            while q and target_of(q[0]) is not None and target_of(q[0]) in seen:
                q.pop(0)
            if q:
                t = q.pop(0)
                tgt = target_of(t)
                if tgt is not None:
                    seen.add(tgt)
                out.append((t, [x for x in ths if x is not t and tgt is not None and target_of(x) == tgt]))
    return out


def open_threads(st, base=None, shown=SHOWN):
    """The open threads as a reply gives them: the measured ones kind by kind, a target once (at most shown targets,
    the rest counted), then the design's."""
    ths = [t for t in (st.get("threads") or {}).values() if t.get("status") == "open"]
    measured = measured_order([t for t in ths if t.get("strength") is not None])
    # one level up (item 25): after the measured ones, those joining more findings first; then the design's
    up = sorted((t for t in ths if t.get("strength") is None and t.get("joins")), key=lambda t: -len(t["joins"]))
    design = [t for t in ths if t.get("strength") is None and not t.get("joins")]
    out = {"open": [{**view(t, base), **({"also": [{"thread_id": x["thread_id"], "about": x["about"]}
                                                    for x in also]} if also else {})}
                    for t, also in measured[:shown]] + [view(t, base) for t in up + design]}
    if len(measured) > shown:
        out["more"] = (f"{len(measured) - shown} more targets with measured threads: pertura_status lists them all")
    return out


def strength_order(st):
    """Every thread's ID: the measured ones as replies show them (kind by kind, a target's threads together), then
    those one level up, joining more findings first, then the rest in the order opened."""
    ths = list((st.get("threads") or {}).values())
    measured = []
    for t, also in measured_order([t for t in ths if t.get("strength") is not None]):
        measured += [x["thread_id"] for x in [t, *also] if x["thread_id"] not in measured]
    rest = sorted((t for t in ths if t.get("strength") is None),
                  key=lambda t: (not t.get("joins"), -len(t.get("joins") or []), t.get("seq") or 0))
    return measured + [t["thread_id"] for t in rest]


def _table(path):
    import pandas as pd
    try:
        return pd.read_csv(path, sep="\t")
    except (OSError, ValueError):
        return None


def read_run(folder):
    """(result.json, result.tsv) of a module's run folder, or (None, None)."""
    import json
    import os
    try:
        with open(os.path.join(folder, "result.json"), encoding="utf-8") as fh:
            result = json.load(fh)
    except (OSError, ValueError):
        return None, None
    return result, _table(os.path.join(folder, "result.tsv"))

