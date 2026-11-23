"""The composition step's backends (pertura.steps.composition; docs/adr/0031, phase 2): each finds a shift of a state
planted in every sample, calls no state that neither side has, returns one row shape, and names a target it cannot
fit. On the synthetic screen: four samples, T1 with 60% of its cells in state A against the control cells' 20%, T2
as the control cells, and a state Z that only T2's cells have."""
import importlib.util
import os
import shutil
import tempfile
import unittest

import numpy as np
import pandas as pd

from pertura.envs import R_HOME
from pertura.steps import composition as step

HAS_R = os.path.exists(f"{R_HOME}/bin/Rscript")
HAS_PERTPY = importlib.util.find_spec("pertpy") is not None
HAS_SCANPY = importlib.util.find_spec("scanpy") is not None


def labelled_screen(folder, units=("u1", "u2", "u3", "u4"), t1_fold=None):
    from test_common_response_and_field_measures import build_screen
    os.makedirs(folder, exist_ok=True)
    s, design = build_screen(folder, {"T1": (40, t1_fold or {}), "T2": (40, {})}, units=units, controls=12,
                             control_cells=20)
    # each cell's state drawn by its group's shares (the rest B), so the samples vary as real ones do
    shares = {"control": {"A": 0.2}, "T1": {"A": 0.6}, "T2": {"A": 0.2, "Z": 0.1}}
    rng = np.random.default_rng(3)
    lab = np.full(len(s.label), "B", dtype=object)
    for group, of in shares.items():
        idx = np.flatnonzero(s.label == group)
        states = list(of) + ["B"]
        lab[idx] = rng.choice(states, size=len(idx), p=list(of.values()) + [1 - sum(of.values())])
    return s, design, lab.astype(str)


def row(per, target, state):
    r = per[(per["target"] == target) & (per["state"] == state)]
    return r.iloc[0] if len(r) else None


