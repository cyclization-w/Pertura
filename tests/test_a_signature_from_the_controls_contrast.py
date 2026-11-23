"""A signature from the control cells' own contrast between two levels (docs/adr/0044, D1): the control cells' genes
up and down between two levels of a cell column, read on the whole screen, scored on every target; published screens
build an activation score so (a score from a contrast, then every perturbation scored)."""
import importlib.util
import json
import os
import tempfile
import unittest

import numpy as np
import pandas as pd

import pertura.blocks as pt
from pertura.question_modules import REGISTRY

HAS_SCANPY = importlib.util.find_spec("scanpy") is not None
CONTRAST = {"column": "condition_time", "levels": ["stim", "rest"]}


def screen_with_a_stimulation(folder, seed=0):
    """Four mice, two stimulated and two at rest; the stimulation raises genes g0-g9 threefold in every cell but target
    A's, whose cells stay at rest; target B changes nothing."""
    import anndata as ad
    import scipy.sparse as sp
    from pertura.question_modules.data import Screen
    rng = np.random.default_rng(seed)
    # a thousand genes that nothing moves, so the stimulated cells' larger totals barely shift the others' share
    genes = ["A", "B"] + [f"g{i}" for i in range(60)] + [f"h{i}" for i in range(1000)]
    rows = []
    for unit, level in (("u1", "stim"), ("u2", "stim"), ("u3", "rest"), ("u4", "rest")):
        for guide, label, n in (("A_1", "A", 40), ("B_1", "B", 40), ("NTC_1", "control", 60), ("NTC_2", "control", 60)):
            rows += [(unit, level, guide, label)] * n
    obs = pd.DataFrame(rows, columns=["unit", "condition_time", "guide", "label"])
    obs["control_group"] = np.where(obs["label"] == "control", "NTC", "")
    mu = np.full((len(obs), len(genes)), 4.0)
    stim = (obs["condition_time"] == "stim").to_numpy() & (obs["label"] != "A").to_numpy()
    mu[np.ix_(stim, np.arange(2, 12))] *= 3.0
    X = sp.csr_matrix(rng.poisson(mu).astype(np.float32))
    obs["guides"] = [f"{g}:{rng.integers(3, 30)}" for g in obs["guide"]]
    obs["condition"] = obs["condition_time"]  # the screen object's own column of each cell's condition
    obs.index = [f"c{i}" for i in range(len(obs))]
    a = ad.AnnData(X, obs=obs[["unit", "label", "guides", "control_group", "condition_time", "condition"]].copy(),
                   var=pd.DataFrame(index=genes))
    pd.DataFrame({"guide": ["A_1", "B_1", "NTC_1", "NTC_2"], "target": ["A", "B", "NTC", "NTC"],
                  "kind": ["targeting"] * 2 + ["non_targeting"] * 2}).to_csv(os.path.join(folder, "guides.tsv"),
                                                                              sep="\t", index=False)
    with open(os.path.join(folder, "screen.json"), "w", encoding="utf-8") as fh:
        json.dump({"unit_source": "separate_samples", "flags": [], "rule": {"rule": "dominance"}}, fh)
    design = {"perturbation": {"modality": {"value": "CRISPRi"}},
              "units": {"independent_unit": {"value": "mouse"}, "n_units": {"value": 4}},
              "experiment": {"organism": {"value": "human"}}}
    return Screen(folder, design, adata=a), design


class TestTheContractTakesAContrastOnlyAsItsOwnSets(unittest.TestCase):
    def test_contrast_belongs_to_gene_sets_contrast_and_is_needed_there(self):
        m = REGISTRY["C.programs"]
        m.check({"gene_sets": "contrast", "contrast": CONTRAST})
        with self.assertRaisesRegex(ValueError, "contrast is used only with gene_sets"):
            m.check({"gene_sets": "hallmark", "contrast": CONTRAST})
        with tempfile.TemporaryDirectory() as d:
            r = m.fn(None, {"experiment": {"organism": {"value": "human"}}}, d, gene_sets="contrast")
        self.assertEqual(r.status, "not_enough_data")
        self.assertIn("needs contrast", r.summary)


@unittest.skipUnless(HAS_SCANPY, "needs scanpy")
class TestASignatureFromTheControlsContrast(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        folder = os.path.join(self.tmp.name, "screen")
        os.makedirs(folder)
        self.screen, self.design = screen_with_a_stimulation(folder)

    def tearDown(self):
        self.tmp.cleanup()

    def test_scored_in_the_stimulated_cells_it_finds_the_target_that_blunts_the_response(self):
        s = self.screen
        with pt.inside(s, os.path.join(self.tmp.name, "blocks")):
            res = pt.scores(contrast=CONTRAST, cells=s.subset(condition_time="stim"), targets=["A", "B"])
        up = res.extra["genes_used"]["stim vs rest, up"]
        self.assertTrue(set(up) <= {f"g{i}" for i in range(10)} and len(up) >= 8, up)
        t = res.table.set_index(["target", "score"])
        self.assertLess(t.loc[("A", "stim vs rest, up"), "effect"], 0)
        self.assertLess(t.loc[("A", "stim vs rest, up"), "q"], 0.05)
        self.assertGreater(t.loc[("B", "stim vs rest, up"), "q"], 0.05)

    def test_a_level_without_control_cells_is_refused(self):
        with pt.inside(self.screen, os.path.join(self.tmp.name, "blocks")):
            with self.assertRaisesRegex(ValueError, "too few control cells"):
                pt.scores(contrast={"column": "condition_time", "levels": ["stim", "nowhere"]}, targets=["A"])

    def test_c_programs_scores_the_contrast_on_every_target(self):
        r = REGISTRY["C.programs"].run(self.screen, self.design, os.path.join(self.tmp.name, "m"),
                                       gene_sets="contrast", contrast=CONTRAST)
        hit = r.table[(r.table["target"] == "A") & (r.table["score"] == "stim vs rest, up")]
        self.assertLess(float(hit["effect"].iat[0]), 0)
        self.assertIn("stim vs rest, up", r.diagnostics["genes_used"])


if __name__ == "__main__":
    unittest.main()
