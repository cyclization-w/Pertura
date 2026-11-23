"""A module's reply gave counts and identifiers, and the biology sat in its tables: of the points never looked at on
eight screens, 41% sat in a module that had run (2026-11-07). Each result now gives its biology in lines (which genes,
states, conditions or pairs), and its reply leads with them; the package calls, the policy and the figure's own
checks leave the reply."""
import json
import os
import tempfile
import unittest

import numpy as np
import pandas as pd

from pertura.analysis_service import _concise
from pertura.question_modules import c as cmod
from pertura.question_modules import e as emod
from pertura.question_modules import overview
from pertura.question_modules.contract import Result


class TestEachModuleSaysItsBiology(unittest.TestCase):
    def test_the_genes_of_each_target_past_its_line_most_first(self):
        summary = pd.DataFrame({"target": ["A", "B", "C"], "genes_changed": [2, 5, 1], "up": [1, 3, 1],
                                "down": [1, 2, 0], "beyond_control_groups": [True, True, False]})
        changed = pd.DataFrame({"target": ["A", "A", "B", "B", "B", "B", "B", "C"],
                                "gene": ["a1", "a2", "b1", "b2", "b3", "b4", "b5", "c1"],
                                "effect": [1.0, -1.0, 2.0, 1.5, 0.5, -2.0, -1.0, 1.0],
                                "p": [1e-5, 1e-4, 1e-9, 1e-8, 1e-3, 1e-7, 1e-6, 1e-3]})
        lines = cmod.genes_biology(changed, summary)
        self.assertEqual(lines, ["B: 5 genes, 3 up and 2 down; up b1, b2, b3; down b4, b5",
                                 "A: 2 genes, 1 up and 1 down; up a1; down a2"])  # C is inside its line

    def test_without_a_line_each_target_that_changes_genes_and_the_rest_named(self):
        summary = pd.DataFrame({"target": ["A", "B"], "genes_changed": [1, 2], "up": [1, 2], "down": [0, 0]})
        changed = pd.DataFrame({"target": ["A", "B", "B"], "gene": ["a1", "b1", "b2"], "effect": [1.0, 1.0, 2.0],
                                "p": [1e-3, 1e-3, 1e-4]})
        lines = cmod.genes_biology(changed, summary, most=1)
        self.assertEqual(lines[0], "B: 2 genes, 2 up and 0 down; up b2, b1")
        self.assertIn("1 more targets change genes", lines[1])

    def test_the_states_each_target_shifts_with_its_shares(self):
        per = pd.DataFrame({"target": ["A", "B", "C"], "state": ["s1", "s2", "s1"], "state_name": ["s1", "s2", "s1"],
                            "share": [0.30, 0.05, 0.12], "control_share": [0.10, 0.20, 0.10],
                            "mean_difference": [0.20, -0.15, 0.02], "q": [1e-6, 0.001, 0.5],
                            "method": ["fisher"] * 3, "changed": [True, True, False]})
        self.assertEqual(cmod.states_biology(per), [
            "A in s1: 30% of its cells against 10% of the control cells (q 1e-06)",
            "B in s2: 5% of its cells against 20% of the control cells (q 0.001)"])

    def test_a_composition_test_without_shares_still_names_the_pair(self):
        per = pd.DataFrame({"target": ["A"], "state": ["s1"], "share": [np.nan], "control_share": [np.nan],
                            "mean_difference": [np.nan], "q": [np.nan], "method": ["sccoda"], "changed": [True]})
        self.assertEqual(cmod.states_biology(per), ["A in s1: changed (sccoda)"])

    def test_the_programs_each_target_moves(self):
        hits = pd.DataFrame({"target": ["A", "B"], "score": ["HALLMARK_X", "HALLMARK_Y"], "effect": [0.05, -0.3],
                             "q": [0.01, 0.001]})
        self.assertEqual(cmod.programs_biology(hits), [
            "B in HALLMARK_Y: -0.3 against the control cells (q 0.001)",
            "A in HALLMARK_X: +0.05 against the control cells (q 0.01)"])

    def test_an_effect_that_differs_by_condition_in_each_condition(self):
        hits = pd.DataFrame({"target": ["A"], "score": ["HALLMARK_TNFA_SIGNALING_VIA_NFKB"], "interaction": [0.4],
                             "q": [0.002]})
        by_unit = pd.DataFrame({"target": ["A"] * 4, "score": ["HALLMARK_TNFA_SIGNALING_VIA_NFKB"] * 4,
                                "unit": ["u1", "u2", "u1", "u2"], "condition": ["6h", "6h", "18h", "18h"],
                                "difference": [0.1, 0.1, 0.5, 0.5]})
        line = emod.condition_biology(hits, by_unit, ["6h", "18h"])[0]
        self.assertTrue(line.startswith("A on "), line)
        self.assertIn("6h +0.1, 18h +0.5; change +0.4 (q 0.002)", line)

    def test_the_pairs_past_the_control_pairs_line_most_correlated_first(self):
        table = pd.DataFrame({"target": ["A", "B", "A"], "other": ["B", "C", "C"], "correlation": [0.2, 0.5, 0.01],
                              "control_pairs_as_high": [0.0, 0.01, 0.6]})
        self.assertEqual(emod.similar_biology(table), ["B and C: r 0.50", "A and B: r 0.20"])

    def test_the_network_names_the_other_targets_genes_each_target_changes(self):
        network = {"rows": ["RAF1", "FOS", "EGR1"], "cols": ["RAF1", "FOS", "EGR1"],
                   "own": {"RAF1": "RAF1", "FOS": "FOS", "EGR1": "EGR1"},
                   "M": np.array([[-1.0, -0.8, -0.6], [0.0, -0.2, 0.3], [0.1, 0.0, -0.5]]),
                   "q": np.array([[1e-9, 1e-6, 1e-4], [0.9, 0.04, 0.001], [0.5, 0.9, 1e-5]])}
        lines = overview.network_biology(network)
        self.assertIn("q < 0.05", lines[0])
        self.assertEqual(lines[1:], ["RAF1: lowers FOS (-0.80), lowers EGR1 (-0.60)", "FOS: raises EGR1 (+0.30)"])
        self.assertEqual(overview.network_biology(None), [])


