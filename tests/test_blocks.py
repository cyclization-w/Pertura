"""Blocks (docs/adr/0024, sections 2, 4 and 8): the analyses' parts the agent calls in its notebook as pt.<name>,
each call checked, logged and returning a Result; the checks rerunning a result's own call (control-guide groups,
each guide, thinned depth); the blocks that need R or processes handed to a job and collected through a Handle."""
import importlib.util
import json
import os
import tempfile
import unittest

import numpy as np
import pandas as pd

from synthetic_screen import make_cell_screen
from test_question_modules import synthetic_screen

import pertura.blocks as pt

HAS_SCANPY = importlib.util.find_spec("scanpy") is not None


class Notebook:
    """pt's context on a synthetic screen held in memory, as a notebook's start-up sets it."""

    def __init__(self, make):
        self.tmp = tempfile.TemporaryDirectory()
        d = self.tmp.name
        self.screen_dir = os.path.join(d, "screen")
        os.makedirs(self.screen_dir)
        out = make(self.screen_dir)
        self.screen = out[0]
        self.nb = os.path.join(d, "notebooks", "main")
        os.makedirs(self.nb)
        pt.CONTEXT.clear()
        pt.context(screen_dir=self.screen_dir, design_path=None, notebook_dir=self.nb,
                   jobs_dir=os.path.join(d, "jobs"))
        pt._SCREEN.clear()
        pt._SCREEN[self.screen_dir] = self.screen

    def calls(self):
        with open(os.path.join(self.nb, "blocks", pt.LOG), encoding="utf-8") as fh:
            return [json.loads(x) for x in fh.read().splitlines() if x.strip()]

    def close(self):
        pt.CONTEXT.clear()
        pt._SCREEN.clear()
        self.tmp.cleanup()


