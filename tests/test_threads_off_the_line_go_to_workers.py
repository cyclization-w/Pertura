"""The research lead's threads off the main line go to workers. On GSE342764 the lead opened three threads off the
line from findings; no reply named them, the main agent set one aside unprobed and ran the others itself, and no
worker ran. Each thread the lead marks on or off the line is named while no run takes it, with a worker's brief; a
thread off the line set aside before something probed it is recorded, and the report lists it (docs/adr/0049, D4)."""
import os
import tempfile
import unittest

from reading_a_report import report_text
from test_design_service import make_deposit

from pertura import record as rc
from pertura.analysis_service import AnalysisService

WAITING = "threads_waiting"


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

    def tearDown(self):
        self.svc.close()
        self.tmp.cleanup()

    def call(self, tool, **args):
        return self.svc.call(tool, args)

    def ok(self, tool, **args):
        ok, out = self.call(tool, **args)
        self.assertTrue(ok, out)
        return out


class TestThreadsOffTheLine(Service):
    @unittest.skip('left the flow on 2026-11-17 (no main line, forks, maps moment, figure plan, end proposal or doubt branches: docs/adr/0051, Amendment 2); moves to tests_research with its code after 11-30')
    def test_a_thread_off_the_line_raised_by_a_finding_is_named_with_its_brief(self):
        out = self.ok("open_thread", about="What the set Ep300, Ezh1, Hells moves, and Zmynd8 opposite it.",
                      raised_by="f_prog_axis", thread_id="t4", on_line=False)
        waiting = out[WAITING]
        self.assertEqual(waiting["for_workers"], ["t4"])
        brief = waiting["briefs"]["t4"]
        self.assertEqual(brief["worker"], "t4-probe")
        self.assertIn("Zmynd8 opposite it", brief["brief"])
        self.assertIn("thread_id t4", brief["brief"])
        self.assertIn("at once", waiting["do"])  # docs/adr/0049, D6: recommended, not told

    @unittest.skip('left the flow on 2026-11-17 (no main line, forks, maps moment, figure plan, end proposal or doubt branches: docs/adr/0051, Amendment 2); moves to tests_research with its code after 11-30')
    def test_a_kind_of_conclusion_off_the_line_gets_its_kinds_brief(self):
        out = self.ok("open_thread", about="the states", raised_by="the lead", thread_id="t5", on_line=False,
                      reads="states")
        self.assertEqual(out[WAITING]["briefs"]["t5"]["worker"], "t5-states")

    @unittest.skip('left the flow on 2026-11-17 (no main line, forks, maps moment, figure plan, end proposal or doubt branches: docs/adr/0051, Amendment 2); moves to tests_research with its code after 11-30')
    def test_a_thread_on_the_line_is_the_main_agents(self):
        out = self.ok("open_thread", about="the next step", raised_by="f1", thread_id="t2", on_line=True)
        self.assertNotIn("for_workers", out[WAITING])
        self.assertEqual(out[WAITING]["yours"], ["t2"])
        do = out[WAITING]["do"]
        self.assertIn("say in your reply what it found", do)  # the recorder records it (docs/adr/0051)
        self.assertIn("whether the thread is explained", do)  # so it ends
        self.assertLess(do.index("an analysis tool"), do.index("run_code"))  # the tools first (docs/adr/0050, D4)

    def test_a_thread_off_the_line_set_aside_unprobed_is_recorded_and_listed(self):
        """docs/adr/0049, D4: recorded, not refused, and the report lists it."""
        self.ok("open_thread", about="A side question on A2.", raised_by="f1", thread_id="t4", on_line=False)
        out = self.ok("thread_status", thread_id="t4", status="set aside", reason="breadth only")
        self.assertIn("set aside before anything probed it", out["listed"])
        self.assertEqual(self.rec.state()["threads"]["t4"]["status"], "set aside")
        text = report_text(self.ok("write_report")["report"])
        self.assertIn("1 thread off the main line set aside before anything probed it: A side question on A2 "
                      "(breadth only)", text)
        self.ok("open_thread", about="A second side question.", raised_by="f1", thread_id="t6", on_line=False)
        self.rec.append("run_logged", {"run_id": "r1", "what": "a probe", "code": "c", "inputs": [],
                                       "thread_id": "t6", "worker": "t6-probe"}, rc.TOOL)
        out = self.ok("thread_status", thread_id="t6", status="set aside",
                      reason="the probe found nothing past its line")
        self.assertNotIn("listed", out)  # probed: an ordinary end

    def test_a_thread_on_the_line_can_be_set_aside_with_its_reason(self):
        self.ok("open_thread", about="the next step", raised_by="f1", thread_id="t2", on_line=True)
        self.ok("thread_status", thread_id="t2", status="set aside", reason="the user asked for another question")


if __name__ == "__main__":
    unittest.main()
