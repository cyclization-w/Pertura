"""Proteins per target (docs/adr/0044, D1): the antibody counts the screen object keeps (obsm antibody_counts),
normalised as Seurat's multimodal vignette does (NormalizeData, CLR, margin 2: each cell across its proteins), each
tested per target against the control cells by the scores' test, in pt.scores and C.programs."""
import os
import tempfile
import unittest

import numpy as np
import pandas as pd

from test_question_modules import synthetic_screen

import pertura.blocks as pt
from pertura.question_modules import REGISTRY


def with_proteins(s):
    """Two proteins on the synthetic screen: CD25 raised fourfold in target B's cells, CD3 alike in every cell."""
    rng = np.random.default_rng(0)
    obs = s.adata.obs
    raised = np.where(obs["label"] == "B", 4.0, 1.0)
    s.adata.obsm["antibody_counts"] = pd.DataFrame({"CD25": rng.poisson(20 * raised), "CD3": rng.poisson(50,
                                                                                                      len(obs))},
                                                   index=obs.index)
    return s


class TestProteins(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        folder = os.path.join(self.tmp.name, "screen")
        os.makedirs(folder)
        self.screen, self.design = synthetic_screen(folder)

    def tearDown(self):
        self.tmp.cleanup()

    def test_clr_is_seurats_each_cell_across_its_proteins(self):
        s = self.screen
        s.adata.obsm["antibody_counts"] = pd.DataFrame(np.tile([0, 1, 3], (len(s.adata.obs), 1)),
                                                       index=s.adata.obs.index, columns=["P0", "P1", "P2"])
        values = pt._protein_values(s, "all")
        # Seurat: log1p(x / exp(sum(log1p(x[x > 0])) / length(x))); for counts 0, 1 and 3 the divisor is (2 x 4)^(1/3), 2
        g = np.exp((np.log1p(1) + np.log1p(3)) / 3)
        np.testing.assert_allclose([values[p][0] for p in ("P0", "P1", "P2")], np.log1p(np.array([0, 1, 3]) / g))
        with self.assertRaisesRegex(ValueError, r"no protein \['CD99'\]"):
            pt._protein_values(s, ["CD99"])

    def test_the_raised_protein_is_found_in_its_target_and_the_flat_one_nowhere(self):
        s = with_proteins(self.screen)
        with pt.inside(s, os.path.join(self.tmp.name, "blocks")):
            res = pt.scores(proteins=["CD25", "CD3"], targets=["A", "B"])
        t = res.table.set_index(["target", "score"])
        self.assertGreater(t.loc[("B", "CD25"), "effect"], 0)
        self.assertLess(t.loc[("B", "CD25"), "q"], 0.05)
        self.assertGreater(t.loc[("A", "CD25"), "q"], 0.05)

    def test_c_programs_reads_the_proteins_or_says_there_are_none(self):
        m = REGISTRY["C.programs"]
        with self.assertRaisesRegex(ValueError, "proteins is used only with gene_sets"):
            m.check({"gene_sets": "hallmark", "proteins": ["CD25"]})
        r = m.run(self.screen, self.design, os.path.join(self.tmp.name, "none"), gene_sets="proteins")
        self.assertEqual(r.status, "not_enough_data")
        self.assertIn("no antibody counts", r.summary)
        r = m.run(with_proteins(self.screen), self.design, os.path.join(self.tmp.name, "some"), gene_sets="proteins")
        hit = r.table[(r.table["target"] == "B") & (r.table["score"] == "CD25")]
        self.assertLess(float(hit["q"].iat[0]), 0.05)


if __name__ == "__main__":
    unittest.main()
