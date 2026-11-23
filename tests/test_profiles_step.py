"""Profiles and the replicate agreement (docs/adr/0031, D1a; ADR 0022's routine step: the screen's reproducibility,
replicate against replicate, before pooling).
- A planted effect in three samples gives a high r between them; noise alone none.
- E.similar_targets' profiles read the step: the same numbers as before on a screen without matched references.
- F.replicates_agree runs as its recipe and is planned where the screen has 2 or more samples."""
import os
import tempfile
import unittest

import numpy as np

from test_common_response_and_field_measures import build_screen

EFFECT = {f"g{i}": 3.0 for i in range(20)}


def screen(d, effect=True):
    groups = {"T1": (60, dict(EFFECT) if effect else {}), "T2": (60, {})}
    return build_screen(d, groups, units=("u1", "u2", "u3"), genes=150, controls=6, control_cells=40)


class TestReplicateAgreement(unittest.TestCase):
    def test_a_planted_effect_agrees_between_the_samples(self):
        from pertura.steps import profiles
        with tempfile.TemporaryDirectory() as d:
            s, _ = screen(d)
            pairs, per = profiles.replicate_agreement(s, genes="all")
        self.assertEqual(len(pairs), 3)  # u1-u2, u1-u3, u2-u3
        self.assertTrue((pairs["r"] > 0.5).all(), pairs)
        t1 = per[per["target"] == "T1"]["r"]
        t2 = per[per["target"] == "T2"]["r"]
        self.assertTrue((t1 > 0.5).all() and (t2.abs() < t1.min()).all(), per)

    def test_noise_alone_does_not(self):
        from pertura.steps import profiles
        with tempfile.TemporaryDirectory() as d:
            s, _ = screen(d, effect=False)
            pairs, _ = profiles.replicate_agreement(s, genes="all")
        self.assertTrue((pairs["r"].abs() < 0.4).all(), pairs)

    def test_profiles_against_each_samples_control_cells(self):
        from pertura.steps import profiles
        with tempfile.TemporaryDirectory() as d:
            s, _ = screen(d)
            by = profiles.profiles(s, ["T1"], ["u1"], genes="all")
            L = s.lognorm
            m, r = (s.label == "T1") & (s.unit == "u1"), (s.label == "control") & (s.unit == "u1")
            expected = np.asarray(L[np.flatnonzero(m)].mean(0)).ravel() - np.asarray(L[np.flatnonzero(r)].mean(0)).ravel()
        self.assertTrue(np.allclose(by["u1"].loc["T1"].to_numpy(), expected))
        with self.assertRaises(ValueError):
            profiles.profiles(s, ["T1"], ["u1"], genes="variable")


class TestSimilarTargetsReadTheStep(unittest.TestCase):
    def test_the_same_profiles_as_the_modules_own_code_gave(self):
        from pertura.question_modules.e import DETECTION, unit_profiles
        with tempfile.TemporaryDirectory() as d:
            s, _ = screen(d)
            out = unit_profiles(s, ["u2"], min_cells=30)
            L = s.lognorm
            mr = (s.label == "control") & (s.unit == "u2")
            keep = np.flatnonzero(s.detected(mr, DETECTION))
            Lu = L[:, keep]
            base = np.asarray(Lu[np.flatnonzero(mr)].mean(0)).ravel()
            m = (s.label == "T2") & (s.unit == "u2")
            old = np.asarray(Lu[np.flatnonzero(m)].mean(0)).ravel() - base
        self.assertEqual(sorted(out["u2"]), ["T1", "T2"])
        self.assertTrue(np.allclose(out["u2"]["T2"].to_numpy(), old))


class TestTheModule(unittest.TestCase):
    def test_f_replicates_agree_runs_its_recipe(self):
        from pertura.question_modules import REGISTRY
        with tempfile.TemporaryDirectory() as d:
            s, design = screen(d)
            r = REGISTRY["F.replicates_agree"].run(s, design, os.path.join(d, "out"), genes="all")
            self.assertTrue(os.path.exists(os.path.join(d, "out", "figure.png")))
        self.assertEqual(r.status, "measured")
        self.assertEqual(len(r.tables["pairs"]), 3)
        self.assertIn("median r", r.summary)
        self.assertIn("pt.replicate_agreement(genes='all')", r.diagnostics["recipe_as_run"])

    def test_planned_where_the_screen_has_two_or_more_samples(self):
        from pertura import record as rc
        design = {"perturbation": {"modality": {"value": "CRISPRi"}}}
        planned = lambda n: {q["question_id"] for q in rc.plan_questions(  # noqa: E731
            design, {"n_targets": 10, "guides_per_target": 2, "n_units": n})}
        self.assertIn("F.replicates_agree", planned(3))
        self.assertNotIn("F.replicates_agree", planned(1))


if __name__ == "__main__":
    unittest.main()
