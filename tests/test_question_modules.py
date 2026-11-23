"""The analysis modules (docs/adr/0020, section 2; build step 4): every module has the six contract fields, serves a
question of the plan, and declares its choices with the first as its default; on a synthetic screen with planted
effects each module finds what was planted. Modules that need scanpy (states, PCA) are tested where it is installed;
their checks against the learning screens run on the analysis server (analysis/end_to_end/check_modules_on_learning_
screens.py)."""
import importlib.util
import inspect
import json
import os
import tempfile
import unittest

import numpy as np
import pandas as pd
import scipy.sparse as sp
from scipy import stats

from pertura import question_modules as qm
from pertura import record as rc
from pertura.question_modules import b as mb
from pertura.question_modules import c as mc
from pertura.question_modules.contract import FIELDS
from pertura.question_modules.data import Screen

HAS_SCANPY = importlib.util.find_spec("scanpy") is not None
OFF_THE_PLAN = {  # follow-up moves: the agent adds their question when a result triggers one (follow_up_moves.md)
               "C.within_states", "G.direct_targets", "E.program_axis", "E.co_functional_sets",
               "A.screen_overview"}  # started by code when the first look's runs it reads have ended
ALL_QUESTIONS = None


def all_questions():
    """Every question ID the plan's rules can produce (a design and screen that make every rule apply)."""
    design = {"perturbation": {"modality": {"value": ["CRISPRa", "CRISPRi", "knockout"]},
                               "vectors": {"value": [["a", "b"]]}, "sets": {"value": [["a", "b"]]},
                               "controls": {"value": [{"name": "NTC", "kind": "non_targeting", "guides": ["n"]},
                                                      {"name": "safe", "kind": "safe_targeting", "guides": ["s"]}]}},
              "experiment": {"context": {"value": "in_vivo"}, "conditions": {"value": ["x", "y"]},
                             "time_points": {"value": [1, 2]}, "organism": {"value": "mouse"}},
              "units": {"independent_unit": {"value": "mouse"}, "n_units": {"value": 4}}}
    return {q["question_id"] for q in rc.plan_questions(design, {"n_targets": 5, "guides_per_target": 3,
                                                                 "pairs": True, "n_units": 4})}


def synthetic_screen(folder, seed=0):
    """Four mice; targets A (guide A_1 knocks its gene down, A_2 does not), B (both guides work; B's cells are one
    burst of B_1 in mouse u1) and C (its gene is not in the matrix); control guides NTC_1 and NTC_2; cells without a
    guide. Every target shares a shift of genes s0-s9 (a shared response); target B also raises genes b0-b4."""
    rng = np.random.default_rng(seed)
    genes = ["A", "B"] + [f"s{i}" for i in range(10)] + [f"b{i}" for i in range(5)] + [f"g{i}" for i in range(60)]
    gi = {g: i for i, g in enumerate(genes)}
    rows = []
    for u in ("u1", "u2", "u3", "u4"):
        for guide, label, n in (("A_1", "A", 40), ("A_2", "A", 40), ("B_1", "B", 60 if u == "u1" else 0),
                                ("C_1", "C", 30), ("NTC_1", "control", 40), ("NTC_2", "control", 40), ("", "none", 50)):
            rows += [(u, guide, label)] * n
    obs = pd.DataFrame(rows, columns=["unit", "guide", "label"])
    obs["control_group"] = np.where(obs["label"] == "control", "NTC", "")
    mu = np.full((len(obs), len(genes)), 4.0)
    mu[:, gi["A"]] = 8.0
    mu[:, gi["B"]] = 8.0
    mu[(obs["guide"] == "A_1").to_numpy(), gi["A"]] = 1.0
    mu[(obs["label"] == "B").to_numpy(), gi["B"]] = 1.0
    targets = obs["label"].isin(["A", "B", "C"]).to_numpy()
    for i in range(10):  # up and down by the same counts, so the cells' totals stay
        mu[targets, gi[f"s{i}"]] *= 1.8 if i % 2 == 0 else 0.2
    for i in range(5):
        mu[(obs["label"] == "B").to_numpy(), gi[f"b{i}"]] *= 4.0
    X = sp.csr_matrix(rng.poisson(mu).astype(np.float32))
    obs["guides"] = [f"{g}:{rng.integers(3, 30)}" if g else "" for g in obs["guide"]]
    obs.index = [f"c{i}" for i in range(len(obs))]
    import anndata as ad
    a = ad.AnnData(X, obs=obs[["unit", "label", "guides", "control_group"]].copy(), var=pd.DataFrame(index=genes))
    pd.DataFrame({"guide": ["A_1", "A_2", "B_1", "C_1", "NTC_1", "NTC_2"],
                  "target": ["A", "A", "B", "C", "NTC", "NTC"],
                  "kind": ["targeting"] * 4 + ["non_targeting"] * 2}).to_csv(os.path.join(folder, "guides.tsv"),
                                                                              sep="\t", index=False)
    with open(os.path.join(folder, "screen.json"), "w", encoding="utf-8") as fh:
        json.dump({"unit_source": "separate_samples", "flags": [], "rule": {"rule": "dominance"}}, fh)
    design = {"perturbation": {"modality": {"value": "CRISPRi"}},
              "units": {"independent_unit": {"value": "mouse"}, "n_units": {"value": 4}},
              "experiment": {"organism": {"value": "human"}}}
    return Screen(folder, design, adata=a), design


