"""What development session 6 showed (docs/build/development_sessions/session_06_crisprai_sonnet, problem 6), fixed
for any session (docs/build_goal_session6_fixes.md, item 6): four runs that had ended by 20:12 showed as running for
an hour. The job check never started, as the development launch sets no plugin option, and a job was marked ended
only when the tool that started it named it again. Now:
- the hook reads the run states of every Pertura reply: the runs ended since the last call, a run any reply names,
  and pertura_status's whole list of running runs;
- the job check takes the host from the server's reply when the plugin option is not set;
- a job not checked for a while is said to be not checked since a time of day, never running."""
import os
import sys
import tempfile
import time
import unittest

from pertura import analysis_service

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "plugin", "status"))
import hooks  # noqa: E402
import status_file as sf  # noqa: E402
import status_words as words  # noqa: E402

NOW = 1_800_000_000.0


def running(st, rid, what, at):
    st["jobs"][rid] = {"run_id": rid, "what": what, "kind": "module", "question_id": None, "finding_id": None,
                       "started": at, "state": "running", "ended": None, "checked": at}


class TestEveryReplysRunStates(unittest.TestCase):
    def test_a_run_any_reply_names_as_ended_is_ended_at_that_call(self):
        st = sf.empty("/w/p-1a2b3c4d")
        running(st, "r1", "C.genes", NOW - 3600)
        sf.apply_call(st, "read_table", {}, {"rows": [], "ended_since_your_last_call": [
            {"run_id": "r1", "what": "C.genes", "state": "done"}]}, now=NOW)
        self.assertEqual((st["jobs"]["r1"]["state"], st["jobs"]["r1"]["ended"]), ("done", NOW))
        self.assertIn(f"E\t{NOW:.0f}\tdone\tC.genes", sf.line_file(st))  # what the status line draws as finished

    def test_the_status_list_of_running_runs_is_the_whole_list(self):
        st = sf.empty("/w/p-1a2b3c4d")
        running(st, "r1", "first look: C.any_effect", NOW - 3600)
        since = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(NOW - 120))
        sf.apply_call(st, "pertura_status", {}, {"project": "/w/p-1a2b3c4d", "running": [
            {"run_id": "r2", "what": "checks of finding F6", "since": since}]}, now=NOW)
        self.assertEqual(st["jobs"]["r1"]["state"], "ended")  # left out of the list: it has ended
        self.assertEqual(st["jobs"]["r2"]["state"], "running")  # started where no reply named it, as in the notebook
        self.assertEqual(st["jobs"]["r2"]["started"], NOW - 120)
        self.assertIn("ended", words.job_line([], [(NOW, "ended", "first look: C.any_effect")], None, NOW + 30))

    def test_a_run_a_notebook_cell_starts_is_a_job(self):
        st = sf.empty("/w/p-1a2b3c4d")
        sf.apply_call(st, "run_code", {"what": "Mixscape on every target"},
                      {"run_id": "run0042-mixscape", "state": "running"}, now=NOW)
        self.assertEqual(st["jobs"]["run0042-mixscape"]["what"], "Mixscape on every target")
        self.assertEqual(len(sf.running_jobs(st)), 1)


class TestTheHostFromTheServer(unittest.TestCase):
    def test_the_server_names_the_host_it_was_launched_from(self):
        old = os.environ.pop("PERTURA_HOST", None)
        try:
            self.assertEqual(analysis_service.with_host({"a": 1}), {"a": 1})
            os.environ["PERTURA_HOST"] = "analysis-server"
            out = analysis_service.with_host({"a": 1})  # and the server's code and Python, for the recorder's reads
            self.assertEqual({k: out[k] for k in ("a", "analysis_host")}, {"a": 1, "analysis_host": "analysis-server"})
            self.assertEqual(sorted(out), ["a", "analysis_host", "remote_code", "remote_python"])
        finally:
            os.environ.pop("PERTURA_HOST", None)
            if old is not None:
                os.environ["PERTURA_HOST"] = old

    def test_the_job_check_starts_with_it_where_the_plugin_option_is_not_set(self):
        old = os.environ.pop(hooks.HOST_VARIABLE, None)
        started = []
        try:
            with tempfile.TemporaryDirectory() as home:
                event = {"session_id": "s1", "tool_name": sf.TOOL_PREFIX + "open_project",
                         "tool_response": {"project": "/w/p-1a2b3c4d", "analysis_host": "analysis-server"}}
                hooks.after_tool_call(event, home, start_check=lambda h, r: started.append(h))
                hooks.after_tool_call({**event, "tool_name": sf.TOOL_PREFIX + "run_module",
                                       "tool_input": {"module": "C.genes"},
                                       "tool_response": {"run_id": "r1", "state": "running"}},
                                      home, start_check=lambda h, r: started.append(h))
        finally:
            if old is not None:
                os.environ[hooks.HOST_VARIABLE] = old
        self.assertEqual(started, ["analysis-server"])


class TestAJobNotChecked(unittest.TestCase):
    def test_it_is_said_to_be_not_checked_since_a_time_and_not_running(self):
        line = words.job_line([(NOW - 4000, NOW - 3600, "C.genes"), (NOW - 60, NOW - 5, "B.dose")], [], None, NOW,
                              clock=lambda t: time.strftime("%H:%M", time.localtime(t)))
        self.assertIn("running  B.dose 1 min", words.plain(line))
        self.assertIn(f"C.genes not checked since {time.strftime('%H:%M', time.localtime(NOW - 3600))}",
                      words.plain(line))
        self.assertNotIn("C.genes 1 h", words.plain(line))


if __name__ == "__main__":
    unittest.main()
