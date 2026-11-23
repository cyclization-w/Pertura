"""The response step (pertura.steps.response; docs/adr/0031, phase 2): B.escape's Mixscape path is pt.response's
call; Mixscale scores the target with genes of its own highest; a setting a method lacks is refused; the calibration
reads each target against groups of control guides put through the same call."""
import importlib.util
import os
import shutil
import tempfile
import unittest

import pandas as pd

HAS_PERTPY = importlib.util.find_spec("pertpy") is not None and importlib.util.find_spec("scanpy") is not None


class TestSettings(unittest.TestCase):
    def test_a_setting_the_method_lacks_is_refused(self):
        from pertura.steps import response
        with self.assertRaises(ValueError) as e:
            response.check_params("mixscape", {"max_de_genes": 50})
        self.assertIn("its settings are", str(e.exception))
        response.check_params("mixscale", {"max_de_genes": 50})
        with self.assertRaises(ValueError):
            response.check_params("augur", {})


@unittest.skipUnless(HAS_PERTPY, "needs pertpy and scanpy")
class TestResponse(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from test_question_modules import synthetic_screen
        cls.tmp = tempfile.TemporaryDirectory()
        cls.s, cls.design = synthetic_screen(cls.tmp.name)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_b_escapes_mixscape_is_the_blocks_call(self):
        from pertura import blocks as pt
        from pertura.question_modules import REGISTRY
        r = REGISTRY["B.escape"].run(self.s, self.design, os.path.join(self.tmp.name, "escape"))
        with pt.inside(self.s, os.path.join(self.tmp.name, "blocks")):
            res = pt.response()
        cols = ["target", "cells", "perturbed", "not_called", "share_perturbed"]
        pd.testing.assert_frame_equal(r.table[cols].reset_index(drop=True), res.table[cols].reset_index(drop=True))
        self.assertIn("pt.response(", r.diagnostics["recipe_as_run"])
        self.assertGreater(res.table.set_index("target").loc["B", "share_perturbed"], 0.5)

    def test_mixscale_scores_the_target_with_its_own_genes_highest(self):
        from pertura import blocks as pt
        with pt.inside(self.s, os.path.join(self.tmp.name, "mixscale")):
            res = pt.response(method="mixscale")
        t = res.table.set_index("target")
        self.assertEqual(t["median_score"].idxmax(), "B", t)
        self.assertTrue(bool(t.loc["B", "scored"]))
        self.assertIn("score", res.extra["cells"].columns)


@unittest.skipUnless(HAS_PERTPY, "needs pertpy and scanpy")
class TestCalibration(unittest.TestCase):
    def test_a_target_with_its_own_genes_goes_past_the_control_guide_groups(self):
        from pertura import blocks as pt
        from test_common_response_and_field_measures import build_screen
        d = tempfile.mkdtemp()
        try:
            s, _ = build_screen(d, {"T1": (60, {f"g{j}": 5.0 for j in range(8)}), "T2": (60, {})},
                                units=("u1", "u2"), controls=12, control_cells=20)
            with pt.inside(s, os.path.join(d, "blocks")):
                res = pt.response()
                cal = pt.calibrate(res)
            summary = cal.extra["summary"]
            self.assertGreaterEqual(summary["groups"], 2)
            self.assertEqual(summary["measure"], "share_perturbed")
            self.assertIn("T1", summary["targets_beyond"])
            self.assertNotIn("T2", summary["targets_beyond"])
        finally:
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
