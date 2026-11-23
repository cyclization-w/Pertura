"""The scores step's two backends (docs/adr/0031, phase 2, batch C): Seurat's AddModuleScore gives the planted set's
shift for the target that raises it, as scanpy's score_genes does, on the same genes."""
import importlib.util
import os
import tempfile
import unittest

from pertura.envs import R_HOME

HAS_R = os.path.exists(f"{R_HOME}/bin/Rscript")
HAS_SCANPY = importlib.util.find_spec("scanpy") is not None


@unittest.skipUnless(HAS_R and HAS_SCANPY, "needs R (Seurat) and scanpy")
class TestSeuratAndScanpyScores(unittest.TestCase):
    def test_both_find_the_target_that_raises_the_set(self):
        from pertura import blocks as pt
        from test_common_response_and_field_measures import build_screen
        # 3,000 genes: Seurat draws 100 control genes from each of 24 expression bins
        sets = {"B set": [f"b{i}" for i in range(8)], "S": [f"g{i}" for i in range(100, 120)]}
        with tempfile.TemporaryDirectory() as d:
            s, _ = build_screen(d, {"B": (60, {f"b{i}": 4.0 for i in range(8)}), "A": (60, {})}, units=("u1", "u2"),
                                genes=3000, controls=6, control_cells=30)
            with pt.inside(s, os.path.join(d, "blocks")):
                sc = pt.scores(sets=sets)
                se = pt.scores(sets=sets, method="seurat")
        for res in (sc, se):
            t = res.table.set_index(["target", "score"])
            self.assertGreater(t.loc[("B", "B set"), "effect"], 0)
            self.assertLess(t.loc[("B", "B set"), "q"], 0.05)
            self.assertGreater(t.loc[("B", "B set"), "effect"], t.loc[("A", "B set"), "effect"])
        self.assertEqual(se.calls[0]["package"], "Seurat")
        self.assertEqual(se.extra["genes_used"], sc.extra["genes_used"])


if __name__ == "__main__":
    unittest.main()