class TestACall(unittest.TestCase):
    def setUp(self):
        self.nb = Notebook(lambda d: make_cell_screen(d, seed=3))

    def tearDown(self):
        self.nb.close()

    def test_a_gene_test_finds_the_planted_gene_and_logs_its_call(self):
        res = pt.genes(targets=["EVERY"], method="seurat3_lr")
        self.assertIsInstance(res, pt.Result)
        top = res.table.sort_values("p").iloc[0]
        self.assertEqual(top["gene"], "gE")
        self.assertGreater(top["effect"], 0)
        self.assertEqual(set(res.table.columns) >= {"target", "gene", "effect", "p", "q", "cells", "reference_cells"},
                         True)
        self.assertEqual(res.n["targets"], {"EVERY": int((self.nb.screen.label == "EVERY").sum())})
        call = self.nb.calls()[-1]
        self.assertEqual((call["block"], call["targets"], call["choices"]), ("genes", ["EVERY"],
                                                                             {"method": "seurat3_lr"}))
        self.assertEqual(call["cells"], "every cell")
        self.assertTrue(call["kept_cells"])

    def test_calls_that_would_be_misread_are_refused(self):
        with self.assertRaises(ValueError) as e:
            pt.genes(method="edgeR")
        self.assertIn("edger_ql", str(e.exception))
        with self.assertRaises(ValueError) as e:
            pt.genes(targets=["NOPE"], method="seurat3_lr")
        self.assertIn("no target ['NOPE']", str(e.exception))
        with self.assertRaises(ValueError):
            pt.genes(targets=["EVERY"], reference="controls", method="seurat3_lr")
        with self.assertRaises(ValueError):
            pt.genes(cells=3)

    def test_the_checks_rerun_the_results_own_call(self):
        res = pt.genes(targets=["EVERY"], method="seurat3_lr")
        null = pt.calibrate(res)
        self.assertTrue(all(t.startswith("ncg_") for t in null.args["targets"]))
        summary = null.extra["summary"]
        self.assertIn("EVERY", summary["targets_beyond"])
        guides = res.by_guide()
        self.assertEqual(sorted(guides.args["targets"]), ["EVERY_1", "EVERY_2"])
        agree = guides.extra["agreement"]
        self.assertTrue((agree["same_sign"] == 1.0).all(), agree)
        thin = res.match_depth()
        self.assertIn("depth_factor", thin.table.columns)
        checks = [c for c in self.nb.calls() if c.get("check")]
        self.assertEqual([c["check"] for c in checks], ["calibration", "by_guide", "match_depth"])
        self.assertTrue(all(c["check_of"] == res.call_id for c in checks))

    def test_the_screen_printed_says_what_its_cells_hold(self):
        text = repr(pt.screen())
        self.assertIn(f"{len(self.nb.screen.label)} cells x", text)
        self.assertIn("label_before_qc", text)
        self.assertIn("EVERY", text)
        self.assertIn("pt.describe()", text)

    def test_a_selection_and_a_kept_set(self):
        s = pt.screen().subset(unit=["m1", "m2", "m3"])
        res = pt.genes(s, targets=["EVERY"], method="seurat3_lr")
        self.assertIn("not kept", res.cells)
        self.assertFalse(self.nb.calls()[-1]["kept_cells"])

    def test_by_a_cell_column(self):
        res = pt.genes(targets=["EVERY"], method="seurat3_lr", by="unit")
        self.assertEqual(sorted(set(res.table["unit"])), [f"m{i}" for i in range(1, 7)])
        self.assertEqual(len(res.n), 6)

    def test_by_a_cell_column_skips_a_value_with_no_cells_to_test(self):
        # the fair comparison's D: one unit held only cells that failed QC, and the whole call failed
        s = pt.screen()
        part = s.adata.obs["unit"].astype(str).to_numpy().copy()
        part[np.flatnonzero(s.reference("control_guides"))[:30]] = "no_unit"
        s.adata.obs["part"] = part
        with self.assertWarns(UserWarning):
            res = pt.genes(targets=["EVERY"], method="seurat3_lr", by="part")
        self.assertEqual(sorted(set(res.table["part"])), [f"m{i}" for i in range(1, 7)])
        self.assertEqual(res.extra["skipped"], ["no_unit"])
        self.assertIn("skipped by 'part': ['no_unit']", repr(res))
        with self.assertRaises(pt.NoCells):  # no value holds the target: said, not an empty result
            pt.genes(targets=["NOPE"], method="seurat3_lr", by="part")

    def test_a_result_saved_and_loaded_from_any_notebook(self):
        res = pt.genes(targets=["EVERY"], method="seurat3_lr")
        res.save("every.lr")
        self.assertEqual(pt.results.list(), {"every.lr": "main"})
        back = pt.results.load("every.lr")
        self.assertEqual(len(back.table), len(res.table))
        with self.assertRaises(ValueError):
            back.calibrate()  # a loaded result has no cells to rerun

    def test_shared_and_similar(self):
        res = pt.genes(targets=["EVERY", "ONE"], method="seurat3_lr")
        self.assertIn("targets", res.shared().columns)
        self.assertEqual(res.similar().shape[0], res.similar().shape[1])

    def test_a_job_block_returns_a_handle_and_writes_its_request(self):
        h = pt.genes(targets=["EVERY"], method="edger_ql")
        self.assertIsInstance(h, pt.Handle)
        self.assertEqual(h.state(), "starts when this cell ends")
        req = os.path.join(h.folder, "request.json")
        self.assertTrue(os.path.exists(req))
        cells = pd.read_csv(os.path.join(h.folder, "cells.tsv.gz"), sep="\t")
        self.assertEqual(len(cells), len(self.nb.screen.label))
        self.assertTrue(self.nb.calls()[-1]["job"])
        with self.assertRaises(RuntimeError):
            h.result()

    def test_help_lists_every_name_and_explains_one(self):
        text = pt.help()
        for name in ("genes", "any_effect", "scores", "states", "on_target", "calibrate", "by_guide", "match_depth",
                     "describe", "draw", "save_cells", "results"):
            self.assertIn(f"pt.{name}", text)
        self.assertTrue(pt.help(question="C.genes").startswith("pt.genes"))
        self.assertIn("as a job (a Handle) for method edger_ql, mast, seurat_lr", pt.help("genes"))


