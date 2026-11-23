"""Cells along a trajectory (docs/adr/0044, D1): a diffusion pseudotime (scanpy's tl.diffmap and tl.dpt) from a root
the agent names, and each target's cells along it against the control cells of its own samples, as published screens
place perturbations along a differentiation or activation path. On a screen whose cells lie along one continuum,
target A's cells sit late on it and target B's where the control cells' do."""
import importlib.util
import json
import os
import tempfile
import unittest

import numpy as np
import pandas as pd

from pertura.question_modules import REGISTRY
from pertura.question_modules.c import trajectory_shifts

HAS_SCANPY = importlib.util.find_spec("scanpy") is not None and importlib.util.find_spec("igraph") is not None


def screen_along_a_continuum(folder, seed=0):
    """Four mice; every cell at a place tau in [0, 1] along one continuum, where genes up0-up29 rise and down0-down29
    fall; target A's cells lie in the later half, target B's and the control cells' all along it. A cell column
    'stage' marks the cells before tau 0.1 as early."""
    import anndata as ad
    import scipy.sparse as sp
    from pertura.question_modules.data import Screen
    rng = np.random.default_rng(seed)
    genes = [f"up{i}" for i in range(30)] + [f"down{i}" for i in range(30)] + [f"h{i}" for i in range(300)]
    rows = []
    for unit in ("u1", "u2", "u3", "u4"):
        for guide, label in (("A_1", "A"), ("B_1", "B"), ("NTC_1", "control"), ("NTC_2", "control")):
            rows += [(unit, guide, label)] * 60
    obs = pd.DataFrame(rows, columns=["unit", "guide", "label"])
    tau = np.where(obs["label"] == "A", rng.uniform(0.5, 1, len(obs)), rng.uniform(0, 1, len(obs)))
    mu = np.full((len(obs), len(genes)), 4.0)
    mu[:, :30] = 1 + 24 * tau[:, None]
    mu[:, 30:60] = 25 - 24 * tau[:, None]
    X = sp.csr_matrix(rng.poisson(mu).astype(np.float32))
    obs["control_group"] = np.where(obs["label"] == "control", "NTC", "")
    obs["guides"] = [f"{g}:20" for g in obs["guide"]]
    obs["stage"] = np.where(tau < 0.1, "early", "later")
    obs.index = [f"c{i}" for i in range(len(obs))]
    a = ad.AnnData(X, obs=obs[["unit", "label", "guides", "control_group", "stage"]].copy(),
                   var=pd.DataFrame(index=genes))
    pd.DataFrame({"guide": ["A_1", "B_1", "NTC_1", "NTC_2"], "target": ["A", "B", "NTC", "NTC"],
                  "kind": ["targeting"] * 2 + ["non_targeting"] * 2}).to_csv(os.path.join(folder, "guides.tsv"),
                                                                              sep="\t", index=False)
    with open(os.path.join(folder, "screen.json"), "w", encoding="utf-8") as fh:
        json.dump({"unit_source": "separate_samples", "flags": [], "rule": {"rule": "dominance"}}, fh)
    design = {"perturbation": {"modality": {"value": "CRISPRi"}},
              "units": {"independent_unit": {"value": "mouse"}, "n_units": {"value": 4}},
              "experiment": {"organism": {"value": "human"}}}
    return Screen(folder, design, adata=a), design, tau


class TestEachTargetsShiftAlongAnAxis(unittest.TestCase):
    def test_a_target_late_on_the_axis_moves_past_the_control_guides_band(self):
        with tempfile.TemporaryDirectory() as d:
            s, _, tau = screen_along_a_continuum(d)
        table, band, guides = trajectory_shifts(s, tau)
        t = table.set_index("target")
        self.assertGreater(t.loc["A", "shift"], 0.15)
        self.assertLess(t.loc["A", "q"], 0.05)
        self.assertTrue(bool(t.loc["A", "past_band"]))
        self.assertGreater(t.loc["B", "q"], 0.05)
        self.assertEqual(guides, 2)
        self.assertEqual(list(table["target"])[0], "A")  # the largest shift first


@unittest.skipUnless(HAS_SCANPY, "needs scanpy and igraph")
class TestCellsAlongATrajectory(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        folder = os.path.join(self.tmp.name, "screen")
        os.makedirs(folder)
        self.screen, self.design, self.tau = screen_along_a_continuum(folder)

    def tearDown(self):
        self.tmp.cleanup()

    def test_the_pseudotime_follows_the_continuum_and_places_the_late_target(self):
        r = REGISTRY["C.states_and_composition"].run(self.screen, self.design, os.path.join(self.tmp.name, "m"),
                                                     trajectory_root={"stage": "early"}, naming_sets="none")
        from scipy.stats import spearmanr
        pseudotime = r.tables["cell_states"]["pseudotime"].to_numpy(float)
        self.assertGreater(spearmanr(pseudotime, self.tau, nan_policy="omit")[0], 0.8)
        t = r.tables["trajectory"].set_index("target")
        self.assertGreater(t.loc["A", "shift"], 0)
        self.assertLess(t.loc["A", "q"], 0.05)
        self.assertGreater(t.loc["B", "q"], 0.05)
        self.assertTrue(r.biology[0].startswith("A: later along the pseudotime from stage early"), r.biology)
        self.assertIn("along a diffusion pseudotime from stage early", r.summary)
        self.assertEqual(r.figure_checks.get("layout_issues"), [])
        self.assertTrue(os.path.exists(os.path.join(self.tmp.name, "m", "states.png")))

    def test_a_root_without_cells_is_refused(self):
        with self.assertRaisesRegex(ValueError, "no cell in the root"):
            REGISTRY["C.states_and_composition"].run(self.screen, self.design, os.path.join(self.tmp.name, "m"),
                                                     trajectory_root="S99", naming_sets="none")


if __name__ == "__main__":
    unittest.main()
