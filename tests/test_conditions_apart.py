"""The conditions apart (docs/adr/0044, D1 and D2): any module run once per value of a cell column and gathered
(by), and E.condition_x_perturbation's other readings: the change of named genes' effects between two conditions,
the control cells' own response, and each target's state shares in each condition. On the screen of a stimulation,
the stimulation raises genes g0-g9 in every cell but target A's."""
import copy
import importlib.util
import json
import os
import tempfile
import unittest
from unittest import mock

import numpy as np
import pandas as pd

from test_a_signature_from_the_controls_contrast import screen_with_a_stimulation

from pertura.question_modules import REGISTRY
from pertura.question_modules.__main__ import run_by
from pertura.question_modules.contract import Contract, Result, register

HAS_SCANPY = importlib.util.find_spec("scanpy") is not None
from pertura import envs  # noqa: E402
HAS_R = os.path.exists(envs.rscript())


def fake_edger(seen):
    """subprocess.run as the R script would answer: g0 up; the sample sheets it was given kept in seen."""
    def run(cmd, env=None, capture_output=True, text=True, **kw):
        from types import SimpleNamespace
        if "PERTURA_EXPR_DIR" not in (env or {}):  # the package versions the record keeps
            return SimpleNamespace(returncode=0, stdout="edgeR 4.8.2\n", stderr="")
        base = env["PERTURA_EXPR_DIR"]
        seen.append(pd.read_csv(os.path.join(base, "pb", "conditions.samples.tsv"), sep="\t"))
        os.makedirs(os.path.join(base, "r_results"), exist_ok=True)
        pd.DataFrame({"gene": ["g0", "h1"], "logFC": [1.6, 0.0], "p": [1e-6, 0.9]}).to_csv(
            os.path.join(base, "r_results", "conditions__edger.tsv.gz"), sep="\t", index=False)
        return SimpleNamespace(returncode=0, stderr="")
    return run


