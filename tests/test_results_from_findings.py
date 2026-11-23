"""Item 9 of docs/build_goal_session6_fixes.md: the report's results come from findings, not dossiers. Session 6's report
took 53 record calls, 11 of which failed (problem 10), and write_report's reply was 191,816 characters. Now:
- one report_result per result: its title, the findings it rests on, its figure from their runs and its text; the
  same ID again is its next version; the user confirms or drops it in chat, relayed with their words;
- the text's numbers are looked for in its findings' runs, and those not found are listed, never refused;
- the report: each result with its figure, legend, text, findings with their checks column (what ran in code, what
  was answered by hand, or why there are none) and the prior knowledge beside them; the open questions in a few
  lines; the doubts, the open items and the record's entries in an appendix; every sentence linked;
- write_report's reply is a summary, not the report;
- the skeptic and the figure review are due for what a result rests on, and the digest lists the results to
  confirm."""
from reading_a_report import report_text
import os
import tempfile
import unittest

from test_design_service import make_deposit

from pertura import digest, record as rc, results_report, text_numbers
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
        rec = self.svc.record
        for rid, output in (("r1", "GATA1-i raises SPI1 by 217% (4 of 4 guides); r = 0.49"), ("r2", "other")):
            rec.append("run_logged", {"run_id": rid, "what": "a cell", "code": "c", "inputs": []}, rc.TOOL)
            rec.append("run_finished", {"run_id": rid, "state": "ok", "output": output}, rc.TOOL)
        rec.append("figure_saved", {"figure_id": "fig1", "run_id": "r1", "paths": {"png": "fig1.png"},
                                    "caption": "SPI1 in GATA1-i's cells against the control cells"}, rc.TOOL)
        self.ok("record_finding", finding_id="f1", statement="GATA1-i raises SPI1 [run:r1].", run_ids=["r1"],
                prior={"text": "GATA1 and PU.1 repress each other", "source": "PMID 10086723"}, kind="description")
        self.ok("record_finding", finding_id="f2", statement="A second finding [run:r2].", run_ids=["r2"],
                kind="description")

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


TEXT = ("GATA1-i raises SPI1 by 217%, in 4 of 4 guides [finding:f1]. The two profiles correlate, r = 0.49 [run:r1]. "
        "Figure 2 shows it, with 1.75 as the band [run:r1].")


