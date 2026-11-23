"""Pertura's hooks for the status line (hooks/hooks.json runs them; Claude Code passes the event's JSON on stdin).

    python -S hooks.py after-tool-call   after each Pertura tool call: fold it into the status file, and start the
                                         job check while a job runs; the reply's figures go to the page, and to the
                                         main agent a run's own figure, the others only where asked (agent_view);
                                         after a main-agent analysis call, the recorder reads the turn so far
    python -S hooks.py before-wait       before wait_for_runs: the project holds an armed wait until it returns
    python -S hooks.py stop              when the agent would end its turn: the turn recorded, then send it back
                                         where the record holds items open, nothing waits on the user, or a run
                                         runs with no wait armed (stop_words); counted and capped, never while a
                                         wait is armed
    python -S hooks.py session-start     copy the status line and subagent rows to a fixed place, and say once how
                                         to install the status line and the output style
    python -S hooks.py session-end       mark the session ended, so the job check stops for it
    python -S hooks.py user-prompt       when the user writes after 10 minutes or more, or after a finding: ask the
                                         agent to give them the digest first
    python -S hooks.py agent-model       before the main agent launches one of Pertura's agents: the model the user
                                         chose for it in the plugin's options (/config)
    python -S hooks.py subagent-stop     when one of Pertura's agents ends: its turn goes to the recorder
    python -S hooks.py before-report     before write_report: the turn so far is recorded first, in place

The recorder (recorder.py, docs/adr/0051) records the main agent's turn after each of its analysis calls and at its
end, and each Pertura subagent when it ends, in the background; a user's fixed words (a next step's pick, a result
kept or dropped) go to the record as the prompt is submitted.

A hook never stands in Claude's way: whatever goes wrong, it exits 0 and prints nothing, and writes the error to
hook_errors.log beside the status files.
"""
import json
import os
import shutil
import sys
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import job_check  # noqa: E402
import recorder  # noqa: E402
import status_file as sf  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
COPIED = ("status_line.py", "status_words.py", "subagent_rows.py")
HOST_VARIABLE = "CLAUDE_PLUGIN_OPTION_ANALYSIS_HOST"
FIRST_RUN = (
    "Pertura can show the analysis's stage and its running jobs below the prompt. To turn it on, add this to "
    "~/.claude/settings.json:\n"
    '  "statusLine": {"type": "command", "command": "python -S \\"$HOME/.claude/pertura/status_line.py\\"", '
    '"refreshInterval": 1}\n'
    "For replies that always open with the stage, the findings, the next step and the wait: "
    "/output-style, then Pertura. The figures and the findings are on a page on this "
    "machine: ~/.claude/pertura/projects/<the project>/page.html, written after each Pertura call. A long analysis "
    "costs less when the conversation is compacted at 200,000 tokens: add \"env\": {\"CLAUDE_CODE_AUTO_COMPACT_WINDOW\": "
    "\"200000\"} to the same file. (Shown once; the plugin's README has all four.)")
MAX_LOG = 100_000
MODELS = ("opus", "sonnet", "haiku", "fable")  # the aliases a launch's model takes


def after_tool_call(event, root=None, start_check=job_check.start, open_page=None):
    st = sf.record_call(event, root, open_page=open_page)
    # the plugin's option, else the host the server says it was launched from (a development launch sets no option)
    host = os.environ.get(HOST_VARIABLE) or (st or {}).get("analysis_host")
    if st and sf.running_jobs(st) and host:
        start_check(host, root)
    return st


# the figures a reply carries reach the user's page (record_call saved them). The main agent's context, read again on
# every call, was most of a session's cost (the fair comparison, 2026-11-03), so it takes a run's own figure, once,
# when the run's result is opened, and the others where asked; with every figure held back it saw no module's figure
SHOWN = ("read_record", "build_screen_object", "confirm_design")  # figures asked for by id; the cells' overview,
# read to propose the quality rule
RUN_FIGURE = "the figure of run "  # how the reply's "images" names a run's own figure (analysis_service.RUN_FIGURE)
FIGURES_KEPT = ("The figures went to the user's page, not into your context. To look at one: Read its image path "
                "above, or read_record with what figures and its id; show_figures: true on a call shows them.")


