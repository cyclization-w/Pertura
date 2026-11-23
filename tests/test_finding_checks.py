"""docs/adr/0025, section 3: a recorded finding's checks, chosen from the design and run in code, each in its own
terms; a check without the data says why; checks that disagree are named, with no verdict."""
import importlib.util
import json
import os
import shutil
import tempfile
import unittest

import numpy as np

from pertura import finding_checks as fc
from pertura import record as rc
from pertura.question_modules.data import Screen

HAS_SCANPY = importlib.util.find_spec("scanpy") is not None
HAS_R = shutil.which("Rscript") is not None or os.path.exists(
    __import__("pertura.envs", fromlist=["rscript"]).rscript())


def spec(target, feature, direction, kind="gene", **more):
    return {"target": target, "feature": feature, "direction": direction, "kind": kind,
            "reference": "control_guides", **more}


WILCOXON = {"method": "wilcoxon", "reason": "a test on cells (Wilcoxon), the test the finding came from"}


@unittest.skipUnless(HAS_SCANPY, "needs scanpy")
class TestChecksOnCells(unittest.TestCase):
    """Technical units (lanes of one pool), a finding from Wilcoxon on cells against every control cell: the field's
    other tests (Wilcoxon against the control cells of the same samples, MAST, and pseudobulk by sample where the lanes
    allow it) beside it. The design's own lead there is Wilcoxon against the control cells of the same samples
    (TestTheDesignsLeadOnLanes); the checks' mechanics are tested on a Wilcoxon finding, which runs without R."""

    def setUp(self):
        from test_design_fit import screen
        self.d = tempfile.mkdtemp()
        self.s, _ = screen(self.d, independent="none", context="cell_line")

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def checks(self, sp, **kw):
        lead, out = fc.run_checks(self.s, sp, os.path.join(self.d, "work"), **kw)
        return lead, {c["check"]: c for c in out}

    def test_a_one_guide_finding_in_one_lane(self):
        lead, c = self.checks(spec("B", "b0", 1, lead=WILCOXON))  # B: one guide, its cells in lane u1 only
        self.assertEqual((lead["test"], lead["outcome"]), ("wilcoxon", "agrees"))
        self.assertIn("1 guide", c["guides_agree"]["why"])
        # one lane holds B: no pseudobulk; Wilcoxon against that lane's control cells and MAST beside the lead
        self.assertEqual([r["method"] for r in c["tests"]["rows"]], ["wilcoxon_same_samples", "mast"])
        # halves of the units need 4 units holding both sides; halves of the cells are not separate data
        self.assertIn("halves of the units need 4", c["half"]["why"])
        self.assertEqual(c["calibration"]["outcome"], "beyond the control groups")
        self.assertEqual(c["calibration"]["groups_calling_the_gene"], 0)
        self.assertFalse(fc.disagrees(lead, list(c.values())))
        words = fc.sentence(spec("B", "b0", 1), lead, list(c.values()))
        self.assertIn("each guide alone: not run (1 guide", words)
        self.assertIn("each half of the data: not run (1 units hold both sides", words)

    def test_the_checks_run_side_by_side_and_come_back_in_the_order_planned(self):
        """The checks are independent; side by side they take the time of the longest, not of all of them."""
        import threading
        import time
        from unittest import mock
        spans = {}

        def slow(c, *a, **k):
            start = time.monotonic()
            time.sleep(0.5)
            spans[c["check"]] = (start, time.monotonic(), threading.current_thread().name)
            return {"check": c["check"], "outcome": "agree"}

        with mock.patch.object(fc, "_one_check", slow):
            lead, out = fc.run_checks(self.s, spec("B", "b0", 1, lead=WILCOXON), os.path.join(self.d, "work"))
        planned = fc.fit.checks("gene", self.s, "B", "control_guides", WILCOXON)
        self.assertEqual([c["check"] for c in out], [c["check"] for c in planned])
        self.assertGreaterEqual(len(spans), 2, out)
        self.assertLess(max(s for s, _, _ in spans.values()), min(e for _, e, _ in spans.values()))  # overlapped
        self.assertEqual(len({n for _, _, n in spans.values()}), len(spans))

    def test_a_checks_parts_are_measured_side_by_side_and_kept_in_their_order(self):
        """A target's guides (or halves) are independent measurements; side by side they take the longest one's time."""
        import threading
        import time
        spans = {}

        def slow(g):
            start = time.monotonic()
            time.sleep(0.3)
            spans[g] = (start, time.monotonic(), threading.current_thread().name)
            return g * 2

        self.assertEqual(fc.side_by_side(slow, [3, 1, 2]), [6, 2, 4])
        self.assertLess(max(s for s, _, _ in spans.values()), min(e for _, e, _ in spans.values()))  # overlapped
        self.assertEqual(fc.side_by_side(slow, [5]), [10])  # one part: no thread

    def test_the_wrong_direction_is_named_not_judged(self):
        lead, c = self.checks(spec("B", "b0", -1, lead=WILCOXON))
        self.assertEqual(lead["outcome"], "other direction")
        self.assertTrue(fc.disagrees(lead, list(c.values())))

    @unittest.skipUnless(HAS_R, "needs R (edgeR)")
    def test_two_guides_and_pseudobulk_by_lane(self):
        lead, c = self.checks(spec("A", "s0", 1, lead=WILCOXON))  # s0 is raised in every target's cells, by both of A's guides
        self.assertEqual(lead["outcome"], "agrees")
        self.assertEqual(c["guides_agree"]["outcome"], "agree")
        self.assertEqual(sorted(c["guides_agree"]["parts"]), ["A_1", "A_2"])
        self.assertIsNotNone(c["guides_agree"]["parts"]["A_1"]["own_gene"])  # each guide shown with its knockdown
        self.assertLess(c["guides_agree"]["parts"]["A_1"]["own_gene"], c["guides_agree"]["parts"]["A_2"]["own_gene"])
        rows = {r["method"]: r for r in c["tests"]["rows"]}
        # the lead is Wilcoxon against every control cell
        self.assertEqual(sorted(rows), ["edger_ql", "mast", "pydeseq2", "wilcoxon_same_samples"])
        self.assertEqual(rows["edger_ql"]["outcome"], "agrees")
        self.assertIn("by sample", rows["edger_ql"]["label"])
        self.assertNotIn("control_groups", rows["edger_ql"])  # on the target only: the groups are the calibration's
        self.assertNotIn("half", c)  # two guides: no half

    def test_unequal_depth_plans_no_depth_matching(self):
        """Depth matching left the automatic checks (the open points' F5); pt.match_depth stays in the notebook."""
        X = self.s.adata.X.tolil()
        for i in np.flatnonzero(self.s.cells_of("B")):
            X[i] = X[i] * 2
        self.s.adata.X = X.tocsr()
        self.s = Screen(self.d, self.s.design, adata=self.s.adata)
        lead, c = self.checks(spec("B", "b0", 1, lead=WILCOXON))
        self.assertNotIn("depth_matched", c)

    def test_the_deposits_calls_for_a_calling_rule(self):
        lead, c = self.checks(spec("B", "b0", 1, lead=WILCOXON), deposit=self.s)
        self.assertEqual(c["deposit_calls"]["outcome"], "agrees")

    def test_a_program_score(self):
        up = [f"s{i}" for i in range(0, 10, 2)]
        lead, c = self.checks(spec("A", "s_up", 1, kind="gene_set", genes=up))
        self.assertEqual((lead["test"], lead["outcome"]), ("linear_model", "agrees"))
        self.assertEqual([(r["method"], r["outcome"]) for r in c["tests"]["rows"]], [("wilcoxon", "agrees")])
        self.assertEqual(c["guides_agree"]["outcome"], "agree")
        self.assertIn("calibration", c)  # control guides too (planted-effect test: test_common_response_and_field_measures)

    def test_a_state(self):
        rng = np.random.default_rng(1)
        lab = np.where(self.s.cells_of("A"), np.where(rng.random(len(self.s.label)) < 0.6, "S1", "S2"),
                       np.where(rng.random(len(self.s.label)) < 0.1, "S1", "S2"))
        lead, c = self.checks(spec("A", "S1", 1, kind="state", state_labels=lab))
        self.assertEqual((lead["test"], lead["outcome"]), ("fisher_same_samples", "agrees"))
        self.assertEqual(c["guides_agree"]["outcome"], "agree")
        self.assertEqual(sorted(c), ["calibration", "guides_agree", "tests"])
        # the tests by sample the four lanes allow beside Fisher's, each agreeing or saying why not
        self.assertEqual(sorted(r["method"] for r in c["tests"]["rows"]), ["cmh", "propeller"])
        for r in c["tests"]["rows"]:
            self.assertTrue(r["outcome"] == "agrees" or r.get("why"), r)

    def test_a_check_without_a_result_says_why_and_the_run_still_records(self):
        rec = rc.Record(os.path.join(self.d, "rec"))
        rec.append("question_planned", {"question_id": "C.states", "letter": "C", "item": "states",
                                        "because": "always"}, rc.PLAN)
        rec.append("finding_recorded", {"finding_id": "f1", "question_id": "C.states", "target": "A",
                                        "feature": "S1", "direction": "up", "population": "all", "run_ids": [],
                                        "kind": "state"}, rc.AGENT)
        lab = np.where(self.s.cells_of("A"), "S1", "S2")
        # A_2's cells only in a lane with no control cells: that guide gives no result
        unit = self.s.unit.copy()
        unit[self.s.cells_of("A") & (self.s.top_guide == "A_2")] = "u9"
        self.s.unit = unit
        out = fc.run(rec, self.s, spec("A", "S1", 1, kind="state", state_labels=lab), os.path.join(self.d, "o"), "f1")
        g = next(c for c in out["checks"] if c["check"] == "guides_agree")
        self.assertEqual(g["outcome"], "not run")
        self.assertIn("A_2", g["why"])
        self.assertIn("guides_agree", rec.state()["findings"]["f1"]["checks"])

    def test_a_state_the_labelling_does_not_have(self):
        lab = np.where(self.s.cells_of("A"), "S1", "S2")
        lead, _ = self.checks(spec("A", "cycling", 1, kind="state", state_labels=lab))
        self.assertEqual(lead["outcome"], "not run")
        self.assertIn("no state 'cycling'", lead["why"])

    def test_the_halves_cut_the_reference_too(self):
        unit = self.s.unit.copy()  # B's one guide over the four lanes: halves of the units
        b = np.flatnonzero(self.s.cells_of("B"))
        unit[b] = np.array(["u1", "u2", "u3", "u4"])[np.arange(len(b)) % 4]
        self.s.unit = unit
        lab = np.where(self.s.cells_of("B"), "S1", np.where(np.arange(len(self.s.label)) % 3 == 0, "S1", "S2"))
        _, c = self.checks(spec("B", "S1", 1, kind="state", state_labels=lab))
        refs = [v["reference_cells"] for v in c["half"]["parts"].values()]
        whole = int(self.s.reference("control_guides").sum())
        self.assertTrue(all(0 < r < whole for r in refs), refs)

    def test_a_check_that_fails_is_reported_not_raised(self):
        from unittest import mock
        with mock.patch.object(fc, "_calibrate_one", side_effect=RuntimeError("boom")):
            _, c = self.checks(spec("B", "b0", 1, lead=WILCOXON))
        self.assertEqual(c["calibration"]["outcome"], "not run")
        self.assertIn("it failed: RuntimeError: boom", c["calibration"]["why"])

    def test_combinations_and_own_code_are_not_checked_here(self):
        for kind in ("interaction", "custom"):
            lead, out = fc.run_checks(self.s, spec("A", "s0", 1, kind=kind), self.d)
            self.assertIsNone(lead)
            self.assertEqual(out[0]["outcome"], "not run")

    def test_the_run_records_each_check_by_the_tool(self):
        rec = rc.Record(os.path.join(self.d, "rec"))
        rec.append("question_planned", {"question_id": "C.genes", "letter": "C", "item": "genes",
                                        "because": "always"}, rc.PLAN)
        rec.append("finding_recorded", {"finding_id": "f1", "question_id": "C.genes", "target": "B", "feature": "b0",
                                        "direction": "up", "population": "all", "run_ids": [], "kind": "gene"},
                   rc.AGENT)
        out = fc.run(rec, self.s, spec("B", "b0", 1, lead=WILCOXON), os.path.join(self.d, "out"), "f1")
        f = rec.state()["findings"]["f1"]
        self.assertEqual(f["checks"]["lead"]["outcome"], "agrees")
        self.assertTrue(all(c["by"] == rc.TOOL for c in f["checks"].values()))
        self.assertIn("1 guide", f["checks"]["guides_agree"]["reason"])
        self.assertTrue(rc.checks_ran(f) and not rc.checks_disagree(f))
        with open(os.path.join(self.d, "out", "result.json"), encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["summary"], out["summary"])  # strict JSON: no NaN
        self.assertTrue(os.path.exists(out["figure"]))

    def test_a_saved_score_is_checked_as_a_set_score_and_several_targets_each_on_their_own(self):
        """A per-cell score the agent's notebook computed (kind score, its table saved with pt.save_table) goes through
        a gene set's checks; a claim on several targets is checked target by target, each check with its target."""
        import pandas as pd
        up = [f"s{i}" for i in range(0, 10, 2)]
        score = np.asarray(self.s.adata[:, up].X.mean(axis=1)).ravel()  # the agent's own score: the set's mean
        path = os.path.join(self.d, "own_score.tsv.gz")
        table = pd.DataFrame({"barcode": np.asarray(self.s.adata.obs_names).astype(str), "score": score})
        table.iloc[::-1].to_csv(path, sep="\t", index=False)  # in another order: read by barcode
        sp = spec("A", "table:own_score", 1, kind="score", score_table=path, values=fc.score_values(path))
        lead, c = self.checks(sp)
        self.assertEqual((lead["test"], lead["outcome"]), ("linear_model", "agrees"))
        self.assertEqual(c["guides_agree"]["outcome"], "agree")
        self.assertEqual(c["calibration"]["check"], "calibration")
        rec = rc.Record(os.path.join(self.d, "rec"))
        rec.append("question_planned", {"question_id": "C.genes", "letter": "C", "item": "genes",
                                        "because": "always"}, rc.PLAN)
        rec.append("finding_recorded", {"finding_id": "f2", "question_id": "C.genes", "target": "A",
                                        "targets": ["A", "B"], "feature": "table:own_score", "direction": "up",
                                        "population": "all", "run_ids": [], "kind": "score"}, rc.AGENT)
        out = fc.run(rec, self.s, {**sp, "targets": ["A", "B"]}, os.path.join(self.d, "out2"), "f2")
        f = rec.state()["findings"]["f2"]
        self.assertEqual(sorted(f["checks_by_target"]), ["A", "B"])
        self.assertEqual(f["checks"], f["checks_by_target"]["A"])  # the first target's are the finding's
        self.assertEqual(sorted(out["targets"]), ["A", "B"])
        self.assertTrue(rc.checks_ran(f))
        with self.assertRaises(rc.RecordError):  # a check names one of the claim's targets
            rec.append("check_recorded", {"finding_id": "f2", "check": "lead", "outcome": "agrees", "target": "C"},
                       rc.TOOL)


