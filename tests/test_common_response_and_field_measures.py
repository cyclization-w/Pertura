"""Batch c of development session 1: Pertura's own measures replaced by what published screens do
(docs/build/development_sessions/measures_without_field_basis.md), each tested against planted effects:
- a response every perturbation shares is read against the other targets' cells (D.common_response);
- CRISPRa against CRISPRi of one gene, each against the control cells, read against different genes;
- a pair's departure from its singles read against control pseudo-pairs at its cell numbers;
- the plan asks the common-response question with 3 or more targets, and the cutting question wherever control
  guides that target where no gene is sit beside non-targeting ones;
- the depth-matched check runs whenever the two sides' depths differ."""
import importlib.util
import json
import os
import tempfile
import unittest

import numpy as np
import pandas as pd

from pertura import design_fit, record as rc
from pertura import question_modules as qm

HAS_PERTPY = importlib.util.find_spec("pertpy") is not None


def build_screen(folder, groups, units=("u1",), genes=120, controls=12, control_cells=20, seed=0, guides=None,
                 design=None):
    """A screen object in folder and its Screen. groups: {label: (cells per unit, {gene: fold})}; genes g0.. plus
    any gene a group names; controls: control guides CTRL_k with control_cells cells each per unit; guides:
    {label: (guide names, target)} (default two guides L_1, L_2 with the label as target)."""
    import anndata as ad
    import scipy.sparse as sp
    from pertura.question_modules.data import Screen
    rng = np.random.default_rng(seed)
    named = sorted({g for _, fx in groups.values() for g in fx})
    names = named + [f"g{j}" for j in range(genes - len(named))]
    gi = {g: j for j, g in enumerate(names)}
    base = rng.gamma(4, 5, len(names))
    guides = guides or {lab: ([f"{lab}_1", f"{lab}_2"], lab) for lab in groups}
    rows = []
    for u in units:
        for lab, (n, _) in groups.items():
            gl = guides[lab][0]
            rows += [(u, gl[k % len(gl)], lab) for k in range(n)]
        rows += [(u, f"CTRL_{k}", "control") for k in range(1, controls + 1) for _ in range(control_cells)]
    obs = pd.DataFrame(rows, columns=["unit", "guide", "label"])
    mu = np.tile(base, (len(obs), 1))
    for lab, (_, fx) in groups.items():
        m = obs["label"].to_numpy() == lab
        for g, fold in fx.items():
            mu[m, gi[g]] *= fold
    lam = rng.gamma(5.0, mu * np.exp(rng.normal(0, 0.2, len(obs)))[:, None] / 5.0)
    X = sp.csr_matrix(rng.poisson(lam).astype(np.float32))
    obs["guides"] = [f"{g}:{rng.integers(5, 40)}" for g in obs["guide"]]
    obs["control_group"] = np.where(obs["label"] == "control", obs["guide"], "")
    obs.index = [f"c{i}" for i in range(len(obs))]
    a = ad.AnnData(X, obs=obs[["unit", "label", "guides", "control_group"]].copy(), var=pd.DataFrame(index=names))
    table = [(g, t, "targeting") for lab, (gl, t) in guides.items() for g in gl]
    table += [(f"CTRL_{k}", f"CTRL_{k}", "non_targeting") for k in range(1, controls + 1)]
    pd.DataFrame(table, columns=["guide", "target", "kind"]).to_csv(os.path.join(folder, "guides.tsv"), sep="\t",
                                                                    index=False)
    with open(os.path.join(folder, "screen.json"), "w", encoding="utf-8") as fh:
        json.dump({"unit_source": "separate_samples", "flags": []}, fh)
    design = design or {"perturbation": {"modality": {"value": "CRISPRi"}},
                        "units": {"independent_unit": {"value": "none"}}}
    return Screen(folder, design, adata=a), design


@unittest.skipUnless(HAS_PERTPY, "needs pertpy")
class TestACommonResponse(unittest.TestCase):
    @staticmethod
    def groups(n_targets):
        shift = {f"s{i}": 1.8 for i in range(20)}  # every targeting group: the shared part
        groups = {f"T{k}": (80, dict(shift)) for k in range(1, n_targets + 1)}
        groups["T1"][1].update({f"o{i}": 3.0 for i in range(20)})  # T1's own effect
        return groups

    def test_only_a_target_with_an_effect_of_its_own_differs_from_the_other_targets(self):
        with tempfile.TemporaryDirectory() as d:
            screen, design = build_screen(d, self.groups(20))
            os.makedirs(os.path.join(d, "ae"))
            against_controls = qm.REGISTRY["C.any_effect"].fn(screen, design, os.path.join(d, "ae"),
                                                              permutations=200)  # fewer, for speed
            r = qm.REGISTRY["D.common_response"].run(screen, design, os.path.join(d, "cr"), permutations=200)
        q_ctrl = against_controls.table.set_index("target")["q"]
        self.assertTrue((q_ctrl < 0.05).all(), q_ctrl)  # the shared part makes every target differ from controls
        q = r.table.set_index("target")["q"]
        self.assertLess(q["T1"], 0.05)
        self.assertLessEqual(int((q.drop("T1") < 0.05).sum()), 1, q)
        self.assertIn("other targets' cells", r.summary)

    def test_with_few_targets_one_targets_effect_sits_in_each_others_reference(self):
        # the contract's failure case: with 6 targets, T1's cells are a fifth of every other target's reference
        with tempfile.TemporaryDirectory() as d:
            screen, design = build_screen(d, self.groups(6))
            r = qm.REGISTRY["D.common_response"].run(screen, design, os.path.join(d, "cr"), permutations=200)
        t = r.table.set_index("target")
        self.assertGreater(int((t["q"].drop("T1") < 0.05).sum()), 1, t["q"])
        self.assertGreater(t.at["T1", "e_distance"], 5 * t["e_distance"].drop("T1").max())


