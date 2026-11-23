"""The runner (ADR 0020, build step 1): the logic everywhere, real processes on Linux only. The server-side pass bars
are measured by analysis/runner_checks/check_runner_on_server.py."""
import json
import os
import sys
import time

import pytest

from pertura import limits, runner

linux = pytest.mark.skipif(not sys.platform.startswith("linux"), reason="processes and affinity need Linux /proc")


def test_parse_cores():
    assert limits.parse_cores("60-63") == (60, 61, 62, 63)
    assert limits.parse_cores("0,2,4-5") == (0, 2, 4, 5)
    assert limits.parse_cores(" 3 , 1 ") == (1, 3)


def test_protected_pids_come_from_the_site_not_the_code(tmp_path, monkeypatch):
    monkeypatch.setenv(limits.PROTECTED_VARIABLE, "11, 12")
    assert limits.protected_pids() == {11, 12}
    (tmp_path / "site.json").write_text(json.dumps({"protected_pids": [13]}))
    assert runner.protected(tmp_path) == {11, 12, 13}
    monkeypatch.delenv(limits.PROTECTED_VARIABLE)
    assert limits.protected_pids() == frozenset()


def test_job_id_is_safe_and_unique():
    a, b = runner.new_job_id("my job/1"), runner.new_job_id("my job/1")
    assert a != b and "/" not in a and " " not in a and "my-job-1" in a


def test_no_root_is_refused(monkeypatch):
    monkeypatch.delenv(runner.ROOT_VARIABLE, raising=False)
    with pytest.raises(ValueError):
        runner.jobs()


def _fake_job(root, jid, **files):
    d = root / jid
    d.mkdir()
    (d / "spec.json").write_text(json.dumps({"job_id": jid, "name": "n", "cmd": "true", "cwd": str(d),
                                             "created": "t"}))
    for name, content in files.items():
        (d / name).write_text(content)
    return d


def test_states_from_files(tmp_path):
    _fake_job(tmp_path, "a", **{"exit_code.json": json.dumps({"exit_code": 0})})
    _fake_job(tmp_path, "b", **{"exit_code.json": json.dumps({"exit_code": 3})})
    _fake_job(tmp_path, "c", **{"exit_code.json": json.dumps({"exit_code": -15}), "stopped": "t"})
    _fake_job(tmp_path, "d")
    states = {j["job_id"]: j["state"] for j in runner.jobs(tmp_path)}
    assert states == {"a": "done", "b": "failed", "c": "stopped", "d": "lost"}


def test_a_job_reads_stopped_once_its_launcher_has_ended(tmp_path, monkeypatch):
    """The marker is written as stopping begins: while the launcher still ends the job's processes, the job runs."""
    d = _fake_job(tmp_path, "j", **{"stopped": "t at the job's time limit", "launch.json": "{}", "launcher.pid": "77"})
    monkeypatch.setattr(runner, "_alive", lambda pid: pid == 77)
    assert runner.status("j", tmp_path)["state"] == "running"
    (d / "exit_code.json").write_text(json.dumps({"exit_code": -15, "time_limit": True}))
    st = runner.status("j", tmp_path)
    assert st["state"] == "stopped" and st["time_limit"]
    (d / "exit_code.json").unlink()
    monkeypatch.setattr(runner, "_alive", lambda pid: False)  # a launcher killed before it wrote its exit
    assert runner.status("j", tmp_path)["state"] == "stopped"


def test_signal_never_touches_protected_or_init(monkeypatch):
    sent = []
    monkeypatch.setattr(os, "kill", lambda pid, sig: sent.append(pid))
    monkeypatch.setenv(limits.PROTECTED_VARIABLE, "4242")
    for pid in (0, 1, 4242, 4343):
        runner._signal(pid, 15, keep=frozenset({4343}))
    assert sent == []


@linux
def test_core_set_follows_the_variable(monkeypatch):
    allowed = sorted(os.sched_getaffinity(0))
    monkeypatch.setenv(limits.CORES_VARIABLE, str(allowed[0]))
    assert limits.core_set() == (allowed[0],)
    monkeypatch.delenv(limits.CORES_VARIABLE)
    assert limits.core_set() == tuple(allowed[-limits.THREAD_LIMIT:])


