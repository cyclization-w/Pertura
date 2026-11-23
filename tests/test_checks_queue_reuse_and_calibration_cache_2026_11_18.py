"""The checks after the first run of one simple loop (docs/build/what_the_first_run_of_one_simple_loop_found_2026-11-17.md,
problems 11 and 12): a queued spec waits as a file and starts when a slot is free; a repeated claim takes the checks of
the claim it repeats; a job takes its slot before it loads the screen object; a gene calibration is computed once for
a screen, test and seed, and read back."""
import json
import os
import tempfile
import types
import unittest
from unittest import mock

import numpy as np
import pandas as pd

import test_analysis_service as service_tests

from pertura import finding_checks, record as rc


class TestTheQueue(unittest.TestCase):
    """A spec waits as a file while checks_at_once jobs run; a repeated claim takes the earlier claim's checks."""

    session = service_tests.TestRobustnessSwitch.session
    finding = service_tests.TestRobustnessSwitch.finding

    def started_by_hand(self, svc):
        """start_run as a runner job would log it, without a process: the run stays running until finished here."""
        started = []

        def start(what, cmd, inputs, root, **k):
            rid = f"job{len(started) + 1}"
            started.append(cmd)
            svc.record.append("run_logged", {"run_id": rid, "job_id": None, "what": what, "code": " ".join(cmd),
                                             "inputs": inputs, "question_id": k.get("question_id")}, rc.TOOL)
            return rid
        return started, start

    def finish(self, svc, run_id, finding_id, outcome="agrees"):
        svc.record.append("check_recorded", {"finding_id": finding_id, "check": "lead", "outcome": outcome,
                                             "reason": "its test", "run_id": run_id}, rc.TOOL)
        svc.record.append("run_finished", {"run_id": run_id, "state": "done", "output": ""}, rc.TOOL)

    def test_a_spec_waits_as_a_file_and_starts_when_a_slot_is_free(self):
        with tempfile.TemporaryDirectory() as d:
            svc = self.session(os.path.join(d, "s"), {})
            started, start = self.started_by_hand(svc)
            resources = svc._resources
            with mock.patch.object(svc.record, "start_run", side_effect=start), \
                    mock.patch.object(svc, "_resources", lambda: {**resources(), "checks_at_once": 1}):
                ok, out = self.finding(svc)
                self.assertTrue(ok, out)
                self.assertEqual(out["checks_run"], "job1")
                ok, out = self.finding(svc, finding_id="f2", target="TB")
                self.assertTrue(ok, out)
                self.assertTrue(out["checks"].startswith("queued behind 0 others"), out)
                self.assertEqual(len(started), 1)  # no process for the waiting spec
                self.finish(svc, "job1", "f1")
                svc.call("read_record", {})  # any call starts what is queued, once a slot is free
                self.assertEqual(len(started), 2)
                spec = started[1][started[1].index("--spec") + 1]
                self.assertIn("f2-", os.path.basename(spec))
                svc.call("read_record", {})
                self.assertEqual(len(started), 2)  # started once
            svc.close()

    def test_a_repeated_claim_takes_the_checks_of_the_claim_it_repeats(self):
        with tempfile.TemporaryDirectory() as d:
            svc = self.session(os.path.join(d, "s"), {})
            started, start = self.started_by_hand(svc)
            with mock.patch.object(svc.record, "start_run", side_effect=start):
                self.finding(svc)
                ok, out = self.finding(svc, finding_id="f2")  # the same claim while f1's checks run: it waits
                self.assertTrue(ok, out)
                self.assertTrue(out["checks"].startswith("queued behind"), out)
                self.assertEqual(len(started), 1)
                self.finish(svc, "job1", "f1", outcome="other direction")
                svc.call("read_record", {})
                self.assertEqual(len(started), 1)  # taken, not run again
                f2 = svc.record.state()["findings"]["f2"]
                self.assertEqual(f2["checks"]["lead"]["outcome"], "other direction")
                self.assertTrue(f2["checks"]["lead"]["reason"].startswith("the same claim as f1"))
                ok, out = self.finding(svc, finding_id="f3")  # the same claim once f1's checks ended
                self.assertIn("the same claim as f1", out["checks"])
                ok, out = self.finding(svc, finding_id="f4", direction="up")  # another claim runs its own
                self.assertEqual(out["checks_run"], "job2")
            svc.close()


