"""Pertura's runner: it starts, watches and stops our jobs on an analysis machine (docs/adr/0020).

- start() returns a job ID at once. status(), wait() and stop() are separate calls, so stopping or interrupting a
  wait never touches the job, and a job never outlives a call by accident.
- Each job runs under a launcher that leads its own session, is niced, and is pinned to the one set of cores all our
  jobs share (limits.core_set). A library that starts more threads than allowed still runs on those cores only.
- Every process of a job carries PERTURA_JOB_ID in its environment. stop() finds a job's processes by that tag, by
  session and by descent from the launcher. It never signals another user's process, a protected PID, or itself and
  its ancestors (a pattern-matching kill can match the shell that runs it).

Site settings live in <root>/site.json, not in the code: {"protected_pids": [...], "cores": "60-71"}. Protected PIDs
are also read from limits.PROTECTED_VARIABLE.

A job's folder, <root>/<job_id>/, holds: spec.json; launch.json (the launcher's PID, cores, niceness); launcher.log;
job.log; exit_code.json (time_limit true for a job stopped at its time limit); and the markers `done` and
`stopped`.

Command line (on the server, or through Remote over ssh from the laptop):
    python -m pertura.runner start --root R --name NAME [--cwd DIR] [--threads N] -- CMD ...
    python -m pertura.runner status|wait|stop --root R JOB_ID [--timeout S]
    python -m pertura.runner list --root R
"""
import argparse
import json
import os
import shlex
import signal
import subprocess
import sys
import time

from . import limits

TAG = "PERTURA_JOB_ID"
ROOT_VARIABLE = "PERTURA_RUNNER_ROOT"  # no default, so jobs write only where the caller chose
SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ACTIVE = ("starting", "running", "orphaned")
JOB_LIMIT_SECONDS = 4 * 3600  # a job ends by this time, whether or not a server is there to read it
_CHILDREN = {}  # launchers this process started, polled so they never linger as zombies


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def _write(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(obj, fh, indent=1, default=str)
    os.replace(tmp, path)


def _read(path, default=None):
    try:
        with open(path) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def _root(root):
    root = root or os.environ.get(ROOT_VARIABLE)
    if not root:
        raise ValueError(f"no runner root: pass root or set {ROOT_VARIABLE}")
    return os.path.abspath(root)


def new_job_id(name):
    safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in str(name))[:40] or "job"
    return f"{time.strftime('%Y%m%dT%H%M%S')}-{safe}-{os.urandom(3).hex()}"


# ---- processes (Linux /proc) ----

def _stat(pid):
    """(state, ppid, session) from /proc/<pid>/stat, or None. The command name may hold spaces and brackets, so the
    fields are read after its last ')'."""
    try:
        with open(f"/proc/{pid}/stat") as fh:
            rest = fh.read().rsplit(")", 1)[1].split()
        return rest[0], int(rest[1]), int(rest[3])
    except (OSError, IndexError, ValueError):
        return None


def _alive(pid):
    if not pid:
        return False
    if os.path.isdir("/proc"):
        st = _stat(pid)
        return st is not None and st[0] not in ("Z", "X")
    if os.name == "nt":  # os.kill(pid, 0) on Windows sends CTRL_C_EVENT, which can end the whole console
        import ctypes
        kernel = ctypes.windll.kernel32
        handle = kernel.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        code = ctypes.c_ulong()
        ok = kernel.GetExitCodeProcess(handle, ctypes.byref(code))
        kernel.CloseHandle(handle)
        return bool(ok) and code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _self_and_ancestors():
    out, pid = set(), os.getpid()
    while pid and pid not in out and pid > 1:
        out.add(pid)
        st = _stat(pid)
        pid = st[1] if st else 0
    out.add(os.getpid())
    return out


def site(root):
    """The site's settings in <root>/site.json (protected_pids, cores), or {}."""
    return _read(os.path.join(root, "site.json"), {}) or {}


