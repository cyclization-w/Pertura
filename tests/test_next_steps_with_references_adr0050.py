"""ADR 0050, D5: each result offers ranked next steps, with references computed by code from the knowledge base.

The agent writes each step's question, its move (a kind of analysis, in the knowledge base's words) and why; code
attaches how many of the studies read went on to that move after a result of the same kinds ("N of the M studies
read", M always stated), with the steps that did it as references (paper, step, panels, source). The user picks one
(note_reply kind next_step, with the option's rank), which opens its thread on the line, next_of the result.

Unattended runs are no longer a target. The simulated user of development runs takes the recommended option, by the
same rule as rc.next_step_awaiting. The pick is the user's item: with no item of the main agent's pending, the turn
ends for it. A result proposed without
next steps is a step the agent owes: the state list asks for them with what the studies read did after results like
it."""
import os
import sys
import tempfile
import unittest
from unittest import mock

from test_results_from_findings import TEXT, Service
from test_the_agent_works_on_until_the_user_is_needed import NOW, transcript

from pertura import knowledge_store, record as rc

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "plugin", "status"))
import hooks  # noqa: E402
import status_file as sf  # noqa: E402


def row(paper, label, work, step, kind, panels, raised_by=()):
    return {"paper": paper, "work": work, "label": label, "modality": "CRISPRi", "system": "cell line",
            "readout": "transcriptome", "step": step, "question": f"step {step}", "raised_by": list(raised_by),
            "result": f"result {step}", "quote": f"quote {step}", "checks": [], "doi": None, "pmid": str(paper),
            "notes": None, "title": label,
            "evidence": [{"analysis": kind, "panels": panels, "evidence": "a chart", "compared_with": None}]}


ROWS = [row(1, "PMID1 Alpha 2024", "wA", 1, "states", "Fig 2a"),
        row(1, "PMID1 Alpha 2024", "wA", 2, "genes", "Fig 3b", raised_by=[1]),
        row(2, "PMID2 Beta 2023", "wB", 1, "states", "Fig 1c"),
        row(2, "PMID2 Beta 2023", "wB", 2, "enrichment", "Fig 4a", raised_by=[1]),
        row(3, "PMID3 Gamma 2022", "wC", 1, "similarity", "Fig 2b"),
        row(3, "PMID3 Gamma 2022", "wC", 2, "genes", "Fig 5d", raised_by=[1])]


class TestTheStoreCounts(unittest.TestCase):
    def test_counts_are_of_studies_after_results_of_these_kinds(self):
        with mock.patch.object(knowledge_store, "results", return_value=ROWS):
            got = knowledge_store.went_on_to(["states"], "genes")
            moves = knowledge_store.went_on_to(["states", "similarity"])
        self.assertEqual((got["n"], got["m"]), (1, 2))
        self.assertEqual(got["references"][0]["row"], "1:2")
        self.assertIn("Fig 3b", got["references"][0]["panels"])
        self.assertEqual(moves["m"], 3)
        self.assertEqual(dict(moves["moves"])["genes"], 2)


STEPS = [{"question": "Which genes move within S04?", "move": "genes", "why": "it tells composition from regulation"},
         {"question": "Which programmes do they share?", "move": "enrichment", "why": "it names the process",
          "recommended": True}]


