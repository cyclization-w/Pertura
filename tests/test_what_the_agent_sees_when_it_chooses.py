"""What the agent sees when it chooses (the user's decision F4, 2026-10-30). In development session 7 the agent
checked one finding 13 times while eight modules that apply never ran, two measured signals had no answer, and the
reader one level up was never launched. Now:
- each step's reply carries a line from code: the modules that apply and have not run on this build, the measured
  signals and the threads one level up with no answer, and the findings whose choices have no alternative tried;
  pertura_status carries the same as lists; the plugin's hooks give the line when the agent next chooses;
- the replies at the reader one level up's three moments name its launch (the first look's end, a result proposed
  for the report, the user's enough);
- the threads it opens, and the first look's about the whole screen, are listed in the report while unanswered.
Nothing of it blocks, and the line names no next step."""
import json
import os
import sys
import tempfile
import unittest
import unittest.mock

from test_design_service import make_deposit
from test_threads_from_the_design_and_the_first_look import any_effect

from pertura import record as rc
from pertura import results_report, turn_end
from pertura.analysis_service import PAGE_STATE, AnalysisService

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "plugin", "status"))
import hooks  # noqa: E402
import job_check  # noqa: E402
import status_file as sf  # noqa: E402

def state(**extra):
    st = rc.empty()
    st["runs"] = {
        "r1": {"what": "module C.genes", "state": "done", "build": "b1", "job_id": "r1"},
        "r2": {"what": "module E.similar_targets", "state": "running", "build": "b1", "job_id": "r2"},
        "r3": {"what": "module C.within_states", "state": "failed", "build": "b1", "job_id": "r3"},
        "r4": {"what": "module C.programs", "state": "done", "build": "b0", "job_id": "r4"},  # an earlier build
        "r5": {"what": "a notebook cell", "state": "done", "build": "b1"},
        "r7": {"what": 'module D.cutting_response with {"targets": ["T1"]}', "state": "done", "build": "b1",
               "job_id": "r7"}}  # on some targets only: not the screen as a whole
    st["build"] = "b1"
    st["builds"] = {"b0": {"seq": 1, "changed": True, "previous": None},
                    "b1": {"seq": 2, "changed": True, "previous": "b0"}}
    st.update(extra)
    return st


def thread(kind, raised_by, status="open", changes=()):
    return {"kind": kind, "raised_by": raised_by, "status": status, "changes": list(changes), "about": f"{kind} thread"}


class TestTheCoverage(unittest.TestCase):
    def test_the_runs_states_on_this_build_over_every_module(self):
        cov = turn_end.coverage(state())
        self.assertEqual(cov["running"], ["E.similar_targets"])
        self.assertEqual(cov["failed"], ["C.within_states"])  # C.programs ran on an earlier build: not listed
        self.assertNotIn("applies_not_run", cov)  # what comes next is the agent's plan

    def test_signals_and_threads_one_level_up_without_an_answer(self):
        st = state(threads={
            "t1": thread("replicates", "first look"), "t2": thread("guides", "first look", "set aside",
                                                                  [{"status": "set aside"}]),
            "t3": thread("past the calibration", {"run": "r9"}), "t4": thread("state shift", {"run": "r9"}),
            "t5": thread("opened", "one level up"), "t6": thread("opened", "the user's words"),
            "t7": thread("past the calibration", {"run": "r9"}, "followed", [{"status": "followed"}])})
        cov = turn_end.coverage(st)
        self.assertEqual([t["thread_id"] for t in cov["unanswered"]], ["t5", "t1", "t3", "t4"])
        text = turn_end.line(cov)
        self.assertIn("Measured signals with no answer in the record: t1 (the replicates' agreement), 1 past the "
                      "control groups' line and 1 state shifts.", text)
        self.assertIn("Threads one level up with no answer: t5.", text)

    def test_the_line_is_information_short_and_empty_when_nothing_is_listed(self):
        st = state()
        st["runs"] = {f"x{i}": {"what": f"module M{i}", "state": "failed", "build": "b1", "job_id": f"x{i}"}
                      for i in range(9)}
        text = turn_end.line(turn_end.coverage(st))
        self.assertIn("Modules whose runs on this build failed: M0, M1, M2, M3, M4 and 4 more.", text)
        for word in ("must", "should", "next", "Run "):
            self.assertNotIn(word, text)
        st["runs"] = {"r1": {"what": "module C.genes", "state": "done", "build": "b1", "job_id": "r1"}}
        self.assertEqual(turn_end.line(turn_end.coverage(st)), "")

    def test_between_the_build_and_the_users_answer_only_the_wait(self):
        cov = turn_end.coverage(state(), waits_on_user=["data.qc"])
        self.assertEqual((cov["failed"], cov["waits_on_user"]), ([], ["data.qc"]))
        self.assertEqual(turn_end.line(cov), "The first look waits on the user's answer to data.qc.")

    def test_a_reply_keeps_each_list_short(self):
        kept = turn_end.kept({"failed": list("abcdefgh"), "running": []}, n=3)
        self.assertEqual((kept["failed"], kept["failed_more"], kept["running"]), (["a", "b", "c"], 5, []))