class TestTheSentence(unittest.TestCase):
    """The checks' sentence for each kind of calibration: changed genes for a gene finding, effects otherwise."""

    LEAD = {"outcome": "agrees", "test": "wilcoxon", "effect": 1.0, "q": 1e-5, "cells": 160}

    def test_a_state_findings_calibration_is_told_by_effects(self):
        cal = {"check": "calibration", "groups": 5, "groups_as_far": 0, "groups_95th_percentile_effect": 0.12,
               "target_effect": 1.0, "outcome": "beyond the control groups"}
        words = fc.sentence(spec("A", "S1", 1, kind="state"), self.LEAD, [cal])
        self.assertIn("5 groups (95th percentile of their effects 0.12; 0 go as far its way; the finding's effect 1: "
                      "beyond the control groups)", words)

    def test_a_gene_findings_calibration_is_told_by_changed_genes(self):
        cal = {"check": "calibration", "groups": 5, "discoveries": 4939, "groups_95th_percentile": 1200.0,
               "target_genes_changed": 300, "groups_calling_the_gene": 2,
               "outcome": "within the control groups' range"}
        words = fc.sentence(spec("A", "g1", 1), self.LEAD, [cal])
        self.assertIn("4939 discoveries in 5 groups (95th percentile 1200 genes; the target changes 300", words)

    def test_the_report_gives_each_targets_checks(self):
        from pertura import results_report
        lead = {"outcome": "agrees", "numbers": {"test": "linear_model", "cells": 50, "effect": 0.3}, "by": rc.TOOL}
        cal = {"outcome": "beyond the control groups", "numbers": {"groups": 20, "groups_as_far": 0}, "by": rc.TOOL}
        f = {"target": "A", "targets": ["A", "B"], "feature": "table:own_score", "direction": "down",
             "checks": {"lead": lead, "calibration": cal},
             "checks_by_target": {"A": {"lead": lead, "calibration": cal}}}
        words = results_report.checks_sentence("f2", f)
        self.assertIn("A on own_score (down), by the linear model with the sample (50 cells; effect 0.3): agrees", words)
        self.assertIn("groups of control guides through the same test: 0 of 20 groups go as far its way", words)
        self.assertIn("B: its checks have not run [finding:f2].", words)
        self.assertEqual(results_report.finding_name(f), "A, B on own_score (down)")


