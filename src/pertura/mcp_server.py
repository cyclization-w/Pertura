"""Minimal MCP server: JSON-RPC 2.0 over stdio, one message per line. Standard library only (no MCP SDK on the
analysis server). It serves one of three services; each brings its own tools, instructions and server name.
- The blinded error-control service (the first evaluations; anonymised groups; research era, moved to
  pertura_research.blinded_service on 2026-10-09 and imported only for these commands): get_design, submit_table,
  get_results, freeze_plan. Unblinding is not a tool; the orchestrator runs it from the command line after the plan is
  frozen.
    python -m pertura.mcp_server make-config <run> [--budget 10] [--state-dir DIR] > config.json
    python -m pertura.mcp_server serve <config.json>
    python -m pertura.mcp_server unblind <config.json> [--key keys/<run>/key.json]
- The screen service (the product; real target names, hidden checks; pertura_research.retired.screen_service): describe_screen,
  list_analyses, run_analysis, get_results, get_hits.
    python -m pertura.mcp_server make-screen-config <screen> [--checks NAME] [--state-dir DIR] > config.json
    python -m pertura.mcp_server serve-screen <config.json>
- The design server (the design auditor's tools; pertura.design_service): describe_deposit, measure, check_design,
  confirm_design, get_design.
    python -m pertura.mcp_server make-design-config <deposit> [--geo SOFT] [--state-dir DIR] > config.json
    python -m pertura.mcp_server serve-design <config.json>
  confirm_design asks the user through the client's form (MCP elicitation, protocol 2025-06-18): while a tool call
  runs, the server sends an elicitation/create request and waits for the client's answer. Only clients that declare
  the elicitation capability are asked.
- The analysis server (docs/adr/0020; pertura.analysis_service): the design, the screen object, the plan and the
  shared record on the full data. The plugin starts this one, on a configuration written once:
    python -m pertura.mcp_server make-workspace-config <root> [--cores 60-71] [--thread-limit 12] > workspace.json
    python -m pertura.mcp_server serve-analysis <workspace.json>
  (the configuration: {"workspace": <folder for projects>, "resources": {"cores": ..., "thread_limit": ...}};
  pertura.resources)
- Pertura's earlier server for a user's session (projects, the design, the analysis and the review;
  pertura_research.retired.pertura_service), on the same configuration with --registry FILE:
    python -m pertura.mcp_server serve-pertura <workspace.json>
stdout carries protocol messages only; tracebacks go to stderr.
"""
import argparse
import collections
import itertools
import json
import math
import os
import sys
import threading
import traceback

PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")


def _jsonable(o):
    return o.item() if hasattr(o, "item") else str(o)


