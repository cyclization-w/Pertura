"""Dossiers, figure panels and the results report (docs/adr/0020, section 7; build step 7): the record's dossier
rules and the critic's dossier check, the panel, and a session on the synthetic deposit through the analysis server
that ends in a report whose every sentence links to a record entry."""
import os
import tempfile
import unittest

import numpy as np

from test_design_service import answer, confirm_the_cells, make_deposit, proposal

from pertura import design_gate, panels, record as rc, results_report, screen_object
from pertura.analysis_service import AnalysisService


def checked(record, finding_id, guides="agree", other="agrees"):
    """A finding's checks as pertura.finding_checks records them: the lead, each guide alone, the other test."""
    record.append("check_recorded", {"finding_id": finding_id, "check": "lead", "outcome": "agrees",
                                     "reason": "a test on cells (Wilcoxon), since the screen is one pool",
                                     "numbers": {"test": "wilcoxon", "cells": 40, "effect": -1.0, "q": 0.001}},
                  rc.TOOL)
    record.append("check_recorded", {"finding_id": finding_id, "check": "guides_agree", "outcome": guides,
                                     "numbers": {"parts": {"g1": {"effect": -1.1}, "g2": {"effect": -0.8}}}}, rc.TOOL)
    record.append("check_recorded", {"finding_id": finding_id, "check": "other_test", "outcome": other,
                                     "numbers": {"label": "edgeR-QL pseudobulk by sample (technical replicates)"}},
                  rc.TOOL)


