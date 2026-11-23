"""Similar targets widened (docs/adr/0044, D1): pairs with opposite effects beside the pairs alike, each read against
the control-guide pairs; the genes each pair past a line changes both; and the pairs within one GO cellular component
(a known complex) against the other pairs, as published screens check their groups."""
import importlib.util
import os
import tempfile
import unittest
from unittest import mock

import pandas as pd

from test_question_modules import synthetic_screen

from pertura.question_modules import REGISTRY
from pertura.question_modules.e import shared_genes, similar_biology, within_complexes

HAS_SCANPY = importlib.util.find_spec("scanpy") is not None
PAIRS = pd.DataFrame({"target": ["A", "A", "B"], "other": ["B", "C", "C"], "correlation": [0.8, -0.6, 0.05],
                      "control_pairs_as_high": [0.0, 1.0, 0.5], "control_pairs_as_low": [1.0, 0.0, 0.5]})
DE = pd.DataFrame({"target": ["A", "A", "A", "B", "B", "C", "C"],
                   "gene": ["x1", "x2", "x3", "x1", "x2", "x1", "x3"],
                   "effect": [1.0, -1.0, 2.0, 0.5, -0.5, -1.5, 1.0], "q": [0.01] * 7})


class TestOppositePairsAndSharedGenes(unittest.TestCase):
    def test_a_pair_alike_shares_genes_moved_the_same_way_and_an_opposite_pair_genes_moved_apart(self):
        t = shared_genes(PAIRS, DE).set_index(["target", "other"])
        self.assertEqual((t.loc[("A", "B"), "way"], t.loc[("A", "B"), "genes"]), ("alike", "x1, x2"))
        self.assertEqual((t.loc[("A", "C"), "way"], t.loc[("A", "C"), "genes"]), ("opposite", "x1"))
        self.assertNotIn(("B", "C"), t.index)  # past neither line

    def test_the_lines_give_the_pairs_alike_first_then_the_opposite_ones_with_their_genes(self):
        lines = similar_biology(PAIRS, {("A", "B"): "x1, x2", ("A", "C"): "x1"})
        self.assertEqual(lines, ["A and B: r 0.80; both change x1, x2", "A and C: r -0.60, opposite; both change x1"])


class TestKnownComplexes(unittest.TestCase):
    def test_the_pairs_within_one_component_are_set_against_the_others(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "sets", "msigdb"))
            with open(os.path.join(d, "sets", "msigdb", "c5.go.cc.v2025.1.Hs.symbols.gmt"), "w",
                      encoding="utf-8") as fh:
                fh.write("GOCC_A_B_COMPLEX\turl\tA\tB\nGOCC_HUGE\turl\tA\n")
            folder = os.path.join(d, "screen")
            os.makedirs(folder)
            s, design = synthetic_screen(folder)
            with mock.patch.dict(os.environ, {"PERTURA_GENE_SETS": os.path.join(d, "sets")}):
                complexes, words = within_complexes(s, design, PAIRS)
        self.assertEqual(complexes[["complex", "targets", "pairs"]].values.tolist(), [["GOCC_A_B_COMPLEX", "A, B", 1]])
        self.assertIn("the 1 pairs within one GO cellular component correlate at a median r of 0.80 against -0.27",
                      words)  # the median of -0.6 and 0.05, -0.275, is -0.27499... in floating point


@unittest.skipUnless(HAS_SCANPY, "needs scanpy")
class TestTheModuleReadsBothLines(unittest.TestCase):
    def test_the_table_holds_the_opposite_side_and_the_summary_counts_both(self):
        from synthetic_screen import make_cell_screen  # 13 targets and 12 control guides, so control pairs to read by
        with tempfile.TemporaryDirectory() as d:
            folder = os.path.join(d, "screen")
            os.makedirs(folder)
            s, design, _ = make_cell_screen(folder, seed=3)
            r = REGISTRY["E.similar_targets"].run(s, design, os.path.join(d, "out"), min_cells=10)
        self.assertIn("control_pairs_as_low", r.table.columns)
        self.assertTrue(r.table["control_pairs_as_low"].notna().any())
        self.assertIn("opposite effects", r.summary)


if __name__ == "__main__":
    unittest.main()