def screen_with_many_control_guides(folder, seed=0):
    """Two mice; target T (four guides, 50 cells each in each mouse) raises genes g0-g9, target U (four guides) moves
    nothing; eight control guides of 25 cells each in each mouse, so a group of four of them holds 200 cells, fewer than
    each target's 400."""
    rng = np.random.default_rng(seed)
    genes = [f"g{i}" for i in range(60)]
    rows = []
    for u in ("u1", "u2"):
        rows += [(u, f"{t}_{j}", t) for t in ("T", "U") for j in range(1, 5) for _ in range(50)]
        rows += [(u, f"NTC_{j}", "control") for j in range(1, 9) for _ in range(25)]
    obs = pd.DataFrame(rows, columns=["unit", "guide", "label"])
    obs["control_group"] = np.where(obs["label"] == "control", "NTC", "")
    mu = np.full((len(obs), len(genes)), 4.0)
    mu[(obs["label"] == "T").to_numpy(), :10] *= 3.0
    X = sp.csr_matrix(rng.poisson(mu).astype(np.float32))
    obs["guides"] = [f"{g}:{rng.integers(3, 30)}" for g in obs["guide"]]
    obs.index = [f"c{i}" for i in range(len(obs))]
    import anndata as ad
    a = ad.AnnData(X, obs=obs[["unit", "label", "guides", "control_group"]].copy(), var=pd.DataFrame(index=genes))
    guides = sorted(set(obs["guide"]))
    pd.DataFrame({"guide": guides, "target": [g.split("_")[0] for g in guides],
                  "kind": ["non_targeting" if g.startswith("NTC") else "targeting" for g in guides]}).to_csv(
        os.path.join(folder, "guides.tsv"), sep="	", index=False)
    with open(os.path.join(folder, "screen.json"), "w", encoding="utf-8") as fh:
        json.dump({"unit_source": "separate_samples", "flags": [], "rule": {"rule": "dominance"}}, fh)
    design = {"perturbation": {"modality": {"value": "CRISPRi"}},
              "units": {"independent_unit": {"value": "mouse"}, "n_units": {"value": 2}},
              "experiment": {"organism": {"value": "human"}}}
    return Screen(folder, design, adata=a), design


class TestContracts(unittest.TestCase):
    def test_a_result_says_its_tests_what_was_corrected_together_and_how_its_targets_were_chosen(self):
        from pertura.question_modules.contract import (CORRECTED_TOGETHER, Result, multiple_testing,
                                                       multiple_testing_words)
        self.assertLessEqual(set(CORRECTED_TOGETHER), set(qm.REGISTRY))
        self.assertIn("within the target", qm.REGISTRY["C.genes"].describe()["corrected_together"])
        self.assertNotIn("corrected_together", qm.REGISTRY["F.guides_agree"].describe())
        table = pd.DataFrame({"target": ["A", "B", "C"], "p": [0.01, np.nan, 0.2]})
        r = Result("detected", table, {}, "", {})
        mt = multiple_testing("C.any_effect", r)
        self.assertEqual((mt["tests"], mt["targets"]), (2, "every target with enough cells"))
        r.diagnostics = {"multiple_testing": {"tests": 3000}}  # the module's own count: every gene of every target
        mt = multiple_testing("C.genes", r, targets=["A", "B"])
        self.assertEqual((mt["tests"], mt["targets"]), (3000, "the 2 targets given, chosen before this run"))
        self.assertIn("3000 tests, corrected together: each target's genes, within the target",
                      multiple_testing_words(mt))
        self.assertEqual(multiple_testing_words({}), "")
        # a method that corrects in its own way says its own family; a method that tests given groups says so
        mt = multiple_testing("C.genes", Result("detected", table, {}, "", {}), choices={"method": "seurat3_lr",
                                                                                   "groups": {"a": ["A"], "b": ["B"]}})
        self.assertIn("Bonferroni within the group", mt["corrected_together"])
        self.assertEqual(mt["targets"], "the 2 groups given, chosen before this run")
        mt = multiple_testing("C.genes", Result("detected", table, {"method": "rank_test"}, "", {}),
                              choices={"method": "auto"})
        self.assertIn("within the target and unit", mt["corrected_together"])
        mt = multiple_testing("C.states_and_composition", Result("detected", table, {}, "", {}),
                              choices={"test": "sccoda"})
        self.assertIn("scCODA's own false discovery rate", mt["corrected_together"])
        mt = multiple_testing("G.meaning", Result("detected", table, {}, "", {}), choices={"method": "gsea"})
        self.assertIn("GSEA's permutation false discovery rate", mt["corrected_together"])
        # a run that tested nothing says no family
        self.assertIsNone(multiple_testing("C.any_effect", Result("not_enough_data", table, {}, "", {})))

    def test_every_module_has_the_six_fields_and_serves_a_planned_question(self):
        questions = all_questions()
        self.assertGreater(len(qm.REGISTRY), 12)
        for name, m in qm.REGISTRY.items():
            d = m.describe()
            for f in FIELDS:
                self.assertTrue(d[f], (name, f))
            self.assertEqual(name, m.contract.serves)
            if name in OFF_THE_PLAN:  # an agent may still add its question (add_question)
                self.assertNotIn(m.contract.serves, questions, name)
                continue
            self.assertIn(m.contract.serves, questions, name)
            for how, check in m.contract.fails_when:
                self.assertTrue(how and check, name)

    def test_each_choice_lists_its_default_first(self):
        for name, m in qm.REGISTRY.items():
            params = inspect.signature(m.fn).parameters
            for choice, options in m.contract.choices.items():
                self.assertIn(choice, params, (name, choice))
                if params[choice].default is None and choice == "min_cells":  # Pertura's number in effect
                    from pertura import policy
                    self.assertIn(options[0], (policy.DEFAULTS["min_cells"], policy.DEFAULTS["min_cells_profile"]),
                                  (name, choice))
                    continue
                self.assertEqual(params[choice].default, options[0], (name, choice))

    def test_the_questions_of_a_to_d_in_build_step_4_have_modules(self):
        step4 = {"A.what_was_done", "B.guide_calls_and_their_evidence", "B.vector_structure", "B.efficacy",
                 "B.escape", "B.dose", "B.abundance", "B.clonality", "C.any_effect", "C.genes", "C.programs",
                 "C.states_and_composition", "D.common_response"}
        self.assertEqual(step4 - set(qm.REGISTRY), set())
        # Pertura's own measures that no published screen uses are off the product path: their code stays
        # (docs/build/development_sessions/measures_without_field_basis.md)
        self.assertEqual({"D.shared_response", "D.the_choice_of_reference"} & set(qm.REGISTRY), set())

    def test_the_questions_of_e_to_h_in_build_step_6_have_modules(self):
        step6 = {"E.similar_targets", "E.opposite_modes", "E.combinations", "E.condition_x_perturbation",
                 "G.meaning"}
        self.assertEqual(step6 - set(qm.REGISTRY), set())
        self.assertNotIn("H.what_next", qm.REGISTRY)  # the agent proposes the validation (pertura_research.retired)

    def test_each_input_is_a_parameter_without_a_varied_default(self):
        for name, m in qm.REGISTRY.items():
            params = inspect.signature(m.fn).parameters
            for given in m.contract.inputs:
                self.assertIn(given, params, (name, given))
                self.assertNotIn(given, m.contract.choices, (name, given))

    def test_an_unknown_choice_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            s, design = synthetic_screen(d)
            with self.assertRaises(ValueError):
                qm.REGISTRY["B.efficacy"].run(s, design, os.path.join(d, "out"), nonsense=1)


