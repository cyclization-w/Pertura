"""The similarity step and the steps on effects across targets (docs/adr/0031, phase 2, batch B): two targets that
share a planted effect correlate beyond every pair of control guides and an inert one does not; two programs that
targets move in opposite directions correlate below -0.8; a target planted up on one program and down on another
ranks first and past the control-guide groups; two groups of targets moving different programs fall into two stable
sets by each method."""
import importlib.util
import os
import shutil
import tempfile
import unittest

import numpy as np
import pandas as pd

from pertura.steps import effects_across_targets as eat
from pertura.steps import similarity

try:
    from sklearn.cluster import HDBSCAN  # noqa: F401 (scikit-learn 1.3 and later)
    HAS_HDBSCAN = True
except ImportError:
    HAS_HDBSCAN = False
HAS_LEIDEN = importlib.util.find_spec("leidenalg") is not None and importlib.util.find_spec("igraph") is not None
METHODS = ["hierarchical"] + (["hdbscan"] if HAS_HDBSCAN else []) + (["leiden"] if HAS_LEIDEN else [])


def shared_effect_screen(folder):
    """Two samples; T1 and T2 raise g0-g9 together, T3 does nothing; 12 non-targeting control guides of one kind."""
    from test_common_response_and_field_measures import build_screen
    os.makedirs(folder, exist_ok=True)
    fx = {f"g{j}": 3.0 for j in range(10)}
    s, design = build_screen(folder, {"T1": (60, fx), "T2": (60, fx), "T3": (60, {})}, units=("u1", "u2"),
                             genes=150, controls=12, control_cells=40)
    s.adata.obs["control_group"] = np.where(s.label == "control", "NT", "")
    s.__dict__.pop("control_group", None)
    s.control_group = np.where(s.label == "control", "NT", "")
    return s, design


def effects_table():
    """Targets by programs: T00-T09 lower P1 and raise P2; T10-T19 lower P1 and raise P3; noise elsewhere."""
    rng = np.random.default_rng(0)
    rows = []
    for i in range(20):
        t = f"T{i:02d}"
        eff = {"P1": -1.0, "P2": 1.0 if i < 10 else 0.0, "P3": 1.0 if i >= 10 else 0.0, "P4": 0.0}
        for p, e in eff.items():
            rows.append({"target": t, "score": p, "effect": e + rng.normal(0, 0.1)})
    return pd.DataFrame(rows)


class TestSimilarity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = tempfile.mkdtemp()
        cls.s, cls.design = shared_effect_screen(cls.d)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.d, ignore_errors=True)

    def test_targets_sharing_an_effect_correlate_beyond_the_control_pairs(self):
        table, matrix, control = similarity.pairs(self.s, "target", ["T1", "T2", "T3"], min_cells=30)
        self.assertGreater(len(control), 10)  # 12 guides of one kind: 66 pairs, each averaged over 2 samples
        t = table[table["kind"] != "control guides"].set_index(["group", "other"])
        self.assertEqual(t.loc[("T1", "T2"), "control_pairs_as_high"], 0.0)
        self.assertGreater(t.loc[("T1", "T3"), "control_pairs_as_high"], 0.05)
        self.assertEqual(set(t["units"]), {2})
        self.assertGreater(matrix.at["T1", "T2"], matrix.at["T1", "T3"])

    def test_a_target_spread_over_samples_is_profiled_when_its_cells_are_pooled(self):
        """Six samples of 10 cells of each target: pooled over the samples (each cell against its own sample's
        control cells), the targets have 60 cells and a profile; by_sample, none has 30 in a sample."""
        from test_common_response_and_field_measures import build_screen
        fx = {f"g{j}": 3.0 for j in range(10)}
        with tempfile.TemporaryDirectory() as d:
            s, _ = build_screen(d, {"T1": (10, fx), "T2": (10, fx), "T3": (10, {})},
                                units=tuple(f"u{k}" for k in range(1, 7)), genes=150, controls=12, control_cells=10)
            pooled, _, _ = similarity.pairs(s, "target", ["T1", "T2", "T3"], min_cells=30)
            by_sample, _, _ = similarity.pairs(s, "target", ["T1", "T2", "T3"], min_cells=30, profile="by_sample")
        t = pooled[pooled["kind"] != "control guides"].set_index(["group", "other"])
        self.assertGreater(t.loc[("T1", "T2"), "correlation"], 0.5)
        self.assertEqual(int(t.loc[("T1", "T2"), "units"]), 6)
        self.assertEqual(len(by_sample[by_sample["kind"] != "control guides"]), 0)
        with self.assertRaises(ValueError):
            similarity.pairs(s, "target", profile="per_cell")

    def test_targets_without_an_effect_are_not_read_as_alike_beside_small_control_guides(self):
        """Profiles set against the same control cells share that mean's noise, more the more cells a target has
        beside them; control guides with few cells share less. Read against halves of the control cells, six inert
        targets of 60 cells a sample are not beyond the control-guide pairs, pooled or by sample."""
        from test_common_response_and_field_measures import build_screen
        targets = [f"T{i}" for i in range(1, 7)]
        with tempfile.TemporaryDirectory() as d:
            s, _ = build_screen(d, {t: (60, {}) for t in targets}, units=tuple(f"u{k}" for k in range(1, 7)),
                                genes=150, controls=12, control_cells=8)
            s.control_group = np.where(s.label == "control", "NT", "")
            for profile, cells in (("pooled", 30), ("by_sample", 5)):
                table, _, control = similarity.pairs(s, "target", targets, min_cells=cells, profile=profile)
                t = table[table["kind"] != "control guides"]
                self.assertGreater(len(control), 10, profile)
                self.assertEqual(len(t), 15, profile)
                beyond = float((t["control_pairs_as_high"] <= 0.05).mean())
                self.assertLess(beyond, 0.25, profile)
                # the correlation against all the control cells, the one reported, carries the shared noise
                self.assertGreater(t["correlation"].mean(), t["correlation_halves"].mean(), profile)

    def test_each_metric_and_a_wrong_one(self):
        for metric in ("spearman", "cosine"):
            table, _, _ = similarity.pairs(self.s, "target", ["T1", "T2", "T3"], metric=metric, min_cells=30)
            t = table[table["kind"] != "control guides"].set_index(["group", "other"])
            # beyond 95% of the control-guide pairs: a pooled profile's correlation is one value, not a mean over
            # samples, so its control pairs spread wider than by_sample's
            self.assertLessEqual(t.loc[("T1", "T2"), "control_pairs_as_high"], 0.05, metric)
        with self.assertRaises(ValueError):
            similarity.pairs(self.s, "target", metric="euclid")
        with self.assertRaises(ValueError):
            similarity.pairs(self.s, "construct")

    def test_the_guide_level_pairs_guides_of_one_target(self):
        table, _, _ = similarity.pairs(self.s, "guide", min_cells=20)
        kinds = table["kind"].value_counts().to_dict()
        self.assertEqual(kinds["same target"], 3)  # T1_1 with T1_2, and so on
        t = table[table["kind"] == "same target"].set_index("target")
        self.assertEqual(t.loc["T1", "control_pairs_as_high"], 0.0)


