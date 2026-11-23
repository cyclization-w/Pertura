"""The next check where the analysis ends (docs/adr/0045, amendment 1; docs/build/next_checks_for_weak_results_plan_2026-11-10.md,
version 2). In 13 development runs a doubt stated as a limit closed checks that were never run (33, at least 9 saying
so), and the lead's coverage left out every limit, so a check skipped "for now" was out of its sight when it said
complete. Now:
- a limit says what leaves it: the design or a tool; a check left for time is no limit (on 01f3de3 'budget' closed
  checks of seconds, with no time or spend shown): it stays still open, in the lead's coverage and the critic's list;
- the lead's coverage, a thread set aside and a limit stated name one check, the one most likely to change the main
  result, from the record alone; it lists and never blocks;
- "holds, narrowed" is an answer, with its run;
- a call of the same comparison gets the run that made it, whoever started it; one on another cell slice is another
  comparison;
- a weak result carries its module's own ways to mislead, with their checks."""
import json
import os
import tempfile
import unittest
from unittest import mock

from test_design_service import make_deposit

from pertura import record as rc, results_report
from pertura.analysis_service import AnalysisService

KEY = "the_check_most_likely_to_change_the_main_result"


class TestTheNextCheck(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        deposit = os.path.join(self.tmp.name, "deposit")
        os.makedirs(deposit)
        make_deposit(deposit)
        self.svc = AnalysisService({"workspace": os.path.join(self.tmp.name, "ws")})
        ok, out = self.svc.call("open_project", {"data_folder": deposit})
        assert ok, out
        self.rec = self.svc.record
        for rid in ("r1", "r2", "r3"):
            self.rec.append("run_logged", {"run_id": rid, "what": "a cell", "code": "c", "inputs": []}, rc.TOOL)
            self.rec.append("run_finished", {"run_id": rid, "state": "ok", "output": "o"}, rc.TOOL)
        self.rec.append("figure_saved", {"figure_id": "fig1", "run_id": "r1", "paths": {"png": "f.png"},
                                         "caption": "c"}, rc.TOOL)
        self.ok("record_finding", finding_id="f1", statement="one [run:r1].", run_ids=["r1"], kind="description")
        self.ok("report_result", result_id="R1", title="t.", finding_ids=["f1"], figure="fig1", text="[finding:f1].")

    def tearDown(self):
        self.svc.close()
        self.tmp.cleanup()

    def ok(self, tool, **args):
        ok, out = self.svc.call(tool, args)
        self.assertTrue(ok, out)
        return out

    def refused(self, tool, **args):
        ok, out = self.svc.call(tool, args)
        self.assertFalse(ok, out)
        return out["error"]

    def opened(self):
        """The open questions of the record as it stands (2026-11-16), where the doubts no run has answered went."""
        return self.coverage().get("the_record_as_it_stands", {}).get("open", {})

    def coverage(self):
        return self.ok("read_record", what="coverage")["coverage"]

    def test_a_limit_says_what_leaves_it(self):
        self.ok("raise_doubt", doubt_id="d1", finding_id="f1", text="depth", would_change="gone at matched depth")
        self.assertIn("says what leaves it", self.refused("answer_doubt", doubt_id="d1", outcome="stated as a limit",
                                                          reason="not run"))
        self.assertIn("limit's kind", self.refused("answer_doubt", doubt_id="d1", outcome="holds", reason="x",
                                                   run_ids=["r2"], limit="design"))
        self.assertIn("design, tool", self.refused("answer_doubt", doubt_id="d1", outcome="stated as a limit",
                                                   reason="depth matching not run", limit="budget"))
        self.ok("raise_doubt", doubt_id="d2", finding_id="f1", text="batch", would_change="gone within a batch")
        out = self.ok("answer_doubt", doubt_id="d1", outcome="stated as a limit", reason="one batch per condition",
                      limit="design")
        self.assertEqual(out[KEY]["doubt_id"], "d2")  # the check still owed, not the limit just stated
        self.assertEqual(self.rec.state()["doubts"]["d1"]["answer"]["limit"], "design")

    def test_a_check_left_for_time_stays_in_the_leads_sight_and_a_design_limit_does_not(self):
        """GPT's case: the lead's complete (record_review) writes the report, so the main agent may never call
        write_report; the hint stands in what the lead reads before its verdict."""
        self.ok("raise_doubt", doubt_id="d1", finding_id="f1", text="depth", would_change="gone at matched depth")
        self.ok("raise_doubt", doubt_id="d2", finding_id="f1", text="batch", would_change="gone within a batch")
        self.ok("answer_doubt", doubt_id="d1", outcome="stated as a limit", reason="one batch per condition",
                limit="design")
        self.assertIn("d2", self.opened()["doubts_no_run_has_answered"])  # not answered
        self.ok("answer_doubt", doubt_id="d2", outcome="still open", reason="depth matching not run yet")
        cov = self.coverage()
        self.assertNotIn(KEY, cov)  # the coverage leads with the observations not yet explained (2026-11-11)
        self.assertEqual(self.opened()["doubts_no_run_has_answered"], ["d2"])  # the design's limit is no run owed
        self.assertEqual([i["id"] for i in self.rec.critic() if i["kind"] == "doubt still open"], ["d2"])
        self.ok("answer_doubt", doubt_id="d2", outcome="holds", reason="the same within each batch", run_ids=["r2"])
        self.assertIn("d2", self.opened()["doubts_no_run_has_answered"])  # r2 was made before the doubt
        self.rec.append("run_logged", {"run_id": "r7", "what": "within each batch", "code": "c", "inputs": []},
                        rc.TOOL)
        self.ok("answer_doubt", doubt_id="d2", outcome="holds", reason="the same within each batch", run_ids=["r7"])
        self.assertNotIn("d2", self.opened().get("doubts_no_run_has_answered", []))

    def test_a_thread_set_aside_names_the_check(self):
        self.ok("raise_doubt", doubt_id="d1", finding_id="f1", text="depth", would_change="gone at matched depth")
        self.ok("open_thread", thread_id="t1", about="a probe", raised_by="r1")
        out = self.ok("thread_status", thread_id="t1", status="set aside", reason="enough")
        self.assertEqual(out[KEY]["doubt_id"], "d1")

    def test_holds_narrowed_is_an_answer_with_its_run_and_the_report_says_each_kind(self):
        self.ok("raise_doubt", doubt_id="d1", finding_id="f1", text="one guide", would_change="gone in guide 2")
        out = self.ok("answer_doubt", doubt_id="d1", outcome="holds, narrowed", reason="two of three guides")
        # no run demanded (GSE190604, a9510b4: the refusal was met with runs from before the doubt); the reply gives
        # the doubt's test back with the route to a worker (GSE267740, 16ea437: 7 testable doubts answered in 6 s
        # with no run), and the report says it with the test
        self.assertEqual(out["its_test"], "gone in guide 2")
        self.assertIn("open_thread with this doubt_id", out["to_test_it"])
        self.assertTrue(any("with no run made after the doubt; its test: gone in guide 2" in x
                            for x in results_report.doubt_lines(self.rec.state())))
        self.rec.append("run_logged", {"run_id": "r6", "what": "each guide", "code": "c", "inputs": []}, rc.TOOL)
        out = self.ok("answer_doubt", doubt_id="d1", outcome="holds, narrowed", reason="two of three guides",
                      run_ids=["r6"])
        self.assertIn("reword f1", out["reword"])
        self.assertNotIn("its_test", out)  # tested by a run made after the doubt
        self.ok("raise_doubt", doubt_id="d2", finding_id="f1", text="depth", would_change="gone at matched depth")
        self.ok("answer_doubt", doubt_id="d2", outcome="stated as a limit", reason="one depth", limit="design")
        lines = results_report.doubt_lines(self.rec.state())
        self.assertTrue(any("Answered: holds, narrowed" in x for x in lines), lines)
        self.assertTrue(any("Answered: stated as a limit (the design cannot answer it)" in x for x in lines), lines)

    def test_the_report_names_the_doubts_without_an_answer(self):
        """GSE250558's replay: a finding answered d-f1-composition, no answer was recorded, and the lead's next check
        named it at every review."""
        self.ok("raise_doubt", doubt_id="d1", finding_id="f1", text="state", would_change="gone within a state")
        self.assertTrue(self.ok("write_report")["doubts_without_an_answer"].startswith("d1: "))
        self.ok("answer_doubt", doubt_id="d1", outcome="holds", reason="the same within each state", run_ids=["r2"])
        self.assertNotIn("doubts_without_an_answer", self.ok("write_report"))

    def test_an_answer_recorded_before_reads_as_it_did(self):
        self.ok("raise_doubt", doubt_id="d1", finding_id="f1", text="depth", would_change="gone at matched depth")
        self.rec.append("doubt_answered", {"doubt_id": "d1", "outcome": "stated as a limit", "reason": "old"},
                        rc.AGENT)  # no kind: a record from before
        self.assertNotIn("doubts_no_run_has_answered", self.opened())  # none: an empty key is left out
        self.assertNotIn("d1", [i["id"] for i in self.rec.critic()])


class TestTheSameComparison(unittest.TestCase):
    """GPT's case: a run of the same module on another slice is not the comparison asked for."""
    def setUp(self):
        from test_the_survey_beside_the_agents_line import ready_service
        self.tmp = tempfile.TemporaryDirectory()
        self.svc = ready_service(self.tmp.name)
        self.rec = self.svc.record

    def tearDown(self):
        self.svc.close()
        self.tmp.cleanup()

    def log(self, module, given=None, done=True):
        rid = f"run{len(self.rec.state()['runs']) + 1}"
        self.rec.append("run_logged", {"run_id": rid, "job_id": rid, "what": f"module {module}" + (
            f" with {json.dumps(given)}" if given else ""), "code": "c", "inputs": []}, rc.TOOL)
        if done:
            self.rec.append("run_finished", {"run_id": rid, "state": "done", "output": ""}, rc.TOOL)
        return rid

    def test_the_same_comparison_is_given_back_and_another_slice_is_not(self):
        mine = self.log("E.similar_targets")  # the agent's own, done
        other = self.log("C.genes", {"cells": "condition_a-1"})
        with mock.patch.object(self.rec, "start_run", side_effect=AssertionError("a second job")), \
                mock.patch.object(self.svc, "module_result", side_effect=lambda r, w=0: {"run_id": r, "state": "done"}):
            out = self.svc.run_module("E.similar_targets", wait=0)
        self.assertEqual(out["run_id"], mine)
        self.assertIn("given back", out["from_an_earlier_run"])
        self.assertIsNone(self.svc._same_comparison("C.genes", {}))  # the slice's run is another comparison
        self.assertEqual(self.svc._same_comparison("C.genes", {"cells": "condition_a-1"}), other)


class TestAWeakResult(unittest.TestCase):
    def test_a_result_not_found_carries_its_modules_ways_to_mislead(self):
        from pertura.question_modules import REGISTRY
        from test_the_survey_beside_the_agents_line import ready_service
        with tempfile.TemporaryDirectory() as d:
            svc = ready_service(d)
            try:
                rid = "run1"
                svc.record.append("run_logged", {"run_id": rid, "job_id": rid, "what": "module B.efficacy",
                                                 "code": "c", "inputs": []}, rc.TOOL)
                svc.record.append("run_finished", {"run_id": rid, "state": "done", "output": ""}, rc.TOOL)
                folder = os.path.join(svc._jobs_root(), rid)
                os.makedirs(folder, exist_ok=True)
                with open(os.path.join(folder, "result.json"), "w", encoding="utf-8") as fh:
                    json.dump({"status": "not_enough_data", "summary": "no guide with 10 cells"}, fh)
                out = svc.module_result(rid)
            finally:
                svc.close()
        ways = REGISTRY["B.efficacy"].contract.fails_when
        self.assertEqual(out["if_it_misleads"], [f"{how}: {check}" for how, check in ways])


if __name__ == "__main__":
    unittest.main()
