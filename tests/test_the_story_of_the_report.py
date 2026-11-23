"""The report as one story (the user's decision of 2026-11-02, after session 11's two results side by side). Each
result says the question it answers and the earlier result or finding whose reading raised it; the report orders the
results by those links, opens with the story the agent writes, and lists each break in the chain among what is still
open."""
from reading_a_report import report_text
import os
import tempfile
import unittest

from test_design_service import make_deposit

from pertura import record as rc, results_report
from pertura.analysis_service import AnalysisService

TEXT = "GATA1-i raises SPI1 by 217%, in 4 of 4 guides [finding:f1]."


class TestTheStory(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        deposit = os.path.join(self.tmp.name, "deposit")
        os.makedirs(deposit)
        make_deposit(deposit)
        self.svc = AnalysisService({"workspace": os.path.join(self.tmp.name, "ws")})
        ok, out = self.svc.call("open_project", {"data_folder": deposit})
        assert ok, out
        rec = self.svc.record
        rec.append("run_logged", {"run_id": "r1", "what": "a cell", "code": "c", "inputs": []}, rc.TOOL)
        rec.append("run_finished", {"run_id": "r1", "state": "ok", "output": "GATA1-i raises SPI1 by 217% (4 of 4)"},
                   rc.TOOL)
        rec.append("figure_saved", {"figure_id": "fig1", "run_id": "r1", "paths": {"png": "fig1.png"},
                                    "caption": "SPI1 in GATA1-i's cells against the control cells"}, rc.TOOL)
        self.ok("record_finding", finding_id="f1", statement="GATA1-i raises SPI1 [run:r1].", run_ids=["r1"],
                kind="description")

    def tearDown(self):
        self.svc.close()
        self.tmp.cleanup()

    def ok(self, tool, **args):
        ok, out = self.svc.call(tool, args)
        self.assertTrue(ok, out)
        return out

    def result(self, rid, **links):
        return self.svc.call("report_result", {"result_id": rid, "title": f"result {rid}", "finding_ids": ["f1"],
                                               "figure": "fig1", "text": TEXT, **links})

    def test_results_follow_the_questions_that_raised_them_and_the_story_opens_the_report(self):
        for rid, links in (("r_map", {}), ("r_far", {}), ("r_axis", {"raised_by": "r_map",
                                                                     "answers": "do the two blocks oppose?"}),
                           ("r_lead", {"raised_by": "f1", "answers": "does GATA1 act in every guide?"})):
            ok, out = self.result(rid, **links)
            self.assertTrue(ok, out)
        st = self.svc.record.state()
        self.assertEqual(st["results"]["r_axis"]["answers"], "do the two blocks oppose?")
        order = [r["result_id"] for r in results_report.results_of(st)]
        self.assertEqual(order, ["r_map", "r_axis", "r_far", "r_lead"])  # each after what raised it
        out = self.ok("write_report", story="GATA1 knockdown raises SPI1 [result:r_map], and the two blocks it "
                                            "reveals oppose each other [result:r_axis].")
        text = report_text(out["report"])
        story = text[text.index("## The story"):text.index("## The screen")]
        self.assertIn("the two blocks it reveals oppose each other", story)
        self.assertNotIn("does not say", story)  # each break is an open item of the appendix, not part of the story
        still_open = text[text.index("### Still open"):]
        self.assertIn("Result r_far does not say which earlier result raised its question", still_open)
        self.assertNotIn("Result r_lead does not say", still_open)  # raised by a finding

    def test_a_result_is_raised_by_an_entry_the_record_holds_and_not_by_itself(self):
        self.assertTrue(self.result("r1x")[0])
        ok, out = self.result("r2x", raised_by="r2x")
        self.assertFalse(ok)
        self.assertIn("not by itself", out["error"])
        ok, out = self.result("r3x", raised_by="nothing")
        self.assertFalse(ok)
        self.assertIn("no result, finding or thread nothing", out["error"])

    def test_the_results_samples_sentence_is_the_agents(self):
        """docs/adr/0041, step 2: the agent says what the result's n is; the report shows its words."""
        said = "Two captures of one pool of four mice, no biological replicate told apart; the cells are the observations."
        self.assertTrue(self.result("r_n", samples=said)[0])
        self.assertEqual(self.svc.record.state()["results"]["r_n"]["samples"], said)
        text = report_text(self.ok("write_report")["report"])
        self.assertIn(f"**Samples.** {said}", text)

    def test_no_story_no_section(self):
        self.assertTrue(self.result("r1x")[0])
        text = report_text(self.ok("write_report")["report"])
        self.assertNotIn("## The story", text)


if __name__ == "__main__":
    unittest.main()
