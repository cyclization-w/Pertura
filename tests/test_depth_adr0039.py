"""ADR 0039 (depth), phase A, from the overnight runs on 547c5c7:
- D3: the first look's candidate structures are facts read from its tables (pertura.candidates); an observation the
  agent opens is a thread of its own kind, offered for a probe, and the lead's complete waits for each to end, be
  followed or be set aside (the BAF core of GSE203592 was seen by a worker and dropped into a limitation);
- D4: two reviews of the figures as a set are a process, not a figure: a planned panel found missing is drawn or
  answered with what the data cannot give before complete (GSE250558 run 2 ended "at a modest paper level" with
  its planned panels undrawn);
- D5: a figure's review keeps what the figure shows that the text does not say (MED12's sign change was drawn in
  GSE278572's Figure 5a and said nowhere);
- D2: a result's reply counts what it rests on, and its text's length is guidance, not a note to fix;
- D1: the main notebook's cells, in order, are the agent's memory after a compaction."""
import os
import tempfile
import unittest
from unittest import mock

import pandas as pd

from test_design_service import make_deposit

from pertura import candidates
from pertura import record as rc
from pertura.analysis_service import AnalysisService


class TestCandidates(unittest.TestCase):
    def test_sets_alike_join_the_pairs_beyond_the_control_pairs(self):
        pairs = pd.DataFrame({"target": ["A", "B", "A", "D", "E"], "other": ["B", "C", "C", "E", "F"],
                              "correlation": [0.73, 0.65, 0.64, 0.62, 0.10],
                              "control_pairs_as_high": [0.0, 0.0, 0.01, 0.02, 0.40]})
        out = candidates.sets_alike(pairs)
        self.assertEqual(out["count"], 2)
        self.assertEqual(out["sets"][0]["targets"], ["A", "B", "C"])  # the most alike first
        self.assertEqual(out["sets"][0]["pairs"], 3)
        self.assertEqual(out["sets"][1]["targets"], ["D", "E"])  # F joins by no pair beyond the controls

    def test_a_set_names_the_targets_opposite_it(self):
        """Groups of perturbations with opposite effects (2026-11-11): on GSE342764 Zmynd8, Hdac4 and Phf20l1 act
        against Ep300, Kdm6b and Tbx21, on GSE250558 FOS against EGR1, on GSE263761 Spi1 against the interferon axis;
        the first look listed the sets alike alone."""
        rows = [("A", "B", 0.7), ("A", "C", 0.6), ("B", "C", 0.65), ("A", "D", -0.4), ("B", "D", -0.35),
                ("C", "D", -0.3), ("A", "E", 0.0), ("B", "E", -0.05), ("C", "E", 0.02), ("D", "E", 0.01)]
        pairs = pd.DataFrame(rows, columns=["target", "other", "correlation"]).assign(control_pairs_as_high=0.5)
        sets = candidates.sets_alike(pairs, reference=0.1, low=-0.1)["sets"]
        top = next(x for x in sets if x["targets"] == ["A", "B", "C"])  # the core A, B comes first, then this
        self.assertEqual(top["opposite"], [{"target": "D", "mean_correlation": -0.35}])  # E is neither
        self.assertNotIn("opposite", candidates.sets_alike(pairs, reference=0.1)["sets"][0])  # no line, none said

    def test_with_the_control_reference_a_core_and_its_wider_set_both_show_and_nothing_chains(self):
        # GSE203592's shape: a tight core (A, B, C), a weaker member (D), a separate pair (E, F), and one pair
        # (A, E) just beyond the control pairs that joined everything when pairs alone joined the sets
        rows = [("A", "B", 0.73), ("B", "C", 0.65), ("A", "C", 0.64), ("A", "D", 0.39), ("B", "D", 0.41),
                ("C", "D", 0.35), ("E", "F", 0.62), ("A", "E", 0.25), ("B", "E", 0.05), ("C", "E", 0.02),
                ("D", "E", 0.0), ("A", "F", 0.02), ("B", "F", 0.03), ("C", "F", 0.01), ("D", "F", 0.04)]
        pairs = pd.DataFrame(rows, columns=["target", "other", "correlation"]).assign(
            control_pairs_as_high=lambda t: [0.0 if c > 0.2 else 0.5 for c in t["correlation"]])
        self.assertEqual(candidates.sets_alike(pairs)["count"], 1)  # chained through A and E
        out = candidates.sets_alike(pairs, reference=0.16)
        found = [s["targets"] for s in out["sets"]]
        self.assertIn(["A", "B", "C"], found)
        self.assertIn(["A", "B", "C", "D"], found)
        self.assertIn(["E", "F"], found)
        self.assertNotIn(["A", "B", "C", "D", "E", "F"], found)
        self.assertEqual(found[0], ["A", "B"])  # the most alike first

    def test_targets_changing_between_conditions_at_the_modules_level(self):
        per = pd.DataFrame({"target": ["MED12", "MED12", "MYC", "KLF2"], "score": ["stim", "treg", "stim", "stim"],
                            "interaction": [-0.31, 0.12, -0.2, 0.05], "q": [0.001, 0.03, 0.2, 0.04]})
        out = candidates.changing_between_conditions(per)
        self.assertEqual([t["target"] for t in out["targets"]], ["MED12", "KLF2"])
        self.assertEqual(out["targets"][0]["changes"], {"stim": -0.31, "treg": 0.12})

    def test_targets_far_from_the_control_cells_past_their_line_only(self):
        t = pd.DataFrame({"target": ["A", "B", "C"], "e_distance": [0.7, 0.2, 0.9],
                          "past_the_line": [True, True, False], "genes_changed": [208, None, 5]})
        out = candidates.far_from_controls(t)
        self.assertEqual([x["target"] for x in out["targets"]], ["A", "B"])
        self.assertEqual(out["targets"][0]["genes_changed"], 208)
        self.assertNotIn("genes_changed", out["targets"][1])

    def test_known_complexes_beside_a_set_hold_two_of_its_targets_and_are_no_compartment(self):
        """D6: GO cellular components holding two or more of a set's targets, matched without regard to case (a
        mouse screen's Arid1a, MSigDB's mouse sets), the most held first, then the smallest; a compartment past
        COMPLEX_MAX genes (chromatin: 1,422) is left out."""
        sets = {"GOCC_BAF": ["ARID1A", "SMARCC1", "SMARCD2", "SMARCE1"], "GOCC_SWI_SNF": ["ARID1A", "SMARCC1"] + [
                f"G{i}" for i in range(40)], "GOCC_PAIR": ["ARID1A", "X"], "GOCC_INO80": ["INO80", "ACTR5"],
                "GOCC_CHROMATIN": ["ARID1A", "SMARCC1", "SMARCD2"] + [f"C{i}" for i in range(300)]}
        out = candidates.known_complexes(["Arid1a", "Smarcc1", "Smarcd2"], sets)
        self.assertEqual([x["set"] for x in out], ["GOCC_BAF", "GOCC_SWI_SNF"])
        self.assertEqual(out[0], {"set": "GOCC_BAF", "holds": ["Arid1a", "Smarcc1", "Smarcd2"], "size": 4})
        self.assertEqual(candidates.known_complexes(["Arid1a", "Tox"], sets), [])  # one target in each: none

    def test_a_table_missing_or_without_its_columns_lists_nothing(self):
        for fn in (candidates.sets_alike, candidates.changing_between_conditions, candidates.far_from_controls):
            self.assertEqual(fn(None)["count"], 0)
            self.assertEqual(fn(pd.DataFrame({"x": [1]}))["count"], 0)


