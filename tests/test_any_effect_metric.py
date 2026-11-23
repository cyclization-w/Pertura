"""The any-effect step's metric (docs/adr/0031, phase 2, batch C): another of pertpy's distances ranks the target with
an effect of its own first, as the energy distance does, and the table names the metric; an unknown one is refused."""
import importlib.util
import os
import tempfile
import unittest

HAS = importlib.util.find_spec("pertpy") is not None and importlib.util.find_spec("scanpy") is not None


class TestTheMetric(unittest.TestCase):
    def test_an_unknown_metric_is_refused(self):
        from pertura.steps import any_effect
        with self.assertRaises(ValueError):
            any_effect.test(None, metric="wasserstein")  # needs ott-jax, not offered

    @unittest.skipUnless(HAS, "needs pertpy and scanpy")
    def test_mmd_ranks_the_target_with_its_own_genes_first(self):
        from pertura.steps import any_effect
        from test_question_modules import synthetic_screen
        with tempfile.TemporaryDirectory() as d:
            s, _ = synthetic_screen(d)
            e, _, _ = any_effect.test(s, permutations=200)
            m, calls, _ = any_effect.test(s, permutations=200, metric="mmd")
        self.assertEqual(e.iloc[0]["target"], "B")
        self.assertEqual(m.iloc[0]["target"], "B")
        self.assertEqual(set(m["metric"]), {"mmd"})
        self.assertTrue(m["e_distance"].isna().all())  # the energy distance only where it is the metric
        self.assertEqual(calls[1]["params"]["metric"], "mmd")


if __name__ == "__main__":
    unittest.main()
