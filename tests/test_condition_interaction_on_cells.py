"""E.condition_x_perturbation's test on cells: each sample in each condition is its own baseline, so a condition's
shift that differs between samples is not read as a change of the target's effect; a layout that cannot tell the
change from the samples' baselines gives no number; a planted change is found."""
import unittest

import numpy as np

from pertura.question_modules.e import _interaction_on_cells


def cells(rng, layout, shift, change=0.0):
    """Cells from (sample, second condition, target, n) groups; shift[(sample, second)] is that sample's condition
    shift, change the planted change of the target's effect in the second condition."""
    sample, second, target, y = [], [], [], []
    for s, c, t, n in layout:
        sample += [s] * n
        second += [c] * n
        target += [t] * n
        y.append(rng.normal(shift.get((s, c), 0.0) + 0.3 * t + change * (t and c), 1.0, n))
    return (np.concatenate(y)[:, None], np.array(target, bool), np.array(second, bool), np.array(sample))


class TestTheInteractionOnCells(unittest.TestCase):
    def test_a_condition_shift_that_differs_between_samples_is_not_a_change(self):
        rng = np.random.default_rng(0)
        # two lanes, each holding both conditions; lane A's second condition is shifted, lane B's is not; the
        # target's cells of the second condition sit mostly in lane A, its control cells mostly in lane B
        layout = [("A", False, True, 50), ("A", False, False, 50), ("B", False, True, 50), ("B", False, False, 50),
                  ("A", True, True, 90), ("A", True, False, 10), ("B", True, True, 10), ("B", True, False, 90)]
        shift = {("A", True): 1.5}
        p = [float(_interaction_on_cells(*cells(rng, layout, shift))[1][0]) for _ in range(200)]
        self.assertLess(np.mean(np.array(p) < 0.05), 0.10)

    def test_a_layout_that_cannot_tell_the_change_from_the_samples_gives_no_number(self):
        rng = np.random.default_rng(1)
        # sample A (first condition) holds both; in the second condition, B holds only the target's cells and C only
        # control cells: the change cannot be told from B's baseline
        layout = [("A", False, True, 40), ("A", False, False, 40), ("B", True, True, 40), ("C", True, False, 40)]
        inter, p, _ = _interaction_on_cells(*cells(rng, layout, {("B", True): 2.0}))
        self.assertTrue(np.isnan(inter[0]) and np.isnan(p[0]))

    def test_a_planted_change_is_found_with_one_sample_per_condition(self):
        rng = np.random.default_rng(2)
        layout = [("A", False, True, 80), ("A", False, False, 80), ("B", True, True, 80), ("B", True, False, 80)]
        inter, p, _ = _interaction_on_cells(*cells(rng, layout, {("B", True): 0.7}, change=1.0))
        self.assertAlmostEqual(float(inter[0]), 1.0, delta=0.5)
        self.assertLess(float(p[0]), 1e-3)


if __name__ == "__main__":
    unittest.main()
