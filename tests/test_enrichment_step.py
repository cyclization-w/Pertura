"""The enrichment step (pertura.steps.enrichment; docs/adr/0031, phase 2, batch C): each backend gives its known
case's p under its own rule; a set planted among a target's up genes comes first, up and not down, against the
target's tested genes; GSEA finds the set at the top of a ranking; G.meaning takes the backend."""
import importlib.util
import os
import shutil
import tempfile
import unittest

import numpy as np
import pandas as pd

from pertura.envs import R_HOME
from pertura.steps import enrichment

HAS_GSEAPY = importlib.util.find_spec("gseapy") is not None
HAS_R = os.path.exists(f"{R_HOME}/bin/Rscript")


def gene_result():
    """Target T raises g0-g19 (set S holds g0-g9) and lowers g50-g69; target N changes nothing; 200 genes tested."""
    rows = []
    rng = np.random.default_rng(0)
    for t in ("T", "N"):
        for i in range(200):
            up, down = t == "T" and i < 20, t == "T" and 50 <= i < 70
            rows.append({"target": t, "gene": f"g{i}", "effect": 1.5 if up else -1.5 if down else rng.normal(0, 0.1),
                         "p": 1e-6 if up or down else 0.5, "q": 1e-4 if up or down else 0.9})
    return pd.DataFrame(rows)


SETS = {"S": [f"g{i}" for i in range(10)], "D": [f"g{i}" for i in range(50, 60)],
        "R": [f"g{i}" for i in range(100, 115)]}


class TestKnownCases(unittest.TestCase):
    @unittest.skipUnless(HAS_GSEAPY, "needs GSEApy")
    def test_gseapy_gives_the_hypergeometric_over_the_universe_given(self):
        from scipy import stats
        self.assertAlmostEqual(enrichment.checked("gseapy") / stats.hypergeom.sf(9, 200, 10, 15), 1, places=6)

    @unittest.skipUnless(HAS_R, "needs R (clusterProfiler)")
    def test_clusterprofiler_gives_it_over_the_genes_some_set_holds(self):
        d = tempfile.mkdtemp()
        try:
            from scipy import stats
            self.assertAlmostEqual(enrichment.checked("clusterprofiler", d), stats.hypergeom.sf(9, 20, 10, 10))
            self.assertEqual(os.listdir(d), [])  # its work files gone
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_a_backend_that_does_not_exist_is_refused(self):
        with self.assertRaises(ValueError):
            enrichment.ora(["g1"], {"g1", "g2"}, {"S": ["g1"]}, backend="enrichr")


class Planted(unittest.TestCase):
    backend = None

    def setUp(self):
        if self.backend is None:
            self.skipTest("the base class")
        self.d = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_the_planted_set_comes_first_up_and_not_down(self):
        t = gene_result()
        part = t[t["target"] == "T"]
        universe = set(part["gene"])
        up = part.loc[(part["q"] < 0.05) & (part["effect"] > 0), "gene"]
        r = enrichment.ora(up, universe, SETS, self.backend, self.d)
        self.assertEqual(r.sort_values("p").iloc[0]["set"], "S")
        self.assertLess(r.set_index("set").at["S", "q"], 0.05)
        self.assertNotIn("D", set(r.loc[r["q"] < 0.05, "set"]))

    def test_gsea_finds_the_set_at_the_top_of_the_ranking(self):
        t = gene_result()
        rank = t[t["target"] == "T"].set_index("gene")["effect"]
        # sets of 20 genes: GSEApy's prerank leaves out sets of fewer than 15
        sets = {"S": [f"g{i}" for i in range(20)], "D": [f"g{i}" for i in range(50, 70)],
                **{f"filler{k}": [f"g{i}" for i in range(100 + 15 * k, 120 + 15 * k)] for k in range(6)}}
        r = enrichment.gsea(rank, sets, self.backend, self.d, seed=0).set_index("set")
        self.assertGreater(r.at["S", "nes"], 0)
        self.assertLess(r.at["D", "nes"], 0)


@unittest.skipUnless(HAS_GSEAPY, "needs GSEApy")
class TestGSEApy(Planted):
    backend = "gseapy"


@unittest.skipUnless(HAS_R, "needs R (clusterProfiler)")
class TestClusterProfiler(Planted):
    backend = "clusterprofiler"


@unittest.skipUnless(HAS_GSEAPY and importlib.util.find_spec("scanpy") is not None, "needs GSEApy and scanpy")
class TestTheBlock(unittest.TestCase):
    def test_pt_enrich_reads_each_targets_changed_genes_against_its_tested_genes(self):
        from pertura import blocks as pt
        from test_common_response_and_field_measures import build_screen
        d = tempfile.mkdtemp()
        try:
            s, _ = build_screen(d, {"T": (12, {}), "N": (12, {})}, units=("u1",), genes=40, controls=4,
                                control_cells=10)
            s.design["experiment"] = {"organism": {"value": "human"}}
            with pt.inside(s, os.path.join(d, "blocks")):
                genes = pt.Result("genes", {"reference": "control_guides"}, gene_result(), {}, [], "every cell",
                                  call_id="genes-test")
                res = pt.enrich(of=genes, gene_lists=SETS)
            t = res.table
            first = t[(t["target"] == "T") & (t["direction"] == "up")].sort_values("p").iloc[0]
            self.assertEqual((first["set"], int(first["universe"])), ("S", 200))
            down = t[(t["target"] == "T") & (t["direction"] == "down")].sort_values("p").iloc[0]
            self.assertEqual(down["set"], "D")
            self.assertFalse(((t["target"] == "N") & (t["q"] < 0.05)).any())
        finally:
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
