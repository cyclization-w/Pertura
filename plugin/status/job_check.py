"""The job check: while a Pertura job runs and the agent is idle, find out once a minute whether it has ended.

A job ends on the server when its folder gets exit_code.json (or a 'stopped' marker). Once a minute, one ssh call
per project reads those files for the running jobs, with plain shell commands: no Pertura code runs on the server
for it. The status file is updated, so the status line shows a job as finished soon after it is.

One check runs per machine. The hook after a tool call starts it when a job is running and none is alive (it
writes a heartbeat each round); it ends by itself when no live session has a running job, or after MAX_HOURS.

    python job_check.py --host <ssh host>
"""
import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import status_file as sf  # noqa: E402

EVERY = 60.0
MAX_HOURS = 24.0
SESSION_IDLE = 24 * 3600.0  # a session silent this long counts as ended, as when it closed without saying so
HEARTBEAT_LATE = 3 * EVERY
SAFE_HOST = re.compile(r"^[A-Za-z0-9_.@:-]{1,253}$")
SAFE_ID = re.compile(r"^[A-Za-z0-9_.-]{1,200}$")
SAFE_PATH = re.compile(r"^/[A-Za-z0-9_./+-]{1,1000}$")


def jobs_root(project):
    """Where the analysis service starts a project's jobs (its default runner root)."""
    return project.rstrip("/") + "/design/jobs"


def script(root, run_ids):
    """The shell lines that report each job's end, in the runner's order (runner.status): '<id> stopped' where its
    'stopped' marker is written (as the stopping begins) and its exit is written or its launcher is gone; else
    '<id> exit <exit_code.json>'; else '<id> active', or '<id> missing'."""
    lines = []
    for rid in run_ids:
        d, q = shlex.quote(f"{root}/{rid}"), shlex.quote(rid)
        launcher_alive = '[ -f "$d/launcher.pid" ] && kill -0 "$(cat "$d/launcher.pid")" 2>/dev/null'
        lines.append(f'd={d}; if [ -e "$d/stopped" ] && {{ [ -f "$d/exit_code.json" ] || ! {{ {launcher_alive}; }}; }}; '
                     f"then echo {q} stopped; "
                     f'elif [ -f "$d/exit_code.json" ]; then printf \'%s exit \' {q}; tr -d \'\\n\' < "$d/exit_code.json"; '
                     f'echo; elif [ -d "$d" ]; then echo {q} active; else echo {q} missing; fi')
    return "\n".join(lines)


PAGE_STATE = "page_state.json"  # in the project, written by the analysis service at each call


def page_state_line(project):
    """The shell line that prints the project's page state on one line after the word 'page_state', if it has one."""
    p = shlex.quote(f"{project.rstrip('/')}/{PAGE_STATE}")
    return f"if [ -f {p} ]; then printf 'page_state '; tr -d '\\n' < {p}; echo; fi"


def read_page_state(text):
    """The page state the line printed, or None."""
    for line in str(text or "").splitlines():
        if line.startswith("page_state "):
            try:
                state = json.loads(line[len("page_state "):])
            except ValueError:
                return None
            return state if isinstance(state, dict) else None
    return None


def read_reply(text):
    """{run ID: state} from the script's output: done, failed, stopped or active; a missing folder says nothing."""
    out = {}
    for line in str(text or "").splitlines():
        parts = line.strip().split(" ", 2)
        if len(parts) < 2 or not SAFE_ID.match(parts[0]):
            continue
        rid, word = parts[0], parts[1]
        if word == "exit":
            try:
                code = json.loads(parts[2]).get("exit_code") if len(parts) == 3 else None
            except ValueError:
                code = None
            out[rid] = "done" if code == 0 else "failed"
        elif word in ("stopped", "active"):
            out[rid] = word
    return out


def run_ssh(host, text, timeout=45):
    kw = {"creationflags": 0x08000000} if os.name == "nt" else {}  # no console window from a detached process
    r = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", host, text],
                       capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL, **kw)
    return r.stdout


def fetch_page_state(host, key, root=None, ssh=run_ssh, now=None):
    """Fold the project's page state into its status and page: read where the project is on this machine, else over
    one ssh call to the host. Returns the status, or None."""
    root = root or sf.home()
    st = sf.read_json(sf.project_path(key, root)) or {}
    project = str(st.get("project") or "")
    local = os.path.join(project, PAGE_STATE)
    if project and os.path.isfile(local):
        state = sf.read_json(local)
    elif host and SAFE_HOST.match(host) and SAFE_PATH.match(project):
        try:
            state = read_page_state(ssh(host, page_state_line(project)))
        except (OSError, subprocess.SubprocessError):
            return None
    else:
        return None
    return sf.fold_page_state(key, state, root, now)


