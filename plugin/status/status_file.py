"""The local status file: what the status line, the subagent rows and the tree views read.

The analysis runs on a server reached over ssh, so nothing local knows its state. A hook after each Pertura tool call
reads the call (its name, its input and what it returned) and keeps a small JSON file per project current, with:
- the project and the design (confirmed or not);
- the screen object (not built, building, built);
- the planned questions with their status;
- the findings, each with its sentence and the user's replies (docs/adr/0032, D6), the doubts and the branches that
  test them;
- the user's page: the design, the findings and the results, with the figures the tool calls returned to the
  agent, saved beside the file (page.py writes it after each call; the hook opens it in the browser once when a
  session opens a project);
- the running jobs with their start times;
- the number of open items, and the pending list by decider (the agent's items and the user's).

A view reads the file and computes nothing from the server; elapsed times are computed from the start times. A
session points to its project's file, so a new session on the same project starts from what the last one saw.
Beside each project's JSON is its line file (line_file), the status line's input composed here, so the status
line, which starts afresh every second, needs no json.

Standard library only: it runs on the user's machine, where Pertura itself is not installed.
"""
import hashlib
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import page_and_session_helper as window  # noqa: E402
import page as user_page  # noqa: E402
import status_words as words  # noqa: E402

SCHEMA = "pertura-status/1"
LINE_SCHEMA = "pertura-line/1"
TOOL_PREFIX = "mcp__plugin_pertura_pertura__"
ACTIVE = words.ACTIVE
HOME_VARIABLE = "PERTURA_STATUS_HOME"
KEEP_FINISHED, DAY = 20, 86400.0
READS = ("open_project", "pertura_status", "get_design", "confirm_design", "build_screen_object", "plan_questions",
         "add_question", "close_question", "run_module", "module_result", "null_of_right_unit", "run_code",
         "wait_for_runs", "record_finding", "reword_finding", "note_reply", "raise_doubt", "answer_doubt",
         "open_branch",
         "close_branch", "open_items", "read_record", "write_report", "open_thread", "thread_status",
         "report_result")
OPEN_PAGE_VARIABLE = "CLAUDE_PLUGIN_OPTION_OPEN_PAGE"  # the plugin's open_page option, as hooks receive it
# the analysis server's tools by the field's kinds (pertura.kinds), each a module run, and the tools renamed
KINDS = ("guides", "impact", "genes", "scores", "states", "programs", "similarity", "conditions", "combinations",
         "enrichment", "reproducibility")
RENAMED = {"run_result": "module_result"}  # the module menu's tools were retired (2026-11-16)


def home():
    return os.environ.get(HOME_VARIABLE) or os.path.join(os.path.expanduser("~"), ".claude", "pertura")


def project_key(project):
    """The file name of a project's status: the folder's name (name and hash) and a hash of its whole path, made
    safe, so one deposit's projects in two workspaces keep two statuses."""
    path = str(project).replace("\\", "/").rstrip("/")
    name = re.sub(r"[^A-Za-z0-9_.-]", "-", os.path.basename(path) or "project")[:100]
    return f"{name}--{hashlib.sha256(path.encode()).hexdigest()[:8]}"


def session_path(session_id, root=None, ext=".json"):
    return os.path.join(root or home(), "sessions", re.sub(r"[^A-Za-z0-9_-]", "-", str(session_id))[:120] + ext)


def project_path(key, root=None, ext=".json"):
    return os.path.join(root or home(), "projects", key + ext)


