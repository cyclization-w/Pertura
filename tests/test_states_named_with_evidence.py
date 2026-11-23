"""States named with evidence (docs/adr/0044, D1): each state's cells scored on a collection's sets by decoupler's
univariate linear model and each state's sets ranked against the other states' cells (rankby_group), as decoupler's
tutorial annotates clusters; the evidence sits beside the markers, and the agent names the state."""
import importlib.util
import os
import tempfile
import unittest

import numpy as np

from test_a_signature_from_the_controls_contrast import screen_with_a_stimulation
from test_question_modules import synthetic_screen

from pertura.question_modules import REGISTRY
from pertura.question_modules.c import state_naming_evidence

HAS_DECOUPLER = importlib.util.find_spec("decoupler") is not None
HAS_SCANPY = importlib.util.find_spec("scanpy") is not None
SETS = {"STIM_TYPE": [f"g{i}" for i in range(10)], "OTHER_TYPE": [f"g{i}" for i in range(20, 30)]}


class TestNoSetsNoEvidence(unittest.TestCase):
    def test_none_or_an_organism_without_collections_says_why(self):
        lab = np.array(["s1", "s2"])
        table, about = state_naming_evidence(None, lab, {}, "none")
        self.assertTrue(table.empty and about == {"not_run": "naming_sets none"})
        table, about = state_naming_evidence(None, lab, {"experiment": {"organism": {"value": "zebrafish"}}})
        self.assertIn("need experiment.organism", about["not_run"])


@unittest.skipUnless(HAS_DECOUPLER, "needs decoupler")
class TestNamingEvidence(unittest.TestCase):
    def test_the_stimulated_state_fits_the_stimulation_set(self):
        with tempfile.TemporaryDirectory() as d:
            s, design = screen_with_a_stimulation(d)
            lab = s.adata.obs["condition_time"].astype(str).to_numpy()
            table, about = state_naming_evidence(s, lab, design, SETS)
        first = table[table["state"] == "stim"].iloc[0]
        self.assertEqual(first["set"], "STIM_TYPE")
        self.assertGreater(first["stat"], 0)
        self.assertFalse((table["stat"] <= 0).any())
        self.assertEqual(about["sets"], "given lists")

    def test_sets_none_of_whose_genes_the_screen_holds_leave_the_states_standing(self):
        with tempfile.TemporaryDirectory() as d:
            s, design = screen_with_a_stimulation(d)
            lab = s.adata.obs["condition_time"].astype(str).to_numpy()
            table, about = state_naming_evidence(s, lab, design, {"ELSEWHERE": ["zz1", "zz2", "zz3"]})
        self.assertTrue(table.empty)
        self.assertIn("has 3 of its genes among the screen's genes", about["not_run"])

    @unittest.skipUnless(HAS_SCANPY, "needs scanpy")
    def test_the_states_module_puts_the_sets_beside_the_markers(self):
        with tempfile.TemporaryDirectory() as d:
            folder = os.path.join(d, "screen")
            os.makedirs(folder)
            s, design = synthetic_screen(folder)
            r = REGISTRY["C.states_and_composition"].run(s, design, os.path.join(d, "out"),
                                                          naming_sets={"B_LIKE": [f"b{i}" for i in range(5)],
                                                                       "QUIET": [f"g{i}" for i in range(5)]})
        evidence = r.tables["naming_evidence"]
        self.assertFalse(evidence.empty)
        self.assertIn("fits", r.summary)
        self.assertEqual(r.diagnostics["naming"]["sets"], "given lists")


if __name__ == "__main__":
    unittest.main()
