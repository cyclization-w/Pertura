"""One simple loop (d2's review of what reaches the agents): the research lead and the figure
reviewer are off by default, and an arm turns them on in its workspace's switches; a due item is said when it falls
due and once more if still undone, not on every reply (research_lead_due was said about six times an episode); a new
doubt, thread or result makes it new again; no reply puts the next step to the user (0 of 7 ended the turn)."""
import os
import tempfile
import unittest

from test_results_from_findings import Service

from pertura import analysis_service as asv, record as rc

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestTheLoopsSwitches(unittest.TestCase):

    def test_no_text_the_main_agent_reads_names_either_role(self):
        for name in (("plugin", "skills", "pertura", "SKILL.md"), ("plugin", "agents", "worker.md")):
            text = open(os.path.join(ROOT, *name), encoding="utf-8").read()
            for role in ("research lead", "research-lead", "figure reviewer", "figure-reviewer"):
                self.assertNotIn(role, text.lower(), name)
        self.assertFalse(os.path.exists(os.path.join(ROOT, "plugin", "agents", "skeptic.md")))  # the reader replaced it
        self.assertNotIn("the_users_next_step", asv.PENDING)


class TestADueItemIsSaidOnceAndOnceMore(Service):
    def doubt(self, did):
        # the pending doubts are those the checks open (2026-11-18); a review's are said once, never pending
        self.svc.raise_doubt(doubt_id=did, text="its checks disagree with it: guides_agree: disagree",
                             would_change="the claim kept, narrowed or withdrawn", finding_id="f1",
                             opened_by=asv.CHECKS_DOUBT)

    def reply(self):
        return self.svc._with_lead_threads({})

    def test_said_when_due_and_once_more_after_its_calls_and_again_for_a_new_doubt(self):
        self.doubt("d1")
        said = ["doubts_to_answer" in self.reply() for _ in range(asv.SAY_AGAIN_AFTER + 4)]
        self.assertEqual(said.count(True), 2)  # when due, and once more
        self.assertEqual((said[0], said[asv.SAY_AGAIN_AFTER]), (True, True))
        self.doubt("d2")  # a new doubt: said again at once, with every doubt that waits
        out = self.reply()
        self.assertEqual({d["doubt_id"] for d in out["doubts_to_answer"]["doubts"]}, {"d1", "d2"})
        self.assertNotIn("doubts_to_answer", self.reply())

    def test_an_item_no_longer_due_is_said_again_when_due_again(self):
        self.doubt("d1")
        self.assertIn("doubts_to_answer", self.reply())
        self.svc.answer_doubt(doubt_id="d1", outcome="holds", reason="both guides agree [run:r1]")
        self.assertNotIn("doubts_to_answer", self.reply())
        self.doubt("d3")
        self.assertIn("doubts_to_answer", self.reply())


if __name__ == "__main__":
    unittest.main()
