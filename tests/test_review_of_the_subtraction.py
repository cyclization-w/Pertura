"""The independent review of item 13's batch (docs/build/session6_fix_batch_log.md): what it found, fixed.
- a call that fails writes nothing: a finding with a bad difference, a thread on a doubt never raised, a review whose
  second answer is malformed; each can be given again, right;
- a thread that ends shuts its workers' notebooks;
- a result's figure is one the record holds or one its run drew;
- the number check takes the runs' numbers, not the digits of IDs and times, keeps both ends of a range, and rounds
  half up;
- the server names the tool that does a retired tool's work;
- every sentence of a result's caption is linked."""
import os
import tempfile
import unittest
from unittest import mock

from test_design_service import make_deposit

from pertura import mcp_server, record as rc, text_numbers
from pertura.analysis_service import AnalysisService


class Service(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        deposit = os.path.join(self.tmp.name, "deposit")
        os.makedirs(deposit)
        make_deposit(deposit)
        self.svc = AnalysisService({"workspace": os.path.join(self.tmp.name, "ws")})
        ok, out = self.svc.call("open_project", {"data_folder": deposit})
        assert ok, out
        for rid in ("r1", "r2"):
            self.svc.record.append("run_logged", {"run_id": rid, "what": "a cell", "code": "c", "inputs": []},
                                   rc.TOOL)
            self.svc.record.append("run_finished", {"run_id": rid, "state": "ok", "output": "0.42 of cells"},
                                   rc.TOOL)

    def tearDown(self):
        self.svc.close()
        self.tmp.cleanup()

    def call(self, tool, **args):
        return self.svc.call(tool, args)


class TestNothingHalfWritten(Service):
    def test_a_finding_with_a_bad_difference_is_not_recorded_and_can_be_given_again(self):
        ok, out = self.call("record_finding", finding_id="f1", statement="s [run:r1].", run_ids=["r1"],
                            prior={"text": "known", "source": "PMID 1"}, differs={"text": "x", "source": "the authors"},
                            kind="description")
        self.assertFalse(ok)
        st = self.svc.record.state()
        self.assertNotIn("f1", st["findings"])
        self.assertEqual(st["knowledge"], [])
        ok, out = self.call("record_finding", finding_id="f1", statement="s [run:r1].", run_ids=["r1"],
                            differs={"text": "the paper saw none", "source": "the paper"}, kind="description")
        self.assertTrue(ok, out)
        self.assertNotIn("f1", out.get("skeptic_due") or [])  # the skeptic reads the findings a result rests on

    def test_a_thread_on_a_doubt_never_raised_writes_nothing(self):
        ok, _ = self.call("open_thread", about="a", raised_by="r1", thread_id="u", user_words="what moves?",
                          doubt_id="d9")
        self.assertFalse(ok)
        st = self.svc.record.state()
        self.assertNotIn("u", st["threads"])
        self.assertIsNone(st["question"])

    def test_a_review_with_a_malformed_answer_writes_nothing(self):
        self.svc.record.append("figure_saved", {"figure_id": "fig1", "run_id": "r1", "paths": {"png": "x.png"},
                                                "caption": "c"}, rc.TOOL)
        ok, _ = self.call("record_figure_review", figure_id="fig1", verdict="fix", issues=["a", "b"],
                          answers=[{"issue": 1, "answer": "fine"}, {"issue": "two", "answer": "fine"}])
        self.assertFalse(ok)
        self.assertEqual(self.svc.record.state()["figures"]["fig1"]["reviews"], [])


class TestWorkersAndFigures(Service):
    def test_a_thread_that_ends_shuts_its_workers_notebooks(self):
        self.svc.record.append("thread_opened", {"thread_id": "t1", "about": "a", "raised_by": "r1"}, rc.TOOL)
        nb = mock.Mock()
        self.svc.notebooks = {"main": mock.Mock(), "t1": nb, "t1-n1000": mock.Mock(), "t10": mock.Mock()}
        ok, out = self.call("thread_status", thread_id="t1", status="set aside", reason="weaker than t2")
        self.assertTrue(ok, out)
        self.assertEqual(out["notebook_shut"], ["t1", "t1-n1000"])
        self.assertEqual(sorted(self.svc.notebooks), ["main", "t10"])
        nb.shutdown.assert_called_once()

    def test_a_results_figure_is_one_there_is_or_a_run_of_its_findings(self):
        self.call("record_finding", finding_id="f1", statement="s [run:r1].", run_ids=["r1"], kind="description")
        ok, out = self.call("report_result", result_id="res1", title="t", finding_ids=["f1"], figure="r1")
        self.assertTrue(ok, out)  # a run that saved no figure stands for it until the lead's figure is drawn (B1)
        self.assertEqual(self.svc.record.state()["results"]["res1"]["figure"], "r1")
        self.svc.record.append("figure_saved", {"figure_id": "r1:umap", "run_id": "r1", "paths": {"png": "u.png"},
                                                "caption": "The cells on a UMAP. Each target's density."}, rc.TOOL)
        ok, out = self.call("report_result", result_id="res1", title="t", finding_ids=["f1"], figure="r1",
                            text="It holds [finding:f1].")
        self.assertTrue(ok, out)
        self.assertEqual(self.svc.record.state()["results"]["res1"]["figure"], "r1:umap")
        report = self.call("write_report")[1]
        self.assertEqual(report["links"]["unlinked"], [])  # each sentence of the caption carries its run


class TestTheNumberCheck(unittest.TestCase):
    def test_ids_and_times_are_no_sources_ranges_keep_both_ends_and_half_rounds_up(self):
        sources = [{"run_id": "run0042-3f9", "time": "2026-10-28T01:05:19", "effect": 0.125, "fold": 2}]
        self.assertEqual(text_numbers.unsupported("42 cells; 05 genes", sources), ["42", "05"])
        self.assertEqual(text_numbers.unsupported("0.13 and a 2-3 fold change", sources), ["3"])
        self.assertEqual(text_numbers.unsupported("q below 10^-5", [{"q": 1e-5}]), [])


class TestTheServerNamesTheWayInstead(Service):
    def test_a_retired_tool_named(self):
        out = mcp_server.handle(self.svc, {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                           "params": {"name": "close_branch", "arguments": {}}}, {})
        self.assertIn("recorder", out["error"]["message"])  # its work went to the recorder (docs/adr/0051)


if __name__ == "__main__":
    unittest.main()