def protected(root=None):
    """PIDs never to be signalled: the site's, and those in limits.PROTECTED_VARIABLE."""
    own = site(root).get("protected_pids", []) if root else []
    return limits.protected_pids() | frozenset(int(p) for p in own)


def processes(job_id, launcher_pid=None, skip_pids=frozenset()):
    """The live processes of a job: tagged with its ID, in the launcher's session, or descended from the launcher.
    Only this user's processes; never `skip_pids`, this process or its ancestors. Empty where /proc is absent."""
    if not os.path.isdir("/proc"):
        return []
    uid, skip = os.getuid(), _self_and_ancestors() | set(skip_pids)
    tag = f"{TAG}={job_id}".encode() + b"\0"
    info, found = {}, set()
    for name in os.listdir("/proc"):
        if not name.isdigit():
            continue
        pid = int(name)
        if pid <= 1 or pid in skip:
            continue
        try:
            if os.stat(f"/proc/{pid}").st_uid != uid:
                continue
        except OSError:
            continue
        st = _stat(pid)
        if st is None or st[0] in ("Z", "X"):
            continue
        info[pid] = st
        try:
            with open(f"/proc/{pid}/environ", "rb") as fh:
                if tag in fh.read() + b"\0":
                    found.add(pid)
        except OSError:
            pass
        if launcher_pid and (pid == launcher_pid or st[2] == launcher_pid):
            found.add(pid)
    if launcher_pid:  # descendants, through the parent links of this user's live processes
        changed = True
        while changed:
            changed = False
            for pid, st in info.items():
                if pid not in found and (st[1] == launcher_pid or st[1] in found):
                    found.add(pid)
                    changed = True
    return sorted(found)


# ---- the launcher (runs in the job's own session) ----

def _become_subreaper():
    """Orphaned descendants then attach to the launcher rather than to init, so they stay in the job's tree."""
    try:
        import ctypes
        return ctypes.CDLL(None, use_errno=True).prctl(36, 1, 0, 0, 0) == 0  # PR_SET_CHILD_SUBREAPER
    except Exception:
        return False


def _launch(job_dir):
    spec = _read(os.path.join(job_dir, "spec.json"))
    cores = limits.pin_this_process()
    if hasattr(os, "nice") and os.nice(0) < limits.NICE:
        os.nice(limits.NICE - os.nice(0))
    env = dict(os.environ)
    env[TAG] = spec["job_id"]
    # the job's own threads, whatever its caller had: a server that keeps itself to one or two threads does not
    # pass them on to its jobs
    threads = int(spec.get("threads") or limits.THREAD_LIMIT)
    env.update({v: str(threads) for v in limits.THREAD_VARIABLES})
    env.update({k: str(v) for k, v in (spec.get("env") or {}).items()})
    _write(os.path.join(job_dir, "launch.json"), {"pid": os.getpid(), "cores": cores, "nice": os.nice(0)
                                                  if hasattr(os, "nice") else None,
                                                  "subreaper": _become_subreaper(), "started": _now()})
    cmd = spec["cmd"]
    argv = cmd if isinstance(cmd, list) else ["bash", "-c", cmd]
    stopping = {"flag": False}
    child = None

    def on_term(signum, frame):
        stopping["flag"] = True
        if child is not None and child.poll() is None:
            child.terminate()
    signal.signal(signal.SIGTERM, on_term)
    error, at_limit = None, False
    limit = float(spec.get("limit_seconds") or JOB_LIMIT_SECONDS)
    with open(os.path.join(job_dir, "job.log"), "ab") as log:
        try:
            child = subprocess.Popen(argv, cwd=spec["cwd"], env=env, stdin=subprocess.DEVNULL, stdout=log,
                                     stderr=subprocess.STDOUT)
            try:
                rc = child.wait(timeout=limit)
            except subprocess.TimeoutExpired:  # a job outlives the server that started it, never its limit
                at_limit = True
                with open(os.path.join(job_dir, "stopped"), "a") as fh:
                    fh.write(_now() + " at the job's time limit\n")
                log.write(f"stopped at the job's time limit ({int(limit)} seconds)\n".encode())
                log.flush()
                _end_own_processes(spec["job_id"])
                rc = child.wait()
        except OSError as e:  # the command or its folder does not exist: a failed job, not a lost one
            rc, error = 127, f"{type(e).__name__}: {e}"
            log.write((error + "\n").encode())
    _write(os.path.join(job_dir, "exit_code.json"), {"exit_code": rc, "finished": _now(),
                                                     "terminated": stopping["flag"] or at_limit,
                                                     "time_limit": at_limit, "error": error})
    deadline = time.monotonic() + 5  # reap what the command left, briefly; stop() handles anything longer
    while time.monotonic() < deadline:
        try:
            pid, _ = os.waitpid(-1, os.WNOHANG)
        except ChildProcessError:
            break
        if pid == 0:
            time.sleep(0.2)
    with open(os.path.join(job_dir, "done"), "w") as fh:
        fh.write(_now() + "\n")


