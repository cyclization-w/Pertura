"""The loop decided by the record (2026-11-20), its checks and its replies:
- the session's server starts queued checks with no call, and is the only process that starts them (apply-inbox at a
  turn's Stop only queues them);
- each checks job's row says whether the checks held, with the claims that repeat it;
- every unanswered doubt is to answer and each open part of the question is said, in reads' replies too;
- what the replies delivered is kept in said_in_replies.json;
- stop-facts lists what is open;
- the server names its code and Python beside its host."""
import io
import json
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

import test_analysis_service as service_tests

from pertura import analysis_service as asv, mcp_server, record as rc, recording
from pertura.analysis_service import AnalysisService


def by_hand(svc, job_ids=False):
    """start_run as a runner job would log it, without a process: the run stays running until finished here."""
    started = []

    def start(what, cmd, inputs, root, **k):
        rid = f"job{len(started) + 1}"
        started.append(cmd)
        svc.record.append("run_logged", {"run_id": rid, "job_id": rid if job_ids else None, "what": what,
                                         "code": " ".join(cmd), "inputs": inputs,
                                         "question_id": k.get("question_id")}, rc.TOOL)
        return rid
    return started, start


def checked(svc, run_id, finding_id, outcomes, reason="its test"):
    for check, outcome in outcomes.items():
        svc.record.append("check_recorded", {"finding_id": finding_id, "check": check, "outcome": outcome,
                                             "reason": reason, "run_id": run_id}, rc.TOOL)


class Session(unittest.TestCase):
    session = service_tests.TestRobustnessSwitch.session
    finding = service_tests.TestRobustnessSwitch.finding

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = os.path.join(self.tmp.name, "s")
        self.svc = self.session(self.root, {})
        no_jobs = mock.patch("pertura.runner.start", side_effect=AssertionError("no real job in these tests"))
        no_jobs.start()
        self.addCleanup(no_jobs.stop)

    def tearDown(self):
        self.svc.stop_waiting()
        self.svc.close()
        self.tmp.cleanup()


class TestTheQueueWithNoCall(Session):

    def test_the_servers_thread_starts_a_queued_spec_when_no_call_comes(self):
        svc = self.svc
        started, start = by_hand(svc)
        resources = svc._resources
        with mock.patch.object(svc.record, "start_run", side_effect=start), \
                mock.patch.object(svc, "_resources", lambda: {**resources(), "checks_at_once": 1}), \
                mock.patch.object(asv, "CHECKS_TICK", 0.05):
            svc.checks_in_background = True
            self.assertTrue(self.finding(svc)[0])
            ok, out = self.finding(svc, finding_id="f2", target="TB")
            self.assertTrue(out["checks"].startswith("queued behind"), out)
            checked(svc, "job1", "f1", {"lead": "agrees"})
            svc.record.append("run_finished", {"run_id": "job1", "state": "done", "output": ""}, rc.TOOL)
            deadline = time.time() + 10
            while len(started) < 2 and time.time() < deadline:
                time.sleep(0.05)
            self.assertEqual(len(started), 2)  # no call came
            svc.stop_waiting()
            svc._checks_driver.join(5)
        self.assertFalse(svc._checks_driver.is_alive())

    def test_queue_only_starts_none_and_the_server_starts_it(self):
        svc = self.svc
        started, start = by_hand(svc)
        with mock.patch.object(svc.record, "start_run", side_effect=start):
            svc.queue_only = True
            ok, out = self.finding(svc)
            self.assertTrue(ok, out)
            self.assertTrue(out["checks"].startswith("queued behind"), out)
            self.assertEqual(started, [])
            svc.queue_only = False
            svc.call("read_record", {})
            self.assertEqual(len(started), 1)

    def test_apply_inbox_takes_the_project_and_the_queue_only_flag(self):
        config = os.path.join(self.tmp.name, "config.json")
        with open(config, "w", encoding="utf-8") as fh:
            json.dump({"workspace": os.path.join(self.root, "ws")}, fh)
        seen = []

        def apply(service):
            seen.append(service.queue_only)
            return []

        def run(*more):
            with mock.patch("sys.stdout", new_callable=io.StringIO) as printed, \
                    mock.patch("pertura.limits.limit_this_process"), \
                    mock.patch.object(AnalysisService, "_apply_inbox", apply):
                mcp_server.main(["apply-inbox", config, *more])
            return json.loads(printed.getvalue())
        self.assertIn("not this workspace's last project", run("--project", self.tmp.name)["error"])
        self.assertEqual(seen, [])
        self.assertEqual(run("--project", self.svc.project, "--queue-only"), [])
        self.assertEqual(run(), [])
        self.assertEqual(seen, [True, False])

    def test_apply_inbox_is_set_up_as_the_server_is_before_it_starts_checks(self):
        # dev run 1 (2026-11-20): the end flush's checks ran without the config's NUMPY_MADVISE_HUGEPAGE=0 and took
        # 65 minutes each, against 40 seconds in the session, stalled in memory compaction
        config = os.path.join(self.tmp.name, "config.json")
        with open(config, "w", encoding="utf-8") as fh:
            json.dump({"workspace": os.path.join(self.root, "ws"),
                       "resources": {"environment": {"NUMPY_MADVISE_HUGEPAGE": "0"}}}, fh)
        seen, limited = [], []
        with mock.patch.dict(os.environ), mock.patch("sys.stdout", new_callable=io.StringIO), \
                mock.patch("pertura.limits.limit_this_process",
                           lambda *a: limited.append((a, os.environ.get("NUMPY_MADVISE_HUGEPAGE")))), \
                mock.patch.object(AnalysisService, "_apply_inbox",
                                  lambda svc: seen.append(os.environ.get("NUMPY_MADVISE_HUGEPAGE")) or []):
            os.environ.pop("NUMPY_MADVISE_HUGEPAGE", None)
            mcp_server.main(["apply-inbox", config])
        self.assertEqual(seen, ["0"])  # in the environment the runner's jobs inherit
        self.assertEqual(limited, [((1, asv.KERNEL_THREADS), "0")])  # niced and pinned, once the config is read

    def test_the_records_readers_on_the_command_line_are_niced_and_pinned(self):
        import runpy
        import warnings
        record_dir = os.path.join(self.svc.project, "record")
        with mock.patch("pertura.limits.limit_this_process") as limited, \
                mock.patch.object(sys, "argv", ["recording", "stop-facts", record_dir]), \
                mock.patch("sys.stdout", new_callable=io.StringIO) as printed, warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)  # runpy: pertura.recording is imported already
            runpy.run_module("pertura.recording", run_name="__main__")
        limited.assert_called_once_with(1)
        self.assertEqual(json.loads(printed.getvalue())["checks_running"], [])