class TestAResult(Service):
    def test_its_figure_comes_from_its_findings_runs_and_a_new_version_keeps_its_id(self):
        refused = self.refused("report_result", result_id="res1", title="t", finding_ids=["f1"], figure="r2")
        self.assertIn("drawn from the runs of its findings", refused)
        self.assertIn("reword_finding with run_ids", refused)  # a figure redrawn from a new run (ADR 0047, B9)
        self.assertIn("no figure or run", self.refused("report_result", result_id="res1", title="t",
                                                       finding_ids=["f1"], figure="nothing"))
        out = self.ok("report_result", result_id="res1", title="GATA1 knockdown raises SPI1", finding_ids=["f1"],
                      figure="fig1", text=TEXT)
        self.assertEqual(out["version"], 1)
        self.assertNotIn("text", out)  # 31 words, and the length is guidance, not a note (docs/adr/0039, D2)
        self.assertEqual(out["rests_on"]["findings"], 1)  # what it rests on, counted
        self.assertIn("1.75", out["numbers_not_in_its_runs"])  # listed, not refused
        self.assertNotIn("217", out["numbers_not_in_its_runs"])
        # the skeptic once proposed, from the state list (docs/adr/0050, D2); its figure waits for the set's review (B1)
        self.assertNotIn("skeptic_due_on_the_results", out)  # the recorder starts the skeptic (docs/adr/0051)
        self.assertFalse([i for i in self.svc.record.critic() if i["kind"] == "figure review due"])
        again = self.ok("report_result", result_id="res1", title="GATA1 knockdown raises SPI1 in every guide",
                        finding_ids=["f1"], figure="r1", text=TEXT)
        r = self.svc.record.state()["results"]["res1"]
        self.assertEqual((again["version"], r["version"], len(r["versions"])), (2, 2, 1))
        self.assertEqual(r["figure_run"], "r1")

    def test_a_figure_drawn_by_a_cell_that_read_its_findings_runs_is_its_figure_and_the_findings_stay(self):
        """A notebook cell that loaded the findings' tables and drew them was refused six times; the agent then added
        the drawing cells to the findings' runs. A cell whose trace reads a finding's run, directly or through an
        earlier cell, draws the result's figure; and a run that saved several figures names the one it gives."""
        rec = self.svc.record
        cells = (("c1", ["r1:result"]), ("c2", ["c1"]), ("c3", ["r2:result"]))
        for rid, uses in cells:
            rec.append("run_logged", {"run_id": rid, "what": "a cell", "code": "c", "inputs": [], "uses": uses},
                       rc.TOOL)
            rec.append("run_finished", {"run_id": rid, "state": "ok", "output": ""}, rc.TOOL)
        for fid, rid in (("main", "c2"), ("extended", "c2"), ("other", "c3")):
            rec.append("figure_saved", {"figure_id": fid, "run_id": rid, "paths": {"png": f"{fid}.png"},
                                        "caption": fid}, rc.TOOL)
        self.assertIn("saved 2 figures (main, extended)", self.refused(
            "report_result", result_id="res1", title="t", finding_ids=["f1"], figure="c2", text=TEXT))
        self.ok("report_result", result_id="res1", title="GATA1 knockdown raises SPI1", finding_ids=["f1"],
                figure="main", text=TEXT)
        st = rec.state()
        self.assertEqual((st["results"]["res1"]["figure"], st["results"]["res1"]["figure_run"]), ("main", "c2"))
        self.assertEqual(st["findings"]["f1"]["run_ids"], ["r1"])  # the finding is as it was
        self.assertIn("read none of them", self.refused(
            "report_result", result_id="res1", title="t", finding_ids=["f1"], figure="other", text=TEXT))

    def test_a_results_figure_is_reviewed_with_the_set_and_its_redrawing_is_not_due_again(self):
        """Each redrawing after a review made the figure review due again: six reviews and eight drawings of two
        results' figures. A result's figure is reviewed with the set, before the report (B1, 2026-11-13)."""
        self.svc.record.append("reply_recorded", {"kind": "enough", "words": "that is enough"}, rc.USER)
        self.ok("report_result", result_id="res1", title="GATA1 knockdown raises SPI1", finding_ids=["f1"],
                figure="fig1", text=TEXT)
        figure_due = lambda: [i["id"] for i in self.svc.record.critic() if i["kind"] == "figure review due"]
        self.assertEqual(figure_due(), [])
        rec = self.svc.record
        rec.append("figure_reviewed", {"figure_id": "fig1", "verdict": "fix", "issues": ["the n is missing"]},
                   rc.AGENT)
        rec.append("figure_saved", {"figure_id": "fig2", "run_id": "r1", "paths": {"png": "fig2.png"},
                                    "caption": "SPI1 with the n of each side"}, rc.TOOL)
        self.ok("report_result", result_id="res1", title="GATA1 knockdown raises SPI1", finding_ids=["f1"],
                figure="fig2", text=TEXT)
        self.assertEqual(figure_due(), [])

    def test_only_the_user_confirms_or_drops_it_and_a_dropped_one_leaves_the_report(self):
        self.ok("report_result", result_id="res1", title="kept", finding_ids=["f1"], figure="fig1", text=TEXT)
        self.svc.record.append("figure_saved", {"figure_id": "r2:figure", "run_id": "r2", "paths": {"png": "r2.png"},
                                                "caption": "the second figure"}, rc.TOOL)
        self.ok("report_result", result_id="res2", title="dropped", finding_ids=["f2"], figure="r2",
                text="A second finding [finding:f2].")
        told = digest.digest(self.svc.record.state(), self.svc.record.events(), True)
        self.assertIn("2 results proposed for the report", told["waits_on_you"][0])
        self.assertIn("relay their words", self.refused("report_result", result_id="res2", decision="dropped"))
        self.ok("report_result", result_id="res2", decision="dropped", user_words="leave the second one out")
        self.ok("report_result", result_id="res1", decision="confirmed", user_words="yes, that one")
        self.assertEqual([r["result_id"] for r in rc.results_shown(self.svc.record.state())], ["res1"])
        out = self.ok("write_report")
        self.assertEqual([r["result_id"] for r in out["results"]], ["res1"])
        self.assertNotIn("text", out)  # a summary, not the report
        text = report_text(out["report"])
        self.assertNotIn("dropped", text.split("## Appendix")[0])
        self.assertIn("A second finding", text.split("### Findings not shown as results")[1])