def _end_own_processes(job_id, grace=3.0, limit=10.0):
    """From inside a job's launcher: end the job's other processes, SIGTERM, then SIGKILL after `grace` seconds."""
    keep = limits.protected_pids()
    t0 = time.monotonic()
    procs = processes(job_id, None, keep)
    for pid in procs:
        _signal(pid, signal.SIGTERM, keep)
    while procs and time.monotonic() - t0 < limit:
        time.sleep(0.2)
        procs = processes(job_id, None, keep)
        if procs and time.monotonic() - t0 >= grace:
            for pid in procs:
                _signal(pid, signal.SIGKILL, keep)


# ---- the calls ----

def _reap():
    """Poll every launcher this process started and forget those that ended: each call reaps them all, whichever
    job it asks about, so a launcher never lingers as a zombie while no one reads its job."""
    for jid, proc in list(_CHILDREN.items()):
        if proc.poll() is not None:
            del _CHILDREN[jid]


def start(cmd, name, root=None, cwd=None, threads=None, env=None, limit_seconds=None):
    """Start `cmd` (a list, run as is, or a string, run by bash -c) as a job and return its ID at once. The job
    ends at its time limit (JOB_LIMIT_SECONDS unless given), whether or not its caller is still there."""
    _reap()
    root = _root(root)
    jid = new_job_id(name)
    job_dir = os.path.join(root, jid)
    os.makedirs(job_dir)
    _write(os.path.join(job_dir, "spec.json"), {
        "job_id": jid, "name": name, "cmd": cmd, "cwd": os.path.abspath(cwd) if cwd else job_dir,
        "threads": int(threads or limits.THREAD_LIMIT), "env": dict(env or {}), "created": _now(),
        "limit_seconds": float(limit_seconds or JOB_LIMIT_SECONDS),
        "host": os.uname().nodename if hasattr(os, "uname") else ""})
    penv = dict(os.environ)
    penv[TAG] = jid
    penv["PERTURA_JOB_DIR"] = job_dir  # where its steps write their progress (pertura.progress)
    penv["PYTHONPATH"] = SRC + (os.pathsep + penv["PYTHONPATH"] if penv.get("PYTHONPATH") else "")
    if site(root).get("cores") and not penv.get(limits.CORES_VARIABLE):
        penv[limits.CORES_VARIABLE] = str(site(root)["cores"])
    keep = protected(root)
    if keep:  # inherited, so a runner called inside the job keeps them too
        penv[limits.PROTECTED_VARIABLE] = ",".join(str(p) for p in sorted(keep))
    with open(os.path.join(job_dir, "launcher.log"), "ab") as log:
        proc = subprocess.Popen([sys.executable, "-m", "pertura.runner", "_launch", job_dir],
                                stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, env=penv,
                                close_fds=True, start_new_session=True)
    _CHILDREN[jid] = proc
    with open(os.path.join(job_dir, "launcher.pid"), "w") as fh:
        fh.write(str(proc.pid))
    return jid


