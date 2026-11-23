"""The record as the workbench reads it (pertura.page_view): each finding's claim with each target's checks, a
description with none, each result with its figure's file on the server, the threads and the lead's last verdict;
read without writing, from a record a run is still writing."""
import contextlib
import io
import json
import os
import tempfile
import unittest

from pertura import page_view
from pertura import record as rc


class TestTheRunsMethods(unittest.TestCase):
    """The methods page's facts: each module run that ended, with what its result's diagnostics state (packages and
    versions, test, units, corrections); read once a run, never raising."""

    def test_each_module_run_that_ended_with_its_results_diagnostics(self):
        with tempfile.TemporaryDirectory() as jobs:
            def job(name, result):
                os.makedirs(os.path.join(jobs, name))
                with open(os.path.join(jobs, name, "result.json"), "w", encoding="utf-8") as fh:
                    fh.write(result if isinstance(result, str) else json.dumps(result))
            job("j1", {"summary": "122 of 135 targets change genes", "diagnostics": {
                "package_calls": [{"package": "edgeR", "version": "4.8.2", "function": "glmQLFit, glmQLFTest"}],
                "method": "auto", "units": 9, "multiple_testing": {"tests": 3632, "corrected_together": "per target"},
                "rows_unread": [1, 2]}})
            job("j2", "{not json")
            st = {"runs": {
                "r1": {"state": "done", "job_id": "j1", "question_id": "C.genes", "why": "genes per target"},
                "r2": {"state": "done", "job_id": "j2", "question_id": "C.states_and_composition"},  # unreadable
                "r3": {"state": "running", "job_id": "j3", "question_id": "C.genes"},
                "cell": {"state": "ok", "job_id": None, "question_id": None}}}
            seen = {}
            got = page_view.methods(st, jobs, seen)
            self.assertEqual([m["run_id"] for m in got], ["r1"])
            m = got[0]
            self.assertEqual((m["module"], m["why"]), ("C.genes", "genes per target"))
            self.assertEqual(m["packages"], [{"package": "edgeR", "version": "4.8.2",
                                              "function": "glmQLFit, glmQLFTest"}])
            self.assertEqual(m["facts"], {"method": "auto", "units": "9",
                                          "multiple_testing": "tests 3632; corrected together per target"})
            self.assertEqual(list(seen), ["r1"])  # the unreadable one is tried again next time
            job("j2b", {"summary": "s"})
            st["runs"]["r2"]["job_id"] = "j2b"
            self.assertEqual([x["run_id"] for x in page_view.methods(st, jobs, seen)], ["r1", "r2"])


