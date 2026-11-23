"""A target's control cells matched within its condition as well as its sample, where a sample holds several
conditions (item 9; four hashtag conditions in each lane of GSE278572). A target whose cells lean to one condition
must not read that condition's difference as its own; a screen whose samples hold one condition each is unchanged."""
import importlib.util
import json
import os
import re
import tempfile
import unittest

import anndata as ad
import numpy as np
import pandas as pd
import scipy.sparse as sp

from pertura.question_modules import c as mc
from pertura.question_modules.data import Screen, same_sample_controls

SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src", "pertura")
HAS_SCANPY = importlib.util.find_spec("scanpy") is not None


def _has_r():
    from pertura import envs
    return os.path.exists(envs.rscript())


HAS_R = _has_r()


def screen(d, crossed=True, lean=(10, 90), n_control=200, seed=0, units=("U1", "U2")):
    """Two units; in each, conditions A and B (crossed), or one condition per unit (not crossed). Condition B raises
    genes g0-g19 and puts most cells in state x; target T does nothing, its cells split lean[0] in A and lean[1] in B
    within each unit."""
    rng = np.random.default_rng(seed)
    rows = []
    for k, u in enumerate(units):
        for cond, n_t in zip(("A", "B"), lean):
            c = cond if crossed else "AB"[k % 2]
            rows += [(u, c, "control")] * n_control + [(u, c, "T")] * n_t
    obs = pd.DataFrame(rows, columns=["unit", "condition", "label"])
    genes = [f"g{i}" for i in range(60)]
    mu = np.full((len(obs), len(genes)), 2.0)
    mu[(obs["condition"] == "B").to_numpy(), :20] *= 4.0
    obs["guides"] = np.where(obs["label"] == "T", "T_1:20", "CTRL_1:20")
    obs["control_group"] = np.where(obs["label"] == "control", "CTRL", "")
    obs.index = [f"c{i}" for i in range(len(obs))]
    a = ad.AnnData(sp.csr_matrix(rng.poisson(mu).astype(np.float32)), obs=obs, var=pd.DataFrame(index=genes))
    pd.DataFrame({"guide": ["CTRL_1", "T_1"], "target": ["CTRL", "T"],
                  "kind": ["non_targeting", "targeting"]}).to_csv(os.path.join(d, "guides.tsv"), sep="\t", index=False)
    with open(os.path.join(d, "screen.json"), "w", encoding="utf-8") as fh:
        json.dump({"unit_source": "separate_samples", "flags": []}, fh)
    return Screen(d, {"units": {"independent_unit": {"value": "unit"}}}, adata=a)


class TestTheStrata(unittest.TestCase):
    def test_a_unit_with_two_conditions_is_crossed_and_matched_within_both(self):
        with tempfile.TemporaryDirectory() as d:
            s = screen(d)
            self.assertTrue(s.crossed)
            self.assertEqual(sorted(set(s.strata)), ["U1|A", "U1|B", "U2|A", "U2|B"])
            self.assertEqual(s.model_terms, ["unit", "condition"])
            self.assertIn("own samples and conditions", s.matching_words)
            tm, rm = s.cells_of("T"), s.reference("control_guides", target="T")
            kept, matched, left = s.matched(tm, rm)
            for u in ("U1", "U2"):  # the controls in the target's mix of conditions within each unit: 10 and 90
                share = (s.condition[matched & (s.unit == u)] == "B").mean()
                self.assertAlmostEqual(share, 0.9, places=2)
            self.assertEqual((int(kept.sum()), left), (200, 0))

    def test_a_condition_cut_from_a_crossed_screen_is_not_crossed(self):
        with tempfile.TemporaryDirectory() as d:
            s = screen(d)
            self.assertTrue(s.crossed)
            b = s.subset(condition="B")
            self.assertFalse(b.crossed)
            self.assertIs(b.strata, b.unit)

    def test_a_few_stray_cells_of_another_condition_do_not_cross_a_screen(self):
        from pertura.question_modules.data import CROSSED_CELLS
        with tempfile.TemporaryDirectory() as d:
            s = screen(d, crossed=False)
            one = s.with_labels(s.label)
            one.condition = s.condition.copy()
            first = np.flatnonzero(s.unit == "U1")
            one.condition[first[:CROSSED_CELLS - 1]] = "B"  # U1 is condition A: a few cells read B by mistake
            self.assertFalse(one.crossed)
            more = s.with_labels(s.label)
            more.condition = s.condition.copy()
            more.condition[first[:CROSSED_CELLS]] = "B"
            self.assertTrue(more.crossed)

    def test_a_screen_whose_units_hold_one_condition_each_matches_by_unit_as_before(self):
        with tempfile.TemporaryDirectory() as d:
            s = screen(d, crossed=False)
            self.assertFalse(s.crossed)
            self.assertIs(s.strata, s.unit)
            self.assertEqual(s.model_terms, ["unit"])
            self.assertEqual(s.matching_words, "the control cells of its own samples, in its mix")
            tm, rm = s.cells_of("T"), s.reference("control_guides", target="T")
            for a, b in zip(s.matched(tm, rm, 10, 3), same_sample_controls(s.unit, tm, rm, 10, 3)):
                self.assertTrue(np.array_equal(a, b))


