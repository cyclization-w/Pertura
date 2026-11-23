"""E.condition_x_perturbation over units: where only some units hold both conditions, the paired test reads those,
and the figure's points, the text's means and the result table read the same units (GPT's review of 251c436: with
6 donors, 4 in each condition and 2 in both, the text read "rest +1.02, stim -0.478, change +1.01", the means over
all 4 units of each condition beside a change over the 2 pairs)."""
import os
import tempfile
import unittest
from unittest import mock

import numpy as np

from synthetic_screen import make_condition_screen

from pertura.question_modules import e as qe
from pertura.question_modules.data import Screen


def partly_paired(folder):
    """Six wells, w1-w3 at rest and w4-w6 stimulated; half of w3's cells moved to stim and half of w4's to rest, so
    w1-w4 hold rest, w3-w6 hold stim, and only w3 and w4 hold both."""
    import anndata as ad
    s, design, _ = make_condition_screen(folder, seed=1, wells=6)
    obs = s.adata.obs.copy()
    col = obs.columns.get_loc("condition")
    for unit, other in (("w3", "stim"), ("w4", "rest")):
        obs.iloc[np.flatnonzero(obs["unit"].to_numpy() == unit)[::2], col] = other
    return Screen(folder, design, adata=ad.AnnData(s.adata.X, obs=obs, var=s.adata.var.copy())), design


class TestAPartlyPairedLayout(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.screen, self.design = partly_paired(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def run_it(self):
        with mock.patch.object(qe, "condition_biology", wraps=qe.condition_biology) as told, \
                mock.patch.object(qe.figures, "condition_map", wraps=qe.figures.condition_map) as drawn:
            res = qe.condition_x_perturbation(self.screen, self.design, os.path.join(self.tmp.name, "out"),
                                              what="genes", genes=["s0", "s1"], test="units")
        return res, told.call_args.args[1], drawn.call_args.args[3]

    def test_the_test_the_figure_and_the_text_read_the_units_in_both_conditions(self):
        os.makedirs(os.path.join(self.tmp.name, "out"))
        res, text_rows, figure_rows = self.run_it()
        row = res.table[(res.table["target"] == "INT") & (res.table["score"] == "s0")].iloc[0]
        self.assertEqual(row["test"], "paired over units")
        self.assertEqual((row["units paired"], row["units in one condition only"], row["units rest"],
                          row["units stim"]), (2, 4, 2, 2))
        for rows in (text_rows, figure_rows):
            mine = rows[(rows["target"] == "INT") & (rows["score"] == "s0")]
            self.assertEqual(sorted(set(mine["unit"])), ["w3", "w4"])
        by = res.tables["by_unit"]  # the table by unit keeps every unit, as measured
        self.assertEqual(sorted(set(by[(by["target"] == "INT") & (by["score"] == "s0")]["unit"])),
                         ["w1", "w2", "w3", "w4", "w5", "w6"])
        mean = text_rows[(text_rows["target"] == "INT") & (text_rows["score"] == "s0")].groupby("condition")[
            "difference"].mean()
        self.assertAlmostEqual(mean["stim"] - mean["rest"], row["interaction"], places=9)


if __name__ == "__main__":
    unittest.main()