class TestCalibrationSummary(unittest.TestCase):
    @staticmethod
    def result(method, targets, rows):
        return pt.Result("genes", {"method": method, "targets": targets},
                         pd.DataFrame(rows, columns=["target", "gene", "q"]), {}, [], "every cell")

    def test_a_group_edger_could_not_fit_is_left_out_not_counted_as_none(self):
        """Counted as no discovery, two groups without 3 samples pulled the 95th percentile from 40 to 36, and a target
        with 38 changed genes looked beyond the control groups."""
        target = self.result("edger_ql", ["T"], [("T", f"g{i}", 0.01) for i in range(38)])
        null = self.result("edger_ql", ["ncg_1", "ncg_2", "ncg_3"], [("ncg_1", f"g{i}", 0.01) for i in range(40)])
        s = pt.calibration_summary(target, null)
        self.assertEqual(s["groups_not_fitted"], ["ncg_2", "ncg_3"])
        self.assertEqual(s["group_discoveries"], {"ncg_1": 40})
        self.assertEqual(s["groups_95th_percentile"], 40.0)
        self.assertEqual(s["targets_beyond"], [])

    def test_a_group_with_no_gene_past_seurats_filters_has_no_discovery(self):
        """Seurat's tests drop genes below min.pct and logfc.threshold first: no row there is no discovery."""
        target = self.result("seurat3_lr", ["T"], [("T", f"g{i}", 0.01) for i in range(38)])
        null = self.result("seurat3_lr", ["ncg_1", "ncg_2", "ncg_3"], [("ncg_1", f"g{i}", 0.01) for i in range(40)])
        s = pt.calibration_summary(target, null)
        self.assertEqual(s["groups_not_fitted"], [])
        self.assertEqual(s["group_discoveries"], {"ncg_1": 40, "ncg_2": 0, "ncg_3": 0})
        self.assertEqual(s["targets_beyond"], ["T"])

    def test_no_group_fitted_is_said_so(self):
        target = self.result("edger_ql", ["T"], [("T", "g", 0.01)])
        null = self.result("edger_ql", ["ncg_1"], [])
        s = pt.calibration_summary(target, null)
        self.assertIn("could fit none", s["not_run"])
        self.assertNotIn("targets_beyond", s)


class TestOnTarget(unittest.TestCase):
    def test_a_guide_that_knocks_its_gene_down(self):
        nb = Notebook(lambda d: synthetic_screen(d))
        try:
            res = pt.on_target(targets=["A"])
            by = res.table.groupby("guide")["log2fc"].median()
            self.assertLess(by["A_1"], -0.5)
            self.assertGreater(by["A_2"], by["A_1"])
        finally:
            nb.close()

    def test_the_checks_that_do_not_apply_say_so(self):
        """Both returned no rows without a word (the module inventory, docs/build/adr0031)."""
        nb = Notebook(lambda d: synthetic_screen(d))
        try:
            res = pt.on_target(targets=["A"])
            with self.assertRaises(ValueError) as e:
                pt.calibrate(res)
            self.assertIn("no target gene of its own", str(e.exception))
            with self.assertRaises(ValueError) as e:
                res.by_guide()
            self.assertIn("already per guide", str(e.exception))
        finally:
            nb.close()