class TestTheView(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = os.path.join(self.tmp.name, "record")
        rec = self.rec = rc.Record(self.folder)
        rec.append("run_logged", {"run_id": "r1", "what": "a score", "code": "c", "inputs": []}, rc.TOOL)
        rec.append("run_finished", {"run_id": "r1", "state": "ok", "output": "o"}, rc.TOOL)
        rec.append("finding_recorded", {"finding_id": "F1", "target": "A", "targets": ["A", "B"], "feature":
                                        "table:score", "direction": "down", "population": "all", "run_ids": ["r1"],
                                        "kind": "score", "statement": "A and B lower the score [run:r1]."}, rc.AGENT)
        for target, outcome in (("A", "agrees"), ("B", "other direction")):
            rec.append("check_recorded", {"finding_id": "F1", "check": "lead", "outcome": outcome, "target": target,
                                          "numbers": {"test": "linear_model", "cells": 50, "effect": -0.3,
                                                      "rows": [1, 2, 3]}}, rc.TOOL)
        rec.append("finding_recorded", {"finding_id": "F2", "run_ids": ["r1"], "kind": "description",
                                        "statement": "Two blocks of targets [run:r1]."}, rc.AGENT)
        rec.append("figure_saved", {"figure_id": "fig1", "run_id": "r1", "paths": {"png": "/w/p/fig1.png"},
                                    "caption": "c"}, rc.TOOL)
        rec.append("result_reported", {"result_id": "R1", "title": "A lowers the score", "finding_ids": ["F1"],
                                       "figure": "fig1", "figure_run": "r1",
                                       "text": "A lowers the score [finding:F1]."}, rc.AGENT)
        rec.append("thread_opened", {"thread_id": "t1", "about": "the score", "raised_by": "one level up",
                                     "joins": ["F2"]}, rc.AGENT)
        rec.append("lead_reviewed", {"verdict": "not yet", "story": "A lowers the score.", "threads": ["t1"],
                                     "figures": []}, rc.AGENT)

    def tearDown(self):
        self.tmp.cleanup()

    def test_each_claim_with_each_targets_checks_and_the_rest_beside(self):
        v = page_view.view(self.rec.state())
        f1 = v["findings"]["F1"]
        self.assertEqual(f1["targets"], ["A", "B"])
        self.assertEqual(f1["checks"]["A"]["lead"], {"outcome": "agrees", "test": "linear_model", "cells": 50,
                                                     "effect": -0.3})  # the numbers a reader needs, not every one
        self.assertEqual(f1["checks"]["B"]["lead"]["outcome"], "other direction")
        self.assertIn("B lead: other direction", f1["disagree"])
        self.assertEqual((v["findings"]["F2"]["kind"], v["findings"]["F2"]["checks"]), ("description", {}))
        self.assertEqual(v["results"]["R1"]["png"], "/w/p/fig1.png")
        self.assertEqual(v["threads"]["t1"]["status"], "open")
        self.assertEqual(v["lead"]["verdict"], "not yet")
        self.assertIsNone(v["story"])  # no report's story until one is written
        self.rec.append("story_written", {"text": "A and B lower the score."}, rc.AGENT)
        self.assertEqual(page_view.view(self.rec.state())["story"], "A and B lower the score.")

    def _the_agents_work(self):
        """Two doubts on F1, the second the skeptic's; its check of F1; t1 set aside with nothing of its own read; a
        second review that keeps the main line."""
        rec = self.rec
        rec.append("doubt_raised", {"doubt_id": "D1", "text": "one sample drives it", "would_change": "F1 drops",
                                    "finding_id": "F1"}, rc.AGENT)
        rec.append("doubt_raised", {"doubt_id": "D2", "text": "the controls differ", "would_change": "F1 halves",
                                    "finding_id": "F1"}, rc.AGENT)
        rec.append("skeptic_checked", {"finding_ids": ["F1"], "doubt_ids": ["D2"],
                                       "checked": [{"finding_id": "F1", "check": "each sample alone",
                                                    "outcome": "sound"},
                                                   {"finding_id": "F1", "check": "the controls", "outcome": "D2"}]},
                   rc.AGENT)
        rec.append("thread_changed", {"thread_id": "t1", "status": "set aside", "reason": "the design cannot say"},
                   rc.AGENT)
        rec.append("lead_reviewed", {"verdict": "not yet", "story": "A lowers the score; B is open.",
                                     "main_line": {"thread_id": None, "why": "t1 showed nothing new"}}, rc.AGENT)
        return page_view.view(rec.state())

    def test_each_agents_work_who_raised_each_doubt_what_the_skeptic_checked_and_every_review(self):
        v = self._the_agents_work()
        self.assertEqual(v["doubts_raised_by"], {"D1": "main", "D2": "pertura:skeptic"})
        self.assertEqual(v["findings"]["F1"]["skeptic"]["checked"][0],
                         {"check": "each sample alone", "outcome": "sound"})
        self.assertEqual([r["story"] for r in v["reviews"]], ["A lowers the score.", "A lowers the score; B is open."])
        self.assertEqual(v["reviews"][1]["main_line"]["why"], "t1 showed nothing new")
        t1 = next(r for r in v["tree"] if r["thread_id"] == "t1")
        self.assertEqual((t1["reason"], t1["read"]), ("the design cannot say", False))  # ended with nothing of its own

    def test_the_page_shows_each_agents_work_where_it_acts(self):
        """The user, 2026-11-13 (relayed): the lead, the skeptic and the design auditor show the user how they work;
        the main agent cannot analyse well before the design auditor is done."""
        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "plugin",
                                        "status"))
        import page
        v = self._the_agents_work()
        v["design"] = {"confirmed": ["Controls"], "open": ["Biological replicates"], "since": "2026-11-13T23:00:00",
                       "last_check": {"time": "2026-11-13T23:10:00", "problems": ["no control guides named"]}}
        p = page.empty()
        p["doubts"] = {d: {"doubt_id": d, "finding_id": "F1", "text": t, "answers": [], "time": 1.0}
                       for d, t in (("D1", "one sample drives it"), ("D2", "the controls differ"))}
        html = page.render(p, "Pertura · screen_X", v, [])
        self.assertIn("The analysis starts once you confirm the design.", html)  # the overview's top, first
        self.assertIn("Still open: Biological replicates.", html)
        self.assertIn("1 problem</summary>", html)
        self.assertIn("raised by the main agent", html)
        self.assertIn("raised by the skeptic", html)
        self.assertIn("The skeptic's check", html)
        self.assertIn("each sample alone", html)
        lead = html[html.index("id=\"lead\""):]
        lead = lead[:lead.index("</section>")]
        self.assertLess(lead.index("Review 2"), lead.index("Review 1"))  # the latest first
        self.assertIn("<b>Main line:</b> kept. t1 showed nothing new", lead)
        self.assertIn("nothing of its own read · the design cannot say", html)  # on t1's node in the tree
        v["design"]["open"] = []  # confirmed: the overview no longer leads with the design
        self.assertNotIn("The analysis starts once", page.render(p, "Pertura · screen_X", v, []))

    def test_it_is_read_without_writing(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(page_view.main([self.folder]), 0)
        self.assertEqual(json.loads(out.getvalue())["findings"]["F1"]["targets"], ["A", "B"])


if __name__ == "__main__":
    unittest.main()
