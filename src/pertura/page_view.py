"""The record as the user's page reads it (the workbench reads, never writes): each finding with its claim and each
target's checks, each result with its findings, figure and text, each thread with its end, the research lead's last
verdict, and the figures they name with their files on the server. Kept small: a check gives its outcome and the few
numbers a reader needs. Beside it, methods: each module run's method as its result's diagnostics state it, for the
page's methods.

    python -m pertura.page_view <record folder>    the view as JSON, read without writing (a run in progress)
"""
import json
import os
import sqlite3
import sys

from . import record as rc
from .events_store import _rows

NUMBERS = ("test", "cells", "reference_cells", "effect", "q", "p", "groups", "groups_as_far", "discoveries",
           "genes_changed", "target_effect", "groups_95th_percentile", "groups_95th_percentile_effect")


def _check(c):
    n = c.get("numbers") or {}
    return {"outcome": c.get("outcome"), **({"reason": c["reason"]} if c.get("reason") else {}),
            **{k: n[k] for k in NUMBERS if n.get(k) is not None}}


def tree_rows(st):
    """The tree in reading order, as the page draws it: each thread with its parent and depth below the root, on the
    main line or once on it, a fork's one change, when it opened and ended, and its findings with their times
    (record.tree): the page's timeline lays it out across time."""
    t, threads, out = rc.tree(st), st.get("threads") or {}, []
    was = {m["thread_id"] for m in st.get("main_line_moves") or []} | {n for n, th in threads.items()
                                                                        if th.get("on_line")}
    findings = st.get("findings") or {}

    def walk(n, depth):
        for c in sorted(t["children"].get(n, []), key=lambda c: threads[c].get("seq") or 0):
            th = threads[c]
            ends = [x.get("time") for x in th.get("changes") or [] if x.get("status") in rc.THREAD_ENDS]
            out.append({"thread_id": c, "about": th.get("about"), "status": th.get("status") or "open",
                        "depth": depth, "parent": t["parent"].get(c), "main": c in t["line"], "was_main": c in was,
                        "fork_of": th.get("fork_of"), "choice": th.get("choice"), "opened": th.get("time"),
                        "ended": ends[-1] if ends and th.get("status") in rc.THREAD_ENDS else None,
                        "opened_by": th.get("opened_by"), "reason": th.get("reason"),
                        "read": rc.ended_with_something_read(st, c) if th.get("status") in rc.THREAD_ENDS else None,
                        "findings": [{"id": f, "time": (findings.get(f) or {}).get("time")}
                                     for f in th.get("findings") or []]})
            walk(c, depth + 1)
    walk(rc.ROOT, 0)
    return out


def _png(st, figure):
    """A finding's or a result's figure as a file on the server: a figure the record holds, a run's figure, or a path."""
    if not figure:
        return None
    f = (st.get("figures") or {}).get(figure)
    if f:
        return (f.get("paths") or {}).get("png")
    run = (st.get("runs") or {}).get(figure)
    if run:
        return run.get("figure")
    return figure if str(figure).endswith(".png") else None


FIRST_LOOK_FIELDS = ("section", "condition", "run_id", "png", "finding_ids")


def _next_steps(st, rid, r, waits):
    """A result's next steps as the user picks among them (docs/adr/0050, D5): its latest list, whether it waits for
    the pick (rc.next_step_awaiting), and the user's picks, each with its option (none for their own step) and the
    thread it opened: pick, the latest list's (one ends the offer); earlier, the earlier lists'. Nothing for a
    result that offers none."""
    if not r.get("next_steps"):
        return {}
    since, picks = r.get("next_steps_seq") or 0, []
    for x in st.get("replies") or []:
        if x.get("kind") == "next_step" and x.get("result_id") == rid:
            picks.append(((x.get("seq") or 0) > since, {"option": x.get("option"), "thread_id": x.get("thread_id"),
                                                         "words": x.get("words")}))
    return {"next_steps": r["next_steps"], "next_step_waits": bool(waits and waits["result_id"] == rid),
            "pick": next((p for latest, p in reversed(picks) if latest), None),
            "earlier_picks": [p for latest, p in picks if not latest]}


