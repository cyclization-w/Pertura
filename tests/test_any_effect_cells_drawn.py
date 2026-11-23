"""C.any_effect's draw: each side of a target's test (its cells, its reference cells) drawn, seeded, to at most the
overview's cap (steps.any_effect.CAP, 1,000 cells), so that a screen of many cells is tested in minutes; a side with
fewer keeps every cell, and the calibration's groups are drawn the same way."""
import importlib.util
import inspect
import os
import tempfile
import unittest

import numpy as np

from pertura.steps import any_effect as step

HAS = importlib.util.find_spec("pertpy") is not None and importlib.util.find_spec("scanpy") is not None


class TestTheDraw(unittest.TestCase):
    def test_a_draw_keeps_the_mask_where_it_holds_no_more(self):
        rng = np.random.default_rng(0)
        m = np.zeros(100, bool)
        m[:30] = True
        cut = step._draw(m, 10, rng)
        self.assertEqual(int(cut.sum()), 10)
        self.assertTrue(set(np.flatnonzero(cut)) <= set(range(30)))
        self.assertIs(step._draw(m, 40, rng), m)

    def test_the_default_is_the_overviews_cap(self):
        from pertura import blocks
        self.assertEqual(step.CAP, 1000)
        self.assertEqual(inspect.signature(blocks.any_effect).parameters["draw"].default, step.CAP)
        self.assertEqual(inspect.signature(blocks._any_effect).parameters["draw"].default, step.CAP)

    @unittest.skipUnless(HAS, "needs pertpy and scanpy")
    def test_each_side_is_drawn_to_at_most_the_cap_and_the_groups_too(self):
        from test_question_modules import synthetic_screen
        from pertura import blocks as pt
        with tempfile.TemporaryDirectory() as d:
            s, _ = synthetic_screen(d)
            with pt.inside(s, os.path.join(d, "blocks")):
                res = pt.any_effect(permutations=50, draw=20)
                cal = pt.calibrate(res)
                every = pt.any_effect(permutations=50, draw=None)
            t = res.table
            self.assertEqual(res.extra["drawn"], {"at_most": 20})
            self.assertTrue((t["cells_drawn"] == np.minimum(t["cells"], 20)).all(), t)
            self.assertTrue((t["reference_cells_drawn"] == np.minimum(t["reference_cells"], 20)).all(), t)
            self.assertLessEqual(int(cal.table["cells_drawn"].max()), 20)
            self.assertLessEqual(int(cal.table["reference_cells_drawn"].max()), 20)
            e = every.table
            self.assertTrue((e["cells_drawn"] == e["cells"]).all() and (e["reference_cells_drawn"] ==
                                                                        e["reference_cells"]).all())

    @unittest.skipUnless(HAS, "needs pertpy and scanpy")
    def test_the_module_says_its_draw(self):
        from test_question_modules import synthetic_screen
        from pertura import question_modules as qm
        with tempfile.TemporaryDirectory() as d:
            s, design = synthetic_screen(d)
            r = qm.REGISTRY["C.any_effect"].fn(s, design, os.path.join(d, "out"), permutations=50)
            self.assertEqual(r.diagnostics["drawn"], {"at_most": step.CAP})
            self.assertIn(f"at most {step.CAP} cells", r.summary)


@unittest.skipUnless(HAS, "needs pertpy and scanpy")
class TestFewerCells(unittest.TestCase):
    """A down-sampling, as published screens draw a perturbation's cells to fewer to see how many its call needs."""

    def test_the_targets_cells_are_drawn_and_its_reference_kept(self):
        from test_question_modules import synthetic_screen
        with tempfile.TemporaryDirectory() as d:
            s, _ = synthetic_screen(d)
            t, _, drawn = step.test(s, permutations=20, draw=10, draw_reference=step.CAP)
        self.assertTrue((t["cells_drawn"] == 10).all(), t)
        self.assertTrue((t["reference_cells_drawn"] == t["reference_cells"]).all(), t)
        self.assertEqual(drawn, {"at_most": 10})

    def test_the_module_tests_each_number_of_cells_in_three_draws(self):
        from test_question_modules import synthetic_screen
        from pertura import question_modules as qm
        with tempfile.TemporaryDirectory() as d:
            s, design = synthetic_screen(d)
            r = qm.REGISTRY["C.any_effect"].fn(s, design, os.path.join(d, "out"), permutations=50,
                                               fewer_cells=[40, 100])
        by_draw = r.tables["fewer_cells_by_draw"].set_index(["cells_per_target", "draw"])
        self.assertEqual(sorted(by_draw.index), [(n, k) for n in (40, 100) for k in (1, 2, 3)])
        self.assertEqual(int(by_draw.loc[(40, 1), "targets_tested"]), 3)  # A 320 cells, B 60, C 120
        self.assertEqual(int(by_draw.loc[(100, 1), "targets_tested"]), 2)  # B holds fewer than 100
        self.assertEqual(float(by_draw.loc[(40, 1), "share_called_again"]), 1.0)  # the shared shift is strong
        self.assertIn("with fewer cells (3 draws of each number", r.summary)
        self.assertEqual(r.figure_checks.get("layout_issues"), [])


if __name__ == "__main__":
    unittest.main()
