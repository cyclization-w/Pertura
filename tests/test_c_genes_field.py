"""C.genes on the field's package (docs/build_goal_adr0022.md, step 3, item 2; bars 1, 2, 6 and 7): Seurat's
FindMarkers, MAST with the sample as a latent variable, changed at Seurat's adjusted p < 0.05; the calibration check
(groups of control guides tested as targets, reported only) and the positive controls; the call recorded and wrong
input refused."""
import json
import os
import tempfile
import unittest

import numpy as np
import pandas as pd

from pertura import envs
from pertura.question_modules import REGISTRY, packages

HAS_R = os.path.exists(envs.rscript())


def calibration_screen(folder, clone_offset=0.0, lanes=3, seed=0):
    """Lanes of one pool, CRISPRi: 12 control guides, target T (2 guides) lowering its own gene T by 70% and g0 by
    half, target N (2 guides) doing nothing. With clone_offset, each control guide's cells share a multiplicative
    offset on 20 genes (as the cells of one clone do), so control guides differ from each other."""
    import anndata as ad
    import scipy.sparse as sp
    from pertura.question_modules.data import Screen
    rng = np.random.default_rng(seed)
    genes = ["g0", "T", "N"] + [f"h{j}" for j in range(60)]
    ctrl = [f"CTRL_{i}" for i in range(1, 13)]
    offsets = {g: np.exp(rng.normal(0, clone_offset, len(genes))) * (np.arange(len(genes)) >= 43) +
               (np.arange(len(genes)) < 43) for g in ctrl} if clone_offset else {}
    rows = []
    for k in range(lanes):
        rows += [(f"L{k + 1}", g, "control") for g in ctrl for _ in range(40)]
        rows += [(f"L{k + 1}", f"{t}_{i}", t) for t in ("T", "N") for i in (1, 2) for _ in range(60)]
    obs = pd.DataFrame(rows, columns=["unit", "guide", "label"])
    mu = np.full((len(obs), len(genes)), 6.0)
    t = (obs["label"] == "T").to_numpy()
    mu[t, 0] *= 0.5
    mu[t, 1] *= 0.3
    for g, f in offsets.items():
        mu[(obs["guide"] == g).to_numpy()] *= f
    X = sp.csr_matrix(rng.poisson(mu).astype(np.float32))
    obs["guides"] = [f"{g}:20" for g in obs["guide"]]
    obs["control_group"] = np.where(obs["label"] == "control", "CTRL", "")
    obs.index = [f"c{i}" for i in range(len(obs))]
    a = ad.AnnData(X, obs=obs[["unit", "label", "guides", "control_group"]].copy(), var=pd.DataFrame(index=genes))
    guides = ctrl + [f"{t}_{i}" for t in ("T", "N") for i in (1, 2)]
    pd.DataFrame({"guide": guides, "target": ["CTRL"] * 12 + ["T", "T", "N", "N"],
                  "kind": ["non_targeting"] * 12 + ["targeting"] * 4}).to_csv(os.path.join(folder, "guides.tsv"),
                                                                             sep="\t", index=False)
    with open(os.path.join(folder, "screen.json"), "w", encoding="utf-8") as fh:
        json.dump({"unit_source": "lanes", "flags": []}, fh)
    design = {"perturbation": {"modality": {"value": "CRISPRi"}}, "units": {"independent_unit": {"value": "none"}},
              "experiment": {"context": {"value": "cell_line"}}}
    return Screen(folder, design, adata=a), design


class TestWrongInput(unittest.TestCase):
    def test_log_normalised_values_are_refused(self):
        with tempfile.TemporaryDirectory() as d:
            s, design = calibration_screen(d)
            s.X = s.X.copy()
            s.X.data = np.log1p(s.X.data)
            with self.assertRaises(packages.PackageInputError):
                REGISTRY["C.genes"].run(s, design, os.path.join(d, "out"))

    def test_named_genes_none_of_which_the_screen_holds_are_said_before_the_test(self):
        """GSE190604: a value quoted twice named a gene no screen holds, and the run tested every gene for 15 minutes
        before saying none of the named genes was tested."""
        with tempfile.TemporaryDirectory() as d:
            s, design = calibration_screen(d)
            r = REGISTRY["C.genes"].run(s, design, os.path.join(d, "out"), genes=['"targets_own"', "NOT_A_GENE"])
            self.assertEqual((r.status, len(r.table)), ("not_enough_data", 0))
            self.assertIn("none of the named genes", r.summary)