class TestAKnockoutsOwnGeneIsReadAsPF7Says(unittest.TestCase):
    """GSE250558 on a8eecf8: a worker read FOS's guides raising JUNB, NR4A1 and ID4 as an artefact because they raised
    FOS's own transcript, a knockdown check in a Cas9 knockout. The knowledge base's PF7 now reaches the replies that
    read a knockout's own gene."""

    def test_a_knockout_guide_that_raises_its_transcript_is_said_so(self):
        from pertura.question_modules import b as bmod
        up, past = bmod.place(0.6, -1, bmod.EFFICACY_LOG2FC, knockout=True)
        self.assertEqual((up, past), (bmod.TRANSCRIPT_UP, False))
        self.assertEqual(bmod.place(0.1, -1, bmod.EFFICACY_LOG2FC, knockout=True)[0], bmod.TRANSCRIPT_UNCHANGED)
        self.assertTrue(bmod.place(-0.8, -1, bmod.EFFICACY_LOG2FC, knockout=True)[1])
        self.assertNotIn(bmod.place(0.6, -1, bmod.EFFICACY_LOG2FC)[0],  # CRISPRi keeps its own words
                         (bmod.TRANSCRIPT_UP, bmod.TRANSCRIPT_UNCHANGED))

    def test_the_efficacy_and_the_network_say_what_a_knockouts_own_gene_says(self):
        from pertura.question_modules import b as bmod
        verdicts = pd.DataFrame({"guide": ["FOS_1", "FOS_2", "TCF7_1"], "target": ["FOS", "FOS", "TCF7"],
                                 "verdict": [bmod.TRANSCRIPT_UP, bmod.TRANSCRIPT_UP, "at least 30% down"],
                                 "past_the_line": [False, False, True]})
        lines = bmod.efficacy_biology(verdicts, knockout=True)
        self.assertIn("PF7", lines[0])
        self.assertEqual(lines[1:], ["TCF7: at least 30% down 1", "FOS: transcript up 2"])
        self.assertNotIn("PF7", " ".join(bmod.efficacy_biology(verdicts, knockout=False)))
        network = {"rows": ["FOS"], "cols": ["JUNB"], "own": {"FOS": "FOS"}, "M": np.array([[0.47]]),
                   "q": np.array([[1e-6]])}
        lines = overview.network_biology(network, knockout=True)
        self.assertIn("PF7", lines[0])
        self.assertEqual(lines[-1], "FOS: raises JUNB (+0.47)")


