"""docs/adr/0035, D1 and D2 (items 12 and 17 of docs/build_goal_session6_fixes.md). Development session 6 showed it
(docs/build/development_sessions/session_06_crisprai_sonnet, problem 8): the first look was read for data quality
only; SPI1-a's state shift appeared as a limitation; GATA1-i, the strongest effect, became a finding only when the
developer pointed to it; the pairs, the screen's own design, ran only when asked. Now:
- threads are kept in the record, each with its sentence, what raised it, its evidence and its moves;
- code opens them from the design's structure and from the first look's measured patterns, each against its
  calibration, and none where nothing passes it;
- the open ones come back at every pause, the first time with the workers' default;
- a finding joins its thread, and a thread's status changes with its reason."""
import json
import os
import tempfile
import unittest

import pandas as pd

from test_design_service import make_deposit

from pertura import record as rc
from pertura import threads
from pertura.analysis_service import FIRST_LOOK, PAGE_STATE, AnalysisService


def any_effect(beyond, rows):
    result = {"status": "detected", "summary": "s", "diagnostics": {"calibration": {
        "groups": 10, "groups_95th_percentile": 2.0, "targets_beyond": beyond}}}
    table = pd.DataFrame(rows, columns=["target", "cells", "e_distance", "q"])
    return result, table


