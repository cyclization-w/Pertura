"""Findings, doubts and the record (docs/adr/0032, amendment 1; docs/build_goal_session5_fixes.md, step 4).

- A finding is reworded in place: the record keeps each earlier wording, the user's replies and the checks stay, and
  only another claim (target, feature, direction) starts its checks again.
- A doubt is raised on a finding, and its answer is recorded on the doubt; an answered doubt is no open item.
- The report writes an ID the record does not hold as text, shows each doubt with its finding and its answer, and
  says where the user ended the analysis; write_report says when the user's "enough" is not recorded.
- The counts and names: the cells counted by how many targets they carry, the units each of a finding's tests used,
  and the workspace note's units column and one-valued columns."""
from reading_a_report import report_text
import gzip
import importlib.util
import json
import os
import shutil
import sqlite3
import tempfile
import time
import unittest
import uuid
from unittest import mock

import numpy as np

from pertura import record as rc
from pertura import results_report

HAS_SCANPY = importlib.util.find_spec("scanpy") is not None


class Project(unittest.TestCase):
    """An analysis service with a project open on the synthetic deposit, its record holding one run."""

    def setUp(self):
        from test_design_service import make_deposit
        from pertura.analysis_service import AnalysisService
        self.tmp = tempfile.mkdtemp()
        deposit = os.path.join(self.tmp, "deposit")
        os.makedirs(deposit)
        make_deposit(deposit)
        self.svc = AnalysisService({"workspace": os.path.join(self.tmp, "ws")})
        self.ok("open_project", data_folder=deposit)
        self.rec = self.svc.record
        self.rec.append("run_logged", {"run_id": "r1", "what": "HBZ in GATA1's cells", "code": "c", "inputs": []},
                        rc.TOOL)
        self.rec.append("run_finished", {"run_id": "r1", "state": "ok", "output": "o"}, rc.TOOL)

    def tearDown(self):
        self.svc.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def ok(self, tool, **args):
        ok, out = self.svc.call(tool, args)
        self.assertTrue(ok, out)
        return out

    def fails(self, tool, expect, **args):
        ok, out = self.svc.call(tool, args)
        self.assertFalse(ok, out)
        self.assertIn(expect, out["error"])

    def finding(self, fid="F-GATA1", **more):
        self.rec.append("finding_recorded", {"finding_id": fid, "run_ids": ["r1"],
                                             "statement": "GATA1 repression lowers HBZ.", **more}, rc.AGENT)

    def report(self):
        out = self.ok("write_report")
        return out, report_text(out["report"])