class TestTheJobSide(unittest.TestCase):
    def test_a_handed_over_call_runs_on_its_cells_and_comes_back(self):
        nb = Notebook(lambda d: make_cell_screen(d, seed=3))
        try:
            h = pt.genes(targets=["EVERY"], method="edger_ql")
            with open(os.path.join(h.folder, "request.json"), encoding="utf-8") as fh:
                req = json.load(fh)
            req["choices"] = {"method": "seurat3_lr"}  # the job side runs any block; R is not needed for the test
            with open(os.path.join(h.folder, "request.json"), "w", encoding="utf-8") as fh:
                json.dump(req, fh)
            out = os.path.join(pt.CONTEXT["jobs_dir"], "job1")
            from unittest import mock
            with mock.patch.object(pt, "Screen", lambda d, design: nb.screen):
                pt.run_job(h.folder, nb.screen_dir, None, out)
            with open(os.path.join(h.folder, "run.json"), "w", encoding="utf-8") as fh:
                json.dump({"run_id": "job1"}, fh)
            self.assertEqual(h.state(), "done")
            back = h.result()
            self.assertEqual(back.table.sort_values("p").iloc[0]["gene"], "gE")
            self.assertEqual(back.call_id, req["call_id"])
        finally:
            nb.close()

    def test_a_job_reads_an_earlier_run_by_its_id_as_the_notebook_does(self):
        # development runs of 2026-11-05: pt.enrich as a job refused '<run>:changed_genes' and the run's ID, on
        # GSE203592 and GSE267740, since the job process had no notebook or jobs folder to look in
        nb = Notebook(lambda d: make_cell_screen(d, seed=3))
        saved = dict(pt.CONTEXT)
        try:
            h = pt.genes(targets=["EVERY"], method="edger_ql")
            with open(os.path.join(h.folder, "request.json"), encoding="utf-8") as fh:
                req = json.load(fh)
            req["choices"] = {"method": "seurat3_lr"}
            with open(os.path.join(h.folder, "request.json"), "w", encoding="utf-8") as fh:
                json.dump(req, fh)
            run = os.path.join(pt.CONTEXT["jobs_dir"], "run1")
            os.makedirs(run)
            pd.DataFrame({"target": ["EVERY"], "gene": ["gE"]}).to_csv(os.path.join(run, "changed_genes.tsv"),
                                                                       sep="\t", index=False)
            out = os.path.join(pt.CONTEXT["jobs_dir"], "job2")
            for k in ("notebook_dir", "jobs_dir"):  # a job's own process starts without them
                pt.CONTEXT.pop(k)
            from unittest import mock
            with mock.patch.object(pt, "Screen", lambda d, design: nb.screen):
                pt.run_job(h.folder, nb.screen_dir, None, out)
            self.assertEqual(pt.CONTEXT["jobs_dir"], saved["jobs_dir"])
            self.assertEqual(list(pt.load("run1:changed_genes")["gene"]), ["gE"])
        finally:
            pt.CONTEXT.clear()
            pt.CONTEXT.update(saved)
            nb.close()


@unittest.skipUnless(HAS_SCANPY, "needs scanpy")
class TestScanpyBlocks(unittest.TestCase):
    def setUp(self):
        self.nb = Notebook(lambda d: synthetic_screen(d))

    def tearDown(self):
        self.nb.close()

    def test_wilcoxon_finds_bs_planted_genes(self):
        res = pt.genes(targets=["B"])
        hits = set(res.table[(res.table["q"] < 0.05) & (res.table["effect"] > 0)]["gene"])
        self.assertTrue({f"b{i}" for i in range(5)} <= hits, hits)

    def test_scores_find_the_shared_shift(self):
        res = pt.scores({"shared_up": [f"s{i}" for i in range(0, 10, 2)]}, targets=["A", "B"])  # the rising half
        self.assertTrue((res.table["effect"] > 0).all(), res.table)

    def test_states_and_a_drawing(self):
        res = pt.states(targets=["A", "B"], resolution=0.5)
        self.assertEqual(set(res.table["target"]), {"A", "B"})
        self.assertEqual(len(res.extra["states"]), len(self.nb.screen.label))
        fig = res.draw()
        self.assertTrue(fig.get_axes())


@unittest.skipUnless(importlib.util.find_spec("pertpy") is not None, "needs pertpy")
class TestAnyEffectReadsTheWholeScreen(unittest.TestCase):
    def test_a_block_call_on_one_target_gives_the_modules_numbers_for_it(self):
        """pt.any_effect fitted its PCA on the named targets and the reference only (the module inventory); now on
        every cell, as C.any_effect, with each target's permutations seeded by its place among the targets."""
        from pertura.question_modules import REGISTRY
        with tempfile.TemporaryDirectory() as d:
            s, design = synthetic_screen(d)
            module = REGISTRY["C.any_effect"].fn(s, design, os.path.join(d, "m"), permutations=200)
            with pt.inside(s, os.path.join(d, "b")):
                res = pt.any_effect(targets=["B"], permutations=200)
        row = module.table.set_index("target").loc["B"]
        self.assertEqual(list(res.table["target"]), ["B"])
        self.assertAlmostEqual(float(res.table["e_distance"].iat[0]), float(row["e_distance"]), places=8)
        self.assertAlmostEqual(float(res.table["p"].iat[0]), float(row["p"]), places=8)


