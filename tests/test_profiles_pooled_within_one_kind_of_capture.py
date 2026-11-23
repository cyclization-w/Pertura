"""What development session 6 showed (docs/build/development_sessions/session_06_crisprai_sonnet, problem 13), fixed
for any screen (docs/build_goal_session6_fixes.md, item 3): E.opposite_modes averaged each label's profile with equal
weight over every capture holding it, single libraries, a mixed capture and a pair library of another batch alike, and
one gene's value moved from -0.52 to -0.06 with the captures pooled. Now:
- each cell has a kind of capture, from the design's delivery, batches and other grouping factor;
- a label's profile is pooled only within one kind, weighted by cells;
- with more than one kind, each kind's value is shown beside the pooled one, and a disagreement is said;
- the similarity step gives each kind's value and reference too;
- in a capture of pairs, a single-target cell records whether its partner guide was called, and the guide-call module
  counts them."""
import os
import tempfile
import unittest

import numpy as np
import pandas as pd
import scipy.sparse as sp

from test_common_response_and_field_measures import build_screen

from pertura import question_modules as qm
from pertura import screen_object
from pertura.steps import profiles, similarity

PROGRAM = [f"p{i}" for i in range(30)]


def scale(screen, label, unit, genes, by):
    """The label's cells in one unit with the genes' counts scaled (before anything reads the expression)."""
    idx = np.flatnonzero((screen.label == label) & (screen.unit == unit))
    cols = [screen.gene_index[g] for g in genes]
    X = screen.X.toarray()
    X[np.ix_(idx, cols)] = np.round(X[np.ix_(idx, cols)] * by)
    screen.X = sp.csr_matrix(X)
    screen.libsize = X.sum(1)


def two_modes(design_units):
    """Activation and inhibition of four genes; G1's activation raises a program and its inhibition lowers it."""
    groups, guides, modes = {}, {}, {}
    for k, gene in enumerate(["G1", "G2", "G3", "G4"]):
        for arm, fold in (("a", 2.0), ("i", 0.5)):
            lab = f"{gene}-{arm}"
            fx = {gene: fold}
            if gene == "G1":
                fx.update({p: (1.8 if arm == "a" else 0.55) for p in PROGRAM})
            elif arm == "a":
                fx.update({f"x{k}_{i}": 2.0 for i in range(15)})
            groups[lab] = (120, fx)
            guides[lab] = ([f"{lab}1", f"{lab}2"], gene)
            modes.update({g: "CRISPRa" if arm == "a" else "CRISPRi" for g in guides[lab][0]})
    design = {"perturbation": {"modality": {"value": "other"}, "guide_modes": {"value": modes}},
              "units": {"independent_unit": {"value": "none"}, **design_units}}
    return groups, guides, design


class TestTheKindOfCapture(unittest.TestCase):
    def test_it_joins_the_delivery_the_batch_and_the_other_factor(self):
        design = {"perturbation": {"delivery": {"value": {"by_unit": {"u1": "singles", "u2": "singles",
                                                                       "u3": "pairs"}}}},
                  "units": {"batches": {"value": {"by_unit": {"u1": "1", "u2": "batch2", "u3": "batch2"}}},
                            "capture_factor": {"value": {"name": "effector", "by_unit": {"u1": "VPR", "u2": "KRAB",
                                                                                          "u3": "KRAB"}}}}}
        with tempfile.TemporaryDirectory() as d:
            screen, _ = build_screen(d, {"A": (30, {})}, units=("u1", "u2", "u3"), design=design)
            self.assertEqual(screen.kind_of_units(), {"u1": "singles · batch 1 · effector VPR",
                                                      "u2": "singles · batch2 · effector KRAB",
                                                      "u3": "pairs · batch2 · effector KRAB"})

    def test_none_without_the_design_saying_and_not_recorded_beside_one_that_does(self):
        with tempfile.TemporaryDirectory() as d:
            screen, _ = build_screen(d, {"A": (30, {})}, units=("u1", "u2"))
            self.assertEqual(screen.kind_of_units(), {"u1": "", "u2": ""})
            design = {"units": {"batches": {"value": {"by_unit": {"u1": "1"}}}}}
            screen, _ = build_screen(d, {"A": (30, {})}, units=("u1", "u2"), design=design)
            self.assertEqual(screen.kind_of_units(), {"u1": "batch 1", "u2": "not recorded"})