class TestAFindingRewordedInPlace(Project):
    def test_the_replies_and_checks_stay_and_the_earlier_wording_is_kept(self):
        self.finding(target="GATA1", feature="HBZ", direction="down", kind="gene", population="all")
        self.rec.append("check_recorded", {"finding_id": "F-GATA1", "check": "lead", "outcome": "agrees",
                                           "reason": "the design's lead"}, rc.TOOL)
        self.ok("note_reply", finding_id="F-GATA1", kind="keep", words="Keep this one.")
        out = self.ok("reword_finding", finding_id="F-GATA1", reason="the control-relative number, from the checks",
                      statement="GATA1 repression lowers HBZ (log2 FC -1.2 against each capture's controls).",
                      effect={"log2fc": -1.2})
        self.assertEqual(out["checks"], "kept: the target, feature and direction are the same")
        f = self.rec.state()["findings"]["F-GATA1"]
        self.assertEqual(f["statement"], "GATA1 repression lowers HBZ (log2 FC -1.2 against each capture's controls).")
        self.assertEqual([r["words"] for r in f["replies"]], ["Keep this one."])
        self.assertEqual(f["checks"]["lead"]["outcome"], "agrees")
        self.assertEqual(len(f["wordings"]), 1)
        earlier = f["wordings"][0]
        self.assertEqual((earlier["statement"], earlier["because"]),
                         ("GATA1 repression lowers HBZ.", "the control-relative number, from the checks"))
        self.assertTrue(earlier["time"] and earlier["replaced"])
        self.assertEqual(list(self.rec.state()["findings"]), ["F-GATA1"])  # one finding, not a second one

    def test_another_claim_starts_its_checks_again_and_the_earlier_go_with_the_earlier_wording(self):
        self.finding(target="GATA1", feature="HBZ", direction="down", kind="gene", population="all")
        self.rec.append("check_recorded", {"finding_id": "F-GATA1", "check": "lead", "outcome": "agrees",
                                           "reason": "the design's lead"}, rc.TOOL)
        started = []
        with mock.patch.object(self.svc, "_finding_parts", return_value={"kind": "gene"}), \
                mock.patch.object(self.svc, "_start_checks",
                                  side_effect=lambda fid, f, parts, **k: started.append(f) or {"checks_run": "j"}):
            out = self.ok("reword_finding", finding_id="F-GATA1", reason="the drop is in HBA1", feature="HBA1")
        self.assertEqual(started[0]["feature"], "HBA1")
        self.assertIn("another claim", out["checks"])
        f = self.rec.state()["findings"]["F-GATA1"]
        self.assertEqual(f["checks"], {})
        self.assertEqual(f["wordings"][0]["checks"]["lead"]["outcome"], "agrees")

    def test_a_description_given_its_claim_becomes_one_in_place_and_its_checks_start(self):
        """GSE267740 (d3a7dcc): ten findings were descriptions, and the only route to a checked claim was a new
        finding and a new version of each result. A description given its claim and kind keeps its ID."""
        self.finding(kind="description")
        self.rec.append("figure_saved", {"figure_id": "fig1", "run_id": "r1", "paths": {"png": "f.png"},
                                         "caption": "c"}, rc.TOOL)
        out = self.ok("report_result", result_id="res1", title="GATA1 lowers HBZ", finding_ids=["F-GATA1"],
                      figure="fig1", text="GATA1 repression lowers HBZ [finding:F-GATA1].")
        self.assertIn("reword_finding with target, feature, direction and kind", out["no_claim_checked"])
        started = []
        with mock.patch.object(self.svc, "_finding_parts", return_value={"kind": "gene"}) as parts, \
                mock.patch.object(self.svc, "_start_checks",
                                  side_effect=lambda fid, f, p, **k: started.append(fid) or {"checks_run": "j"}):
            out = self.ok("reword_finding", finding_id="F-GATA1", reason="its claim, so its checks run",
                          target="GATA1", feature="HBZ", direction="down", kind="gene", reference="control_guides")
        self.assertEqual(started, ["F-GATA1"])
        self.assertEqual(parts.call_args[0][1], "gene")  # the kind given, not the description's
        self.assertIn("another claim", out["checks"])
        f = self.rec.state()["findings"]["F-GATA1"]
        self.assertEqual((f["kind"], f["target"], f["reference"]), ("gene", "GATA1", "control_guides"))
        self.assertEqual(self.rec.state()["results"]["res1"]["finding_ids"], ["F-GATA1"])  # the result cites it still
        again = self.ok("report_result", result_id="res1", text="GATA1 repression lowers HBZ [finding:F-GATA1].")
        self.assertNotIn("no_claim_checked", again)

    def test_a_kind_comes_with_its_whole_claim_and_a_set_is_claimed_in_place(self):
        """The review of 076ca1b (d2): a kind given without a direction was stored, so the result's notice went
        quiet with no check run; and a set's claim (four targets down on one score) could not be made in place."""
        self.finding(kind="description")
        self.fails("reword_finding", "comes with its target (or targets), feature and direction",
                   finding_id="F-GATA1", reason="r", target="GATA1", feature="HBZ", kind="gene")
        self.assertEqual(self.rec.state()["findings"]["F-GATA1"]["kind"], "description")  # nothing recorded
        self.fails("reword_finding", "in place of target", finding_id="F-GATA1", reason="r", target="GATA1",
                   targets=["GATA1", "TAL1"], feature="HBZ", direction="down", kind="gene")
        named, started = [], []
        with mock.patch.object(self.svc, "_finding_parts",
                               side_effect=lambda t, *a, **k: named.append(t) or {"kind": "gene"}), \
                mock.patch.object(self.svc, "_start_checks",
                                  side_effect=lambda fid, f, p, **k: started.append(f["targets"]) or {}):
            self.ok("reword_finding", finding_id="F-GATA1", reason="the set's claim", targets=["GATA1", "TAL1"],
                    feature="HBZ", direction="down", kind="gene")
        self.assertEqual((named, started), (["GATA1", "TAL1"], [["GATA1", "TAL1"]]))  # each target, as recorded ones
        f = self.rec.state()["findings"]["F-GATA1"]
        self.assertEqual((f["target"], f["targets"], f["kind"]), ("GATA1", ["GATA1", "TAL1"], "gene"))

    def test_one_target_given_as_targets_is_no_set(self):
        """d2's nit on a83fe3e: record_finding keeps targets only for two or more; a rewording does the same, so a
        later rewording to another target is not refused as another set."""
        self.finding(kind="description")
        with mock.patch.object(self.svc, "_finding_parts", return_value={"kind": "gene"}), \
                mock.patch.object(self.svc, "_start_checks", return_value={}):
            self.ok("reword_finding", finding_id="F-GATA1", reason="its claim", targets=["GATA1"], feature="HBZ",
                    direction="down", kind="gene")
            self.assertFalse(self.rec.state()["findings"]["F-GATA1"].get("targets"))
            self.ok("reword_finding", finding_id="F-GATA1", reason="TAL1's, as the guides show", target="TAL1")

    def test_neither_a_new_wording_nor_new_runs_make_the_skeptic_due_again(self):
        """Each rewording with new runs made the skeptic due again: the skeptic reads a finding once."""
        self.finding()
        self.ok("note_reply", finding_id="F-GATA1", kind="keep", words="Keep it.")
        self.ok("record_skeptic_check", finding_ids=["F-GATA1"], doubt_ids=[])
        out = self.ok("reword_finding", finding_id="F-GATA1", reason="clearer", statement="HBZ falls with GATA1 off.")
        self.assertNotIn("skeptic_due", out)
        self.rec.append("run_logged", {"run_id": "r2", "what": "HBZ per capture", "code": "c", "inputs": []},
                        rc.TOOL)
        out = self.ok("reword_finding", finding_id="F-GATA1", reason="per capture too", run_ids=["r1", "r2"])
        self.assertNotIn("F-GATA1", out.get("skeptic_due") or [])

    def test_a_rewording_says_why_and_changes_something(self):
        self.finding()
        self.fails("reword_finding", "gives its reason", finding_id="F-GATA1", reason=" ", statement="x.")
        self.fails("reword_finding", "changes nothing", finding_id="F-GATA1", reason="r",
                   statement="GATA1 repression lowers HBZ.")
        self.fails("reword_finding", "no finding", finding_id="F-none", reason="r", statement="x.")

    def test_note_reply_says_it_records_the_users_own_words(self):
        from pertura.analysis_service import TOOLS
        words = next(t for t in TOOLS if t["name"] == "note_reply")["description"]
        self.assertIn("the user's words only", words)
        self.assertIn("reword_finding", words)


