"""The unattended mode (agreed with the user on 2026-11-01, built after session 11). The user's part is the design and
their question at the start; then they are away. Session 11's owner said "Go ahead with what you propose", and that
was recorded as the user confirming a result (its problem 3). Now:
- the workspace configuration's 'unattended' key turns it on, and open_project's reply says so;
- unattended, the analysis keeps or drops a result with its reason, recorded as decided for the user; otherwise only
  the user's relayed words do;
- unattended, note_reply's enough is the analysis's decision to end, with its reason;
- the report lists what was decided for the user, and says who ended the analysis."""
from reading_a_report import report_text
import os
import tempfile
import unittest

from test_design_service import make_deposit

from pertura import record as rc
from pertura.analysis_service import AnalysisService

TEXT = "GATA1-i raises SPI1 by 217%, in 4 of 4 guides [finding:f1]."


class Service(unittest.TestCase):
    unattended = True

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        deposit = os.path.join(self.tmp.name, "deposit")
        os.makedirs(deposit)
        make_deposit(deposit)
        self.svc = AnalysisService({"workspace": os.path.join(self.tmp.name, "ws"), "unattended": self.unattended})
        ok, self.opened = self.svc.call("open_project", {"data_folder": deposit})
        assert ok, self.opened
        rec = self.svc.record
        rec.append("run_logged", {"run_id": "r1", "what": "a cell", "code": "c", "inputs": []}, rc.TOOL)
        rec.append("run_finished", {"run_id": "r1", "state": "ok", "output": "GATA1-i raises SPI1 by 217% (4 of 4)"},
                   rc.TOOL)
        rec.append("figure_saved", {"figure_id": "fig1", "run_id": "r1", "paths": {"png": "fig1.png"},
                                    "caption": "SPI1 in GATA1-i's cells against the control cells"}, rc.TOOL)
        self.ok("record_finding", finding_id="f1", statement="GATA1-i raises SPI1 [run:r1].", run_ids=["r1"],
                kind="description")
        self.first = self.ok("report_result", result_id="res1", title="GATA1 knockdown raises SPI1",
                             finding_ids=["f1"], figure="fig1", text=TEXT)

    def tearDown(self):
        self.svc.close()
        self.tmp.cleanup()

    def ok(self, tool, **args):
        ok, out = self.svc.call(tool, args)
        self.assertTrue(ok, out)
        return out

    def refused(self, tool, **args):
        ok, out = self.svc.call(tool, args)
        self.assertFalse(ok, out)
        return out["error"]