class TestAFoundResultSaysWhatPublishedScreensDidNext(unittest.TestCase):
    """P13: the knowledge base's next steps reached the agent as ids to look up, and none was read (read_knowledge 0 to
    8 calls a run). A found result now gives them as lines of content."""

    def test_a_states_result_gives_its_moves_with_when_and_a_screen_that_made_them(self):
        from pertura import moves
        lines = moves.after("C.states_and_composition", fitting={"P3"})
        self.assertTrue(lines[0].startswith("Between or within states, when state composition shifts: "
                                            "`states` view `within` (C.within_states)"), lines[0])
        self.assertIn("(as Xia 2023", lines[0])
        self.assertIn("A state's identity, when a state is new or mixed", lines[1])
        self.assertFalse([x for x in lines if "card P3" in x])  # P3.1 and P3.3 are what it did

    def test_a_cards_steps_only_where_the_design_allows_the_card(self):
        from pertura import moves
        self.assertFalse([x for x in moves.after("E.combinations", fitting={"P3"}) if "card P6" in x])
        self.assertIn("the pair's effect on cell states [states] (card P6, step P6.7)",  # its tool call (ADR 0044)
                      moves.after("E.combinations", fitting={"P3", "P6"}))
        self.assertFalse([x for x in moves.after("E.condition_x_perturbation") if "Condition by perturbation" in x])

    def test_module_result_gives_them_with_a_found_result_only(self):
        from unittest import mock
        from pertura.analysis_service import AnalysisService
        with tempfile.TemporaryDirectory() as tmp:
            svc = AnalysisService({"workspace": os.path.join(tmp, "ws")})
            svc.project = os.path.join(tmp, "project")
            svc.design = mock.Mock(cfg={})
            svc.record = mock.Mock()
            runs = {r: {"run_id": r, "job_id": r, "state": "done", "what": "states",
                        "question_id": "C.states_and_composition"} for r in ("found", "none")}
            svc.record.state.side_effect = lambda: {"runs": runs, "plan": {"items": {}}, "knowledge": []}
            for r, status in (("found", "detected"), ("none", "not_detected")):
                os.makedirs(os.path.join(svc._jobs_root(), r))
                with open(os.path.join(svc._jobs_root(), r, "result.json"), "w", encoding="utf-8") as fh:
                    json.dump({"status": status, "summary": "s"}, fh)
            with mock.patch.object(svc, "_design_facts", return_value={"n_control_guides": 9}):
                out = svc.module_result("found")
                self.assertIn("Between or within states", out["published_next"][0])
                self.assertEqual(list(out).index("published_next"), list(out).index("summary") + 1)
                self.assertNotIn("published_next", svc.module_result("none"))
            svc.record = None
            svc.close()


class TestTheReplyLeadsWithTheBiology(unittest.TestCase):
    def test_a_result_writes_its_biology_only_when_it_has_one(self):
        with tempfile.TemporaryDirectory() as d:
            Result("detected", pd.DataFrame(), {}, "figure.png", {}, "s", biology=["A: 2 genes"]).write(d)
            with open(os.path.join(d, "result.json"), encoding="utf-8") as fh:
                self.assertEqual(json.load(fh)["biology"], ["A: 2 genes"])
            Result("detected", pd.DataFrame(), {}, "figure.png", {}, "s").write(d)
            with open(os.path.join(d, "result.json"), encoding="utf-8") as fh:
                self.assertNotIn("biology", json.load(fh))

    def test_the_package_calls_policy_and_figure_checks_leave_the_reply(self):
        out = _concise({"run_id": "r1", "state": "done", "job_id": "r1", "status": "detected",
                        "diagnostics": {"method": "wilcoxon", "package_calls": [{"package": "scanpy"}],
                                        "policy": {"q": 0.05}, "recipe_as_run": "pt.genes()"},
                        "figure_checks": {"layout_issues": ["text overlaps"]}, "summary": "2 of 3 targets",
                        "biology": ["A: 2 genes"], "folder": "/ws/jobs/r1"})
        self.assertEqual(list(out)[:4], ["run_id", "state", "summary", "biology"])
        self.assertEqual(out["diagnostics"], {"method": "wilcoxon"})
        self.assertEqual(out["in_result_json"], ["package_calls", "policy"])
        self.assertEqual(out["in_the_notebook"], "pt.genes()")  # the lines that give the same result (ADR 0044)
        self.assertNotIn("figure_checks", out)
        self.assertNotIn("job_id", out)  # the run's own ID again
        self.assertEqual(out["folder"], "/ws/jobs/r1")  # notebook code reads the run's tables from it

    def test_a_notebook_runs_empty_job_stays_for_the_status_line(self):
        self.assertIsNone(_concise({"run_id": "r1", "job_id": None, "diagnostics": {}})["job_id"])


if __name__ == "__main__":
    unittest.main()