class TestDoubtsOnFindings(Project):
    def test_session_5s_case_a_doubt_on_a_finding_then_the_report(self):
        self.finding()
        self.fails("raise_doubt", "finding_id", doubt_id="D1", question_id="F-GATA1", text="ambient haemoglobin RNA",
                   would_change="the drop gone once the haemoglobin fraction is regressed out")
        self.ok("raise_doubt", doubt_id="D1", finding_id="F-GATA1", text="ambient haemoglobin RNA",
                would_change="the drop gone once the haemoglobin fraction is regressed out")
        out, text = self.report()
        still_open = text[text.index("## Still open"):text.index("## The record")]
        line = next(x for x in still_open.splitlines() if "ambient haemoglobin RNA" in x)
        self.assertIn("(#doubt-D1)", line)
        self.assertIn("(#finding-F-GATA1)", line)
        self.assertNotIn("### Doubts answered", text)  # not answered: it is an open item, listed once
        self.assertEqual(out["links"]["unlinked"], [])

    def test_an_answered_doubt_is_no_open_item_and_the_report_shows_its_answer(self):
        self.finding()
        self.ok("raise_doubt", doubt_id="D1", finding_id="F-GATA1", text="ambient haemoglobin RNA",
                would_change="the drop gone once the haemoglobin fraction is regressed out")
        self.assertIn(("doubt without an answer", "D1"), {(i["kind"], i["id"]) for i in self.rec.critic()})
        self.fails("answer_doubt", "one of", doubt_id="D1", outcome="fine", reason="r")
        self.ok("answer_doubt", doubt_id="D1", outcome="weakens", reason="the drop shrinks to -0.37 once regressed",
                run_ids=["r1"])
        self.assertNotIn("D1", {i["id"] for i in self.rec.critic()})
        out, text = self.report()
        answered = text[text.index("### Doubts answered"):text.index("## Record entries")]
        line = next(x for x in answered.splitlines() if "Answered: weakens" in x)
        self.assertIn("the drop shrinks to -0.37 once regressed", line)
        self.assertIn("(#run-r1)", line)
        self.assertIn("(#finding-F-GATA1)", line)
        self.assertEqual(out["links"]["unlinked"], [])

    def test_a_list_item_is_one_entry_linked_once(self):
        self.finding()
        self.ok("raise_doubt", doubt_id="D1", finding_id="F-GATA1", would_change="x",
                text="The HBZ drop may be ambient RNA. Each capture's haemoglobin load differs. No check removes it.")
        out, _ = self.report()
        self.assertEqual(out["links"]["unlinked"], [])

    def test_a_doubt_answered_still_open_stays_listed_with_its_reason(self):
        self.finding()
        self.ok("raise_doubt", doubt_id="D1", finding_id="F-GATA1", text="ambient RNA", would_change="x")
        self.ok("answer_doubt", doubt_id="D1", outcome="still open", reason="the adjustment cannot separate them")
        item = next(i for i in self.rec.critic() if i["id"] == "D1")
        self.assertEqual(item["kind"], "doubt still open")
        self.assertIn("cannot separate them", item["what"])

    def test_a_doubt_on_a_question_or_on_the_analysis(self):
        self.rec.append("question_planned", {"question_id": "C.genes", "letter": "C", "item": "genes",
                                             "because": "always"}, rc.PLAN)
        self.ok("raise_doubt", doubt_id="D1", question_id="C.genes", text="one capture carries it", would_change="x")
        self.ok("raise_doubt", doubt_id="D2", text="the captures differ at baseline", would_change="y")
        self.assertEqual(self.ok("answer_doubt", doubt_id="D2", outcome="still open", reason="z")["on"], None)