class TestTheReportListsThreadsLeftWithoutAnAnswer(unittest.TestCase):
    def test_the_readers_threads_and_the_screens_signals_are_open_items_until_answered(self):
        d = tempfile.mkdtemp()
        rec = rc.Record(os.path.join(d, "record"))
        rec.append("thread_opened", {"thread_id": "u1", "about": "Dimt1 and Ndufv2 move cells the same way",
                                     "raised_by": "one level up"}, rc.AGENT)
        rec.append("thread_opened", {"thread_id": "s1", "about": "the mice agree at median r 0.01",
                                     "raised_by": "first look", "kind": "replicates"}, rc.TOOL)
        rec.append("thread_opened", {"thread_id": "m1", "about": "Hdac1 past the line", "raised_by": {"run": "r1"},
                                     "kind": "past the calibration"}, rc.TOOL)
        items = [i for i in rec.critic() if i["kind"] == "thread without an answer"]
        self.assertEqual([i["id"] for i in items], ["u1", "s1"])  # a target's measured thread is the line's only
        lines = results_report._open_questions(rec.state(), rec.critic())
        said = [x for x in lines if "left without an answer" in x]
        self.assertEqual(len(said), 1)
        self.assertIn("[thread:u1]", said[0])
        self.assertIn("[thread:s1]", said[0])
        rec.append("thread_changed", {"thread_id": "u1", "status": "set aside",
                                      "reason": "15 and 27 cells: too few to test per mouse"}, rc.AGENT)
        self.assertEqual([i["id"] for i in rec.critic() if i["kind"] == "thread without an answer"], ["s1"])


