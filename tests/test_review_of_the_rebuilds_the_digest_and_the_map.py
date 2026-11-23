"""The independent review of items 2, 5, 25 and 27 (docs/build_goal_session6_fixes.md): what it found, fixed.
- a rebuild is noted when the next run starts or the status is read, not only when the build's reply is read;
- a record from before builds had IDs compares with the build kept before the rebuild; its runs read that build;
- what changed since the build a run read counts every changed rebuild after it;
- a run names a thread the record holds; a thread one level up rests on one finding or more (session 11's
  problem 11: the rule of two refused the reader three times);
- a finding the user dropped leaves the map; one on the previous screen is drawn as such;
- a result split by a column is given to the next block under an ID of its own;
- the user is back when ten minutes have passed since the agent's turn ended."""
import json
import os
import tempfile
import unittest
from unittest import mock

import pandas as pd

from test_a_rebuilt_screen_marks_what_it_supersedes import cells
from test_design_service import make_deposit

from pertura import blocks, record as rc, screen_map
from pertura.analysis_service import AnalysisService


class Service(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        deposit = os.path.join(self.tmp.name, "deposit")
        os.makedirs(deposit)
        make_deposit(deposit)
        self.svc = AnalysisService({"workspace": os.path.join(self.tmp.name, "ws")})
        ok, out = self.svc.call("open_project", {"data_folder": deposit})
        assert ok, out
        self.sd = os.path.join(self.tmp.name, "screen_object")
        os.makedirs(self.sd)
        self.screen = mock.patch.object(self.svc, "_screen_dir", return_value=self.sd)
        self.screen.start()

    def tearDown(self):
        self.screen.stop()
        self.svc.close()
        self.tmp.cleanup()

    def build(self, bid, labels, folder=None):
        folder = folder or self.sd
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, "screen.json"), "w", encoding="utf-8") as fh:
            json.dump({"build_id": bid}, fh)
        cells(labels).to_csv(os.path.join(folder, "cells.tsv.gz"), sep="\t", index=False)

    def log_run(self, rid, build=None):
        rec = self.svc.record
        rec.append("run_logged", {"run_id": rid, "what": "a cell", "code": "c", "inputs": [],
                                  **({"build": build} if build else {})}, rc.TOOL)
        rec.append("run_finished", {"run_id": rid, "state": "ok", "output": "o"}, rc.TOOL)


class TestARebuild(Service):
    def test_a_record_from_before_the_builds_compares_with_the_build_kept_before_it(self):
        self.log_run("r1")  # a run from before builds had IDs
        self.build("old", ["A", "B", "control"], os.path.join(self.svc._builds_dir(), "old"))
        self.build("b2", ["A", "A", "control"])
        told = self.svc._note_build()["rebuilt"]
        self.assertEqual(told["runs_on_the_previous_screen"], 1)
        st = self.svc.record.state()
        self.assertEqual(rc.first_build(st), "old")
        self.assertTrue(rc.on_previous_screen(st, st["runs"]["r1"]))

    def test_without_a_kept_build_its_runs_stand_and_nothing_is_guessed(self):
        self.log_run("r1")
        self.build("b1", ["A", "B", "control"])
        self.assertEqual(self.svc._note_build(), {})
        st = self.svc.record.state()
        self.assertFalse(rc.on_previous_screen(st, st["runs"]["r1"]))

    def test_the_rebuilds_reply_says_what_this_rebuild_changed(self):
        self.build("b1", ["A", "B", "control", "control"])
        self.svc._note_build()
        self.svc._keep_build()
        self.build("b2", ["A", "B", "control"])  # a cell left
        self.svc._note_build()
        self.svc._keep_build()
        self.build("b3", ["A", "B", "control", "control", "A"])  # two came
        said = self.svc._note_build()["rebuilt"]["what_changed"]
        self.assertIn("0 cells left and 2 came", said)

    def test_a_summary_that_does_not_read_stops_no_rebuild(self):
        with open(os.path.join(self.sd, "screen.json"), "w", encoding="utf-8") as fh:
            fh.write('{"build_id": "b1", "cel')  # half written, as an earlier release could leave it
        with mock.patch.object(self.svc.design, "call", return_value=(False, {"error": "not now"})):
            ok, out = self.svc.call("build_screen_object", {})
        self.assertEqual((ok, out), (False, {"error": "not now"}))

    def test_the_status_notes_a_rebuild_that_ended_unread(self):
        self.build("b1", ["A", "B", "control"])
        self.svc._note_build()
        self.svc._keep_build()
        self.build("b2", ["A", "A", "control"])
        self.svc.call("pertura_status", {})
        self.assertEqual(self.svc.record.state()["build"], "b2")

    def test_what_changed_since_a_build_counts_every_changed_rebuild_after_it(self):
        with tempfile.TemporaryDirectory() as d:
            r = rc.Record(os.path.join(d, "rec"))
            r.append("screen_rebuilt", {"build_id": "b1", "previous": None, "changed": False}, rc.TOOL)
            for bid, prev, n, t in (("b2", "b1", 3, "A"), ("b3", "b2", 4, "B")):
                r.append("screen_rebuilt", {"build_id": bid, "previous": prev, "changed": True,
                                            "what": {"cells_changed": n, "targets": [t]}}, rc.TOOL)
            st = r.state()
            self.assertIn("changed 7 cells", rc.what_changed(st, "b1"))
            self.assertIn("targets A, B", rc.what_changed(st, "b1"))
            self.assertIn("changed 4 cells", rc.what_changed(st, "b2"))
            r.append("screen_rebuilt", {"build_id": "b4", "previous": "b3", "changed": True,
                                        "what": {"unknown": True}}, rc.TOOL)
            self.assertIn("not known", rc.what_changed(r.state(), "b3"))


