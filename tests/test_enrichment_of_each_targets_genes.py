"""Enrichment as the development runs on 0adebaa met it (docs/build/tools_by_the_fields_kinds_build_log.md, the fixes
after the runs):
- a network repeats no (source, target) pair: the mouse CollecTRI's repeated pairs are kept once where they agree and
  left out where their signs disagree, and a repeated pair is a stated error, not a target read as having too few
  genes;
- a gene list or a ranking given as a table is read from its columns, and a bare string is refused with the way to
  each target's own genes;
- with a genes run (de), ORA reads each target's changed genes, up and down apart, against the genes tested for it,
  and GSEA each target's genes ranked by their signed statistics."""
import os
import tempfile
import unittest

import numpy as np
import pandas as pd

from pertura import gene_sets
from pertura.question_modules import g
from pertura.steps import enrichment

try:
    import gseapy  # noqa: F401
    HAS_GSEAPY = True
except ImportError:
    HAS_GSEAPY = False

HUMAN = {"experiment": {"organism": {"value": "human"}}}


class TestOneRowPerPair(unittest.TestCase):
    def test_repeats_that_agree_stay_once_and_those_that_disagree_go(self):
        net = pd.DataFrame({"source": ["F1", "F1", "F1", "F2", "F2", "F3"],
                            "target": ["a", "a", "b", "c", "c", "d"],
                            "weight": [1.0, 1.0, -1.0, 1.0, -1.0, 1.0]})
        kept = gene_sets.one_row_per_pair(net)
        self.assertEqual(sorted(map(tuple, kept[["source", "target"]].values)), [("F1", "a"), ("F1", "b"),
                                                                                  ("F3", "d")])
        enrichment.one_pair_each(kept)
        with self.assertRaisesRegex(ValueError, r"repeats 2 \(source, target\) pairs"):
            enrichment.one_pair_each(net)

    def test_activities_state_a_repeated_pair_instead_of_reading_no_activity(self):
        table = pd.DataFrame({"target": ["A"] * 3, "gene": ["a", "b", "c"], "effect": [1.0, -1.0, 0.5],
                              "p": [0.01, 0.02, 0.5]})
        net = pd.DataFrame({"source": ["F1", "F1"], "target": ["a", "a"], "weight": [1.0, 1.0]})
        with self.assertRaisesRegex(ValueError, "repeats 1"):
            enrichment.activities(table, net)


class TestGenesAndRankingsGiven(unittest.TestCase):
    def test_a_table_is_read_by_its_columns_and_a_bare_string_refused(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "changed_genes.tsv")
            pd.DataFrame({"target": ["A", "A", "B"], "gene": ["x", "y", "x"], "effect": [1.0, -2.0, 0.5]}).to_csv(
                p, sep="\t", index=False)
            self.assertEqual(g._genes_given("genes", p), ["x", "y"])
            self.assertEqual(g._genes_given("up", ["x", 3]), ["x", "3"])
            with self.assertRaisesRegex(ValueError, "give de"):
                g._ranking_given(p)  # two targets: no one ranking
            one = os.path.join(d, "one.tsv")
            pd.DataFrame({"gene": ["x", "y"], "stat": [2.0, -1.0]}).to_csv(one, sep="\t", index=False)
            self.assertEqual(g._ranking_given(one), {"x": 2.0, "y": -1.0})
        with self.assertRaisesRegex(ValueError, "a list of genes"):
            g._genes_given("genes", "20261109T152857-C-genes-711a73")

    def test_targets_without_a_genes_run_are_refused(self):
        with tempfile.TemporaryDirectory() as d, self.assertRaisesRegex(ValueError, "give de"):
            g.meaning(None, HUMAN, d, method="ora", genes=["x"], targets=["A"])


def a_genes_run(rng):
    """Two targets over 200 tested genes: A raises S1's genes (g0-g19) and lowers S2's (g20-g39); B changes ten genes
    of no set. Each set holds 25 genes, past GSEApy's least set size (15)."""
    genes = [f"g{i}" for i in range(200)]
    rows = []
    for t in ("A", "B"):
        for i, gene in enumerate(genes):
            changed = (t == "A" and i < 40) or (t == "B" and 150 <= i < 160)
            effect = (1.0 if i < 20 or t == "B" else -1.0) if changed else rng.normal(0, 0.05)
            p = 1e-6 if changed else rng.uniform(0.2, 1.0)
            rows.append((t, gene, effect, p, 1e-4 if changed else min(1.0, p * 2), changed))
    table = pd.DataFrame(rows, columns=["target", "gene", "effect", "p", "q", "changed"])
    sets = {"S1": [f"g{i}" for i in [*range(20), *range(100, 105)]],
            "S2": [f"g{i}" for i in [*range(20, 40), *range(105, 110)]],
            "S3": [f"g{i}" for i in range(60, 85)]}
    return table, sets


@unittest.skipUnless(HAS_GSEAPY, "needs gseapy")
class TestEachTargetsOwnGenes(unittest.TestCase):
    def test_ora_reads_each_targets_changed_genes_up_and_down_apart(self):
        table, sets = a_genes_run(np.random.default_rng(0))
        with tempfile.TemporaryDirectory() as d:
            r = g.meaning(None, HUMAN, d, method="ora", de=table, gene_lists=sets)
        self.assertEqual(r.status, "detected")
        hits = r.table[r.table["q"] < 0.05]
        self.assertEqual(sorted(map(tuple, hits[["target", "direction", "set"]].values)),
                         [("A", "down", "S2"), ("A", "up", "S1")])
        self.assertTrue((r.table["tested"] == 200).all())  # the genes tested for the target, its universe
        self.assertTrue(r.diagnostics["per_target"])
        self.assertIn("up and down apart", r.summary)
        self.assertTrue(any(line.startswith("A: in its up genes S1") for line in r.biology), r.biology)

    def test_gsea_ranks_each_targets_genes_and_targets_narrow_the_run(self):
        table, sets = a_genes_run(np.random.default_rng(1))
        with tempfile.TemporaryDirectory() as d:
            r = g.meaning(None, HUMAN, d, method="gsea", de=table, gene_lists=sets, targets=["A"])
        self.assertEqual(set(r.table["target"]), {"A"})
        top = r.table.sort_values("nes", ascending=False)
        self.assertEqual(top.iloc[0]["set"], "S1")
        self.assertEqual(top.iloc[-1]["set"], "S2")


if __name__ == "__main__":
    unittest.main()