class TestWhatThePageIsTold(Project):
    """docs/build_goal_session5_fixes.md, step 6: the replies the user's page and status line read."""

    def test_a_notebook_runs_result_names_no_job(self):
        out = self.ok("module_result", run_id="r1")
        self.assertIn("job_id", out)
        self.assertIsNone(out["job_id"])

    def test_a_finding_without_a_figure_is_told_what_the_page_shows(self):
        out = self.ok("record_finding", finding_id="F-1", run_ids=["r1"], statement="The captures compare.",
                      kind="description")
        self.assertIn("the latest figure of its runs", out["figure"])
        out = self.ok("record_finding", finding_id="F-2", run_ids=["r1"], statement="The captures compare.",
                      figure="r1:figure", kind="description")
        self.assertNotIn("figure", out)


class TestTheReport(Project):
    def test_an_id_the_record_does_not_hold_is_written_as_text(self):
        """A record written before doubts named their finding: the finding's ID in the doubt's question_id."""
        self.finding()
        with sqlite3.connect(self.rec.path) as con:  # an event as such a record holds it, past the record's checks
            con.execute("INSERT INTO events(event_id, event_type, timestamp, actor, payload) VALUES (?,?,?,?,?)",
                        (uuid.uuid4().hex, "doubt_raised", time.strftime("%Y-%m-%dT%H:%M:%S"), rc.AGENT,
                         json.dumps({"doubt_id": "D-old", "question_id": "F-GATA1", "text": "ambient RNA",
                                     "would_change": "x"})))
        out, text = self.report()
        self.assertIn("question F-GATA1", text)
        self.assertNotIn("(#question-F-GATA1)", text)
        self.assertIn("(#doubt-D-old)", text)

    def test_write_report_says_when_enough_is_not_recorded_and_the_report_where_the_user_ended(self):
        self.finding()
        out, text = self.report()
        self.assertIn("the recorder records their words", out["enough"])  # no agent writes the record (ADR 0051)
        self.assertNotIn("The user ended the analysis", text)
        self.ok("note_reply", kind="enough", words="That's enough for today: please write the report")
        out, text = self.report()
        self.assertNotIn("enough", out)
        lines = [x for x in text.splitlines() if x.startswith("The user ended the analysis")]
        self.assertEqual(len(lines), 2)  # the report's top (docs/adr/0049, D7) and the evidence's record
        for line in lines:
            self.assertIn("after 1 finding", line)
            self.assertIn("#finding-F-GATA1)", line)
            self.assertIn("That's enough for today: please write the report", line)
        self.assertEqual(out["links"]["unlinked"], [])