class TestPooledWithinAKind(unittest.TestCase):
    def test_weighted_by_cells_and_never_across_kinds(self):
        g = ["g1", "g2"]
        by_unit = {"u1": {"A": pd.Series([1.0, 1.0], index=g)}, "u2": {"A": pd.Series([4.0, 4.0], index=g)},
                   "u3": {"A": pd.Series([-2.0, -2.0, 5.0], index=g + ["g3"])}}
        cells = {("u1", "A"): 10, ("u2", "A"): 30, ("u3", "A"): 50}
        pooled, n = profiles.pooled_within_kinds(by_unit, cells, {"u1": "k1", "u2": "k1", "u3": "k2"})
        self.assertAlmostEqual(pooled["k1"]["A"]["g1"], 0.25 * 1 + 0.75 * 4)
        self.assertEqual(list(pooled["k2"]["A"]), [-2.0, -2.0, 5.0])
        self.assertEqual((n[("k1", "A")], n[("k2", "A")]), (40, 50))

    def test_a_disagreement_in_words(self):
        self.assertIn("the sign differs", profiles.kinds_disagree({"k1": -0.6, "k2": 0.4}))
        self.assertEqual(profiles.kinds_disagree({"k1": -0.6, "k2": -0.4}, {"k1": True, "k2": True}), "")
        self.assertEqual(profiles.kinds_disagree({"k1": -0.6, "k2": -0.4}, {"k1": True, "k2": False}),
                         "past the null in k1, not in k2")
        self.assertEqual(profiles.kinds_disagree({"k1": -0.6}), "")


class TestOppositeModesWithinAKind(unittest.TestCase):
    def test_each_kind_beside_the_pooled_value_and_their_disagreement_said(self):
        groups, guides, design = two_modes({"batches": {"value": {"by_unit": {"u1": "1", "u2": "2"}}}})
        with tempfile.TemporaryDirectory() as d:
            screen, design = build_screen(d, groups, units=("u1", "u2"), guides=guides, design=design)
            scale(screen, "G1-a", "u2", PROGRAM, 0.55 / 1.8)  # in batch 2, G1's activation lowers the program too
            r = qm.REGISTRY["E.opposite_modes"].run(screen, design, os.path.join(d, "om"))
        t = r.table.set_index("gene")
        self.assertLess(t.loc["G1", "correlation_top: batch 1"], -0.5)
        self.assertGreater(t.loc["G1", "correlation_top: batch 2"], 0.5)
        self.assertLess(t.loc["G1", "correlation_top: batch 1"], t.loc["G1", "correlation_top"])
        self.assertLess(t.loc["G1", "correlation_top"], t.loc["G1", "correlation_top: batch 2"])
        self.assertIn("the sign differs", t.loc["G1", "kinds_disagree"])
        self.assertEqual(r.diagnostics["kinds_of_capture"], ["batch 1", "batch 2"])
        self.assertIn("G1-a / G1-i", r.diagnostics["kinds_disagree"])
        self.assertIn("kinds of capture", r.summary)
        self.assertTrue(set(r.tables["profiles"]["kind"]) <= {"batch 1", "batch 2"})  # the figure's, of one kind

    def test_labels_that_share_no_kind_are_compared_across_kinds_and_said_so(self):
        groups, guides, design = two_modes({"capture_factor": {"value": {"name": "effector",
                                                                         "by_unit": {"u1": "VPR", "u2": "KRAB"}}}})
        with tempfile.TemporaryDirectory() as d:
            screen, design = build_screen(d, groups, units=("u1", "u2"), guides=guides, design=design)
            arm = np.array([x.rsplit("-", 1)[-1] if x != "control" else "" for x in screen.label])
            keep = ~(((arm == "a") & (screen.unit == "u2")) | ((arm == "i") & (screen.unit == "u1")))
            r = qm.REGISTRY["E.opposite_modes"].run(screen.subset(cells=keep), design, os.path.join(d, "om"))
        t = r.table.set_index("gene")
        self.assertEqual(t.loc["G1", "kinds"], profiles.ACROSS)
        self.assertLess(t.loc["G1", "correlation_top"], -0.5)
        self.assertIn("G1-a / G1-i", r.diagnostics["across_kinds"])
        self.assertIn("compared across kinds", r.summary)

    def test_one_kind_adds_no_columns(self):
        groups, guides, design = two_modes({})
        with tempfile.TemporaryDirectory() as d:
            screen, design = build_screen(d, groups, units=("u1", "u2"), guides=guides, design=design)
            r = qm.REGISTRY["E.opposite_modes"].run(screen, design, os.path.join(d, "om"))
        self.assertNotIn("kinds_disagree", r.table)
        self.assertNotIn("kinds_of_capture", r.diagnostics)