class Base(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.s, self.design, self.lab = labelled_screen(self.d)

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def run_method(self, method, params=None):
        per, by_unit, calls = step.compare(self.s, ["T1", "T2"], "control_guides", self.lab, method,
                                           os.path.join(self.d, method), params=params)
        self.assertTrue(set(step.COLUMNS) <= set(per.columns), per.columns)
        self.assertTrue(len(by_unit) and set(by_unit["unit"]) == {"u1", "u2", "u3", "u4"})
        self.assertTrue(calls and all(c.get("package") for c in calls))
        return per

    def planted_found(self, per):
        self.assertTrue(bool(row(per, "T1", "A")["changed"]), per)
        self.assertGreater(row(per, "T1", "A")["mean_difference"], 0.25)
        self.assertFalse(bool(row(per, "T2", "A")["changed"]), per)  # T2's share of A is the control cells'
        z = row(per, "T1", "Z")  # Z is on neither of T1's sides
        self.assertTrue(z is None or not bool(z["changed"]), per)


class TestOnCells(Base):
    def test_fisher_is_the_modules_test(self):
        from pertura.question_modules import c
        per = self.run_method("fisher")
        self.planted_found(per)
        old, _ = c.composition_rows(self.s, ["T1", "T2"], "control_guides", self.lab)
        pd.testing.assert_frame_equal(old.reset_index(drop=True), per.reset_index(drop=True))
        self.assertEqual(set(per["method"]), {"fisher"})

    def test_cmh_stratifies_by_sample(self):
        per = self.run_method("cmh")
        self.planted_found(per)
        self.assertTrue((per["target_samples"] == 4).all())
        self.assertEqual(row(per, "T1", "Z")["p"], 1.0)  # nothing to test

    def test_a_target_in_too_few_samples_is_named_not_fitted(self):
        s, _, lab = labelled_screen(os.path.join(self.d, "one"), units=("u1",))
        per, _, calls = step.compare(s, ["T1", "T2"], "control_guides", lab, "cmh", os.path.join(self.d, "c1"))
        self.assertEqual(len(per), 0)
        self.assertEqual(calls[0]["not_fitted"], ["T1", "T2"])
        self.assertIn("cmh needs 2", calls[0]["not_fitted_why"]["T1"])

    def test_a_setting_the_backend_lacks_is_refused(self):
        with self.assertRaises(ValueError) as e:
            step.compare(self.s, ["T1"], "control_guides", self.lab, "fisher", self.d, params={"transform": "asin"})
        self.assertIn("its settings are", str(e.exception))
        with self.assertRaises(ValueError):
            step.compare(self.s, ["T1"], "control_guides", self.lab, "chisq", self.d)

    def test_each_target_by_its_lead_one_family(self):
        leads = {"T1": {"method": "fisher_same_samples", "reason": "r"}, "T2": {"method": "cmh", "reason": "r"}}
        per, _, calls = step.compare_by_leads(self.s, leads, "control_guides", self.lab, self.d)
        self.assertEqual(dict(per.groupby("target")["method"].first()), {"T1": "fisher", "T2": "cmh"})
        from statsmodels.stats.multitest import multipletests
        np.testing.assert_allclose(per["q"], multipletests(per["p"], method="fdr_bh")[1])


@unittest.skipUnless(HAS_R, "needs R (speckle)")
class TestPropeller(Base):
    def test_the_planted_shift_by_samples_each_transform(self):
        for transform in ("logit", "asin"):
            per = self.run_method("propeller", {"transform": transform})
            self.planted_found(per)
            self.assertEqual(set(per["proportion_transform"]), {transform})
            self.assertTrue(np.isfinite(row(per, "T1", "A")["prop_ratio"]))
        with self.assertRaises(ValueError):
            self.run_method("propeller", {"transform": "sqrt"})


@unittest.skipUnless(HAS_PERTPY, "needs pertpy (scCODA, JAX)")
class TestScCODA(Base):
    def test_the_planted_shift_is_a_credible_effect(self):
        per = self.run_method("sccoda", {"num_samples": 2000, "num_warmup": 500})
        self.assertTrue(per["p"].isna().all())  # credible effects, not p-values
        a = row(per, "T1", "A")
        ref = a["reference_state"]
        self.assertTrue(bool(a["changed"]) or ref == "A", per)  # the reference state has no effect of its own
        self.assertFalse(bool(row(per, "T2", "A")["changed"]), per)  # T2's share of A is the control cells'
        self.assertTrue(bool(row(per, "T2", "Z")["changed"]), per)  # Z: 10% of T2's cells, none of the controls'


@unittest.skipUnless(HAS_PERTPY and HAS_SCANPY, "needs pertpy and scanpy (Milo)")
class TestMilo(unittest.TestCase):
    def test_neighbourhoods_of_a_target_with_its_own_expression(self):
        d = tempfile.mkdtemp()
        try:
            s, _, lab = labelled_screen(d, t1_fold={f"g{j}": 6.0 for j in range(10)})
            per, _, calls = step.compare(s, ["T1", "T2"], "control_guides", lab, "milo", os.path.join(d, "milo"))
            self.assertTrue({"neighbourhood", "logFC", "state_fraction"} <= set(per.columns))
            t1, t2 = per[per["target"] == "T1"], per[per["target"] == "T2"]
            self.assertTrue((t1["changed"] & (t1["logFC"] > 0)).any(), t1.head())
            self.assertLess(t2["changed"].astype(bool).mean(), t1["changed"].astype(bool).mean())
        finally:
            shutil.rmtree(d, ignore_errors=True)


@unittest.skipUnless(HAS_SCANPY, "needs scanpy")
class TestTheBlock(unittest.TestCase):
    def test_pt_composition_takes_the_method_and_calibrates_by_it(self):
        from pertura import blocks as pt
        d = tempfile.mkdtemp()
        try:
            s, _, _ = labelled_screen(d)
            with pt.inside(s, os.path.join(d, "blocks")):
                st = pt.states(resolution=0.5)
                res = pt.composition(of=st, method="cmh")
                cal = pt.calibrate(res)
            self.assertEqual(set(res.table["method"]), {"cmh"})
            self.assertEqual(res.args.get("method"), "cmh")
            self.assertEqual(set(cal.table["method"]), {"cmh"})
            self.assertIn("group_pairs_changed", cal.extra["summary"])
            with pt.inside(s, os.path.join(d, "auto")):
                auto = pt.composition(of=pt.states(resolution=0.5), method="auto")
            self.assertTrue(set(auto.extra["leads"]) == {"T1", "T2"})  # the design's lead per target, named
        finally:
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