def live_projects(root, now):
    """The project keys of the sessions still open (not ended, not silent for a day)."""
    keys = set()
    folder = os.path.join(root, "sessions")
    for name in os.listdir(folder) if os.path.isdir(folder) else []:
        s = sf.read_json(os.path.join(folder, name)) or {}
        if s.get("project") and not s.get("ended") and now - float(s.get("updated") or 0) < SESSION_IDLE:
            keys.add(s["project"])
    return sorted(keys)


def check_once(host, root=None, ssh=run_ssh, now=None):
    """One round: for each live project with running jobs, one ssh call; the jobs that ended are marked so.
    Returns the number of jobs still running."""
    root = root or sf.home()
    running = 0
    for key in live_projects(root, time.time() if now is None else now):
        pp = sf.project_path(key, root)
        st = sf.read_json(pp) or {}
        project = str(st.get("project") or "")
        ids = [j["run_id"] for j in sf.running_jobs(st) if SAFE_ID.match(str(j.get("run_id")))]
        if not ids or not SAFE_PATH.match(project):
            continue
        try:
            said = ssh(host, script(jobs_root(project), ids) + "\n" + page_state_line(project))
            states, page_state = read_reply(said), read_page_state(said)
        except (OSError, subprocess.SubprocessError):
            states = page_state = None
        t = time.time() if now is None else now
        with sf.Lock(pp):
            st = sf.read_json(pp) or st
            st["jobs_checked"] = {"at": t, "ok": states is not None}
            for rid, state in (states or {}).items():
                job = st["jobs"].get(rid)
                if job is None or job.get("state") not in sf.ACTIVE:
                    continue
                job["checked"] = t
                if state != "active":
                    job["state"], job["ended"] = state, t
                    if rid == (st.get("screen_object") or {}).get("job_id"):
                        st["screen_object"]["state"] = "built" if state == "done" else "failed"
            sf.save(key, st, root)
        if page_state is not None:  # what the page shows beside the replies, while the run runs
            st = sf.fold_page_state(key, page_state, root, t) or st
        running += len(sf.running_jobs(st))
    return running


def heartbeat_path(root=None):
    return os.path.join(root or sf.home(), "job_check.heartbeat")


def alive(root=None, now=None):
    try:
        return (time.time() if now is None else now) - os.path.getmtime(heartbeat_path(root)) < HEARTBEAT_LATE
    except OSError:
        return False


def start(host, root=None):
    """Start the check in the background, detached from the hook that starts it; nothing when one is alive or the
    host is not a plain ssh host name."""
    if not host or not SAFE_HOST.match(host) or alive(root):
        return False
    root = root or sf.home()
    os.makedirs(root, exist_ok=True)
    with open(heartbeat_path(root), "w") as fh:  # claimed at once, so a second hook does not start another
        fh.write(str(time.time()))
    kw = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL, "close_fds": True}
    if os.name == "nt":
        kw["creationflags"] = 0x00000008 | 0x00000200 | 0x08000000  # detached, own process group, no window
    else:
        kw["start_new_session"] = True
    env = dict(os.environ, **{sf.HOME_VARIABLE: root})
    subprocess.Popen([sys.executable, "-S", os.path.abspath(__file__), "--host", host], env=env, **kw)
    return True


def start_page_state(host, key, root=None):
    """Fetch the project's page state once, in the background, detached from the hook that asks for it; nothing when
    the host is not a plain ssh host name."""
    if not host or not SAFE_HOST.match(host) or not SAFE_ID.match(str(key)):
        return False
    root = root or sf.home()
    kw = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL, "close_fds": True}
    if os.name == "nt":
        kw["creationflags"] = 0x00000008 | 0x00000200 | 0x08000000  # detached, own process group, no window
    else:
        kw["start_new_session"] = True
    env = dict(os.environ, **{sf.HOME_VARIABLE: root})
    subprocess.Popen([sys.executable, "-S", os.path.abspath(__file__), "--host", host, "--page-state", str(key)],
                     env=env, **kw)
    return True


def beat():
    with open(heartbeat_path(), "w") as fh:
        fh.write(str(time.time()))


def main(argv=None):
    p = argparse.ArgumentParser(prog="job_check.py")
    p.add_argument("--host", required=True)
    p.add_argument("--page-state", help="a project's key: fetch its page state once, and end")
    a = p.parse_args(argv)
    if not SAFE_HOST.match(a.host):
        return 2
    if a.page_state:
        fetch_page_state(a.host, a.page_state)
        return 0
    t0 = time.time()
    while time.time() - t0 < MAX_HOURS * 3600:  # the call that started it has just reported, so wait first
        beat()
        time.sleep(EVERY)
        beat()
        if check_once(a.host) == 0:
            break
    try:
        os.remove(heartbeat_path())
    except OSError:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