def _without_images(x, keep=lambda block: False):
    """x without its image blocks, but those keep(block) is true for, asked of each in the blocks' order."""
    if isinstance(x, list):
        return [_without_images(v, keep) for v in x
                if not (isinstance(v, dict) and v.get("type") == "image" and not keep(v))]
    if isinstance(x, dict):
        return {k: _without_images(v, keep) for k, v in x.items()}
    return x


def _image_names(response):
    """What each image of a reply is, in their order: the reply's "images", or [] (a reply that names none)."""
    if isinstance(response, dict):
        if isinstance((response.get("structuredContent") or {}).get("images"), list):
            return response["structuredContent"]["images"]
        response = response.get("content")
    for b in response if isinstance(response, list) else []:
        text = str(b.get("text") or "") if isinstance(b, dict) and b.get("type") == "text" else ""
        if text.lstrip().startswith("{"):
            try:
                names = json.loads(text).get("images")
            except (ValueError, AttributeError):
                continue
            if isinstance(names, list):
                return names
    return []


def agent_view(event):
    """The PostToolUse answer that keeps a reply's figures out of the main agent's context but a run's own figure, or
    None (the reply as it is): in a subagent's call (its own short context), where the call asked (show_figures), and
    for SHOWN."""
    tool = str(event.get("tool_name") or "").rsplit("__", 1)[-1]
    if (event.get("agent_id") or tool in SHOWN or (event.get("tool_input") or {}).get("show_figures")
            or not str(event.get("tool_name") or "").startswith(sf.TOOL_PREFIX)):
        return None
    response = event.get("tool_response")
    names = iter(_image_names(response))
    kept = _without_images(response, lambda block: str(next(names, "")).startswith(RUN_FIGURE))
    if kept == response:
        return None
    note = {"type": "text", "text": FIGURES_KEPT}
    if isinstance(kept, dict) and isinstance(kept.get("content"), list):
        kept = {**kept, "content": kept["content"] + [note]}
    elif isinstance(kept, list):
        kept = kept + [note]
    else:
        return None
    return {"hookSpecificOutput": {"hookEventName": "PostToolUse", "updatedToolOutput": kept}}


# the main agent's calls whose results it states as claims: after each, the recorder reads the turn so far
ANALYSIS = sf.KINDS + ("run_code", "run_result", "wait_for_runs")


def start_reading(event, start=recorder.start):
    """After a main-agent analysis call, the turn so far goes to the recorder in the background, one reading at a time
    (a call during a reading leaves it a flag to read again), so claims enter the record while the agent works. A
    subagent's calls (agent_id) are left to its own reading when it ends."""
    if event.get("agent_id") or sf.short_tool(event.get("tool_name", "")) not in ANALYSIS:
        return None
    return start(event.get("session_id"), event.get("transcript_path"), queue=True)


def before_wait(event, root=None):
    return sf.arm_wait(event, root)


STOP_RETURNS = 3  # the returns in a turn for items the agent can settle (pending, doubts, the task list, the asks once, "nothing")
STOP_WAITS = 1200  # seconds of a stretch of Stops sent back to wait (runs, checks, the reading), from its first
STOP_BUDGET = 280  # seconds the Stop hook may take (hooks.json gives it 300)
RECORD_WAIT = 240  # of which the turn's recording may take


WAITS = ("inbox", "checks_running", "reading")  # what the agent is sent back to wait for, uncounted
HELD = ("doubts_open", "choices_open", "todos_open", "answers_to_update")  # what it is sent back to settle, counted


