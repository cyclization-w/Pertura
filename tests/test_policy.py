"""Pertura's numbers in one place (pertura.policy): a default is not a permission. A target or a method the run names
runs wherever it can be computed, with a note; a run changes the numbers with `policy`; every result records them."""
import os
import tempfile
import unittest

from pertura import blocks, policy
from pertura import question_modules as qm


def screen(folder):
    from test_common_response_and_field_measures import build_screen
    return build_screen(folder, {"T1": (20, {}), "T2": (6, {}), "T3": (2, {})}, units=("u1",))


class TestTheNumbers(unittest.TestCase):
    def test_a_change_is_checked(self):
        self.assertEqual(policy.validate({"q": 0.1, "min_cells": 5, "min_effect": None}), [])
        bad = policy.validate({"cells": 5, "min_cells": 1, "q": 2, "min_replicates": "three"})
        self.assertEqual(len(bad), 4)
        self.assertTrue(any("cannot be computed; the least is 3" in p for p in bad))
        with self.assertRaises(ValueError):
            with policy.using({"min_replicates": 1}):
                pass

    def test_changes_stack_and_return_and_notes_reach_the_outer_run(self):
        self.assertEqual(policy.get("q"), policy.DEFAULTS["q"])
        with policy.using({"q": 0.1}, explicit_choices={"method"}):
            self.assertTrue(policy.explicit("method"))
            self.assertEqual(policy.floor("min_replicates", "method"), 2)
            self.assertEqual(policy.floor("min_replicates", "test"), 3)
            with policy.using({"min_cells": 5}):
                self.assertEqual((policy.get("q"), policy.get("min_cells")), (0.1, 5))
                policy.note("an inner note")
            self.assertEqual(policy.get("min_cells"), 10)
            self.assertEqual(policy.notes(), ["an inner note"])
            self.assertEqual(policy.used()["changed"], {"q": 0.1})
        self.assertEqual(policy.get("q"), policy.DEFAULTS["q"])
        self.assertFalse(policy.explicit("method"))

    def test_the_notebook_sets_them_for_every_later_call(self):
        try:
            self.assertEqual(blocks.policy(min_cells=5)["min_cells"], 5)
            self.assertEqual(policy.get("min_cells"), 5)
            self.assertEqual(blocks.policy()["min_cells"], 5)
        finally:
            blocks.policy(reset=True)
        self.assertEqual(policy.get("min_cells"), 10)


class TestADefaultIsNotAPermission(unittest.TestCase):
    def test_a_named_target_below_the_default_runs_with_a_note(self):
        with tempfile.TemporaryDirectory() as d:
            s, _ = screen(d)
            self.assertEqual(blocks._targets(s, None), ["T1"])  # Pertura's choice: 10 cells
            with policy.using():
                self.assertEqual(blocks._targets(s, ["T2"]), ["T2"])  # named: 6 cells can be computed
                self.assertIn("T2 has fewer than 10 cells", policy.notes()[0])
            with self.assertRaises(ValueError):  # 2 cells cannot
                blocks._targets(s, ["T3"])
            with policy.using({"min_cells": 5}):
                self.assertEqual(blocks._targets(s, None), ["T1", "T2"])

    def test_a_module_records_the_numbers_it_used(self):
        with tempfile.TemporaryDirectory() as d:
            s, design = screen(d)
            r = qm.REGISTRY["A.what_was_done"].run(s, design, os.path.join(d, "a"), policy={"q": 0.1})
            with self.assertRaises(ValueError):
                qm.REGISTRY["A.what_was_done"].run(s, design, os.path.join(d, "b"), policy={"cells": 3})
        self.assertEqual(r.diagnostics["policy"]["changed"], {"q": 0.1})
        self.assertEqual(r.diagnostics["policy"]["numbers"]["min_cells"], 10)


if __name__ == "__main__":
    unittest.main()