class TestCrisprAAgainstCrisprI(unittest.TestCase):
    def test_opposite_effects_of_one_gene_lie_below_different_genes(self):
        prog = [f"p{i}" for i in range(30)]
        groups, guides, modes = {}, {}, {}
        for k, gene in enumerate(["G1", "G2", "G3", "G4"]):
            for arm, fold in (("a", 2.0), ("i", 0.5)):
                lab = f"{gene}-{arm}"
                fx = {gene: fold}
                if gene == "G1":  # activation raises the program, interference lowers it
                    fx.update({p: (1.8 if arm == "a" else 0.55) for p in prog})
                elif arm == "a":  # the others: an unrelated effect in one arm only
                    fx.update({f"x{k}_{i}": 2.0 for i in range(15)})
                groups[lab] = (120, fx)
                guides[lab] = ([f"{lab}1", f"{lab}2"], gene)
                modes.update({g: "CRISPRa" if arm == "a" else "CRISPRi" for g in guides[lab][0]})
        design = {"perturbation": {"modality": {"value": "other"}, "guide_modes": {"value": modes}},
                  "units": {"independent_unit": {"value": "none"}}}
        with tempfile.TemporaryDirectory() as d:
            screen, design = build_screen(d, groups, guides=guides, design=design)
            r = qm.REGISTRY["E.opposite_modes"].run(screen, design, os.path.join(d, "om"))
        t = r.table.set_index("gene")
        self.assertLess(t.loc["G1", "correlation_top"], -0.5)
        self.assertLessEqual(t.loc["G1", "different_gene_pairs_as_low"], 0.05)
        self.assertIn("G1-a / G1-i", r.diagnostics["below_the_null"])
        self.assertGreater(t.loc["G2", "different_gene_pairs_as_low"], 0.05)
        self.assertEqual(r.diagnostics["different_gene_pairs"], 12)


class TestPairsAgainstControlPseudoPairs(unittest.TestCase):
    def test_a_planted_interaction_stands_out_and_an_additive_pair_does_not(self):
        a = {f"a{i}": 2.0 for i in range(25)}
        b = {f"b{i}": 2.0 for i in range(25)}
        c = {f"c{i}": 0.5 for i in range(25)}
        extra = {f"x{i}": 2.5 for i in range(25)}  # only in the A|B pair: what neither single explains
        groups = {"A": (60, dict(a)), "B": (60, dict(b)), "C": (60, dict(c)),
                  "A|B": (60, {**a, **b, **extra}), "A|C": (60, {**a, **c})}
        guides = {"A": (["A_1"], "A"), "B": (["B_1"], "B"), "C": (["C_1"], "C"),
                  "A|B": (["A_1"], "A"), "A|C": (["A_1"], "A")}
        with tempfile.TemporaryDirectory() as d:
            screen, design = build_screen(d, groups, units=("u1", "u2", "u3"), control_cells=25, guides=guides)
            r = qm.REGISTRY["E.combinations"].run(screen, design, os.path.join(d, "pairs"))
        t = r.table.set_index("pair")
        self.assertGreater(t.loc["A|B", "control_pseudo_pairs"], 0)
        self.assertLessEqual(t.loc["A|B", "control_pseudo_pairs_as_high"], 0.05)
        self.assertGreater(t.loc["A|C", "control_pseudo_pairs_as_high"], 0.05)
        self.assertNotIn("0.2 or more", str(qm.REGISTRY["E.combinations"].contract.fails_when))

    def test_a_pair_in_one_sample_is_fitted_and_its_agreement_not_checked(self):
        """With one sample, a pair and its singles are still fitted (Norman et al. 2019), with their cells; the
        agreement between units, and the pseudo-pairs against it, need 2 or more units, so the result is measured."""
        a = {f"a{i}": 2.0 for i in range(25)}
        b = {f"b{i}": 2.0 for i in range(25)}
        extra = {f"x{i}": 2.5 for i in range(25)}
        groups = {"A": (80, dict(a)), "B": (80, dict(b)), "A|B": (80, {**a, **b, **extra})}
        guides = {"A": (["A_1"], "A"), "B": (["B_1"], "B"), "A|B": (["A_1"], "A")}
        with tempfile.TemporaryDirectory() as d:
            screen, design = build_screen(d, groups, units=("u1",), control_cells=60, guides=guides)
            r = qm.REGISTRY["E.combinations"].run(screen, design, os.path.join(d, "pairs"))
        row = r.table.set_index("pair").loc["A|B"]
        self.assertEqual((r.status, r.diagnostics["testable"], r.diagnostics["with_the_agreement_check"]),
                         ("measured", 1, 0))
        self.assertEqual((int(row["cells"]), int(row["cells_A"]), int(row["units_for_agreement"])), (80, 80, 1))
        self.assertTrue(np.isnan(row["interaction_replicate_r"]))
        self.assertGreater(row["interaction_size"], 0.3)
        self.assertIn("the departure's agreement is not checked", r.summary)


