"""Factors' and pathways' activity from each target's changes (docs/adr/0044, D1): decoupler's univariate linear model
on each target's log2 fold changes over its tested genes, as decoupler's tutorials read CollecTRI and PROGENy, in the
enrichment step, pt.enrich and G.meaning; beside the control-guide groups' line, from the same gene test's
calibration, found in the gene run's own block log."""
import importlib.util
import json
import os
import tempfile
import unittest
from unittest import mock

import numpy as np
import pandas as pd

from test_question_modules import synthetic_screen

import pertura.blocks as pt

HAS_DECOUPLER = importlib.util.find_spec("decoupler") is not None
GENES = [f"g{i}" for i in range(60)] + [f"h{i}" for i in range(1000)]  # most genes in no regulon, as in a screen
REGULONS = pd.DataFrame({"source": ["TF_A"] * 20 + ["TF_B"] * 20 + ["TF_X"] * 20, "target": GENES[:60],
                         "weight": 1.0})


def changes(rng, lowered=("A",), groups=()):
    """A genes table: target A lowers TF_A's twenty genes g0-g19, the others change nothing; each control-guide group
    of the calibration changes nothing."""
    rows = []
    for t in ("A", "B", *groups):
        for i, g in enumerate(GENES):
            down = t in lowered and i < 20
            rows.append({"target": t, "gene": g, "effect": -1.0 + rng.normal(0, 0.1) if down else rng.normal(0, 0.1),
                         "p": 1e-8 if down else rng.uniform(0, 1), "q": 0.001 if down else 1.0})
    return pd.DataFrame(rows)


def network_folder(root):
    """CollecTRI and PROGENy laid out as the fetched set holds them, for human."""
    for d in ("collectri", "progeny", "msigdb"):
        os.makedirs(os.path.join(root, d))
    REGULONS.to_csv(os.path.join(root, "collectri", "CollecTRI_regulons_human.csv"), index=False)
    pd.DataFrame({"gene": [f"b{i}" for i in range(5)] + [f"g{i}" for i in range(10)],
                  "pathway": ["PATH_B"] * 5 + ["PATH_G"] * 10, "weight": 1.0, "p.value": 0.01}).to_csv(
        os.path.join(root, "progeny", "progeny_human_top500.tsv"), sep="\t", index=False)
    return root


class TestTheCalibrationOfAGenesRun(unittest.TestCase):
    def test_the_groups_tables_are_the_calibration_checks_of_that_very_call(self):
        with tempfile.TemporaryDirectory() as d:
            blocks = os.path.join(d, "run1", "blocks")
            for cid, rows in (("g1", 3), ("c1", 2), ("c2", 2), ("x1", 1)):
                os.makedirs(os.path.join(blocks, "outputs", cid))
                pd.DataFrame({"target": [cid] * rows, "gene": ["a"] * rows, "effect": 0.0}).to_csv(
                    os.path.join(blocks, "outputs", cid, "table.tsv.gz"), sep="\t", index=False)
            log = [{"block": "genes", "call_id": "g1", "check_of": None, "check": None},
                   {"block": "genes", "call_id": "c1", "check_of": "g1", "check": "calibration"},
                   {"block": "genes", "call_id": "c2", "check_of": "g1", "check": "calibration"},
                   {"block": "genes", "call_id": "x1", "check_of": "g1", "check": "by_guide"}]
            with open(os.path.join(blocks, pt.LOG), "w", encoding="utf-8") as fh:
                fh.write("\n".join(json.dumps(x) for x in log) + "\n")
            found = pt.genes_calibration_tables(os.path.join(blocks, "outputs", "g1", "table.tsv.gz"))
            self.assertEqual(sorted(t["target"].iat[0] for t in found), ["c1", "c2"])
            self.assertEqual(pt.genes_calibration_tables(os.path.join(d, "some_table.tsv")), [])


class TestTheStatisticReadIsTheTestsNotTheFoldChange(unittest.TestCase):
    def test_a_sparse_genes_extreme_fold_change_weighs_little(self):
        from pertura.steps import enrichment
        part = pd.DataFrame({"gene": ["sparse", "solid", "none"], "effect": [-25.0, 0.3, 0.0],
                             "p": [0.9, 1e-10, 1.0]})
        z = enrichment.signed_statistic(part)
        self.assertLess(abs(z[0]), 0.2)  # the weak test of a gene seen in a few cells
        self.assertGreater(z[1], 6)
        self.assertEqual(z[2], 0.0)
        np.testing.assert_array_equal(enrichment.signed_statistic(part.drop(columns="p")), part["effect"])
        np.testing.assert_array_equal(enrichment.signed_statistic(part.assign(stat=[1.0, 2.0, 3.0])), [1, 2, 3])


