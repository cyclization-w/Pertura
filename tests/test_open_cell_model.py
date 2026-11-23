"""ADR 0026 on a synthetic mixed library (two arms, CRISPRa and CRISPRi, each with its own control guides):
- the guide table: each guide's mode and gene reach the plan, the modules and the guide table of the screen object;
- the design check asks for every guide's mode in a mixed library, and for the controls' when references match;
- where a unit delivers pairs, each cell's label is its pair;
- a reference matched on the mode compares each target with its own arm's controls, so an arm's own baseline is
  not called a target's effect;
- memberships: the cells that carry a target's guide, and those that carry none of its guides."""
import importlib.util
import os
import tempfile
import unittest

import numpy as np
import pandas as pd

from test_common_response_and_field_measures import build_screen

from pertura import design_gate, record as rc, screen_object
from pertura.design_audit import checks
from pertura.question_modules.data import Screen

HAS_SCANPY = importlib.util.find_spec("scanpy") is not None


def node(v):
    return {"value": v, "status": "proposed", "source": "test"}


def mixed_screen(d, arm_shift=3.0, matches=True):
    """Targets G1-a, G2-a (CRISPRa) and G1-i, G2-i (CRISPRi), two guides each; controls CTRL_1-6 in the activation
    arm and CTRL_7-12 in the interference arm. The activation arm's cells (its targets and its controls) carry
    genes arm0-arm9 at arm_shift times the other arm's: a difference of the arms, not of any target."""
    groups = {"G1-a": (120, {"G1": 3.0}), "G2-a": (120, {"G2": 3.0}),
              "G1-i": (120, {"G1": 0.3}), "G2-i": (120, {"G2": 0.3}), "ARMS": (1, {f"arm{i}": 1.0 for i in range(10)})}
    guides = {lab: ([f"{lab}1", f"{lab}2"], lab) for lab in groups}
    screen, design = build_screen(d, groups, guides=guides, controls=12, control_cells=40)
    keep = screen.label != "ARMS"  # the gene names arm0-arm9 come from its group; its one cell goes
    a = screen.adata[keep].copy()
    guide = a.obs["guides"].str.split(":").str[0].to_numpy()
    arm_a = np.isin(guide, ["G1-a1", "G1-a2", "G2-a1", "G2-a2"] + [f"CTRL_{k}" for k in range(1, 7)])
    X = a.X.tolil()
    cols = [list(a.var_names).index(f"arm{i}") for i in range(10)]
    for i in np.flatnonzero(arm_a):
        for j in cols:
            X[i, j] = round(X[i, j] * arm_shift) + 3
    a.X = X.tocsr()
    table = pd.read_csv(os.path.join(d, "guides.tsv"), sep="\t")
    table = table[table["target"] != "ARMS"]
    mode = {g: ("CRISPRa" if "-a" in g or g in [f"CTRL_{k}" for k in range(1, 7)] else "CRISPRi")
            for g in table["guide"]}
    table["mode"] = [mode[g] for g in table["guide"]]
    table["gene"] = [t.split("-")[0] if t.startswith("G") else "" for t in table["target"]]
    table.to_csv(os.path.join(d, "guides.tsv"), sep="\t", index=False)
    design = {"perturbation": {"modality": {"value": "mixed"},
                               "guides": {"value": {g: {"mode": mode[g]} for g in table["guide"]}}},
              "units": {"independent_unit": {"value": "none"}}}
    if matches:
        design["perturbation"]["reference_matches"] = {"value": ["mode"]}
    return Screen(d, design, adata=a), design


class TestTheGuideTable(unittest.TestCase):
    def test_modes_reach_the_plan_and_the_modules(self):
        design = {"perturbation": {"modality": {"value": "mixed"},
                                   "guides": {"value": {"G1-a1": {"mode": "CRISPRa", "gene": "G1"},
                                                        "G1-i1": {"mode": "CRISPRi", "gene": "G1"}}}}}
        self.assertEqual(design_gate.guide_attributes(design)["G1-a1"], {"mode": "CRISPRa", "gene": "G1"})
        self.assertEqual(rc.modalities(design), {"crispra", "crispri"})
        planned = {q["question_id"] for q in rc.plan_questions(design, {"n_targets": 4, "guides_per_target": 2})}
        self.assertIn("E.opposite_modes", planned)  # session 1: never planned with modality "other"
        from pertura.question_modules.b import expected_sign
        self.assertEqual((expected_sign(design, "G1-a1"), expected_sign(design, "G1-i1")), (1, -1))

    def test_an_earlier_designs_guide_modes_still_read(self):
        design = {"perturbation": {"guide_modes": {"value": {"g1": "CRISPRi"}}}}
        self.assertEqual(design_gate.guide_attributes(design), {"g1": {"mode": "CRISPRi"}})

    def test_the_check_asks_for_what_a_mixed_library_needs(self):
        lib = {"guides": ["G1-a1", "G1-i1", "NTC-a", "NTC-i"]}
        controls = node([{"name": "NTC", "kind": "non_targeting", "guides": ["NTC-a", "NTC-i"]}])

        def problems(perturbation):
            out = []
            checks._guides({"perturbation": {"controls": controls, **perturbation}}, lib, out)
            return out
        self.assertTrue(any("every targeting guide needs its mode" in p
                            for p in problems({"modality": node("mixed")})))
        modes = node({"G1-a1": {"mode": "CRISPRa"}, "G1-i1": {"mode": "CRISPRi"}})
        self.assertEqual(problems({"modality": node("mixed"), "guides": modes}), [])
        self.assertTrue(any("control guides need their mode" in p for p in
                            problems({"modality": node("mixed"), "guides": modes,
                                      "reference_matches": node(["mode"])})))
        self.assertTrue(any("not the library's" in p for p in problems({"guides": node({"X1": {"mode": "CRISPRa"}})})))
        self.assertTrue(any("singles or pairs" in p for p in problems({"delivery": node({"by_unit": {"D1": "two"}})})))

    def test_the_form_shows_the_guide_table_in_one_line(self):
        from pertura.design_service import summary
        design = {"perturbation": {"guides": {"value": {"a1": {"mode": "CRISPRa", "gene": "G1"},
                                                        "i1": {"mode": "CRISPRi", "gene": "G1", "element": "enhancer"}}},
                                   "delivery": {"value": {"by_unit": {"D1": "pairs", "S1": "singles"}}}}}
        self.assertEqual(summary(design, "perturbation.guides"),
                         "2 guides; 1 CRISPRa, 1 CRISPRi; genes for 2; elements: enhancer")
        self.assertEqual(summary(design, "perturbation.delivery"), "D1: pairs, S1: singles")