def _strict(o):
    """o with only what JSON can hold: a missing or infinite number as null (JSON has no NaN, and a client drops a
    line that carries one), numpy scalars and arrays as Python values, anything else as its text."""
    if isinstance(o, float):
        return o if math.isfinite(o) else None
    if o is None or isinstance(o, (str, int, bool)):
        return o
    if isinstance(o, dict):
        return {(k.item() if hasattr(k, "item") else k): _strict(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_strict(v) for v in o]
    if hasattr(o, "tolist"):  # numpy arrays and scalars, pandas series
        return _strict(o.tolist())
    return _strict(_jsonable(o))


def dumps(o, **kw):
    """Strict JSON text of o (no NaN or Infinity)."""
    return json.dumps(_strict(o), allow_nan=False, **kw)


SERVER_INFO = {"name": "pertura-error-control", "version": "0.1.0"}
INSTRUCTIONS = ("Decoy-based error control for a blinded perturbation screen. Analyse the data in your workspace "
                "as you see fit, register each specification's full result table with submit_table, then call "
                "get_results. Some groups are hidden decoys scored like any other group. get_results has a budget; "
                "freeze_plan ends submissions and returns the final results.")


def _obj(props, required=()):
    return {"type": "object", "properties": props, "required": list(required), "additionalProperties": False}


_SPECS = {"type": "array", "items": {"type": "string"}, "description": "spec_ids to combine (default: all submitted)"}
_MAXC = {"type": "integer", "minimum": 0, "maximum": 1000,
         "description": "number of top supported claims listed per group (default 10)"}
_COLS = {"type": "object", "description": "column names, if not perturbation_id / gene / logFC / p / construct",
         "properties": {k: {"type": "string"} for k in ("group", "feature", "effect", "p", "construct")},
         "additionalProperties": False}
TOOLS = [
    {"name": "get_design", "inputSchema": _obj({}),
     "description": "Groups to test, table format, rules, status and the remaining query budget."},
    {"name": "submit_table",
     "description": "Register one specification's full result table (TSV, or CSV by extension, inside the "
                    "workspace): one row per group and feature with effect and p-value. Optional column 'construct': "
                    "'all' for the pooled test, or the vector ID for a test of that vector's cells only; per-vector "
                    "rows let the server check that claims replicate across a group's independent vectors.",
     "inputSchema": _obj({"spec_id": {"type": "string", "description": "short name of the specification"},
                          "path": {"type": "string", "description": "table path relative to the workspace"},
                          "columns": _COLS}, ("spec_id", "path"))},
    {"name": "get_results", "inputSchema": _obj({"specs": _SPECS, "max_claims_per_group": _MAXC}),
     "description": "Decoy-controlled evidence levels for every group from the submitted tables. Uses one query "
                    "of the budget."},
    {"name": "freeze_plan",
     "description": "Commit the analysis plan (tables, rules, what will be reported). Stops submissions and "
                    "returns the final results.",
     "inputSchema": _obj({"plan": {"type": "string"}, "specs": _SPECS, "max_claims_per_group": _MAXC}, ("plan",))},
]
TOOL_NAMES = {t["name"] for t in TOOLS}


def _error(id_, code, message):
    return {"jsonrpc": "2.0", "id": id_, "error": {"code": code, "message": message}}


def _result(id_, result):
    return {"jsonrpc": "2.0", "id": id_, "result": result}


def _as_declared(tool, args):
    """The arguments with an object or a list sent as JSON text read as one, where the tool declares only that type."""
    props = (tool.get("inputSchema") or {}).get("properties") or {}
    out = dict(args)
    for k, v in args.items():
        if isinstance(v, str) and (props.get(k) or {}).get("type") in ("object", "array"):
            try:
                parsed = json.loads(v)
            except ValueError:
                continue
            if isinstance(parsed, dict if props[k]["type"] == "object" else list):
                out[k] = parsed
    return out


def handle(service, msg, session):
    """One parsed JSON-RPC message -> response dict, or None for notifications and stray responses."""
    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
        return _error(msg.get("id") if isinstance(msg, dict) else None, -32600, "Invalid Request")
    method, id_ = msg.get("method"), msg.get("id")
    if method is None and ("result" in msg or "error" in msg):
        return None
    if not isinstance(method, str):
        return _error(id_, -32600, "Invalid Request")
    if "id" not in msg:
        if method == "notifications/initialized":
            session["initialized"] = True
        return None
    params = msg.get("params", {})
    if not isinstance(params, dict):
        return _error(id_, -32602, "params must be an object")
    tools = getattr(service, "TOOLS", TOOLS)
    if method == "initialize":
        asked = params.get("protocolVersion")
        session["protocol"] = asked if asked in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0]
        session["client_capabilities"] = params.get("capabilities") or {}
        return _result(id_, {"protocolVersion": session["protocol"], "capabilities": {"tools": {"listChanged": False}},
                             "serverInfo": getattr(service, "SERVER_INFO", SERVER_INFO),
                             "instructions": getattr(service, "INSTRUCTIONS", INSTRUCTIONS)})
    if method == "ping":
        return _result(id_, {})
    if method == "tools/list":  # loaded upfront, so the agent sees each tool's arguments before its first call
        return _result(id_, {"tools": [{**t, "_meta": {**t.get("_meta", {}), "anthropic/alwaysLoad": True}}
                                       for t in tools]})
    if method == "tools/call":
        name, args = params.get("name"), params.get("arguments", {})
        if name not in {t["name"] for t in tools}:
            instead = (getattr(service, "RETIRED", None) or {}).get(name)
            return _error(id_, -32602, f"Unknown tool: {name}" + (f"; its work is done by {instead}" if instead else ""))
        if not isinstance(args, dict):
            return _error(id_, -32602, "arguments must be an object")
        args = _as_declared(next(t for t in tools if t["name"] == name), args)
        kw = {}
        if getattr(service, "USES_ELICITATION", False):  # the user is asked only by clients that can show a form
            can = "elicitation" in session.get("client_capabilities", {})
            kw["elicit"] = session.get("elicit") if can else None
            session["current_request"] = id_
        if getattr(service, "USES_CANCEL", False):  # an event set when the client cancels this call
            kw["cancel"] = session.get("cancel")
        ok, out = service.call(name, args, **kw)
        images = out.pop("_images", None) if isinstance(out, dict) else None  # figures the reply shows the model
        if images:  # what each image is, in their order: the model reads it, and the plugin's hook sorts by it
            out["images"] = [str(im.get("about") or "") for im in images]
        text = dumps(out)
        content = [{"type": "text", "text": text}]
        content += [{"type": "image", "data": im["data"], "mimeType": im.get("mimeType", "image/png")}
                    for im in images or []]
        return _result(id_, {"content": content, "structuredContent": json.loads(text), "isError": not ok})
    return _error(id_, -32601, f"Method not found: {method}")


