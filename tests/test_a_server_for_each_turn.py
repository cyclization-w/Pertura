"""A client without a window starts the analysis server again for each turn. One server per turn behaves like one
long server:
- a server that starts with no project opens the workspace's last one, through open_project, and the reply says so;
  with no last one, the error names the workspace's projects;
- what the replies have said is kept in the project: a run that ended between two turns is named by the next
  server, and the reply that ended the first look (the reader one level up, the threads with the workers' default)
  is not given again."""
import json
import os
import tempfile
import unittest

from test_design_service import make_deposit
from test_threads_from_the_design_and_the_first_look import any_effect

from pertura import record as rc
from pertura.analysis_service import LAST_PROJECT, PAGE_STATE, AnalysisService


class TestAServerForEachTurn(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.deposit = os.path.join(self.tmp.name, "deposit")
        os.makedirs(self.deposit)
        make_deposit(self.deposit)
        self.cfg = {"workspace": os.path.join(self.tmp.name, "ws")}
        self.servers = []

    def tearDown(self):
        for s in self.servers:
            s.close()
        self.tmp.cleanup()

    def server(self):
        s = AnalysisService(dict(self.cfg))
        self.servers.append(s)
        return s

    def ended_run(self, svc, rid, module="C.any_effect"):
        svc.record.append("run_logged", {"run_id": rid, "job_id": rid, "what": f"module {module}", "code": "c",
                                         "inputs": [], "question_id": module}, rc.TOOL)
        svc.record.append("run_finished", {"run_id": rid, "state": "done", "output": "ok"}, rc.TOOL)
        folder = os.path.join(svc._jobs_root(), rid)
        os.makedirs(folder)
        result, table = any_effect(["GATA1-i"], [("GATA1-i", 300, 12.2, 0.001)])
        with open(os.path.join(folder, "result.json"), "w", encoding="utf-8") as fh:
            json.dump(result, fh)
        table.to_csv(os.path.join(folder, "result.tsv"), sep="\t", index=False)

    def test_the_next_server_opens_the_last_project_and_says_so(self):
        first = self.server()
        ok, out = first.call("open_project", {"data_folder": self.deposit})
        self.assertTrue(ok, out)
        self.assertTrue(os.path.exists(os.path.join(self.cfg["workspace"], LAST_PROJECT)))
        later = self.server()
        ok, out = later.call("read_record", {"what": "threads"})
        self.assertTrue(ok, out)
        self.assertEqual(later.project, first.project)
        self.assertIn("this workspace's last project", out["reopened"])

    def test_with_no_last_project_the_error_names_the_workspaces_projects(self):
        first = self.server()
        self.assertTrue(first.call("open_project", {"data_folder": self.deposit})[0])
        os.remove(os.path.join(self.cfg["workspace"], LAST_PROJECT))
        ok, out = self.server().call("read_record", {"what": "threads"})
        self.assertFalse(ok)
        self.assertIn("open the project first", out["error"])
        self.assertIn(os.path.basename(first.project), out["error"])

    def test_a_run_that_ended_between_turns_is_named_once_and_the_agents_threads_go_to_the_page_state(self):
        first = self.server()
        self.assertTrue(first.call("open_project", {"data_folder": self.deposit})[0])
        for qid in ("C.states_and_composition", "C.any_effect"):
            first.record.append("question_planned", {"question_id": qid, "letter": "C", "item": qid,
                                                     "because": "always"}, rc.PLAN)
        self.ended_run(first, "r0", "C.states_and_composition")
        self.ended_run(first, "ov", "A.screen_overview")
        self.ended_run(first, "r1")
        ok, out = first.call("read_record", {"what": "findings"})
        self.assertTrue(ok, out)
        for field in ("one_level_up", "threads", "open_threads", "coverage_line", "digest_line", "screen_overview"):
            self.assertNotIn(field, out)  # a reply carries its call's result
        self.assertEqual(sorted(r["run_id"] for r in out["ended_since_your_last_call"]), ["ov", "r0", "r1"])
        ok, made = first.call("open_thread", {"about": "GATA1-i moves the whole profile", "raised_by": "r1",
                                              "matters": "the strongest distance", "tells_apart": "its genes"})
        self.assertTrue(ok, made)
        with open(os.path.join(first.project, PAGE_STATE), encoding="utf-8") as fh:
            page = json.load(fh)
        self.assertEqual([t["thread_id"] for t in page["threads"]["open"]], [made["thread_id"]])  # none from runs
        later = self.server()  # the next turn's server
        self.assertTrue(later.call("open_project", {"data_folder": self.deposit})[0])
        self.ended_run(later, "r2", "B.dose")  # ended between the two turns
        ok, out = later.call("thread_status", {"thread_id": made["thread_id"], "status": "followed",
                                               "reason": "the strongest distance"})
        self.assertTrue(ok, out)
        self.assertEqual([r["run_id"] for r in out["ended_since_your_last_call"]], ["r2"])  # the others were named


if __name__ == "__main__":
    unittest.main()
