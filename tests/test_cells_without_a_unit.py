"""Cells whose unit the build could not tell (no clear hashtag, no value in the unit column: '<sample>:unassigned') are
no replicate: the screen's units leave them out, and A.what_was_done counts them apart."""
import os
import tempfile
import unittest

import numpy as np

from pertura import question_modules as qm


class TestCellsWithoutAUnit(unittest.TestCase):
    def test_they_are_no_unit_and_are_counted_apart(self):
        from test_common_response_and_field_measures import build_screen
        with tempfile.TemporaryDirectory() as d:
            s, design = build_screen(d, {"T1": (20, {}), "T2": (20, {})}, units=("u1", "u2"))
            unit = np.asarray(s.unit).astype(object)
            unit[:7] = "u1:unassigned"
            s.unit = unit.astype(str)
            self.assertEqual(s.units(), ["u1", "u2"])
            self.assertEqual(int(s.unassigned().sum()), 7)
            r = qm.REGISTRY["A.what_was_done"].run(s, design, os.path.join(d, "a"))
        self.assertEqual((r.diagnostics["units"], r.diagnostics["cells_without_a_unit"]), (2, 7))
        self.assertIn("7 cells whose unit could not be told", r.summary)


if __name__ == "__main__":
    unittest.main()
