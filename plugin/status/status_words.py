"""The words of the status line, shared by the hooks that compose it and the status line that draws it.

No imports: the status line runs in a fresh interpreter every second, and json alone would double its start-up.
"""
SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
GREEN, YELLOW, RED, DIM, RESET = "\033[32m", "\033[33m", "\033[31m", "\033[2m", "\033[0m"
ACTIVE = ("starting", "running", "orphaned", "started")
RECENT = 10 * 60  # a finished job is mentioned this long
LATE = 120  # a running job not checked this long says when it last was


def ago(seconds):
    """A span in the words people use: 40 s, 12 min, 1 h 5 min, 2 d 3 h."""
    s = max(0, int(seconds))
    if s < 60:
        return f"{s} s"
    m = s // 60
    if m < 60:
        return f"{m} min"
    h, m = divmod(m, 60)
    if h < 48:
        return f"{h} h {m} min" if m else f"{h} h"
    d, h = divmod(h, 24)
    return f"{d} d {h} h" if h else f"{d} d"


def plain(text):
    """The text without its colour codes."""
    out, i = [], 0
    while i < len(text):
        if text[i] == "\033":
            j = text.find("m", i)
            i = len(text) if j < 0 else j + 1
            continue
        out.append(text[i])
        i += 1
    return "".join(out)


def mark(state, text):
    sym, col = {"done": ("●", GREEN), "under way": ("◐", YELLOW), "failed": ("✗", RED)}.get(state, ("○", DIM))
    return f"{col}{sym}{RESET} {text}"


def question_counts(st):
    """(planned, closed): from the questions the status holds, or the counts pertura_status gave."""
    qs = st.get("questions") or {}
    if qs:
        return len(qs), sum(1 for q in qs.values() if q.get("status"))
    counts = st.get("question_counts") or {}
    total = sum(int(v) for v in counts.values())
    return total, total - int(counts.get("open", 0))


def finding_counts(st):
    """(findings, those the user has not answered) from the status (docs/adr/0032, D6)."""
    fs = st.get("findings") or {}
    return len(fs), sum(1 for f in fs.values() if not f.get("replies"))


def stages(st):
    """The stages in order, each done (●), under way (◐) or not reached (○): the design, the screen object, the
    findings (docs/adr/0032: findings, not closed questions, are what the analysis is made of), and the report once
    written."""
    out = []
    d = st.get("design") or {}
    if d.get("confirmed"):
        out.append(mark("done", "design confirmed"))
    elif d.get("open"):
        out.append(mark("under way", f"design: {len(d['open'])} to confirm"))
    else:
        out.append(mark("not yet", "design"))
    so = (st.get("screen_object") or {}).get("state")
    out.append(mark("done", "screen object built") if so == "built" else
               mark("under way", "building the screen object") if so == "building" else
               mark("failed", "screen object build failed") if so == "failed" else
               mark("not yet", "screen object"))
    found, waiting = finding_counts(st)
    out.append(mark("under way", f"{found} finding{'s' if found != 1 else ''}"
                                 + (f", {waiting} awaiting your reply" if waiting else ""))
               if found else mark("not yet", "no finding yet"))
    if st.get("report"):
        out.append(mark("done", "report written"))
    return out


def first_line(st):
    """Pertura and the project, the digest's line (the thread the agent is on, and what waits on the user) once the
    server has given one, then the stages; the open items and the pending items, for the agent and for the user."""
    lead = st.get("digest_line")
    parts = [f"Pertura · {st.get('name') or 'project'}", *([lead] if lead else []),
             f" {DIM}─{RESET} ".join(stages(st))]
    extra = []
    n = st.get("open_items")
    if n:
        extra.append(f"{n} open item{'s' if n != 1 else ''}")
    due = st.get("due") if isinstance(st.get("due"), dict) else {}  # the pending list, by decider
    for decider, whose in (("main agent", "for the agent"), ("user", "for you")):
        if due.get(decider):
            extra.append(f"{len(due[decider])} {whose}")
    if extra:
        parts.append(", ".join(extra))
    return " · ".join(parts)


def job_line(running, ended, failed_check_at, now, clock=None):
    """The second line: the running jobs [(started, seen, what[, progress])], else the jobs that ended in the last minutes
    [(ended, state, what)], else None. A job not checked for LATE seconds is not called running: it says when it was
    last checked (clock: seconds to the time of day, from the status line, which imports time).
    failed_check_at: when the server was last tried and not reached."""
    if running:
        running = [tuple(j) + ("",) * (4 - len(j)) for j in running]
        fresh = sorted(j for j in running if not j[1] or now - j[1] < LATE)
        stale = sorted((j for j in running if j[1] and now - j[1] >= LATE), key=lambda j: j[1])
        bits = []
        if fresh:
            head = f"{YELLOW}{SPINNER[int(now) % len(SPINNER)]}{RESET} running"
            bits.append(f"{head}  " + " · ".join(f"{what} {ago(now - started)}" + (f" ({far})" if far else "")
                                                for started, _, what, far in fresh))
        if stale:
            since = clock or (lambda t: f"{ago(now - t)} ago")
            bits.append(f"{DIM}" + " · ".join(f"{what} not checked since {since(seen)}" for _, seen, what, _ in stale)
                        + f"{RESET}")
        line = "  ".join(bits)
        if failed_check_at:
            line += f"  {DIM}(server not reached, last tried {ago(now - failed_check_at)} ago){RESET}"
        return line
    recent = sorted((e for e in ended if now - e[0] < RECENT), reverse=True)[:3]
    if not recent:
        return None
    bits = []
    for t, state, what in recent:
        sym = f"{GREEN}✓{RESET}" if state == "done" else f"{DIM}•{RESET}" if state == "ended" else f"{RED}✗{RESET}"
        word = {"done": "finished", "stopped": "stopped", "ended": "ended"}.get(state, "failed")
        bits.append(f"{sym} {what} {word} {ago(now - t)} ago")
    return "  ".join(bits)