def _safe(service, msg, session):
    try:
        return handle(service, msg, session)
    except Exception:
        traceback.print_exc(file=sys.stderr)
        return _error(msg.get("id") if isinstance(msg, dict) else None, -32603, "Internal error")


def _elicitor(stdin, stdout, session, pending):
    """elicit(message, schema) -> (action, content): send elicitation/create to the client and wait for its answer.
    Pings are answered while waiting; other messages wait in `pending` until the tool call returns. A cancellation of
    the running tool call, or the end of input, ends the wait as 'cancel'."""
    ids = itertools.count(1)

    def write(msg):
        stdout.write(dumps(msg) + "\n")
        stdout.flush()

    def elicit(message, schema):
        rid = f"pertura-elicit-{next(ids)}"
        write({"jsonrpc": "2.0", "id": rid, "method": "elicitation/create",
               "params": {"message": message, "requestedSchema": schema}})
        while True:
            line = stdin.readline()
            if not line:
                return "cancel", None
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(msg, dict) and msg.get("id") == rid and "method" not in msg:
                if "error" in msg:
                    return "error", msg["error"]
                result = msg.get("result") or {}
                return result.get("action", "cancel"), result.get("content")
            if isinstance(msg, dict) and msg.get("method") == "ping" and "id" in msg:
                write(_result(msg["id"], {}))
                continue
            if isinstance(msg, dict) and msg.get("method") == "notifications/cancelled" and \
                    (msg.get("params") or {}).get("requestId") == session.get("current_request"):
                return "cancel", None
            pending.append(line)
    return elicit


POOL_SIZE = 7  # ADR 0016 §6: 4 workers, the organizer, the reviewer and one spare
# waits (a service's WAIT_TOOLS) stay out of the pool; one beyond WAIT_POOL at once is told to retry. ADR 0016 §6 said
# 2; 5 lets the four workers and the organizer wait at once, so none polls in a paid loop (the review, 2026-10-12)
WAIT_POOL = 5