@linux
def test_a_job_runs_pinned_and_finishes(tmp_path):
    jid = runner.start(["bash", "-c", "echo hello; python3 -c 'import os; print(sorted(os.sched_getaffinity(0)))'"],
                       "echo", tmp_path)
    st = runner.wait(jid, tmp_path, timeout=30, poll=0.2)
    assert st["state"] == "done" and st["exit_code"] == 0
    assert "hello" in st["log_tail"]
    assert st["log_tail"][-1] == str(list(limits.core_set()))
    assert st["launch"]["cores"] == list(limits.core_set())


@linux
def test_stop_ends_a_job_and_its_escaped_children(tmp_path):
    jid = runner.start("sleep 60 & setsid sleep 60 & (sleep 60 &) ; sleep 60", "sleepers", tmp_path)
    time.sleep(1.5)
    assert len(runner.status(jid, tmp_path)["processes"]) >= 4
    out = runner.stop(jid, tmp_path)
    assert out["stopped"] and out["left"] == [] and out["seconds"] <= 10
    assert runner.status(jid, tmp_path)["state"] == "stopped"


@linux
def test_a_job_stops_at_its_time_limit_with_its_children(tmp_path):
    jid = runner.start("sleep 60 & sleep 60", "past its limit", tmp_path, limit_seconds=1)
    st = runner.wait(jid, tmp_path, timeout=30, poll=0.2)
    # the launcher may still be finishing for a moment after it writes its exit; the job's own processes are gone
    assert st["state"] == "stopped" and st["time_limit"]
    assert [p for p in st["processes"] if p != st["launcher_pid"]] == []
    assert any("time limit" in line for line in st["log_tail"])


def test_a_job_carries_its_time_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(runner.subprocess, "Popen", lambda *a, **k: type("P", (), {"pid": 1, "poll": lambda s: 0})())
    jid = runner.start(["true"], "limit", tmp_path)
    with open(tmp_path / jid / "spec.json") as fh:
        assert json.load(fh)["limit_seconds"] == runner.JOB_LIMIT_SECONDS
    jid = runner.start(["true"], "limit", tmp_path, limit_seconds=5)
    with open(tmp_path / jid / "spec.json") as fh:
        assert json.load(fh)["limit_seconds"] == 5


@linux
def test_the_servers_end_spares_the_processes_of_jobs(tmp_path):
    import subprocess
    from pertura import mcp_server
    jid = runner.start("sleep 60", "outlives the server", tmp_path)
    plain = subprocess.Popen(["sleep", "60"])
    time.sleep(1.0)
    mcp_server._end_children(spare_jobs=True)
    plain.wait(timeout=10)
    assert plain.returncode != 0
    assert runner.status(jid, tmp_path)["state"] == "running"
    runner.stop(jid, tmp_path)


@linux
def test_a_missing_command_fails_not_lost(tmp_path):
    jid = runner.start(["no-such-command-pertura"], "missing", tmp_path)
    st = runner.wait(jid, tmp_path, timeout=30, poll=0.2)
    assert st["state"] == "failed"


class _Launcher:
    """A launcher as runner._CHILDREN holds it: poll() gives None while it runs and its exit code once it ended."""

    def __init__(self, ended):
        self.ended, self.polled = ended, 0

    def poll(self):
        self.polled += 1
        return 0 if self.ended else None


def test_every_call_reaps_the_launchers_that_ended(tmp_path, monkeypatch):
    """Session 6: a checks job's launcher lingered as a zombie for about 20 minutes, because the runner reaped a
    launcher only when its own job was read. Any call now reaps every launcher that ended."""
    ended, running = _Launcher(True), _Launcher(False)
    monkeypatch.setattr(runner, "_CHILDREN", {"job-ended": ended, "job-running": running})
    assert runner.jobs(tmp_path) == []  # a call about no job at all
    assert ended.polled == 1 and running.polled == 1
    assert list(runner._CHILDREN) == ["job-running"]
