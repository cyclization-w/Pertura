"""A module refuses what would do nothing or be misread (docs/adr/0024, section 2): an unknown name, a value outside
a choice's list, a choice or input its method does not use. Trial 1, attempt 3: C.genes took exclude_cells with
edgeR-QL and ignored it."""
import os
import tempfile
import unittest
from unittest import mock

from pertura import question_modules as qm
from pertura.analysis_service import AnalysisError, AnalysisService


class TestModuleCheck(unittest.TestCase):
    def refused(self, module, given, words):
        with self.assertRaises(ValueError) as e:
            qm.REGISTRY[module].check(given)
        for w in words:
            self.assertIn(w, str(e.exception))

    def test_a_value_outside_the_list_is_refused_with_the_list(self):
        self.refused("C.genes", {"method": "edgeR"}, ["method is one of", "edger_ql"])
        self.refused("C.genes", {"reference": "controls"}, ["control_guides"])

    def test_a_numeric_choice_takes_any_number_but_not_text(self):
        qm.REGISTRY["C.states_and_composition"].check({"resolution": 0.6})
        qm.REGISTRY["B.efficacy"].check({"threshold": 0.3})
        self.refused("C.states_and_composition", {"resolution": "high"}, ["a number"])
        self.refused("C.states_and_composition", {"resolution": True}, ["a number"])

    def test_an_input_its_method_does_not_use_is_refused(self):
        self.refused("C.genes", {"method": "edger_ql", "groups": {"a": ["A"]}},
                     ["groups is used only with method ['seurat3_lr']", "'edger_ql'"])
        self.refused("C.genes", {"exclude_cells": ["a"]}, ["no choice or input"])  # cell sets replace it
        self.refused("C.genes", {"thresholds": {"p_adj": 0.05}}, ["'auto'"])  # the default method
        qm.REGISTRY["C.genes"].check({"method": "seurat3_lr", "thresholds": {"p_adj": 0.05}})
        self.refused("C.programs", {"gene_lists": {"a": ["G1"]}}, ["gene_sets ['given']"])
        qm.REGISTRY["C.programs"].check({"gene_sets": "given", "gene_lists": {"a": ["G1"]}})
        self.refused("G.meaning", {"method": "gsea", "genes": ["G1"]}, ["method ['ora']"])
        self.refused("E.combinations", {"synergy": 0.2}, ["model ['regulatory_modes']"])
        self.refused("B.escape", {"resolution": 0.5}, ["perturbation_driven_clusters"])
        qm.REGISTRY["B.escape"].check({"resolution": 0.8})  # its default changes nothing: not refused

    def test_an_unknown_name_is_refused_with_what_exists(self):
        self.refused("B.efficacy", {"nonsense": 1}, ["no choice or input", "threshold"])

    def test_every_scope_names_real_choices_and_values(self):
        for name, m in qm.REGISTRY.items():
            for k, (by, values) in m.contract.scope.items():
                self.assertTrue(k in m.contract.choices or k in m.contract.inputs, (name, k))
                self.assertIn(by, m.contract.choices, (name, k))
                self.assertTrue(set(values) <= set(m.contract.choices[by]), (name, k, values))

    def test_each_default_is_allowed(self):
        for name, m in qm.REGISTRY.items():
            m.check({k: m.default(k) for k in m.contract.choices if m.default(k) is not None})


class TestTheServiceRefusesBeforeAJob(unittest.TestCase):
    def test_run_module_refuses_an_input_its_method_ignores(self):
        with tempfile.TemporaryDirectory() as d:
            svc = AnalysisService({"workspace": os.path.join(d, "ws")})
            try:
                svc.record = mock.Mock()
                with mock.patch.object(svc, "_need"), mock.patch.object(svc, "_confirmed"):
                    with self.assertRaises(AnalysisError) as e:
                        svc.run_module("C.genes", choices={"method": "edger_ql"}, inputs={"groups": {"a": ["A"]}})
                    self.assertIn("used only with method", str(e.exception))
                    svc.record.start_run.assert_not_called()
                svc.record = None
            finally:
                svc.close()









if __name__ == "__main__":
    unittest.main()
