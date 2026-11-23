"""Named genes across the targets, and the targets' network (docs/adr/0044, D1): C.genes reads the genes asked for in
every target, from its own test or from an earlier run (de), as a heatmap with the first genes' boxes per target; with
targets_own, each target's own target gene in the other targets' cells, as a network of the changes."""
import importlib.util
import os
import tempfile
import unittest

import numpy as np
import pandas as pd

from test_question_modules import synthetic_screen

from pertura.question_modules import REGISTRY

HAS_SCANPY = importlib.util.find_spec("scanpy") is not None


def gene_test_table():
    """An earlier gene test of targets A, B and C: B raises b0-b4; A lowers B's target gene and B raises A's; C (whose
    gene is not in the matrix) lowers A's gene; nothing else changes."""
    rows = []
    for t in ("A", "B", "C"):
        for g in ["A", "B"] + [f"b{i}" for i in range(5)] + [f"g{i}" for i in range(10)]:
            effect, q = 0.0, 0.9
            if t == "B" and g.startswith("b"):
                effect, q = 2.0, 1e-6
            elif (t, g) == ("A", "B"):
                effect, q = -1.0, 1e-4
            elif (t, g) == ("B", "A"):
                effect, q = 0.8, 1e-3
            elif (t, g) == ("C", "A"):
                effect, q = -0.6, 1e-2
            elif t == g:
                effect, q = -2.5, 1e-9  # a target's target gene in its own cells: its knockdown, no edge
            rows.append({"target": t, "gene": g, "effect": effect, "p": q / 10, "q": q})
    return pd.DataFrame(rows)


class TestReadFromAnEarlierRun(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        folder = os.path.join(self.tmp.name, "screen")
        os.makedirs(folder)
        self.screen, self.design = synthetic_screen(folder)

    def tearDown(self):
        self.tmp.cleanup()

    def run_genes(self, name, **inputs):
        out = os.path.join(self.tmp.name, name)
        os.makedirs(out)
        return REGISTRY["C.genes"].fn(self.screen, self.design, out, de=gene_test_table(), **inputs), out

    def test_named_genes_give_their_targets_the_genes_not_tested_and_boxes(self):
        r, out = self.run_genes("named", genes=["b0", "b1", "g0", "zzz"])
        self.assertEqual(r.status, "detected", r.summary)
        self.assertEqual(sorted(set(r.table["gene"])), ["b0", "b1", "g0"])
        self.assertEqual(r.biology[0], "b0: higher in B (+2.00)")
        self.assertIn("no target changes g0", r.biology[-1])
        self.assertEqual(r.diagnostics["not_tested"], ["zzz"])
        self.assertEqual(r.diagnostics["boxes"], ["figure_boxes_b0.png", "figure_boxes_b1.png",
                                                  "figure_boxes_g0.png"])
        for f in ["figure.png"] + r.diagnostics["boxes"]:
            self.assertTrue(os.path.exists(os.path.join(out, f)), f)
        self.assertIn("read from the earlier run given as de", r.summary)

    def test_the_targets_own_genes_give_the_network_without_a_targets_own_knockdown(self):
        r, out = self.run_genes("own", genes="targets_own")
        edges = r.table[r.table["changed"]]
        self.assertEqual(sorted(map(tuple, edges[["target", "of"]].values.tolist())),
                         [("A", "B"), ("B", "A"), ("C", "A")])  # never (A, A) or (B, B)
        self.assertIn("A: lowers B (-1.00)", r.biology)
        self.assertIn("B: raises A (+0.80)", r.biology)
        self.assertTrue(os.path.exists(os.path.join(out, "figure.png")))

    def test_de_alone_or_with_a_method_that_takes_no_genes_is_refused(self):
        with self.assertRaisesRegex(ValueError, "give genes"):
            REGISTRY["C.genes"].fn(self.screen, self.design, self.tmp.name, de=gene_test_table())
        with self.assertRaisesRegex(ValueError, "genes is used only with method"):
            REGISTRY["C.genes"].check({"method": "seurat3_lr", "genes": ["b0"]})


@unittest.skipUnless(HAS_SCANPY, "needs scanpy")
class TestNamedGenesBesideANewTest(unittest.TestCase):
    def test_the_named_genes_lead_the_reply_beside_the_tests_own_lines(self):
        with tempfile.TemporaryDirectory() as d:
            folder = os.path.join(d, "screen")
            os.makedirs(folder)
            s, design = synthetic_screen(folder)
            r = REGISTRY["C.genes"].run(s, design, os.path.join(d, "out"), method="wilcoxon_same_samples",
                                        genes=["b0", "b1"])
            self.assertTrue(r.figure.endswith("figure_named.png") and os.path.exists(r.figure), r.figure)
            named = r.tables["named_genes"]
            hit = named[(named["target"] == "B") & named["changed"]]
            self.assertEqual(sorted(hit["gene"]), ["b0", "b1"])
            self.assertTrue(r.biology[0].startswith("b0: higher in B"), r.biology)


if __name__ == "__main__":
    unittest.main()