class _ConcurrentServer:
    """The server for a service that declares CONCURRENT (ADR 0016 §6; the first unknown, measured 2026-10-11:
    parallel subagents' calls reach the server at the same time, one agent's own calls in turn).
    - **One reader** owns stdin. Requests that answer at once (initialize, ping, tools/list) are answered on it;
      tool calls go to a pool of POOL_SIZE threads; a service's WAIT_TOOLS go to a pool of WAIT_POOL, and a wait
      beyond it is told to retry.
    - **One lock** guards every stdout write.
    - **Elicitation** (a form for the user) is per call: its request carries its own ID, and the reader routes the
      client's answer to the call that asked. A cancellation of that call, or the end of input, ends the wait as
      'cancel'.
    - **At the end of input** open forms end as 'cancel', a form asked for later is cancelled at once, the service's
      waits end (stop_waiting), and the calls in flight finish and are answered before the service closes.
    - **A cancelled call** ends its form, and its wait when the service takes a cancel event (USES_CANCEL).
    The service's methods must be safe to call from several threads; one agent's calls still arrive in its order."""

    def __init__(self, service, stdin, stdout):
        from concurrent.futures import ThreadPoolExecutor
        self.service, self.stdin, self.stdout = service, stdin, stdout
        self.out_lock, self.lock = threading.Lock(), threading.Lock()
        self.session = {}
        self.elicits = {}  # elicitation request id -> {"event", "answer", "request"}
        self.ids = itertools.count(1)
        self.pool = ThreadPoolExecutor(POOL_SIZE, thread_name_prefix="pertura-call")
        self.waits = ThreadPoolExecutor(WAIT_POOL, thread_name_prefix="pertura-wait")
        self.waiting = 0
        self.wait_tools = set(getattr(service, "WAIT_TOOLS", ()))
        self.closed = False  # the input has ended: no new form waits
        self.cancels = {}  # call id -> the event its cancellation sets

    def write(self, msg):
        with self.out_lock:
            self.stdout.write(dumps(msg) + "\n")
            self.stdout.flush()

    def elicitor(self, request_id):
        def elicit(message, schema):
            rid = f"pertura-elicit-{next(self.ids)}"
            entry = {"event": threading.Event(), "answer": ("cancel", None), "request": request_id}
            with self.lock:
                if self.closed:  # asked for after the end of input: nobody can answer it (the review, finding 4)
                    return "cancel", None
                self.elicits[rid] = entry
            self.write({"jsonrpc": "2.0", "id": rid, "method": "elicitation/create",
                        "params": {"message": message, "requestedSchema": schema}})
            entry["event"].wait()
            return entry["answer"]
        return elicit

    def _answer(self, rid, answer):
        with self.lock:
            entry = self.elicits.pop(rid, None)
        if entry is not None:
            entry["answer"] = answer
            entry["event"].set()

    def _cancel(self, request_id=None):
        with self.lock:
            rids = [r for r, e in self.elicits.items() if request_id is None or e["request"] == request_id]
            events = [ev for i, ev in self.cancels.items() if request_id is None or i == request_id]
        for ev in events:
            ev.set()
        for r in rids:
            self._answer(r, ("cancel", None))

    def _call(self, msg, wait):
        ev = threading.Event()
        with self.lock:
            if self.closed:
                ev.set()
            self.cancels[msg.get("id")] = ev
        try:
            session = {**self.session, "current_request": msg.get("id"), "cancel": ev}
            session["elicit"] = self.elicitor(msg.get("id"))
            out = _safe(self.service, msg, session)
            if out is not None:
                self.write(out)
        finally:
            with self.lock:
                self.cancels.pop(msg.get("id"), None)
                if wait:
                    self.waiting -= 1

    def _message(self, msg):
        """One message from the reader: a response routed, a notification noted, a request answered or queued."""
        if not isinstance(msg, dict):
            out = _safe(self.service, msg, self.session)
            if out is not None:
                self.write(out)
            return
        if msg.get("method") is None and ("result" in msg or "error" in msg):  # an answer to our elicitation
            if "error" in msg:
                self._answer(msg.get("id"), ("error", msg["error"]))
            else:
                r = msg.get("result") or {}
                self._answer(msg.get("id"), (r.get("action", "cancel"), r.get("content")))
            return
        if msg.get("method") == "notifications/cancelled":
            self._cancel((msg.get("params") or {}).get("requestId"))
            return
        if msg.get("method") != "tools/call" or "id" not in msg:
            out = _safe(self.service, msg, self.session)  # initialize, ping, tools/list, notifications
            if out is not None:
                self.write(out)
            return
        name = (msg.get("params") or {}).get("name") if isinstance(msg.get("params"), dict) else None
        if name in self.wait_tools:
            with self.lock:
                full = self.waiting >= WAIT_POOL
                if not full:
                    self.waiting += 1
            if full:
                out = {"error": "retry", "detail": f"{WAIT_POOL} waits are already running: wait again in a moment"}
                actions = getattr(self.service, "current_user_actions", None)
                if callable(actions):
                    try:
                        out["user_actions"] = actions()
                    except Exception:  # noqa: BLE001 - the reply goes out without them
                        traceback.print_exc(file=sys.stderr)
                text = dumps(out)
                self.write(_result(msg["id"], {"content": [{"type": "text", "text": text}],
                                               "structuredContent": json.loads(text), "isError": True}))
                return
            self.waits.submit(self._call, msg, True)
        else:
            self.pool.submit(self._call, msg, False)

    def run(self):
        try:
            while True:
                line = self.stdin.readline()
                if not line:
                    break
                if not line.strip():
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    self.write(_error(None, -32700, "Parse error"))
                    continue
                for m in (msg if isinstance(msg, list) and msg else [msg]):
                    self._message(m)
        finally:
            with self.lock:
                self.closed = True
            self._cancel()  # the end of input ends every open form as 'cancel', and every cancellable wait
            stop = getattr(self.service, "stop_waiting", None)
            if callable(stop):
                stop()
            self.pool.shutdown(wait=True)  # calls in flight finish and are answered
            self.waits.shutdown(wait=True)
            close = getattr(self.service, "close", None)
            if callable(close):
                close()


