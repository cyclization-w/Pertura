"""Tests for the design server (pertura.design_service), its proposal checks (pertura.design_audit.checks) and its
confirmation through MCP elicitation, on the synthetic deposit of test_design_audit: two samples, 4 targets x 2
guides, 6 non-targeting guides, 3 hashtags, 5 surface proteins, two donors of different sex pooled in both samples,
and guides that knock their targets down (CRISPRi-like)."""
import io
import json
import os
import tempfile
import unittest

from pertura import design_gate
from pertura.design_service import BY, DesignService, make_design_config
from pertura.mcp_server import serve

from test_design_audit import TARGETS, make_deposit

NTC = [f"NTC_{k}" for k in range(1, 7)]
MAP = {t: [f"{t}_1", f"{t}_2"] for t in TARGETS}


def proposal(**change):
    """A design proposal as the design auditor would write it for the synthetic deposit."""
    p = {"schema": "pertura.design/v1",
         "experiment": {"conditions": {"value": [], "status": "reported", "source": "GEO: one condition"}},
         "perturbation": {
             "modality": {"value": "CRISPRi", "status": "inferred", "source": "on-target expression",
                          "note": "targets' own expression drops in their cells"},
             "controls": {"value": [{"name": "NTC", "kind": "non_targeting", "guides": list(NTC)}],
                          "status": "proposed", "source": "guide names, read by the auditor"},
             "target_map": {"value": {t: list(g) for t, g in MAP.items()}, "status": "proposed",
                            "source": "guide names, read by the auditor"}},
         "units": {"independent_unit": {"value": "donor", "status": "proposed", "source": "GEO sample titles"},
                   "n_units": {"value": 2, "status": "observed", "source": "files"}},
         "unresolved": []}
    for key, value in change.items():
        section, name = key.split("__")
        p[section][name] = value
    return p


def answer(**override):
    """A form answer: every asked key confirmed unless overridden (e.g. perturbation_modality='reject')."""
    def elicit(message, schema):
        elicit.calls.append((message, schema))
        content = {f: override.get(f, "confirm") for f in schema["properties"] if f != "note"}
        content["note"] = override.get("note", "")
        return override.get("action", "accept"), content
    elicit.calls = []
    return elicit


def confirm_the_cells(svc, rule=None):
    """After the build: the cells' quality rule (data.qc) added to the design last checked, checked and confirmed on
    the form, as the agent and the user do from the build's cells overview; modules wait for it."""
    last = svc.design._read("checks.jsonl")[-1]["proposal_sha256"]
    p = svc.design._proposal(last)
    p.setdefault("data", {})["qc"] = {"value": rule or {"filters": []}, "status": "proposed",
                                      "source": "the cells overview"}
    ok, out = svc.call("check_design", {"proposal": p})
    assert ok and out["ok"], out
    ok, out = svc.call("confirm_design", {}, elicit=answer())
    assert ok, out
    return out


class TestReplaceFile(unittest.TestCase):
    def test_a_replace_refused_while_a_reader_holds_the_file_is_tried_again(self):
        """Windows refused os.replace of design.yaml.tmp while a notebook kernel had design.yaml open."""
        from unittest import mock
        from pertura import design_service
        real, calls = os.replace, []

        def refuse_twice(src, dst):
            calls.append(src)
            if len(calls) <= 2:
                raise PermissionError(5, "Access is denied")
            return real(src, dst)
        with tempfile.TemporaryDirectory() as d:
            tmp, path = os.path.join(d, "design.yaml.tmp"), os.path.join(d, "design.yaml")
            open(tmp, "w").write("a: 1\n")
            with mock.patch.object(design_service.os, "replace", refuse_twice), \
                    mock.patch.object(design_service.time, "sleep"):
                design_service.replace_file(tmp, path)
            self.assertEqual((len(calls), open(path).read()), (3, "a: 1\n"))
            open(tmp, "w").write("b: 2\n")
            with mock.patch.object(design_service.os, "replace", side_effect=PermissionError(5, "denied")), \
                    mock.patch.object(design_service.time, "sleep"):
                self.assertRaises(PermissionError, design_service.replace_file, tmp, path)