class TestTheJob(unittest.TestCase):

    def test_a_checks_job_takes_its_slot_before_it_loads_the_screen_object(self):
        order = []

        class Slots:
            def __init__(self, *a, **k):
                pass

            def __enter__(self):
                order.append("slot")
                return self

            def __exit__(self, *exc):
                order.append("free")
                return False

        with tempfile.TemporaryDirectory() as d:
            spec = os.path.join(d, "f1-1.json")
            with open(spec, "w", encoding="utf-8") as fh:
                json.dump({"target": "TA", "kind": "gene", "feature": "G1", "direction": -1, "cells": None,
                           "_queue": {"finding_id": "f1"}}, fh)
            seen = {}

            def run(record, screen, sp, *a, **k):
                seen["spec"] = sp
                return {"summary": "done"}

            with mock.patch.object(finding_checks, "_slots", Slots), \
                    mock.patch("pertura.question_modules.data.Screen",
                               side_effect=lambda *a, **k: order.append("load") or mock.Mock()), \
                    mock.patch("pertura.screen_object.load_design", return_value={}), \
                    mock.patch("pertura.record.Record"), mock.patch.object(finding_checks, "run", run):
                finding_checks.main(["run", "--record", os.path.join(d, "record"), "--finding", "f1", "--spec", spec,
                                     "--screen", d, "--design", os.path.join(d, "design.yaml"), "--out", d])
        self.assertEqual(order, ["slot", "load", "free"])
        self.assertNotIn("_queue", seen["spec"])
        self.assertEqual(seen["spec"]["_kept"]["folder"], os.path.join(d, "checks", "kept_measurements"))