class TestDossierRecord(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.r = rc.Record(os.path.join(self.tmp.name, "record"))
        self.r.append("question_planned", {"question_id": "C.genes", "letter": "C", "item": "genes",
                                           "because": "always"}, rc.PLAN)
        self.r.append("run_logged", {"run_id": "r1", "what": "module C.genes", "code": "", "inputs": [],
                                     "question_id": "C.genes"}, rc.TOOL)
        for f in ("f1", "f2"):
            self.r.append("finding_recorded", {"finding_id": f, "question_id": "C.genes", "target": "TA",
                                               "feature": "G1", "direction": "down", "population": "all",
                                               "run_ids": ["r1"]}, rc.AGENT)

    def tearDown(self):
        self.tmp.cleanup()

    def propose(self, did, targets=8, genes=0):
        self.r.append("dossier_proposed", {"dossier_id": did, "title": "t", "lead_findings": ["f1"], "main_run": "r1",
                                           "map_size": {"targets": targets, "genes": genes}}, rc.AGENT)

    def test_only_the_user_chooses_and_only_proposed_dossiers(self):
        self.propose("d1")
        with self.assertRaises(rc.RecordError):
            self.r.append("dossier_chosen", {"dossier_ids": ["d1"]}, rc.AGENT)
        with self.assertRaises(rc.RecordError):
            self.r.append("dossier_chosen", {"dossier_ids": ["d9"]}, rc.USER)
        with self.assertRaises(rc.RecordError):
            self.r.append("dossier_proposed", {"dossier_id": "d2", "title": "t", "lead_findings": ["nope"],
                                               "main_run": "r1", "map_size": {}}, rc.AGENT)
        self.r.append("dossier_chosen", {"dossier_ids": ["d1"]}, rc.USER)
        self.assertEqual(self.r.state()["chosen"], ["d1"])

    def test_a_part_needs_what_it_points_to(self):
        self.propose("d1")
        for part, extra in (("effect", {}), ("convergence", {"run_ids": ["r1"], "kinds": ["genes"]}),
                            ("mechanism", {"knowledge_ids": [99]}), ("relations", {}), ("nonsense", {})):
            with self.assertRaises(rc.RecordError, msg=part):
                self.r.append("dossier_part_recorded", {"dossier_id": "d1", "part": part, "text": "x", **extra},
                              rc.AGENT)

    def test_the_critic_lists_parts_map_level_and_lead_findings(self):
        """The critic lists (docs/adr/0022, section 13; docs/adr/0025, section 3): a lead finding whose checks have
        not run, or whose checks disagree; one whose checks agree is not listed."""
        self.propose("d1")
        self.propose("small", targets=3, genes=10)
        self.assertEqual([i["kind"] for i in rc.dossier_items(self.r.state())], ["results proposed, none chosen"])
        self.r.append("dossier_chosen", {"dossier_ids": ["d1", "small"]}, rc.USER)
        kinds = {(i["kind"], i["id"]) for i in rc.dossier_items(self.r.state())}
        self.assertIn(("result part missing", "d1"), kinds)
        self.assertIn(("result's main figure covers few targets or genes", "small"), kinds)
        self.assertNotIn(("result's main figure covers few targets or genes", "d1"), kinds)
        self.assertIn(("lead finding whose checks have not run", "d1"), kinds)
        checked(self.r, "f1", guides="disagree")
        kinds = {(i["kind"], i["id"]) for i in rc.dossier_items(self.r.state())}
        self.assertIn(("lead finding whose checks disagree", "d1"), kinds)
        checked(self.r, "f1")  # a later check replaces the earlier one
        kinds = {i["kind"] for i in rc.dossier_items(self.r.state())}
        self.assertFalse({"lead finding whose checks disagree", "lead finding whose checks have not run"} & kinds)
        checked(self.r, "f1", other="other direction")
        self.assertEqual(rc.disagreement(self.r.state()["findings"]["f1"]), "other_test: other direction")


class TestPanel(unittest.TestCase):
    def test_the_main_figure_first_then_the_rest_at_their_drawn_size(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as d:
            paths = []
            for k, (w, h) in enumerate(((2400, 600), (900, 500), (900, 700), (2000, 400))):
                p = os.path.join(d, f"f{k}.png")
                Image.new("RGB", (w, h), (200, 200, 200)).save(p)
                paths.append(p)
            out = os.path.join(d, "panel.png")
            tiles = panels.compose(paths, out)
            with Image.open(out) as im:
                width = im.width
        self.assertEqual([t["letter"] for t in tiles], ["a", "b", "c", "d"])
        self.assertEqual([t["scaled"] for t in tiles], [True, False, False, False])  # only the one wider than 183 mm
        self.assertEqual(width, int(round(panels.DOUBLE * panels.DPI)))


class TestReportSession(unittest.TestCase):
    """A session on the synthetic deposit through the server, from a module run to a linked report."""

    def call(self, tool, elicit=None, **args):
        ok, out = self.svc.call(tool, args, elicit=elicit)
        self.assertTrue(ok, (tool, out))
        return out

    def refused(self, tool, **args):
        ok, out = self.svc.call(tool, args)
        self.assertFalse(ok, (tool, out))
        return out["error"]

    def test_from_runs_to_a_report_with_every_sentence_linked(self):
        from unittest import mock
        # the synthetic deposit has 4 targets and some 15 changed genes: the map-level size, tested above, is lowered
        # here so that its one module run can carry a dossier through to the report
        with tempfile.TemporaryDirectory() as d, mock.patch.object(rc, "MAP_GENES", 10):
            deposit = os.path.join(d, "deposit")
            os.makedirs(deposit)
            make_deposit(deposit)
            self.svc = svc = AnalysisService({"workspace": os.path.join(d, "ws"),  # its figure reviews: the
                                              "switches": {"research_lead": True, "figure_review": True}})  # ablation (ADR 0052)
            organism = {"value": "human", "status": "reported", "source": "GEO"}
            self.call("open_project", data_folder=deposit)
            self.call("measure")
            self.call("check_design", proposal=proposal(experiment__organism=organism))
            self.call("confirm_design", elicit=answer())
            screen_object.build(deposit, design_gate.load(svc.design.design_file)[0], svc._screen_dir())
            confirm_the_cells(svc)
            self.call("plan_questions")
            genes = self.call("run_module", module="C.genes", choices={"method": "rank_test"}, wait=300)
            self.assertEqual(genes["state"], "done", genes)
            done = self.call("run_module", module="A.what_was_done", wait=300)
            for r in (genes, done):
                self.call("read_run", run_id=r["run_id"], reading=r["summary"])
            self.call("close_question", question_id="A.what_was_done", status=done["status"],
                      evidence=[done["run_id"]], figure=os.path.join(done["folder"], "figure.png"))
            with mock.patch.object(svc.record, "start_run", return_value="checks-job") as started:
                for f, t in (("f1", "TA"), ("f2", "TB")):
                    out = self.call("record_finding", finding_id=f, question_id="C.genes", target=t, feature=t,
                                    direction="down", population="all", run_ids=[genes["run_id"]], kind="gene")
                    self.assertEqual(out["checks_run"], "checks-job")
            self.assertEqual(started.call_count, 2)  # each finding's checks start as a job when it is recorded
            self.assertIn("pertura.finding_checks", started.call_args[0][1])
            checked(svc.record, "f1")
            checked(svc.record, "f2")
            k = self.call("record_knowledge", question_id="G.meaning", source="PubMed", citation="PMID 1",
                          text="TA is known to act on its own gene")
            seq = svc.record.state()["knowledge"][-1]["seq"]
            proposed = self.call("propose_dossier", dossier_id="d1", title="Each target lowers its own gene",
                                 lead_findings=["f1"], main_run=genes["run_id"], why="every target, one map")
            self.assertTrue(proposed["map_level"], proposed)
            self.assertEqual(proposed["lead_findings"]["f1"]["checks"]["lead"], "agrees")
            self.assertIsNone(proposed["lead_findings"]["f1"]["disagree"])
            self.call("propose_dossier", dossier_id="d2", title="A single gene", lead_findings=["f2"],
                      main_run=done["run_id"], why="one gene")
            # the user chooses in chat, relayed with their words (docs/adr/0032, D8): no words, no choice
            self.refused("choose_dossiers", chosen=["d1", "d2"], user_words="")
            self.call("choose_dossiers", chosen=["d1", "d2"], user_words="both of them")
            self.assertIn("result's main figure covers few targets or genes", [i["kind"] for i in svc.record.critic()])
            early = self.call("write_report")  # written whatever is open, and it says what is (ADR 0022, section 13)
            self.assertIn("A result whose main figure covers few targets or genes",
                          open(early["report"], encoding="utf-8").read())
            unlinked = self.call("record_dossier_part", dossier_id="d1", part="effect", run_ids=[genes["run_id"]],
                                 text="TA lowers its gene. No link here.")  # kept, its sentences listed (ADR 0027)
            self.assertEqual(unlinked["unlinked"], ["TA lowers its gene.", "No link here."])
            g = f"[run:{genes['run_id']}]"
            self.call("record_dossier_part", dossier_id="d1", part="effect", run_ids=[genes["run_id"]],
                      text=f"TA lowers TA in both samples {g}.")
            self.call("record_dossier_part", dossier_id="d1", part="specificity", run_ids=[genes["run_id"]],
                      text=f"TB does not lower TA {g}.")
            self.call("record_dossier_part", dossier_id="d1", part="checks",
                      text="Both guides and the pseudobulk test agree [finding:f1].")
            self.call("record_dossier_part", dossier_id="d1", part="convergence", kinds=["genes", "guides"],
                      run_ids=[genes["run_id"], done["run_id"]],
                      text=f"Both guides of TA agree {g}. The cells per guide are even [run:{done['run_id']}].")
            self.call("record_dossier_part", dossier_id="d1", part="mechanism", knowledge_ids=[seq],
                      text=f"CRISPRi silences a target's own promoter [knowledge:{seq}].")
            self.call("record_dossier_part", dossier_id="d1", part="relations", finding_ids=["f2"],
                      text="TB's own effect is the same kind [finding:f2].")
            h = self.call("run_module", module="B.guide_calls_and_their_evidence", wait=300)  # beside the main run
            self.call("record_dossier_part", dossier_id="d1", part="validation", run_ids=[h["run_id"]],
                      text=f"An arrayed CRISPRi of TA in three donors would settle it [run:{h['run_id']}].")
            self.assertIn("first tile", self.refused("make_panel", dossier_id="d1",
                                                     tiles=[{"run_id": h["run_id"], "caption": "x",
                                                             "role": "overview"}]))
            overview = {"run_id": genes["run_id"], "caption": "changed genes per target and sample",
                        "role": "overview"}
            self.assertIn("role", self.refused("make_panel", dossier_id="d1",
                                               tiles=[{k: v for k, v in overview.items() if k != "role"}]))
            self.assertIn("overview", self.refused("make_panel", dossier_id="d1",
                                                   tiles=[{**overview, "role": "detail"}]))
            figure_id = f"{done['run_id']}:figure"  # a module's figure, entered in the record when its run ended
            self.assertIn(figure_id, svc.record.state()["figures"])
            panel = self.call("make_panel", dossier_id="d1",
                              tiles=[overview,
                                     {"figure_id": figure_id, "caption": "cells per guide and sample",
                                      "role": "detail"},
                                     {"run_id": h["run_id"], "caption": "the guide calls",
                                      "role": "evidence"}],
                              paragraph=f"Each target lowers its own gene {g}. The effect leads the finding f1 "
                                        "[finding:f1].")
            self.assertTrue(os.path.exists(panel["panel"]) and os.path.exists(panel["supplementary"]))
            self.assertEqual([(t["letter"], t["figure"]) for t in panel["tiles"]],
                             [("a", "main"), ("b", "main"), ("a", "supplementary")])
            # the figure reviews, due once its result was proposed, left with the figure reviewer (2026-11-20)
            self.assertFalse([i for i in svc.record.critic() if i["kind"] == "figure review due"])
            again = self.call("run_module", module="C.genes", choices={"method": "rank_test"}, wait=300)
            self.call("read_run", run_id=again["run_id"], reading=again["summary"])
            self.call("make_panel", dossier_id="d1",  # a rerun of the main analysis may lead the panel
                      tiles=[{**overview, "run_id": again["run_id"]}])
            self.call("choose_dossiers", chosen=["d1"], user_words="only the first one")
            items = [i for i in svc.record.critic() if i["id"] == "d1"]
            self.assertEqual(items, [])
            # the skeptic, for each proposed lead, before the user chooses (docs/adr/0030)
            skeptic_due = lambda: [i["id"] for i in self.call("open_items")["due"] if i["kind"] == "skeptic due"]
            self.assertEqual(skeptic_due(), ["f1", "f2"])
            self.call("record_skeptic_check", finding_ids=["f1", "f2"], doubt_ids=[])
            self.assertEqual(skeptic_due(), [])
            self.assertEqual(svc.record.blocks_report(), [])
            listed = open(self.call("write_report")["report"], encoding="utf-8").read()
            # docs/adr/0032 (D7): the questions are a coverage list, so a question without a status is not open
            self.assertNotIn("Not yet answered", listed)
            for qid in [i["id"] for i in svc.record.critic() if i["kind"] == "question without a status"]:
                self.call("close_question", question_id=qid, status="cannot_answer", reason="outside this test")
            out = self.call("write_report")
            links = out["links"]
            self.assertGreater(links["sentences"], 10)
            self.assertEqual(links["unlinked"], [])
            text = open(out["report"], encoding="utf-8").read()
            self.assertLess(text.index("![Figure 1]"), text.index("**Effect.**"))  # the figure first
            main_legend = text[text.index("**Figure 1.**"):text.index("The effect leads the finding f1")]
            self.assertIn("cells per guide and sample", main_legend)  # overview and detail in the main figure
            self.assertNotIn("the guide calls", main_legend)
            supplementary = text[text.index("## Supplementary figures"):text.index("## Methods, from the record")]
            self.assertIn("![Supplementary Figure 1]", supplementary)  # evidence in the supplementary one
            self.assertIn("the guide calls", supplementary)
            self.assertIn("Mechanism (prior knowledge)", text)
            self.assertIn("**Checks.** TA on TA (down), by Wilcoxon on cells (40 cells; effect -1): agrees; each "
                          "guide alone: agree (2 guides)", text)
            self.assertIn("was checked by each guide alone and the other test", text)
            self.assertNotIn("A single gene", text)  # a dossier not chosen stays in the record only
            with open(out["report"], "w", encoding="utf-8") as fh:  # a sentence without its link is caught
                fh.write(text.replace("## The record", "A claim without a source.\n\n"
                                                                   "## The record"))
            again = results_report.check_links(out["report"], svc.record.state())
            self.assertEqual(again["unlinked"], ["A claim without a source."])
            svc.close()


if __name__ == "__main__":
    unittest.main()