class TestEffectsAcrossTargets(unittest.TestCase):
    def test_two_programs_moved_in_opposite_directions_correlate(self):
        out = eat.correlate(effects_table())
        r = out.set_index(["program_a", "program_b"])
        self.assertLess(r.loc[("P1", "P2"), "r"], 0.3)  # every target lowers P1: little spread to correlate
        self.assertLess(r.loc[("P2", "P3"), "r"], -0.8)  # the two sets move P2 or P3, never both
        self.assertLess(r.loc[("P2", "P3"), "q"], 0.05)
        with self.assertRaises(ValueError):
            eat.correlate(effects_table(), method="kendall")

    def test_a_target_up_on_one_and_down_on_the_other_ranks_first_and_past_the_groups(self):
        t = effects_table()
        t.loc[(t["target"] == "T00") & (t["score"] == "P2"), "effect"] = 3.0
        null = pd.DataFrame([{"target": f"G{k}", "score": p, "effect": np.random.default_rng(k).normal(0, 0.2)}
                             for k in range(10) for p in ("P1", "P2")])
        per, summary = eat.rank_on(t, up="P2", down="P1", null=null)
        self.assertEqual(per.iloc[0]["target"], "T00")
        self.assertIn("T00", summary["targets_beyond"])
        self.assertEqual(summary["groups"], 10)
        with self.assertRaises(ValueError):
            eat.rank_on(t, up="P9", down="P1")

    def test_two_groups_of_targets_fall_into_two_stable_sets(self):
        M = eat.effects_matrix(effects_table())[["P2", "P3", "P4"]]
        first, second = {f"T{i:02d}" for i in range(10)}, {f"T{i:02d}" for i in range(10, 20)}
        for method in METHODS:  # each the environment has
            got, _ = eat.clusters(M.to_numpy(), list(M.index), method)
            sets = pd.Series(got).groupby(pd.Series(got)).apply(lambda s: set(s.index))
            self.assertTrue(any(x == first for x in sets) and any(x == second for x in sets), (method, sets))
        got, _ = eat.clusters(M.to_numpy(), list(M.index), "hierarchical")
        stable = eat.stability(M.to_numpy(), list(M.index), got, "hierarchical", draws=20)
        # with three features, a resample that draws only the noise program splits the sets at random
        self.assertTrue(all(v is not None and v > 0.6 for v in stable.values()), stable)


class TestTheBlocks(unittest.TestCase):
    def test_the_effects_blocks_read_an_earlier_result(self):
        from pertura import blocks as pt
        d = tempfile.mkdtemp()
        try:
            from test_common_response_and_field_measures import build_screen
            labels = {f"T{i:02d}": (12, {}) for i in range(20)}
            s, _ = build_screen(d, labels, units=("u1",), genes=40, controls=4, control_cells=10)
            with pt.inside(s, os.path.join(d, "blocks")):
                scores = pt.Result("scores", {"reference": "control_guides"}, effects_table(), {}, [], "every cell",
                                   call_id="scores-test")
                ax = pt.correlate_effects(of=scores)
                shared = pt.correlate_effects(of=scores, top_genes={"P2": ["g1", "g2"], "P3": ["g2", "g3"]})
                ranked = pt.rank_on(of=scores, up="P2", down="P1")
                sets = pt.cluster_targets(of=scores, method="hierarchical", draws=10)
            self.assertIn("r", ax.table.columns)
            # the programs' shared top genes counted as E.program_axis counts them (bug register, BR10)
            row = shared.table.set_index(["program_a", "program_b"]).loc[("P2", "P3")]
            self.assertEqual(int(row["shared_top_genes"]), 1)
            pd.testing.assert_series_equal(shared.table["r"], ax.table["r"])
            self.assertEqual(len(ranked.table), 20)
            self.assertGreaterEqual(sets.extra["summary"]["sets"], 2)
            with self.assertRaises(ValueError):
                pt.calibrate(ax)
        finally:
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