class TestNextSteps(Service):
    def setUp(self):
        super().setUp()
        for p in (mock.patch.object(knowledge_store, "results", return_value=ROWS),
                  mock.patch.object(self.svc, "_kinds_named", return_value=["states"])):
            p.start()
            self.addCleanup(p.stop)

    def propose(self, **more):
        return self.ok("report_result", result_id="res1", title="GATA1 knockdown raises SPI1", finding_ids=["f1"],
                       figure="fig1", text=TEXT, **more)

    def test_a_result_offers_ranked_next_steps_with_counts_computed_by_code(self):
        self.propose(next_steps=STEPS)
        steps = self.svc.record.state()["results"]["res1"]["next_steps"]
        self.assertEqual([(s["rank"], s["recommended"]) for s in steps], [(1, False), (2, True)])
        self.assertEqual(steps[0]["counts"], {"n": 1, "m": 2, "situation": "after a result of states"})
        self.assertEqual(steps[0]["references"][0]["row"], "1:2")
        awaiting = rc.next_step_awaiting(self.svc.record.state())
        self.assertEqual((awaiting["result_id"], awaiting["rank"], awaiting["question"]),
                         ("res1", 2, "Which programmes do they share?"))
        out = self.ok("pertura_status")  # the_users_next_step is gone (docs/adr/0052, D3): 0 of 7 ended the turn;
        self.assertNotIn("the_users_next_step", out)  # the result card carries its next steps

    def test_a_move_is_a_kind_the_knowledge_base_names_and_one_option_is_recommended(self):
        self.assertIn("move", self.refused("report_result", result_id="res1", title="t", finding_ids=["f1"],
                                           figure="fig1", text=TEXT,
                                           next_steps=[{"question": "q", "move": "dance", "why": "w"}]))
        both = [{**s, "recommended": True} for s in STEPS]
        self.assertIn("one", self.refused("report_result", result_id="res1", title="t", finding_ids=["f1"],
                                          figure="fig1", text=TEXT, next_steps=both))

    def test_the_users_pick_opens_the_next_thread_and_ends_the_offer(self):
        self.propose(next_steps=STEPS)
        out = self.ok("note_reply", kind="next_step", words="yes, the programmes", option=2)
        th = self.svc.record.state()["threads"][out["thread_id"]]
        # no main line (2026-11-17): the picked step is the main agent's own, marked by next_of alone
        self.assertEqual((th["next_of"], th["about"], th["on_line"]), ("res1", "Which programmes do they share?", None))
        self.assertIsNone(rc.next_step_awaiting(self.svc.record.state()))
        self.ok("report_result", result_id="res1", title="GATA1 knockdown raises SPI1 in every guide",
                finding_ids=["f1"], figure="fig1", text=TEXT)  # a new version without steps carries the list
        st = self.svc.record.state()
        self.assertEqual(len(st["results"]["res1"]["next_steps"]), 2)
        self.assertIsNone(rc.next_step_awaiting(st))  # its pick stands: the offer is not made again

    def test_the_users_own_next_step_opens_its_thread_in_their_words(self):
        self.propose(next_steps=STEPS)
        out = self.ok("note_reply", kind="next_step", words="look at the S04 cells by donor first")
        th = self.svc.record.state()["threads"][out["thread_id"]]
        self.assertEqual((th["next_of"], th["about"]), ("res1", "look at the S04 cells by donor first"))
        self.assertIsNone(rc.next_step_awaiting(self.svc.record.state()))
        self.assertIn("no next steps wait", self.refused("note_reply", kind="next_step", words="again", option=1))

    def test_an_option_that_needs_new_data_is_marked_and_never_recommended(self):
        """The trial of 2026-11-16: the recommended step was an independent experiment, and its thread ended out of
        data. Such an option is shown, marked, and the first the data can answer is recommended in its place."""
        steps = [{"question": "Do they persist in a transfer?", "move": "an independent experiment", "why": "w",
                  "recommended": True}, STEPS[0]]
        out = self.propose(next_steps=steps)
        got = self.svc.record.state()["results"]["res1"]["next_steps"]
        self.assertEqual([(s["recommended"], s.get("needs_new_data", False)) for s in got],
                         [(False, True), (True, False)])
        self.assertIn("needs new data", out["recommended_moved"])
        self.assertEqual(rc.next_step_awaiting(self.svc.record.state())["rank"], 2)

    def test_with_every_option_needing_new_data_none_is_recommended(self):
        steps = [{"question": "q1", "move": "an independent experiment", "why": "w"},
                 {"question": "q2", "move": "outside data", "why": "w"}]
        self.assertIn("no option is", self.propose(next_steps=steps)["recommended_moved"])
        waits = rc.next_step_awaiting(self.svc.record.state())
        self.assertEqual((waits["rank"], waits["question"], len(waits["options"])), (None, None, 2))

    def test_the_counts_follow_the_results_main_kind(self):
        """Pooling the kinds behind a result counted 67 of the 78 studies read: the counts read the kind of the run
        its figure comes from, and where that kind checks the screen (guides), the findings' most common kind."""
        with mock.patch.object(self.svc, "_kinds_named", return_value=["guides"]), \
                mock.patch.object(self.svc, "_kinds_of", return_value=["genes", "states", "genes"]):
            self.propose(next_steps=STEPS)
        got = self.svc.record.state()["results"]["res1"]["next_steps"]
        self.assertEqual(got[0]["counts"]["situation"], "after a result of genes")

    def test_a_result_waits_for_the_users_decision_as_their_item(self):
        self.assertIn("results to decide", self.propose(next_steps=STEPS)["pending"]["user"])
        out = self.ok("report_result", result_id="res1", decision="confirmed", user_words="yes, keep it")
        self.assertNotIn("results to decide", out["pending"].get("user", []))

    def test_a_result_without_next_steps_is_owed_them_with_what_studies_did(self):
        self.propose()
        item = self.ok("pertura_status")["next_steps_to_offer"]
        self.assertEqual(item["results"], ["res1"])
        went = item["studies_went_on_to"]["res1"]
        self.assertEqual(went["m"], 2)
        self.assertIn(["genes", 1], [list(m) for m in went["moves"]])
        # one of the loop's own items (docs/adr/0052, D3): the pending line lists it, and the Stop hook sends the agent
        # back once while it is open
        self.assertIn("next steps to offer", self.ok("pertura_status")["pending"].get("main agent", []))


class TestTheStopHookAndThePick(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp()
        sf.record_call({"session_id": "s1", "tool_name": sf.TOOL_PREFIX + "open_project",
                        "tool_response": {"project": "/w/p-1a2b3c4d"}}, self.home, now=NOW)

    def stop_with(self, pending):
        sf.record_call({"session_id": "s1", "tool_name": sf.TOOL_PREFIX + "report_result",
                        "tool_response": {"result_id": "res1", "pending": pending}}, self.home, now=NOW)
        path = transcript(self.home, "go on", "Here is r1, with three next steps.")
        return hooks.stop({"session_id": "s1", "transcript_path": path}, self.home, now=NOW)

    def test_a_pick_awaited_lets_the_turn_end_and_the_agents_own_items_send_it_back(self):
        self.assertIsNone(self.stop_with({"user": ["the user's next step"]}))
        out = self.stop_with({"main agent": ["next steps to offer"], "user": ["the user's next step"]})
        self.assertEqual(out["decision"], "block")
        self.assertIn("next steps to offer", out["reason"])


if __name__ == "__main__":
    unittest.main()
