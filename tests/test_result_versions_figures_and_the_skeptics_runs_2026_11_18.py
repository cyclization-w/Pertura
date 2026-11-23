"""Problems 1-3 of the first run of one simple loop:
1. the skeptic reader gives a claim that names no run its turn's runs, as the record applies it;
2. a result's new version keeps its earlier findings, until a doubt overturns one (r1 went from 28 findings to 18);
3. a result whose named figure fails the figure rule is recorded with code's pick, its findings' latest figure."""
import os
import sys
import unittest
from unittest import mock

import test_the_record_kept_by_code_adr0051 as inbox_tests
from test_results_from_findings import TEXT, Service

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "plugin", "status"))
import recorder  # noqa: E402

inbox = inbox_tests.inbox


class TestResults(Service):
    claim = inbox_tests.TestTheInbox.claim

    def setUp(self):
        super().setUp()
        parts = {"cells": None, "reference": "control_guides", "kind": "gene", "genes": None, "state_table": None,
                 "lead": None}
        for p in (mock.patch.object(self.svc, "_finding_parts", return_value=parts),
                  mock.patch.object(self.svc, "_start_checks", return_value={"checks_run": "c1"}),
                  mock.patch.object(self.svc, "_confirmed", return_value={})):
            p.start()  # as the inbox's own tests: the claims' checks need a built screen object
            self.addCleanup(p.stop)

    def result(self, **more):
        return {"title": "GATA1 knockdown raises SPI1", "text": TEXT, "figure": "fig1", **more}

    def test_a_new_version_keeps_its_findings_until_a_doubt_overturns_one(self):
        inbox(self.svc, "t1", {"claims": [self.claim()], "results": [self.result(claims=[0])]})
        self.ok("pertura_status")
        inbox(self.svc, "t2", {"claims": [self.claim(feature={"kind": "gene", "name": "CEBPA"})],
                               "results": [self.result(claims=[0], result_id="r1")]})
        self.ok("pertura_status")
        self.assertEqual(self.svc.record.state()["results"]["r1"]["finding_ids"], ["t1-c1", "t2-c1"])
        self.ok("raise_doubt", doubt_id="D1", text="one guide", would_change="a second guide", finding_id="t1-c1")
        self.ok("answer_doubt", doubt_id="D1", outcome="overturns", reason="the second guide does not show it")
        inbox(self.svc, "t3", {"results": [self.result(result_id="r1", finding_ids=["t2-c1"])]})
        self.ok("pertura_status")
        self.assertEqual(self.svc.record.state()["results"]["r1"]["finding_ids"], ["t2-c1"])

    def test_the_reviewer_reads_facts_and_no_choice(self):
        """2026-11-18: the user's words, the doubts the record holds, and the targets the user named that no result
        cites yet (whole words, as written)."""
        from pertura import recording
        folder = os.path.join(self.svc.project, "design", "screen_object")
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, "guides.tsv"), "w", encoding="utf-8") as fh:
            fh.write("guide\ttarget\tkind\nGATA1.g1\tGATA1\ttargeting\nCEBPA.g1\tCEBPA\ttargeting\n"
                     "CEBPAB.g1\tCEBPAB\ttargeting\nNTC.1\t\tcontrol\n")
        self.ok("open_thread", about="Which of GATA1 and CEBPA moves the cells?", raised_by="the user",
                user_words="Which of GATA1 and CEBPA moves the cells?")
        inbox(self.svc, "t1", {"claims": [self.claim()], "results": [self.result(claims=[0])]})
        self.ok("pertura_status")
        self.ok("raise_doubt", doubt_id="D1", text="cells counted as replicates", would_change="a second capture",
                finding_id="t1-c1")
        facts = recording.reader_facts(self.svc.project)
        self.assertEqual(facts["the_users_words"], ["Which of GATA1 and CEBPA moves the cells?"])
        self.assertEqual(facts["targets_the_user_named_with_no_result_yet"], ["CEBPA"])  # not CEBPAB
        self.assertEqual(facts["doubts_the_record_holds"], [{"text": "cells counted as replicates", "answer": None}])

    def test_a_named_figure_the_rule_refuses_gives_way_to_its_findings_latest_figure(self):
        inbox(self.svc, "t1", {"claims": [self.claim()], "results": [self.result(claims=[0], figure="r2")]})
        out = self.ok("pertura_status")
        r = self.svc.record.state()["results"]["r1"]
        self.assertEqual(r["figure"], "fig1")  # r2 is none of its findings' runs: fig1, drawn from r1, is
        self.assertIn("r2 was not taken", out["recorded"][0]["applied"][-1]["figure"])


class TestTheSkepticsRuns(unittest.TestCase):
    def test_a_claim_that_names_no_run_is_read_with_its_turns_runs(self):
        rec = {"claims": [{"statement": "x", "targets": ["A"], "feature": {"kind": "gene", "name": "G"},
                           "direction": "up", "run_ids": []}],
               "results": [{"title": "t", "text": "x", "claims": [0]}], "turn_runs": ["run0047-3fa9c1"],
               "range": {"session": "s", "agent": "main", "first": "u1", "last": "u2"}}
        asked = {}
        with mock.patch.object(recorder, "checks_of", return_value={}), \
                mock.patch.object(recorder, "run_outputs", side_effect=lambda h, p, ids: asked.setdefault("runs", ids)
                                  and {}), \
                mock.patch.object(recorder, "send"):
            recorder.read_by_the_skeptic("host", "/p", "t1", rec, ask=lambda *a, **k: {"doubts": []})
        self.assertEqual(asked["runs"], ["run0047-3fa9c1"])


if __name__ == "__main__":
    unittest.main()
