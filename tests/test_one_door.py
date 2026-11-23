"""One door's rules: a module offers what its block offers. C.genes' methods are the genes block's; G.meaning takes
one's own gene sets and a universe, as pt.enrich does."""
import importlib.util
import os
import tempfile
import unittest

from pertura import question_modules as qm
from pertura.steps.genes import BACKENDS

HAS_GSEAPY = importlib.util.find_spec("gseapy") is not None
HAS_SCANPY = importlib.util.find_spec("scanpy") is not None


class TestOneDoor(unittest.TestCase):
    def test_c_genes_offers_every_method_of_the_genes_block(self):
        methods = qm.REGISTRY["C.genes"].contract.choices["method"]
        self.assertEqual(set(BACKENDS) - set(methods), set())
        self.assertEqual(methods[0], "auto")

    @unittest.skipUnless(HAS_SCANPY, "needs scanpy")
    def test_a_backends_settings_are_its_package_functions_own(self):
        from pertura.steps import genes
        self.assertIn("corr_method", genes.accepted("wilcoxon"))  # scanpy's own setting, registered by no one
        genes.check_params({"wilcoxon"}, {"corr_method": "bonferroni"})
        with self.assertRaises(ValueError):  # Pertura sets the groups and the reference itself
            genes.check_params({"wilcoxon"}, {"groups": ["A"]})

    @unittest.skipUnless(HAS_GSEAPY, "needs gseapy")
    def test_g_meaning_takes_ones_own_sets_for_any_organism_and_a_universe(self):
        from test_common_response_and_field_measures import build_screen
        design = {"perturbation": {"modality": {"value": "CRISPRi"}}, "experiment": {"organism": {"value": "other"}}}
        with tempfile.TemporaryDirectory() as d:
            s, design = build_screen(d, {"T1": (20, {})}, design=design)
            genes = [f"g{j}" for j in range(100)]
            sets = {"mine": genes[:10], "other": genes[50:70]}
            r = qm.REGISTRY["G.meaning"].run(s, design, os.path.join(d, "g"), up=genes[:8] + genes[80:82],
                                              gene_lists=sets, universe=genes)
        self.assertNotEqual(r.status, "not_enough_data", r.summary)
        self.assertEqual(r.table.iloc[0]["set"], "mine")
        self.assertIn("100 genes given as the universe", r.summary)


if __name__ == "__main__":
    unittest.main()