class TestARunByACellColumn(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.screen, self.design = screen_with_a_stimulation(self.tmp.name)

        @register("T.cells_counted", Contract(serves="T.cells_counted", applies_when="always", needs="labels",
                                              returns="each target's cells",
                                              fails_when=[("a count is no test", "none: a test module")],
                                              tested_against="this test"))
        def counted(screen, design, out_dir):
            table = pd.Series(screen.label).value_counts().rename_axis("target").reset_index(name="cells")
            path = os.path.join(out_dir, "figure.png")
            open(path, "w").close()
            return Result("measured", table, {}, path, {}, f"{len(screen.label)} cells",
                          biology=[f"{len(screen.label)} cells"])

    def tearDown(self):
        del REGISTRY["T.cells_counted"]
        self.tmp.cleanup()

    def test_each_value_runs_apart_and_the_results_are_gathered(self):
        out = os.path.join(self.tmp.name, "out")
        r = run_by("T.cells_counted", self.screen, self.design, out, "condition_time")
        self.assertEqual(sorted(set(r.table["condition_time"])), ["rest", "stim"])
        self.assertEqual(int(r.table["cells"].sum()), len(self.screen.label))
        self.assertEqual(r.biology, ["rest: 400 cells", "stim: 400 cells"])
        self.assertTrue(r.summary.startswith("by condition_time, each value apart: rest: 400 cells; stim: 400 cells"))
        with open(os.path.join(out, "result.json"), encoding="utf-8") as fh:
            self.assertEqual(sorted(json.load(fh)["diagnostics"]["values"]), ["rest", "stim"])
        self.assertTrue(os.path.isdir(os.path.join(out, "by_rest")) and os.path.isdir(os.path.join(out, "by_stim")))

    def test_a_value_with_nothing_to_read_is_said_and_the_others_still_run(self):
        """GSE263761 on 0adebaa: one condition of three had two targets with ten cells, and its error failed the run."""
        refused = set()

        @register("T.refuses", Contract(serves="T.refuses", applies_when="always", needs="labels",
                                        returns="each target's cells",
                                        fails_when=[("a count is no test", "none: a test module")],
                                        tested_against="this test"))
        def refuses(screen, design, out_dir):
            if set(screen.adata.obs["condition_time"].astype(str)) & refused:
                raise ValueError("no target with 10 or more cells here")
            return REGISTRY["T.cells_counted"].fn(screen, design, out_dir)
        try:
            refused.add("stim")
            r = run_by("T.refuses", self.screen, self.design, os.path.join(self.tmp.name, "out"), "condition_time")
            self.assertEqual(sorted(set(r.table["condition_time"])), ["rest"])
            self.assertEqual(r.diagnostics["values"]["stim"]["status"], "not_enough_data")
            self.assertIn("stim: not read (no target with 10 or more cells here)", r.summary)
            refused.add("rest")
            with self.assertRaisesRegex(ValueError, "no value could be read"):
                run_by("T.refuses", self.screen, self.design, os.path.join(self.tmp.name, "out2"), "condition_time")
        finally:
            del REGISTRY["T.refuses"]

    def test_a_column_of_one_value_or_none_is_refused(self):
        with self.assertRaisesRegex(ValueError, "no cell column 'nowhere'"):
            run_by("T.cells_counted", self.screen, self.design, self.tmp.name, "nowhere")
        with self.assertRaisesRegex(ValueError, "holds 1 values"):
            run_by("T.cells_counted", self.screen.subset(condition_time="stim"), self.design, self.tmp.name,
                   "condition_time")


class TestTheConditionsReadings(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.screen, self.design = screen_with_a_stimulation(self.tmp.name)
        self.module = REGISTRY["E.condition_x_perturbation"]

    def tearDown(self):
        self.tmp.cleanup()

    def out(self, name):
        path = os.path.join(self.tmp.name, name)
        os.makedirs(path)
        return path

    def test_a_named_genes_effect_that_changes_with_the_condition_is_found(self):
        r = self.module.fn(self.screen, self.design, self.out("genes"), what="genes", pair=["rest", "stim"],
                           genes=["g0", "g1", "g30"])
        t = r.table.set_index(["target", "score"])
        self.assertLess(t.loc[("A", "g0"), "interaction"], 0)  # A's cells miss the stimulation's rise
        self.assertLess(t.loc[("A", "g0"), "q"], 0.05)
        self.assertGreater(t.loc[("A", "g30"), "q"], 0.05)
        self.assertIn("target x gene effects differ between rest and stim", r.summary)

    def states(self):
        rng = np.random.default_rng(1)
        obs = self.screen.adata.obs
        a_stim = (obs["label"] == "A").to_numpy() & (obs["condition"] == "stim").to_numpy()
        s1 = np.where(a_stim, rng.random(len(obs)) < 0.8, rng.random(len(obs)) < 0.1)
        return pd.DataFrame({"barcode": obs.index.astype(str), "state": np.where(s1, "S1", "S2")})

    def test_shares_shifting_in_each_condition_are_said_apart(self):
        """docs/adr/0045, D4: a shift in one condition and none in the other are two facts, not a difference."""
        r = self.module.fn(self.screen, self.design, self.out("shares"), what="shares", pair=["rest", "stim"],
                           cell_states=self.states())
        row = r.table.set_index(["target", "state"]).loc[("A", "S1")]
        self.assertEqual(row["changed_in"], "stim")
        self.assertEqual(r.status, "detected")
        self.assertTrue(any(x.startswith("in stim: A in S1, share +") for x in r.biology), r.biology)
        self.assertFalse(any("only" in x for x in r.biology), r.biology)
        self.assertIn("not tested between them", r.summary)

    def test_without_a_pair_the_conditions_go_in_the_designs_order(self):
        design = {**self.design, "experiment": {**self.design["experiment"], "conditions": {
            "value": {"column": "condition_time", "names": ["stim", "rest"]}}}}
        r = self.module.fn(self.screen, design, self.out("order"), what="shares", cell_states=self.states())
        self.assertEqual(r.diagnostics["conditions"], ["stim", "rest"])

    def controls(self, screen, name):
        with mock.patch("subprocess.run", side_effect=fake_edger([])):
            return self.module.fn(screen, screen.design, self.out(name), what="controls", pair=["rest", "stim"])

    def test_two_kinds_of_control_leave_the_cutting_ones_out(self):
        """docs/adr/0045, D4: one kind of control cell is the reference, in the module, for every caller."""
        s = copy.copy(self.screen)
        s.guides = s.guides.assign(kind=np.where(s.guides["guide"] == "NTC_2", "intergenic", s.guides["kind"]))
        r = self.controls(s, "kinds")
        self.assertTrue(r.diagnostics["reference"].startswith(
            "the non-targeting control cells (240 cutting control cells left out"), r.diagnostics["reference"])
        self.assertEqual(r.diagnostics["control_cells"], {"every control cell": {"rest": 120, "stim": 120}})

    def test_each_arm_s_control_cells_are_compared_apart(self):
        s = copy.copy(self.screen)
        s.guides = s.guides.assign(mode=["crispri", "crispra", "crispri", "crispra"])
        s.design = {**self.design, "perturbation": {**self.design["perturbation"],
                                                    "reference_matches": {"value": ["mode"]}}}
        for k in ("reference_matches", "arms_of_cells", "match_key_of_cells", "_in_arm"):
            s.__dict__.pop(k, None)
        r = self.controls(s, "arms")
        self.assertEqual(r.diagnostics["arms"], ["crispra", "crispri"])
        self.assertEqual(sorted(set(r.table["arm"])), ["crispra", "crispri"])
        self.assertIn("(crispri arm)", r.summary)
        self.assertEqual(r.diagnostics["control_cells"]["crispri"], {"rest": 120, "stim": 120})

    @unittest.skipUnless(HAS_SCANPY, "needs scanpy")
    def test_the_test_on_cells_runs_only_as_chosen(self):
        r = self.module.fn(self.screen, self.design, self.out("cells"), what="controls", pair=["rest", "stim"],
                           test="cells")
        self.assertIn("Wilcoxon on cells, as chosen", r.diagnostics["test"])

    def test_the_readings_take_only_their_own_inputs(self):
        with self.assertRaisesRegex(ValueError, "genes is used only with what"):
            self.module.check({"what": "scores", "genes": ["g0"]})
        r = self.module.fn(self.screen, self.design, self.out("none"), what="shares", pair=["rest", "stim"])
        self.assertEqual(r.status, "not_enough_data")

    @unittest.skipUnless(HAS_SCANPY and HAS_R, "needs scanpy and the R environment (edgeR)")
    def test_the_control_cells_response_to_the_stimulation(self):
        r = self.module.fn(self.screen, self.design, self.out("controls"), what="controls", pair=["rest", "stim"])
        up = r.biology[0]
        self.assertTrue(up.startswith("higher in stim: ") and set(up[len("higher in stim: "):].split(", "))
                        <= {f"g{i}" for i in range(10)}, up)
        self.assertEqual(r.status, "detected")
        self.assertEqual(r.diagnostics["test"], "edgeR-QL pseudobulk of 2 and 2 units, unpaired (~ condition)")

    @unittest.skipUnless(HAS_SCANPY and HAS_R, "needs scanpy and the R environment (edgeR)")
    def test_the_control_cells_blocked_by_batch(self):
        design = {**self.design, "units": {**self.design["units"], "batches": {
            "value": {"by_unit": {"u1": "b1", "u3": "b1", "u2": "b2", "u4": "b2"}}}}}
        r = self.module.fn(self.screen, design, self.out("paired"), what="controls", pair=["rest", "stim"])
        self.assertEqual(r.diagnostics["test"], "edgeR-QL pseudobulk of 2 and 2 units, blocked by batch "
                                                "(~ batch + condition)")
        self.assertEqual(r.status, "detected")


class TestTheControlCellsOnUnits(unittest.TestCase):
    """docs/adr/0045, D4: GSE250558's control cells changed in 12,794 genes from 6 h to 18 h on cells, with two
    cultures in each condition; the change is tested on units where each condition holds two."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.screen, self.design = screen_with_a_stimulation(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def sides(self):
        from pertura.question_modules.e import controls_by_unit
        control, cond = self.screen.reference("control_guides"), np.asarray(self.screen.condition)
        return controls_by_unit, control & (cond == "rest"), control & (cond == "stim")

    def run_on(self, screen, design=None, first="rest", second="stim"):
        from pertura.question_modules.e import controls_by_unit
        control, cond = screen.reference("control_guides"), np.asarray(screen.condition)
        seen = []
        with mock.patch("subprocess.run", side_effect=fake_edger(seen)):
            t, how = controls_by_unit(screen, design or self.design, control & (cond == first),
                                      control & (cond == second), self.tmp.name)
        return t, how, seen

    def test_units_blocked_by_the_batches_that_hold_both_conditions(self):
        design = {**self.design, "units": {**self.design["units"], "batches": {
            "value": {"by_unit": {"u1": "b1", "u3": "b1", "u2": "b2", "u4": "b2"}}}}}
        t, how, seen = self.run_on(self.screen, design)
        self.assertEqual(sorted(seen[0]["pair"]), ["b1", "b1", "b2", "b2"])
        self.assertIn("blocked by batch (~ batch + condition)", how["how"])
        self.assertTrue(how["tested"])
        self.assertEqual(list(t.columns), ["gene", "effect", "statistic", "p", "q"])
        self.assertGreater(t.set_index("gene").loc["g0", "statistic"], 0)

    def test_batches_named_by_sample_block_too(self):
        s = copy.copy(self.screen)
        s.sample = np.asarray(s.unit).copy()  # each mouse its own sample
        design = {**self.design, "units": {**self.design["units"], "batches": {
            "value": {"by_sample": {"u1": "b1", "u3": "b1", "u2": "b2", "u4": "b2"}}}}}
        _, how, seen = self.run_on(s, design)
        self.assertEqual(sorted(seen[0]["pair"]), ["b1", "b1", "b2", "b2"])

    def test_units_in_one_condition_each_are_unpaired(self):
        _, how, seen = self.run_on(self.screen)
        self.assertNotIn("pair", seen[0].columns)
        self.assertEqual(how["how"], "edgeR-QL pseudobulk of 2 and 2 units, unpaired (~ condition)")

    def test_a_unit_in_both_conditions_pairs_with_itself(self):
        """D4: one mouse sampled at rest and stimulated is its own pair, not two independent samples."""
        s = copy.copy(self.screen)
        s.unit = np.array([{"u3": "u1", "u4": "u2"}.get(u, u) for u in s.unit])
        _, how, seen = self.run_on(s)
        self.assertEqual(sorted(seen[0]["pair"]), ["u1", "u1", "u2", "u2"])
        self.assertEqual(how["how"], "edgeR-QL pseudobulk of 2 and 2 units, paired by unit (~ unit + condition)")

    def test_one_complete_pair_keeps_its_own_difference_and_no_q(self):
        s = copy.copy(self.screen)
        s.unit = np.array([{"u3": "u1"}.get(u, u) for u in s.unit])  # u1 in both; u2 and u4 in one condition each
        t, how, seen = self.run_on(s)
        self.assertEqual(seen, [])  # no test
        self.assertFalse(how["tested"])
        self.assertIn("one unit holds both conditions", how["how"])
        self.assertNotIn("q", t.columns)
        self.assertEqual([c for c in t.columns if c.startswith("log2 CPM")], ["log2 CPM u1|FIRST",
                                                                               "log2 CPM u1|SECOND"])
        self.assertGreater(t.set_index("gene").loc["g0", "effect"], 1)  # threefold with the stimulation

    def test_one_unit_in_a_condition_lists_each_units_values_with_no_q(self):
        fn, first, second = self.sides()
        one = second & (np.asarray(self.screen.unit) == "u1")
        t, how = fn(self.screen, self.design, first, one, self.tmp.name)
        self.assertFalse(how["tested"])
        self.assertIn("descriptive, no q", how["how"])
        self.assertIn("2 and 1 units of 10 control cells", how["how"])
        self.assertNotIn("q", t.columns)

    def test_a_design_without_replicates_lists_its_captures_with_no_q(self):
        s = copy.copy(self.screen)
        s.design = {**self.design, "units": {**self.design["units"], "independent_unit": {"value": "none"}}}
        t, how, seen = self.run_on(s, s.design)
        self.assertEqual(seen, [])
        self.assertIn("technical captures", how["how"])
        self.assertNotIn("q", t.columns)

    def test_cells_whose_unit_was_not_told_are_no_unit(self):
        s = copy.copy(self.screen)
        unit = np.asarray(s.unit).astype(object)
        unit[np.flatnonzero((unit == "u1") & (s.label == "control"))[:30]] = "u1:unassigned"
        s.unit = unit.astype(str)
        _, _, seen = self.run_on(s)
        self.assertEqual(sorted(seen[0]["mouse"]), ["u1", "u2", "u3", "u4"])

    def test_counts_that_are_not_integers_are_described(self):
        s = copy.copy(self.screen)
        s.X = self.screen.X * 0.5
        t, how, seen = self.run_on(s)
        self.assertEqual(seen, [])
        self.assertIn("not raw integers", how["how"])


if __name__ == "__main__":
    unittest.main()
