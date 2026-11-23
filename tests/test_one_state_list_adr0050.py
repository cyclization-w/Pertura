"""ADR 0050, D2: one state list. Every item due of the main agent is computed in one place (the central layer) and
reaches the main agent only, once per change; a tool's reply carries only feedback on its own call.

On run A (GSE342764, 3bae44e) the research lead's due notice rode on record_finding's reply. Workers recorded, so 18
of 23 notices reached workers, and the lead reviewed once in two hours. Each main-agent reply now also names what is
pending; the status file keeps that list and the Stop hook reads it, so the push no longer depends on the output
style's "Waiting on" line, which was off in every headless run."""
import os
import sys
import unittest

from test_results_from_findings import TEXT, Service
from test_the_agent_works_on_until_the_user_is_needed import NOW, transcript

from pertura import record as rc
from pertura.analysis_service import AGENT_ARG

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "plugin", "status"))
import hooks  # noqa: E402
import status_file as sf  # noqa: E402


class TestOneStateList(Service):
    def as_worker(self, tool, **args):
        ok, out = self.svc.call(tool, {**args, AGENT_ARG: "pertura:worker"})
        self.assertTrue(ok, out)
        return out

    def test_a_workers_reply_carries_no_due_item_and_the_main_agent_keeps_it_pending(self):
        # a doubt to answer (the research lead's review, the item this was shown with, is retired, 2026-11-20): a main
        # agent's reply gives it once, a worker's never, and it stays pending until it is answered
        self.svc.raise_doubt("D1", "one guide carries it", "each guide alone", finding_id="f1")
        self.assertIn("doubts_to_answer", self.ok("record_finding", finding_id="f0", statement="x [run:r1].",
                                                  run_ids=["r1"], kind="description"))
        out = self.as_worker("record_finding", finding_id="t1-f3", statement="a worker's [run:r2].", run_ids=["r2"],
                             kind="description")
        self.assertNotIn("doubts_to_answer", out)
        self.assertNotIn("pending", out)
        out = self.ok("record_finding", finding_id="f4", statement="the main agent's [run:r2].", run_ids=["r2"],
                      kind="description")
        self.assertNotIn("doubts_to_answer", out)  # given once, unchanged since
        self.assertIn("doubts to answer", out["pending"]["main agent"])  # still open, named on every reply
        self.svc.record.append("doubt_answered", {"doubt_id": "D1", "outcome": "holds", "reason": "each guide agrees",
                                                  "run_ids": ["r2"]}, rc.AGENT)
        self.assertNotIn("doubts to answer", self.ok("record_finding", finding_id="f9", statement="n [run:r2].",
                                                     run_ids=["r2"], kind="description")["pending"].get(
                                                         "main agent", []))

    def test_the_recording_tools_carry_no_copies_of_due_items(self):
        out = self.ok("record_finding", finding_id="f5", statement="differs [run:r1].", run_ids=["r1"],
                      differs={"text": "the paper saw none", "source": "the paper"}, kind="description")
        self.assertNotIn("skeptic_due", out)
        out = self.ok("report_result", result_id="res1", title="GATA1 knockdown raises SPI1", finding_ids=["f1"],
                      figure="fig1", text=TEXT)
        self.assertNotIn("due", out)
        self.assertNotIn("skeptic_due_on_the_results", out)  # the recorder starts the skeptic (docs/adr/0051, D3)
        self.assertNotIn("skeptic_due", self.ok("note_reply", kind="keep", words="keep it", finding_id="f1"))
        self.assertNotIn("skeptic_due", self.ok("reword_finding", finding_id="f1", reason="clearer",
                                                statement="GATA1-i raises SPI1 in every guide [run:r1]."))

    @unittest.skip('left the flow on 2026-11-17 (no main line, forks, maps moment, figure plan, end proposal or doubt branches: docs/adr/0051, Amendment 2); moves to tests_research with its code after 11-30')
    def test_the_maps_come_once_and_to_the_main_agent_only(self):
        def overview(rid):
            self.svc.record.append("run_logged", {"run_id": rid, "what": "the map", "code": "m", "inputs": [],
                                                  "question_id": "A.screen_overview"}, rc.TOOL)
            self.svc.record.append("run_finished", {"run_id": rid, "state": "done", "output": "2 targets"}, rc.TOOL)
        overview("map1")
        self.assertNotIn("the_maps_are_in", self.as_worker("record_finding", finding_id="t1-f6",
                                                           statement="w [run:r1].", run_ids=["r1"],
                                                           kind="description"))
        out = self.ok("record_finding", finding_id="f7", statement="m [run:r1].", run_ids=["r1"], kind="description")
        self.assertIn("the_maps", out["the_maps_are_in"])
        self.assertNotIn("research_lead_due", out["the_maps_are_in"])  # the lead's item is the list's, not the map's
        overview("map2")  # drawn again: the moment does not come back
        self.assertNotIn("the_maps_are_in", self.ok("record_finding", finding_id="f8", statement="m [run:r1].",
                                                    run_ids=["r1"], kind="description"))

    @unittest.skip('left the flow on 2026-11-17 (no main line, forks, maps moment, figure plan, end proposal or doubt branches: docs/adr/0051, Amendment 2); moves to tests_research with its code after 11-30')
    def test_pertura_status_giving_the_maps_counts_as_once(self):
        self.svc.record.append("run_logged", {"run_id": "map1", "what": "the map", "code": "m", "inputs": [],
                                              "question_id": "A.screen_overview"}, rc.TOOL)
        self.svc.record.append("run_finished", {"run_id": "map1", "state": "done", "output": "2 targets"}, rc.TOOL)
        self.assertIn("the_maps_are_in", self.ok("pertura_status"))
        self.assertNotIn("the_maps_are_in", self.ok("record_finding", finding_id="f7", statement="m [run:r1].",
                                                    run_ids=["r1"], kind="description"))


