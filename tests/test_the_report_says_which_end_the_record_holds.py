"""The report's top says which end the record holds (docs/adr/0049, D7): the user's enough in their words, or none,
and then the session ended with its answer written, at a cap or by a stop, or is still running; and what the record
still holds open. (The research lead's complete, the other end, left with the lead on 2026-11-20.) GSE342764's report
on e1ab825 said the analysis ended at 10:34 while the follow-ups went on to 12:38 and two results were confirmed at
12:22: an end that later work follows says so."""
import os
import tempfile
import unittest

from reading_a_report import report_text
from test_design_service import make_deposit

from pertura import record as rc
from pertura.analysis_service import AnalysisService


class TestTheEnd(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        deposit = os.path.join(self.tmp.name, "deposit")
        os.makedirs(deposit)
        make_deposit(deposit)
        self.svc = AnalysisService({"workspace": os.path.join(self.tmp.name, "ws")})
        ok, out = self.svc.call("open_project", {"data_folder": deposit})
        assert ok, out
        self.rec = self.svc.record
        self.rec.append("run_logged", {"run_id": "r1", "what": "a cell", "code": "c", "inputs": []}, rc.TOOL)
        self.rec.append("run_finished", {"run_id": "r1", "state": "ok", "output": "o"}, rc.TOOL)
        self.ok("record_finding", finding_id="f1", statement="A1 lowers G1 [run:r1].", run_ids=["r1"],
                kind="description")

    def tearDown(self):
        self.svc.close()
        self.tmp.cleanup()

    def ok(self, tool, **args):
        ok, out = self.svc.call(tool, args)
        self.assertTrue(ok, out)
        return out

    def top(self):
        return report_text(self.ok("write_report")["report"]).split("\n\n")[1]

    def test_no_end_recorded(self):
        self.assertEqual(self.top(), "No enough is recorded: the session ended with its answer written, at a cap or "
                                     "by a stop, or it is still running, and it can resume; nothing in the record is "
                                     "still open [finding f1](evidence.md#finding-f1).")
        self.svc.raise_doubt("D1", "one guide carries it", "each guide alone", finding_id="f1")
        self.assertIn("and it can resume; still open: 1 doubt without an answer (D1) [finding f1]", self.top())

    def test_the_users_enough_and_the_work_after_it(self):
        self.ok("note_reply", kind="enough", words="That is enough for today.")
        self.assertTrue(self.top().startswith("The user ended the analysis at "))
        self.assertIn('"That is enough for today"; nothing in the record is still open.', self.top())
        self.ok("open_thread", about="The user asks about A2.", raised_by="the user's words", thread_id="t9",
                user_words="and A2?")
        self.assertIn("The analysis went on after it: 1 thread opened and 0 results reported or changed, the last "
                      "at ", self.top())
        self.assertIn("[thread t9](evidence.md#thread-t9).", self.top())
        report = self.ok("write_report")
        self.assertEqual(report["links"]["unlinked"], [])  # each sentence of the end links its entry


if __name__ == "__main__":
    unittest.main()
