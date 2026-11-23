"""Quality control (pertura.quality, the design's data.qc): the cells' four measures, the rule the user confirms (fixed
thresholds, median absolute deviations within each capture, a column of one's own), the cells whose unit could not be
told left out by default, and every module leaving out what fails."""
import os
import tempfile
import unittest

import numpy as np
import pandas as pd

from pertura import quality
from pertura import question_modules as qm


def screen(folder, rule=None):
    from test_common_response_and_field_measures import build_screen
    design = {"perturbation": {"modality": {"value": "CRISPRi"}}, "units": {"independent_unit": {"value": "none"}}}
    if rule is not None:
        design["data"] = {"qc": {"value": rule, "status": "confirmed"}}
    # mt-Co1 at its base level in every group: a gene the mitochondrial share reads
    return build_screen(folder, {"T1": (40, {"mt-Co1": 1.0}), "T2": (40, {})}, units=("u1", "u2"), design=design)


class TestTheMeasures(unittest.TestCase):
    def test_four_measures_per_cell(self):
        with tempfile.TemporaryDirectory() as d:
            s, _ = screen(d)
            m = quality.measures(s)
        self.assertEqual(list(m.columns), list(quality.MEASURES))
        self.assertEqual(len(m), len(s.label))
        self.assertTrue((m["n_counts"] > 0).all() and (m["n_genes"] > 0).all())
        self.assertTrue(((m["pct_mito"] > 0) & (m["pct_mito"] < 1)).all())  # mt-Co1 is one of the genes


class TestReceptorGenes(unittest.TestCase):
    # development run on GSE267740: the states were clustered on the TCR V genes too, and the shifted states were
    # named by V pairs (S14 Trbv3 Trav6-1): clones, not states (the knowledge base's PF4)
    def test_receptor_segments_are_named_and_other_genes_are_not(self):
        # Tcrg-V4: the mouse gamma chain's names, missed on GSE267740 on 667f20a
        named = ["TRAV12-1", "Trbv3", "Trav9d-1", "Traj57", "TRAC", "TRBC1", "Trdc", "IGHV1-2", "Igkc", "Iglv1",
                 "Tcrg-V4", "Tcrg-C1"]
        other = ["TRADD", "Traf6", "TRAPPC1", "Trdn", "IGHMBP2", "Igll1", "Ifng", "Tbx21"]
        self.assertTrue(quality.receptor_genes(named).all())
        self.assertFalse(quality.receptor_genes(other).any())

    @unittest.skipUnless(__import__("importlib").util.find_spec("scanpy") is not None, "needs scanpy")
    def test_variable_genes_leave_the_receptor_segments_out(self):
        import anndata as ad
        import scanpy as sc
        rng = np.random.default_rng(0)
        X = rng.poisson(2.0, (300, 40)).astype(np.float32)
        X[:100, 0] += rng.poisson(30.0, 100)  # a clone's V gene, the most variable of all
        a = ad.AnnData(X, var=pd.DataFrame(index=["Trbv3"] + [f"g{i}" for i in range(39)]))
        sc.pp.log1p(a)  # the seurat flavour reads log values, as every caller gives it
        quality.variable_genes(a, n_top_genes=10)
        self.assertFalse(a.var.loc["Trbv3", "highly_variable"])
        self.assertGreater(int(a.var["highly_variable"].sum()), 0)
        self.assertEqual(a.uns["receptor_segments_left_out"], ["Trbv3"])  # said, not done silently


