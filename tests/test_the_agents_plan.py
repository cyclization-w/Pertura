"""The agent's own plan, held by code (docs/build_goal_subtraction_after_session8.md, S7): written after the screen's
map, each item done when a run naming it ends, changed with a reason, one line at a step's end for the next open item,
and the open items listed near the report. In session 8 a reply's single next step was followed 43 times of 69 and a
list of what was left in none of 8 turns; the session ended with nothing saying what came next."""
import json
import os
import tempfile
import unittest

from test_design_service import make_deposit

from pertura import record as rc
from pertura.analysis_service import PAGE_STATE, AnalysisService

PLAN = [{"does": "a score from the control cells' contrast between the conditions", "why": "two conditions",
         "how": "the notebook", "cards": ["P2"]},
        {"does": "each target's score in each condition", "why": "the map's strongest targets differ by condition",
         "how": "run_module E.condition_x_perturbation"},
        {"does": "the skeptic reads the results", "why": "before the user sees them", "how": "the skeptic"}]


def record(folder):
    rec = rc.Record(os.path.join(folder, "rec"))
    rec.append("run_logged", {"run_id": "ov", "job_id": "ov", "what": "module A.screen_overview", "code": "c",
                              "inputs": []}, rc.TOOL)
    return rec


class TestThePlanInTheRecord(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.rec = record(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def items(self):
        return [(i["id"], i["status"]) for i in rc.plan_items(self.rec.state())]

    def test_the_map_asks_for_the_plan_and_each_run_naming_an_item_marks_it_done(self):
        self.assertIsNone(rc.plan_next(self.rec.state()))  # the overview still runs: nothing to say yet
        self.rec.append("run_finished", {"run_id": "ov", "state": "done", "output": "ok"}, rc.TOOL)
        self.assertIsNone(rc.plan_next(self.rec.state()))  # no plan, no line: the replies ask for none (session 10)
        self.rec.append("plan_written", {"items": [{**i, "id": str(k)} for k, i in enumerate(PLAN, 1)]}, rc.AGENT)
        self.assertEqual(self.items(), [("1", "open"), ("2", "open"), ("3", "open")])
        self.assertEqual(rc.plan_next(self.rec.state()),
                         "your plan's next item 1: a score from the control cells' contrast between the conditions "
                         "(the notebook); give step 1 when you run it")
        self.rec.append("run_logged", {"run_id": "c1", "what": "the score", "code": "c", "inputs": [], "step": "1"},
                        rc.TOOL)
        self.assertEqual(self.items()[0], ("1", "open"))  # a running cell does not do it
        self.rec.append("run_finished", {"run_id": "c1", "state": "failed", "output": "error"}, rc.TOOL)
        self.assertEqual(self.items()[0], ("1", "open"))  # nor a failed one
        self.rec.append("run_logged", {"run_id": "c2", "what": "the score", "code": "c", "inputs": [], "step": "1"},
                        rc.TOOL)
        self.rec.append("run_finished", {"run_id": "c2", "state": "ok", "output": "done"}, rc.TOOL)
        self.assertEqual(self.items()[0], ("1", "done"))
        self.assertIn("next item 2", rc.plan_next(self.rec.state()))

    def test_a_plan_changes_with_its_reason_and_an_item_left_out_is_left(self):
        self.rec.append("plan_written", {"items": [{**i, "id": str(k)} for k, i in enumerate(PLAN, 1)]}, rc.AGENT)
        with self.assertRaises(rc.RecordError) as e:
            self.rec.append("plan_written", {"items": [{**PLAN[0], "id": "1"}]}, rc.AGENT)
        self.assertIn("says why it changes", str(e.exception))
        self.rec.append("plan_written", {"items": [{**PLAN[0], "id": "1", "status": "done",
                                                    "done_by": "the score from the overview's table"},
                                                   {**PLAN[2], "id": "3"},
                                                   {"id": "4", "does": "the targets acting on each other",
                                                    "why": "two regulators move the score oppositely",
                                                    "how": "the notebook, from C.genes' tables"}],
                                         "why": "the conditions agree, so item 2 adds nothing"}, rc.AGENT)
        st = self.rec.state()
        self.assertEqual(self.items(), [("1", "done"), ("3", "open"), ("4", "open"), ("2", "left")])
        self.assertEqual(next(i for i in rc.plan_items(st) if i["id"] == "2")["left_why"],
                         "the conditions agree, so item 2 adds nothing")
        self.assertEqual([v["why"] for v in st["plan"]["versions"]],
                         [None, "the conditions agree, so item 2 adds nothing"])

    def test_what_a_plan_needs(self):
        for items, words in (([], "a list of items"), ([{"id": "1", "does": "x", "why": "y"}], "item 1: needs how"),
                             ([{"id": "1", "does": "x", "why": "y", "how": "z", "status": "done"}], "done_by"),
                             ([{"id": "1", "does": "x", "why": "y", "how": "z"}] * 2, "different ids")):
            with self.assertRaises(rc.RecordError) as e:
                self.rec.append("plan_written", {"items": items}, rc.AGENT)
            self.assertIn(words, str(e.exception))

    def test_a_plan_done_points_to_the_results(self):
        self.rec.append("plan_written", {"items": [{**PLAN[0], "id": "1", "status": "done", "done_by": "a cell"}]},
                        rc.AGENT)
        self.assertIn("propose the results it supports (report_result)", rc.plan_next(self.rec.state()))


class TestThePlanInTheService(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        deposit = os.path.join(self.tmp.name, "deposit")
        os.makedirs(deposit)
        make_deposit(deposit)
        self.svc = AnalysisService({"workspace": os.path.join(self.tmp.name, "ws")})
        ok, out = self.svc.call("open_project", {"data_folder": deposit})
        assert ok, out

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

    def test_the_agent_writes_it_numbers_are_kept_and_a_new_item_takes_the_next(self):
        out = self.ok("write_plan", items=PLAN)
        self.assertEqual([i["id"] for i in out["plan"]], ["1", "2", "3"])
        self.assertNotIn("next", out)  # the reply is the plan; the status and the user's page give its next item
        self.assertIn("no item 9 in your plan", self.refused("write_plan", items=[{**PLAN[0], "id": "9"}],
                                                            why="x"))
        out = self.ok("write_plan", items=[{**PLAN[1], "id": "2"}, {"does": "the leads and their checks",
                                                                   "why": "the map's two strongest", "how": "notebook"}],
                      why="item 1 is done by the overview's table; the skeptic comes with the report")
        self.assertEqual([(i["id"], i["status"]) for i in out["plan"]],
                         [("2", "open"), ("4", "open"), ("1", "left"), ("3", "left")])
        status = self.ok("pertura_status")
        self.assertEqual([i["id"] for i in status["plan"]["items"] if i["status"] == "open"], ["2", "4"])
        self.assertIn("next item 2", status["plan"]["next"])

    def test_a_steps_end_carries_no_plan_line_and_the_page_state_holds_the_plan(self):
        self.ok("write_plan", items=PLAN)
        out = self.ok("note_reply", kind="enough", words="enough for now")
        self.assertNotIn("next", out)  # session 10: replies carry facts, not the plan's next item
        with open(os.path.join(self.svc.project, PAGE_STATE), encoding="utf-8") as fh:
            page = json.load(fh)
        self.assertEqual([i["id"] for i in page["plan"]["items"]], ["1", "2", "3"])
        self.assertIn("next item 1", page["plan"]["next"])

    def test_a_step_the_plan_does_not_hold_is_said_and_the_run_goes_ahead(self):
        step, note = self.svc._plan_step("1")
        self.assertIsNone(step)
        self.assertIn("there is no plan", note)
        self.ok("write_plan", items=PLAN)
        self.assertEqual(self.svc._plan_step("2"), ("2", None))
        self.assertEqual(self.svc._plan_step(None), (None, None))
        step, note = self.svc._plan_step("7")
        self.assertIsNone(step)
        self.assertIn("the plan's items are 1, 2, 3", note)
        self.ok("write_plan", items=[{**PLAN[0], "id": "1"}], why="the rest waits on the score")
        step, note = self.svc._plan_step("2")
        self.assertIsNone(step)
        self.assertIn("the plan left it (the rest waits on the score)", note)

    def test_the_run_tools_take_a_step(self):
        import json
        from pertura import kinds
        from pertura.analysis_service import SHOWN
        for name in ("run_code", *kinds.KINDS):  # the analyses by the field's kinds run as run_module did
            tool = next(t for t in SHOWN if t["name"] == name)
            self.assertIn("step", tool["inputSchema"]["properties"])
            self.assertNotIn("write_plan", json.dumps(tool))  # the plan is retired (2026-11-16); step stays


if __name__ == "__main__":
    unittest.main()