class TestCalibrationOfStatesAndGeneSets(unittest.TestCase):
    """The skeptic's null is the finding's calibration check now: the finding's own measure on groups of control
    guides, each against the rest of the control cells (development session 1 used it on a state and gene sets)."""

    def state_screen(self, d):
        groups = {f"T{k}": (120, {}) for k in range(1, 4)}
        screen, design = build_screen(d, groups, controls=12, control_cells=30)
        rng = np.random.default_rng(3)
        p = np.where(screen.label == "T1", 0.6, 0.1)  # T1 moves cells into S1; the others do not
        return screen, np.where(rng.random(len(screen.label)) < p, "S1", "S2")

    def test_a_state_shift_lies_beyond_the_control_groups_and_none_within(self):
        from pertura import finding_checks as fc
        with tempfile.TemporaryDirectory() as d:
            screen, lab = self.state_screen(d)
            out = {}
            for t in ("T1", "T2"):
                spec = {"target": t, "feature": "S1", "direction": 1, "kind": "state", "reference": "control_guides",
                        "state_labels": lab}
                _, checks = fc.run_checks(screen, spec, os.path.join(d, f"work_{t}"))
                out[t] = {c["check"]: c for c in checks}["calibration"]
        self.assertEqual(out["T1"]["outcome"], "beyond the control groups")
        self.assertGreater(out["T1"]["groups"], 0)
        self.assertEqual(out["T2"]["outcome"], "within the control groups' range")

    @unittest.skipUnless(importlib.util.find_spec("scanpy") is not None, "needs scanpy")
    def test_a_gene_set_rise_lies_beyond_the_control_groups(self):
        from pertura import finding_checks as fc
        genes = [f"h{i}" for i in range(15)]
        groups = {"T1": (120, {g: 2.0 for g in genes}), "T2": (120, {}), "T3": (120, {})}
        with tempfile.TemporaryDirectory() as d:
            screen, design = build_screen(d, groups, controls=12, control_cells=30)
            spec = {"target": "T1", "feature": "H", "direction": 1, "kind": "gene_set", "genes": genes,
                    "reference": "control_guides"}
            _, checks = fc.run_checks(screen, spec, os.path.join(d, "work"))
        self.assertEqual({c["check"]: c for c in checks}["calibration"]["outcome"], "beyond the control groups")


class TestThePlanAndTheChecks(unittest.TestCase):
    def plan(self, design, n_targets=5):
        return {q["question_id"] for q in rc.plan_questions(design, {"n_targets": n_targets, "guides_per_target": 2})}

    def test_the_common_response_needs_targets_to_compare_and_the_shared_response_is_gone(self):
        design = {"perturbation": {"modality": {"value": "CRISPRi"},
                                   "controls": {"value": [{"name": "NTC", "kind": "non_targeting",
                                                           "guides": ["n1"]}]}}}
        self.assertIn("D.common_response", self.plan(design))
        self.assertNotIn("D.common_response", self.plan(design, n_targets=2))
        self.assertNotIn("D.shared_response", self.plan(design))
        self.assertNotIn("D.cutting_response", self.plan(design))  # only non-targeting controls

    def test_binding_controls_beside_non_targeting_ones_plan_the_cutting_question_under_crispri(self):
        controls = [{"name": "NTC", "kind": "non_targeting", "guides": ["n1"]},
                    {"name": "safe", "kind": "safe_targeting", "guides": ["s1"]}]
        design = {"perturbation": {"modality": {"value": "CRISPRi"}, "controls": {"value": controls}}}
        self.assertIn("D.cutting_response", self.plan(design))

    def test_similar_targets_has_no_shared_response_option(self):
        self.assertNotIn("shared_response", qm.REGISTRY["E.similar_targets"].contract.choices)

    def test_depth_matching_is_no_longer_planned_when_depths_differ(self):
        """The field adjusts for depth in the test; depth matching left the automatic checks (the open points' F5)."""
        with tempfile.TemporaryDirectory() as d:
            screen, design = build_screen(d, {"T1": (60, {"e1": 2.0})})
            screen.libsize = np.where(screen.label == "T1", 1.05, 1.0) * screen.libsize  # 5% deeper only
            plan = design_fit.checks("gene", screen, "T1")
        self.assertEqual([c for c in plan if c["check"] == "depth_matched"], [])
        self.assertFalse(hasattr(design_fit, "DEPTH_RATIO"))


if __name__ == "__main__":
    unittest.main()