def view(st, first_look=None):
    """The record as the page reads it; first_look: the first look's sections as the analysis service lists them
    (each FIRST_LOOK_FIELDS, in their order), which the record alone cannot give since they are read from runs' tables."""
    findings = {}
    for fid, f in (st.get("findings") or {}).items():
        by = rc.checks_by_target(f) if f.get("checks") or f.get("checks_by_target") else {}
        findings[fid] = {"statement": f.get("statement"),
                         "targets": f.get("targets") or ([f["target"]] if f.get("target") else []),
                         "feature": f.get("feature"), "direction": f.get("direction"), "kind": f.get("kind"),
                         "figure": f.get("figure"), "png": _png(st, f.get("figure")),
                         "run_ids": list(f.get("run_ids") or []), "thread_id": f.get("thread_id"),
                         "checks": {t: {k: _check(c) for k, c in each.items()} for t, each in by.items() if t},
                         "disagree": rc.disagreement(f) or None, "time": f.get("time"),
                         "skeptic": ({"checked": list(f["skeptic"].get("checked") or []),
                                      "doubt_ids": list(f["skeptic"].get("doubt_ids") or [])}
                                     if f.get("skeptic") else None)}
    waits = rc.next_step_awaiting(st)
    results = {rid: {"title": r.get("title"), "finding_ids": list(r.get("finding_ids") or []),
                     "figure": r.get("figure"), "png": _png(st, r.get("figure")), "text": r.get("text"),
                     "decision": r.get("decision"), "version": r.get("version"), "time": r.get("time"),
                     **_next_steps(st, rid, r, waits)}
               for rid, r in (st.get("results") or {}).items()}
    threads = {tid: {"about": th.get("about"), "status": th.get("status") or "open", "raised_by": th.get("raised_by"),
                     "findings": list(th.get("findings") or []), "reason": th.get("reason"),
                     "opened_by": th.get("opened_by"), "next_of": th.get("next_of")}
               for tid, th in (st.get("threads") or {}).items()}
    # who raised each doubt: the agent stored on it, else the skeptic where a skeptic check lists it, else the main
    # agent. shortcut: a finding keeps its latest skeptic check alone, so a doubt from an earlier check of a finding
    # checked again reads as the main agent's on a record from before doubts stored opened_by
    by_skeptic = {d for f in (st.get("findings") or {}).values()
                  for d in (f.get("skeptic") or {}).get("doubt_ids") or []}
    doubts_raised_by = {did: d.get("opened_by") or ("pertura:skeptic" if did in by_skeptic else "main")
                        for did, d in (st.get("doubts") or {}).items()}
    reviews = [{k: r.get(k) for k in ("verdict", "story", "threads", "figures", "blocks", "main_line", "left_open",
                                      "time")} for r in st.get("reviews") or []]
    menus = []  # one per module menu: from its module_menu_given to the next, its picks and the user's choice
    for m in st.get("module_menus") or []:
        if m["kind"] == "module_menu_given" or not menus:
            menus.append({"seq": m["seq"], "time": m.get("time"), "could_run": list(m.get("modules") or []),
                          "picks": [], "chosen": [], "action": None, "note": None})
        if m["kind"] == "module_picks_recorded":
            menus[-1]["picks"] = [{"module": x.get("module"), "why": x.get("why")} for x in m.get("picks") or []]
        elif m["kind"] == "module_menu_answered":
            menus[-1].update({"chosen": list(m.get("chosen") or []), "action": m.get("action"),
                              "note": m.get("note") or None})
    last = (st.get("reviews") or [None])[-1]
    from .candidates import SECTIONS  # each section with its name in words, for the page
    return {"findings": findings, "results": results, "threads": threads, "module_menus": menus,
            "tree": tree_rows(st),  # the analysis as a tree
            "lead": {"verdict": last["verdict"], "story": last["story"], "time": last.get("time")} if last else None,
            "reviews": reviews, "doubts_raised_by": doubts_raised_by,  # each of the lead's reviews; each doubt's agent
            "story": (st.get("story") or {}).get("text"),  # the report's story, once write_report recorded one
            # done once the report is written and the user's enough stands: the user says when it is done
            "complete": bool(st.get("story")) and rc.enough_stands(st),
            "question_lacks": (st.get("question_lacks") or {}).get("text"),
            **({"first_look": [{**s, "label": (SECTIONS.get(s.get("section")) or (s.get("section"),))[0]}
                               for s in first_look]} if first_look is not None else {})}


METHOD_FACTS = ("method", "test", "replicates", "units", "cells", "control_cells", "min_cells", "multiple_testing")


def methods(st, jobs_root, seen=None):
    """The methods of the module runs that ended, as their results' diagnostics state them, for the page's methods,
    oldest first: [{run_id, module, why, summary, packages [{package, version, function}], facts {key: text}}].
    seen: a cache by run id, since a run's result does not change once it ended."""
    seen = {} if seen is None else seen
    out = []
    for rid, run in (st.get("runs") or {}).items():
        if run.get("state") != "done" or not run.get("job_id") or not run.get("question_id"):
            continue
        if rid not in seen:
            try:
                seen[rid] = _method_of(rid, run, os.path.join(jobs_root, run["job_id"], "result.json"))
            except (OSError, ValueError, TypeError, AttributeError, KeyError):  # unreadable: no line for it
                continue
        out.append(seen[rid])
    return out


def _method_of(rid, run, path):
    with open(path, encoding="utf-8") as fh:
        res = json.load(fh)
    d = res.get("diagnostics") if isinstance(res.get("diagnostics"), dict) else {}
    calls = d.get("package_calls") if isinstance(d.get("package_calls"), list) else []
    return {"run_id": rid, "module": run["question_id"], "why": run.get("why"),
            "summary": str(res.get("summary") or "")[:400],
            "packages": [{k: str(c.get(k))[:160] for k in ("package", "version", "function") if c.get(k)}
                         for c in calls if isinstance(c, dict)],
            "facts": {k: _said(d[k])[:240] for k in METHOD_FACTS if d.get(k) not in (None, "", [], {})}}


def _said(v):
    """A diagnostic as a line: a mapping's keys with their values, a list's items, anything else as it is."""
    if isinstance(v, dict):
        return "; ".join(f"{str(k).replace('_', ' ')} {_said(x)}" for k, x in v.items())
    if isinstance(v, list):
        return ", ".join(_said(x) for x in v)
    return str(v)


def main(argv=None):
    folder = (argv if argv is not None else sys.argv[1:])[0]
    con = sqlite3.connect(f"file:{folder}/events.db?mode=ro", uri=True)
    try:
        st = rc.reduce(_rows(con))
    finally:
        con.close()
    print(json.dumps(view(st), default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
