"""The review loop held the session on its first result (2026-11-07): of the main agent's calls after the first
result, 43-50% were in it, and r1 reached 22-28 versions. Each answered doubt named a run, the run's finding joined
the result, and the skeptic was due again for it.
- P3: the skeptic reads each result once; a finding that joins it later is not due again, and a doubt whose answer
  would not change the result is stated as its limit, with no run;
- P5: the report is written once, at the end, and its reply neither carries its text nor asks for it again."""
import os
import tempfile
import unittest

from test_design_service import make_deposit

from pertura import record as rc
from pertura.analysis_service import AnalysisService


class TestEachResultIsReadOnce(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        deposit = os.path.join(self.tmp.name, "deposit")
        os.makedirs(deposit)
        make_deposit(deposit)
        self.svc = AnalysisService({"workspace": os.path.join(self.tmp.name, "ws")})
        ok, out = self.svc.call("open_project", {"data_folder": deposit})
        assert ok, out
        self.rec = self.svc.record
        for rid in ("r1", "r2", "r3"):
            self.rec.append("run_logged", {"run_id": rid, "what": "a cell", "code": "c", "inputs": []}, rc.TOOL)
            self.rec.append("run_finished", {"run_id": rid, "state": "ok", "output": "o"}, rc.TOOL)
        for f, r in (("f1", "r1"), ("f2", "r2"), ("f3", "r3")):
            self.rec.append("figure_saved", {"figure_id": f"fig_{r}", "run_id": r, "paths": {"png": f"{r}.png"},
                                             "caption": "c"}, rc.TOOL)
            self.ok("record_finding", finding_id=f, statement=f"{f} [run:{r}].", run_ids=[r], kind="description")
        # the skeptic reads the results once they are about to go to the user (docs/adr/0047, A6)
        self.rec.append("reply_recorded", {"kind": "enough", "words": "that is enough"}, rc.USER)

    def tearDown(self):
        self.svc.close()
        self.tmp.cleanup()

    def ok(self, tool, **args):
        ok, out = self.svc.call(tool, args)
        self.assertTrue(ok, out)
        return out

    def due(self):
        return sorted(i["id"] for i in rc.skeptic_due(self.rec.state()))

    def report(self, rid, findings):  # its figure from its first finding's run (f1 is r1's, and so on)
        return self.ok("report_result", result_id=rid, title=f"{rid}.", finding_ids=findings,
                       figure=f"fig_r{findings[0][1:]}", text=" ".join(f"[finding:{f}]." for f in findings))

    def test_a_finding_that_joins_a_read_result_is_not_due_again(self):
        self.report("R1", ["f1"])
        self.assertEqual(self.due(), ["f1"])
        self.ok("record_skeptic_check", finding_ids=["f1"], doubt_ids=[])
        self.report("R1", ["f1", "f2"])  # a doubt's finding joined it: the loop of 2026-11-07
        self.assertEqual(self.due(), [])
        self.report("R2", ["f3"])  # a new result is read once
        self.assertEqual(self.due(), ["f3"])

    def test_a_result_of_findings_already_read_is_read(self):
        self.report("R1", ["f1", "f2"])
        self.ok("record_skeptic_check", finding_ids=["f1", "f2"], doubt_ids=[])
        self.report("R2", ["f2"])
        self.report("R2", ["f2", "f3"])
        self.assertEqual(self.due(), [])

    def test_a_result_read_in_part_stays_due_for_the_rest(self):
        self.report("R1", ["f1", "f2"])
        self.ok("record_skeptic_check", finding_ids=["f1"], doubt_ids=[])
        self.assertEqual(self.due(), ["f2"])
        self.report("R1", ["f1", "f2", "f3"])
        self.assertEqual(self.due(), ["f2", "f3"])  # not read yet: its findings now are what it reads


class TestTheReportIsWrittenOnce(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        deposit = os.path.join(self.tmp.name, "deposit")
        os.makedirs(deposit)
        make_deposit(deposit)
        self.svc = AnalysisService({"workspace": os.path.join(self.tmp.name, "ws")})
        ok, out = self.svc.call("open_project", {"data_folder": deposit})
        assert ok, out

    def tearDown(self):
        self.svc.close()
        self.tmp.cleanup()

    def test_its_reply_says_where_it_is_and_asks_for_no_rewrite(self):
        ok, out = self.svc.call("write_report", {})
        self.assertTrue(ok, out)
        self.assertTrue(out["report"].endswith("report.md"))
        self.assertNotIn("next", out)  # its text is on the user's page
        self.assertNotIn("again", " ".join(str(v) for v in out.values()))
        self.assertIn("code keeps the report written", out["enough"])

    def test_code_keeps_it_written_as_results_are_proposed(self):
        """Two of the four runs with follow-ups ended at their caps, where a report written once at the end would
        not stand: code writes it again as each result is proposed or decided."""
        rec = self.svc.record
        rec.append("run_logged", {"run_id": "r1", "what": "a cell", "code": "c", "inputs": []}, rc.TOOL)
        rec.append("run_finished", {"run_id": "r1", "state": "ok", "output": "o"}, rc.TOOL)
        rec.append("figure_saved", {"figure_id": "fig_r1", "run_id": "r1", "paths": {"png": "r1.png"},
                                    "caption": "c"}, rc.TOOL)
        ok, out = self.svc.call("record_finding", {"finding_id": "f1", "statement": "one [run:r1].",
                                                   "run_ids": ["r1"], "kind": "description"})
        self.assertTrue(ok, out)
        report = os.path.join(self.svc.project, "report", "report.md")
        self.assertFalse(os.path.exists(report))
        ok, out = self.svc.call("report_result", {"result_id": "R1", "title": "GENEA lowers GENEB.",
                                                  "finding_ids": ["f1"], "figure": "fig_r1",
                                                  "text": "It does [finding:f1]."})
        self.assertTrue(ok, out)
        with open(report, encoding="utf-8") as fh:
            self.assertIn("GENEA lowers GENEB", fh.read())


if __name__ == "__main__":
    unittest.main()