class TestOnASyntheticScreen(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.screen, cls.design = synthetic_screen(cls.tmp.name)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def run_module(self, name, **choices):
        out = os.path.join(self.tmp.name, name)
        r = qm.REGISTRY[name].run(self.screen, self.design, out, **choices)
        self.assertTrue(os.path.exists(os.path.join(out, "result.tsv")))
        self.assertTrue(os.path.exists(r.figure))
        with open(os.path.join(out, "result.json"), encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["status"], r.status)
        if name != "A.what_was_done":
            self.assertNotIn("units_as_dots", r.figure_checks, name)  # not a figure rule (docs/adr/0022, section 4)
            # a figure of shares compares nothing: no reference or direction to show (None), never a missing one
            self.assertNotIn(False, [r.figure_checks[k] for k in ("reference_shown", "direction_labelled",
                                                                   "zero_centred")], (name, r.figure_checks))
        return r

    def test_a_summarises_cells_by_kind_and_unit(self):
        r = self.run_module("A.what_was_done")
        self.assertEqual(r.diagnostics["units"], 4)
        self.assertEqual(r.diagnostics["control_cells"], 4 * 80)

    def test_guide_calls_count_cells_per_guide_and_unit(self):
        r = self.run_module("B.guide_calls_and_their_evidence")
        per = r.tables["cells_per_guide"].set_index(["guide", "unit"])["cells"]
        self.assertEqual(per[("A_1", "u2")], 40)
        self.assertEqual(r.diagnostics["cells_by_kind"]["no guide"], 200)

    def test_efficacy_reads_a_knockouts_unchanged_transcript_as_such(self):
        """Replogle et al. 2022's rule (at least 30% down); under knockout, a guide that leaves its target gene's
        transcript is 'transcript unchanged' (a knocked-out gene's transcript can stay)."""
        design = {**self.design, "perturbation": {**self.design["perturbation"],
                                                  "modality": {"value": "knockout"}}}
        r = qm.REGISTRY["B.efficacy"].run(self.screen, design, os.path.join(self.tmp.name, "efficacy-ko"))
        v = r.tables["guides"].set_index("guide")["verdict"]
        self.assertEqual((v["A_1"], v["A_2"]), ("at least 30% down", "transcript unchanged"))
        self.assertIn("leave their transcript unchanged", r.summary)
        self.assertAlmostEqual(r.diagnostics["threshold"], -np.log2(0.7), places=3)

    def test_efficacy_places_each_guide_against_the_line(self):
        r = self.run_module("B.efficacy")
        self.assertIn("A_1", r.diagnostics["past_the_line"])
        self.assertIn("A_2", r.diagnostics["short_of_the_line"])
        self.assertIn("C_1", r.diagnostics["not_measurable"])
        self.assertIn("B_1", r.diagnostics["past_the_line"])

    def test_dose_finds_the_discordant_target(self):
        r = self.run_module("B.dose")
        self.assertEqual([d["target"] for d in r.diagnostics["discordant"]], ["A"])

    @unittest.skipUnless(importlib.util.find_spec("pertpy") is not None, "needs pertpy (Mixscape)")
    def test_escape_by_mixscape_calls_the_perturbed_cells(self):
        """B.escape's default is pertpy's Mixscape (step 3, item 6): B's cells raise b0-b4, so most are perturbed."""
        r = self.run_module("B.escape")
        self.assertEqual((r.status, r.diagnostics["method"]), ("measured", "mixscape"))
        t = r.table.set_index("target")
        self.assertGreater(t.loc["B", "share_perturbed"], 0.5)
        self.assertEqual(set(r.tables["cell_classes"]["class"]) - {"perturbed", "not perturbed", "control"}, set())
        self.assertIn("Mixscape", r.diagnostics["package_calls"][1]["function"])

    @unittest.skipUnless(importlib.util.find_spec("pertpy") is not None, "needs pertpy (Mixscape)")
    def test_mixscape_leaves_a_target_with_one_cell_in_a_sample_not_called(self):
        """A target with a single cell in a sample: Mixscape's marker test cannot run there, so that cell is not
        called and the rest are (CRISPRai's LYL1-a)."""
        lab = self.screen.label.copy()
        lone = np.flatnonzero((lab == "control") & (self.screen.unit == "u2"))[0]
        lab[lone] = "B"  # B's cells are otherwise all in u1
        out = os.path.join(self.tmp.name, "B.escape_lone")
        os.makedirs(out, exist_ok=True)
        r = mb.escape_by_mixscape(self.screen.with_labels(lab), out)
        c = r.tables["cell_classes"].set_index("barcode")
        self.assertEqual(c.loc[str(self.screen.adata.obs_names[lone]), "class"], "not called")
        self.assertEqual(r.diagnostics["cells_not_called"], 1)
        self.assertGreater(r.table.set_index("target").loc["B", "share_perturbed"], 0.5)

    def test_escape_reports_detection_against_the_reference(self):
        r = self.run_module("B.escape", method="own_gene")
        pooled = r.diagnostics["pooled"]
        self.assertLess(pooled["B"]["detected_target"], pooled["B"]["detected_reference"])

    @unittest.skipUnless(HAS_SCANPY, "needs scanpy (tl.score_genes)")
    def test_a_target_in_one_mouse_is_tested_and_its_n_stated(self):
        """ADR 0022 (section 3): a result with few mice is reported with its n, never as "not testable"; the test
        is the field's, with the sample as a covariate."""
        r = self.run_module("C.programs", gene_sets="given", gene_lists={"B set": [f"b{i}" for i in range(5)],
                                                                           "S": [f"s{i}" for i in range(10)]})
        t = r.table.set_index(["target", "score"])
        self.assertEqual(t.loc[("B", "B set"), "test"], "OLS, sample as a covariate")
        self.assertEqual((t.loc[("B", "B set"), "target_samples"], t.loc[("A", "S"), "target_samples"]), (1, 4))
        self.assertLess(t.loc[("B", "B set"), "q"], 0.05)

    def test_a_feature_named_as_a_guide_stays_a_gene(self):
        # docs/adr/0041, step 6: dropping it took a target's own gene where a guide bore its name; the build's reading
        # checks name such features now, and the old drop is tested in tests_research
        import anndata as ad
        s = self.screen
        a = s.adata
        extra = ad.AnnData(sp.csr_matrix(np.full((a.n_obs, 1), 7.0, np.float32)), obs=a.obs,
                           var=pd.DataFrame(index=["A_1"]))
        with_guide = ad.concat([a, extra], axis=1, merge="same")
        from pertura.question_modules.data import Screen
        t = Screen(s.dir, s.design, adata=with_guide)
        self.assertIn("A_1", t.gene_index)
        self.assertEqual(len(t.genes), len(s.genes) + 1)
        np.testing.assert_allclose(t.libsize, s.libsize + 7)

    def test_a_label_naming_its_mode_finds_its_own_gene_through_its_guides(self):
        s = self.screen
        label = s.label.copy()
        s.label = np.where(label == "B", "B-i", label)
        try:
            self.assertEqual(s.own_gene_of("B-i"), "B")
            pooled = self.run_module("B.escape", method="own_gene").diagnostics["pooled"]
        finally:
            s.label = label
        self.assertIn("B-i (B)", pooled)

    def test_escape_reports_each_member_of_a_combination(self):
        s = self.screen
        label = s.label.copy()
        s.label = np.where(label == "B", "A+B", label)
        out = os.path.join(self.tmp.name, "escape-combination")
        os.makedirs(out, exist_ok=True)
        try:
            r = qm.REGISTRY["B.escape"].fn(s, self.design, out, method="own_gene")
        finally:
            s.label = label
        pooled = r.diagnostics["pooled"]
        self.assertIn("A+B (A)", pooled)
        self.assertIn("A+B (B)", pooled)

    def test_the_power_rule_needs_cells_and_detection(self):
        self.assertGreater(mb.detection_power(200, 1000, 0.5, -1), 0.99)
        self.assertLess(mb.detection_power(20, 1000, 0.02, -1), 0.2)
        self.assertGreater(mb.detection_power(300, 1000, 0.05, 1), mb.detection_power(30, 1000, 0.05, 1))
        self.assertGreater(mb.detection_power(100, 1000, 0.0, 1), 0.9)  # a silent gene switched on can be seen
        self.assertEqual(mb.detection_power(100, 1000, 0.0, -1), 0.0)  # a silent gene cannot be seen to fall

    def test_abundance_and_clonality_find_the_one_unit_burst(self):
        r = self.run_module("B.abundance")  # against the control guides (ADR 0022 build, step 3, item 1)
        self.assertEqual(r.status, "measured")  # no test of our own, no cut-off
        self.assertNotIn("carried_by_one_unit", r.diagnostics)
        t = r.table.set_index("target")
        self.assertEqual(r.diagnostics["control_median_cells_per_guide"], 160)  # NTC_1, NTC_2: 40 in each of 4 mice
        self.assertAlmostEqual(t.loc["A", "log2_vs_control_guides"], 0.0, places=6)  # 160 a guide, as the controls
        self.assertAlmostEqual(t.loc["B", "log2_vs_control_guides"], np.log2(61 / 161), places=6)
        self.assertEqual(t.loc["B", "largest_guide_sample_share"], 1.0)  # all of B's cells: B_1 in u1
        self.assertEqual(r.diagnostics["above_every_control_guide"], [])
        pg = r.tables["per_guide"]
        self.assertEqual(pg[pg["guide"] == "B_1"][["unit", "cells"]].values.tolist(), [["u1", 60]])
        one = Screen(self.tmp.name, self.design, adata=self.screen.adata[self.screen.unit == "u1"].copy())
        out = os.path.join(self.tmp.name, "abundance-one-unit")
        r1 = qm.REGISTRY["B.abundance"].run(one, self.design, out)
        t1 = r1.table.set_index("target")  # in u1 alone, B's burst of 60 is above the control guides' 40 each
        self.assertEqual(r1.diagnostics["above_every_control_guide"], ["B"])
        self.assertEqual(t1.loc["B", "largest_guide_sample_share"], 1.0)  # and the table shows it is one burst
        c = self.run_module("B.clonality")
        b = c.table[c.table["group"] == "B"]  # the shares, without a cut-off: B's cells are B_1 in u1 alone
        self.assertEqual(b[["unit", "top_guide", "top_share"]].values.tolist(), [["u1", "B_1", 1.0]])
        self.assertEqual(c.status, "measured")

    def test_abundance_names_a_target_above_the_controls_within_one_sample_but_not_one_guides_burst(self):
        """Session 11: the pooled count said "above every control guide: none", while the paper's most increased
        target rose in one condition (a hashtag sample). Within each sample a target is named only with two of its
        guides holding cells there, so one guide's burst (B_1 in u1) is not named."""
        r = self.run_module("B.abundance")
        b = r.tables["per_sample_against_controls"].set_index(["target", "unit"])
        self.assertFalse(b.loc[("B", "u1"), "above_every_control_guide"])  # one guide (B_1) holds its cells
        from pertura.question_modules.b import _above_controls_in_each_sample
        per_unit = pd.DataFrame({"guide": ["T_1", "T_2", "C_1", "C_2"] * 2,
                                 "unit": ["u1"] * 4 + ["u2"] * 4,
                                 "cells": [40, 40, 40, 40, 100, 90, 40, 45], "target": ["T", "T", "", ""] * 2})
        lib = pd.DataFrame({"guide": ["T_1", "T_2", "C_1", "C_2"], "target": ["T", "T", "NTC", "NTC"]})
        out = _above_controls_in_each_sample(per_unit, lib, {"C_1", "C_2"}).set_index(["target", "unit"])
        self.assertTrue(out.loc[("T", "u2"), "above_every_control_guide"])
        self.assertFalse(out.loc[("T", "u1"), "above_every_control_guide"])

    @unittest.skipUnless(os.path.exists(__import__("pertura.envs", fromlist=["mageck"]).mageck()),
                         "needs MAGeCK (the analysis server)")
    def test_abundance_by_mageck_finds_the_target_expanded_in_one_mouse(self):
        """MAGeCK's test, normalised to the control guides: B's 60 cells are all in u1 (u1 against u2 to u4)."""
        out = os.path.join(self.tmp.name, "abundance-mageck")
        r = qm.REGISTRY["B.abundance"].run(self.screen, self.design, out, treatment=["u1"], versus=["u2", "u3", "u4"])
        m = r.tables["mageck"].set_index("target")
        self.assertEqual(m.index[0], "B")
        self.assertLess(m.loc["B", "fdr"], 0.05)
        self.assertEqual(m.loc["B", "direction"], "up")
        self.assertEqual(r.diagnostics["package_calls"][0]["package"], "MAGeCK")
        self.assertIn("0.5.9", r.diagnostics["package_calls"][0]["params"]["mageck_version"])

    def test_abundance_keeps_a_genes_crispri_and_crispra_guides_apart(self):
        """A dual-modality screen labels A_1's cells A-i and A_2's A-a: two targets, as the screen labels them, not
        one gene."""
        guide = self.screen.top_guide
        lab = np.where(guide == "A_1", "A-i", np.where(guide == "A_2", "A-a", self.screen.label))
        out = os.path.join(self.tmp.name, "abundance-two-modalities")
        r = qm.REGISTRY["B.abundance"].run(self.screen.with_labels(lab), self.design, out)
        t = r.table.set_index("target")
        self.assertEqual((t.loc["A-i", "cells_per_guide"], t.loc["A-a", "cells_per_guide"]), ("A_1:160", "A_2:160"))
        self.assertNotIn("A", t.index)

    def test_abundance_counts_a_vector_of_several_guides_as_one_construct(self):
        design = {**self.design, "perturbation": {**self.design["perturbation"],
                                                  "vectors": {"value": {"A_vec": ["A_1", "A_2"]}}}}
        out = os.path.join(self.tmp.name, "abundance-vectors")
        r = qm.REGISTRY["B.abundance"].run(self.screen, design, out)
        t = r.table.set_index("target")
        self.assertEqual(t.loc["A", "cells_per_guide"], "A_vec:320")  # A_1's and A_2's 160 cells, one construct
        self.assertAlmostEqual(t.loc["A", "log2_vs_control_guides"], np.log2(321 / 161), places=6)

    def test_the_off_path_measures_still_run_as_code(self):
        """Off the product path, not deleted: the functions run (research code), but no plan or agent reaches them."""
        from pertura.question_modules import d as md
        for name in ("shared", "choice"):
            os.makedirs(os.path.join(self.tmp.name, name), exist_ok=True)
        r = md.shared_response(self.screen, self.design, os.path.join(self.tmp.name, "shared"))
        self.assertEqual(r.status, "measured")
        r = md.the_choice_of_reference(self.screen, self.design, os.path.join(self.tmp.name, "choice"))
        self.assertFalse(r.tables["reference_shifts"].empty)

    def test_genes_by_rank_test_find_the_planted_genes(self):
        r = self.run_module("C.genes", method="rank_test")
        changed = r.tables["changed_genes"]
        b = set(changed[changed["target"] == "B"]["gene"])
        self.assertTrue({f"b{i}" for i in range(5)} <= b, b)
        self.assertEqual(r.diagnostics["method"], "rank_test")

    @unittest.skipUnless(HAS_SCANPY, "needs scanpy (tl.score_genes)")
    def test_programs_on_given_sets_find_the_shared_and_specific_shifts(self):
        """scanpy's scores, tested as C.genes is (step 3, item 4): a shift is reported with its n, never as not
        testable."""
        from pertura import gene_sets
        original = gene_sets.msigdb
        gene_sets.msigdb = lambda collection, organism: {"UP": ["s0", "s2", "s4", "s6", "s8"],
                                                         "B_SET": [f"b{i}" for i in range(5)],
                                                         "NOTHING": [f"g{i}" for i in range(5)]}
        try:
            r = self.run_module("C.programs")
        finally:
            gene_sets.msigdb = original
        t = r.table.set_index(["target", "score"])
        hits = set(t[t["q"] < 0.05].index)
        self.assertIn(("A", "UP"), hits)
        self.assertGreater(t.loc[("B", "B_SET"), "mean_difference"], 0.5)
        self.assertIn(("B", "B_SET"), hits)  # B's cells are one mouse's: reported, with that n
        self.assertEqual(t.loc[("B", "B_SET"), "target_samples"], 1)
        self.assertLess(abs(t.loc[("A", "NOTHING"), "mean_difference"]),
                        0.1 * abs(t.loc[("A", "UP"), "mean_difference"]))
        self.assertEqual(r.diagnostics["genes_used"]["UP"], 5)
        self.assertEqual(r.diagnostics["package_calls"][0]["function"], "tl.score_genes")

    @unittest.skipUnless(HAS_SCANPY, "needs scanpy")
    def test_any_effect_ranks_the_strongest_target_first(self):
        out = os.path.join(self.tmp.name, "C.any_effect")
        os.makedirs(out, exist_ok=True)
        r = qm.REGISTRY["C.any_effect"].fn(self.screen, self.design, out, permutations=50)  # fewer, for speed
        self.assertEqual(r.tables["targets"]["target"].iat[0], "B")
        self.assertEqual([c["function"] for c in r.diagnostics["package_calls"]][1], "tl.DistanceTest")
        self.assertLess(r.tables["targets"].set_index("target").loc["B", "q"], 0.05)

    @unittest.skipUnless(HAS_SCANPY and importlib.util.find_spec("pertpy") is not None, "needs scanpy and pertpy")
    def test_the_overviews_distance_reads_each_target_against_groups_of_its_own_cells(self):
        from pertura.steps import any_effect
        per, calls = any_effect.distances(self.screen)
        row = per.set_index("target")
        self.assertEqual(row["e_distance"].idxmax(), "B")  # the strongest target, as C.any_effect ranks it
        line = row.loc["B", "effect_line"]
        if np.isfinite(line):  # with 3 or more control-guide groups holding B's cells
            self.assertGreater(row.loc["B", "e_distance"], line)
        self.assertEqual(calls[1]["function"], "tl.Distance")

    @unittest.skipUnless(HAS_SCANPY and importlib.util.find_spec("pertpy") is not None, "needs scanpy and pertpy")
    def test_a_target_with_more_cells_than_a_group_of_control_guides_is_still_read_against_its_line(self):
        # development runs of 2026-11-05: no line for 16 of 23 targets (GSE250558), 11 of 11 (GSE203592), 26 of 28
        # (GSE267740), where the targets held more cells than the groups or the control guides made too few groups
        from pertura.steps import any_effect
        with tempfile.TemporaryDirectory() as d:
            s, _ = screen_with_many_control_guides(d)
            per, _ = any_effect.distances(s)
        row = per.set_index("target")
        self.assertEqual(row.loc["T", "cells_drawn"], 200)  # what a group of four control guides holds
        self.assertEqual(row.loc["T", "line_groups"], any_effect.LINE_GROUPS)  # 20 of the 70 sets of four
        self.assertGreater(row.loc["T", "e_distance"], row.loc["T", "effect_line"])
        self.assertLess(row.loc["U", "e_distance"], row.loc["U", "effect_line"])

    @unittest.skipUnless(HAS_SCANPY, "needs scanpy")
    def test_states_and_composition_write_states_and_markers(self):
        r = self.run_module("C.states_and_composition")
        self.assertEqual(len(r.tables["cell_states"]), len(self.screen.label))
        self.assertTrue({"umap_1", "umap_2"} <= set(r.tables["cell_states"].columns))  # later figures use one map
        # the states keep their IDs for the agent to name (GSE267740 on 667f20a: names taken from the top markers were
        # genes every cell expresses, Tpt1 and Xist); the markers table gives each gene's shares inside and outside
        named = r.tables["cell_states"].drop_duplicates("state").set_index("state")["state_name"].to_dict()
        self.assertEqual({s: n for s, n in named.items() if s}, r.diagnostics["state_names"])
        self.assertTrue(all(n == s for s, n in r.diagnostics["state_names"].items()))
        self.assertTrue({"pct_nz_group", "pct_nz_reference"} <= set(r.tables["markers"].columns))
        # the summary itself lists each state's top markers with their shares inside/outside (GSE267740 on a15df12:
        # with the markers in a table only, the 21 states reached the report unnamed)
        first = r.tables["markers"].sort_index(kind="stable").iloc[0]
        self.assertIn(f"{first['state']}: ", r.summary)
        self.assertRegex(r.summary, r"S\d+: \S+ \d+/\d+")
        dots = r.tables["marker_dots"]  # each state's markers measured in every state
        self.assertEqual(set(dots.groupby("gene")["state"].nunique()), {len(r.diagnostics["state_names"])})
        # each target by the design's test (docs/adr/0031, phase 2's notes): propeller where 3 or more mice hold both
        # sides, else Fisher's exact test against the control cells of its own samples (B's cells are one mouse's)
        tests = r.table.groupby("target")["test"].first()
        self.assertEqual(tests["B"], "Fisher's exact test on cells, against same-sample control cells")
        self.assertEqual(tests["A"], "propeller (speckle), the samples' proportions")
        self.assertEqual(r.diagnostics["leads"]["A"]["method"], "propeller")
        self.assertEqual(set(r.table[r.table["target"] == "B"]["target_samples"]), {1})
        b = r.table[(r.table["target"] == "B") & (r.table["q"] < 0.05)]
        self.assertTrue((b["share"] > b["control_share"]).any())  # B's raised genes make a state of its own
        self.assertEqual(r.diagnostics["package_calls"][1]["function"], "stats.fisher_exact")


class TestModulesEToH(unittest.TestCase):
    @classmethod
    def setUpClass(cls):  # its own synthetic screen, so the class runs alone or in any order
        cls.tmp = tempfile.TemporaryDirectory()
        cls.screen, cls.design = synthetic_screen(cls.tmp.name)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    @unittest.skipUnless(HAS_SCANPY, "needs scanpy (tl.score_genes)")
    def test_condition_x_perturbation_finds_the_planted_interaction(self):
        from synthetic_screen import make_condition_screen
        with tempfile.TemporaryDirectory() as d:
            s, design, sets = make_condition_screen(d, seed=1)
            r = qm.REGISTRY["E.condition_x_perturbation"].run(s, design, os.path.join(d, "out"), gene_sets="given",
                                                               gene_lists=sets)
            by_units = qm.REGISTRY["E.condition_x_perturbation"].run(s, design, os.path.join(d, "units"),
                                                                      gene_sets="given", gene_lists=sets,
                                                                      test="units")
        t = r.table.set_index("target")
        self.assertLess(t.loc["INT", "p"], 0.05)
        self.assertGreater(t.loc["INT", "interaction"], 0)
        # the design's test: the synthetic screen's wells are not named as replicates, so on cells
        self.assertEqual(t.loc["INT", "test"], "cells, each sample in each condition its own baseline")
        self.assertEqual((t.loc["INT", "cells stim"], t.loc["INT", "samples stim"]), (240, 4))
        self.assertGreater(t.loc["NUL", "p"], 0.05)
        u = by_units.table.set_index("target")
        self.assertEqual(u.loc["INT", "test"], "Welch over units")
        self.assertLess(u.loc["INT", "p"], 0.05)

    def test_combinations_report_testable_pairs_and_the_departure(self):
        s = self.screen
        label = s.label.copy()
        # in units u1-u3 half of B's... A's cells become the pair 'A|C' (C has no gene effect): the pair carries A's own
        # effect on gene A, so it is additive; a pair without its singles in 2 units is not testable
        s.label = np.where((label == "A") & (s.top_guide == "A_1"), "A|C", label)
        s.label = np.where((label == "C") & (s.unit == "u4"), "C|B", s.label)
        try:
            out = os.path.join(self.tmp.name, "combinations")
            os.makedirs(out, exist_ok=True)
            r = qm.REGISTRY["E.combinations"].fn(s, self.design_for(s), out, min_cells=10, gene_sets="given",
                                                 gene_lists={"S": [f"s{i}" for i in range(10)]})
            # on a cell set of units u1-u3, the pair of u4 is not theirs and is not counted
            u13 = s.subset(cells=np.isin(s.unit, ["u1", "u2", "u3"]))
            given = qm.REGISTRY["E.combinations"].fn(u13, self.design_for(u13), out, min_cells=10, gene_sets="given",
                                                     gene_lists={"S": [f"s{i}" for i in range(10)]})
        finally:
            s.label = label
        self.assertEqual((r.diagnostics["pairs"], r.diagnostics["testable"]), (2, 1))
        self.assertEqual((given.diagnostics["pairs"], given.diagnostics["testable"]), (1, 1))
        self.assertGreater(r.table.set_index("pair").loc["A|C", "additive_correlation_sq"], 0.5)
        kept = r.tables["profiles"]  # each testable pair's observed and additive profile over all genes, drawn
        self.assertEqual(set(kept["pair"]), {"A|C"})
        self.assertEqual(list(kept.columns), ["pair", "gene", "observed", "additive", "fitted"])
        row = r.table.set_index("pair").loc["A|C"]
        # both singles here are the shared response (A's single cells carry A_2, which spares gene A; C changes
        # nothing else): the pair holds one dose of it, so the fitted weights sum to about 1 and the pair's slope on
        # the plain sum is below 1
        self.assertGreater(row["weight_A"] + row["weight_C"], 0.7)
        self.assertLess(row["weight_A"] + row["weight_C"], 1.3)
        self.assertLess(row["slope"], 0.8)
        self.assertEqual(r.diagnostics["model"], "fitted_weights")
        # each member on its own gene in the pair's cells: A's gene falls (A_1); C's gene is not in the matrix
        self.assertLess(row["own_change_A"], -1.0)
        self.assertFalse(bool(row["members_act"]))

    def design_for(self, s):
        return self.design

    @unittest.skipUnless(os.path.exists(__import__("pertura.envs", fromlist=["x"]).cnmf_python()), "needs cNMF")
    def test_cnmf_programs_find_the_planted_program(self):
        """C.programs with cNMF: a program carries B's raised genes b0-b4, and B's usage of it is above the
        control cells'."""
        out = os.path.join(self.tmp.name, "cnmf")
        r = qm.REGISTRY["C.programs"].run(self.screen, self.design, out, gene_sets="cnmf", components=[3, 4],
                                            n_iter=5)
        top = r.diagnostics["top_genes"]
        prog = max(top, key=lambda p: len({f"b{i}" for i in range(5)} & set(top[p][:10])))
        self.assertGreaterEqual(len({f"b{i}" for i in range(5)} & set(top[prog][:10])), 3)
        row = r.table[(r.table["target"] == "B") & (r.table["score"] == prog)]
        self.assertGreater(row["effect"].iat[0], 0)
        self.assertIn(r.diagnostics["k"], (3, 4))

    def program_effects(self):
        """30 targets' effects on four programs: P2 falls as P1 rises; T00-T09 lower P1 and raise P2, T10-T19
        lower P1 and raise P3; P4 is noise."""
        rng = np.random.default_rng(3)
        rows = []
        for i in range(30):
            x = rng.normal(0, 1)
            e = {"P1": x, "P2": -x + rng.normal(0, 0.2), "P3": rng.normal(0, 0.2), "P4": rng.normal(0, 0.2)}
            if i < 20:
                e["P1"] = -1.5 + rng.normal(0, 0.1)
                e["P2"], e["P3"] = (1.0 + rng.normal(0, 0.1), rng.normal(0, 0.1)) if i < 10 else \
                    (rng.normal(0, 0.1), 1.0 + rng.normal(0, 0.1))
            rows += [{"target": f"T{i:02d}", "score": k, "effect": v} for k, v in e.items()]
        return pd.DataFrame(rows)

    def test_program_axis_finds_the_planted_axis(self):
        r = qm.REGISTRY["E.program_axis"].run(self.screen, self.design, os.path.join(self.tmp.name, "axis"),
                                                effects=self.program_effects())
        t = r.table.set_index(["program_a", "program_b"])
        self.assertLess(t.loc[("P1", "P2"), "r"], -0.5)
        self.assertLess(t.loc[("P1", "P2"), "q"], 0.05)
        self.assertGreater(t.loc[("P3", "P4"), "q"], 0.05)

    def test_co_functional_sets_split_regulators_by_what_else_they_move(self):
        r = qm.REGISTRY["E.co_functional_sets"].run(self.screen, self.design, os.path.join(self.tmp.name, "sets"),
                                                    effects=self.program_effects(), program="P1", top=20)
        members = r.tables["members"].groupby("set")["target"].apply(set)
        first, second = {f"T{i:02d}" for i in range(10)}, {f"T{i:02d}" for i in range(10, 20)}
        self.assertTrue(any(s == first for s in members) and any(s == second for s in members), members)

    @unittest.skipUnless(importlib.util.find_spec("decoupler"), "needs decoupler")
    def test_direct_targets_find_the_planted_regulon(self):
        """G.direct_targets: target A (CRISPRi) lowers its planted regulon g0-g19, so A's activity is the lowest of
        the factors and its targets are enriched among its changed genes; B's regulon (g20-g39) does not move."""
        rng = np.random.default_rng(0)
        genes = [f"g{i}" for i in range(60)]
        rows = []
        for t in ("A", "B"):
            for i, g in enumerate(genes):
                down = t == "A" and i < 20
                rows.append({"target": t, "gene": g, "effect": -1.0 + rng.normal(0, 0.1) if down else
                             rng.normal(0, 0.1), "q": 0.001 if down else 1.0})
        net = pd.DataFrame({"source": ["A"] * 20 + ["B"] * 20 + ["X"] * 20,
                            "target": genes[:20] + genes[20:40] + genes[40:60], "weight": 1.0})
        out = os.path.join(self.tmp.name, "direct")
        r = qm.REGISTRY["G.direct_targets"].run(self.screen, self.design, out, de=pd.DataFrame(rows), net=net)
        t = r.table.set_index("target")
        self.assertLess(t.loc["A", "activity"], 0)
        self.assertEqual(t.loc["A", "activity_rank"], 1)
        self.assertLess(t.loc["A", "p_fisher"], 0.01)
        self.assertEqual(t.loc["A", "moving_as_predicted"], 20)
        self.assertGreater(t.loc["B", "p_fisher"], 0.5)

    def test_similar_targets(self):
        s = self.screen
        out = os.path.join(self.tmp.name, "similar")
        r = qm.REGISTRY["E.similar_targets"].run(s, self.design, out, min_cells=10)
        self.assertIn("B", r.diagnostics["most_similar"])

    def test_enrichment_without_genes_says_the_genes_are_missing_not_the_organism(self):
        # development run on GSE203592: run with no genes, the message named the organism first, which the design had
        r = qm.REGISTRY["G.meaning"].run(self.screen, self.design, os.path.join(self.tmp.name, "g0"))
        self.assertEqual(r.status, "not_enough_data")
        self.assertIn("no genes given", r.summary)
        self.assertNotIn("organism", r.summary)

    @unittest.skipUnless(importlib.util.find_spec("gseapy") is not None, "needs GSEApy")
    def test_enrichment_against_the_detected_universe(self):
        """GSEApy's over-representation test (after the review of 75 screens): the hypergeometric test against the
        detected genes, BH over the sets the list touches; up and down genes apart."""
        from pertura import gene_sets
        s = self.screen
        original = gene_sets.msigdb
        gene_sets.msigdb = lambda c, o: {"PLANTED": [f"b{i}" for i in range(5)] + ["g1"],
                                         "OTHER": [f"g{i}" for i in range(20, 30)]}
        try:
            r = qm.REGISTRY["G.meaning"].run(s, self.design,
                                             os.path.join(self.tmp.name, "g"),
                                             genes=[f"b{i}" for i in range(5)])
            both = qm.REGISTRY["G.meaning"].run(s, self.design, os.path.join(self.tmp.name, "g2"),
                                                up=[f"b{i}" for i in range(5)], down=[f"g{i}" for i in range(20, 25)])
        finally:
            gene_sets.msigdb = original
        t = r.table.set_index("set")
        self.assertEqual(t.index[0], "PLANTED")
        n = len(s.genes)  # every synthetic gene is detected
        p = stats.hypergeom.sf(4, n, 6, 5)  # 5 of the list's 5 genes in the set of 6
        self.assertAlmostEqual(t.loc["PLANTED", "p"], p, places=10)
        self.assertNotIn("OTHER", t.index)  # the list touches no gene of it
        self.assertEqual(r.diagnostics["package_calls"][0]["function"], "enrich")
        self.assertEqual(r.diagnostics["tag"], "prior knowledge")
        by = both.table.set_index(["direction", "set"])
        self.assertLess(by.loc[("up", "PLANTED"), "q"], 0.05)
        self.assertLess(by.loc[("down", "OTHER"), "q"], 0.05)
        self.assertNotIn(("up", "OTHER"), by.index)
        self.assertEqual(both.figure_checks.get("layout_issues"), [])


class TestRankTest(unittest.TestCase):
    def test_it_matches_scipys_mann_whitney_with_ties_at_zero(self):
        from pertura.tools.de.wilcoxon import SortedReference
        rng = np.random.default_rng(1)
        ref = rng.poisson(0.7, (80, 6)) * rng.random((80, 6))
        grp = rng.poisson(1.2, (50, 6)) * rng.random((50, 6))
        z, p = mc.rank_test(SortedReference(ref), sp.csr_matrix(grp))
        want = stats.mannwhitneyu(grp, ref, axis=0).pvalue
        np.testing.assert_allclose(p, want, rtol=1e-6)


class TestDataHelpers(unittest.TestCase):
    def test_units_merge_maps_channels_of_one_animal(self):
        with tempfile.TemporaryDirectory() as d:
            s, design = synthetic_screen(d)
            design = {**design, "units": {**design["units"], "merge": {"value": {"u1": "m1", "u2": "m1"}}}}
            s2 = Screen(d, design, adata=s.adata)
            self.assertEqual(s2.units(), ["m1", "u3", "u4"])
            self.assertEqual(sorted(set(s2.capture)), ["u1", "u2", "u3", "u4"])

    def test_the_expected_sign_follows_the_guides_mode(self):
        design = {"perturbation": {"modality": {"value": ["CRISPRa", "CRISPRi"]},
                                   "guide_modes": {"value": {"X-a1": "CRISPRa", "X-i1": "CRISPRi"}}}}
        self.assertEqual((mb.expected_sign(design, "X-a1"), mb.expected_sign(design, "X-i1"),
                          mb.expected_sign(design, "Y")), (1, -1, 0))


if __name__ == "__main__":
    unittest.main()


class TestFigureChecks(unittest.TestCase):
    def _fig(self, width_mm, text_pt=7, overlap=False):
        from pertura.question_modules import figures as fg
        plt = fg._plt()
        fig, ax = plt.subplots(figsize=(width_mm * fg.MM, 50 * fg.MM))
        ax.plot([0, 1], [0, 1])
        ax.set_xlabel("Share of cells, TF − scramble", fontsize=text_pt)
        ax.text(0.2, 0.5, "a label", fontsize=7)
        if overlap:
            ax.text(0.2, 0.5, "another label", fontsize=7)
        fig.tight_layout(pad=0.4)  # without it the axis label is cut at the figure's bottom edge
        return plt, fig

    def test_planted_faults_are_found_and_clean_figures_pass(self):
        from pertura.question_modules import figures as fg
        planted = [self._fig(183, overlap=True), self._fig(183, text_pt=4), self._fig(120)]
        clean = [self._fig(183), self._fig(89), self._fig(183, text_pt=6)]
        found = [fg.check_figure(fig) for _, fig in planted]
        passed = [fg.check_figure(fig) for _, fig in clean]
        for plt, fig in planted + clean:
            plt.close(fig)
        self.assertTrue(all(found), found)
        self.assertEqual(passed, [[], [], []])
        self.assertIn("overlaps", found[0][0])
        self.assertIn("below 5 pt", found[1][0])
        self.assertIn("width", found[2][0])

    def test_the_published_screens_charts_draw_and_pass_the_checks(self):
        import pandas as pd
        from pertura.question_modules import figures as fg
        rng = np.random.default_rng(0)
        states = ["S1", "S2", "S3"]
        colours = dict(zip(states, fg.OKABE_ITO[:3]))
        shares = pd.DataFrame([[0.3, 0.4, 0.3], [0.1, 0.5, 0.4], [0.2, 0.2, 0.6]], index=["control", "A", "B"],
                              columns=states)
        xy = rng.normal(size=(300, 2))
        markers = pd.DataFrame([{"gene": g, "state": s, "share expressing": 0.5, "mean": 1.0}
                                for g in ("g1", "g2") for s in states])
        reps = pd.DataFrame({"group": ["A"] * 3 + ["B"] * 3, "unit": ["m1", "m2", "m3"] * 2,
                             "value": [-0.1, -0.2, -0.15, 0.05, 0.0, 0.02]})
        plt = fg._plt()
        fig = plt.figure(figsize=(183 * fg.MM, 70 * fg.MM))
        gs = fig.add_gridspec(1, 5, wspace=0.6)
        checks = fg.composition_bars(fig.add_subplot(gs[0]), shares, states, colours,
                                     spread=shares * 0.1)
        fg.density_maps([fig.add_subplot(gs[1]), fig.add_subplot(gs[2])], xy,
                        {"control": rng.random(300) < 0.3, "A": rng.random(300) < 0.2})
        fg.marker_dotplot(fig.add_subplot(gs[3]), markers, ["g1", "g2"], states)
        ax = fig.add_subplot(gs[4])
        fg.effects_with_replicates(ax, reps, ["A", "B"])
        ax.set_yticklabels(["A", "B"])
        with tempfile.TemporaryDirectory() as d:
            issues = fg.save_figure_files(fig, os.path.join(d, "f"))
            self.assertTrue(os.path.exists(os.path.join(d, "f.pdf")) and os.path.exists(os.path.join(d, "f.svg")))
        plt.close(fig)
        self.assertTrue(checks["control_shown"])
        self.assertEqual(issues, [])
