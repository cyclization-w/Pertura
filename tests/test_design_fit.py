"""docs/adr/0025, section 2 and its amendment 1: each module's one default test and a finding's checks, chosen from
the confirmed design before any result; C.genes' default is the design's lead, per target, and its Wilcoxon is
pt.genes' own (against the control cells of the target's own samples where the screen has two or more:
docs/build/gene_test_calibration_by_design_2026-10-31.md)."""
import importlib.util
import json
import os
import shutil
import tempfile
import unittest

import numpy as np
import pandas as pd

from pertura import design_fit as fit
from pertura.question_modules.data import Screen

HAS_SCANPY = importlib.util.find_spec("scanpy") is not None
HAS_R = shutil.which("Rscript") is not None or os.path.exists(
    __import__("pertura.envs", fromlist=["rscript"]).rscript())


def screen(folder, independent="mouse", units=("u1", "u2", "u3", "u4"), context=None):
    """test_question_modules' synthetic screen, with the design's replicate and the units changed."""
    from test_question_modules import synthetic_screen
    s, design = synthetic_screen(folder)
    keep = np.isin(s.unit, list(units))
    a = s.adata[keep].copy()
    design = json.loads(json.dumps(design))
    design["units"]["independent_unit"]["value"] = independent
    if context:
        design.setdefault("experiment", {})["context"] = {"value": context}
    return Screen(folder, design, adata=a), design