class TestADecisionAndAVersionInOneCall(Service):
    """GSE250558 on a8eecf8: a decision given with the whole result again dropped its changed text, r1 went through 11
    versions, and the main agent wrote the report again after each (P7)."""
    TITLE = "GATA1 knockdown raises SPI1"

    def test_a_decision_with_a_changed_text_records_the_version_then_the_decision(self):
        self.ok("report_result", result_id="res1", title=self.TITLE, finding_ids=["f1"], figure="fig1", text=TEXT)
        out = self.ok("report_result", result_id="res1", title=self.TITLE, finding_ids=["f1"], figure="fig1",
                      text=TEXT + " It holds in each guide [finding:f1].", decision="confirmed", user_words="yes")
        r = self.svc.record.state()["results"]["res1"]
        self.assertEqual((out["version"], r["version"], r["decision"]), (2, 2, "confirmed"))
        self.assertIn("each guide", r["text"])
        self.assertNotIn("next", out)  # decided already

    def test_what_a_version_leaves_out_stays_and_the_same_result_again_is_only_its_decision(self):
        self.ok("report_result", result_id="res1", title=self.TITLE, finding_ids=["f1"], figure="fig1", text=TEXT)
        out = self.ok("report_result", result_id="res1", title=self.TITLE, finding_ids=["f1"], figure="fig1",
                      decision="confirmed", user_words="yes")  # its text left out
        r = self.svc.record.state()["results"]["res1"]
        self.assertEqual((out["version"], r["version"], r["text"], r["decision"]), (1, 1, TEXT, "confirmed"))
        out = self.ok("report_result", result_id="res1", title=self.TITLE + " in each guide")
        r = self.svc.record.state()["results"]["res1"]
        self.assertEqual((out["version"], r["finding_ids"], r["figure"], r["text"]), (2, ["f1"], "fig1", TEXT))
        self.assertIsNone(r["decision"])  # a new version waits for its own decision

    def test_its_replies_say_code_wrote_the_report_and_the_page_marks_it(self):
        out = self.ok("report_result", result_id="res1", title=self.TITLE, finding_ids=["f1"], figure="fig1", text=TEXT)
        self.assertTrue(os.path.exists(out["report"].split("written again by code: ", 1)[1]))
        out = self.ok("report_result", result_id="res1", decision="confirmed", user_words="yes")
        self.assertIn("written again by code", out["report"])
        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "plugin",
                                        "status"))
        import status_file
        st = status_file.empty("/w/p-1a2b3c4d")
        status_file.apply_call(st, "report_result", {"result_id": "res1"}, out)
        self.assertTrue(st.get("report"))


class TestTheReport(Service):
    def test_each_result_with_its_figure_findings_checks_and_prior_knowledge_every_sentence_linked(self):
        self.ok("raise_doubt", doubt_id="d1", text="the shift is the cell cycle", would_change="it fades in G1",
                finding_id="f1")
        self.ok("answer_doubt", doubt_id="d1", outcome="holds", reason="it stays in G1", run_ids=["r1"])
        self.ok("open_thread", about="does SPI1 rise in every unit?", raised_by="f1", thread_id="units")
        self.ok("report_result", result_id="res1", title="GATA1 knockdown raises SPI1", finding_ids=["f1"],
                figure="fig1", text=TEXT)
        out = self.ok("write_report")
        self.assertEqual(out["links"]["unlinked"], [])
        text = report_text(out["report"])
        body = text.split(results_report.ENTRIES)[0]
        result = body[body.index("### Result 1. GATA1 knockdown raises SPI1"):body.index("## Open questions")]
        checks = body[body.rindex("### Result 1. GATA1 knockdown raises SPI1"):body.index("## Appendix")]
        self.assertIn("SPI1 in GATA1-i's cells against the control cells", result)  # the saved figure's caption
        self.assertIn("A description (a pattern, a count or a technical fact): no check runs on it in code", checks)
        self.assertIn("Answered by hand: the shift is the cell cycle, holds", checks)
        # no search of the session returned the PMID, so it is the agent's memory
        self.assertIn("GATA1 and PU.1 repress each other (PMID 10086723; from memory, not looked up)", result)
        opened = body[body.index("## Open questions"):body.index("## Methods, from the record")]
        self.assertIn("does SPI1 rise in every unit? (open)", opened)
        self.assertLessEqual(len([x for x in opened.splitlines() if x.startswith("- ")]), 5)
        appendix = body[body.index("## Appendix"):]
        for part in ("### Still open", "### Doubts answered", "### Findings not shown as results",
                     "### The record"):
            self.assertIn(part, appendix)
        self.assertIn('<a id="result-res1"></a>', text)
        self.assertIn('<a id="thread-units"></a>', text)


class TestTheNumbersOfAText(unittest.TestCase):
    def test_as_written_rounded_as_a_percentage_or_without_its_sign(self):
        sources = ["r = 0.4912; share 0.26; log2fc -0.39; 763 genes", {"n": 4, "fold": 2.17}]
        text = ("r = 0.49 [run:r1]; 26% of cells; down 0.39; 763 genes; 217% up; 4 of 4 guides in Figure 2 and "
                "panel 3; GATA1 and run0003; 1.5 times; 12,000 cells")
        self.assertEqual(text_numbers.unsupported(text, sources), ["1.5", "12,000"])
        self.assertEqual(text_numbers.numbers("a 1e-5 q and -2.5"), [("1e-5", 1e-05, 5, False), ("-2.5", -2.5, 1, False)])

    def test_its_limits_never_fail_and_say_no_more_than_the_text(self):
        self.assertEqual(text_numbers.unsupported("q = 1e-5", [{"q": 0.3}]), ["1e-5"])  # read to its fifth decimal
        self.assertEqual(text_numbers.unsupported("q = 1e-5", [{"q": 1.2e-05}]), [])
        self.assertEqual(text_numbers.unsupported("NKX2-1 is up 2-3 fold", ["2 and 3"]), [])  # a gene, not a range
        self.assertEqual(text_numbers.unsupported("p = 3.0e-03", [{"p": "3.0e-03"}]), [])
        text_numbers.unsupported("0.12345678901234567890123456789 and 1e30", [{"x": 1e30, "y": 12345.0}])


if __name__ == "__main__":
    unittest.main()