def stop(event, root=None, now=None, facts=None):
    """The Stop hook's answer: {decision: block, reason}, or None to let the turn end (stop_words). Each answer is
    written beside the session's status as the turn's decision (_decided), for a harness that ends a session only
    where nothing is open. facts: what the record holds open (record_facts, given in tests)."""
    import stop_words
    t0 = time.time()
    st = sf.session_status(event.get("session_id") or "unknown", root)
    if not st or not st.get("project"):
        return None  # not a Pertura session
    path = sf.session_path(event.get("session_id") or "unknown", root, ".stop.json")
    reply, asked = stop_words.last_turn(event)
    waiting = stop_words.waiting_on(reply)
    if stop_words.user_needs_the_turn(asked) or waiting == "you":
        return _decided({"path": path}, "end", {"the_user": True}, now)
    if sf.wait_armed(st, now):  # the wait's return wakes the agent; the harness holds the run meanwhile
        found = facts(event, st) if facts else record_facts(event, st, t0)
        return _decided({"path": path}, "wait", {**(found or {}), "wait_armed": True}, now)
    turn = _turn(event, root, now)
    if turn is None:  # the count cannot be kept: the turn ends rather than loop
        return None
    now = time.time() if now is None else now
    settle = turn["returns"] < STOP_RETURNS
    if sf.running_jobs(st) and waiting in ("a job", "nothing", None):
        jobs = {"runs": sorted(str(j.get("run_id") or j.get("job_id") or "") for j in sf.running_jobs(st))}
        if _waits(turn, now):
            return _back(turn, stop_words.ARM, jobs, counted=False, wait=now)
        return _decided(turn, "end", jobs, now)
    found = facts(event, st) if facts else record_facts(event, st, t0)
    todos = stop_words.todos_open(event)  # the agent's own list of the question: with none, nothing is required
    if todos:
        found = {**(found or {}), "todos_open": todos}
    if found is None:  # the record cannot be read: the last reply's pending list, as before
        due = st.get("due") if isinstance(st.get("due"), dict) else {}  # {decider: [labels]}
        mine = due.get(stop_words.MAIN) or []
        if mine and settle:  # the user's items are for the agent's reply and the status line
            return _back(turn, stop_words.pending(mine), {"pending": mine})
    else:
        if not any(found.get(k) for k in WAITS) and turn.get("waits_since"):
            turn = _kept(turn, waits_since=None)  # nothing left to wait on: a later wait has its own stretch
        asks = _asks(found, todos, turn) if settle else []
        held = settle and any(found.get(k) for k in HELD)
        wait_on = any(found.get(k) for k in WAITS) and _waits(turn, now)
        if held or wait_on or asks:
            if asks:
                turn = _kept(turn, asked=(turn.get("asked") or []) + asks)
            words = {"task_list": stop_words.NO_TASK_LIST, "choice": stop_words.NO_CHOICE}
            reason = [stop_words.open_items(found, wait_on)] if held or wait_on else []
            return _back(turn, "\n\n".join([words[k] for k in asks] + reason),  # the asks first, never under a list
                         {**found, **({"asked": asks} if asks else {})}, counted=held or bool(asks),
                         wait=now if wait_on else None)
    if waiting == "nothing" and settle:
        return _back(turn, stop_words.CARRY_ON, {"waiting_on": "nothing"})
    return _decided(turn, "end", found or {}, now)


ASKS = ("task_list", "choice")  # each asked once a session (stop_words.NO_TASK_LIST, NO_CHOICE)


def _asks(found, todos, turn):
    """The one-time asks due where the record holds a finding: a task list where the agent made none, and a Choice or
    No choice line where it said neither (read once the inbox is taken, as the line may be in it)."""
    if not found.get("findings"):
        return []
    asked = turn.get("asked") or []
    due = {"task_list": todos is None, "choice": not found.get("choice_said") and not found.get("inbox")}
    return [k for k in ASKS if due[k] and k not in asked]


def _waits(turn, now):
    """Whether the stretch of Stops sent back to wait is still under STOP_WAITS."""
    return now - float(turn.get("waits_since") or now) < STOP_WAITS


def _turn(event, root=None, now=None):
    """The turn's returns and the time of its first Stop, kept beside the session's status: a turn starts at the
    user's last message (its uuid, or the turn kept before where the transcript's tail holds none). None where the
    file cannot be read or written."""
    import stop_words
    path = sf.session_path(event.get("session_id") or "unknown", root, ".stop.json")
    now = time.time() if now is None else now
    try:
        kept = sf.read_json(path, {}) or {}
        key = stop_words.turn_key(event) or kept.get("turn") or "start"
        turn = kept if kept.get("turn") == key else {"turn": key, "returns": 0, "first": now, "waits_since": None,
                                                     **({"asked": kept["asked"]} if kept.get("asked") else {})}
        sf.write_json(path, turn)
        return {**turn, "path": path}
    except (OSError, ValueError, TypeError):
        return None