class TestTheVerdictInTheRows(Session):

    def ended(self, run_id, finding_id, outcomes, reason="its test"):
        rec = self.svc.record
        rec.append("run_logged", {"run_id": run_id, "job_id": run_id, "what": f"checks of finding {finding_id}",
                                  "code": "c", "inputs": []}, rc.TOOL)
        checked(self.svc, run_id, finding_id, outcomes, reason)
        rec.append("run_finished", {"run_id": run_id, "state": "done", "output": ""}, rc.TOOL)

    def rows(self):
        """The checks rows a read's reply carries, by run."""
        ok, out = self.svc.call("read_record", {})
        self.assertTrue(ok, out)
        return {r["run_id"]: r for r in out.get("ended_since_your_last_call") or []}

    def test_a_checks_row_says_held_did_not_hold_or_not_tested(self):
        svc = self.svc
        started, start = by_hand(svc)
        with mock.patch.object(svc.record, "start_run", side_effect=start):
            for fid, target in (("f1", "TA"), ("f2", "TB"), ("f3", "TC")):
                self.assertTrue(self.finding(svc, finding_id=fid, target=target)[0])
            self.ended("j1", "f1", {"lead": "agrees", "guides_agree": "agree"})
            self.ended("j2", "f2", {"lead": "agrees", "guides_agree": "disagree"})
            self.ended("j3", "f3", {"lead": "not run"}, reason="too few cells")
            rows = self.rows()
            self.assertEqual(rows["j1"]["finding_id"], "f1")
            self.assertEqual(rows["j1"]["verdict"], "held")
            self.assertEqual(rows["j2"]["verdict"], "did not hold: guides_agree: disagree")
            self.assertEqual(rows["j3"]["verdict"], "not tested: too few cells")
            self.assertEqual(self.rows(), {})  # said once
        self.assertEqual(sorted(svc._told["verdicts"]), ["f1", "f2", "f3"])
        with open(os.path.join(svc.project, asv.SAID), encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["told"]["verdicts"]["f2"]["verdict"], "did not hold: guides_agree: disagree")

    def test_a_checks_row_names_the_claims_that_repeat_it(self):
        svc = self.svc
        started, start = by_hand(svc)
        with mock.patch.object(svc.record, "start_run", side_effect=start):
            self.assertTrue(self.finding(svc)[0])
            self.assertTrue(self.finding(svc, finding_id="f2")[0])
            checked(svc, None, "f2", {"lead": "agrees"}, reason="the same claim as f1: its test")
            self.ended("j1", "f1", {"lead": "agrees"})
            self.assertEqual(self.rows()["j1"]["repeats"], ["f2"])

    def test_a_checks_jobs_run_result_says_the_verdict(self):
        svc = self.svc
        started, start = by_hand(svc)
        with mock.patch.object(svc.record, "start_run", side_effect=start):
            self.assertTrue(self.finding(svc)[0])
            self.ended("j1", "f1", {"lead": "other direction"})
            ok, out = svc.call("run_result", {"run_id": "j1"})
            self.assertTrue(ok, out)
            self.assertEqual((out["finding_id"], out["verdict"]), ("f1", "did not hold: lead: other direction"))
            self.assertEqual(self.rows(), {})  # opened: not again as a row
        self.assertEqual(svc._told["verdicts"]["f1"]["verdict"], "did not hold: lead: other direction")

    def test_the_verdict_of_a_finding(self):
        self.assertIsNone(asv.checks_verdict({"target": "TA", "checks": {}}))
        self.assertEqual(asv.checks_verdict({"target": "TA", "checks": {"lead": {"outcome": "its way, not significant"}}}),
                         "held (the lead test: its way, not significant)")
        self.assertEqual(asv.checks_verdict({"target": "TA", "checks": {"lead": {"outcome": "other direction"}}}),
                         "did not hold: lead: other direction")


