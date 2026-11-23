"""Modules as recipes of blocks (docs/adr/0031, D2 and D3): C.genes runs its blocks inside the module (pt.genes, then
pt.calibrate, then each target's own gene), gives what the blocks give, shows its recipe in list_modules and returns
the recipe as run, ready to paste into a notebook cell."""
import importlib.util
import os
import shutil
import tempfile
import unittest

import numpy as np
import pandas as pd

from pertura import blocks as pt
from pertura.question_modules import REGISTRY, c

HAS_SCANPY = importlib.util.find_spec("scanpy") is not None


class TestTheRecipeIsShown(unittest.TestCase):
    def test_list_modules_shows_the_recipe_beside_the_contract(self):
        d = REGISTRY["C.genes"].describe()
        self.assertIn("def genes_recipe", d["recipe"])
        self.assertIn("pt.genes(", d["recipe"])
        self.assertIn("pt.calibrate(res)", d["recipe"])
        self.assertNotIn("recipe", REGISTRY["A.what_was_done"].describe())  # not a recipe of blocks

    def test_the_recipe_as_run_binds_the_arguments(self):
        text = pt.recipe_as_run(c.genes_recipe, method="wilcoxon", reference="control_guides", params=None)
        self.assertIn("res = pt.genes(method='wilcoxon', reference='control_guides')", text)  # None: the default
        changed = pt.recipe_as_run(c.genes_recipe, method="wilcoxon", reference="control_guides",
                                   params={"logfc_threshold": 0.25})
        self.assertIn("params={'logfc_threshold': 0.25}", changed)
        self.assertIn("cal = pt.calibrate(res)", text)
        self.assertIn("own = res.own_genes()", text)
        self.assertNotIn("return", text)
        self.assertNotIn("import", text)
        self.assertNotIn("as a job", text)  # Wilcoxon runs in the notebook's own process
        compile(text, "recipe", "exec")
        job = pt.recipe_as_run(c.genes_recipe, method="auto", reference="control_guides")
        self.assertTrue(job.startswith("# pt.genes with method 'auto' runs as a job here"))

    def test_inside_a_module_the_blocks_run_on_its_screen_and_the_notebook_context_comes_back(self):
        pt.CONTEXT.update({"screen_dir": "the notebook's"})
        try:
            with tempfile.TemporaryDirectory() as d:
                with pt.inside("a screen", d):
                    self.assertEqual(pt.screen(), "a screen")
                    self.assertFalse(pt._is_job("genes", {"method": "edger_ql"}))
            self.assertEqual(pt.CONTEXT["screen_dir"], "the notebook's")
            self.assertNotIn("in_process", pt.CONTEXT)
        finally:
            pt.CONTEXT.pop("screen_dir", None)