def _launcher_pid(job_dir):
    try:
        with open(os.path.join(job_dir, "launcher.pid")) as fh:
            return int(fh.read().strip())
    except (OSError, ValueError):
        return None


def _tail(path, lines=20):
    try:
        with open(path, "rb") as fh:
            fh.seek(0, 2)
            fh.seek(max(0, fh.tell() - 8000))
            return fh.read().decode(errors="replace").splitlines()[-lines:]
    except OSError:
        return []


def status(job_id, root=None):
    _reap()
    root = _root(root)
    job_dir = os.path.join(root, job_id)
    spec = _read(os.path.join(job_dir, "spec.json"))
    if spec is None:
        raise KeyError(f"no job {job_id} under {root}")
    if job_id in _CHILDREN:
        _CHILDREN[job_id].poll()
    lpid = _launcher_pid(job_dir)
    procs = processes(job_id, lpid, protected(root))
    ec = _read(os.path.join(job_dir, "exit_code.json"))
    # the marker is written when stopping begins; the job reads stopped once its launcher has written its exit or
    # is gone, so a reader never sees it stopped while its processes are still being ended
    if os.path.exists(os.path.join(job_dir, "stopped")) and (ec is not None or not _alive(lpid)):
        state = "stopped"
    elif ec is not None:
        state = "done" if ec["exit_code"] == 0 else "failed"
    elif _alive(lpid):
        state = "running" if os.path.exists(os.path.join(job_dir, "launch.json")) else "starting"
    elif procs:
        state = "orphaned"  # the launcher died and left processes: stop() ends them
    else:
        state = "lost"
    from . import progress
    return {"job_id": job_id, "name": spec["name"], "state": state, "progress": progress.read(job_dir),
            "exit_code": ec["exit_code"] if ec else None, "processes": procs, "launcher_pid": lpid,
            "launch": _read(os.path.join(job_dir, "launch.json")), "created": spec["created"],
            "limit_seconds": spec.get("limit_seconds"), "time_limit": bool(ec and ec.get("time_limit")),
            "log_tail": _tail(os.path.join(job_dir, "job.log"))}


def wait(job_id, root=None, timeout=None, poll=2.0):
    """Wait until the job is no longer active, or `timeout` seconds pass. Waiting only reads; interrupting it leaves
    the job as it is."""
    t0 = time.monotonic()
    while True:
        st = status(job_id, root)
        if st["state"] not in ACTIVE or (timeout is not None and time.monotonic() - t0 >= timeout):
            st["waited"] = round(time.monotonic() - t0, 1)
            return st
        time.sleep(poll)


def stop(job_id, root=None, grace=3.0, limit=10.0):
    """End every process of the job: SIGTERM, then SIGKILL after `grace` seconds; give up after `limit` and report
    what is left."""
    _reap()
    root = _root(root)
    job_dir = os.path.join(root, job_id)
    if _read(os.path.join(job_dir, "spec.json")) is None:
        raise KeyError(f"no job {job_id} under {root}")
    with open(os.path.join(job_dir, "stopped"), "a") as fh:
        fh.write(_now() + "\n")
    lpid, keep = _launcher_pid(job_dir), protected(root)
    t0, killed = time.monotonic(), False
    procs = processes(job_id, lpid, keep)
    for pid in procs:
        _signal(pid, signal.SIGTERM, keep)
    while procs and time.monotonic() - t0 < limit:
        time.sleep(0.2)
        procs = processes(job_id, lpid, keep)
        if procs and time.monotonic() - t0 >= grace:
            for pid in procs:
                _signal(pid, signal.SIGKILL, keep)
            killed = True
    if job_id in _CHILDREN:
        _CHILDREN[job_id].poll()
    return {"job_id": job_id, "stopped": not procs, "left": procs, "killed": killed,
            "seconds": round(time.monotonic() - t0, 2)}


