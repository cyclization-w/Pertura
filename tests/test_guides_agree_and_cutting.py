"""Two checks from published screens (docs/build/adr0022/published_screens_on_our_choices_and_ideas.md, Part B, the
user's choice of 2026-10-17): the guides of one target against pairs of control guides (F.guides_agree), and cutting
control guides against non-targeting ones (D.cutting_response); with each target's own gene left out when guides or
targets are compared."""
import importlib.util
import json
import os
import tempfile
import unittest

import numpy as np
import pandas as pd

from pertura import envs
from pertura.question_modules import REGISTRY
from pertura.question_modules.f import guide_pairs

HAS_R = os.path.exists(envs.rscript())
HAS_PERTPY = importlib.util.find_spec("pertpy") is not None


def guides_screen(folder, cutting_shift=0.0, lanes=3, seed=0):
    """Lanes of one pool, knockout: 4 non-targeting and 4 intergenic control guides (the intergenic ones raise genes
    d0-d4 by cutting_shift); target P (3 guides) raising p0-p9; target Q (3 guides) doing nothing; target M (3
    guides) lowering only its own gene M."""
    import anndata as ad
    import scipy.sparse as sp
    from pertura.question_modules.data import Screen
    rng = np.random.default_rng(seed)
    genes = ["M"] + [f"p{i}" for i in range(10)] + [f"d{i}" for i in range(5)] + [f"h{j}" for j in range(200)]
    gi = {g: i for i, g in enumerate(genes)}
    nt, ig = [f"NT_{i}" for i in range(1, 5)], [f"IG_{i}" for i in range(1, 5)]
    rows = []
    for k in range(lanes):
        rows += [(f"L{k + 1}", g, "control") for g in nt + ig for _ in range(40)]
        rows += [(f"L{k + 1}", f"{t}_{i}", t) for t in ("P", "Q", "M") for i in (1, 2, 3) for _ in range(40)]
    obs = pd.DataFrame(rows, columns=["unit", "guide", "label"])
    mu = np.full((len(obs), len(genes)), 5.0)
    mu[(obs["label"] == "P").to_numpy(), gi["p0"]:gi["p9"] + 1] *= 2.5
    mu[(obs["label"] == "M").to_numpy(), gi["M"]] *= 0.1
    if cutting_shift:
        mu[obs["guide"].isin(ig).to_numpy(), gi["d0"]:gi["d4"] + 1] *= cutting_shift
    X = sp.csr_matrix(rng.poisson(mu).astype(np.float32))
    obs["guides"] = [f"{g}:20" for g in obs["guide"]]
    obs["control_group"] = np.where(obs["guide"].isin(nt), "NT", np.where(obs["guide"].isin(ig), "IG", ""))
    obs.index = [f"c{i}" for i in range(len(obs))]
    a = ad.AnnData(X, obs=obs[["unit", "label", "guides", "control_group"]].copy(), var=pd.DataFrame(index=genes))
    targeting = [f"{t}_{i}" for t in ("P", "Q", "M") for i in (1, 2, 3)]
    pd.DataFrame({"guide": nt + ig + targeting,
                  "target": ["NT"] * 4 + ["IG"] * 4 + [g.split("_")[0] for g in targeting],
                  "kind": ["non_targeting"] * 4 + ["intergenic"] * 4 + ["targeting"] * 9}).to_csv(
        os.path.join(folder, "guides.tsv"), sep="\t", index=False)
    with open(os.path.join(folder, "screen.json"), "w", encoding="utf-8") as fh:
        json.dump({"unit_source": "lanes", "flags": []}, fh)
    design = {"perturbation": {"modality": {"value": "knockout"}}, "units": {"independent_unit": {"value": "none"}},
              "experiment": {"context": {"value": "cell_line"}}}
    return Screen(folder, design, adata=a), design