class TestThreads(Service):
    def test_a_run_names_a_thread_the_record_holds(self):
        ok, out = self.svc.call("run_code", {"code": "1", "what": "a cell", "thread_id": "no-such-thread"})
        self.assertFalse(ok)
        self.assertIn("no thread", out["error"])

    def test_a_thread_one_level_up_rests_on_one_finding_or_more(self):
        self.log_run("r1")
        ok, out = self.svc.call("record_finding", {"finding_id": "f1", "statement": "s [run:r1].", "run_ids": ["r1"],
                                                   "kind": "description"})
        self.assertTrue(ok, out)
        ok, out = self.svc.call("open_thread", {"about": "what it raises", "raised_by": "f1", "joins": ["f1"]})
        self.assertTrue(ok, out)
        ok, out = self.svc.call("open_thread", {"about": "a second", "raised_by": "f1", "joins": ["f9"]})
        self.assertFalse(ok)
        self.assertIn("no finding f9", out["error"])


class TestTheMap(unittest.TestCase):
    def test_a_dropped_finding_leaves_it_and_one_on_the_previous_screen_is_drawn_as_such(self):
        with tempfile.TemporaryDirectory() as d:
            r = rc.Record(os.path.join(d, "rec"))
            r.append("screen_rebuilt", {"build_id": "b1", "previous": None, "changed": False}, rc.TOOL)
            for rid in ("r1", "r2"):
                r.append("run_logged", {"run_id": rid, "what": "a cell", "code": "c", "inputs": [], "build": "b1"},
                         rc.TOOL)
                r.append("run_finished", {"run_id": rid, "state": "ok", "output": "o"}, rc.TOOL)
            r.append("finding_recorded", {"finding_id": "f1", "statement": "s", "run_ids": ["r1"],
                                          "relates": [{"from": "GATA1", "to": "SPI1", "sign": "-"}]}, rc.AGENT)
            r.append("finding_recorded", {"finding_id": "f2", "statement": "s", "run_ids": ["r2"],
                                          "relates": [{"from": "MYB", "to": "KIT", "sign": "+"}]}, rc.AGENT)
            r.append("reply_recorded", {"kind": "drop", "words": "leave it out", "finding_id": "f2"}, rc.AGENT)
            m = screen_map.build(r.state())
            self.assertEqual([(x["from"], x["status"]) for x in m["relations"]], [("GATA1", "seen")])
            r.append("reply_recorded", {"kind": "keep", "words": "keep it after all", "finding_id": "f2"}, rc.AGENT)
            self.assertIn("MYB", [x["from"] for x in screen_map.build(r.state())["relations"]])  # the latest word
            r.append("reply_recorded", {"kind": "drop", "words": "no, drop it", "finding_id": "f2"}, rc.AGENT)
            r.append("screen_rebuilt", {"build_id": "b2", "previous": "b1", "changed": True,
                                        "what": {"cells_changed": 2}}, rc.TOOL)
            m = screen_map.build(r.state())
            self.assertEqual(m["relations"][0]["status"], "on the previous screen")


class TestASplitResult(unittest.TestCase):
    def test_is_given_to_the_next_block_under_an_id_of_its_own(self):
        res = blocks.Result("scores", {}, pd.DataFrame({"target": ["A"], "score": ["P1"], "effect": [1.0]}), 1, [],
                            "all")
        given = blocks._given_of(res)
        self.assertTrue(given and given.startswith("scores-split-"))
        self.assertEqual(blocks._given_of(res), given)  # the same result, the same ID
        self.assertIs(blocks._TABLES[given], res.table)


if __name__ == "__main__":
    unittest.main()