def read_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def write_text(path, text):
    """Write whole or not at all, so a reader never sees half a file."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    user_page.replace_file(tmp, path)


def write_json(path, obj):
    write_text(path, json.dumps(obj, indent=1))


def _field(text):
    return " ".join(str(text or "").split())  # one line, no tabs: the line file's separators


def line_file(st):
    """The status line's input, composed here so the status line needs no json: a header, then
    1<tab>the first line; J<tab>started<tab>last seen<tab>what<tab>progress, per running job;
    E<tab>ended<tab>state<tab>what, per finished job; X<tab>when, if the last check could not reach the server."""
    rows = [LINE_SCHEMA, "1\t" + _field(words.first_line(st))]
    checked = st.get("jobs_checked") or {}
    for j in st.get("jobs", {}).values():
        what = _field(j.get("what") or j.get("run_id"))
        if j.get("state") in ACTIVE:
            seen = max(float(j.get("checked") or 0), float(checked.get("at") or 0))
            rows.append(f"J\t{float(j.get('started') or 0):.0f}\t{seen:.0f}\t{what}\t{_field(j.get('progress'))}")
        elif j.get("ended") is not None:
            rows.append(f"E\t{float(j['ended']):.0f}\t{_field(j.get('state'))}\t{what}")
    if checked and not checked.get("ok"):
        rows.append(f"X\t{float(checked.get('at') or 0):.0f}")
    return "\n".join(rows) + "\n"


def save(key, st, root=None):
    """The project's status, and its line file beside it."""
    write_json(project_path(key, root), st)
    write_text(project_path(key, root, ".line"), line_file(st))


