"""Control-guide pairs take their kind from the design's control groups (development session 3, problem 14): on
CRISPRai each control cell's group was its own guide, so no two control guides shared a kind and no pair formed."""
import unittest
from unittest import mock

import numpy as np


class TestControlKinds(unittest.TestCase):
    def test_guides_of_one_named_group_and_mode_share_a_kind(self):
        from pertura.steps import similarity as sim
        guides = ["NTC-1-i", "NTC-2-i", "NTC-7-a", "NTC-8-a"]
        s = mock.Mock()
        s.label = np.array(["control"] * 8)
        s.top_guide = np.array(guides * 2)
        s.control_group = np.array(guides * 2)  # the cells' group is their own guide
        design = {"perturbation": {
            "controls": {"value": [{"name": "non_targeting_interference", "kind": "non_targeting",
                                    "guides": ["NTC-1-i", "NTC-2-i"]},
                                   {"name": "non_targeting_activation", "kind": "non_targeting",
                                    "guides": ["NTC-7-a", "NTC-8-a"]}]},
            "modality": {"value": "mixed"},
            "guides": {"value": {"NTC-1-i": {"mode": "CRISPRi"}, "NTC-2-i": {"mode": "CRISPRi"},
                                 "NTC-7-a": {"mode": "CRISPRa"}, "NTC-8-a": {"mode": "CRISPRa"}}}}}
        kind = sim.control_kind_of(s, design)
        self.assertEqual(kind["NTC-1-i"], kind["NTC-2-i"])
        self.assertEqual(kind["NTC-7-a"], kind["NTC-8-a"])
        self.assertNotEqual(kind["NTC-1-i"], kind["NTC-7-a"])

    def test_without_named_groups_the_cells_group_is_kept(self):
        from pertura.steps import similarity as sim
        s = mock.Mock()
        s.label = np.array(["control"] * 4)
        s.top_guide = np.array(["c1", "c2", "c1", "c2"])
        s.control_group = np.array(["ntc", "ntc", "ntc", "ntc"])
        kind = sim.control_kind_of(s, {"perturbation": {"modality": {"value": "CRISPRi"}}})
        self.assertEqual(kind["c1"], kind["c2"])


if __name__ == "__main__":
    unittest.main()
