"""Gene-set scores on a column-stored matrix (docs/build/gene_set_scores_on_a_column_stored_matrix_proposal.md):
scanpy_scores gives each set the scores scanpy's score_genes gives on the matrix stored by rows."""
import importlib.util
import os
import shutil
import tempfile
import unittest

import numpy as np

HAS_SCANPY = importlib.util.find_spec("scanpy") is not None


@unittest.skipUnless(HAS_SCANPY, "needs scanpy")
class TestScoresOnColumns(unittest.TestCase):
    def test_each_set_s_scores_are_those_of_the_matrix_stored_by_rows(self):
        import anndata as ad
        import pandas as pd
        import scanpy as sc
        import scipy.sparse as sp
        from test_genes_against_same_sample_controls import screen_with_a_sample_effect
        from pertura.question_modules import c
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        os.makedirs(os.path.join(d, "screen"))  # the screen's files go there
        s, _ = screen_with_a_sample_effect(os.path.join(d, "screen"))
        genes = [str(g) for g in s.genes]
        scores, used, _ = c.scanpy_scores(s, {"first": genes[:12], "second": genes[15:40]})
        self.assertEqual(set(scores), {"first", "second"})
        by_rows, _, _ = c.scanpy_scores(s, {"first": genes[:12], "second": genes[15:40]}, by_columns=False)
        for name in scores:  # either way the same scores, to float rounding (a finding's checks keep the rows)
            np.testing.assert_allclose(scores[name], by_rows[name], rtol=1e-12, atol=1e-14)
        rows = ad.AnnData(sp.csr_matrix(s.lognorm), var=pd.DataFrame(index=pd.Index(genes)))
        rows.var_names_make_unique()
        for name, keep in used.items():
            sc.tl.score_genes(rows, keep, ctrl_size=50, n_bins=25, random_state=0, score_name="_score",
                              use_raw=False)
            np.testing.assert_array_equal(by_rows[name], rows.obs["_score"].to_numpy())


if __name__ == "__main__":
    unittest.main()