class TestDesignService(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.deposit = os.path.join(cls.tmp.name, "deposit")
        os.makedirs(cls.deposit)
        make_deposit(cls.deposit)
        cls.measured_dir = os.path.join(cls.tmp.name, "measured")
        svc = DesignService(make_design_config(cls.deposit, cls.measured_dir))
        cls.measurement = svc.measure()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        # a fresh state folder per test, sharing the one measurement
        self.state = tempfile.mkdtemp(dir=self.tmp.name)
        with open(os.path.join(self.measured_dir, "measurements.json")) as fh:
            m = fh.read()
        self.svc = DesignService(make_design_config(self.deposit, self.state))
        with open(os.path.join(self.state, "measurements.json"), "w") as fh:
            fh.write(m)
        import shutil
        shutil.copy(os.path.join(self.measured_dir, "arrays.npz"), os.path.join(self.state, "arrays.npz"))
        shutil.copytree(os.path.join(self.measured_dir, "cell_tables"), os.path.join(self.state, "cell_tables"))

    def ok(self, tool, elicit=None, **args):
        ok, out = self.svc.call(tool, args, elicit=elicit)
        self.assertTrue(ok, out)
        return out

    def test_describe_deposit(self):
        import shutil
        shutil.rmtree(os.path.join(self.state, "cell_tables"))  # the deposit as it came, before its reading
        d = self.ok("describe_deposit")
        self.assertEqual(d["layout"], "per_sample_mtx")
        self.assertEqual(sorted(s["name"] for s in d["samples"]), ["S1", "S2"])
        for s in d["samples"]:
            self.assertEqual(s["feature_types"]["CRISPR Guide Capture"], 14)
            self.assertIsNotNone(s["calls"])
            # the names of the features that are neither genes nor guides, as they are, for the agent to read
            self.assertEqual(s["other_features"], {"Custom": ["HT-1", "HT-2", "HT-3"],
                                                   "Antibody Capture": ["CD3", "CD4", "CD8", "CD45", "CD69"]})
        self.assertEqual((d["cellranger_configs"], d["tables"]), ([], []))
        self.assertFalse(self.svc.call("read_metadata", {"item": "config"})[0])  # no configuration to read

    def test_read_metadata_gives_text_to_read(self):
        g = self.ok("read_metadata", item="guides")
        self.assertEqual(g["guides_total"], 14)
        first = {x["name"]: x for x in g["guides"]}
        self.assertEqual(first["NTC_1"]["reference_target"], "Non-Targeting")  # the label, not an interpretation
        self.assertGreater(first["TA_1"]["cells"], 0)
        f = self.ok("read_metadata", item="file", path="GSE0_feature_reference.csv.gz")
        self.assertTrue(f["lines"][0].startswith("id,name"))
        self.assertIsNone(f["next_offset"])
        for bad in ("../outside.csv", "GSM001_S1_matrix.mtx.gz"):
            self.assertFalse(self.svc.call("read_metadata", {"item": "file", "path": bad})[0])
        ok, out = self.svc.call("read_metadata", {"item": "geo"})
        self.assertFalse(ok)
        self.assertIn("no GEO metadata", out["error"])

    def test_read_metadata_shows_an_objects_cell_metadata(self):
        import anndata as ad
        import numpy as np
        import pandas as pd
        dep = tempfile.mkdtemp(dir=self.tmp.name)
        obs = pd.DataFrame({"sgRNA": ["A_g1"] * 5 + ["WT"] * 3 + ["A_g1,B_g2"] * 2, "individual": list("pqpqpqpqpq"),
                            "n_genes": np.arange(10)}, index=[f"c{i}" for i in range(10)])
        ad.AnnData(np.zeros((10, 3), dtype=np.float32), obs=obs).write_h5ad(os.path.join(dep, "screen.h5ad"))
        svc = DesignService(make_design_config(dep, tempfile.mkdtemp(dir=self.tmp.name)))
        ok, out = svc.call("read_metadata", {"item": "object"})
        self.assertTrue(ok, out)
        cols = {c["column"]: c for c in out["columns"]}
        self.assertEqual(out["cells"], 10)
        self.assertEqual(cols["sgRNA"]["top_values"], {"A_g1": 5, "WT": 3, "A_g1,B_g2": 2})  # shown, not interpreted
        self.assertEqual((cols["n_genes"]["min"], cols["n_genes"]["max"]), (0.0, 9.0))
        self.assertFalse(svc.call("read_metadata", {"item": "object", "path": "../screen.h5ad"})[0])
        self.assertFalse(self.svc.call("read_metadata", {"item": "object"})[0])  # the matrix deposit has no object

    def test_measure_keeps_the_library(self):
        m = self.measurement
        self.assertEqual(m["library"]["guides"], 14)
        self.assertEqual(sorted(m["library"]["samples"]), ["S1", "S2"])
        self.assertTrue(any(f["key"] == "units.pooled_donors" for f in m["facts"]))
        self.assertEqual(self.ok("measure")["measured"], m["measured"])  # stored, not measured again

    def test_a_background_measurement_answers_once_done(self):
        import time
        cfg = make_design_config(self.deposit, tempfile.mkdtemp(dir=self.tmp.name), background=True)
        svc = DesignService({**cfg, "measure_wait": 0})  # the call does not wait: it says the measurement runs
        ok, out = svc.call("measure", {})
        self.assertTrue(ok, out)
        t0 = time.time()
        while "facts" not in out and time.time() - t0 < 120:
            self.assertEqual(out["state"], "running")
            time.sleep(0.5)
            out = svc.call("measure", {})[1]
        self.assertEqual({x["key"] for x in out["facts"]}, {x["key"] for x in self.measurement["facts"]})
        self.assertEqual(svc.call("measure", {})[1]["measured"], out["measured"])  # stored, not measured again

    def test_a_value_in_another_shape_is_refused_with_its_shape(self):
        wrong = [{"name": "NTC", "kind": "scrambled", "guides": NTC}]
        r = self.ok("check_design", design={"perturbation.controls": {"value": wrong, "source": "guide names"},
                                            "perturbation.modality": {"value": "knockdown", "source": "GEO"}})
        self.assertFalse(r["ok"])
        text = " ".join(r["problems"])
        for expected in ("perturbation.controls: at 0.kind", "kind one of non_targeting", "perturbation.modality",
                         "knockout, crispri, crispra"):
            self.assertIn(expected, text)
        self.assertNotIn("proposal_sha256", r)  # nothing stored
        self.assertFalse(self.svc.call("confirm_design", {}, elicit=answer())[0])

    def test_check_blocks_what_the_data_do_not_hold(self):
        extra = [{"name": "NTC", "kind": "non_targeting", "guides": NTC + ["NTC_9"]}]
        bad = proposal(perturbation__controls={"value": extra, "status": "proposed", "source": "guide names"},
                       units__unit_column={"value": "donor_id", "status": "proposed", "source": "x"})
        r = self.ok("check_design", proposal=bad)
        self.assertFalse(r["ok"])
        text = " ".join(r["problems"])
        for expected in ("1 of 7 guides are not in the deposit's library", "no column 'donor_id' in the cell tables",
                         "leave units.unit_column out"):
            self.assertIn(expected, text)
        self.assertFalse(self.svc.call("confirm_design", {}, elicit=answer())[0])  # problems block confirmation

    def test_missing_required_keys_block(self):
        p = proposal()
        del p["experiment"]["conditions"], p["perturbation"]["modality"]
        problems = " ".join(self.ok("check_design", proposal=p)["problems"])
        self.assertIn("perturbation.modality: missing", problems)
        self.assertIn("experiment.conditions: missing", problems)

    def test_flags_go_to_the_form(self):
        r = self.ok("check_design", proposal=proposal())
        self.assertTrue(r["ok"], r["problems"])
        by_key = {}
        for f in r["flags"]:
            by_key.setdefault(f["key"], []).append(f["text"])
        self.assertTrue(any("both sexes" in t for t in by_key["units.independent_unit"]))
        self.assertTrue(any("pseudobulk can test 0%" in t for t in by_key["units.independent_unit"]))
        # the targets' own expression stands beside the proposed modality as numbers, without a verdict word
        self.assertTrue(any("median log2 fold change" in t for t in by_key["perturbation.modality"]))
        ko = self.ok("check_design", proposal=proposal(perturbation__modality={
            "value": "KO", "status": "proposed", "source": "the user said so"}))
        said = [f["text"] for f in ko["flags"] if f["key"] == "perturbation.modality"]
        self.assertTrue(said and "down by more than 0.5 in 100%" in said[0] and "suggests" not in said[0], said)
        elicit = answer()
        self.ok("confirm_design", elicit=elicit)
        message, schema = elicit.calls[0]
        # one question for the whole design, with the user's note (docs/build/design_confirmation_rebuilt_plan)
        self.assertEqual(set(schema["properties"]), {"answer", "note"})
        self.assertEqual(schema["properties"]["answer"]["enum"], ["confirm", "change"])
        self.assertEqual(schema["properties"]["answer"]["enumNames"], ["Right", "Change (say what in the note)"])
        # each key a line: its value in plain words, where it comes from, what was measured beside it
        self.assertIn("- Biological replicates: each donor; each sample one unit (from: GEO sample titles)", message)
        self.assertRegex(message, r"Biological replicates: [^\n]*Measured: [^\n]*both sexes")
        self.assertIn("- Controls: NTC: 6 non-targeting guides", message)
        self.assertIn("- Targets: 4 targets", message)
        page = {x["item"]: x for x in r["proposal"]}
        self.assertTrue(any("both sexes" in t for t in page["Biological replicates"]["measured"]))
        self.assertEqual(page["Biological replicates"]["decides"],
                         "what counts as an independent sample in the statistics")
        self.assertNotIn("cell_table_column", message)  # plain words: no internal names on the form

    def test_a_key_outside_the_design_or_without_its_source_is_told_what_it_takes(self):
        out = self.ok("check_design", design={"units.independent_unit.value": {"value": "mouse", "source": "x"},
                                              "units.n_units": {"value": 2, "source": "files"},
                                              "perturbation.modality": {"value": "crispri"}})
        self.assertFalse(out["ok"])
        text = " ".join(out["problems"])
        self.assertIn("units.independent_unit.value: not a key of the design; the keys are units.independent_unit", text)
        self.assertIn("units.n_units: not a key of the design", text)
        self.assertIn("perturbation.modality: give its source", text)
        self.assertIn("design: give {dotted key", " ".join(self.ok("check_design")["problems"]))

    def test_a_target_of_promoter_and_enhancer_guides_is_flagged(self):
        # development session 3, problem 5: each gene's enhancer guides were pooled with its promoter guides
        guides = {"value": {f"{x}_{k}": ({"gene": x, "element": "enhancer"} if (x, k) == ("TA", 2) else {"gene": x})
                            for x in MAP for k in (1, 2)}, "status": "proposed", "source": "guide names"}
        out = self.ok("check_design", proposal=proposal(perturbation__guides=guides))
        flag = [f["text"] for f in out["flags"] if f["key"] == "perturbation.target_map"]
        self.assertTrue(flag and "different elements" in flag[0] and "TA" in flag[0], out["flags"])

    def test_the_on_target_measure_reads_the_guides_genes(self):
        # development session 3, problem 7: targets named by mode were "not measurable (no target gene detected)"
        from pertura.design_audit import detectors as dt
        summary = {"guides": ["a1", "a2", "c1"], "genes": ["GENE1", "OTHER"], "cells": [20, 20, 40],
                   "sum": __import__("numpy").array([[1.0, 5.0], [1.0, 5.0], [40.0, 5.0]]),
                   "detected": __import__("numpy").array([[5, 20], [5, 20], [40, 40]]), "basis": "one guide"}
        target_of = {"a1": "GENE1-CRISPRi", "a2": "GENE1-CRISPRi", "c1": "control"}
        value, not_genes, _ = dt.on_target(summary, target_of, ["GENE1", "OTHER"])
        self.assertIn("no guide's gene is given", value)
        value, _, _ = dt.on_target(summary, target_of, ["GENE1", "OTHER"], {"a1": "GENE1", "a2": "GENE1"})
        self.assertIsInstance(value, dict)
        self.assertEqual(value["targets_measured"], 1)
        self.assertLess(value["median_log2fc"], -1)

    def test_the_modality_question_names_mixed_modes(self):
        questions = [q for q in self.measurement["questions"] if q["key"] == "perturbation.modality"]
        self.assertTrue(questions and "mixed" in questions[0]["question"], questions)

    def test_the_guide_calling_rule_is_checked_and_shown(self):
        text = {"value": "Cell Ranger 9.0.0 protospacer_calls_per_cell.csv per library", "status": "observed",
                "source": "call files"}
        r = self.ok("check_design", proposal=proposal(perturbation__assignment=text))
        self.assertFalse(r["ok"])
        self.assertIn("rule one of auto, deposit", " ".join(r["problems"]))
        self.assertFalse(self.svc.call("confirm_design", {}, elicit=answer())[0])  # blocked, not an internal error
        no_column = {"value": {"rule": "labels"}, "status": "proposed", "source": "the authors' labels"}
        self.assertIn("needs the column", " ".join(self.ok("check_design", proposal=proposal(
            perturbation__assignment=no_column))["problems"]))
        deposit = {"value": {"rule": "deposit"}, "status": "observed", "source": "call files"}
        out = self.ok("check_design", proposal=proposal(perturbation__assignment=deposit))
        self.assertTrue(out["ok"], out["problems"])
        elicit = answer()
        self.ok("confirm_design", elicit=elicit)
        self.assertRegex(elicit.calls[0][0], r"(?i)- Guide calls: [^\n]*Measured: of [\d,]+ cells")
        # the deposit's own calls are shown with what they leave, counted before any build
        said = " ".join(f["text"] for f in out["flags"] if f["key"] == "perturbation.assignment")
        self.assertRegex(said, r"(?i)of [\d,]+ cells, the cells with one target each rule leaves: the deposit's own calls "
                               r"\(proposed\) [\d,]+ \([^)]*weak calls[^)]*\); dominance \(3 UMIs, 3 times the next "
                               r"target\) [\d,]+")
        from pertura.design_service import summary
        self.assertIn("the rule", summary(proposal(perturbation__assignment=text), "perturbation.assignment"))

    def test_a_change_to_named_keys_is_checked_on_the_last_proposal_with_what_its_rule_leaves(self):
        """Proposing one item cost the whole design: an agent sent it five times and read Pertura's source for the
        formats; and a guide-calling rule's cost showed only after a build."""
        self.ok("check_design", proposal=proposal())
        rule = {"rule": "dominance", "min_umis": 3, "ratio": 3, "source": "matrix"}
        out = self.ok("check_design", changes={"perturbation.assignment": rule}, why="the paper's dominant-guide rule")
        self.assertTrue(out["ok"], out["problems"])
        p = self.svc._proposal(out["proposal_sha256"])
        self.assertEqual(p["perturbation"]["assignment"],
                         {"value": rule, "status": "proposed", "source": "the paper's dominant-guide rule"})
        self.assertEqual(p["perturbation"]["modality"]["value"], "crispri")  # the rest is the last proposal's
        rows = next(f["rules"] for f in out["flags"] if f.get("rules"))
        self.assertEqual(rows[0]["rule"], "dominance (3 UMIs, 3 times the next target) (proposed)")
        self.assertEqual(len(rows[0]["one_per_unit"]), 2)  # the fewest and most over the 2 units
        self.assertEqual([r["rule"] for r in rows if "one" in r][1], "the deposit's own calls")
        self.assertEqual(sum(r["rule"].startswith("dominance") for r in rows), 1)  # the same rule counted once
        self.assertIn("counted by the build", next(r for r in rows if r["rule"].startswith("Cell Ranger"))["not_counted"])
        for bad, words in (({"units.independent_unit.value": {"value": "mouse", "source": "x"}}, "not a key"),
                           ({"units": {"value": {}, "source": "x"}}, "not a key"), ({}, "design: give")):
            out = self.ok("check_design", design=bad)
            self.assertFalse(out["ok"])
            self.assertIn(words, " ".join(out["problems"]))

    def test_a_design_given_as_text_is_told_what_to_give(self):
        """A proposal's sha256 and the design file's path, given as the design, ended in an internal error."""
        for given in ("/home/user/workspace/design/design.yaml", "f" * 64):
            out = self.ok("check_design", design=given)
            self.assertFalse(out["ok"])
            self.assertIn("design: give {dotted key: {value, source, note}}", " ".join(out["problems"]))
        sha = self.ok("check_design", proposal=proposal())["proposal_sha256"]
        self.assertEqual(self.ok("check_design", design={"units.pooled_from": None})["proposal_sha256"], sha)

    def test_the_target_map_is_checked(self):
        def problems(target_map, controls=None):
            change = {"perturbation__target_map": target_map}
            if controls is not None:
                change["perturbation__controls"] = {"value": controls, "status": "proposed", "source": "names"}
            return self.ok("check_design", proposal=proposal(**change))
        no_map = proposal()
        del no_map["perturbation"]["target_map"]
        r = self.ok("check_design", proposal=no_map)
        self.assertIn("perturbation.target_map: missing", " ".join(r["problems"]))
        bad = {"value": {"TA": ["TA_1", "TA_2", "TB_1"], "TB": ["TB_1", "TB_2"], "TC": ["TC_1", "TC_9"],
                         "TD": ["TD_1", "NTC_1"]}, "status": "proposed", "source": "names"}
        text = " ".join(problems(bad)["problems"])
        for expected in ("1 guides are under two targets", "1 of 8 guides are not in the deposit's library",
                         "1 guides are under a target and a control"):
            self.assertIn(expected, text)
        partial = {"value": {"TA": ["TA_1", "TA_2"]}, "status": "proposed", "source": "names"}
        r = problems(partial)
        self.assertTrue(r["ok"], r["problems"])
        self.assertTrue(any("6 of 14 guides are in no target and no control" in f["text"] for f in r["flags"]))
        ref = problems({"value": "feature_reference", "status": "reported", "source": "feature reference"})
        self.assertTrue(ref["ok"], ref["problems"])
        self.assertFalse(any("no target and no control" in f["text"] for f in ref["flags"]))

    def test_measurements_use_the_agents_map(self):
        # without a feature reference the code makes no map; the agent's map is measured by check_design
        dep = tempfile.mkdtemp(dir=self.tmp.name)
        make_deposit(dep, reference=False)
        svc = DesignService(make_design_config(dep, tempfile.mkdtemp(dir=self.tmp.name)))
        m = svc.call("measure", {})[1]
        facts = {f["key"]: f["value"] for f in m["facts"]}
        self.assertNotIn("perturbation.targets", facts)
        self.assertIn("needs the target map", facts["perturbation.on_target"])
        self.assertTrue(any(q["key"] == "perturbation.target_map" for q in m["questions"]))
        ok, ko = svc.call("check_design", {"proposal": proposal(perturbation__modality={
            "value": "KO", "status": "proposed", "source": "the user said so"})})
        self.assertTrue(ko["ok"], ko["problems"])
        texts = [f["text"] for f in ko["flags"]]
        self.assertTrue(any("(with the proposed target map and controls): median log2 fold change -" in t
                            for t in texts), texts)
        self.assertTrue(any("pseudobulk can test 0%" in t for t in texts), texts)  # two samples: under 3 units
        swapped = {"value": {"TA": ["TB_1", "TB_2"], "TB": ["TA_1", "TA_2"], "TC": ["TD_1", "TD_2"],
                             "TD": ["TC_1", "TC_2"]}, "status": "proposed", "source": "names"}
        ok, ci = svc.call("check_design", {"proposal": proposal(perturbation__target_map=swapped)})
        # a wrong map shows in the numbers: the swapped targets' own genes barely move
        moved = [f["text"] for f in ci["flags"] if f["key"] == "perturbation.modality"]
        self.assertTrue(moved and "down by more than 0.5 in 0%" in moved[0], moved)

    def test_confirmation_needs_the_users_form(self):
        self.assertIn("check a design first", self.svc.call("confirm_design", {})[1]["error"])
        self.ok("check_design", proposal=proposal())
        ok, out = self.svc.call("confirm_design", {}, elicit=None)
        self.assertFalse(ok)
        self.assertIn("no MCP elicitation", out["error"])
        self.assertIsNone(self.ok("get_design")["design_file"])

    def test_the_screen_object_waits_for_a_confirmed_design(self):
        ok, out = self.svc.call("build_screen_object", {})
        self.assertFalse(ok)
        self.assertIn("not confirmed yet", out["error"])
        self.assertFalse(self.ok("get_design")["screen_object"])

    def test_a_failed_build_is_reported_not_started_again(self):
        from unittest import mock
        from pertura import runner
        self.ok("check_design", proposal=proposal())
        self.ok("confirm_design", elicit=answer())
        started = []

        def start(cmd, name, root):
            started.append(name)
            return f"job{len(started)}"
        failed = {"state": "failed", "log_tail": "ValueError: HTODemux could not call the hashtags of lane1"}
        with mock.patch.object(runner, "start", start), mock.patch.object(runner, "wait", lambda *a, **k: None), \
                mock.patch.object(runner, "status", lambda *a, **k: failed):
            out = self.ok("build_screen_object", wait=30)  # the build fails at once: one job, its log, no loop
            self.assertEqual(started, ["screen-object"])
            self.assertEqual(out["state"], "failed")
            self.assertIn("could not call the hashtags", out["log_tail"])
            self.assertIn("revise it (check_design)", out["next"])
            again = self.ok("build_screen_object")
            self.assertEqual((again["state"], len(started)), ("failed", 1))
            self.ok("build_screen_object", retry=True)
            self.assertEqual(len(started), 2)

    def test_confirmed_design_opens_the_gate(self):
        self.ok("check_design", proposal=proposal())
        out = self.ok("confirm_design", elicit=answer(note="donors were pooled; samples are lanes"))
        self.assertEqual(sorted(out["confirmed"]), sorted(design_gate.REQUIRED))
        st = design_gate.status(out["design_file"])
        self.assertTrue(st["open"], st)
        import yaml
        with open(out["design_file"], encoding="utf-8") as fh:
            d = yaml.safe_load(fh)
        self.assertEqual(d["perturbation"]["modality"]["status"], "confirmed")
        self.assertEqual({c["by"] for c in d["confirmations"]}, {BY})
        self.assertEqual(d["confirmations"][0]["note"], "donors were pooled; samples are lanes")
        with open(os.path.join(self.state, "confirmations.jsonl"), encoding="utf-8") as fh:
            recs = [json.loads(x) for x in fh]
        # one answer for the whole design; the cells' quality rule is asked after the build, never on the design's form
        self.assertEqual([(r["asked"], r["answer"]) for r in recs], [("design", "confirm")])
        self.assertEqual({c["key"] for c in d["confirmations"]},
                         {"units.independent_unit", "perturbation.controls", "perturbation.modality",
                          "experiment.conditions", "perturbation.target_map", "perturbation.assignment"})
        self.assertNotIn("data", d)
        again = answer()
        self.ok("confirm_design", elicit=again)  # nothing left to ask
        self.assertEqual(again.calls, [])

    def test_the_form_shows_the_batches_left_out(self):
        """GSE263761 on aa75845: no batches were proposed and the form had no line for them, so the owner answered
        Right with the chips as batches in the note, and the design was built without them."""
        self.ok("check_design", proposal=proposal())
        elicit = answer()
        self.ok("confirm_design", elicit=elicit)
        self.assertIn("- Batches: not given", elicit.calls[0][0])
        self.assertIn("- Another factor grouping the captures: not given", elicit.calls[0][0])
        self.ok("check_design", design={"units.batches": {"value": {"by_sample": {"S1": "chip1", "S2": "chip2"}},
                                                          "source": "the user"}})
        elicit = answer()
        self.ok("confirm_design", elicit=elicit)
        self.assertNotIn("Batches: not given", elicit.calls[0][0])
        self.assertIn("- Batches: batch chip1: S1; batch chip2: S2", elicit.calls[0][0])

    def test_a_change_confirms_nothing_and_returns_the_users_note(self):
        self.ok("check_design", proposal=proposal())
        out = self.ok("confirm_design", elicit=answer(answer="change", note="it is CRISPRa"))
        self.assertEqual((out["confirmed"], out["design_confirmed"], out["user_note"]), ([], False, "it is CRISPRa"))
        self.assertIn("Change the keys it names", out["next"])
        self.assertFalse(os.path.exists(os.path.join(self.state, "design.yaml")))  # nothing runs before Right
        self.ok("check_design", design={"perturbation.modality": {"value": "crispra", "source": "the user"}})
        elicit = answer()
        out = self.ok("confirm_design", elicit=elicit)
        self.assertIn("- Modality: CRISPRa (activation)", elicit.calls[0][0])  # the whole design, asked again
        self.assertTrue(design_gate.status(out["design_file"])["open"])

    def test_a_changed_value_is_asked_again_and_declining_confirms_nothing(self):
        self.ok("check_design", proposal=proposal())
        self.ok("confirm_design", elicit=answer())
        fewer = proposal(perturbation__controls={"value": [{"name": "NTC", "kind": "non_targeting",
                                                            "guides": NTC[:4]}],
                                                 "status": "proposed", "source": "guide names"})
        self.ok("check_design", proposal=fewer)
        out = self.ok("confirm_design", elicit=answer(action="decline"))
        self.assertEqual(out["confirmed"], [])
        st = design_gate.status(os.path.join(self.state, "design.yaml"))
        self.assertTrue(st["open"])  # the design confirmed before still stands; the new value is not in it
        elicit = answer()
        self.ok("confirm_design", elicit=elicit)
        self.assertIn("- Controls: NTC: 4 non-targeting guides", elicit.calls[0][0])
        # the units' column, written into the cell tables now, is read without measuring again; moving the units to
        # it asks again
        from pertura import cell_table
        cell_table.annotate(os.path.join(self.state, "cell_tables"), "lane", {"S1": "L1", "S2": "L2"})
        moved = proposal(units__unit_column={"value": "lane", "status": "proposed", "source": "the intake"})
        r = self.ok("check_design", proposal=moved)
        self.assertTrue(r["ok"], r["problems"])
        elicit = answer()
        self.ok("confirm_design", elicit=elicit)
        self.assertIn("each cell's unit read from the column 'lane'", elicit.calls[0][0])

    def test_mcp_elicitation_round_trip(self):
        self.ok("check_design", proposal=proposal())
        lines = [{"jsonrpc": "2.0", "id": 1, "method": "initialize",
                  "params": {"protocolVersion": "2025-06-18", "capabilities": {"elicitation": {}}}},
                 {"jsonrpc": "2.0", "method": "notifications/initialized"},
                 {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                  "params": {"name": "confirm_design", "arguments": {}}},
                 {"jsonrpc": "2.0", "id": 9, "method": "ping"},  # answered while the form is open
                 {"jsonrpc": "2.0", "id": "pertura-elicit-1",
                  "result": {"action": "accept", "content": {"answer": "confirm", "note": ""}}}]
        out = io.StringIO()
        serve(self.svc, io.StringIO("".join(json.dumps(x) + "\n" for x in lines)), out)
        msgs = [json.loads(x) for x in out.getvalue().splitlines()]
        self.assertEqual(msgs[1]["method"], "elicitation/create")
        self.assertIn("requestedSchema", msgs[1]["params"])
        self.assertEqual(msgs[2], {"jsonrpc": "2.0", "id": 9, "result": {}})
        result = msgs[3]["result"]
        self.assertFalse(result["isError"], result)
        self.assertEqual(len(result["structuredContent"]["confirmed"]), 4)

    def test_mcp_client_without_elicitation_cannot_confirm(self):
        self.ok("check_design", proposal=proposal())
        lines = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"capabilities": {}}},
                 {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                  "params": {"name": "confirm_design", "arguments": {}}}]
        out = io.StringIO()
        serve(self.svc, io.StringIO("".join(json.dumps(x) + "\n" for x in lines)), out)
        msgs = [json.loads(x) for x in out.getvalue().splitlines()]
        self.assertEqual(len(msgs), 2)  # no form was sent
        self.assertTrue(msgs[1]["result"]["isError"])
        self.assertIn("no MCP elicitation", msgs[1]["result"]["structuredContent"]["error"])


