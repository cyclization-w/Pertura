"""What every reply carries (2026-11-07, P8 and P9). On GSE250558 run 2 the module menu was in 70% of the replies and
the research lead's threads in 12%; Claude Code refused 129 replies as too long in 26 of 102 runs, 123 of them
read_record, 116 of those a subagent's.
- P8: what is due of the main agent comes once, when it is due or changes; pertura_status gives all of it;
- P9: a read_record reply longer than READ_MAX comes in pages, the latest entries first, and a result's earlier
  versions come by their titles."""
import json
import os
import tempfile
import unittest

from test_design_service import make_deposit

from pertura import record as rc
from pertura.analysis_service import READ_MAX, AnalysisService


class Service(unittest.TestCase):
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

    def tearDown(self):
        self.svc.close()
        self.tmp.cleanup()

    def ok(self, tool, **args):
        ok, out = self.svc.call(tool, args)
        self.assertTrue(ok, out)
        return out


class TestWhatIsDueComesOnce(Service):
    def test_a_thread_waiting_is_named_once_then_again_when_the_list_changes(self):
        # an observation's thread (the research lead's threads, the case here before, are retired, 2026-11-20)
        out = self.ok("open_thread", about="a probe", raised_by="r1", thread_id="t1", observation=True)
        self.assertEqual(out["threads_waiting"]["for_workers"], ["t1"])
        out = self.ok("record_finding", finding_id="f2", statement="two [run:r1].", run_ids=["r1"],
                      kind="description")
        self.assertNotIn("threads_waiting", out)
        self.assertIn("threads_waiting", self.ok("pertura_status"))
        out = self.ok("open_thread", about="another probe", raised_by="r1", thread_id="t2", observation=True)
        self.assertEqual(out["threads_waiting"]["for_workers"], ["t1", "t2"])  # changed: given again

    def test_an_item_no_longer_due_is_given_again_when_it_is_due_again(self):
        self.ok("open_thread", about="a probe", raised_by="r1", thread_id="t1", observation=True)
        out = self.ok("thread_status", thread_id="t1", status="followed", reason="taken on my line")
        self.assertNotIn("threads_waiting", out)
        out = self.ok("thread_status", thread_id="t1", status="open", reason="back off my line")
        self.assertEqual(out["threads_waiting"]["for_workers"], ["t1"])


class TestARecordReadInPages(Service):
    def test_a_long_reading_comes_in_pages_the_latest_first(self):
        text = "x" * 2000
        for k in range(30):
            self.ok("record_finding", finding_id=f"f{k:02d}", statement=f"finding {k} {text} [run:r1].",
                    run_ids=["r1"], kind="description")
        ids = [f"f{k:02d}" for k in range(30)]
        first = self.ok("read_record", what="findings", ids=ids)
        self.assertLessEqual(len(json.dumps(first)), READ_MAX + 2000)
        self.assertGreater(first["pages"], 1)
        self.assertIn("f29", first["findings"])  # the latest first
        seen = set(first["findings"])
        for page in range(2, first["pages"] + 1):
            seen |= set(self.ok("read_record", what="findings", ids=ids, page=page)["findings"])
        self.assertEqual(seen, set(ids))
        self.assertNotIn("pages", self.ok("read_record", what="findings", ids=["f00"]))  # one that fits is whole

    def test_a_results_earlier_versions_come_by_their_titles(self):
        self.rec.append("figure_saved", {"figure_id": "fig1", "run_id": "r1", "paths": {"png": "f.png"},
                                         "caption": "c"}, rc.TOOL)
        self.ok("record_finding", finding_id="f1", statement="one [run:r1].", run_ids=["r1"], kind="description")
        for k in range(3):
            self.ok("report_result", result_id="R1", title=f"Version {k}.", finding_ids=["f1"], figure="fig1",
                    text=f"It holds {'.' * 500} [finding:f1].")
        r = self.ok("read_record", what="results", ids=["R1"])["results"]["R1"]
        self.assertEqual(r["versions"], [{"version": 1, "title": "Version 0."}, {"version": 2, "title": "Version 1."}])


if __name__ == "__main__":
    unittest.main()