class TestWhatEachReplySays(Session):

    def test_every_unanswered_doubt_is_to_answer_in_a_reads_reply_too(self):
        svc = self.svc
        started, start = by_hand(svc)
        with mock.patch.object(svc.record, "start_run", side_effect=start):
            self.assertTrue(self.finding(svc)[0])
            svc.raise_doubt("D1", "one guide carries it", "each guide alone", finding_id="f1",
                            opened_by="pertura:skeptic")
            ok, out = svc.call("read_record", {})
            self.assertTrue(ok, out)
            self.assertEqual([d["doubt_id"] for d in out["doubts_to_answer"]["doubts"]], ["D1"])
            self.assertEqual(out["doubts_to_answer"]["doubts"][0]["would_change"], "each guide alone")
            self.assertIn("doubts to answer", out["pending"][asv.MAIN])
            self.assertNotIn("threads_waiting", out)  # a read carries the doubts and the parts, not the rest
            ok, out = svc.call("read_record", {})
            self.assertNotIn("doubts_to_answer", out)  # said once, by the same rule as every item
        self.assertIn("D1", svc._told["doubts"])

class TestStopFacts(Session):

    def test_what_is_open_in_a_record(self):
        svc = self.svc
        started, start = by_hand(svc, job_ids=True)
        resources = svc._resources
        with mock.patch.object(svc.record, "start_run", side_effect=start), \
                mock.patch.object(svc, "_resources", lambda: {**resources(), "checks_at_once": 1}):
            self.assertTrue(self.finding(svc)[0])  # started: job1
            self.assertTrue(self.finding(svc, finding_id="f2", target="TB")[0])  # queued
            ok, out = svc.call("record_finding", {"finding_id": "f3", "statement": "TA lowers G1 in a few cells",
                                                  "kind": "description", "run_ids": []})
            self.assertTrue(ok, out)
        self.assertEqual(len(started), 1)
        svc.raise_doubt("D1", "one guide", "each guide", finding_id="f1", opened_by="pertura:skeptic")
        svc.raise_doubt("D2", "one tumour", "each tumour", finding_id="f1")
        svc.answer_doubt("D2", "holds", "each tumour agrees")
        svc.raise_doubt("D3", "one sample", "each sample", finding_id="f1")
        svc.answer_doubt("D3", "still open", "no time to test it")  # no test closes it so: it stays open
        applied = os.path.join(svc.record.folder, "inbox", "applied")
        os.makedirs(applied, exist_ok=True)
        with open(os.path.join(applied, "t1.outcome.json"), "w", encoding="utf-8") as fh:
            json.dump({"applied": [{"what": "claim f3", "id": "f3", "checks": "not started: no comparison named",
                                    "to_check_it": "Claim f3: targets TA; feature gene G1; direction down"}]}, fh)
        states = {"job1": "running"}
        with mock.patch("pertura.runner.status", lambda job, root: {"state": states[job]}):
            facts = recording.stop_facts(svc.record.folder)
            self.assertEqual(facts["checks_running"], ["f1", "f2"])
            states["job1"] = "done"
            self.assertEqual(recording.stop_facts(svc.record.folder)["checks_running"], ["f2"])
        self.assertEqual(facts["doubts_open"], ["D1", "D3"])
        due = svc._with_lead_threads({})["doubts_to_answer"]["doubts"]
        self.assertEqual([d["doubt_id"] for d in due], ["D1", "D3"])
        self.assertNotIn("still open", svc._with_lead_threads.__globals__["DOUBTS_DO"])
        self.assertEqual(facts["answers_to_update"], [])
        self.assertEqual(facts["described"], [{"finding_id": "f3", "why": "no comparison named",
                                               "to_check_it": "Claim f3: targets TA; feature gene G1; direction down"}])
        json.dumps(facts)  # what the command prints


