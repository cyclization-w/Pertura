"""The guide-calling rules side by side before the build (docs/build_goal_one_phenomenon_explained_and_its_figures_
2026-11-11.md, step 1.1). On GSE263761 (dc5b321) Cell Ranger's calling with no floor left 9,092 of 76,344 cells with one
target, and check_design counted only the deposit, dominance and combination rules, so no one saw it before the
analysis; with a floor of 3 UMIs it leaves 31,493, dominance 36,129, the paper 28,303. check_design now counts each rule
on the measurement's guide UMIs, the cellranger rule from the fit the measurement kept, with the targets' own genes."""
import tempfile
import unittest

import numpy as np
import scipy.sparse as sp

from pertura import screen_object as so
from pertura.design_audit import checks

from test_screen_object import _mixture_runs


def screen(seed=0):
    """Two targets and a control group in two samples: each cell carries one guide with many UMIs, and most cells
    hold ambient UMIs of one guide seen nowhere else; A's own gene falls in A's cells."""
    rng = np.random.default_rng(seed)
    guides = ["a1", "b1", "nt1", "amb"]
    n = 900
    carried = np.repeat([0, 1, 2], n // 3)
    U = np.zeros((n, len(guides)), int)
    U[np.arange(n), carried] = rng.poisson(60, n) + 10
    ambient = rng.random(n) < 0.6
    U[ambient, 3] = rng.choice([1, 1, 2], ambient.sum())
    M = sp.csr_matrix(U)
    samples = np.array(["s1", "s2"] * (n // 2))
    gene_a = np.where(carried == 0, rng.poisson(1, n), rng.poisson(8, n)).astype(float)
    E = sp.csr_matrix(np.c_[gene_a, rng.poisson(8, n)].astype(np.float32))
    codes = np.array([0, 1] * (n // 2), np.int32)
    arrays = {"guides": np.array(guides), "umis_indptr": M.indptr, "umis_indices": M.indices, "umis_data": M.data,
              "calls_indptr": np.zeros(n + 1, np.int64), "calls_indices": np.zeros(0, np.int32),
              "unit_samples_codes": codes, "unit_samples_names": np.array(["s1", "s2"]),
              "cell_samples_codes": codes, "cell_samples_names": np.array(["s1", "s2"]),
              "own_rows": np.arange(n), "own_genes": np.array(["A", "B"]), "own_indptr": E.indptr,
              "own_indices": E.indices, "own_data": E.data}
    seen = [[(guides[j], int(v)) for j, v in zip(M.indices[M.indptr[i]:M.indptr[i + 1]],
                                                 M.data[M.indptr[i]:M.indptr[i + 1]])] for i in range(n)]
    return arrays, seen, list(samples)


def proposal(rule):
    return {"perturbation": {"assignment": {"value": rule},
                             "target_map": {"value": {"A": ["a1"], "B": ["b1"], "AMB": ["amb"]}},
                             "controls": {"value": [{"guides": ["nt1"], "kind": "non_targeting"}]}}}


@unittest.skipUnless(_mixture_runs(), "scikit-learn's Gaussian mixture does not run in this environment")
class TestRulesSideBySide(unittest.TestCase):
    def test_each_rule_is_counted_from_the_kept_fit_with_the_targets_own_genes(self):
        arrays, seen, samples = screen()
        with tempfile.TemporaryDirectory() as kept:
            so.cellranger_thresholds(seen, samples, 1, cache=kept)  # the measurement's fit, kept
            flags = []
            F = {"perturbation.assignment_source": {"status": "inferred"}}  # the calls are Cell Ranger's method
            checks._assignment_left(proposal({"rule": "auto"}), {}, flags, arrays, F, kept)
        form = next(f["text"] for f in flags if f.get("on_the_form"))
        rows = next(f["rules"] for f in flags if f.get("rules"))
        by = {r["rule"]: r for r in rows}
        no_floor = by["Cell Ranger's protospacer calling, no floor (proposed)"]  # auto, without the deposit's calls
        floor = by["Cell Ranger's protospacer calling, a floor of 3 UMIs"]
        self.assertNotIn("the deposit's own calls", by)
        self.assertLess(no_floor["one"], floor["one"])  # the ambient guide makes cells several with no floor
        self.assertEqual(floor["one"] + floor["control"], 900)
        self.assertIn(f"a floor of 3 UMIs {floor['one']:,}", form)
        self.assertEqual(floor["own_genes"]["targets"], 2)
        self.assertEqual(floor["own_genes"]["down"], 1)  # A's own gene falls in A's cells; B's does not
        self.assertLess(floor["own_genes"]["median_log2fc"], 0)

    def test_a_measurement_from_before_reads_its_samples_from_its_units(self):
        """A measurement made before each cell's run was kept (GSE263761 on dc5b321) holds the samples as its units."""
        arrays, seen, samples = screen()
        for k in ("cell_samples_codes", "cell_samples_names"):
            arrays.pop(k)
        with tempfile.TemporaryDirectory() as kept:
            so.cellranger_thresholds(seen, samples, 1, cache=kept)
            flags = []
            checks._assignment_left(proposal({"rule": "cellranger", "min_umis": 3}), {}, flags, arrays, {}, kept)
        rows = next(f["rules"] for f in flags if f.get("rules"))
        self.assertGreater(rows[0]["one"], 0)

    def test_without_a_kept_fit_the_cellranger_rule_waits_for_the_build(self):
        arrays, _, _ = screen()
        with tempfile.TemporaryDirectory() as kept:
            flags = []
            checks._assignment_left(proposal({"rule": "cellranger", "min_umis": 3}), {}, flags, arrays, {}, kept)
        rows = next(f["rules"] for f in flags if f.get("rules"))
        self.assertIn("counted by the build", rows[0]["not_counted"])
        self.assertTrue(rows[0]["rule"].endswith("(proposed)"))
        self.assertIn("one", next(r for r in rows if r["rule"].startswith("dominance")))  # dominance needs no fit




def test_a_design_that_leaves_the_rule_out_is_counted_as_the_default():
    """GSE263761 on f95ced6: the design auditor left the rule out, so the form gave the default with no count. The
    default is counted as the build reads it, and asked where another rule leaves other cells; where none does, the
    choice has nothing to decide and is not asked (docs/adr/0032, amendment 1, D8)."""
    arrays, _, _ = screen()
    U = sp.csr_matrix((arrays["umis_data"], arrays["umis_indices"], arrays["umis_indptr"]))
    called = (U > 0).astype(np.int32).tocsr()  # the deposit's calls hold the ambient guide too: several targets
    arrays.update(calls_indptr=called.indptr, calls_indices=called.indices)
    bare = proposal({"rule": "auto"})
    del bare["perturbation"]["assignment"]
    flags = []
    with tempfile.TemporaryDirectory() as kept:
        checks._assignment_left(bare, {}, flags, arrays, {}, kept)
    rows = next(f["rules"] for f in flags if f.get("rules"))
    assert rows[0]["rule"] == "the deposit's own calls (proposed)", rows  # auto, as the build reads it
    dominance = next(r for r in rows if r["rule"].startswith("dominance"))
    assert rows[0]["one"] < dominance["one"], rows
    assert any(f.get("on_the_form") for f in flags), flags
    called = sp.csr_matrix(U.multiply(U >= 10)).astype(np.int32)  # calls without the ambient guide: the same cells
    arrays.update(calls_indptr=called.indptr, calls_indices=called.indices)
    flags = []
    with tempfile.TemporaryDirectory() as kept:
        checks._assignment_left(bare, {}, flags, arrays, {}, kept)
    assert flags == [], flags


def test_each_rule_says_the_weak_calls_it_keeps_and_the_unit_it_leaves_fewest():
    """A looser rule keeps more cells and more weak calls (the label's guides under half the cell's guide UMIs): the
    owner reads both, and the unit a rule leaves fewest, before choosing."""
    guides = ["a1", "b1", "nt1"] + [f"x{i}" for i in range(6)]
    rows = ([{"a1": 60}] * 100 + [{"nt1": 60}] * 100           # strong calls, in s1 and in both samples
            + [{"a1": 3, **{f"x{i}": 1 for i in range(6)}}] * 60)  # dominant, but 3 of 9 guide UMIs: weak
    U = sp.csr_matrix(np.array([[r.get(g, 0) for g in guides] for r in rows]))
    n = U.shape[0]
    codes = np.array([0] * 100 + [0, 1] * 50 + [1] * 60, np.int32)
    arrays = {"guides": np.array(guides), "umis_indptr": U.indptr, "umis_indices": U.indices, "umis_data": U.data,
              "calls_indptr": np.zeros(n + 1, np.int64), "calls_indices": np.zeros(0, np.int32),
              "unit_samples_codes": codes, "unit_samples_names": np.array(["s1", "s2"]),
              "cell_samples_codes": codes, "cell_samples_names": np.array(["s1", "s2"])}
    p = {"perturbation": {"assignment": {"value": {"rule": "dominance", "min_umis": 3, "ratio": 3}},
                          "target_map": {"value": {"A": ["a1"], "B": ["b1"], **{f"X{i}": [f"x{i}"] for i in range(6)}}},
                          "controls": {"value": [{"guides": ["nt1"], "kind": "non_targeting"}]}}}
    flags = []
    with tempfile.TemporaryDirectory() as kept:
        checks._assignment_left(p, {}, flags, arrays, {}, kept)
    row = next(f["rules"] for f in flags if f.get("rules"))[0]
    assert (row["one"], row["control"]) == (160, 100), row
    assert row["weak_calls"] == round(60 / 260, 3), row
    assert row["fewest_one_in"] == ["s2", 60], row
    form = next(f["text"] for f in flags if f.get("on_the_form"))
    assert "(proposed) 160 (23% of the cells it keeps are weak calls; the fewest in s2, 60)" in form, form


def test_two_guides_of_one_target_in_a_cell_are_not_a_weak_call():
    """A dual-guide vector puts two guides of one target in each cell, each about half its guide UMIs: the call is
    read on the label's guides together, not on the top guide alone."""
    guides = ["a1", "a2", "nt1", "z1"]
    rows = [{"a1": 30, "a2": 30, "z1": 5}] * 50 + [{"nt1": 60}] * 50  # the top guide alone holds 30 of 65
    U = sp.csr_matrix(np.array([[r.get(g, 0) for g in guides] for r in rows]))
    n = U.shape[0]
    arrays = {"guides": np.array(guides), "umis_indptr": U.indptr, "umis_indices": U.indices, "umis_data": U.data,
              "calls_indptr": np.zeros(n + 1, np.int64), "calls_indices": np.zeros(0, np.int32)}
    p = {"perturbation": {"assignment": {"value": {"rule": "dominance", "min_umis": 3, "ratio": 3}},
                          "target_map": {"value": {"A": ["a1", "a2"], "Z": ["z1"]}},
                          "controls": {"value": [{"guides": ["nt1"], "kind": "non_targeting"}]}}}
    flags = []
    with tempfile.TemporaryDirectory() as kept:
        checks._assignment_left(p, {}, flags, arrays, {}, kept)
    row = next(f["rules"] for f in flags if f.get("rules"))[0]
    assert (row["one"], row["control"], row["weak_calls"]) == (50, 50, 0.0), row


if __name__ == "__main__":
    unittest.main()
