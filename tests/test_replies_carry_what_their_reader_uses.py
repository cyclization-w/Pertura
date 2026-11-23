"""Each reply carries what its reader uses (docs/adr/0041, step 5; development run on GSE335887, 2026-11-06). A
reading of the record without IDs gives each entry in one line and the full entries by their IDs: the workers had
read every thread in full, 104 entries, and named 6 afterwards. The main agent's hints (the module menu, the lead's
threads waiting, the figure set's review) reach the main agent's calls, not the reads the subagents make or a worker's
cells: the workers' replies had carried "module menu due" 247 times."""
import os
import tempfile
import unittest

from test_design_service import make_deposit

from pertura import record as rc
from pertura.analysis_service import AnalysisService, in_brief

WAITING = "threads_waiting"


class TestReplies(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        deposit = os.path.join(self.tmp.name, "deposit")
        os.makedirs(deposit)
        make_deposit(deposit)
        self.svc = AnalysisService({"workspace": os.path.join(self.tmp.name, "ws")})
        ok, out = self.svc.call("open_project", {"data_folder": deposit})
        assert ok, out
        self.svc.record.append("run_logged", {"run_id": "r1", "what": "a cell", "code": "c", "inputs": []}, rc.TOOL)
        self.svc.record.append("run_finished", {"run_id": "r1", "state": "ok", "output": "o"}, rc.TOOL)
        self.ok("record_finding", finding_id="f1", statement="GATA1-i raises SPI1 [run:r1].", run_ids=["r1"],
                kind="description")
        self.ok("open_thread", about="each factor read in the others' cells", raised_by="r1",  # an observation:
                thread_id="t5", observation=True)  # the research lead's threads, the case before, are retired

    def tearDown(self):
        self.svc.close()
        self.tmp.cleanup()

    def ok(self, tool, **args):
        ok, out = self.svc.call(tool, args)
        self.assertTrue(ok, out)
        return out

    def test_a_reading_without_ids_gives_each_entry_in_one_line(self):
        out = self.ok("read_record", what="threads")
        self.assertIn("in_brief", out)
        self.assertTrue(out["threads"]["t5"].startswith("each factor read in the others' cells ["), out["threads"])
        full = self.ok("read_record", what="threads", ids=["t5"])
        self.assertEqual(full["threads"]["t5"]["about"], "each factor read in the others' cells")
        self.assertNotIn("in_brief", full)
        every = self.ok("read_record")  # every section, each entry in one line
        self.assertIn("GATA1-i raises SPI1", every["findings"]["f1"])
        self.assertTrue(every["runs"]["r1"].startswith("a cell ["))

    def test_an_entry_in_one_line_says_where_it_stands(self):
        line = in_brief({"about": "x" * 400, "status": "followed", "findings": ["f1", "f2"], "on_line": True}, "t1")
        self.assertEqual(line, "x" * 157 + "... [followed; on the main line; 2 findings]")
        self.assertEqual(in_brief({"text": "is it the batch?", "answer": None}, "d1"), "is it the batch? [open]")
        figure = {"caption": "SPI1 in GATA1-i's cells", "run_id": "r1", "reviews": [{"verdict": "fix"}]}
        self.assertEqual(in_brief(figure, "fig1"), "SPI1 in GATA1-i's cells [review fix; run r1]")
        self.assertEqual(in_brief({"what": "a cell", "state": "ok", "run_id": "r1"}, "r1"), "a cell [ok]")

    def test_the_main_agents_hints_reach_its_calls_not_the_readers(self):
        self.assertEqual(self.ok("pertura_status")[WAITING]["for_workers"], ["t5"])
        self.assertNotIn(WAITING, self.ok("read_record", what="threads"))
        self.assertNotIn(WAITING, self.ok("read_record", what="findings", ids=["f1"]))
        self.assertNotIn(WAITING, self.ok("run_code", what="a worker's cell", worker="w1", code="print(1)"))
        # given once, when due (P8): the open_thread reply above gave it; a change gives it again
        self.svc.record.append("thread_opened", {"thread_id": "t6", "about": "the factors' shared genes",
                                                 "raised_by": "r1", "kind": rc.OBSERVATION}, rc.AGENT)
        self.assertEqual(self.ok("run_code", what="the main agent's cell", code="print(2)")[WAITING]["for_workers"],
                         ["t5", "t6"])


if __name__ == "__main__":
    unittest.main()