def _signal(pid, sig, keep=frozenset()):
    if pid <= 1 or pid in keep or pid in limits.protected_pids():
        return
    try:
        os.kill(pid, sig)
    except (ProcessLookupError, PermissionError):
        pass


def jobs(root=None):
    _reap()
    root = _root(root)
    out = []
    for name in sorted(os.listdir(root)) if os.path.isdir(root) else []:
        if os.path.exists(os.path.join(root, name, "spec.json")):
            try:
                st = status(name, root)
                st.pop("log_tail", None)
                out.append(st)
            except Exception as e:
                out.append({"job_id": name, "state": "unreadable", "error": str(e)})
    return out


# ---- from the laptop, over ssh ----

class Remote:
    """The same calls on another machine, each one a short ssh command, so stopping the laptop side never touches a
    job. wait() polls status from here."""

    def __init__(self, host, python, src, root, ssh=("ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=20")):
        self.host, self.python, self.src, self.root, self.ssh = host, python, src, root, tuple(ssh)

    def _call(self, *args, timeout=120):
        line = (f"PYTHONPATH={shlex.quote(self.src)} {shlex.quote(self.python)} -m pertura.runner "
                + " ".join(shlex.quote(str(a)) for a in args))
        out = subprocess.run([*self.ssh, self.host, line], capture_output=True, text=True, timeout=timeout)
        if out.returncode != 0:
            raise RuntimeError(f"runner on {self.host} failed ({out.returncode}): {out.stderr.strip()[-2000:]}")
        return json.loads(out.stdout)

    def start(self, cmd, name, cwd=None, threads=None):
        args = ["start", "--root", self.root, "--name", name]
        if cwd:
            args += ["--cwd", cwd]
        if threads:
            args += ["--threads", threads]
        args += ["--"] + (list(cmd) if isinstance(cmd, (list, tuple)) else [cmd])
        return self._call(*args)["job_id"]

    def status(self, job_id):
        return self._call("status", "--root", self.root, job_id)

    def stop(self, job_id):
        return self._call("stop", "--root", self.root, job_id)

    def jobs(self):
        return self._call("list", "--root", self.root)

    def wait(self, job_id, timeout=None, poll=10.0):
        t0 = time.monotonic()
        while True:
            st = self.status(job_id)
            if st["state"] not in ACTIVE or (timeout is not None and time.monotonic() - t0 >= timeout):
                return st
            time.sleep(poll)


def main(argv=None):
    p = argparse.ArgumentParser(prog="python -m pertura.runner")
    sub = p.add_subparsers(dest="call", required=True)
    s = sub.add_parser("start")
    s.add_argument("--root")
    s.add_argument("--name", required=True)
    s.add_argument("--cwd")
    s.add_argument("--threads", type=int)
    s.add_argument("cmd", nargs=argparse.REMAINDER)
    for call in ("status", "wait", "stop"):
        c = sub.add_parser(call)
        c.add_argument("--root")
        c.add_argument("job_id")
        if call == "wait":
            c.add_argument("--timeout", type=float)
    sub.add_parser("list").add_argument("--root")
    sub.add_parser("_launch").add_argument("job_dir")
    a = p.parse_args(argv)
    if a.call == "_launch":
        return _launch(a.job_dir)
    if a.call == "start":
        cmd = a.cmd[1:] if a.cmd[:1] == ["--"] else a.cmd
        if not cmd:
            p.error("start needs a command after --")
        out = {"job_id": start(cmd[0] if len(cmd) == 1 else cmd, a.name, a.root, a.cwd, a.threads)}
    elif a.call == "status":
        out = status(a.job_id, a.root)
    elif a.call == "wait":
        out = wait(a.job_id, a.root, a.timeout)
    elif a.call == "stop":
        out = stop(a.job_id, a.root)
    else:
        out = jobs(a.root)
    print(json.dumps(out, indent=1, default=str))


if __name__ == "__main__":
    main()