class TestTheStopHookReadsWhatIsPending(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.home = tempfile.mkdtemp()
        sf.record_call({"session_id": "s1", "tool_name": sf.TOOL_PREFIX + "open_project",
                        "tool_response": {"project": "/w/p-1a2b3c4d"}}, self.home, now=NOW)

    def reply(self, pending, agent=None):
        sf.record_call({"session_id": "s1", "tool_name": sf.TOOL_PREFIX + "record_finding",
                        "tool_response": {"finding_id": "f1", "pending": pending},
                        **({"agent_id": agent} if agent else {})}, self.home, now=NOW)

    def stop(self, **more):
        path = transcript(self.home, "开始吧", "I recorded f1 and will go on.")  # no status block: the style is off
        return hooks.stop({"session_id": "s1", "transcript_path": path, **more}, self.home, now=NOW)

    def test_an_open_item_sends_the_agent_back_up_to_three_times_in_a_turn_without_the_style(self):
        self.reply({"main agent": ["research lead review"]})
        self.assertEqual(sf.session_status("s1", self.home)["due"], {"main agent": ["research lead review"]})
        out = self.stop()
        self.assertEqual(out["decision"], "block")
        self.assertIn("research lead review", out["reason"])
        self.assertEqual([self.stop(stop_hook_active=True)["decision"] for _ in range(2)], ["block", "block"])
        self.assertIsNone(self.stop(stop_hook_active=True))  # the turn's three returns are spent

    def test_nothing_pending_lets_the_turn_end(self):
        self.reply({"main agent": ["research lead review"]})
        self.reply({})
        self.assertIsNone(self.stop())

    def test_a_subagents_reply_does_not_change_the_list(self):
        self.reply({"main agent": ["research lead review"]})
        sf.record_call({"session_id": "s1", "tool_name": sf.TOOL_PREFIX + "record_finding", "agent_id": "a1",
                        "tool_response": {"finding_id": "t1-f1"}}, self.home, now=NOW)
        self.assertEqual(sf.session_status("s1", self.home)["due"], {"main agent": ["research lead review"]})

    def test_the_users_items_alone_let_the_turn_end(self):
        self.reply({"user": ["results to decide"]})
        self.assertIsNone(self.stop())

    def test_open_items_no_longer_writes_the_list(self):
        self.reply({"main agent": ["research lead review"]})
        sf.record_call({"session_id": "s1", "tool_name": sf.TOOL_PREFIX + "open_items",
                        "tool_response": {"open": [{"kind": "skeptic due"}], "due": [{"kind": "skeptic due"}]}},
                       self.home, now=NOW)
        self.assertEqual(sf.session_status("s1", self.home)["due"], {"main agent": ["research lead review"]})


if __name__ == "__main__":
    unittest.main()