def _kept(turn, **changed):
    """The turn with changed values, written beside the session's status (unwritten where the file cannot be)."""
    turn = {**turn, **changed}
    try:
        sf.write_json(turn["path"], {k: v for k, v in turn.items() if k != "path"})
    except OSError:
        pass
    return turn


def _back(turn, reason, open_, counted=True, wait=None):
    """Send the agent back with reason, to settle what is open (counted in the turn's returns) or to wait (wait: now,
    which starts the stretch of waits where none runs)."""
    turn = _kept(turn, returns=int(turn.get("returns") or 0) + (1 if counted else 0),
                 waits_since=turn.get("waits_since") or wait)
    _decided(turn, "back", open_)
    return {"decision": "block", "reason": reason}


def _decided(turn, decision, open_, now=None):
    """The turn's decision, beside the session's status (<session>.stop.json): decision "end" (the turn ends with
    nothing open, the user's turn, or its caps spent), "back" (the agent sent back) or "wait" (the turn ends while a
    wait is armed, so items stay open: a harness holds the run), what was open (stop-facts' lists), the returns, the
    stretch of waits and when. Returns None, the hook's answer where the turn ends."""
    kept = sf.read_json(turn["path"], {}) or {}
    _kept({**kept, **turn}, decision=decision, open=open_, at=time.time() if now is None else now)
    return None


def record_facts(event, st, t0=None):
    """What the record holds open at the turn's end: the turn recorded first where text is past the mark, the inbox
    applied with its checks queued, then stop-facts (recorder.stop_facts). Where no check runs but a skeptic's reading
    does, the hook waits for it while its time lasts, so its doubts are read too. None where the server cannot be
    read."""
    t0 = time.time() if t0 is None else t0
    host = os.environ.get(HOST_VARIABLE) or st.get("analysis_host")
    project = st.get("project")
    session, transcript = event.get("session_id"), event.get("transcript_path")
    if not recorder.remote_paths(project)[0]:
        return None
    if session and transcript and recorder.unread(session, transcript):
        recorder.record_now(session, transcript, wait=min(RECORD_WAIT, STOP_BUDGET - (time.time() - t0)))
    found = recorder.stop_facts(host, project)
    if found is not None and not found.get("checks_running") and recorder.readings_running(session):
        while recorder.readings_running(session) and time.time() - t0 < STOP_BUDGET - 60:
            time.sleep(5)
        found = recorder.stop_facts(host, project) or found
    if found is not None:
        found["reading"] = bool(recorder.readings_running(session))
    return found


def page_state_at_turn_end(event, root=None, start=job_check.start_page_state):
    """The turn has ended: the project's page state is fetched for the user's page, read here when the project is on
    this machine, else in the background over ssh."""
    session = sf.read_json(sf.session_path(event.get("session_id") or "unknown", root)) or {}
    key = session.get("project")
    if not key:
        return None
    st = sf.read_json(sf.project_path(key, root)) or {}
    project = str(st.get("project") or "")
    if project and os.path.isfile(os.path.join(project, job_check.PAGE_STATE)):
        return job_check.fetch_page_state(None, key, root)
    host = os.environ.get(HOST_VARIABLE) or st.get("analysis_host")
    return start(host, key, root)


AWAY_SECONDS = 600  # the user's message this long after the agent's turn ended gets the digest first
BACK = ("The user is back. Before you answer them, call pertura_status and give them its digest in a few plain lines, "
        "in their language: the decisions waiting on them, the thread you are on and your reason, the changes "
        "since, by thread, and the options next.")


NEW_SESSION = ("This folder's Pertura project ({name}) was worked on in an earlier session. Before you answer, call "
               "pertura_status and give the user its digest in a few plain lines, in their language: the decisions "
               "waiting on them, the thread the work was on and its reason, the changes since, by thread, and the "
               "options next.")


