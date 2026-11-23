"""A graded response (docs/adr/0044, D1): whether each target's perturbation score (Mixscale's, the downstream
signature) follows its strength on its own target gene, as published screens ask whether downstream effects scale
with the on-target change."""
import os
import tempfile
import unittest

import numpy as np
import pandas as pd

from test_question_modules import synthetic_screen

from pertura.question_modules.b import graded_by_score


class TestAGradedResponse(unittest.TestCase):
    def test_a_score_that_deepens_with_the_knockdown_is_found_and_a_flat_one_is_not(self):
        with tempfile.TemporaryDirectory() as d:
            folder = os.path.join(d, "screen")
            os.makedirs(folder)
            s, _ = synthetic_screen(folder)
        names = np.asarray(s.adata.obs_names).astype(str)
        libsize = np.maximum(np.asarray(s.libsize, dtype=float), 1)
        a_gene = np.log1p(s.gene("A") * 1e4 / libsize)
        rng = np.random.default_rng(0)
        # A's score rises as its own gene falls; B's score is noise
        score = np.where(s.label == "A", 2 - a_gene + rng.normal(0, 0.2, len(names)), rng.normal(0, 1, len(names)))
        cells = pd.DataFrame({"barcode": names, "label": s.label, "score": score})
        table, lines = graded_by_score(s, cells)
        t = table.set_index("target")
        self.assertLess(t.loc["A", "rho"], -0.5)
        self.assertLess(t.loc["A", "q"], 0.05)
        self.assertGreater(t.loc["B", "q"], 0.05)
        self.assertTrue(lines and lines[0].startswith("A: the score rises as A falls"), lines)
        thirds = [float(x) for x in t.loc["A", "gene_by_score_third"].split(", ")]
        self.assertGreater(thirds[0], thirds[-1])  # the gene lowest in the third of the highest scores


if __name__ == "__main__":
    unittest.main()