class TestTheRule(unittest.TestCase):
    def test_a_rule_is_checked_before_it_is_applied(self):
        self.assertEqual(quality.validate(None), [])
        self.assertEqual(quality.validate({"filters": [{"metric": "n_counts", "min": 500},
                                                       {"metric": "pct_mito", "mads": 3}]}), [])
        bad = quality.validate({"filters": [{"metric": "cell_cycle", "max": 1}, {"metric": "n_genes"},
                                            {"metric": "pct_mito", "max": "ten"}, {"metric": "n_counts", "mads": 3,
                                                                                   "side": "up"}], "other": 1})
        self.assertEqual(len(bad), 5)
        self.assertTrue(any("none of Pertura's measures" in p for p in bad))
        self.assertTrue(any("give min, max, mads or drop" in p for p in bad))

    def test_by_default_only_the_cells_without_a_unit_are_left_out(self):
        with tempfile.TemporaryDirectory() as d:
            s, _ = screen(d)
        self.assertEqual(set(s.qc_reason), {""})  # every cell has its unit here
        self.assertIn("no filter on the cells' measures", quality.describe(None))

    def test_the_cells_without_a_unit_are_named_only_where_a_cell_can_lack_one(self):
        self.assertIn("(no value in the column 'hashtag') are left out", quality.describe(None, "hashtag"))
        self.assertEqual(quality.describe(None, None), "no filter on the cells' measures")  # each sample one unit

    def test_a_fixed_threshold_leaves_out_the_cells_below_it_with_their_reason(self):
        with tempfile.TemporaryDirectory() as d:
            s0, _ = screen(d)
            cut = float(np.median(s0.libsize))
        with tempfile.TemporaryDirectory() as d:
            s, design = screen(d, {"filters": [{"metric": "n_counts", "min": cut}]})
            r = qm.REGISTRY["A.what_was_done"].run(s, design, os.path.join(d, "a"))
        failed = s.label == quality.FAILED
        self.assertTrue(0 < failed.mean() < 1)
        self.assertTrue((s.libsize[failed] < cut).all())
        self.assertEqual(set(s.qc_reason[failed]), {f"n_counts below {cut}"})
        self.assertNotIn(quality.FAILED, s.targets(1))  # no module reads them as a target or a control
        self.assertEqual(r.diagnostics["left_out_by_reason"], {f"n_counts below {cut}": int(failed.sum())})
        self.assertIn("quality", r.tables)
        self.assertIn("failed QC", list(r.table.columns))

    def test_the_rules_outcome_is_among_the_cells_columns_of_the_anndata(self):
        """An agent's own code read pt.screen().adata.obs["label"], which holds no rule, and counted the cells every
        module leaves out (2,297 cells against the module's 1,949)."""
        with tempfile.TemporaryDirectory() as d:
            s0, _ = screen(d)
            cut = float(np.median(s0.libsize))
        from pertura.question_modules.data import Screen
        with tempfile.TemporaryDirectory() as d:
            s, _ = screen(d, {"filters": [{"metric": "n_counts", "min": cut}]})
            before = list(s.adata.obs["label_before_qc"])
            again = Screen(s.dir, s.design, adata=s.adata)  # read again, the build's label is not lost
        obs = s.adata.obs
        self.assertEqual(list(obs["label_after_qc"]), list(s.label))
        self.assertEqual(list(obs["qc_reason"]), list(s.qc_reason))
        failed = obs["label_after_qc"] == quality.FAILED
        self.assertTrue(0 < failed.mean() < 1)
        # label is the rule's too, as ~100 notebook runs read it; the build's label stays beside it
        self.assertEqual(list(obs["label"]), list(s.label))
        self.assertNotIn(quality.FAILED, set(obs.loc[failed, "label_before_qc"]))
        self.assertEqual(list(again.adata.obs["label_before_qc"]), before)
        self.assertEqual(list(again.label), list(s.label))
        sub = s.subset(cells=list(obs.index[~failed][:10]))
        self.assertEqual(set(sub.adata.obs["label_after_qc"]) & {quality.FAILED}, set())

    def test_median_absolute_deviations_are_read_within_each_capture(self):
        with tempfile.TemporaryDirectory() as d:
            s, _ = screen(d, {"filters": [{"metric": "n_counts", "mads": 1}]})
        failed = s.qc_reason != ""
        self.assertTrue(failed.any())
        x = np.log1p(s.libsize)
        for c in set(s.capture):  # each capture's lowest cells, against its own median
            m = s.capture == c
            med = np.median(x[m])
            self.assertTrue((x[m & failed] < med).all())

    def test_a_column_of_ones_own_comes_with_its_table(self):
        with tempfile.TemporaryDirectory() as d:
            s0, _ = screen(d)
            path = os.path.join(d, "doublets.tsv")
            score = np.zeros(len(s0.label))
            score[:5] = 0.9
            pd.DataFrame({"barcode": s0.barcodes, "doublet_score": score}).to_csv(path, sep="\t", index=False)
            os.makedirs(os.path.join(d, "again"))
            s, _ = screen(os.path.join(d, "again"), {"filters": [{"metric": "doublet_score", "max": 0.5,
                                                                    "table": path}]})
        self.assertEqual(int((s.qc_reason != "").sum()), 5)
        self.assertIn("doublet_score above 0.5", s.qc_reason[0])

    def test_cells_without_a_unit_fail_by_default_with_why_and_can_be_kept(self):
        with tempfile.TemporaryDirectory() as d:
            s, _ = screen(d)
            s.unit = np.where(np.arange(len(s.unit)) < 6, "u1:unassigned", s.unit)
            reason, _ = quality.failures(s)
            kept, _ = quality.failures(s, {"keep_cells_without_a_unit": True})
        self.assertEqual(list(reason[:6]), ["no unit"] * 6)  # whatever the unit column was made from
        self.assertEqual(set(reason[6:]), {""})
        self.assertEqual(set(kept), {""})


if __name__ == "__main__":
    unittest.main()