class TestThroughTheService(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        deposit = os.path.join(self.tmp.name, "deposit")
        os.makedirs(deposit)
        make_deposit(deposit)
        self.svc = AnalysisService({"workspace": os.path.join(self.tmp.name, "ws")})
        ok, out = self.svc.call("open_project", {"data_folder": deposit})
        assert ok, out
        self.rec = self.svc.record
        for rid in ("r1", "r2"):
            self.rec.append("run_logged", {"run_id": rid, "what": "a cell", "code": "c", "inputs": []}, rc.TOOL)
            self.rec.append("run_finished", {"run_id": rid, "state": "ok", "output": "o"}, rc.TOOL)
        png = os.path.join(self.tmp.name, "f.png")
        open(png, "wb").close()
        self.rec.append("figure_saved", {"figure_id": "fig1", "run_id": "r1", "paths": {"png": png}, "caption": "c"},
                        rc.TOOL)

    def tearDown(self):
        self.svc.close()
        self.tmp.cleanup()

    def call(self, tool, **args):
        return self.svc.call(tool, args)

    def review_the_set(self, issues):
        return self.call("record_figure_review", figure_id="the set", verdict="fix", issues=issues)

    def test_a_result_counts_what_it_rests_on_and_its_length_is_no_note(self):
        ok, out = self.call("open_thread", about="the BAF core", raised_by="r1", observation=True)
        tid = out["thread_id"]
        ok, out = self.call("record_finding", finding_id="F1", statement="ARID1A, SMARCC1 and SMARCD2 act alike "
                            "[run:r1].", run_ids=["r1"], kind="description", thread_id=tid,
                            prior={"text": "the subunits of one complex", "source": "PMID 0"})
        self.assertTrue(ok, out)
        ok, out = self.call("report_result", result_id="R1", title="The BAF core acts alike.", finding_ids=["F1"],
                            figure="fig1", text="The BAF core acts alike [finding:F1].")
        self.assertTrue(ok, out)
        self.assertNotIn("text", out)  # 7 words, and no note asks for 150
        self.assertEqual(out["rests_on"]["findings"], 1)
        self.assertEqual(out["rests_on"]["with_prior_knowledge"], 1)
        self.assertEqual(out["rests_on"]["from_a_thread_that_tested_something"], 1)
        ok, out = self.call("read_record", what="results", ids=["R1"])
        self.assertEqual(out["results"]["R1"]["rests_on"]["findings"], 1)
        ok, out = self.call("read_record", what="results")  # without ids, a line that says what it rests on
        self.assertIn("rests on findings 1", out["results"]["R1"])

    def test_known_complexes_come_beside_the_sets_with_their_source(self):
        """D6: beside each set of targets alike, the GO cellular components holding two or more of them, with the
        source named; with the organism not confirmed, why they were not read."""
        folder = os.path.join(self.tmp.name, "gene_sets")
        os.makedirs(os.path.join(folder, "msigdb"))
        with open(os.path.join(folder, "msigdb", "c5.go.cc.v2025.1.Hs.symbols.gmt"), "w", encoding="utf-8") as fh:
            fh.write("\t".join(["GOCC_BAF", "http://x", "ARID1A", "SMARCC1", "SMARCD2"]) + "\n")
        sets = [{"targets": ["ARID1A", "SMARCC1"]}, {"targets": ["TOX", "NR4A1"]}]
        with mock.patch.dict(os.environ, {"PERTURA_GENE_SETS": folder}), \
                mock.patch.object(self.svc, "_organism", return_value="human"):
            out = self.svc._known_complexes(sets)
        self.assertIn("MSigDB GO cellular component sets (release 2025.1, human)", out["known_complexes_from"])
        self.assertEqual(sets[0]["known_complexes"], [{"set": "GOCC_BAF", "holds": ["ARID1A", "SMARCC1"], "size": 3}])
        self.assertEqual(sets[1]["known_complexes"], [])
        self.assertTrue(self.svc._known_complexes([{"targets": ["A", "B"]}])["known_complexes"].startswith("not read"))

    def test_the_leads_coverage_names_the_first_looks_sources_not_run(self):
        ok, out = self.call("open_thread", about="two blocks", raised_by="r1", observation=True)
        ok, out = self.call("read_record", what="coverage")
        self.assertTrue(ok, out)
        cov = out["coverage"]
        self.assertEqual(cov["the_record_as_it_stands"]["open"]["first_look_runs_not_run_yet"],
                         ["similarity view alike (E.similar_targets)", "A.screen_overview"])  # by the tool that runs it
        self.assertEqual([o["about"] for o in cov["the_record_as_it_stands"]["open"]["observations_not_yet_explained"]], ["two blocks"])

    def test_the_notebook_gives_the_main_cells_in_order(self):
        for rid, worker in (("run0003-aaaaaa", None), ("run0004-bbbbbb", "t2-other"), ("run0005-cccccc", None)):
            self.rec.append("run_logged", {"run_id": rid, "what": f"cell {rid[3:7]}", "code": "c", "inputs": [],
                                           "worker": worker}, rc.TOOL)
            self.rec.append("run_finished", {"run_id": rid, "state": "done", "output": "x" * 500}, rc.TOOL)
        ok, out = self.call("read_record", what="notebook")
        self.assertTrue(ok, out)
        cells = out["notebook"]["cells"]
        self.assertEqual([c["run_id"] for c in cells], ["run0003-aaaaaa", "run0005-cccccc"])
        self.assertTrue(cells[0]["output_end"].startswith("..."))


if __name__ == "__main__":
    unittest.main()
