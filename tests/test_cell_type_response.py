"""Which cell types respond (pertura.steps.cell_type_response, pertpy's Augur; docs/adr/0031, phase 2, batch C): a
target that raises genes in one cell type's cells only ranks that cell type first."""
import importlib.util
import os
import tempfile
import unittest

import numpy as np

HAS = importlib.util.find_spec("pertpy") is not None and importlib.util.find_spec("scanpy") is not None


@unittest.skipUnless(HAS, "needs pertpy and scanpy")
class TestCellTypeResponse(unittest.TestCase):
    def test_the_cell_type_the_target_moves_ranks_first(self):
        from pertura import blocks as pt
        from test_common_response_and_field_measures import build_screen
        with tempfile.TemporaryDirectory() as d:
            # T's cells in type X raise g0-g9; its cells in type Y (built as an inert label U) do not
            s, _ = build_screen(d, {"T": (80, {f"g{j}": 4.0 for j in range(10)}), "U": (80, {})},
                                units=("u1",), genes=100, controls=8, control_cells=20)
            kind = np.where(s.label == "T", "X", np.where(s.label == "U", "Y", ""))
            ctrl = np.flatnonzero(s.label == "control")
            kind[ctrl[::2]], kind[ctrl[1::2]] = "X", "Y"
            s.adata.obs["cell_type"] = kind
            s = s.with_labels(np.where(s.label == "U", "T", s.label))
            with pt.inside(s, os.path.join(d, "blocks")):
                res = pt.cell_type_response(column="cell_type", n_subsamples=10)
                with self.assertRaises(ValueError):
                    pt.cell_type_response(column=None)
        t = res.table.set_index("cell_type")
        self.assertGreater(t.loc["X", "auc"], t.loc["Y", "auc"])
        self.assertEqual(t.loc["X", "rank_in_target"], 1)


if __name__ == "__main__":
    unittest.main()