class TestTheServiceEntersCalls(unittest.TestCase):
    """A notebook cell calling pt: its calls entered in the record by the tool that ran it, a job block started."""

    def test_calls_are_recorded_and_a_job_is_started(self):
        from test_design_service import answer, make_deposit, proposal
        from pertura import design_gate, record as rc, screen_object
        from pertura.analysis_service import AnalysisService
        with tempfile.TemporaryDirectory() as d:
            deposit = os.path.join(d, "deposit")
            os.makedirs(deposit)
            make_deposit(deposit)
            svc = AnalysisService({"workspace": os.path.join(d, "ws")})
            try:
                for tool, args, elicit in (("open_project", {"data_folder": deposit}, None), ("measure", {}, None),
                                           ("check_design", {"proposal": proposal()}, None),
                                           ("confirm_design", {}, answer())):
                    ok, out = svc.call(tool, args, elicit=elicit)
                    self.assertTrue(ok, out)
                design = design_gate.load(svc.design.design_file)[0]
                screen_object.build(deposit, design, svc._screen_dir())
                code = ("info = pt.describe()\nprint(info['n_cells'], info['targets_with_10_cells'])\n"
                        "r = pt.on_target()\nh = pt.genes(method='edger_ql')\nprint(h)\n")
                ok, run = svc.call("run_code", {"code": code, "what": "blocks"})
                self.assertTrue(ok, run)
                self.assertEqual(run["status"], "ok", run)
                self.assertEqual(sorted(c["block"] for c in run["block_calls"]), ["genes", "on_target"])  # with IDs
                self.assertTrue(all(c["call_id"] for c in run["block_calls"]))
                self.assertEqual(len(run["block_jobs_started"]), 1)
                calls = svc.record.state()["block_calls"]
                self.assertEqual(sorted(c["block"] for c in calls.values()), ["genes", "on_target"])
                self.assertTrue(all(c["run_id"] == run["run_id"] and c["notebook"] == "main" for c in calls.values()))
                job = run["block_jobs_started"][0]
                self.assertIn(job["run_id"], svc.record.state()["runs"])
                ok, again = svc.call("run_code", {"code": "print(1)", "what": "nothing new"})
                self.assertNotIn("block_calls", again)  # each call entered once
            finally:
                from pertura import runner
                for rid in list((svc.record.state()["runs"] if svc.record else {})):
                    if "block" in rid:
                        runner.wait(rid, svc._jobs_root(), timeout=300)  # its files are released before cleanup
                svc.close()


class TestEveryMethodCalibrates(unittest.TestCase):
    """C.genes gives its calibration check with every method (attempt 3: edgeR-QL gave none)."""

    def run_genes(self, method):
        from pertura.question_modules import REGISTRY
        with tempfile.TemporaryDirectory() as d:
            s, design, _ = make_cell_screen(d, seed=3)
            return REGISTRY["C.genes"].run(s, design, os.path.join(d, "out"), method=method)

    def test_the_rank_test_calibrates(self):
        r = self.run_genes("rank_test")
        cal = r.diagnostics["calibration"]
        self.assertIsNone(cal["not_run"])
        self.assertEqual(cal["groups"], 5)
        self.assertIn("calibration_groups", r.tables)
        self.assertIn("control_groups_95th_percentile", cal)

    @unittest.skipUnless(os.path.exists(__import__("pertura.envs", fromlist=["rscript"]).rscript()), "needs R")
    def test_edger_calibrates(self):
        cal = self.run_genes("edger_ql").diagnostics["calibration"]
        self.assertIsNone(cal["not_run"])
        self.assertGreater(cal["groups"], 0)