class TestTheCalibrationKept(unittest.TestCase):

    def test_a_gene_calibration_is_computed_once_for_a_screen_test_and_seed(self):
        from pertura.question_modules import c
        screen = types.SimpleNamespace(adata=types.SimpleNamespace(obs_names=np.array(["c1", "c2", "c3"])),
                                       label=np.array(["control", "control", "TA"]),
                                       top_guide=np.array(["n1", "n2", "TA.g1"]))
        table = pd.DataFrame({"target": ["group 1", "group 2", "group 2"], "gene": ["G1", "G2", "G1"],
                              "q": [0.01, 0.01, 0.9], "p": [0.001, 0.001, 0.5]})
        computed = []

        def calibrate(s, run, reference, level, seed=0, method=None):
            computed.append(method)
            t, _ = run(s, ["group 1", "group 2"])
            return ({"groups": 2, "groups_not_fitted": 0, "discoveries": 2, "not_run": None},
                    {"calibration_groups": pd.DataFrame({"group": ["group 1", "group 2"], "discoveries": [1, 1]})})

        with tempfile.TemporaryDirectory() as d, mock.patch.object(c, "calibrate_method", calibrate), \
                mock.patch.object(c, "genes_by_leads", return_value=(table, [])):
            spec = {"feature": "G1", "_kept": {"folder": os.path.join(d, "kept"), "base": "b1"}}
            first = finding_checks._calibrate_one(screen, spec, "wilcoxon", d, 0)
            again = finding_checks._calibrate_one(screen, {**spec, "feature": "G2"}, "wilcoxon", d, 0)
            self.assertEqual(computed, ["wilcoxon"])  # read back for another gene's claim
            self.assertEqual((first["groups_calling_the_gene"], again["groups_calling_the_gene"]), (1, 1))
            self.assertEqual(first["groups_95th_percentile"], again["groups_95th_percentile"])
            finding_checks._calibrate_one(screen, spec, "edger_ql", d, 0)
            self.assertEqual(computed, ["wilcoxon", "edger_ql"])  # another test is its own
            other = types.SimpleNamespace(**{**vars(screen), "label": np.array(["control", "TA", "TA"])})
            finding_checks._calibrate_one(other, spec, "wilcoxon", d, 0)
            self.assertEqual(len(computed), 3)  # other cells' labels are another calibration
            finding_checks._calibrate_one(screen, {"feature": "G1"}, "wilcoxon", d, 0)
            self.assertEqual(len(computed), 4)  # no cache folder given: computed, as before

    def test_a_targets_genes_through_the_lead_are_computed_once_for_its_gene_claims(self):
        """On GSE342764, 71 gene claims made 107 genome-wide lead fits, of 11 distinct targets, cells and tests."""
        from pertura.question_modules import c
        screen = types.SimpleNamespace(adata=types.SimpleNamespace(obs_names=np.array(["c1", "c2", "c3"])),
                                       label=np.array(["control", "control", "TA"]),
                                       top_guide=np.array(["n1", "n2", "TA.g1"]))
        table = pd.DataFrame({"target": ["TA", "TA"], "gene": ["G1", "G2"], "effect": [-1.0, 0.5],
                              "p": [0.001, 0.2], "q": [0.01, 0.4]})
        every = np.ones(3, bool)
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(c, "genes_by_leads", return_value=(table, [])) as fitted, \
                mock.patch.object(finding_checks.fit, "sides", return_value=(every, every)), \
                mock.patch.object(finding_checks.fit, "MIN_CELLS", 1), \
                mock.patch.object(finding_checks, "_units", return_value={}):
            kept = {"folder": d, "base": "b1"}
            one = finding_checks.Measure({"kind": "gene", "feature": "G1", "_kept": kept}, d)(screen, ["TA"], "wilcoxon")
            two = finding_checks.Measure({"kind": "gene", "feature": "G2", "_kept": kept}, d)(screen, ["TA"], "wilcoxon")
            self.assertEqual(fitted.call_count, 1)  # the second claim reads the first's
            self.assertEqual((one["TA"]["q"], two["TA"]["q"], one["TA"]["genes_changed"]), (0.01, 0.4, 1))
            finding_checks.Measure({"kind": "gene", "feature": "G1", "_kept": kept}, d)(screen, ["TA"], "wilcoxon",
                                                                                         alone=True)
            self.assertEqual(fitted.call_count, 2)  # a gene tested alone is never kept


class TestARepeatsDoubt(unittest.TestCase):
    session = service_tests.TestRobustnessSwitch.session

    def test_a_repeat_of_a_claim_its_checks_fail_opens_no_second_doubt(self):
        with tempfile.TemporaryDirectory() as d:
            svc = self.session(os.path.join(d, "s"), {})
            svc.record.append("question_planned", {"question_id": "C.genes", "letter": "C", "item": "genes",
                                                   "because": "always"}, rc.PLAN)
            for fid in ("f1", "f2"):
                svc.record.append("finding_recorded", {"finding_id": fid, "question_id": "C.genes", "target": "TA",
                                                       "feature": "G1", "direction": "down", "kind": "gene",
                                                       "run_ids": []}, rc.AGENT)
            svc.record.append("check_recorded", {"finding_id": "f1", "check": "guides_agree", "outcome": "disagree",
                                                 "reason": "one guide of two"}, rc.TOOL)
            svc.record.append("check_recorded", {"finding_id": "f2", "check": "guides_agree", "outcome": "disagree",
                                                 "reason": "the same claim as f1: one guide of two"}, rc.TOOL)
            svc._doubts_from_checks()
            opened = {d["finding_id"] for d in svc.record.state()["doubts"].values()}
            self.assertEqual(opened, {"f1"})
            svc.close()


if __name__ == "__main__":
    unittest.main()