@unittest.skipUnless(HAS_R, "needs the R environment (Seurat, MAST)")
class TestFindMarkers(unittest.TestCase):
    def run_on(self, clone_offset, modality=None):
        with tempfile.TemporaryDirectory() as d:
            s, design = calibration_screen(d, clone_offset)
            if modality:
                design = {**design, "perturbation": {"modality": {"value": modality}}}
            return REGISTRY["C.genes"].run(s, design, os.path.join(d, "out"), method="field")  # MAST, by name

    @classmethod
    def setUpClass(cls):
        cls.clean = cls.run_on(cls, 0.0)
        cls.clonal = cls.run_on(cls, 0.6)
        cls.knockout = cls.run_on(cls, 0.0, "knockout")

    def test_planted_genes_are_found_and_the_null_target_changes_none(self):
        changed = self.clean.tables["changed_genes"]
        self.assertTrue({"g0", "T"} <= set(changed[changed["target"] == "T"]["gene"]))
        self.assertEqual(len(changed[changed["target"] == "N"]), 0)
        self.assertEqual(self.clean.diagnostics["method"], "field")

    def test_the_call_is_recorded(self):
        rec = self.clean.diagnostics["package_calls"][0]
        self.assertEqual((rec["package"], rec["function"], rec["layer"]), ("Seurat", "FindMarkers", "raw_counts"))
        self.assertIn("MAST", rec["formula"])
        self.assertNotEqual(rec["version"], "unknown")
        self.assertEqual(set(self.clean.tables["changed_genes"]["latent"]), {"sample"})

    def test_the_calibration_check_on_a_clean_screen(self):
        c = self.clean.diagnostics["calibration"]
        self.assertEqual((c["groups"], c["group_size"]), (5, 2))  # 12 control guides, targets of 2 guides
        self.assertEqual(c["discoveries"], 0)
        self.assertEqual((c["positive_controls"], c["positive_controls_found"]), (1, 1))  # T's own gene
        self.assertIn("calibration check", self.clean.summary.replace("tested as targets", "calibration check"))

    def test_a_knockout_targets_own_gene_is_not_a_power_check(self):
        """A knocked-out gene's transcript can stay (seven published screens say so), so the own gene is listed and not
        counted as a positive control."""
        c = self.knockout.diagnostics["calibration"]
        self.assertEqual((c["positive_controls"], c["knockout_target_genes"]), (0, 1))
        self.assertIn("not read as a power check", self.knockout.summary)
        pc = self.knockout.tables["positive_controls"]
        self.assertEqual(pc["power_check"].tolist(), [False])

    def test_the_calibration_check_shows_clone_like_control_guides(self):
        c = self.clonal.diagnostics["calibration"]
        self.assertGreater(c["discoveries"], 0)
        self.assertGreater(c["share_p_below_0.05"], 0.1)  # p-values below the uniform's 5%


if __name__ == "__main__":
    unittest.main()


def test_within_states_says_so_when_the_composition_selects_no_pair():
    """The default pairs are the composition's shifted ones; with none it failed on the empty table's 'tested'."""
    with tempfile.TemporaryDirectory() as d:
        s, design = calibration_screen(d)
        cells = pd.DataFrame({"barcode": np.asarray(s.adata.obs_names).astype(str), "state": "S1"})
        comp = pd.DataFrame({"target": ["T"], "state": ["S1"], "q": [0.9], "mean_difference": [0.01]})
        r = REGISTRY["C.within_states"].run(s, design, os.path.join(d, "out"), cell_states=cells, composition=comp)
    assert r.status == "not_enough_data", r.status
    assert "no target-state pair to test" in r.summary and r.table.empty


@unittest.skipUnless(HAS_R, "needs the R environment (Seurat, MAST)")
class TestWithinStates(unittest.TestCase):
    def test_a_change_within_a_state_is_found_and_an_inert_target_has_none(self):
        """C.within_states: within a state, the target lowering its gene T and g0 changes them against the control
        cells in that state; the inert target N changes nothing there."""
        with tempfile.TemporaryDirectory() as d:
            s, design = calibration_screen(d)
            rng = np.random.default_rng(1)
            cells = pd.DataFrame({"barcode": np.asarray(s.adata.obs_names).astype(str),
                                  "state": rng.choice(["S1", "S2"], len(s.label))})
            r = REGISTRY["C.within_states"].run(s, design, os.path.join(d, "out"), cell_states=cells,
                                                pairs=[["T", "S1"], ["N", "S1"]])
        t = r.table.set_index("target")
        self.assertGreaterEqual(t.loc["T", "genes_changed_within"], 1)
        self.assertEqual(t.loc["N", "genes_changed_within"], 0)
        found = set(r.tables["changed_within"]["gene"])
        self.assertTrue({"T", "g0"} & found)