class TestUnattended(Service):
    def test_open_project_says_the_user_is_away(self):
        self.assertIn("unattended", self.opened["unattended"])

    def test_the_first_result_brings_the_charts_papers_use_and_asks_for_a_decision(self):
        ok, out = self.svc.call("report_result", {"result_id": "res2", "title": "a second", "finding_ids": ["f1"],
                                                  "figure": "fig1", "text": TEXT})
        self.assertTrue(ok, out)
        self.assertNotIn("figure_forms_of_published_screens", out)  # only with the first result
        self.assertIn("your reason", out["next"])

    def test_the_analysis_decides_a_result_with_its_reason_and_the_report_lists_it(self):
        self.assertIn("reason", self.refused("report_result", result_id="res1", decision="confirmed"))
        out = self.ok("report_result", result_id="res1", decision="confirmed",
                      reason="its guides agree and it holds in each sample")
        self.assertTrue(out["decided_for_the_user"])
        r = self.svc.record.state()["results"]["res1"]
        self.assertEqual((r["decision"], r["by_analysis"]), ("confirmed", True))
        self.ok("open_thread", about="Does it hold in each sample?", raised_by="res1", next_of="res1",
                thread_id="tn")  # its next question
        self.ok("thread_status", thread_id="tn", status="out of data", reason="one sample", run_ids=["r1"])
        # unattended, the analysis's enough is taken when the record holds nothing open (the research lead's
        # complete, which took it before, is retired, 2026-11-20)
        self.svc.raise_doubt("D9", "one sample", "a second sample", finding_id="f1")
        self.assertIn("doubts open: D9", self.refused("note_reply", kind="enough", words="the threads are run"))
        self.svc.answer_doubt("D9", "stated as a limit", "one sample in the deposit", limit="design")
        self.ok("note_reply", kind="enough", words="the open threads are run or set aside")
        self.assertTrue(self.svc.record.state()["enough"]["by_analysis"])
        text = report_text(self.ok("write_report")["report"])
        decided = text[text.index("## Decided for you"):text.index("## Open questions")]
        self.assertIn("its guides agree and it holds in each sample", decided)
        self.assertIn("The analysis ended", decided)
        self.assertIn("The analysis ended (unattended)", text)
        self.assertIn("decided for the user (unattended)", text)


    def test_a_new_version_keeps_the_analysiss_confirmation_and_never_a_drop(self):
        """GSE267740 (d3a7dcc): two results went through eight versions, each confirmed again by the agent."""
        self.ok("report_result", result_id="res1", decision="confirmed", reason="it holds in each sample")
        out = self.ok("report_result", result_id="res1", text=TEXT.replace("raises", "raises, in each sample,", 1))
        self.assertIn("confirmed in version 1", out["kept"])
        r = self.svc.record.state()["results"]["res1"]
        self.assertEqual((r["version"], r["decision"], r["by_analysis"]), (2, "confirmed", True))
        self.ok("report_result", result_id="res1", decision="dropped", reason="the second sample disagrees")
        out = self.ok("report_result", result_id="res1", text=TEXT)
        self.assertNotIn("kept", out)
        self.assertIsNone(self.svc.record.state()["results"]["res1"]["decision"])  # revived: decided again


    def test_a_result_on_a_run_with_no_figure_has_no_legend_and_is_listed_as_left(self):
        """B1's review (d2, 2026-11-13): a result proposed on a notebook run that saved no figure, and no planned
        figure drawn for it, got a legend for a figure that was not there."""
        rec = self.svc.record
        rec.append("run_logged", {"run_id": "r2", "what": "a cell", "code": "c", "inputs": []}, rc.TOOL)
        rec.append("run_finished", {"run_id": "r2", "state": "ok", "output": "GATA1-i lowers CD71 by 30% (4 of 4)"},
                   rc.TOOL)
        self.ok("record_finding", finding_id="f2", statement="GATA1-i lowers CD71 [run:r2].", run_ids=["r2"],
                kind="description")
        self.ok("report_result", result_id="res2", title="GATA1 knockdown lowers CD71", finding_ids=["f2"],
                figure="r2", text="GATA1-i lowers CD71 by 30%, in 4 of 4 guides [finding:f2].")
        text = report_text(self.ok("write_report")["report"])
        self.assertIn("No figure was drawn for this result [result res2]", text)
        self.assertIn("Result res2: no figure was drawn for it", text[text.index("### Still open"):])
        self.assertIn("**Figure 1.** GATA1 knockdown raises SPI1", text)  # a saved figure keeps its legend


class TestAttended(Service):
    unattended = False

    def test_only_the_users_words_decide_and_no_section_is_written(self):
        self.assertNotIn("unattended", self.opened)
        self.assertIn("| A score against the control (C, G) |", self.first["figure_forms_of_published_screens"])
        self.assertIn("show the user", self.first["next"])
        self.assertIn("relay their words", self.refused("report_result", result_id="res1", decision="confirmed",
                                                         reason="I think it holds"))
        self.ok("report_result", result_id="res1", decision="confirmed", user_words="yes, keep it")
        self.ok("note_reply", kind="enough", words="that will do")
        self.assertFalse(self.svc.record.state()["enough"]["by_analysis"])
        text = report_text(self.ok("write_report")["report"])
        self.assertNotIn("## Decided for you", text)
        self.assertIn("The user ended the analysis", text)


if __name__ == "__main__":
    unittest.main()