class TestPairs(unittest.TestCase):
    def test_a_unit_that_delivers_pairs_labels_each_cell_by_its_pair(self):
        """Session 1: the pairs captures' cells were 'multi', so the combinations module found 0 pairs."""
        table = {"A1": ("A", "targeting"), "B1": ("B", "targeting"), "N1": ("N1", "non_targeting"),
                 "N2": ("N2", "non_targeting")}
        cells = pd.DataFrame({"label": ["multi", "multi", "multi", "multi", "A"],
                              "guides": ["A1:30;B1:2", "A1:9;N1:20", "N1:4;N2:7", "A1:5;B1:3;C9:1", "A1:40"],
                              "unit": ["D1", "D1", "D1", "D1", "S1"], "sample": ["RNA"] * 5})
        deliver = {("D1", "RNA"): "pairs", ("S1", "RNA"): "singles"}
        self.assertEqual(screen_object.pair_labels(cells, table, deliver), ["A|B", "A", "N1", "A|B", "A"])
        self.assertEqual(design_gate.delivery_of({"perturbation": {"delivery": {"value": {"by_unit": {"D1": "pairs"}}}}},
                                                 unit="D1"), "pairs")


class TestReferencesThatFollowAttributes(unittest.TestCase):
    def test_a_target_is_compared_with_its_own_arms_controls(self):
        with tempfile.TemporaryDirectory() as d:
            s, _ = mixed_screen(d)
            ref = s.reference("control_guides", target="G1-a")
            arm = set(s.top_guide[ref])
            self.assertEqual(arm, {f"CTRL_{k}" for k in range(1, 7)})
            self.assertEqual(set(s.top_guide[s.reference("control_guides", target="G2-i")]),
                             {f"CTRL_{k}" for k in range(7, 13)})
            self.assertEqual(int(s.reference("control_guides").sum()), 12 * 40)  # without a target: every control
            self.assertEqual(s.own_gene_of("G1-a"), "G1")  # the guide table's gene, not the label

    @unittest.skipUnless(HAS_SCANPY, "needs scanpy")
    def test_an_arms_own_baseline_is_not_a_targets_effect(self):
        """Session 1: both arms' controls were pooled, so a difference of the arms reads as every target's effect."""
        from pertura.question_modules import c
        with tempfile.TemporaryDirectory() as d:
            matched, _ = mixed_screen(d, matches=True)
            pooled, _ = mixed_screen(d, matches=False)
            leads = {t: {"method": "wilcoxon", "reason": "test"} for t in ("G1-a", "G1-i")}
            arm = {f"arm{i}" for i in range(10)}
            called = {}
            for name, s in (("matched", matched), ("pooled", pooled)):
                t, _ = c.genes_by_leads(s, leads, "control_guides", os.path.join(d, name))
                called[name] = set(t[(t["target"] == "G1-a") & (t["q"] < 0.05)]["gene"])
        self.assertTrue(arm <= called["pooled"])  # pooled controls: the arm's genes look like G1-a's effect
        self.assertFalse(arm & called["matched"])  # its own arm's controls: they do not
        self.assertIn("G1", called["matched"])


class TestMemberships(unittest.TestCase):
    def test_carriers_and_non_carriers_come_from_each_cells_guides(self):
        with tempfile.TemporaryDirectory() as d:
            s, _ = mixed_screen(d)
            carriers = s.carriers("G1-a")
            self.assertEqual(int(carriers.sum()), int((s.label == "G1-a").sum()))
            self.assertFalse((carriers & s.non_carriers("G1-a")).any())
            self.assertEqual(int((carriers | s.non_carriers("G1-a")).sum()), len(s.label))


if __name__ == "__main__":
    unittest.main()
