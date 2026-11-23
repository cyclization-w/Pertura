"""C.genes' test on cells where 3 biological replicates do not hold both sides
(docs/build/gene_test_calibration_by_design_2026-10-31.md): Wilcoxon against the control cells of the target's own
samples, in its mix (wilcoxon_same_samples), so that a sample's difference from the others is not read as a
target's genes; on one sample it is Wilcoxon against every control cell."""
import importlib.util
import inspect
import os
import shutil
import tempfile
import unittest

import numpy as np
import pandas as pd
import scipy.sparse as sp

HAS_SCANPY = importlib.util.find_spec("scanpy") is not None
SAMPLE_GENES = [f"g{i}" for i in range(10)]


def screen_with_a_sample_effect(folder, independent="none", units=("u1", "u2", "u3", "u4")):
    """test_design_fit's screen (technical samples by default), with mouse u1's cells, of every label, raising genes
    g0-g9 four times: target B's cells are all in u1, the control cells in every sample."""
    from test_design_fit import screen
    from pertura.question_modules.data import Screen
    s, design = screen(folder, independent=independent, units=units)
    a = s.adata.copy()
    X = a.X.toarray()
    u1 = (a.obs["unit"] == "u1").to_numpy()
    cols = [a.var_names.get_loc(g) for g in SAMPLE_GENES]
    X[np.ix_(u1, cols)] += np.random.default_rng(1).poisson(12.0, size=(int(u1.sum()), len(cols)))
    a.X = sp.csr_matrix(X.astype(np.float32))
    return Screen(folder, design, adata=a), design


def changed(table, target, q=0.05):
    return set(table.loc[(table["target"] == target) & (table["q"] < q), "gene"].astype(str))


def raised(table, target, q=0.05):
    """The genes called up. B's own raised genes (b0-b4) take a share of its cells' counts, so after the library-size
    normalisation every other gene reads a little lower in B's cells: the sample's genes are read as up or not."""
    return set(table.loc[(table["target"] == target) & (table["q"] < q) & (table["effect"] > 0), "gene"].astype(str))


@unittest.skipUnless(HAS_SCANPY, "needs scanpy")
class TestTheTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_a_samples_difference_is_not_read_as_the_targets_genes(self):
        from pertura.question_modules import c
        s, _ = screen_with_a_sample_effect(self.d)
        every, _ = c.genes_wilcoxon(s, ["B"], "control_guides")
        same, call = c.genes_wilcoxon_same_samples(s, ["B"], "control_guides")
        self.assertGreaterEqual(len(raised(every, "B") & set(SAMPLE_GENES)), 8)  # u1's genes read as B's
        self.assertEqual(raised(same, "B") & set(SAMPLE_GENES), set())
        self.assertTrue({f"b{i}" for i in range(5)} <= changed(same, "B"))  # B's own genes stay
        # B's 60 cells are all in u1: u1's 80 control cells are its reference
        self.assertEqual(call["matched"]["B"], {"cells": 60, "reference_cells": 80, "cells_left_out": 0})
        self.assertEqual(set(same["test"]), {"wilcoxon_same_samples"})

    def test_a_gene_seen_in_too_few_cells_on_both_sides_is_not_tested(self):
        """Seurat's min.pct: against 500 reference cells at zero, the tie-corrected rank test gives a gene seen in one
        of 30 target cells a p near 1e-5, so a gene detected in fewer than 10% of the cells on both sides is not
        tested, against the reference or against the rest; one detected in the reference alone is; q is
        Benjamini-Hochberg over the genes tested."""
        from pertura.question_modules import c
        X = np.random.default_rng(0).poisson(1.0, (530, 40)).astype(np.float32)  # genes most cells detect
        one, ref = np.zeros((530, 1), np.float32), np.zeros((530, 1), np.float32)
        one[0], ref[100:160] = 3.0, 2.0  # one target cell; 60 of the 500 reference cells
        X = sp.csr_matrix(np.log1p(np.hstack([X, one, ref])))
        genes = [f"g{i}" for i in range(40)] + ["one_cell", "reference_only"]
        for labels, reference in ((["T"] * 30 + ["control"] * 500, "control"), (["T"] * 30 + ["U"] * 500, "rest")):
            t = c._wilcoxon_part(X, np.array(labels), genes, ["T"], reference)
            self.assertEqual(set(t["names"]), set(genes) - {"one_cell"}, reference)
            np.testing.assert_allclose(t["pvals_adj"], c.bh(t["pvals"].to_numpy()))

    def test_on_one_sample_it_is_wilcoxon_against_every_control_cell(self):
        from pertura.question_modules import c
        from test_design_fit import screen
        s, _ = screen(self.d, independent="none", units=("u1",))
        every, _ = c.genes_wilcoxon(s, ["A", "B"], "control_guides")
        same, _ = c.genes_wilcoxon_same_samples(s, ["A", "B"], "control_guides")
        cols = ["target", "gene", "effect", "p", "q"]
        order = ["target", "gene"]
        pd.testing.assert_frame_equal(every[cols].sort_values(order).reset_index(drop=True),
                                      same[cols].sort_values(order).reset_index(drop=True))

    def test_the_reference_is_drawn_from_each_sample_in_the_targets_mix(self):
        from pertura.question_modules import c
        from test_design_fit import screen
        s, _ = screen(self.d, independent="none")
        lab = s.label.copy()
        u2 = np.flatnonzero((s.label == "control") & (s.unit == "u2"))
        lab[u2[:75]] = "none"  # u2 keeps 5 control cells, fewer than a sample needs
        s2 = s.with_labels(lab)
        _, call = c.genes_wilcoxon_same_samples(s2, ["A"], "control_guides")
        # A's 80 cells in u2 are left out; in u1, u3 and u4 its 80 cells each meet their samples' 80 control cells
        self.assertEqual(call["matched"]["A"], {"cells": 240, "reference_cells": 240, "cells_left_out": 80})
        self.assertNotIn("not_fitted", call)

    def test_a_target_left_with_too_few_cells_is_not_fitted(self):
        from pertura.question_modules import c
        from pertura.steps import genes
        from test_design_fit import screen
        s, _ = screen(self.d, independent="none")
        lab = s.label.copy()
        lab[(s.label == "control") & (s.unit == "u1")] = "none"  # B's one sample keeps no control cell
        table, call = c.genes_wilcoxon_same_samples(s.with_labels(lab), ["A", "B"], "control_guides")
        self.assertEqual(call["not_fitted"], ["B"])
        self.assertEqual(set(table["target"]), {"A"})
        self.assertEqual(genes.fitted("wilcoxon_same_samples", table, ["A", "B"], [call]), ["A"])

    def test_the_parts_give_the_numbers_of_one_process(self):
        from pertura.question_modules import c
        s, _ = screen_with_a_sample_effect(self.d)
        one, _ = c.genes_wilcoxon_same_samples(s, ["A", "B", "C"], "control_guides", workers=1)
        parts, call = c.genes_wilcoxon_same_samples(s, ["A", "B", "C"], "control_guides", workers=3)
        pd.testing.assert_frame_equal(one, parts)
        self.assertEqual(call["params"]["processes"], 3)