class TestWhatRunsInTheBackground(unittest.TestCase):
    """wait_for_runs reads it: session 7's agent was told "no run is running" while the measurement and the build
    ran."""

    def test_the_measurement_and_the_build_are_named_while_they_run(self):
        import threading
        from unittest import mock
        from pertura import design_service as ds
        with tempfile.TemporaryDirectory() as d:
            svc = DesignService.__new__(DesignService)
            svc.dir, svc.cfg = d, {}
            self.assertEqual(svc.in_background(), [])
            gate = threading.Event()
            th = threading.Thread(target=gate.wait)
            th.start()
            ds._MEASURING[d] = {"thread": th, "started": "t0"}
            with open(os.path.join(d, "screen_object_job.json"), "w", encoding="utf-8") as fh:
                json.dump({"job_id": "j1"}, fh)
            try:
                with mock.patch("pertura.runner.status", return_value={"state": "running", "created": "t1"}):
                    work = svc.in_background()
                    self.assertEqual([w["what"] for w in work], ["the measurement", "the screen object's build"])
                    self.assertEqual([w["done"]() for w in work], [False, False])
                gate.set()
                th.join()
                with mock.patch("pertura.runner.status", return_value={"state": "done", "created": "t1"}):
                    self.assertEqual(svc.in_background(), [])
            finally:
                gate.set()
                ds._MEASURING.pop(d, None)


if __name__ == "__main__":
    unittest.main()