class Lock:
    """One writer at a time across processes (parallel tool calls run their hooks at once): a folder made
    atomically, taken over when older than `stale` seconds."""

    def __init__(self, path, wait=5.0, stale=30.0):
        self.path, self.wait, self.stale = path + ".lock", wait, stale

    def __enter__(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        t0 = time.time()
        while True:
            try:
                os.mkdir(self.path)
                return self
            except FileExistsError:
                try:
                    if time.time() - os.path.getmtime(self.path) > self.stale:
                        os.rmdir(self.path)
                        continue
                except OSError:
                    continue
                if time.time() - t0 > self.wait:
                    raise TimeoutError(f"the status file is locked: {self.path}")
                time.sleep(0.02)

    def __exit__(self, *exc):
        try:
            os.rmdir(self.path)
        except OSError:
            pass


def empty(project=None):
    return {"schema": SCHEMA, "project": project, "name": None, "data_folder": None, "updated": None,
            "design": {"confirmed": None, "open": []}, "screen_object": {"state": "unknown", "job_id": None},
            "questions": {}, "order": [], "question_counts": None, "findings": {}, "doubts": {}, "branches": {},
            "jobs": {}, "open_items": None, "due": {}, "report": None, "last_call": None}


def display_name(project):
    """The project folder's name without the hash Pertura adds (name-1a2b3c4d)."""
    name = os.path.basename(str(project).replace("\\", "/").rstrip("/"))
    return re.sub(r"-[0-9a-f]{8}$", "", name) or name


def response_dict(response):
    """What a Pertura tool returned, as a dict: the tools return one JSON object, which reaches a hook as the
    structured content, as content blocks, or as their text."""
    if isinstance(response, dict):
        if isinstance(response.get("structuredContent"), dict):
            return response["structuredContent"]
        if isinstance(response.get("content"), list):
            return response_dict(response["content"])
        if "type" in response and "text" in response:
            return response_dict(response["text"])
        return response
    if isinstance(response, list):
        for block in response:
            if isinstance(block, dict) and block.get("type") == "text":
                got = response_dict(block.get("text"))
                if isinstance(got, dict):
                    return got
        return None
    if isinstance(response, str):
        try:
            got = json.loads(response)
        except ValueError:
            return None
        return got if isinstance(got, dict) else None
    return None


def failed(reply):
    """Whether a call failed: Pertura's error reply, or none. A notebook cell that raised is a run with its result."""
    return not isinstance(reply, dict) or bool(reply.get("isError")) or (bool(reply.get("error"))
                                                                         and not reply.get("run_id"))


def short_tool(tool_name):
    return tool_name[len(TOOL_PREFIX):] if str(tool_name).startswith(TOOL_PREFIX) else None


def as_module_call(tool, hook_input):
    """A call of a tool by the field's kinds as the module run it started (its reply's analysis names the module),
    and a renamed tool by its earlier name, as the status and the page read them (docs/adr/0044)."""
    if tool in KINDS:
        r = response_dict(hook_input.get("tool_response")) or {}
        module = str(r.get("analysis") or "").rpartition(": ")[2] or tool
        return "run_module", {**hook_input, "tool_input": {**(hook_input.get("tool_input") or {}), "module": module}}
    return RENAMED.get(tool, tool), hook_input


def _design_from(st, d):
    """get_design's shape (confirmed, open), also inside pertura_status, or confirm_design's (not_confirmed)."""
    if not isinstance(d, dict):
        return
    left = d.get("open") if "open" in d else d.get("not_confirmed")
    if left is None:
        return
    st["design"] = {"confirmed": bool(d.get("confirmed")) and not left,
                    "open": sorted(left) if isinstance(left, (dict, list)) else []}
    if d.get("screen_object") is True:
        st["screen_object"]["state"] = "built"


def _job(st, run_id, state, now, what=None, question_id=None, finding_id=None, kind="module", seconds=0.0):
    if not run_id:
        return None
    job = st["jobs"].get(run_id)
    if job is None:
        job = st["jobs"][run_id] = {"run_id": run_id, "what": what or f"run {run_id}", "kind": kind,
                                    "question_id": question_id, "finding_id": finding_id,
                                    "started": now - max(0.0, seconds), "state": state, "ended": None,
                                    "checked": now}
    job["state"], job["checked"] = state, now
    if what:
        job["what"] = what
    if state not in ACTIVE and job["ended"] is None:
        job["ended"] = now
    return job


def _questions_from(st, items):
    st["questions"], st["order"] = {}, []
    for q in items:
        if not isinstance(q, dict) or not q.get("question_id"):
            continue
        st["questions"][q["question_id"]] = {k: q.get(k) for k in ("letter", "item", "because", "status")}
        st["order"].append(q["question_id"])


def _record_from(st, r, whole=True):
    """read_record: the record itself, so its parts replace what the file held; read for some IDs only (whole
    False), they update those entries and leave the rest."""
    if isinstance(r.get("questions"), dict):
        items = [{**q, "question_id": q.get("question_id", k)} for k, q in r["questions"].items()]
        if whole:
            _questions_from(st, items)
        else:
            for q in items:
                st["questions"][q["question_id"]] = {k: q.get(k) for k in ("letter", "item", "because", "status")}
                if q["question_id"] not in st["order"]:
                    st["order"].append(q["question_id"])
    if isinstance(r.get("findings"), dict):
        old = st["findings"]
        st["findings"] = {} if whole else old
        for fid, f in r["findings"].items():
            row = {k: f.get(k) for k in ("question_id", "target", "feature", "direction")}
            row["choices"] = (old.get(fid) or {}).get("choices")
            plans = f.get("robustness") or {}
            if plans:
                last = list(plans.values())[-1]
                results = last.get("results") or {}
                row["choices"] = {"state": "done" if last.get("verdict") else "running",
                                  "analyses": ["primary"] + [v.get("name") for v in last.get("variants") or []],
                                  "outcomes": {k: v.get("outcome") for k, v in results.items()},
                                  "counts": last.get("counts"), "run_id": (row["choices"] or {}).get("run_id")}
            st["findings"][fid] = row
    if isinstance(r.get("doubts"), dict):
        if whole:
            st["doubts"], st["branches"] = {}, {}
        for key, v in r["doubts"].items():
            if key.startswith("branch "):
                st["branches"][key[len("branch "):]] = {k: v.get(k) for k in ("doubt_id", "plan", "outcome")}
            else:
                st["doubts"][key] = {"question_id": v.get("question_id"), "text": v.get("text"),
                                     "branches": list(v.get("branches") or [])}
    if isinstance(r.get("runs"), dict):
        for rid, run in r["runs"].items():
            job = st["jobs"].get(rid)
            if job is not None:
                job["state"] = run.get("state") or job["state"]


def apply_call(st, tool, tool_input, response, now=None, seconds=0.0):
    """Fold one Pertura tool call into the status. tool: the tool's short name; tool_input: its arguments;
    response: what it returned (any shape response_dict reads); seconds: how long the call took, so a job that
    started inside the call gets its true start. Returns the status."""
    now = time.time() if now is None else now
    a = tool_input if isinstance(tool_input, dict) else {}
    r = response_dict(response)
    st["last_call"] = {"tool": tool, "at": now}
    st["updated"] = now
    if failed(r):
        return st
    if isinstance(r.get("pending"), dict):  # what is pending, by decider, named on each main-agent reply: its only
        # writer (the orchestration page; open_items' due holds the critic's kinds)
        st["due"] = {str(k): [str(x) for x in v] for k, v in r["pending"].items() if isinstance(v, list)}
    if isinstance(r.get("digest"), dict):  # pertura_status's digest (item 5): the page's top and the status line
        st["digest"] = {**r["digest"], "at": now}
        st["digest_line"] = r["digest"].get("line")
    elif r.get("digest_line"):
        st["digest_line"] = r["digest_line"]
    if isinstance(r.get("coverage"), dict):  # pertura_status's lists: what applies and has not run, and so on
        st["coverage"] = r["coverage"]
    if "coverage_line" in r or isinstance(r.get("coverage"), dict):  # the turn-end line, as code last gave it
        st["coverage_line"] = str(r.get("coverage_line") or "")
    if tool in ("open_project",) and r.get("project"):
        st["project"], st["name"] = r["project"], display_name(r["project"])
        st["data_folder"] = r.get("data_folder")
    elif tool == "pertura_status" and r.get("project"):
        st["project"], st["name"] = r["project"], display_name(r["project"])
        st["data_folder"] = r.get("data_folder") or st["data_folder"]
        _design_from(st, r.get("design"))
        if r.get("screen_object") is True:
            st["screen_object"]["state"] = "built"
        if isinstance(r.get("questions"), dict):
            st["question_counts"] = {str(k): int(v) for k, v in r["questions"].items()}
        if isinstance(r.get("open_items"), int):
            st["open_items"] = r["open_items"]
    elif tool in ("get_design", "confirm_design"):
        _design_from(st, r)
    elif tool == "build_screen_object" and r.get("job_id"):
        state = r.get("state") or "running"
        _job(st, r["job_id"], state, now, what="building the screen object", kind="screen_object", seconds=seconds)
        st["screen_object"] = {"state": "built" if state == "done" else "building" if state in ACTIVE else "failed",
                               "job_id": r["job_id"]}
    elif tool == "plan_questions" and isinstance(r.get("questions"), list):
        _questions_from(st, r["questions"])
        st["question_counts"] = None
    elif tool == "add_question" and a.get("letter"):
        qid = r.get("question_id") or a.get("question_id")
        if qid:
            st["questions"][qid] = {"letter": a.get("letter"), "item": a.get("item"), "because": a.get("because"),
                                    "status": None}
            if qid not in st["order"]:
                st["order"].append(qid)
    elif tool == "close_question" and r.get("closed") and r.get("question_id"):
        q = st["questions"].setdefault(r["question_id"], {"letter": None, "item": None, "because": None})
        q["status"] = r.get("status")
        if r["question_id"] not in st["order"]:
            st["order"].append(r["question_id"])
        st["question_counts"] = None
    elif tool in ("run_module", "module_result", "check_robustness", "null_of_right_unit") and r.get("run_id") \
            and not (tool == "module_result" and "job_id" in r and not r["job_id"]):
        rid, state = r["run_id"], r.get("state") or "running"
        fid = a.get("finding_id")
        if tool == "run_module":  # a module serves the question of its own name unless the call names another
            what, kind = a.get("module"), "module"
            qid = a.get("question_id") or (a.get("module") if a.get("module") in st["questions"] else None)
        elif tool == "check_robustness":
            what, kind, qid = f"other analysis choices for {fid}", "choices", None
        elif tool == "null_of_right_unit":
            what, kind, qid = f"control-guide null for {fid}", "null", None
        else:
            what, kind, qid = None, "module", None
        job = _job(st, rid, state, now, what=what, question_id=qid, finding_id=fid, kind=kind,
                   seconds=seconds if tool != "module_result" else 0.0)
        job["progress"] = r.get("progress") if state in ACTIVE else None
        if job["kind"] == "choices" and job["finding_id"] in st["findings"]:
            f = st["findings"][job["finding_id"]]
            f["choices"] = {**(f.get("choices") or {}), "run_id": rid,
                            "state": "running" if state in ACTIVE else state}
            if r.get("variants") is not None:
                f["choices"].update(analyses=["primary"] + list(r["variants"]), counts=r.get("counts"))
    elif tool == "record_finding" and a.get("finding_id"):
        st["findings"][a["finding_id"]] = {**{k: a.get(k) for k in ("question_id", "target", "feature", "direction",
                                                                    "statement")},
                                           "choices": None, "replies": []}
    elif tool == "note_reply" and a.get("finding_id") in st["findings"]:
        st["findings"][a["finding_id"]].setdefault("replies", []).append({"kind": a.get("kind"), "at": now})
    elif tool == "raise_doubt" and a.get("doubt_id"):
        st["doubts"][a["doubt_id"]] = {"question_id": a.get("question_id"), "finding_id": a.get("finding_id"),
                                       "text": a.get("text"), "branches": []}
    elif tool == "answer_doubt" and a.get("doubt_id") in st["doubts"]:
        st["doubts"][a["doubt_id"]]["answer"] = a.get("outcome")
    elif tool == "open_branch" and a.get("branch_id"):
        st["branches"][a["branch_id"]] = {"doubt_id": a.get("doubt_id"), "plan": a.get("plan"), "outcome": None}
        d = st["doubts"].get(a.get("doubt_id"))
        if d is not None and a["branch_id"] not in d["branches"]:
            d["branches"].append(a["branch_id"])
    elif tool == "close_branch" and a.get("branch_id"):
        b = st["branches"].setdefault(a["branch_id"], {"doubt_id": None, "plan": None, "outcome": None})
        b["outcome"] = a.get("outcome")
    elif tool == "open_thread" and r.get("thread_id") and (a.get("alternatives") or a.get("doubt_id")):
        # a thread of alternatives, or one testing a doubt, is a branch as well (item 13)
        bid = r["thread_id"]
        st["branches"][bid] = {"doubt_id": a.get("doubt_id"), "plan": a.get("about"), "outcome": None}
        d = st["doubts"].get(a.get("doubt_id"))
        if d is not None and bid not in d["branches"]:
            d["branches"].append(bid)
    elif (tool == "thread_status" and a.get("thread_id") in st["branches"]
          and a.get("status") in ("explained", "contradicted", "out of data", "set aside")):
        st["branches"][a["thread_id"]]["outcome"] = a.get("reason")
    elif tool == "open_items" and isinstance(r.get("open"), list):
        st["open_items"] = len(r["open"])
    elif tool == "read_record" and not r.get("in_brief"):  # a reading in brief holds no entry to keep
        _record_from(st, r, whole=not a.get("ids"))
    elif tool in ("write_report", "report_result") and r.get("report"):  # code writes it with each result too
        st["report"] = {"path": r["report"], "at": now}
    _runs_from(st, tool, a, r, now)
    _prune(st, now)
    return st


def _since(text, now):
    """A server's start time ('2026-10-25T20:10:00') as seconds, or now where it cannot be read."""
    try:
        return time.mktime(time.strptime(str(text)[:19], "%Y-%m-%dT%H:%M:%S"))
    except (TypeError, ValueError, OverflowError):
        return now


def _runs_from(st, tool, a, r, now):
    """The run states any Pertura reply carries, whatever the tool: the runs that ended since the last call
    (ended_since_your_last_call), a run the reply started or names (run_id with its state), and pertura_status's
    running runs, the whole list, so a job it leaves out has ended. The analysis host, code folder and Python the
    server names, for the job check and the recorder where the plugin's options are not set or not usable."""
    for k in ("analysis_host", "remote_code", "remote_python", "workspace_config"):  # the recorder's reads take these
        if isinstance(r.get(k), str) and r[k].strip():
            st[k] = r[k].strip()
    if tool == "wait_for_runs":  # the wait has returned: none is armed, and its lists are the runs' states
        if not r.get("taken_over"):  # a wait that handed over to a newer one leaves that one armed
            st.pop("wait_armed_until", None)
        for j in r.get("still_running") or []:
            if isinstance(j, dict) and j.get("run_id") in st["jobs"]:
                st["jobs"][j["run_id"]].update(checked=now, progress=j.get("progress"))
    for e in (r.get("ended_since_your_last_call") or []) + (r.get("ended") or [] if tool == "wait_for_runs" else []):
        if isinstance(e, dict) and e.get("run_id"):
            state = e.get("state") if e.get("state") not in ACTIVE else "done"
            _job(st, e["run_id"], state or "done", now, what=e.get("what"))
    rid = r.get("run_id")
    if rid and r.get("state") and tool not in ("run_module", "module_result", "check_robustness",
                                                "null_of_right_unit", "build_screen_object"):
        if rid in st["jobs"] or r["state"] in ACTIVE:
            _job(st, rid, r["state"], now, what=r.get("what") or a.get("what") or tool)
    if tool == "pertura_status" and isinstance(r.get("running"), list):
        listed = set()
        for j in r["running"]:
            if isinstance(j, dict) and j.get("run_id"):
                listed.add(j["run_id"])
                if j["run_id"] not in st["jobs"]:
                    _job(st, j["run_id"], "running", now, what=j.get("what"))
                    st["jobs"][j["run_id"]]["started"] = _since(j.get("since"), now)
                else:
                    st["jobs"][j["run_id"]]["checked"] = now
                st["jobs"][j["run_id"]]["progress"] = j.get("progress")
        for job in st["jobs"].values():  # the server lists every running run: one it leaves out has ended
            if job.get("state") in ACTIVE and job.get("kind") != "screen_object" and job["run_id"] not in listed:
                job["state"], job["ended"], job["checked"] = "ended", now, now


def _prune(st, now):
    """Keep the running jobs and the finished ones of the last day, at most KEEP_FINISHED of those."""
    done = sorted((j for j in st["jobs"].values() if j.get("state") not in ACTIVE),
                  key=lambda j: j.get("ended") or 0, reverse=True)
    old = [j for j in done[:KEEP_FINISHED] if j.get("ended") is not None and now - j["ended"] > DAY]
    for j in done[KEEP_FINISHED:] + old:
        st["jobs"].pop(j["run_id"], None)


def open_page_wanted():
    """Whether the plugin's open_page option leaves the page's opening on (it is on unless set off)."""
    return str(os.environ.get(OPEN_PAGE_VARIABLE, "true")).strip().lower() not in ("false", "0", "no", "off")


def open_in_browser(path):
    """The page in the user's browser; it returns at once."""
    import pathlib
    import webbrowser
    webbrowser.open(pathlib.Path(path).resolve().as_uri())


def record_call(hook_input, root=None, now=None, open_page=None):
    """The after-tool-call hook's work: fold the call into its project's file and point the session at it.
    open_page: called with the page's path the first time a session's call names a project, to show it to the user
    (the hook gives open_in_browser). Returns the project's status, or None when the call is not Pertura's or no
    project is known yet."""
    tool = short_tool(hook_input.get("tool_name", ""))
    if tool is None:
        return None
    tool, hook_input = as_module_call(tool, hook_input)
    root = root or home()
    now = time.time() if now is None else now
    sp = session_path(hook_input.get("session_id") or "unknown", root)
    with Lock(sp):
        session = read_json(sp, {}) or {}
        key = session.get("project")
        r = response_dict(hook_input.get("tool_response"))
        project = (r or {}).get("project") if tool in ("open_project", "pertura_status") else None
        if project:
            key = project_key(project)
        if key is None:
            return None
        pp = project_path(key, root)
        with Lock(pp):
            st = read_json(pp) or empty()
            seconds = float(hook_input.get("duration_ms") or 0) / 1000.0
            held = st.get("wait_armed_until") if hook_input.get("agent_id") else None
            apply_call(st, tool, hook_input.get("tool_input"), hook_input.get("tool_response"), now, seconds)
            if held:  # a subagent's wait returning leaves the main agent's wait armed
                st["wait_armed_until"] = held
            write_page(st, key, root, tool, hook_input, now)
            save(key, st, root)
        opened = session.get("page_opened")
        if open_page is not None and opened != key and st.get("page_path"):
            try:
                open_page(st["page_path"])
            except Exception:  # a browser that cannot be opened leaves the page on disk, never the call
                pass
            opened = key
        write_json(sp, {"project": key, "updated": now, "cwd": hook_input.get("cwd"), "page_opened": opened})
        write_text(session_path(hook_input.get("session_id") or "unknown", root, ".key"), key)
    return st


def page_folder(key, root=None):
    """The folder of a project's page and its figures, beside its status file."""
    return os.path.join(root or home(), "projects", key)


def write_page(st, key, root, tool, hook_input, now):
    """The user's page (page.py): the figures this call returned to the agent saved, the call folded into the page's
    state, and the page written again. A call that failed adds nothing."""
    folder = page_folder(key, root)
    r = response_dict(hook_input.get("tool_response"))
    if failed(r):
        return
    images = user_page.response_images(hook_input.get("tool_response"))
    names = user_page.save_images(folder, user_page.stamp(now), images) if images else []
    st["page"] = user_page.fold(st.get("page"), tool, hook_input.get("tool_input"), r, names, now)
    st["page"]["running"] = [{"what": j.get("what"), "started": j.get("started")} for j in running_jobs(st)]
    # the page as a window of the session that called last (a fold of the server's page state names none)
    st["ask_session"] = hook_input.get("session_id") or st.get("ask_session")
    st["page_path"] = user_page.write(folder, st["page"], f"Pertura · {st.get('name') or 'the screen'}",
                                      st.get("record"), st.get("methods"),
                                      window.helper_for(st["ask_session"], root), st.get("due"))


def fold_page_state(key, state, root=None, now=None):
    """The server's page state for the project (what its page shows that no reply carries: the digest, the threads,
    the coverage, the screen overview's rows) folded into its status and page; nothing when it is the one folded
    last, or another project's. Returns the status, or None."""
    root = root or home()
    if not isinstance(state, dict):
        return None
    now = time.time() if now is None else now
    pp = project_path(key, root)
    with Lock(pp):
        st = read_json(pp)
        if not st or (state.get("project") and st.get("project") and state["project"] != st["project"]):
            return None
        if state.get("seq") is not None and state.get("seq") == st.get("page_state_seq"):
            return st
        if isinstance(state.get("digest"), dict):
            st["digest"] = {**state["digest"], "at": now}
            st["digest_line"] = state["digest"].get("line")
        if isinstance(state.get("coverage"), dict):
            st["coverage"] = state["coverage"]
            st["coverage_line"] = str(state.get("coverage_line") or "")
        if isinstance(state.get("record"), dict):  # the record as the workbench reads it (pertura.page_view)
            st["record"] = state["record"]
        if isinstance(state.get("methods"), list):  # each module run's method, for the page's methods
            st["methods"] = state["methods"]
        st["page_state_seq"] = state.get("seq")
        write_page(st, key, root, "page_state", {"tool_name": TOOL_PREFIX + "page_state", "tool_input": {},
                                                  "tool_response": state}, now)
        save(key, st, root)
        return st


def session_status(session_id, root=None):
    """The status of the session's project, or None."""
    root = root or home()
    session = read_json(session_path(session_id, root)) or {}
    return read_json(project_path(session["project"], root)) if session.get("project") else None


def arm_wait(hook_input, root=None, now=None):
    """Before a wait_for_runs call: its session's project holds a wait until the call returns or its minutes pass
    (a call that never returns does not hold one for ever). A subagent's wait is its own: the Stop hook asks the main
    agent for one."""
    if short_tool(hook_input.get("tool_name", "")) != "wait_for_runs" or hook_input.get("agent_id"):
        return None
    root = root or home()
    now = time.time() if now is None else now
    session = read_json(session_path(hook_input.get("session_id") or "unknown", root)) or {}
    if not session.get("project"):
        return None
    a = hook_input.get("tool_input") if isinstance(hook_input.get("tool_input"), dict) else {}
    try:
        minutes = min(60.0, max(0.0, float(a.get("minutes", 30))))
    except (TypeError, ValueError):
        minutes = 30.0
    pp = project_path(session["project"], root)
    with Lock(pp):
        st = read_json(pp) or empty()
        st["wait_armed_until"] = now + minutes * 60 + 120
        save(session["project"], st, root)
    return st


def wait_armed(st, now=None):
    return float((st or {}).get("wait_armed_until") or 0) > (time.time() if now is None else now)


def running_jobs(st):
    return [j for j in (st or {}).get("jobs", {}).values() if j.get("state") in ACTIVE]
