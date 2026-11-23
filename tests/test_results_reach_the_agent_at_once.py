"""Results reach the agent at once (docs/adr/0032, amendment 1; docs/build_goal_session5_fixes.md, step 1).

- No call waits past the client's limit: beyond it a client moves the call to the background, and the figure in its
  reply no longer reaches the model.
- Every reply opens the runs that ended since the last call, each once, with its result and figure (development
  session 11, problem 16: a run only named was never opened).
- Several findings' checks run at a time."""
import os
import re
import tempfile
import threading
import unittest
from unittest import mock

from pertura import analysis_service as asv
from pertura import finding_checks, limits, resources
from pertura.analysis_service import AnalysisService

try:
    import fcntl  # noqa: F401
    HAVE_LOCKS = True
except ImportError:
    HAVE_LOCKS = False


class Service(unittest.TestCase):
    """An analysis service over a stand-in record, as in test_notebooks_and_runs."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.svc = AnalysisService({"workspace": os.path.join(self.tmp.name, "ws")})
        self.svc.project = os.path.join(self.tmp.name, "project")
        os.makedirs(self.svc.project)
        self.svc.design = mock.Mock(cfg={})
        self.svc.record = mock.Mock()
        self.runs = {}
        self.svc.record.state.return_value = {"runs": self.runs}
        roots = mock.patch.object(self.svc, "_jobs_root", return_value=self.tmp.name)
        roots.start()
        self.addCleanup(roots.stop)

    def tearDown(self):
        self.svc.record = None
        self.svc.notebooks = {}
        self.svc.close()
        self.tmp.cleanup()


class TestNoLongWaits(Service):
    def test_the_limit_is_below_the_clients(self):
        self.assertLessEqual(limits.CALL_WAIT_MAX, 100)

    def test_no_tool_description_proposes_a_longer_wait(self):
        for t in asv.TOOLS:
            d = t["description"]
            for m in re.finditer(r"\bwait", d):
                for n in re.findall(r"\d+", d[m.start(): m.start() + 60]):
                    self.assertLessEqual(int(n), limits.CALL_WAIT_MAX, (t["name"], d))

    def test_module_result_waits_at_most_the_limit(self):
        self.runs["r1"] = {"run_id": "r1", "job_id": "r1", "state": "running", "what": "module C.genes"}
        seen = {}

        def first(ids, root, timeout):
            seen["timeout"] = timeout
            return None

        with mock.patch.object(self.svc, "_wait_first", side_effect=first), \
                mock.patch.object(self.svc, "_running_seconds", return_value=5):
            out = self.svc.module_result("r1", wait=1800)
        self.assertLessEqual(seen["timeout"], limits.CALL_WAIT_MAX)
        self.assertNotIn("wait=", out["next"])
        self.assertIn("wait_for_runs returns when a run ends", out["next"])

    def test_the_words_for_a_running_run_propose_no_wait(self):
        self.assertNotIn("wait=", asv.MEANWHILE)
        self.assertIn("wait_for_runs returns when a run ends", asv.MEANWHILE)


class TestTheRunsThatEnded(Service):
    def test_a_run_that_ended_comes_opened_once(self):
        import json
        self.runs["r1"] = {"run_id": "r1", "job_id": "j1", "state": "done", "what": "module B.efficacy",
                           "question_id": "B.efficacy"}
        os.makedirs(os.path.join(self.tmp.name, "r1"))
        with open(os.path.join(self.tmp.name, "r1", "result.json"), "w", encoding="utf-8") as fh:
            json.dump({"status": "measured", "summary": "28 of 28 targets lower their gene",
                       "diagnostics": {"targets": 28}}, fh)
        self.svc._need = lambda: None
        first = self.svc._with_ended({"x": 1})
        again = self.svc._with_ended({"x": 2})
        self.assertEqual([r["run_id"] for r in first["ended_since_your_last_call"]], ["r1"])
        self.assertEqual(first["ended_since_your_last_call"][0]["what"], "module B.efficacy")
        result = first["ended_since_your_last_call"][0]["result"]  # its reading; its diagnostics in run_result
        self.assertEqual(result["summary"], "28 of 28 targets lower their gene")
        self.assertNotIn("diagnostics", result)
        self.assertIn("run_result", first["ended_since_your_last_call"][0]["whole"])
        self.assertIn("r1", self.svc._opened)
        self.assertNotIn("ended_since_your_last_call", again)
        self.assertNotIn("also_ended_not_opened", self.svc.module_result("r1"))  # not named again either

    def test_the_reply_s_own_run_and_an_opened_one_are_not_given_again(self):
        for r in ("r1", "r2", "r3"):
            self.runs[r] = {"run_id": r, "job_id": r, "state": "ok", "what": f"module {r}"}
        self.svc._opened.add("r2")
        out = self.svc._with_ended({"run_id": "r1"})
        self.assertEqual([r["run_id"] for r in out["ended_since_your_last_call"]], ["r3"])

    def test_a_running_run_and_a_notebook_cell_are_not_named(self):
        self.runs["r1"] = {"run_id": "r1", "job_id": "j1", "state": "running", "what": "module C.genes"}
        self.runs["c1"] = {"run_id": "c1", "job_id": None, "state": "ok", "what": "a cell"}
        self.assertNotIn("ended_since_your_last_call", self.svc._with_ended({}))

    def test_every_tool_s_reply_carries_them(self):
        self.runs["r1"] = {"run_id": "r1", "job_id": "j1", "state": "failed", "what": "module A.what_was_done"}
        with mock.patch.object(self.svc, "pertura_status", return_value={"summary": "s"}):
            ok, out = self.svc.call("pertura_status", {})
        self.assertTrue(ok, out)
        self.assertEqual(out["ended_since_your_last_call"][0]["state"], "failed")

    def test_a_reply_is_kept_when_naming_them_fails(self):
        self.svc.record.sync_runs.side_effect = OSError("the jobs' folder is gone")
        self.assertEqual(self.svc._with_ended({"x": 1}), {"x": 1})


class TestTheRunsEndedBeforeTheProjectOpened(unittest.TestCase):
    def log_ended(self, svc, rid):
        from pertura import record as rc
        svc.record.append("run_logged", {"run_id": rid, "job_id": rid, "what": "module A", "code": "x",
                                         "inputs": [], "question_id": None, "uses": [], "step": None}, rc.TOOL)
        svc.record.append("run_finished", {"run_id": rid, "state": "ok", "output": "", "figure": None}, rc.TOOL)

    def test_a_run_a_reply_named_is_not_named_again_by_the_next_server(self):
        # jobs outlive the server, and a client without a window starts one per turn: the replies' memory is the
        # project's, so a run named once is not news again, and one that ended unnamed is named once
        with tempfile.TemporaryDirectory() as tmp:
            data = os.path.join(tmp, "deposit")
            os.makedirs(data)
            first = AnalysisService({"workspace": os.path.join(tmp, "ws")})
            first.open_project(data)
            self.log_ended(first, "old")
            first._with_ended({})  # a reply names it
            first._keep_said()
            self.log_ended(first, "unnamed")
            first.close()
            again = AnalysisService({"workspace": os.path.join(tmp, "ws")})
            again.open_project(data)
            self.assertIn("old", again._announced)
            self.assertNotIn("unnamed", again._announced)
            again.close()

    def test_in_a_project_that_kept_nothing_they_are_the_record_s_and_not_named_as_news(self):
        from pertura.analysis_service import SAID
        with tempfile.TemporaryDirectory() as tmp:
            data = os.path.join(tmp, "deposit")
            os.makedirs(data)
            first = AnalysisService({"workspace": os.path.join(tmp, "ws")})
            first.open_project(data)
            self.log_ended(first, "old")
            first.close()
            os.remove(os.path.join(first.project, SAID))  # a project from before the replies kept their memory
            again = AnalysisService({"workspace": os.path.join(tmp, "ws")})
            again.open_project(data)
            self.assertIn("old", again._announced)
            again.close()


class TestChecksAtOnce(Service):
    def test_the_number_is_a_resource(self):
        self.assertEqual(resources.DEFAULTS["checks_at_once"], 3)
        with self.assertRaises(resources.ResourceError):
            resources.load({"resources": {"checks_at_once": 0}})

    def start(self, busy):
        for k in range(busy):
            self.runs[f"c{k}"] = {"run_id": f"c{k}", "job_id": f"c{k}", "state": "running",
                                  "what": f"checks of finding f{k}"}
        self.svc.record.enabled.return_value = True
        self.svc.record.start_run.return_value = "checks-run"
        f = {"target": "T1", "feature": "G1", "direction": "down", "question_id": None}
        parts = {"kind": "gene", "reference": "control_guides", "cells": None, "genes": None, "state_table": None,
                 "lead": None}
        with mock.patch.object(self.svc, "_screen_dir", return_value=self.tmp.name), \
                mock.patch.object(self.svc, "_design_path", return_value=os.path.join(self.tmp.name, "design.yaml")), \
                mock.patch.object(self.svc, "_resources", return_value={"checks_at_once": 3}):
            return self.svc._start_checks("f9", f, parts)

    def test_the_job_is_given_the_number_and_the_reply_its_place(self):
        out = self.start(busy=0)
        cmd = self.svc.record.start_run.call_args[0][1]
        self.assertEqual(cmd[cmd.index("--at-once") + 1], "3")
        self.assertEqual(out["checks_run"], "checks-run")
        self.assertIn("up to 3", out["next"])

    def test_a_full_set_of_slots_queues_the_spec_as_a_file(self):
        """2026-11-18: a spec waits as a file, never as a process holding the screen object (problem 11)."""
        out = self.start(busy=4)
        self.svc.record.start_run.assert_not_called()
        self.assertTrue(out["checks"].startswith("queued behind 0 others"), out)


@unittest.skipUnless(HAVE_LOCKS, "lock files need fcntl")
class TestTheSlots(unittest.TestCase):
    def test_up_to_n_jobs_hold_a_slot_together_and_the_next_waits_for_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            prefix = os.path.join(tmp, "checks.lock")
            a, b, c = (finding_checks._slots(prefix, 2, poll=0.05) for _ in range(3))
            a.__enter__()
            b.__enter__()  # two at once
            got = threading.Event()

            def third():
                c.__enter__()
                got.set()

            threading.Thread(target=third, daemon=True).start()
            self.assertFalse(got.wait(0.5))  # both slots are taken
            a.__exit__(None, None, None)
            self.assertTrue(got.wait(5))  # a slot came free
            b.__exit__(None, None, None)
            c.__exit__(None, None, None)


if __name__ == "__main__":
    unittest.main()