@unittest.skipUnless(HAS_SCANPY and HAS_R, "needs scanpy and R (edgeR)")
class TestChecksByReplicate(unittest.TestCase):
    """Four mice: edgeR-QL by mouse leads; Wilcoxon on cells (against the control cells of the same mice, and against
    every control cell) among the other tests; each guide by the design's test."""

    def test_a_gene_finding_by_mouse(self):
        from test_design_fit import screen
        d = tempfile.mkdtemp()
        try:
            s, _ = screen(d)
            lead, out = fc.run_checks(s, spec("A", "s0", 1), os.path.join(d, "work"))
            c = {x["check"]: x for x in out}
            self.assertEqual((lead["test"], lead["outcome"]), ("edger_ql", "agrees"))
            rows = {r["method"]: r for r in c["tests"]["rows"]}
            self.assertEqual(sorted(rows), ["mast", "pydeseq2", "wilcoxon", "wilcoxon_same_samples"])
            self.assertEqual(rows["wilcoxon"]["outcome"], "agrees")
            self.assertEqual(rows["wilcoxon_same_samples"]["outcome"], "agrees")
            self.assertEqual(c["guides_agree"]["outcome"], "agree")
            self.assertEqual(c["calibration"]["outcome"], "beyond the control groups")
        finally:
            shutil.rmtree(d, ignore_errors=True)


@unittest.skipUnless(HAS_SCANPY and HAS_R, "needs scanpy and R (Seurat, edgeR)")
class TestTheDesignsLeadOnLanes(unittest.TestCase):
    """Lanes of one pool: the design's lead is Wilcoxon on cells against the control cells of the same lanes
    (docs/adr/0025, amendments 1 and 2); pseudobulk by lane is among the field's other tests beside it."""

    def test_a_gene_finding_on_lanes(self):
        from test_design_fit import screen
        d = tempfile.mkdtemp()
        try:
            s, _ = screen(d, independent="none", context="cell_line")
            lead, out = fc.run_checks(s, spec("A", "s0", 1), os.path.join(d, "work"))
            c = {x["check"]: x for x in out}
            self.assertEqual((lead["test"], lead["outcome"]), ("wilcoxon_same_samples", "agrees"))
            self.assertIn("not biological replicates", lead["reason"])
            rows = {r["method"]: r for r in c["tests"]["rows"]}
            self.assertEqual(rows["edger_ql"]["outcome"], "agrees")
            self.assertEqual(c["guides_agree"]["outcome"], "agree")
            self.assertNotEqual(c["calibration"]["outcome"], "not run")
        finally:
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