class TestChoicesTriedByWorkers(Session):
    """A declared choice ('Choice: <what> = <its value>; other: <the alternative>') opens a thread that waits for a
    worker with its brief, and stays open in stop-facts until it ends or is set aside (docs/adr/0032, D9, revived)."""

    TRIES = {"what": "the q threshold of C.genes", "value": "0.05", "other": "0.1"}

    def test_a_choice_waits_for_a_worker_and_is_open_until_its_thread_ends(self):
        svc = self.svc
        first = svc.open_thread(about="does q 0.1 change which genes move?", raised_by="the main agent",
                                tries=self.TRIES)["thread_id"]
        second = svc.open_thread(about="does 3 guides change it?", raised_by="the main agent",
                                 tries={"what": "guides per target", "value": "2", "other": "3"})["thread_id"]
        due = svc._with_lead_threads({})["threads_waiting"]
        self.assertEqual(due["for_workers"], [first, second])
        self.assertTrue(due["briefs"][first]["brief"].startswith(
            f"Thread {first}: rerun the q threshold of C.genes with 0.1 instead of 0.05, and say whether the "
            "conclusion changes."), due["briefs"][first])
        self.assertEqual(due["briefs"][first]["worker"], f"{first}-choice")
        open_now = lambda: recording.stop_facts(svc.record.folder)["choices_open"]  # noqa: E731
        self.assertEqual(open_now(), [{"thread_id": first, "what": "the q threshold of C.genes"},
                                      {"thread_id": second, "what": "guides per target"}])
        svc.thread_status(first, "explained", "the same genes at q 0.1")
        svc.thread_status(second, "set aside", "two guides per target is the design")
        self.assertEqual(open_now(), [])

    def test_a_choice_names_what_its_value_and_the_other(self):
        with self.assertRaises(asv.AnalysisError):
            self.svc.open_thread(about="x", raised_by="the main agent", tries={"what": "the q threshold"})


class TestTheServerNamesItsCode(unittest.TestCase):

    def test_the_server_names_its_code_and_python_beside_its_host(self):
        with mock.patch.dict(os.environ, {"PERTURA_HOST": "analysis-host"}):
            out = asv.with_host({"project": "p"})
        self.assertEqual(out["analysis_host"], "analysis-host")
        self.assertEqual(out["remote_python"], sys.executable)
        self.assertTrue(os.path.isfile(os.path.join(out["remote_code"], "src", "pertura", "recording.py")))
        self.assertNotIn("workspace_config", out)
        with mock.patch.dict(os.environ, {"PERTURA_HOST": ""}):
            self.assertEqual(asv.with_host({"project": "p"}), {"project": "p"})

    def test_and_the_workspace_config_it_was_started_with(self):
        with tempfile.TemporaryDirectory() as tmp:
            svc = AnalysisService({"workspace": tmp, "config_path": "/w/workspace.json"})
            try:
                with mock.patch.dict(os.environ, {"PERTURA_HOST": "analysis-host"}):
                    ok, out = svc.call("pertura_status", {})
            finally:
                svc.close()
        self.assertTrue(ok, out)
        self.assertEqual(out["workspace_config"], "/w/workspace.json")


if __name__ == "__main__":
    unittest.main()