class TestTheRepliesCarryTheirResultOnly(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        deposit = os.path.join(self.tmp.name, "deposit")
        os.makedirs(deposit)
        make_deposit(deposit)
        self.svc = AnalysisService({"workspace": os.path.join(self.tmp.name, "ws")})
        ok, out = self.svc.call("open_project", {"data_folder": deposit})
        assert ok, out
        rec = self.svc.record
        for qid in ("C.states_and_composition", "C.any_effect", "C.programs"):
            rec.append("question_planned", {"question_id": qid, "letter": "C", "item": qid, "because": "always"},
                       rc.PLAN)
        for rid, what in (("r0", "C.states_and_composition"), ("r1", "C.any_effect"), ("ov", "A.screen_overview")):
            rec.append("run_logged", {"run_id": rid, "job_id": rid, "what": f"module {what}", "code": "c",
                                      "inputs": [], "question_id": what}, rc.TOOL)
            rec.append("run_finished", {"run_id": rid, "state": "done", "output": "ok"}, rc.TOOL)
        folder = os.path.join(self.svc._jobs_root(), "r1")
        os.makedirs(folder)
        result, table = any_effect(["GATA1-i"], [("GATA1-i", 300, 12.2, 0.001)])
        with open(os.path.join(folder, "result.json"), "w", encoding="utf-8") as fh:
            json.dump(result, fh)
        table.to_csv(os.path.join(folder, "result.tsv"), sep="\t", index=False)
        os.makedirs(self.svc._screen_dir(), exist_ok=True)
        with open(os.path.join(self.svc._screen_dir(), "screen.json"), "w", encoding="utf-8") as fh:
            json.dump({}, fh)

    def tearDown(self):
        self.svc.close()
        self.tmp.cleanup()

    def call(self, tool, **args):
        ok, out = self.svc.call(tool, args)
        self.assertTrue(ok, out)
        return out

    def test_no_reply_names_the_reader_or_carries_the_line_and_pertura_status_gives_it(self):
        first = self.call("read_record", what="findings")
        tid = self.call("open_thread", about="GATA1-i moves the whole profile (distance 12.2)", raised_by="r1",
                        matters="the strongest distance", tells_apart="its genes")["thread_id"]
        again = self.call("thread_status", thread_id=tid, status="followed", reason="the strongest distance")
        for out in (first, again):
            for field in ("one_level_up", "coverage_line", "threads", "open_threads", "digest_line"):
                self.assertNotIn(field, out)
        status = self.call("pertura_status")
        self.assertEqual(status["coverage"]["unanswered"], [])  # the agent's own thread is not a signal
        with open(os.path.join(self.svc.project, PAGE_STATE), encoding="utf-8") as fh:
            page = json.load(fh)  # what the user's page shows beside the replies
        self.assertEqual((page["coverage_line"], page["coverage"]), (status["coverage_line"], status["coverage"]))
        self.assertEqual(page["digest"]["line"], status["digest"]["line"])

    def test_while_the_cells_wait_on_the_user_the_line_says_only_that(self):
        self.svc._cells_wait = lambda: "the cells' quality rule (data.qc) for this build"
        status = self.call("pertura_status")
        self.assertEqual(status["coverage"]["failed"], [])
        self.assertEqual(status["coverage_line"], "The first look waits on the user's answer to the cells' quality "
                                                  "rule (data.qc) for this build.")

    def test_a_steps_end_carries_no_plan_line(self):
        """Session 10: 39 replies asked for the plan and the agent followed none; a reply carries facts only (the
        retired lines: pertura_research/retired/plan_lines_in_replies.py)."""
        line = "your plan's next item 2: the target's programme (the notebook)"
        with unittest.mock.patch("pertura.record.plan_next", return_value=line, create=True):
            self.assertNotIn("next", self.call("note_reply", kind="enough", words="enough"))
            self.svc.record.append("figure_saved", {"figure_id": "r1:figure", "run_id": "r1",
                                                    "paths": {"png": "x.png"}, "caption": "c"}, rc.TOOL)
            self.svc.record.append("finding_recorded", {"finding_id": "f1", "run_ids": ["r1"],
                                                        "statement": "GATA1-i's cells differ"}, rc.AGENT)
            self.svc.record.append("finding_recorded", {"finding_id": "f0", "run_ids": ["r0"],  # survey first
                                                        "statement": "the screen's states"}, rc.AGENT)
            self.svc.record.append("finding_recorded", {"finding_id": "f2", "run_ids": ["ov"],  # every view is read
                                                        "statement": "no target stands out"}, rc.AGENT)
            out = self.call("report_result", result_id="res1", title="GATA1-i moves cells", finding_ids=["f1"],
                            figure="r1:figure")
            self.assertIn("confirm or drop it", out["next"])  # the user decides a result: that line stays
            self.assertNotIn("plan_open", out)

    def test_the_users_enough_asks_for_the_report_and_names_no_reader(self):
        out = self.call("note_reply", kind="enough", words="that is enough for now")
        self.assertIn("write_report", out["enough"])
        self.assertNotIn("one_level_up", out)

    def test_a_proposed_result_says_its_next_step_and_names_no_reader(self):
        self.svc.record.append("figure_saved", {"figure_id": "r1:figure", "run_id": "r1", "paths": {"png": "x.png"},
                                                "caption": "c"}, rc.TOOL)
        self.svc.record.append("finding_recorded", {"finding_id": "f1", "run_ids": ["r1"],
                                                    "statement": "GATA1-i's cells differ"}, rc.AGENT)
        self.svc.record.append("finding_recorded", {"finding_id": "f0", "run_ids": ["r0"],  # survey first
                                                    "statement": "the screen's states"}, rc.AGENT)
        self.svc.record.append("finding_recorded", {"finding_id": "f2", "run_ids": ["ov"],  # every view is read
                                                    "statement": "no target stands out"}, rc.AGENT)
        out = self.call("report_result", result_id="res1", title="GATA1-i moves cells", finding_ids=["f1"],
                        figure="r1:figure")
        self.assertIn("confirm or drop it", out["next"])  # the step the user takes
        self.assertNotIn("one_level_up", out)


class TestThePluginGivesTheLine(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.st = sf.empty("/w/screen-1a2b3c4d")
        self.st["project"] = "/w/screen-1a2b3c4d"

    def test_the_status_file_keeps_the_line_and_the_lists(self):
        sf.apply_call(self.st, "pertura_status", {}, {"project": "/w/screen-1a2b3c4d", "coverage_line": "Running: C.genes.",
                                                      "coverage": {"running": ["C.genes"]}})
        self.assertEqual((self.st["coverage_line"], self.st["coverage"]), ("Running: C.genes.", {"running": ["C.genes"]}))
        sf.apply_call(self.st, "thread_status", {}, {"thread_id": "t1", "status": "followed",
                                                     "coverage_line": "Running: C.genes and C.programs."})
        self.assertEqual(self.st["coverage_line"], "Running: C.genes and C.programs.")
        sf.apply_call(self.st, "read_record", {}, {"findings": []})  # a reply without the line keeps it
        self.assertEqual(self.st["coverage_line"], "Running: C.genes and C.programs.")

    def test_the_prompt_hook_sends_no_line(self):
        self.st["coverage_line"] = "Modules that apply and have not run on this build: C.programs."
        key = sf.project_key(self.st["project"])
        sf.save(key, self.st, self.root)
        sf.write_json(sf.session_path("s1", self.root), {"project": key})
        self.assertIsNone(hooks.user_prompt({"session_id": "s1"}, self.root, now=1000.0))  # the user is not away
        self.assertFalse(hasattr(hooks, "with_coverage"))

    def _a_project_with_its_page_state(self, seq=7):
        project = os.path.join(self.root, "ws", "screen-1a2b3c4d")
        os.makedirs(project)
        self.st["project"] = project
        key = sf.project_key(project)
        sf.save(key, self.st, self.root)
        sf.write_json(sf.session_path("s1", self.root), {"project": key})
        state = {"project": project, "seq": seq, "digest": {"line": "on t1: GATA1-i's distance - running: C.genes"},
                 "threads": {"open": [{"thread_id": "t1", "about": "GATA1-i's distance", "status": "open"}]},
                 "coverage": {"running": ["C.genes"]}, "coverage_line": "Running: C.genes.",
                 "record": {"findings": {"F1": {"targets": ["A"], "checks": {"A": {"lead": {"outcome": "agrees"}}}}},
                            "results": {}, "threads": {}, "lead": None}}
        return project, key, state

    def test_the_turns_end_folds_the_page_state_into_the_page(self):
        project, key, state = self._a_project_with_its_page_state()
        with open(os.path.join(project, PAGE_STATE), "w", encoding="utf-8") as fh:
            json.dump(state, fh)
        hooks.page_state_at_turn_end({"session_id": "s1"}, self.root, start=lambda *a: self.fail("no ssh here"))
        st = sf.read_json(sf.project_path(key, self.root))
        self.assertEqual((st["digest_line"], st["coverage_line"]), (state["digest"]["line"], "Running: C.genes."))
        self.assertIn("t1", st["page"]["threads"])
        self.assertEqual(st["page"]["line"], state["digest"]["line"])
        self.assertEqual(st["record"], state["record"])  # the record as the workbench reads it, kept as it came
        self.assertIs(sf.fold_page_state(key, {**state, "project": "/another/project"}, self.root), None)

    def test_the_job_checks_round_reads_the_page_state_with_the_jobs(self):
        project, key, state = self._a_project_with_its_page_state()
        st = sf.read_json(sf.project_path(key, self.root))
        st["project"] = "/w/screen-1a2b3c4d"  # on the server
        st["jobs"] = {"r9": {"run_id": "r9", "state": "running", "started": 0, "ended": None}}
        sf.save(key, st, self.root)
        sf.write_json(sf.session_path("s1", self.root), {"project": key, "updated": 1000.0})
        sent = []

        def ssh(host, text):
            sent.append(text)
            return "r9 exit {\"exit_code\": 0}\npage_state " + json.dumps({**state, "project": "/w/screen-1a2b3c4d"})
        self.assertEqual(job_check.check_once("analysis-host", self.root, ssh=ssh, now=1010.0), 0)
        self.assertIn("page_state.json", sent[0])
        st = sf.read_json(sf.project_path(key, self.root))
        self.assertEqual((st["jobs"]["r9"]["state"], st["coverage_line"]), ("done", "Running: C.genes."))
        self.assertIn("t1", st["page"]["threads"])


if __name__ == "__main__":
    unittest.main()


class TestTheReviewsCases(unittest.TestCase):
    def test_a_stopped_module_reads_as_stopped_and_a_failed_one_as_failed(self):
        st = state()
        st["runs"]["r6"] = {"what": "module D.cutting_response", "state": "stopped", "build": "b1", "job_id": "r6"}
        cov = turn_end.coverage(st)
        self.assertEqual((cov["failed"], cov["stopped"]), (["C.within_states"], ["D.cutting_response"]))
        self.assertIn("Modules whose runs on this build were stopped: D.cutting_response.", turn_end.line(cov))

    def test_the_cells_overviews_threads_reach_the_line_and_the_designs_do_not(self):
        st = state(threads={"c1": thread("cells", {"cells_overview": "C03"}),
                            "d1": thread("two modes", {"design": "CRISPRa and CRISPRi"})})
        self.assertEqual([t["thread_id"] for t in turn_end.coverage(st)["unanswered"]], ["c1"])

    def test_the_report_names_a_thread_once_counts_past_three_and_keeps_the_further_items(self):
        d = tempfile.mkdtemp()
        rec = rc.Record(os.path.join(d, "record"))
        for i in range(5):
            rec.append("thread_opened", {"thread_id": f"u{i}", "about": f"reading {i}", "raised_by": "one level up"},
                       rc.AGENT)
        rec.append("run_logged", {"run_id": "r1", "what": "module C.genes", "code": "", "inputs": []}, rc.TOOL)
        lines = results_report._open_questions(rec.state(), rec.critic())
        self.assertEqual(sum("[thread:u0]" in x for x in lines), 1)
        said = next(x for x in lines if "left without an answer" in x)
        self.assertIn("and 2 more, in the appendix", said)
        self.assertTrue(any("further item" in x for x in lines))
        self.assertLessEqual(len(lines), 5)