@unittest.skipUnless(HAS_SCANPY, "needs scanpy")
class TestWhereItLeads(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_c_genes_leads_with_it_on_technical_samples_and_wilcoxon_on_every_cell_reads_the_sample(self):
        from pertura import question_modules as qm
        s, design = screen_with_a_sample_effect(self.d)
        r = qm.REGISTRY["C.genes"].run(s, design, os.path.join(self.d, "auto"))
        self.assertEqual(set(r.table["test"]), {"wilcoxon_same_samples"})
        self.assertIn("against the control cells of the target's own samples", r.diagnostics["leads"]["B"]["reason"])
        mine = r.tables["changed_genes"]
        self.assertEqual(raised(mine, "B") & set(SAMPLE_GENES), set())
        self.assertTrue({f"b{i}" for i in range(5)} <= raised(mine, "B"))
        asked = qm.REGISTRY["C.genes"].run(s, design, os.path.join(self.d, "every"), method="wilcoxon")
        self.assertGreaterEqual(len(raised(asked.tables["changed_genes"], "B") & set(SAMPLE_GENES)), 8)

    def test_its_calibration_puts_the_control_guide_groups_through_the_same_test(self):
        from pertura import blocks as pt
        s, _ = screen_with_a_sample_effect(self.d)
        with pt.inside(s, os.path.join(self.d, "blocks")):
            res = pt.genes()
            cal = pt.calibrate(res)
        self.assertEqual(set(res.table["test"]), {"wilcoxon_same_samples"})
        self.assertEqual(set(cal.table["test"]), {"wilcoxon_same_samples"})
        self.assertEqual(set(res.extra["matched"]), set(res.table["target"]))

    def test_the_notebook_and_the_finding_checks_name_it(self):
        from pertura import blocks
        from pertura import design_fit as fit
        from test_design_fit import screen
        self.assertEqual(inspect.signature(blocks.genes).parameters["method"].default, "wilcoxon_same_samples")
        s, _ = screen(self.d, independent="none")
        self.assertEqual(fit.gene_lead(s, "A")["method"], "wilcoxon_same_samples")
        checks = {c["check"]: c for c in fit.checks("gene", s, "A")}
        # beside it: Wilcoxon against every control cell, MAST, and pseudobulk by sample (4 samples hold both sides)
        self.assertEqual([m["method"] for m in checks["tests"]["methods"]], ["wilcoxon", "mast", "edger_ql", "pydeseq2"])


class TestTheSummaryGivesEachTestsCalibration(unittest.TestCase):
    def test_two_tests_each_with_its_own_groups(self):
        from pertura.question_modules.c import _calibration_words
        each = {"edger_ql": {"groups": 9, "group_size": 3, "discoveries": 7, "targets": 41,
                             "targets_beyond_control_groups_95th": 6, "control_groups_95th_percentile": 3.2},
                "wilcoxon_same_samples": {"groups": 9, "group_size": 3, "discoveries": 8859, "targets": 11,
                                          "targets_beyond_control_groups_95th": 2,
                                          "control_groups_95th_percentile": 2474.0}}
        words = _calibration_words({**each["edger_ql"], "not_run": None, "by_method": each})
        self.assertIn("edgeR-QL pseudobulk by sample: 9 groups gave 7 discoveries, and 6 of its 41 targets", words)
        self.assertIn("against the control cells of the same samples: 9 groups gave 8859 discoveries, and 2 of its "
                      "11 targets", words)
        one = _calibration_words({**each["edger_ql"], "not_run": None})
        self.assertIn("9 groups of 3 control guides tested as targets gave 7 discoveries", one)


if __name__ == "__main__":
    unittest.main()