def serve(service, stdin=None, stdout=None):
    """Read newline-delimited JSON-RPC messages until EOF; write one response line per request. A service that
    declares CONCURRENT is served by _ConcurrentServer (ADR 0016 §6); any other is served one message at a time, in
    order, as before."""
    stdin, stdout = stdin or sys.stdin, stdout or sys.stdout
    if getattr(service, "CONCURRENT", False):
        _ConcurrentServer(service, stdin, stdout).run()
        return
    session, pending = {}, collections.deque()
    session["elicit"] = _elicitor(stdin, stdout, session, pending)
    try:
        _loop(service, stdin, stdout, session, pending)
    finally:  # the session's notebook kernel and background steps stop with the server
        close = getattr(service, "close", None)
        if callable(close):
            close()


def _loop(service, stdin, stdout, session, pending):
    while True:
        line = pending.popleft() if pending else stdin.readline()
        if not line:
            break
        if not line.strip():
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            out = _error(None, -32700, "Parse error")
        else:
            if isinstance(msg, list) and msg:  # JSON-RPC batch (allowed by protocol versions before 2025-06-18)
                out = [r for r in (_safe(service, m, session) for m in msg) if r is not None] or None
            else:
                out = _safe(service, msg, session)
        if out is not None:
            stdout.write(dumps(out) + "\n")
            stdout.flush()


def serve_loop(config_path):
    """The trying loop's server (pertura_research.loop_service, moved out of the product on 2026-10-12, ADR 0017 §8;
    imported only for this command). The thread variables and the nice level are set before numpy
    and R start (pertura.limits). At the end, after its jobs stopped taking attempts and tool runs, it kills its own
    descendants (the R runs its daemon threads started) until its runner threads end, then closes the attempts it
    still has running as interrupted (the reviews of 2026-10-12: no processes left behind, none taken for the
    machine's failure)."""
    import signal
    from . import limits
    with open(config_path) as fh:
        cfg = json.load(fh)
    limits.limit_this_process(int(cfg.get("runs", 2)))
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))  # ended by its client: through serve's cleanup
    from pertura_research.loop_service import LoopService
    service = LoopService(cfg)
    try:
        serve(service)
    finally:  # no second signal cuts the cleanup short; the service stops taking attempts before anything is killed
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        service.close()  # idempotent: serve has usually closed it already
        for _ in range(24):  # a thread between two R calls starts no new one (the stop), so this ends
            _end_children()
            if service.join_runs(5):
                break
        _end_children()
        service.interrupt_own_attempts()


