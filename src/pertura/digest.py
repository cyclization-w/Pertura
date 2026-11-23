"""The digest (docs/build_goal_session6_fixes.md, item 5, with item 10): where the analysis stands, written by code
from the record, for a user who has been away. Four parts, in plain words:
- what waits on the user: the design until it is confirmed, the results proposed until the user confirms them, and
  the findings that await a reply (which do not stop the agent);
- the thread the agent is on, and why (the reason it gave when it took it);
- what happened since an event of the record (the user's last look), by thread;
- what comes next: the open threads, strongest first, as options.
pertura_status gives it, and the plugin's page and status line read it."""
from . import record as rc
from . import threads

NEXT_SHOWN = 3  # open threads named as what comes next


def _name(f):
    from .results_report import finding_name
    return finding_name(f)


def waits_on_user(st, design_confirmed):
    """What only the user can settle, and the agent waits for: the design, the results proposed for the report."""
    out = []
    if not design_confirmed:
        out.append("the design: the auditor proposes it, and you confirm it on the form")
    proposed = [r for r in (st.get("results") or {}).values() if r.get("decision") is None]
    if proposed:
        out.append(f"{len(proposed)} result{'s' if len(proposed) != 1 else ''} proposed for the report, to confirm: "
                   + "; ".join(str(r.get("title")) for r in proposed[:3]))
    if rc.enough_stands(st):
        out.append("you said enough: the report comes next")
    return out


def awaiting_reply(st):
    """The findings without the user's reply, which do not stop the agent: a line, or None."""
    unanswered = [f for f in (st.get("findings") or {}).values() if not f.get("replies")]
    if not unanswered:
        return None
    return (f"{len(unanswered)} finding{'s' if len(unanswered) != 1 else ''} without your reply (the agent goes on "
            "meanwhile), the latest: " + _name(unanswered[-1]))


def on_thread(st):
    """The thread the agent took last and has not ended: {thread_id, about, why}, or None."""
    taken = [t for t in (st.get("threads") or {}).values() if t.get("status") == "followed"]
    if not taken:
        return None
    t = max(taken, key=lambda x: max([c["seq"] for c in x.get("changes") or []] or [x.get("seq") or 0]))
    return {"thread_id": t["thread_id"], "about": t.get("about"), "why": t.get("reason")}


def since(st, events, seq):
    """What happened after event `seq`, by thread: findings recorded, threads taken, ended or opened, and module runs
    that ended. Returns lines, one per thread (and one for what joins no thread)."""
    by = {}
    findings = st.get("findings") or {}
    runs = st.get("runs") or {}
    for e in events:
        if seq is not None and e["seq"] <= seq:
            continue
        p, kind = e["payload"], e["event_type"]
        if kind == "finding_recorded":
            tid = p.get("thread_id") or ""
            by.setdefault(tid, []).append(f"finding {p['finding_id']}: {_name(findings.get(p['finding_id']) or p)}")
        elif kind == "thread_opened":
            by.setdefault(p["thread_id"], []).append("opened")
        elif kind == "thread_changed":
            by.setdefault(p["thread_id"], []).append(f"{p['status']} ({p['reason']})")
        elif kind == "run_finished" and ((runs.get(p["run_id"]) or {}).get("job_id")
                                         or (runs.get(p["run_id"]) or {}).get("thread_id")):
            r = runs.get(p["run_id"]) or {}
            why = f" (why: {r['why']})" if r.get("why") else ""
            by.setdefault(r.get("thread_id") or "", []).append(f"{r.get('what') or p['run_id']} {p['state']}{why}")
    ths = st.get("threads") or {}
    out = []
    for tid in [t for t in threads.strength_order(st) if t in by] + ([""] if "" in by else []):
        head = f"{tid} ({ths[tid].get('about')})" if tid in ths else "beside the threads"
        items = by[tid]
        out.append(f"{head}: " + "; ".join(items[-4:]) + (f" (and {len(items) - 4} more)" if len(items) > 4 else ""))
    return out


def next_options(st):
    """The open threads, strongest first: what the agent may take next, never a task."""
    order = threads.strength_order(st)
    ths = st.get("threads") or {}
    open_ = [ths[t] for t in order if ths[t].get("status") == "open"]
    return [f"{t['thread_id']}: {t.get('about')}" for t in open_[:NEXT_SHOWN]] + (
        [f"and {len(open_) - NEXT_SHOWN} more open threads"] if len(open_) > NEXT_SHOWN else [])


def digest(st, events, design_confirmed, seq=None):
    """The four parts, and one line for a status line."""
    waits = waits_on_user(st, design_confirmed)
    on = on_thread(st)
    reply = awaiting_reply(st)
    out = {"waits_on_you": waits or ["nothing: the agent goes on"],
           **({"awaiting_your_reply": reply} if reply else {}),
           "on": on, "since": since(st, events, seq), "next": next_options(st)}
    about = str((on or {}).get("about") or "")
    lead = f"on {on['thread_id']}: {about[:60]}{'...' if len(about) > 60 else ''}" if on else "on no thread yet"
    out["line"] = lead + (f" · waits on you: {waits[0].split(':')[0]}" if waits else "")
    return out