def _same_folder(a, b):
    return bool(a and b) and os.path.normcase(os.path.normpath(str(a))) == os.path.normcase(os.path.normpath(str(b)))


def earlier_project(cwd, session_id, root=None):
    """The project of the latest earlier session started in the same folder, whose status is still kept; or None."""
    folder = os.path.dirname(sf.session_path(session_id, root))
    own = os.path.basename(sf.session_path(session_id, root))
    best = None
    for name in os.listdir(folder) if os.path.isdir(folder) else []:
        if name == own or name.count(".") != 1 or not name.endswith(".json"):  # sessions only, not their .turn files
            continue
        s = sf.read_json(os.path.join(folder, name), {}) or {}
        if s.get("project") and _same_folder(s.get("cwd"), cwd) and os.path.exists(sf.project_path(s["project"], root)):
            if best is None or float(s.get("updated") or 0) > float(best.get("updated") or 0):
                best = s
    return best["project"] if best else None


def turn_ended(event, root=None, now=None):
    """The agent's turn ended (the Stop hook let it end): when, for the user's return."""
    sf.write_json(sf.session_path(event.get("session_id") or "unknown", root, ".turn.json"),
                  {"ended": time.time() if now is None else now})


def user_prompt(event, root=None, now=None):
    """The UserPromptSubmit hook's answer: context asking for the digest first, when the user writes AWAY_SECONDS or
    more after the agent's turn ended, or writes the first message of a new session in a folder whose earlier session
    worked on a project (once). None when there is neither, or outside a Pertura session."""
    now = time.time() if now is None else now
    key = event.get("session_id") or "unknown"
    st = sf.session_status(key, root)
    if not st or not st.get("project"):
        turn_file = sf.session_path(key, root, ".turn.json")
        project = None if os.path.exists(turn_file) else earlier_project(event.get("cwd"), key, root)
        if not project:
            return None
        sf.write_json(turn_file, {"ended": None, "new_session_told": now})  # once: the session's first message
        name = (sf.read_json(sf.project_path(project, root), {}) or {}).get("name") or project
        return {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit",
                                       "additionalContext": NEW_SESSION.format(name=name)}}
    turn = sf.read_json(sf.session_path(key, root, ".turn.json"), {}) or {}
    away = bool(turn.get("ended")) and now - float(turn["ended"]) >= AWAY_SECONDS
    if not away:
        return None
    return {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": BACK}}


def install_copies(root=None):
    """The status line's command names a fixed path; the plugin's own folder moves with each update. The catalog's
    figures go beside the projects' pages, which show them (page.CATALOG_FIGURES)."""
    root = root or sf.home()
    os.makedirs(root, exist_ok=True)
    for name in COPIED:
        _copy(os.path.join(HERE, name), os.path.join(root, name))
    figures = os.path.join(HERE, "catalog_figures")
    for name in sorted(os.listdir(figures)) if os.path.isdir(figures) else []:
        os.makedirs(os.path.join(root, "catalog_figures"), exist_ok=True)
        _copy(os.path.join(figures, name), os.path.join(root, "catalog_figures", name))


def _copy(src, dst):
    """A file copied where its copy differs, through a temporary file. A copy held open elsewhere (on Windows: a
    browser showing it, another session's start copying it) stays as it is until a later start."""
    with open(src, "rb") as fh:
        new = fh.read()
    try:
        with open(dst, "rb") as fh:
            if fh.read() == new:
                return
    except OSError:
        pass
    tmp = f"{dst}.{os.getpid()}.tmp"
    shutil.copyfile(src, tmp)
    try:
        os.replace(tmp, dst)
    except PermissionError:
        os.remove(tmp)


def status_line_installed(settings_path=None):
    path = settings_path or os.path.join(os.path.expanduser("~"), ".claude", "settings.json")
    try:
        with open(path, encoding="utf-8") as fh:
            return "pertura/status_line.py" in fh.read()
    except OSError:
        return False


COMPACTED = ("The conversation was compacted. Before you go on, call pertura_status: your plan and its state, "
             "the findings and the runs are in the record. Then read_record with what notebook: your notebook's "
             "cells in order, to pick your main line up where you left it.")


def session_start(event, root=None, settings_path=None):
    """The copies; after a compaction in a Pertura session, context asking the agent to read where it stands; at a
    first start, the first-run message as {systemMessage}; or None."""
    root = root or sf.home()
    install_copies(root)
    if event.get("source") == "compact":  # the plan, the findings and the runs outlive the conversation's context
        st = sf.session_status(event.get("session_id") or "unknown", root)
        if not st or not st.get("project"):
            return None
        return {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": COMPACTED}}
    marker = os.path.join(root, "first_run_shown")
    if event.get("source") not in (None, "startup") or os.path.exists(marker):
        return None
    if status_line_installed(settings_path):
        return None
    with open(marker, "w") as fh:
        fh.write(str(time.time()))
    return {"systemMessage": FIRST_RUN}


def session_end(event, root=None):
    sp = sf.session_path(event.get("session_id") or "unknown", root)
    if not os.path.exists(sp):
        return
    with sf.Lock(sp, wait=1.0):
        s = sf.read_json(sp) or {}
        s["ended"] = time.time()
        sf.write_json(sp, s)


def _log(root, text):
    path = os.path.join(root or sf.home(), "hook_errors.log")
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if os.path.exists(path) and os.path.getsize(path) > MAX_LOG:
            os.replace(path, path + ".old")
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(time.strftime("%Y-%m-%d %H:%M:%S ") + text + "\n")
    except OSError:
        pass


def agent_model(event, env=None):
    """The model the user chose for this Pertura agent (its option, such as worker_model, in /config) as the
    launch's own model, which outranks the one its file names; "default" leaves the launch as it is."""
    env = os.environ if env is None else env
    given = event.get("tool_input") or {}
    plugin, _, name = str(given.get("subagent_type") or "").partition(":")
    option = "CLAUDE_PLUGIN_OPTION_" + name.replace("-", "_").upper() + "_MODEL"
    model = env.get(option, "") if plugin == "pertura" else ""
    if model not in MODELS or given.get("model") == model:
        return None
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "allow",
                                   "updatedInput": {**given, "model": model}}}


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    which = argv[0] if argv else ""
    if os.environ.get(recorder.GUARD):  # the recorder's own model session: no hook of it records again
        return 0
    try:
        event = json.loads(sys.stdin.buffer.read().decode("utf-8") or "{}")
        if not isinstance(event, dict):
            event = {}
        if which == "after-tool-call":
            try:
                after_tool_call(event, open_page=sf.open_in_browser if sf.open_page_wanted() else None)
                turn_ended(event)  # the agent was at work just now: the user's next message counts from here
                start_reading(event)
            finally:  # the figures stay out of the agent's context even when the page could not be written
                out = agent_view(event)
                if out:
                    sys.stdout.write(json.dumps(out))
        elif which == "before-wait":
            before_wait(event)
        elif which == "stop":
            out = stop(event)
            if out:
                sys.stdout.write(json.dumps(out))
            else:
                turn_ended(event)
                page_state_at_turn_end(event)
                recorder.start(event.get("session_id"), event.get("transcript_path"), queue=True)
        elif which == "session-start":
            out = session_start(event)
            if out:
                sys.stdout.write(json.dumps(out))
        elif which == "session-end":
            session_end(event)
        elif which == "subagent-stop":
            if str(event.get("agent_type") or "").startswith("pertura:"):
                recorder.start(event.get("session_id"), event.get("agent_transcript_path"),
                               agent=event.get("agent_type"), agent_id=event.get("agent_id"), queue=True)
        elif which == "before-report":
            recorder.main(["--session", str(event.get("session_id") or ""),
                           "--transcript", str(event.get("transcript_path") or "")])
        elif which == "user-prompt":
            try:
                recorder.record_prompt(event)
            except Exception:  # the user's words are still read at the turn's end
                _log(None, f"record_prompt: {traceback.format_exc()}")
            out = user_prompt(event)
            if out:
                sys.stdout.write(json.dumps(out))
        elif which == "agent-model":
            out = agent_model(event)
            if out:
                sys.stdout.write(json.dumps(out))
    except Exception:
        _log(None, f"{which}: {traceback.format_exc()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