class TestGMeaningTakesActivityOnlyFromAGenesRun(unittest.TestCase):
    def test_de_serves_every_method_and_a_network_belongs_to_activity(self):
        from pertura.question_modules import REGISTRY
        m = REGISTRY["G.meaning"]
        m.check({"method": "ulm", "collection": "progeny", "de": "run1"})
        m.check({"method": "ora", "de": "run1", "targets": ["A"]})  # each target's own changed genes
        m.check({"method": "gsea", "de": "run1"})
        with self.assertRaisesRegex(ValueError, "calibration is used only with method"):
            m.check({"method": "ora", "calibration": "run1"})
        with tempfile.TemporaryDirectory() as d:
            r = m.fn(None, {"experiment": {"organism": {"value": "human"}}}, d, collection="collectri", method="ora",
                     genes=["g1"])
        self.assertEqual(r.status, "not_enough_data")
        self.assertIn("read it with method ulm", r.summary)


@unittest.skipUnless(HAS_DECOUPLER, "needs decoupler")
class TestActivities(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"PERTURA_GENE_SETS": network_folder(self.tmp.name)})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def test_the_lowered_regulon_gives_the_lowest_activity_and_the_line_comes_from_the_groups(self):
        from pertura.steps import enrichment
        rng = np.random.default_rng(0)
        act = enrichment.activities(changes(rng), REGULONS).set_index(["target", "source"])
        self.assertLess(act.loc[("A", "TF_A"), "activity"], 0)
        self.assertLess(act.loc[("A", "TF_A"), "q"], 0.05)
        self.assertEqual(act.loc["A"]["activity"].idxmin(), "TF_A")
        self.assertGreater(act.loc[("B", "TF_A"), "q"], 0.05)
        groups = changes(rng, lowered=(), groups=("G1", "G2", "G3", "G4"))
        band = enrichment.activity_band(groups[groups["target"].str.startswith("G")], REGULONS)
        self.assertEqual(sorted(band), ["TF_A", "TF_B", "TF_X"])
        self.assertGreater(abs(act.loc[("A", "TF_A"), "activity"]), band["TF_A"])
        elsewhere = pd.DataFrame({"target": "Z", "gene": ["zz1", "zz2"], "effect": 1.0, "p": 0.01})
        act = enrichment.activities(pd.concat([changes(rng), elsewhere]), REGULONS)
        self.assertNotIn("Z", set(act["target"]))  # a target none of whose tested genes a source holds is left out

    def test_g_meaning_reads_every_factor_beside_the_control_guide_line(self):
        from pertura.question_modules import REGISTRY
        rng = np.random.default_rng(1)
        de = changes(rng)
        groups = changes(rng, lowered=(), groups=("G1", "G2", "G3", "G4"))
        groups = groups[groups["target"].str.startswith("G")]
        design = {"experiment": {"organism": {"value": "human"}}}
        out = os.path.join(self.tmp.name, "act")
        os.makedirs(out)
        r = REGISTRY["G.meaning"].fn(None, design, out, method="ulm", collection="collectri", de=de,
                                     calibration=groups)
        self.assertEqual(r.status, "detected", r.summary)
        past = r.table[r.table["past_band"].astype(bool) & (r.table["q"] < 0.05)]
        self.assertEqual(past[["target", "source"]].values.tolist(), [["A", "TF_A"]])
        self.assertTrue(r.biology and r.biology[0].startswith("A: lower TF_A"), r.biology)
        self.assertIn("past the control-guide groups' line", r.summary)

    @unittest.skipUnless(importlib.util.find_spec("scanpy") is not None, "needs scanpy")
    def test_pt_enrich_reads_a_pathway_from_a_gene_result(self):
        folder = os.path.join(self.tmp.name, "screen")
        os.makedirs(folder)
        s, _ = synthetic_screen(folder)
        with pt.inside(s, os.path.join(self.tmp.name, "blocks")):
            res = pt.genes(targets=["A", "B"])
            act = pt.enrich(of=res, method="ulm", collection="progeny").table.set_index(["target", "source"])
        self.assertGreater(act.loc[("B", "PATH_B"), "activity"], 0)
        self.assertGreater(act.loc[("B", "PATH_B"), "activity"], act.loc[("A", "PATH_B"), "activity"])


if __name__ == "__main__":
    unittest.main()