def serve_agent(config_path):
    """The lean agent's server (pertura_research.retired.agent_service, ADR 0017): niced, its thread variables set before numpy starts
    (pertura.limits), served concurrently so that workers' calls run in parallel; at the end its notebooks' kernels
    stop with it (AgentService.close) and its own descendants are ended (no processes left behind)."""
    import signal
    from . import limits
    from pertura_research.retired.agent_service import KERNEL_THREADS, AgentService
    with open(config_path) as fh:
        cfg = json.load(fh)
    limits.limit_this_process(1, KERNEL_THREADS)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))  # ended by its client: through serve's cleanup
    service = AgentService(cfg)
    try:
        serve(service)
    finally:
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        service.close()
        _end_children()


def serve_analysis(config_path):
    """The analysis server (pertura.analysis_service): niced and pinned (pertura.limits), served concurrently; its
    notebooks' kernels stop with it, and its own descendants are ended, except its module jobs. A client without a
    window starts a server for each turn, so a job runs to its end and the next server reads it."""
    import signal
    from . import limits, resources
    with open(config_path) as fh:
        cfg = json.load(fh)
    cfg.setdefault("config_path", os.path.abspath(config_path))  # so the resources are read again when edited
    resources.at_server_start(cfg)  # the core set, the thread limit and the environment, before pinning and numpy
    from .analysis_service import KERNEL_THREADS, AnalysisService
    limits.limit_this_process(1, KERNEL_THREADS)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    service = AnalysisService(cfg)
    service.survey_in_background = True  # the session's service steps the survey with no call (docs/adr/0045)
    service.checks_in_background = True  # and starts the queued checks (the loop decided by the record)
    try:
        serve(service)
    finally:
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        service.close()
        _end_children(spare_jobs=True)


def _end_children(spare_jobs=False):
    """Kill this process's descendants (Linux /proc; elsewhere nothing): never a process outside its own tree.
    spare_jobs: leave the processes of the runner's jobs (each carries the job's tag) to run to their end."""
    import signal
    if not os.path.isdir("/proc"):
        return
    from .runner import TAG
    own = os.environ.get(TAG)  # a server started inside a job: its own descendants carry that job's tag
    for pid in descendants(os.getpid(), _proc_parents()):
        if spare_jobs and _job_of(pid) not in (None, own):
            continue
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass


def _job_of(pid):
    """The runner's job a process belongs to (the tag in its environment), or None."""
    from .runner import TAG
    try:
        with open(f"/proc/{pid}/environ", "rb") as fh:
            env = fh.read().split(b"\0")
    except OSError:
        return None
    key = TAG.encode() + b"="
    return next((v[len(key):].decode(errors="replace") for v in env if v.startswith(key)), None)


def descendants(pid, parents):
    """The descendants of pid, given {process: its parent}."""
    children = collections.defaultdict(list)
    for p, pp in parents.items():
        children[pp].append(p)
    out, todo = [], list(children.get(pid, ()))
    while todo:
        p = todo.pop()
        if p in out or p == pid:
            continue
        out.append(p)
        todo += children.get(p, ())
    return out


def _proc_parents():
    """{process: its parent} from /proc (Linux)."""
    out = {}
    for d in os.listdir("/proc"):
        if d.isdigit():
            try:
                with open(f"/proc/{d}/stat") as fh:
                    out[int(d)] = int(fh.read().rsplit(")", 1)[1].split()[1])  # after the name: state, ppid, ...
            except (OSError, IndexError, ValueError):
                continue
    return out


