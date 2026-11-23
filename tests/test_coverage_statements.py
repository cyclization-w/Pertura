"""Tests for what a screen without hidden checks is told (pertura.coverage), after the held-out evaluation (atlas
protocol amendment 21, 2026-10-08)."""
import unittest

from pertura import coverage


class TestStatements(unittest.TestCase):
    def test_in_vitro_crispri_gets_the_recipe_with_its_held_out_record(self):
        s = coverage.statement("cell_line", "crispri", 3)
        self.assertEqual(s["mode"], "recipe")
        self.assertIn("1% or less", s["statement"])
        self.assertIn("13.5%", s["statement"])  # the held-out screen where it was far too low
        self.assertIn("measured on other screens, not this one", s["statement"])

    def test_in_vitro_knockout_gets_the_recipe_since_claim_1b(self):
        s = coverage.statement("primary_in_vitro", "crispr_ko", 2)
        self.assertEqual(s["mode"], "recipe")
        self.assertIn("3 in vitro knockout screens from 1 study", s["statement"])
        self.assertIn("5 of the 6 held-out in vitro screens", s["held_out"])

    def test_in_vivo_knockout_and_crispra_stay_constrained(self):
        self.assertEqual(coverage.statement("in_vivo", "crispr_ko", 4)["mode"], "constrained")
        self.assertEqual(coverage.statement("cell_line", "crispra", 4)["mode"], "constrained")
        self.assertIsNone(coverage.statement("in_vivo", "crispri", 4)["information"])  # no development screen

    def test_single_guide_information_carries_the_held_out_rates(self):
        ko = coverage.single_guide_statement("cell_line", "crispr_ko", 3)
        self.assertEqual(ko["mode"], "constrained")
        self.assertIn("held-out", ko["information"])
        self.assertIn("1% or less", ko["information"])
        self.assertIn("3.7%", coverage.single_guide_statement("cell_line", "crispri", 3)["information"])


if __name__ == "__main__":
    unittest.main()
