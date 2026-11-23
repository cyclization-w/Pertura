"""The similarity comparison names the screen's sets of targets alike, each with the targets opposite it, first in its
lines, and boxes them in its figure. On GSE342764 its lines gave the four most correlated pairs, and the set of Ep300,
Ezh1, Hells, Kdm6b and Zeb2, with Hdac4, Phf20l1, Tox2, Zmynd8 and Ezh2 opposite it, was read by no one."""
import os
import tempfile
import unittest

import numpy as np
import pandas as pd

from pertura import candidates
from pertura.question_modules import charts
from pertura.question_modules.e import similar_biology


def pairs_of(groups, within=0.7, between=-0.4, background=0.05):
    """Every two targets' correlation: high within each group, the first two groups opposite each other, the rest
    near zero; control_pairs_as_high passes for pairs above 0.1."""
    names = [t for g in groups for t in g] + ["L1", "L2", "L3"]
    group = {t: i for i, g in enumerate(groups) for t in g}
    rows = []
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            ga, gb = group.get(a), group.get(b)
            r = within if ga is not None and ga == gb else between if {ga, gb} == {0, 1} else background
            rows.append({"target": a, "other": b, "correlation": r, "control_pairs_as_high": 0.0 if r > 0.1 else 0.5,
                         "control_pairs_as_low": 0.0 if r < -0.1 else 0.5})
    return pd.DataFrame(rows)


class TestTheSetsApart(unittest.TestCase):
    def setUp(self):
        self.pairs = pairs_of([["A1", "A2", "A3"], ["B1", "B2"]])
        self.sets = candidates.sets_alike(self.pairs, reference=0.1, low=-0.1)["sets"]

    def test_a_core_and_the_wider_set_it_joins_show_once(self):
        nested = pairs_of([["A1", "A2", "A3"]], within=0.7)
        nested.loc[(nested["target"] == "A1") & (nested["other"] == "A2"), "correlation"] = 0.9
        listed = candidates.sets_alike(nested, reference=0.1, low=-0.1)["sets"]
        self.assertIn(["A1", "A2"], [s["targets"] for s in listed])
        self.assertEqual([s["targets"] for s in candidates.apart(listed)], [["A1", "A2", "A3"]])

    def test_each_set_names_the_targets_opposite_it(self):
        sets = {tuple(s["targets"]): [o["target"] for o in s["opposite"]] for s in candidates.apart(self.sets)}
        self.assertEqual(sets[("A1", "A2", "A3")], ["B1", "B2"])
        self.assertEqual(sets[("B1", "B2")], ["A1", "A2", "A3"])

    def test_the_lines_name_the_sets_first(self):
        lines = similar_biology(self.pairs, sets=candidates.apart(self.sets))
        self.assertTrue(lines[0].startswith("Alike: A1, A2, A3 (mean r 0.70"), lines[0])
        self.assertIn("opposite them: B1 -0.40, B2 -0.40", lines[0])
        self.assertTrue(any(x.startswith("A1 and A2: r 0.70") for x in lines[2:]))
        self.assertLessEqual(len(lines), 25)

    def test_without_sets_the_lines_are_the_pairs(self):
        self.assertTrue(similar_biology(self.pairs)[0].startswith("A1 and A2: r 0.70"))

    def test_a_set_is_a_run_of_neighbours_in_the_trees_order(self):
        order = candidates.leaf_order(self.pairs)
        for s in candidates.apart(self.sets):
            at = sorted(order.index(t) for t in s["targets"])
            self.assertEqual(at[-1] - at[0] + 1, len(at), (s["targets"], order))


class TestTheFigureBoxesTheSets(unittest.TestCase):
    def test_each_set_of_neighbours_is_boxed(self):
        from matplotlib.patches import Rectangle
        pairs = pairs_of([["A1", "A2", "A3"], ["B1", "B2"]])
        names, r = candidates._matrix(pairs)
        np.fill_diagonal(r, 1.0)
        C = pd.DataFrame(r, index=names, columns=names)
        side = pd.DataFrame({"Cells": [50] * len(names)}, index=names)
        order = candidates.leaf_order(pairs)
        boxes = [["A1", "A2", "A3"], ["B1", "B2"], ["A1", "L1"]]
        plt = charts.figures._plt()
        fig = plt.figure()
        charts.target_panel(C, side, fig=fig, order=order, boxes=boxes)
        ax = fig.axes[0]
        drawn = [p for p in ax.patches if isinstance(p, Rectangle)]
        self.assertEqual(sorted(round(b.get_width()) for b in drawn), [2, 3])  # A1 and L1 are not neighbours
        self.assertEqual([t.get_text() for t in ax.get_yticklabels()], order)
        plt.close(fig)
        with tempfile.TemporaryDirectory() as tmp:
            charts.target_panel(C, side, os.path.join(tmp, "f.png"), order=order, boxes=boxes)
            self.assertTrue(os.path.exists(os.path.join(tmp, "f.png")))


if __name__ == "__main__":
    unittest.main()
