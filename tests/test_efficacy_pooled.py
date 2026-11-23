"""B.efficacy over all units pooled: a guide spread over many small units (lanes or hashtags of one pool) is measured
on its pooled cells, with each unit's row beside it, rather than answered with 'not enough data'."""
import os
import tempfile
import unittest

from pertura import question_modules as qm
from pertura.question_modules.b import ALL_UNITS


class TestKnockdownPooledOverUnits(unittest.TestCase):
    def test_a_guide_thin_in_every_unit_is_measured_over_them(self):
        from test_common_response_and_field_measures import build_screen
        units = tuple(f"u{k}" for k in range(1, 7))
        guides = {"T1": (["T1_1"], "T1")}
        with tempfile.TemporaryDirectory() as d:
            # 4 cells of T1 in each of 6 units: below 10 in every unit, 24 pooled; its own gene T1 at a fifth
            s, design = build_screen(d, {"T1": (4, {"T1": 0.2})}, units=units, guides=guides, control_cells=10)
            r = qm.REGISTRY["B.efficacy"].run(s, design, os.path.join(d, "b"))
        self.assertNotEqual(r.status, "not_enough_data", r.summary)
        rows = r.table[r.table["guide"] == "T1_1"]
        self.assertEqual(list(rows["unit"]), [ALL_UNITS])  # no unit alone has 10 cells
        self.assertEqual(int(rows["cells"].iat[0]), 24)
        self.assertEqual(rows["guide_verdict"].iat[0].split(" ")[0], rows["unit_verdict"].iat[0].split(" ")[0])


if __name__ == "__main__":
    unittest.main()