@unittest.skipUnless(HAS_SCANPY, "needs scanpy")
class TestCGenesIsItsRecipe(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_the_module_gives_what_its_blocks_give(self):
        from test_common_response_and_field_measures import build_screen
        # one pool, so Wilcoxon on cells leads; 12 control guides, so 5 groups of 2 for the calibration
        s, design = build_screen(self.d, {"T1": (60, {"g1": 3.0}), "T2": (60, {})}, units=("u1",), controls=12)
        r = REGISTRY["C.genes"].run(s, design, os.path.join(self.d, "module"))
        with pt.inside(s, os.path.join(self.d, "blocks")):
            res = pt.genes(method="auto")
            cal = pt.calibrate(res)
            own = res.own_genes()
        cols = ["target", "gene", "effect", "p", "q"]
        pd.testing.assert_frame_equal(r.tables["changed_genes"][cols].reset_index(drop=True),
                                      res.table[res.table["q"] < c.GENES_Q][cols].reset_index(drop=True))
        summary = cal.extra["summary"]["by_method"]["wilcoxon"]
        self.assertEqual(r.diagnostics["calibration"]["discoveries"], sum(summary["group_discoveries"].values()))
        self.assertEqual(r.diagnostics["calibration"]["groups"], len(summary["group_discoveries"]))
        self.assertEqual(len(r.tables["positive_controls"]), len(own))
        self.assertIn("res = pt.genes(method='auto', reference='control_guides')", r.diagnostics["recipe_as_run"])
        calls = [x["block"] for x in pt.calls_logged(os.path.join(self.d, "module"))]
        self.assertEqual(calls[0], "genes")
        self.assertIn("genes", calls[1:])  # the calibration: the same block on the control-guide groups


@unittest.skipUnless(HAS_SCANPY, "needs scanpy")
class TestCAnyEffectIsItsRecipe(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_the_module_gives_what_its_blocks_give_and_reads_the_targets_against_the_groups(self):
        from test_common_response_and_field_measures import build_screen
        # 12 control guides: 5 groups of 2 for the calibration; T1 moves 30 genes, T2 none
        s, design = build_screen(self.d, {"T1": (60, {f"g{i}": 3.0 for i in range(30)}), "T2": (60, {})},
                                 units=("u1",), controls=12)
        r = REGISTRY["C.any_effect"].fn(s, design, os.path.join(self.d, "module"), permutations=200)
        with pt.inside(s, os.path.join(self.d, "blocks")):
            res = pt.any_effect(permutations=200)
            cal = pt.calibrate(res)
        cols = ["target", "cells", "reference_cells", "e_distance", "p", "q"]
        pd.testing.assert_frame_equal(r.table[cols].reset_index(drop=True), res.table[cols].reset_index(drop=True))
        summary = r.diagnostics["calibration"]
        self.assertEqual(summary, cal.extra["summary"])
        self.assertEqual(summary["groups"], 5)
        self.assertEqual(summary["targets_beyond"], ["T1"])
        self.assertIn("res = pt.any_effect(", r.diagnostics["recipe_as_run"])
        self.assertIn("cal = pt.calibrate(res)", r.diagnostics["recipe_as_run"])
        self.assertIn("groups of control guides", r.summary)

    def test_the_groups_are_measured_in_the_pca_of_the_targets_call(self):
        """The calibration keeps the targets' cells in the PCA (fitted on every cell, as the first call), testing
        the groups only: a group's distance is the one it gets as a target of the whole screen."""
        from test_common_response_and_field_measures import build_screen
        from pertura.question_modules import c as mc
        from pertura.steps import any_effect as step
        s, _ = build_screen(self.d, {"T1": (60, {f"g{i}": 3.0 for i in range(30)}), "T2": (60, {})},
                            units=("u1",), controls=12)
        with pt.inside(s, os.path.join(self.d, "blocks")):
            res = pt.any_effect(permutations=50)
            cal = pt.calibrate(res)
        group, guides = next(iter(mc.calibration_groups(s).items()))
        lab = np.where((s.label == "control") & np.isin(s.top_guide, guides), group, s.label)
        for g, gl in mc.calibration_groups(s).items():
            lab = np.where((s.label == "control") & np.isin(s.top_guide, gl), g, lab)
        direct, _, _ = step.test(s.with_labels(lab), permutations=50, only=[group],
                                 draw=res.extra["drawn"]["at_most"])
        self.assertAlmostEqual(float(cal.table.set_index("target").loc[group, "e_distance"]),
                               float(direct["e_distance"].iat[0]), places=9)

    def test_a_screen_with_too_few_control_guides_says_why_there_is_no_calibration(self):
        from test_common_response_and_field_measures import build_screen
        s, design = build_screen(self.d, {"T1": (60, {"g1": 3.0})}, units=("u1",), controls=2)
        r = REGISTRY["C.any_effect"].fn(s, design, os.path.join(self.d, "module"), permutations=50)
        self.assertIn("not_run", r.diagnostics["calibration"])
        self.assertEqual(len(r.table), 1)


@unittest.skipUnless(HAS_SCANPY, "needs scanpy")
class TestCStatesIsItsRecipe(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def screen(self):
        from test_common_response_and_field_measures import build_screen
        # T1's cells carry 30 genes at six times the others': a state of their own; T2 sits with the control cells
        return build_screen(self.d, {"T1": (80, {f"g{i}": 6.0 for i in range(30)}), "T2": (80, {})},
                            units=("u1", "u2"), controls=12, control_cells=40)

    def test_the_module_gives_what_its_blocks_give(self):
        s, design = self.screen()
        r = REGISTRY["C.states_and_composition"].run(s, design, os.path.join(self.d, "module"))
        with pt.inside(s, os.path.join(self.d, "blocks")):
            st = pt.states()
            comp = pt.composition(of=st, column="state")
        cols = ["target", "state", "share", "control_share", "mean_difference", "p", "q", "cells", "reference_cells"]
        pd.testing.assert_frame_equal(r.table[cols].reset_index(drop=True), comp.table[cols].reset_index(drop=True))
        each = st.extra["states"]
        cells = r.tables["cell_states"].set_index("barcode")
        self.assertTrue((cells["state"].astype(str) == each.reindex(cells.index)["state"].astype(str)).all())
        np.testing.assert_allclose(cells["umap_1"].to_numpy(), each.reindex(cells.index)["umap_1"].to_numpy())
        pd.testing.assert_frame_equal(r.tables["by_unit"].reset_index(drop=True),
                                      comp.extra["by_unit"].reset_index(drop=True))
        t1 = r.table[r.table["target"] == "T1"].sort_values("mean_difference").iloc[-1]
        self.assertGreater(t1["share"], 0.8)  # T1's own state
        self.assertLess(t1["q"], 0.05)
        self.assertIn("st = pt.states(", r.diagnostics["recipe_as_run"])
        self.assertIn("comp = pt.composition(of=st, column='state'", r.diagnostics["recipe_as_run"])
        self.assertIn("by_score", r.diagnostics["calibration"])
        cal = r.diagnostics["calibration"]
        self.assertLessEqual(cal["group_pairs_changed"], cal["group_pairs"])
        self.assertIn(f"{cal['group_pairs_changed']} of {cal['group_pairs']} group x state pairs changed", r.summary)

    def test_pt_states_table_is_pt_compositions(self):
        """pt.states keeps each target's shares, by the composition step's own function."""
        s, _ = self.screen()
        with pt.inside(s, os.path.join(self.d, "blocks")):
            st = pt.states()
            comp = pt.composition(of=st)
        pd.testing.assert_frame_equal(st.table.reset_index(drop=True), comp.table.reset_index(drop=True))
        self.assertEqual(comp.extra["column"], "state")

    def test_against_every_control_cell(self):
        s, _ = self.screen()
        with pt.inside(s, os.path.join(self.d, "blocks")):
            st = pt.states()
            comp = pt.composition(of=st, control_cells="all")
        self.assertTrue((comp.table["test"] == "Fisher's exact test on cells").all())
        self.assertTrue((comp.table["cells_left_out"] == 0).all())
        self.assertEqual(int(comp.table["reference_cells"].iat[0]), 12 * 40 * 2)  # every control cell of both samples


if __name__ == "__main__":
    unittest.main()