class TestTheComparisons(unittest.TestCase):
    def test_the_composition_test_does_not_read_the_condition_as_the_targets_shift(self):
        with tempfile.TemporaryDirectory() as d:
            s = screen(d)
            rng = np.random.default_rng(1)
            lab = np.where(rng.random(len(s.label)) < np.where(s.condition == "B", 0.8, 0.2), "x", "y")
            tm, rm = s.cells_of("T"), s.reference("control_guides", target="T")
            by_unit = mc.composition_same_samples(s.unit, lab, tm, rm, "x")
            within = mc.composition_same_samples(s.unit, lab, tm, rm, "x", strata=s.strata)
            self.assertLess(by_unit["p"], 1e-6)  # against the unit's controls, the condition shows as a shift
            self.assertGreater(within["p"], 0.05)
            self.assertEqual(within["target_samples"], 2)  # the samples are still counted by unit

    def test_the_cmh_test_takes_each_sample_and_condition_as_a_stratum(self):
        from pertura.steps import composition
        with tempfile.TemporaryDirectory() as d:
            s = screen(d)
            rng = np.random.default_rng(1)
            lab = np.where(rng.random(len(s.label)) < np.where(s.condition == "B", 0.8, 0.2), "x", "y")
            per, by_unit, _ = composition.compare(s, ["T"], "control_guides", lab, method="cmh")
        row = per[per["state"] == "x"].iloc[0]
        self.assertGreater(row["p"], 0.05)
        self.assertIn("samples and conditions", row["test"])
        self.assertLess(by_unit.loc[by_unit["state"] == "x", "difference"].abs().max(), 0.1)  # drawn in its mix

    def test_two_targets_leaning_to_one_condition_are_not_read_as_alike(self):
        from pertura.steps import similarity
        with tempfile.TemporaryDirectory() as d:
            s = screen(d, lean=(10, 90))
            rng = np.random.default_rng(4)
            half = np.flatnonzero(s.label == "T")
            lab = s.label.copy()
            lab[rng.choice(half, len(half) // 2, replace=False)] = "T2"  # two targets, each leaning to B, doing nothing
            s2 = s.with_labels(lab)
            table, _, _ = similarity.pairs(s2, "target", ["T", "T2"], genes="all", min_cells=30)
            flat = s.subset(condition="A").with_labels(lab[s.condition == "A"])
        row = table[(table["group"].isin(["T", "T2"])) & (table["other"].isin(["T", "T2"]))].iloc[0]
        self.assertLess(abs(row["correlation"]), 0.3, row)  # the condition's genes are in neither profile
        self.assertFalse(flat.crossed)

    def test_the_all_controls_alternatives_say_they_pool_the_conditions(self):
        from pertura.steps import composition
        with tempfile.TemporaryDirectory() as d:
            s = screen(d)
            lab = np.where(s.condition == "B", "x", "y")
            per, _, calls = composition.compare(s, ["T"], "control_guides", lab, "fisher", control_cells="all")
            flat, _, _ = composition.compare(screen(d, crossed=False), ["T"], "control_guides", lab, "fisher",
                                             control_cells="all")
        self.assertIn("pooled over the conditions inside the samples", per["test"].iloc[0])
        self.assertIn("pooled over the conditions inside the samples", calls[0]["contrast"])
        self.assertEqual(flat["test"].iloc[0], "Fisher's exact test on cells")

    def test_the_profile_takes_the_reference_in_the_targets_mix_of_conditions(self):
        from pertura.steps import profiles
        with tempfile.TemporaryDirectory() as d:
            s = screen(d)
            prof = profiles.profiles(s, labels=["T"], genes="all", min_cells=30)
            for u, table in prof.items():  # the condition genes (g0-g19) are the condition's, not T's
                self.assertLess(float(table.loc["T", [f"g{i}" for i in range(20)]].abs().mean()), 0.1, u)
            values, m = s.lognorm, s.cells_of("T") & (s.unit == "U1")
            ref = s.reference("control_guides", target="T") & (s.unit == "U1")
            base, kept = s.baseline(values, m, ref)
            truth = sum(
                (m & (s.condition == c)).sum() * np.asarray(values[np.flatnonzero(ref & (s.condition == c))].mean(0))
                .ravel() for c in ("A", "B")) / m.sum()
            self.assertTrue(np.allclose(base, truth))
            self.assertTrue(np.array_equal(kept, m))

    @unittest.skipUnless(HAS_SCANPY, "needs scanpy (its Wilcoxon test)")
    def test_the_gene_test_says_its_controls_are_matched_within_the_conditions(self):
        with tempfile.TemporaryDirectory() as d:
            s = screen(d)
            table, call = mc.genes_wilcoxon_same_samples(s, ["T"], "control_guides", workers=1)
        self.assertIn("own samples and conditions", call["params"]["reference"])
        hit = table[(table["q"] < 0.05) & table["gene"].isin([f"g{i}" for i in range(20)])]
        self.assertTrue(hit.empty, hit)  # the condition's genes are not T's


class TestThePseudobulk(unittest.TestCase):
    """Per-unit pseudobulks stay; where a unit holds several conditions, each target is fitted against its own
    reference, REF_<target>, its control cells drawn within its samples' conditions (pertura/r/pseudobulk_de.R)."""

    def sheet(self, s, targets):
        from types import SimpleNamespace
        from unittest import mock
        with tempfile.TemporaryDirectory() as w:
            done = SimpleNamespace(returncode=0, stderr="", stdout="")
            with mock.patch.object(mc.subprocess, "run", return_value=done):  # the sheet R would read, R not run
                _, _, rec = mc.genes_edger(s, targets, "control_guides", w, min_cells=10)
            return pd.read_csv(os.path.join(w, "pseudobulk", "pb", "all__r0.samples.tsv"), sep="\t"), rec

    def test_each_target_has_its_own_reference_drawn_in_its_mix_where_crossed(self):
        from pertura.question_modules.data import seed_of
        with tempfile.TemporaryDirectory() as d:
            s = screen(d)
            sheet, rec = self.sheet(s, ["T"])
            tm = s.cells_of("T")
            _, ref, _ = s.matched(tm, s.reference("control_guides", target="T") & ~tm, 10, seed_of("T"))
        self.assertEqual(sorted(set(sheet["group"])), ["REF_t00001", "t00001"])
        own = sheet[sheet["group"] == "REF_t00001"].set_index("mouse")["n_cells"].to_dict()
        self.assertEqual(own, {u: int((ref & (s.unit == u)).sum()) for u in ("U1", "U2")})
        self.assertIn("own samples and conditions", rec["contrast"])

    def test_a_screen_whose_units_hold_one_condition_keeps_its_one_reference(self):
        with tempfile.TemporaryDirectory() as d:
            sheet, rec = self.sheet(screen(d, crossed=False), ["T"])
        self.assertEqual(sorted(set(sheet["group"])), ["REF", "t00001"])
        self.assertEqual(rec["contrast"], "each target against the control cells, in the samples they share")

    @unittest.skipUnless(HAS_R, "needs R with edgeR (pertura/r/pseudobulk_de.R)")
    def test_edger_does_not_read_the_condition_as_the_targets_genes(self):
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as w:
            s = screen(d, units=("U1", "U2", "U3", "U4"))
            table, _, _ = mc.genes_edger(s, ["T"], "control_guides", w, min_cells=10)
        self.assertFalse(table.empty)
        hit = table[(table["q"] < 0.05) & table["gene"].isin([f"g{i}" for i in range(20)])]
        self.assertTrue(hit.empty, hit)


class TestTheModels(unittest.TestCase):
    """Models on cells keep every control cell and take the conditions inside the samples as a term."""
    CONDITION_GENES = [f"g{i}" for i in range(20)]

    def test_the_score_test_takes_the_condition_as_a_term_where_crossed(self):
        with tempfile.TemporaryDirectory() as d:
            s = screen(d)
            score = np.where(s.condition == "B", 1.0, 0.0) + np.random.default_rng(2).normal(0, 0.5, len(s.label))
            tm, rm = s.cells_of("T"), s.reference("control_guides", target="T")
            _, p_sample, _ = mc.score_test(s.unit, score, tm, rm, "linear_model")
            _, p_both, how = mc.score_test(s.unit, score, tm, rm, "linear_model", s.condition)
            _, p_drawn, drawn = mc.score_test(s.unit, score, tm, rm, "wilcoxon", s.condition)
        self.assertLess(p_sample, 1e-6)  # the sample alone leaves the condition in T's score
        self.assertGreater(p_both, 0.05)
        self.assertEqual(how, "OLS, sample and condition as covariates")
        self.assertGreater(p_drawn, 0.05)
        self.assertIn("drawn within its samples and conditions", drawn)

    def test_the_lr_test_takes_the_condition_as_a_latent_variable_where_crossed(self):
        with tempfile.TemporaryDirectory() as d:
            s = screen(d)
            tm, rm = s.cells_of("T"), s.reference("control_guides", target="T")
            plain = mc.seurat3_lr_de(s, tm, rm)
            adjusted = mc.seurat3_lr_de(s, tm, rm, mc.with_conditions(s, None, tm | rm))
            self.assertIsNone(mc.with_conditions(screen(d, crossed=False), None, tm | rm))

        def called(t):
            return int(((t["p_val_adj"] < 0.05) & t["gene"].isin(self.CONDITION_GENES)).sum())
        self.assertGreater(called(plain), 0)  # pooled, the condition's genes show as T's
        self.assertEqual(called(adjusted), 0)

    def test_the_lr_paths_a_run_takes_adjust_for_the_condition_where_crossed(self):
        """The genes step's seurat3_lr backend and C.genes' Seurat 3 LR in its design default pass the conditions
        to the test (with_conditions); seurat3_lr_de called bare takes none, on any screen."""
        from pertura.steps import genes
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as w:
            s = screen(d)
            step, calls = genes._seurat3_lr(s, ["T"], "control_guides", w, 1)
            module = mc.genes_seurat3_lr(s, w)
        self.assertEqual(calls[0]["covariates"], ["condition"])
        hit = step[(step["q"] < 0.05) & step["gene"].isin(self.CONDITION_GENES)]
        self.assertTrue(hit.empty, hit)
        de = module.tables["de"]
        self.assertEqual(module.diagnostics["covariate"], "the sample and the condition, as latent variables")
        self.assertFalse(bool(de[de["gene"].isin(self.CONDITION_GENES)]["DE"].any()))

    def test_sceptre_is_given_the_condition_where_crossed(self):
        from unittest import mock

        class Stop(Exception):
            pass
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as w:
            s = screen(d)
            with mock.patch.object(mc.subprocess, "run", side_effect=Stop), self.assertRaises(Stop):
                mc.genes_sceptre(s, ["T"], "control_guides", w)  # the cells R would read; R not run
            cells = pd.read_csv(os.path.join(w, "sceptre", "cells.tsv"), sep="\t")
        self.assertEqual(sorted(set(cells["condition"])), ["A", "B"])

    @unittest.skipUnless(HAS_R, "needs R with Seurat and MAST (pertura/r/findmarkers_de.R)")
    def test_mast_does_not_read_the_condition_as_the_targets_genes(self):
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as w:
            s = screen(d)
            table, _, calls, _, _ = mc.genes_field(s, s.design, ["T"], "control_guides", w, calibrate=False)
        self.assertIn("condition", calls[0]["formula"])
        hit = table[(table["q"] < 0.05) & table["gene"].isin(self.CONDITION_GENES)]
        self.assertTrue(hit.empty, hit)


class TestOnePlaceDecides(unittest.TestCase):
    def test_no_comparison_draws_its_controls_by_unit_alone(self):
        """Every draw goes through Screen.strata (Screen.matched): a call that passes a screen's units to
        same_sample_controls would match a crossed screen's controls across its conditions."""
        bad = []
        for root, _, files in os.walk(SRC):
            for f in files:
                if not f.endswith(".py"):
                    continue
                text = open(os.path.join(root, f), encoding="utf-8").read()
                for m in re.finditer(r"same_sample_controls\(\s*([^,]+),", text):
                    if re.search(r"\.unit\b", m.group(1)):
                        bad.append(f"{f}: {m.group(0)}")
                for m in re.finditer(r"composition_same_samples\([^)]*\)", text):
                    if "def " not in text[max(0, m.start() - 4):m.start()] and "strata=" not in m.group(0):
                        bad.append(f"{f}: {m.group(0)[:80]}")
        self.assertEqual(bad, [])


if __name__ == "__main__":
    unittest.main()