class TestRobustnessFromABlockCall(unittest.TestCase):
    """A finding's checks take a recorded pt call's cells, reference and test (docs/adr/0025); the robustness engine's
    within-state measure (docs/adr/0024, D8) stays in its code."""


    def test_a_block_call_gives_the_findings_cells_reference_and_test(self):
        """docs/adr/0025: recording a finding from a pt call starts its checks on the call's cells, against its
        reference, with its test as the lead."""
        import json
        from unittest import mock
        from pertura.analysis_service import AnalysisError, AnalysisService
        with tempfile.TemporaryDirectory() as d:
            svc = AnalysisService({"workspace": os.path.join(d, "ws")})
            try:
                calls = {"genes-1": {"call_id": "genes-1", "block": "genes", "cells": "strict", "targets": ["T1"],
                                     "reference": "control_guides", "kept_cells": True,
                                     "choices": {"method": "wilcoxon"}},
                         "genes-2": {"call_id": "genes-2", "block": "genes", "cells": "a selection of 5 cells "
                                     "(not kept)", "targets": ["T1"], "reference": "control_guides",
                                     "kept_cells": False, "choices": {}}}
                state = {"block_calls": calls, "findings": {}, "cell_sets": {"strict": {"calls": False}}, "runs": {}}
                svc.record = mock.Mock(folder=d)
                svc.record.state.return_value = state
                svc.record.enabled.return_value = True
                svc.record.start_run.return_value = "checks-job"
                svc.project = d
                with mock.patch.object(svc, "_screen_dir", return_value=d), \
                        mock.patch.object(svc, "_design_path", return_value="design.yaml"), \
                        mock.patch.object(svc, "_jobs_root", return_value=d):
                    out = svc.record_finding("f1", "C.genes", "T1", "G1", "down", "all", [], "gene",
                                             block_call="genes-1")
                    self.assertEqual(out["checks_run"], "checks-job")
                    kind, event, _ = svc.record.append_many.call_args_list[0][0][0][0]  # written with its replies
                    self.assertEqual((kind, event["cells"], event["reference"]),
                                     ("finding_recorded", "strict", "control_guides"))
                    cmd = svc.record.start_run.call_args[0][1]
                    with open(cmd[cmd.index("--spec") + 1], encoding="utf-8") as fh:
                        spec = json.load(fh)
                    self.assertEqual((spec["cells"], spec["lead"]["method"], spec["direction"]), ("strict", "wilcoxon", -1))
                    with self.assertRaises(AnalysisError) as e:
                        svc.record_finding("f2", "C.genes", "T1", "G1", "down", "all", [], "gene", block_call="genes-2")
                    self.assertIn("keep those cells first", str(e.exception))
                    with self.assertRaises(AnalysisError):
                        svc.record_finding("f3", "C.genes", "T2", "G1", "down", "all", [], "gene", block_call="genes-1")
                    with self.assertRaises(AnalysisError):
                        svc.record_finding("f4", "C.genes", "T1", "G1", "down", "all", [], "gene", block_call="nope")
                svc.record = None
            finally:
                svc.close()

    def test_a_states_call_keeps_its_per_cell_states(self):
        nb = Notebook(lambda d: make_cell_screen(d, seed=3))
        try:
            from unittest import mock
            fake = (pd.DataFrame({"target": ["EVERY"]}), [], {"states": pd.Series(
                np.where(nb.screen.unit == "m1", "S1", "S2"), index=np.asarray(nb.screen.adata.obs_names))})
            with mock.patch.object(pt.BLOCKS["states"], "fn", lambda *a, **k: fake):
                pt.states(targets=["EVERY"])
            entry = nb.calls()[-1]
            path = entry["outputs"]["states"]
            back = pd.read_csv(path, sep="\t").set_index("barcode")
            self.assertEqual(len(back), len(nb.screen.label))
        finally:
            nb.close()


class TestEachBlockRegistersItsOwnFunction(unittest.TestCase):
    def test_a_blocks_function_is_its_own_and_takes_the_screen_the_targets_and_the_reference(self):
        # the session 3 fix batch's gate: a helper written under @block("enrich") was registered as the block
        import inspect
        for name, b in pt.BLOCKS.items():
            self.assertEqual(b.fn.__name__, f"_{name}")
            self.assertEqual(list(inspect.signature(b.fn).parameters)[:3], ["s", "targets", "reference"], name)


if __name__ == "__main__":
    unittest.main()