class TestTheGeneTable(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_three_or_more_biological_replicates_lead_with_pseudobulk(self):
        s, _ = screen(self.d)
        lead = fit.gene_lead(s, "A")
        self.assertEqual(lead["method"], "edger_ql")
        self.assertIn("4 mice hold both", lead["reason"])

    def test_a_target_in_fewer_replicates_leads_with_wilcoxon_against_its_samples_control_cells(self):
        s, _ = screen(self.d)  # B's cells are all in mouse u1, the control cells in all four
        lead = fit.gene_lead(s, "B")
        self.assertEqual(lead["method"], "wilcoxon_same_samples")
        self.assertIn("only 1 mouse", lead["reason"])

    def test_technical_units_lead_with_wilcoxon_against_the_control_cells_of_the_same_samples(self):
        s, _ = screen(self.d, independent="none")
        lead = fit.gene_lead(s, "A")
        self.assertEqual(lead["method"], "wilcoxon_same_samples")
        self.assertIn("the field's most common", lead["reason"])
        self.assertIn("the 4 samples are not biological replicates", lead["reason"])

    def test_donors_read_from_a_cell_column_are_biological(self):
        s, _ = screen(self.d, units=("u1", "u2"))  # two donors, as GSE301119 reads them from its cell table
        s.meta["unit_source"] = "column"
        s.design["units"]["independent_unit"]["value"] = "donor"
        s.design["units"]["unit_identification"] = {"value": "cell_table_column"}
        self.assertTrue(s.independent_units())
        self.assertIn("only 2 donors hold both", fit.gene_lead(s, "A")["reason"])

    def test_one_sample_leads_with_wilcoxon_against_every_control_cell(self):
        s, _ = screen(self.d, independent="none", units=("u1",))
        lead = fit.gene_lead(s, "A")
        self.assertEqual(lead["method"], "wilcoxon")
        self.assertIn("one sample", lead["reason"])

    def test_the_threshold_is_named_once(self):
        from pertura.question_modules import c
        s, _ = screen(self.d, units=("u1", "u2", "u3"))
        self.assertEqual(fit.gene_lead(s, "A")["method"], "edger_ql")
        self.assertTrue(c.enough_units(s, s.reference("control_guides")))
        s2, _ = screen(self.d, units=("u1", "u2"))
        self.assertEqual(fit.gene_lead(s2, "A")["method"], "wilcoxon_same_samples")  # two mice: on cells
        self.assertFalse(c.enough_units(s2, s2.reference("control_guides")))


class TestTheScoreTest(unittest.TestCase):
    def test_every_target_takes_its_own_lead_and_a_named_test_holds_for_all(self):
        from pertura.question_modules import c
        d = tempfile.mkdtemp()
        try:
            s, _ = screen(d)
            score = {"x": np.random.default_rng(0).normal(size=len(s.label))}
            _, per, _ = c.compare_scores_field(s, score, "control_guides")
            leads = {t: fit.score_lead(s, t)["method"] for t in per["target"]}
            words = {"linear_model": "OLS, sample as a covariate", "wilcoxon": "Wilcoxon rank-sum"}
            self.assertEqual(dict(zip(per["target"], per["test"])), {t: words[m] for t, m in leads.items()})
            _, per, _ = c.compare_scores_field(s, score, "control_guides", test="wilcoxon")
            self.assertEqual(set(per["test"]), {"Wilcoxon rank-sum"})
        finally:
            shutil.rmtree(d, ignore_errors=True)


class TestThePlanShowsTheTests(unittest.TestCase):
    def test_the_plan_names_each_lead_with_its_reason_from_the_cell_table(self):
        d = tempfile.mkdtemp()
        try:
            s, design = screen(d)
            obs = s.adata.obs.copy()
            obs.insert(0, "barcode", obs.index)
            obs.to_csv(os.path.join(d, "cells.tsv.gz"), sep="\t", index=False)
            out = fit.plan_leads(d, design)
            genes = {g["test"]: g for g in out["genes (C.genes, C.within_states, D.cutting_response)"]}
            self.assertEqual(genes["edger_ql"]["targets"], 2)  # A and C, in 4 mice
            self.assertEqual(genes["wilcoxon_same_samples"]["targets"], 1)  # B, in one mouse
            self.assertIn("only 1 mouse", genes["wilcoxon_same_samples"]["reason"])
            self.assertTrue(out["facts"]["biological_replicates"])
            self.assertEqual(out["facts"]["control_guides"], 2)
        finally:
            shutil.rmtree(d, ignore_errors=True)


class TestAFindingsChecks(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_a_gene_finding_with_two_guides(self):
        s, _ = screen(self.d)
        checks = {c["check"]: c for c in fit.checks("gene", s, "A")}
        self.assertEqual(checks["guides_agree"]["guides"], ["A_1", "A_2"])
        # the field's other tests the design admits beside the lead (edgeR-QL by mouse here)
        self.assertEqual([m["method"] for m in checks["tests"]["methods"]],
                         ["wilcoxon_same_samples", "wilcoxon", "mast", "pydeseq2"])
        self.assertNotIn("other_test", checks)
        self.assertEqual(checks["calibration"]["method"], "edger_ql")
        self.assertNotIn("half", checks)
        self.assertNotIn("deposit_calls", checks)

    def test_a_control_guide_is_never_a_targets_guide(self):
        s, _ = screen(self.d)
        lab = s.label.copy()
        ctrl = np.flatnonzero(s.label == "control")[:20]  # 20 control-guide cells labelled A
        lab[ctrl] = "A"
        s2 = s.with_labels(lab)
        self.assertEqual(fit.guides_with_cells(s2, "A"), ["A_1", "A_2"])

    def test_one_guide_asks_for_halves_of_the_units_only(self):
        s, _ = screen(self.d)  # B's one mouse gives no halves of the units
        checks = {c["check"]: c for c in fit.checks("gene", s, "B")}
        self.assertIn("1 guide", checks["guides_agree"]["not_run"])
        self.assertIn("halves of the units need 4", checks["half"]["not_run"])
        s2, _ = screen(self.d, context="cell_line")  # halves of the cells are not separate data, in vitro too
        self.assertIn("not separate data", {c["check"]: c for c in fit.checks("gene", s2, "B")}["half"]["not_run"])

    def test_a_calling_rule_adds_the_deposits_calls(self):
        s, _ = screen(self.d)
        self.assertIn("deposit_calls", [c["check"] for c in fit.checks("gene", s, "A", calling_rule=True)])

    def test_against_other_targets_has_no_calibration(self):
        s, _ = screen(self.d)
        checks = {c["check"]: c for c in fit.checks("gene", s, "A", reference="other_targets")}
        self.assertIn("not the control cells", checks["calibration"]["not_run"])

    def test_program_and_state_findings(self):
        s, _ = screen(self.d)
        prog = {c["check"]: c for c in fit.checks("gene_set", s, "A")}
        self.assertEqual([m["method"] for m in prog["tests"]["methods"]], ["wilcoxon"])
        # calibrated on control guides too, as the skeptic's null did in development session 1 (heme, a state)
        self.assertIn("calibration", prog)
        state = {c["check"]: c for c in fit.checks("state", s, "A")}
        self.assertEqual(list(state), ["guides_agree", "tests", "calibration"])
        # four mice hold both sides: propeller on the mice's proportions leads; Fisher on cells and the
        # Cochran-Mantel-Haenszel test over the mice beside it
        self.assertEqual(fit.lead("state", s, "A")["method"], "propeller")
        self.assertEqual([m["method"] for m in state["tests"]["methods"]], ["fisher_same_samples", "cmh"])

    def test_technical_samples_lead_with_fishers_test_on_cells(self):
        s, _ = screen(self.d, independent="none")  # four technical samples
        lead = fit.lead("state", s, "A")
        self.assertEqual(lead["method"], "fisher_same_samples")
        self.assertIn("not biological replicates", lead["reason"])

    def test_combinations_and_own_code_say_why_they_are_not_checked_here(self):
        s, _ = screen(self.d)
        for kind in ("interaction", "custom"):
            (c,) = fit.checks(kind, s, "A")
            self.assertTrue(c["not_run"])

    def test_depth_matching_is_not_an_automatic_check(self):
        """The field puts depth in the test or downsamples; the depth-matching tool stays in the notebook."""
        s, _ = screen(self.d)
        X = s.adata.X.tolil()
        for i in np.flatnonzero(s.cells_of("A")):
            X[i] = X[i] * 2
        s.adata.X = X.tocsr()
        s2 = Screen(self.d, s.design, adata=s.adata)
        self.assertNotIn("depth_matched", [c["check"] for c in fit.checks("gene", s2, "A")])


@unittest.skipUnless(HAS_SCANPY, "needs scanpy")
class TestCGenesDefault(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_technical_units_lead_with_wilcoxon_against_the_same_samples_and_find_bs_genes(self):
        from pertura import question_modules as qm
        s, design = screen(self.d, independent="none")
        r = qm.REGISTRY["C.genes"].run(s, design, os.path.join(self.d, "out"))
        self.assertEqual(r.diagnostics["method"], "auto")
        self.assertEqual(set(r.table["test"]), {"wilcoxon_same_samples"})
        self.assertIn("not biological replicates", r.diagnostics["leads"]["A"]["reason"])
        changed = r.tables["changed_genes"]
        self.assertTrue({f"b{i}" for i in range(5)} <= set(changed[changed["target"] == "B"]["gene"]))
        self.assertIn("by a test on cells (Wilcoxon, the field's most common) against the control cells of the "
                      "target's own samples", r.summary)

    def test_wilcoxon_on_cells_when_asked(self):
        from pertura import question_modules as qm
        s, design = screen(self.d, independent="none")
        r = qm.REGISTRY["C.genes"].run(s, design, os.path.join(self.d, "out"), method="wilcoxon")
        self.assertEqual(set(r.table["test"]), {"wilcoxon"})
        changed = r.tables["changed_genes"]
        self.assertTrue({f"b{i}" for i in range(5)} <= set(changed[changed["target"] == "B"]["gene"]))
        self.assertIn("Wilcoxon on cells, as asked", r.summary)

    def test_the_module_and_pt_genes_share_the_wilcoxon(self):
        from pertura import blocks
        from pertura.question_modules import c
        s, _ = screen(self.d, independent="none")
        mod, _ = c.genes_wilcoxon(s, ["A", "B"], "control_guides")
        blk, _, _ = blocks._genes(s, ["A", "B"], "control_guides", method="wilcoxon")
        cols = ["target", "gene", "effect", "p", "q"]
        pd.testing.assert_frame_equal(mod[cols].reset_index(drop=True), blk[cols].reset_index(drop=True))

    def test_the_parts_give_the_numbers_of_one_call(self):
        from pertura.question_modules import c
        s, _ = screen(self.d, independent="none")
        one, _ = c.genes_wilcoxon(s, ["A", "B", "C"], "control_guides", workers=1)
        parts, call = c.genes_wilcoxon(s, ["A", "B", "C"], "control_guides", workers=3)
        pd.testing.assert_frame_equal(one, parts)
        self.assertEqual(call["params"]["processes"], 3)
        rest, _ = c.genes_wilcoxon(s, ["A", "B"], "other_targets", workers=2)  # against the other targets' cells
        alone, _ = c.genes_wilcoxon(s, ["A", "B"], "other_targets", workers=1)
        pd.testing.assert_frame_equal(rest, alone)

    @unittest.skipUnless(HAS_R, "needs R (edgeR)")
    def test_biological_replicates_lead_with_pseudobulk_per_target(self):
        from pertura import question_modules as qm
        s, design = screen(self.d)
        r = qm.REGISTRY["C.genes"].run(s, design, os.path.join(self.d, "out"))
        tests = dict(zip(r.table["target"], r.table["test"]))
        self.assertEqual(tests.get("A"), "edger_ql")
        self.assertEqual(tests.get("B"), "wilcoxon_same_samples")  # B's cells are in one mouse: on cells
        self.assertEqual(dict(zip(r.table["target"], r.table["test"])).get("C"), "edger_ql")


if __name__ == "__main__":
    unittest.main()