class TestTheRecordsThreads(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.rec = rc.Record(os.path.join(self.d, "record"))

    def open(self, tid="t1"):
        self.rec.append("thread_opened", {"thread_id": tid, "about": "GATA1-i's cells differ, 6.1 times the band",
                                          "raised_by": {"run": "r1"}, "strength": 6.1}, rc.TOOL)

    def test_a_finding_joins_its_thread_and_the_thread_is_followed(self):
        self.open()
        self.rec.append("finding_recorded", {"finding_id": "f1", "run_ids": [], "statement": "GATA1-i lowers HBZ",
                                             "thread_id": "t1"}, rc.AGENT)
        th = self.rec.state()["threads"]["t1"]
        self.assertEqual((th["status"], th["findings"]), ("followed", ["f1"]))
        self.rec.append("thread_changed", {"thread_id": "t1", "status": "explained",
                                           "reason": "the globins fall with GATA1 in both guides"}, rc.AGENT)
        th = self.rec.state()["threads"]["t1"]
        self.assertEqual(th["status"], "explained")
        self.assertEqual(th["changes"][-1]["reason"], "the globins fall with GATA1 in both guides")

    def test_what_is_refused(self):
        self.open()
        for kind, payload, words in (
                ("thread_opened", {"thread_id": "t1", "about": "x", "raised_by": "r1"}, "already open"),
                ("thread_opened", {"thread_id": "t2", "about": " ", "raised_by": "r1"}, "a sentence"),
                ("thread_changed", {"thread_id": "t1", "status": "done", "reason": "x"}, "one of"),
                ("thread_changed", {"thread_id": "t1", "status": "set aside", "reason": ""}, "says why"),
                ("thread_changed", {"thread_id": "t9", "status": "open", "reason": "x"}, "no thread t9"),
                ("finding_recorded", {"finding_id": "f1", "run_ids": [], "statement": "s", "thread_id": "t9"},
                 "no thread t9")):
            with self.assertRaises(rc.RecordError) as e:
                self.rec.append(kind, payload, rc.AGENT)
            self.assertIn(words, str(e.exception))


class TestHowThreadsAreShown(unittest.TestCase):
    def test_the_kinds_take_turns_and_a_target_comes_once(self):
        def th(tid, kind, strength, i):
            return {"thread_id": tid, "kind": kind, "strength": strength, "about": tid, "status": "open",
                    "findings": [], "seq": i, "moves": []}
        st = rc.empty()
        st["threads"] = {x["thread_id"]: x for x in [
            th("C.any_effect:GATA1-i", "past the calibration", 5.3, 1),
            th("C.any_effect:SPI1-a", "past the calibration", 3.4, 2),
            th("C.states_and_composition:SPI1-a:S04", "state shift", 52.0, 3),  # a tiny band: not set against 5.3
            th("C.states_and_composition:KLF1-a:S12", "state shift", 24.8, 4),
            th("B.escape:GATA1-i", "responders", 4.1, 5)]}
        shown = threads.open_threads(st)["open"]
        self.assertEqual([t["thread_id"] for t in shown],
                         ["C.any_effect:GATA1-i", "C.states_and_composition:SPI1-a:S04",
                          "C.states_and_composition:KLF1-a:S12"])
        self.assertEqual({a["thread_id"] for a in shown[0]["also"]}, {"B.escape:GATA1-i"})
        self.assertEqual({a["thread_id"] for a in shown[1]["also"]}, {"C.any_effect:SPI1-a"})
        self.assertEqual(threads.strength_order(st)[:2], ["C.any_effect:GATA1-i", "B.escape:GATA1-i"])

    def test_the_first_look_starts_only_what_every_reading_needs(self):
        """Ten modules started by code after the build, Mixscape for 64 minutes, none of them asked for."""
        self.assertEqual(FIRST_LOOK, ("A.what_was_done", "B.guide_calls_and_their_evidence", "B.abundance",
                                      "B.efficacy"))


class TestThreadsInTheReplies(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        deposit = os.path.join(self.tmp.name, "deposit")
        os.makedirs(deposit)
        make_deposit(deposit)
        self.svc = AnalysisService({"workspace": os.path.join(self.tmp.name, "ws")})
        ok, out = self.svc.call("open_project", {"data_folder": deposit})
        assert ok, out
        rec = self.svc.record
        # the first look's states run has ended (a routine run), and a C.any_effect run raised the threads
        rec.append("question_planned", {"question_id": "C.states_and_composition", "letter": "C", "item": "states",
                                        "because": "always"}, rc.PLAN)
        rec.append("run_logged", {"run_id": "r0", "job_id": "r0", "what": "module C.states_and_composition",
                                  "code": "c", "inputs": [], "question_id": "C.states_and_composition"}, rc.TOOL)
        rec.append("run_finished", {"run_id": "r0", "state": "done", "output": "ok"}, rc.TOOL)
        rec.append("question_planned", {"question_id": "C.any_effect", "letter": "C", "item": "any effect",
                                        "because": "always"}, rc.PLAN)
        rec.append("run_logged", {"run_id": "r1", "job_id": "r1", "what": "module C.any_effect", "code": "c",
                                  "inputs": [], "question_id": "C.any_effect"}, rc.TOOL)
        rec.append("run_finished", {"run_id": "r1", "state": "done", "output": "ok"}, rc.TOOL)
        folder = os.path.join(self.svc._jobs_root(), "r1")
        os.makedirs(folder)
        result, table = any_effect(["GATA1-i", "SPI1-a"], [("GATA1-i", 300, 12.2, 0.001), ("SPI1-a", 250, 4.4, 0.002)])
        with open(os.path.join(folder, "result.json"), "w", encoding="utf-8") as fh:
            json.dump(result, fh)
        table.to_csv(os.path.join(folder, "result.tsv"), sep="\t", index=False)

    def tearDown(self):
        self.svc.close()
        self.tmp.cleanup()

    def call(self, tool, **args):
        ok, out = self.svc.call(tool, args)
        self.assertTrue(ok, out)
        return out

    def test_no_thread_is_opened_from_a_run_and_the_agents_own_reach_pertura_status_and_the_page(self):
        """Code opened about twenty threads from single target-by-state signals, and the agent took three of them
        into narrow corners."""
        first = self.call("read_record", what="findings")
        self.assertNotIn("threads", first)  # a reply carries its call's result
        self.assertEqual(self.call("pertura_status")["threads"]["open"], [])  # the ended run opened none
        tid = self.call("open_thread", about="GATA1-i moves the whole profile (distance 12.2)", raised_by="r1",
                        matters="the strongest target of the screen's map",
                        tells_apart="its genes and their programmes")["thread_id"]
        status = self.call("pertura_status")
        self.assertEqual([t["thread_id"] for t in status["threads"]["open"]], [tid])
        self.assertNotIn("menu", status)
        out = self.call("thread_status", thread_id=tid, status="set_aside", reason="its genes first")
        self.assertEqual(out["status"], "set aside")  # read in the schema's spelling
        self.assertNotIn("open_threads", out)
        with open(os.path.join(self.svc.project, PAGE_STATE), encoding="utf-8") as fh:
            page = json.load(fh)
        self.assertEqual(page["threads"]["open"], [])

    def test_the_agent_opens_one_and_a_finding_joins_it(self):
        out = self.call("open_thread", about="KLF1-a and KLF1-i do not mirror each other (r = -0.06)",
                        raised_by="r1", matters="a gene's two modes need not mirror; the user asked about KLF1",
                        tells_apart="each kind of capture's correlation")
        tid = out["thread_id"]
        self.svc.record.append("run_logged", {"run_id": "r2", "what": "a cell", "code": "c", "inputs": []}, rc.TOOL)
        ok, f = self.svc.call("record_finding", {"finding_id": "f1", "statement": "KLF1's modes differ by capture",
                                                 "run_ids": ["r2"], "thread_id": tid, "kind": "description"})
        self.assertTrue(ok, f)
        th = self.svc.record.state()["threads"][tid]
        self.assertEqual((th["status"], th["findings"], th["matters"]),
                         ("followed", ["f1"], "a gene's two modes need not mirror; the user asked about KLF1"))


if __name__ == "__main__":
    unittest.main()
