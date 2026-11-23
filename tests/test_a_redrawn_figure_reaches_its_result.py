"""A figure redrawn after its review reaches its result. On the attended replay of 2026-11-12 the main agent redrew
three figures in its notebook and attached none: the refusal it got (a figure ID, checked by the record) did not say
the way through, which the refusal for a run ID says (add the drawing run to the finding it supports), and a figure ID
given without its folder was only "not found"."""
import os
import tempfile
import unittest

from test_design_service import make_deposit

from pertura import record as rc
from pertura.analysis_service import AnalysisService


class TestARedrawnFigure(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        deposit = os.path.join(self.tmp.name, "deposit")
        os.makedirs(deposit)
        make_deposit(deposit)
        self.svc = AnalysisService({"workspace": os.path.join(self.tmp.name, "ws")})
        ok, out = self.svc.call("open_project", {"data_folder": deposit})
        assert ok, out
        self.rec = self.svc.record
        for rid in ("r1", "r9"):  # r9: a notebook cell that redraws from the screen object, reading no run of f1
            self.rec.append("run_logged", {"run_id": rid, "what": "a cell", "code": "c", "inputs": []}, rc.TOOL)
            self.rec.append("run_finished", {"run_id": rid, "state": "ok", "output": "o"}, rc.TOOL)
        self.rec.append("figure_saved", {"figure_id": "fig1", "run_id": "r1", "paths": {"png": "f.png"},
                                         "caption": "c"}, rc.TOOL)
        self.rec.append("figure_saved", {"figure_id": "figfix/fig2", "run_id": "r9", "paths": {"png": "g.png"},
                                         "caption": "c, redrawn"}, rc.TOOL)
        self.call("record_finding", finding_id="f1", statement="A1 lowers G1 [run:r1].", run_ids=["r1"],
                  kind="description")
        self.call("report_result", result_id="res1", title="A1 lowers G1", finding_ids=["f1"], figure="fig1",
                  text="A1 lowers G1 [finding:f1].")

    def tearDown(self):
        self.svc.close()
        self.tmp.cleanup()

    def call(self, tool, **args):
        ok, out = self.svc.call(tool, args)
        self.assertTrue(ok, out)
        return out

    def refused(self, tool, **args):
        ok, out = self.svc.call(tool, args)
        self.assertFalse(ok, out)
        return out["error"]

    def test_the_refusal_says_the_way_through_and_the_way_works(self):
        said = self.refused("report_result", result_id="res1", figure="figfix/fig2")
        self.assertIn("add r9 to the finding it supports first (reword_finding with run_ids)", said)
        self.call("reword_finding", finding_id="f1", statement="A1 lowers G1 [run:r1] [run:r9].",
                  reason="the redrawn figure's run", run_ids=["r1", "r9"])
        self.call("report_result", result_id="res1", figure="figfix/fig2")
        self.assertEqual(self.rec.state()["results"]["res1"]["figure"], "figfix/fig2")

    def test_a_figure_without_its_folder_names_the_one_the_record_holds(self):
        self.assertIn("the record holds figfix/fig2", self.refused("report_result", result_id="res1", figure="fig2"))


if __name__ == "__main__":
    unittest.main()