class TestSimilarityWithinAKind(unittest.TestCase):
    def test_each_kind_with_its_own_reference(self):
        shared = {f"s{i}": 2.0 for i in range(20)}
        groups = {"T1": (60, dict(shared)), "T2": (60, dict(shared)), "T3": (60, {})}
        design = {"perturbation": {"controls": {"value": [{"name": "NT", "kind": "non_targeting",
                                                           "guides": [f"CTRL_{k}" for k in range(1, 13)]}]}},
                  "units": {"independent_unit": {"value": "none"},
                            "batches": {"value": {"by_unit": {"u1": "1", "u2": "2"}}}}}
        with tempfile.TemporaryDirectory() as d:
            screen, _ = build_screen(d, groups, units=("u1", "u2"), control_cells=30, design=design)
            scale(screen, "T2", "u2", list(shared), 0.25)  # in batch 2, T2 lowers what T1 raises
            table, _, _ = similarity.pairs(screen, "target", min_cells=30)
        row = table[(table["group"].isin(["T1", "T2"])) & (table["other"].isin(["T1", "T2"]))].iloc[0]
        self.assertGreater(row["correlation: batch 1"], 0.5)
        self.assertLess(row["correlation: batch 2"], -0.3)
        self.assertIn("the sign differs", row["kinds_disagree"])
        self.assertTrue(np.isfinite(row["control_pairs_as_high: batch 1"]))
        controls = table[table["kind"] == "control guides"]
        self.assertTrue((controls["kinds_disagree"] == "").all())

    def test_one_kind_adds_no_columns(self):
        with tempfile.TemporaryDirectory() as d:
            screen, _ = build_screen(d, {"T1": (60, {}), "T2": (60, {})}, units=("u1", "u2"), control_cells=30)
            table, _, _ = similarity.pairs(screen, "target", min_cells=30)
        self.assertEqual(list(table.columns), similarity.COLUMNS)


class TestThePartnerOfASingleInACaptureOfPairs(unittest.TestCase):
    def test_seen_or_not_and_counted_by_the_guide_calls(self):
        table = {"A_1": ("A", "targeting"), "B_1": ("B", "targeting"), "NT_1": ("NT_1", "non_targeting")}
        cells = pd.DataFrame({"label": ["A", "A", "A|B", "A", "control"],
                              "guides": ["A_1:5;NT_1:3", "A_1:6", "A_1:4;B_1:4", "A_1:7", "NT_1:5"],
                              "unit": ["D1", "D1", "D1", "S1", "D1"], "sample": ["D1", "D1", "D1", "S1", "D1"]})
        deliver = {("D1", "D1"): "pairs", ("S1", "S1"): "singles"}
        self.assertEqual(screen_object.partner_seen(cells, table, deliver), ["seen", "not seen", "", "", ""])
        with tempfile.TemporaryDirectory() as d:
            screen, design = build_screen(d, {"A": (40, {})}, units=("u1",))
            screen.partner = np.where(screen.label == "A", np.where(np.arange(len(screen.label)) % 4, "seen",
                                                                    "not seen"), "")
            r = qm.REGISTRY["B.guide_calls_and_their_evidence"].run(screen, design, os.path.join(d, "calls"))
        self.assertEqual(r.diagnostics["single_target_cells_in_pair_captures"],
                         {"u1": {"partner not seen": 10, "partner seen": 30}})
        self.assertIn("30 with their partner guide called, 10 without", r.summary)


if __name__ == "__main__":
    unittest.main()
