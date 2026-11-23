"""Item 2 of docs/build_goal_session6_fixes.md: a rebuilt screen marks what it supersedes. Development session 6
rebuilt its screen object after the design changed, and the runs, figures and findings of the earlier build stayed
in every view as if they read the new one. Now:
- each build has an ID in screen.json, and each run records the build it read;
- a rebuild is compared with the build before it, cell by cell (label, unit, condition): when nothing changed,
  nothing is marked; else the runs on the earlier build are on the previous screen, with what changed, the findings
  resting on them are listed, and the first look reruns on the new build;
- nothing earlier is deleted: the earlier build's summary and cells are kept under its ID."""
import json
import os
import tempfile
import unittest
from unittest import mock

import pandas as pd

from test_design_service import make_deposit

from pertura import record as rc
from pertura import screen_object
from pertura.analysis_service import AnalysisService


def cells(labels, units=None):
    n = len(labels)
    return pd.DataFrame({"barcode": [f"c{i}" for i in range(n)], "label": labels,
                         "unit": units or ["u1"] * n, "condition": [""] * n})


class TestWhatARebuildChanged(unittest.TestCase):
    def test_a_target_a_control_and_nothing(self):
        before = cells(["A", "A", "B", "control", "control"])
        self.assertFalse(screen_object.cells_changed(before, before.copy())["changed"])
        moved = screen_object.cells_changed(before, cells(["A", "B", "B", "control", "control"]))
        self.assertEqual((moved["changed"], moved["targets"], moved["controls"], moved["cells_changed"]),
                         (True, ["A", "B"], False, 1))
        ctrl = screen_object.cells_changed(before, cells(["A", "A", "B", "control", "none"]))
        self.assertEqual((ctrl["controls"], ctrl["targets"]), (True, ["A", "B"]))  # every target's reference moved
        unit = screen_object.cells_changed(before, cells(["A", "A", "B", "control", "control"],
                                                         ["u1", "u2", "u1", "u1", "u1"]))
        self.assertEqual(unit["targets"], ["A"])
        self.assertEqual(screen_object.build_id({"design_sha256": "d", "cells_sha256": "c", "built": "t"}),
                         screen_object.build_id({"design_sha256": "d", "cells_sha256": "c", "built": "t"}))


class TestTheRecord(unittest.TestCase):
    def test_runs_on_a_changed_build_are_on_the_previous_screen_and_an_unchanged_one_marks_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            r = rc.Record(os.path.join(d, "rec"))
            r.append("screen_rebuilt", {"build_id": "b1", "previous": None, "changed": False}, rc.TOOL)
            r.append("run_logged", {"run_id": "r1", "what": "a cell", "code": "c", "inputs": [], "build": "b1"},
                     rc.TOOL)
            r.append("run_finished", {"run_id": "r1", "state": "ok", "output": "o"}, rc.TOOL)
            r.append("finding_recorded", {"finding_id": "f1", "statement": "s", "run_ids": ["r1"]}, rc.AGENT)
            r.append("screen_rebuilt", {"build_id": "b2", "previous": "b1", "changed": False}, rc.TOOL)
            st = r.state()
            self.assertEqual(rc.current_builds(st), {"b1", "b2"})
            self.assertFalse(rc.on_previous_screen(st, st["runs"]["r1"]))
            r.append("screen_rebuilt", {"build_id": "b3", "previous": "b2", "changed": True,
                                        "what": {"cells_changed": 4, "targets": ["A"], "controls": False}}, rc.TOOL)
            st = r.state()
            self.assertTrue(rc.on_previous_screen(st, st["runs"]["r1"]))
            self.assertEqual(rc.previous_screen_findings(st), {"f1": ["r1"]})
            self.assertIn("4 cells", rc.what_changed(st))
            item = next(i for i in r.critic() if i["kind"] == "finding on the previous screen")
            self.assertEqual(item["id"], "f1")
            self.assertIn("targets A", item["what"])


class TestTheService(unittest.TestCase):
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

    def build(self, bid, labels):
        with open(os.path.join(self.sd, "screen.json"), "w", encoding="utf-8") as fh:
            json.dump({"build_id": bid}, fh)
        cells(labels).to_csv(os.path.join(self.sd, "cells.tsv.gz"), sep="\t", index=False)

    def test_a_rebuild_marks_the_runs_before_it_and_keeps_the_earlier_build(self):
        self.build("b1", ["A", "B", "control"])
        self.assertEqual(self.svc._note_build(), {})  # the first build
        rec = self.svc.record
        rec.append("run_logged", {"run_id": "r1", "what": "module B.efficacy", "code": "c", "inputs": [],
                                  "question_id": "B.efficacy", "build": self.svc._build_id()}, rc.TOOL)
        rec.append("run_finished", {"run_id": "r1", "state": "done", "output": "o"}, rc.TOOL)
        self.svc.call("record_finding", {"finding_id": "f1", "statement": "s", "run_ids": ["r1"],
                                         "kind": "description"})
        self.svc._keep_build()  # before the rebuild writes over it
        self.build("b2", ["A", "B", "control"])
        self.assertIn("every earlier run stands", self.svc._note_build()["rebuilt"])
        self.svc._keep_build()
        self.build("b3", ["A", "A", "control"])
        told = self.svc._note_build()["rebuilt"]
        self.assertEqual(told["runs_on_the_previous_screen"], 1)
        self.assertEqual(told["findings_resting_on_them"], ["f1"])
        self.assertTrue(os.path.exists(os.path.join(self.svc._builds_dir(), "b1", "cells.tsv.gz")))
        st = rec.state()
        self.assertEqual(self.svc.read_record("findings", ["f1"])["findings"]["f1"]["screen"][:8], "previous")
        with mock.patch.object(self.svc, "_routine_ids", return_value=["B.efficacy"]), \
                mock.patch.object(self.svc, "run_module", return_value={"run_id": "r2"}) as again:
            rec.append("question_planned", {"question_id": "B.efficacy", "letter": "B", "item": "efficacy",
                                            "because": "always"}, rc.PLAN)
            started = self.svc._start_routine()
        self.assertEqual(started["started"], {"B.efficacy": "r2"})  # the first look reruns on the new build
        again.assert_called_once()
        self.assertTrue(rc.on_previous_screen(st, st["runs"]["r1"]))


if __name__ == "__main__":
    unittest.main()
