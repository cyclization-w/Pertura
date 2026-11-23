"""The target gene's change (docs/adr/0031, D1a): one step for the formulas B.efficacy, E's pairs, B.escape and a
finding's guides used, each written once and named; each module keeps its formula, so its numbers stay."""
import tempfile
import unittest

import numpy as np

from test_common_response_and_field_measures import build_screen


def screen_with_a_knockdown(d):
    """T knocks its own gene T down to a quarter; the guides table names each target's gene."""
    import pandas as pd
    s, design = build_screen(d, {"T": (60, {"T": 0.25}), "N": (60, {})}, units=("u1", "u2"), controls=6)
    table = pd.read_csv(f"{d}/guides.tsv", sep="\t")
    table["gene"] = [t if t in ("T", "N") else "" for t in table["target"]]
    table.to_csv(f"{d}/guides.tsv", sep="\t", index=False)
    from pertura.question_modules.data import Screen
    return Screen(d, design, adata=s.adata), design


class TestTheFormulas(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.s, _ = screen_with_a_knockdown(self.tmp.name)
        self.t, self.r = self.s.label == "T", self.s.label == "control"
        self.x = self.s.gene("T")

    def tearDown(self):
        self.tmp.cleanup()

    def test_pooled_cpm_is_b_efficacys_formula(self):
        from pertura.steps import on_target
        lib = self.s.libsize
        cpm = lambda m: self.x[m].sum() / lib[m].sum() * 1e6  # noqa: E731
        expected = np.log2((cpm(self.t) + 1) / (cpm(self.r) + 1))
        self.assertAlmostEqual(on_target.change(self.s, "T", self.t, self.r), expected, places=10)
        self.assertLess(expected, -1.5)  # about a quarter

    def test_mean_cp10k_is_the_pairs_formula(self):
        from pertura.steps import on_target
        cp10k = self.x / np.maximum(self.s.libsize, 1) * 1e4
        expected = np.log2((cp10k[self.t].mean() + 0.1) / (cp10k[self.r].mean() + 0.1))
        self.assertAlmostEqual(on_target.change(self.s, "T", self.t, self.r, "mean_cp10k"), expected, places=10)

    def test_detection_and_the_missing_cases(self):
        from pertura.steps import on_target
        d = on_target.change(self.s, "T", self.t, self.r, "detection")
        self.assertAlmostEqual(d, (self.x[self.t] > 0).mean() - (self.x[self.r] > 0).mean())
        self.assertTrue(np.isnan(on_target.change(self.s, "NOT_A_GENE", self.t, self.r)))
        self.assertTrue(np.isnan(on_target.change(self.s, "T", np.zeros(len(self.t), bool), self.r)))
        with self.assertRaises(ValueError):
            on_target.change(self.s, "T", self.t, self.r, "median")

    def test_b_efficacy_reads_the_step_and_adds_another_formula_when_asked(self):
        from pertura.question_modules.b import own_gene_effects
        base = own_gene_effects(self.s)
        more = own_gene_effects(self.s, method="mean_cp10k")
        self.assertNotIn("change", base.columns)
        self.assertTrue(np.allclose(base["log2fc"], more["log2fc"], equal_nan=True))  # B.efficacy's numbers stay
        self.assertEqual(set(more["formula"]), {"mean_cp10k"})
        t = more[more["target"] == "T"]
        self.assertTrue((t["change"] < -1).all(), t)


if __name__ == "__main__":
    unittest.main()