@unittest.skipUnless(HAS_SCANPY, "needs scanpy")
class TestTheCountsAndNames(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_pair_cells_are_counted_as_two_target_cells(self):
        from test_question_modules import synthetic_screen
        from pertura import question_modules as qm
        s, design = synthetic_screen(self.d)
        pair = np.flatnonzero((s.label == "C") & (s.unit == "u2"))
        s.label = np.where(np.isin(np.arange(len(s.label)), pair), "A|B", s.label)
        r = qm.REGISTRY["B.guide_calls_and_their_evidence"].run(s, design, os.path.join(self.d, "b"))
        self.assertEqual(r.diagnostics["cells_by_kind"]["two or more targets"], len(pair))
        self.assertIn(f"{len(pair)} with two or more (in u2)", r.summary)
        a = qm.REGISTRY["A.what_was_done"].run(s, design, os.path.join(self.d, "a"))
        row = a.table.set_index("unit").loc["u2"]
        self.assertEqual(int(row["two or more targets"]), len(pair))
        self.assertEqual(int(row["one target"]), 40 + 40 + 0)  # u2's A cells; its C cells are now the pair

    def test_a_screen_without_pairs_has_no_two_target_cells(self):
        from test_question_modules import synthetic_screen
        from pertura import question_modules as qm
        s, design = synthetic_screen(self.d)
        r = qm.REGISTRY["B.guide_calls_and_their_evidence"].run(s, design, os.path.join(self.d, "b"))
        self.assertNotIn("two or more targets", r.diagnostics["cells_by_kind"])
        self.assertIn("0 with two or more,", r.summary)

    def test_each_test_names_its_units(self):
        from test_design_fit import screen
        from pertura import design_fit as fit
        from pertura import finding_checks as fc
        s, _ = screen(self.d, independent="none", context="cell_line")
        wilcoxon = {"method": "wilcoxon", "reason": "a test on cells (Wilcoxon), the test the finding came from"}
        sp = {"target": "A", "feature": "s0", "direction": 1, "kind": "gene", "reference": "control_guides",
              "lead": wilcoxon}
        lead, checks = fc.run_checks(s, sp, os.path.join(self.d, "work"))
        self.assertEqual(lead["units"], {"u1": 80, "u2": 80, "u3": 80, "u4": 80})
        self.assertNotIn("replicates", lead)  # a test on cells takes no replicates
        self.assertIn("in 4 units: u1 80, u2 80, u3 80, u4 80", fc.sentence(sp, lead, checks))
        tm, rm = fit.sides(s, "A")
        self.assertEqual(fc._units(s, tm, rm, "edger_ql")["replicates"], ["u1", "u2", "u3", "u4"])


class TestTheWorkspaceNote(unittest.TestCase):
    def test_it_names_the_units_column_and_the_columns_of_one_value(self):
        from pertura.analysis_service import AnalysisService
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "screen.json"), "w", encoding="utf-8") as fh:
                json.dump({"cells": 4, "labels": {"A": 2, "control": 2}, "units": {"D1": 2, "S1Sa": 2}}, fh)
            with gzip.open(os.path.join(d, "cells.tsv.gz"), "wt", encoding="utf-8") as fh:
                fh.write("barcode\tsample\tunit\tlabel\tcondition\n")
                for b, u, lab in (("c1", "D1", "A"), ("c2", "D1", "control"), ("c3", "S1Sa", "A"),
                                  ("c4", "S1Sa", "control")):
                    fh.write(f"{b}\tRNA\t{u}\t{lab}\t\n")
            svc = AnalysisService({"workspace": os.path.join(d, "ws")})
            nb = mock.Mock()
            nb.info.return_value = {"mode": "normal", "threads": 4, "max_cell_seconds": 600, "output_chars": 8000,
                                    "network": False}
            with mock.patch.object(svc, "_screen_dir", return_value=d):
                note = svc._workspace_note(nb)
            # GSE203592 on 99eab77: the note named 8 units where the design merged two captures into one
            merged = os.path.join(d, "design.yaml")
            with open(merged, "w", encoding="utf-8") as fh:
                fh.write("units:\n  merge:\n    value: {S1Sa: D1}\n")
            with mock.patch.object(svc, "_screen_dir", return_value=d), mock.patch.object(svc, "design", mock.Mock()), \
                    mock.patch.object(svc, "_design_path", return_value=merged):
                again = svc._workspace_note(nb)
            svc.close()
        self.assertTrue(note["units_column"].startswith("unit: each cell's unit (2: D1, S1Sa)"), note)
        self.assertEqual(note["columns_with_one_value"], {"sample": "RNA", "condition": ""})
        self.assertTrue(again["units_column"].startswith("unit: each cell's unit (1: D1)"), again)
        self.assertIn("capture: the build's, before units.merge", again["units_column"])


if __name__ == "__main__":
    unittest.main()