def call_analysis(config_path, tool, args_text="{}", b64=False, answer=None):
    """One tool call to the analysis server, made as an MCP client would make it, for an agent that does not have
    the plugin's server connected (a subagent of another session). The config: {"workspace", "data_folder",
    "operator_design" (optional), "switches" (optional)}; the project is opened on the data folder at each call,
    and its record, jobs and design persist on disk between calls. answer (base64 JSON): the user's own answer to
    the tool's form, given in the conversation and relayed as the form's reply; without it a form is refused.
    Prints the tool's answer as JSON."""
    import base64
    from .analysis_service import AnalysisService
    with open(config_path, encoding="utf-8") as fh:
        cfg = json.load(fh)
    svc = AnalysisService(cfg)
    try:
        ok, out = svc.call("open_project", {"data_folder": cfg["data_folder"]})
        if ok and cfg.get("operator_design"):
            svc.design.cfg["operator_design"] = cfg["operator_design"]
        if ok and tool != "open_project" and tool not in {t["name"] for t in svc.TOOLS}:
            from .analysis_service import RETIRED
            ok, out = False, {"error": f"unknown tool {tool}" + (f"; its work is done by {RETIRED[tool]}"
                                                                if tool in RETIRED else "")}
        elif ok and tool != "open_project":
            text = base64.b64decode(args_text).decode() if b64 else args_text
            elicit = None
            if answer:
                reply = json.loads(base64.b64decode(answer).decode())
                elicit = lambda message, schema: ("accept", reply)  # noqa: E731
            ok, out = svc.call(tool, json.loads(text or "{}"), elicit=elicit)
        print(json.dumps({"ok": ok, "answer": out}, indent=1, default=str))
    finally:
        svc.close()
    return 0 if ok else 1


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m pertura.mcp_server")
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("make-config")
    m.add_argument("run")
    m.add_argument("--base", default=os.path.expanduser(os.environ.get("PERTURA_BASE", "~/pertura")))
    m.add_argument("--budget", type=int, default=10)
    m.add_argument("--state-dir")
    m.add_argument("--workspace", help="the agent's workspace, if not agent_ws/<run>")
    sub.add_parser("serve").add_argument("config")
    u = sub.add_parser("unblind")
    u.add_argument("config")
    u.add_argument("--key", help="the full scorer key with holdout decoys (default: the server key in the config)")
    s = sub.add_parser("make-screen-config", help="config for the screen service (real target names, hidden checks)")
    s.add_argument("screen")
    s.add_argument("--base", default=os.path.expanduser(os.environ.get("PERTURA_BASE", "~/pertura")))
    s.add_argument("--checks", help="folder name of the screen's hidden checks (default <screen>_u1)")
    s.add_argument("--state-dir")
    s.add_argument("--rule", default="bh")
    s.add_argument("--design", help="the confirmed design file (pertura.design/v1) the design gate reads")
    s.add_argument("--condition", help="the condition the screen holds, when the design has several")
    sub.add_parser("serve-screen").add_argument("config")
    d = sub.add_parser("make-design-config", help="config for the design server (the design auditor's tools)")
    d.add_argument("deposit")
    d.add_argument("--geo", help="GEO family SOFT file, if not in the deposit folder")
    d.add_argument("--state-dir", required=True)
    d.add_argument("--name")
    sub.add_parser("serve-design").add_argument("config")
    w = sub.add_parser("make-workspace-config", help="the analysis server's configuration: the workspace, and the "
                                                     "core set and thread limit its jobs are pinned to")
    w.add_argument("root")
    w.add_argument("--cores", help="the CPUs our jobs share, such as 60-71 (default: the highest thread-limit CPUs "
                                   "this process may use)")
    w.add_argument("--thread-limit", type=int, help="threads for our jobs in all (default: PERTURA_THREAD_LIMIT, "
                                                    "else 12)")
    w.add_argument("--registry", help="for the earlier servers: screens already built for known data folders")
    sub.add_parser("serve-pertura").add_argument("config")
    lp = sub.add_parser("make-loop-config", help="config for the trying loop's server on one exploration view")
    lp.add_argument("view", help="the exploration view's manifest.json")
    lp.add_argument("--state-dir", required=True)
    lp.add_argument("--mode", default="product", choices=["product", "research"])
    lp.add_argument("--runs", type=int, default=2)
    lp.add_argument("--no-practice-spikes", action="store_true")
    sub.add_parser("serve-loop").add_argument("config")
    sub.add_parser("serve-agent", help="the lean agent's server (pertura_research.retired.agent_service) on a workspace config "
                                       "(make-workspace-config)").add_argument("config")
    sub.add_parser("serve-analysis", help="the analysis server (pertura.analysis_service)").add_argument("config")
    ai = sub.add_parser("apply-inbox", help="apply the recorder's inbox to the workspace's last project (docs/adr/"
                                            "0051): at a run's end, where no server runs, or at a turn's Stop")
    ai.add_argument("config")
    ai.add_argument("--project", help="the project the inbox is for: refused unless it is the workspace's last")
    ai.add_argument("--queue-only", action="store_true",
                    help="queue the claims' checks and start none: the session's server, still running, starts them")
    ca = sub.add_parser("call-analysis", help="one tool call to the analysis server, for an agent without the plugin")
    ca.add_argument("config")
    ca.add_argument("tool")
    ca.add_argument("args", nargs="?", default="{}")
    ca.add_argument("--b64", action="store_true", help="the arguments are base64-encoded JSON")
    ca.add_argument("--answer", help="the user's own answer to the tool's form, base64-encoded JSON")
    a = ap.parse_args(argv)
    if a.cmd == "call-analysis":
        return call_analysis(a.config, a.tool, a.args, a.b64, a.answer)
    if a.cmd == "serve-analysis":
        serve_analysis(a.config)
        return
    if a.cmd == "apply-inbox":
        from . import limits, resources
        with open(a.config, encoding="utf-8") as fh:
            cfg = json.load(fh)
        resources.at_server_start(cfg)  # as serve-analysis does: its checks get the config's environment and cores
        from .analysis_service import KERNEL_THREADS, AnalysisService
        limits.limit_this_process(1, KERNEL_THREADS)  # and it is niced and pinned to those cores, as the server is
        svc = AnalysisService(cfg)
        svc.queue_only = a.queue_only  # one process starts checks: the session's server, while it runs
        svc._reopen_last()
        if not svc.project:
            out = {"error": "no project to apply the inbox to"}
        elif a.project and os.path.abspath(a.project) != os.path.abspath(svc.project):
            out = {"error": f"{a.project} is not this workspace's last project, {svc.project}"}
        else:
            out = svc._apply_inbox()
        print(json.dumps(out, default=str))
        return
    if a.cmd == "serve-agent":
        serve_agent(a.config)
        return
    if a.cmd == "make-loop-config":
        from pertura_research.loop_service import make_loop_config
        print(json.dumps(make_loop_config(a.view, a.state_dir, a.mode, a.runs, not a.no_practice_spikes), indent=1))
        return
    if a.cmd == "serve-loop":
        serve_loop(a.config)
        return
    if a.cmd == "make-workspace-config":
        from .resources import workspace_config
        print(json.dumps(workspace_config(a.root, a.registry, a.cores, a.thread_limit), indent=1))
        return
    if a.cmd == "serve-pertura":
        from pertura_research.retired.pertura_service import PerturaService
        import signal
        # ended by its client: exit through serve's cleanup (the session's kernel and background steps stop)
        signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
        with open(a.config) as fh:
            serve(PerturaService(json.load(fh)))
        return
    if a.cmd == "make-config":
        from pertura_research.blinded_service import make_config
        print(json.dumps(make_config(a.run, a.base, a.budget, a.state_dir, a.workspace), indent=1))
        return
    if a.cmd in ("make-screen-config", "serve-screen"):
        from pertura_research.retired.screen_service import ScreenService, make_screen_config
        if a.cmd == "make-screen-config":
            print(json.dumps(make_screen_config(a.screen, a.base, a.checks, a.state_dir, a.rule, a.design,
                                                a.condition), indent=1))
        else:
            with open(a.config) as fh:
                serve(ScreenService(json.load(fh)))
        return
    if a.cmd in ("make-design-config", "serve-design"):
        from .design_service import DesignService, make_design_config
        if a.cmd == "make-design-config":
            print(json.dumps(make_design_config(a.deposit, a.state_dir, a.geo, a.name), indent=1))
        else:
            with open(a.config) as fh:
                serve(DesignService(json.load(fh)))
        return
    from pertura_research.blinded_service import BlindedService
    with open(a.config) as fh:
        service = BlindedService(json.load(fh))
    if a.cmd == "serve":
        serve(service)
    else:
        print(dumps(service.unblind(a.key), indent=1))


if __name__ == "__main__":
    main()