class TestGuidesAgree(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.screen, cls.design = guides_screen(cls.tmp.name)
        out = os.path.join(cls.tmp.name, "F")
        os.makedirs(out)
        cls.r = REGISTRY["F.guides_agree"].run(cls.screen, cls.design, out)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_pairs_of_each_kind(self):
        kinds = self.r.tables["guide_pairs"]["kind"].value_counts().to_dict()
        self.assertEqual(kinds["same target"], 3 * 3)  # 3 pairs in each of P, Q and M
        self.assertEqual(kinds["control guides"], 2 * (4 * 3 // 2))  # within each kind: non-targeting, intergenic

    def test_a_target_with_an_effect_agrees_beyond_every_control_pair(self):
        t = self.r.table.set_index("target")
        self.assertEqual(t.loc["P", "control_pairs_as_high"], 0.0)
        self.assertGreater(t.loc["Q", "control_pairs_as_high"], 0.05)

    def test_a_target_with_an_effect_agrees_beyond_the_pairs_of_two_targets(self):
        t = self.r.table.set_index("target")
        self.assertEqual(t.loc["P", "different_pairs_as_high"], 0.0)
        self.assertGreater(t.loc["Q", "different_pairs_as_high"], 0.05)
        against = self.r.diagnostics["against_two_targets"]
        self.assertEqual((against["same_target_pairs"], against["different_targets_pairs"]), (9, 27))
        self.assertEqual(against["targets_beyond"], 1)
        self.assertIn("pairs of one target's guides against pairs of two targets' guides", self.r.summary)

    def test_a_guides_own_gene_does_not_make_its_guides_agree(self):
        t = self.r.table.set_index("target")  # M changes only its own gene, which is left out
        self.assertGreater(t.loc["M", "control_pairs_as_high"], 0.05)
        self.assertEqual(self.r.status, "measured")
        self.assertEqual(self.r.figure_checks.get("layout_issues"), [])

    def test_one_target_alone_is_read_against_the_control_pairs(self):
        """GSE250558's replay asked for RAF1 alone: no pair of two targets' guides was left, and the run failed."""
        from pertura.blocks import _focus
        r = REGISTRY["F.guides_agree"].run(_focus(self.screen, ["P"], "control_guides"), self.design,
                                           os.path.join(self.tmp.name, "F_one_target"))
        t = r.table.set_index("target")
        self.assertEqual(list(t.index), ["P"])
        self.assertEqual(t.loc["P", "control_pairs_as_high"], 0.0)
        self.assertTrue(np.isnan(t.loc["P", "different_pairs_as_high"]))

    def test_guides_in_different_samples_are_compared_within_one(self):
        pairs = guide_pairs(self.screen)
        self.assertEqual(set(pairs["units"]), {3})  # each pair averaged over the 3 lanes that hold both


@unittest.skipUnless(HAS_R and HAS_PERTPY, "needs the R environment (Seurat) and pertpy")
class TestCuttingResponse(unittest.TestCase):
    def run_on(self, shift):
        with tempfile.TemporaryDirectory() as d:
            s, design = guides_screen(d, cutting_shift=shift)
            out = os.path.join(d, "D")
            os.makedirs(out)
            return REGISTRY["D.cutting_response"].fn(s, design, out, permutations=200)  # fewer, for speed

    def test_a_shift_planted_in_the_cutting_controls_is_found(self):
        r = self.run_on(2.0)
        self.assertEqual(r.status, "detected")
        self.assertTrue({f"d{i}" for i in range(5)} <= set(r.tables["changed_genes"]["gene"]))
        self.assertEqual(r.diagnostics["guides"]["cutting"], [f"IG_{i}" for i in range(1, 5)])

    def test_nothing_is_found_where_nothing_is_planted(self):
        r = self.run_on(0.0)
        self.assertEqual(r.status, "not_detected")
        self.assertEqual(r.diagnostics["genes_changed"], 0)


class TestCuttingResponseWithoutCuttingControls(unittest.TestCase):
    def test_it_says_there_is_not_enough_data_instead_of_failing(self):
        """Its status was 'not_applicable', which Module.run refuses, so the module raised wherever it applied
        without cutting controls (the module inventory, docs/build/adr0031)."""
        from test_common_response_and_field_measures import build_screen
        with tempfile.TemporaryDirectory() as d:
            s, design = build_screen(d, {"T": (40, {"g1": 3.0})})
            out = os.path.join(d, "D")
            os.makedirs(out)
            r = REGISTRY["D.cutting_response"].run(s, design, out)
        self.assertEqual(r.status, "not_enough_data")
        self.assertIn("needs control guides that cut", r.summary)


if __name__ == "__main__":
    unittest.main()
