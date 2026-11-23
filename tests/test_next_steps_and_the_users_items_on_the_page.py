"""The page's share of ADR 0050: each result's next steps as the record holds them (the question, why, its kind of
analysis, n of the m studies read with their steps as references), a button that puts the user's choice in the
message box while the pick waits, and once chosen, the thread it opened; and "Needs your input" from one source,
the user's items of the pending list the last main-agent reply named (status_file's due), the main agent's own in
one quiet line."""
import os
import re
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATUS = os.path.join(ROOT, "plugin", "status")
if not os.path.isdir(STATUS):  # the plugin is not deployed to the analysis server
    raise unittest.SkipTest("no plugin folder here")
sys.path.insert(0, STATUS)
import page  # noqa: E402
from test_next_steps_with_references_adr0050 import ROWS, STEPS  # noqa: E402
from test_results_from_findings import TEXT, Service  # noqa: E402

from pertura import knowledge_store, page_view  # noqa: E402

LINK = {"port": 9, "token": "t"}


def view(text, vid):
    part = text[text.index(f'<section class="view" id="{vid}"'):]
    return part[:part.index("</section>")]


class TestNextStepsOnThePage(Service):
    def setUp(self):
        super().setUp()
        for p in (mock.patch.object(knowledge_store, "results", return_value=ROWS),
                  mock.patch.object(self.svc, "_kinds_named", return_value=["states"])):
            p.start()
            self.addCleanup(p.stop)
        self.ok("report_result", result_id="res1", title="GATA1 knockdown raises SPI1", finding_ids=["f1"],
                figure="fig1", text=TEXT, next_steps=STEPS)

    def page(self, due=None, link=LINK):
        return page.render(page.empty(), "Pertura · screen_X", page_view.view(self.svc.record.state()), [], link,
                           due)

    def test_the_steps_wait_with_their_counts_references_and_a_choice_put_in_the_box(self):
        got = page_view.view(self.svc.record.state())["results"]["res1"]
        self.assertTrue(got["next_step_waits"])
        self.assertIsNone(got["pick"])
        report = view(self.page(), "report-res1")
        self.assertIn("<h2>Next steps</h2>", report)
        self.assertIn("<b>Option 2</b> Which programmes do they share? <span class=\"tag\">Recommended</span>", report)
        self.assertIn("1 of the 2 studies read took a step of this kind after a result of states.", report)  # m stated
        ref = re.search(r'<ul class="refs"><li>(.*?)</li>', report).group(1)
        self.assertIn(", step 2", ref)
        self.assertEqual(report.count('data-say="Result 1 (res1), option '), 2)  # one button per option
        self.assertIn('data-about="result res1"', report)
        self.assertIn('data-copy="Result 1 (res1), option 2: Which programmes do they share?"',
                      view(self.page(link=None), "report-res1"))  # no session to reach: the choice is copied

    def test_the_pick_shows_the_thread_it_opened_and_no_button_after_it(self):
        out = self.ok("note_reply", kind="next_step", words="yes, the programmes", option=2)
        got = page_view.view(self.svc.record.state())
        self.assertEqual(got["results"]["res1"]["pick"]["option"], 2)
        self.assertEqual(got["results"]["res1"]["pick"]["thread_id"], out["thread_id"])
        self.assertEqual(got["threads"][out["thread_id"]]["next_of"], "res1")
        report = view(self.page(), "report-res1")
        self.assertIn(f'<b>Chosen</b> → <a href="#thread-{out["thread_id"]}"', report)
        self.assertNotIn("data-say", report)
        self.ok("report_result", result_id="res1", title="GATA1 knockdown raises SPI1 in every guide",
                finding_ids=["f1"], figure="fig1", text=TEXT, next_steps=STEPS)  # a new list, offered again
        got = page_view.view(self.svc.record.state())["results"]["res1"]
        self.assertIsNone(got["pick"])
        self.assertEqual([p["thread_id"] for p in got["earlier_picks"]], [out["thread_id"]])
        self.assertIn("Chosen after the result's earlier versions", view(self.page(), "report-res1"))

    def test_their_own_next_step_reads_as_theirs(self):
        out = self.ok("note_reply", kind="next_step", words="look at the S04 cells by donor first")
        self.assertIsNone(page_view.view(self.svc.record.state())["results"]["res1"]["pick"]["option"])
        self.assertIn(f'<b>You chose your own next step</b> → <a href="#thread-{out["thread_id"]}"',
                      view(self.page(), "report-res1"))

    def test_an_option_that_needs_new_data_is_tagged_and_with_none_recommended_each_can_be_chosen(self):
        self.ok("report_result", result_id="res1", title="GATA1 knockdown raises SPI1", finding_ids=["f1"],
                figure="fig1", text=TEXT, next_steps=[{"question": "Do they persist in a transfer?",
                                                       "move": "an independent experiment", "why": "w"},
                                                      {"question": "Which loci does GATA1 bind?",
                                                       "move": "outside data", "why": "w"}])
        report = view(self.page(), "report-res1")
        self.assertEqual(report.count('<span class="tag">Needs new data</span>'), 2)
        self.assertNotIn("Recommended", report)
        self.assertNotIn("next-step rec", report)
        self.assertIn('data-say="Result 1 (res1), option 2: Which loci does GATA1 bind?"', report)

    def test_needs_your_input_holds_the_users_pending_items_and_the_agents_in_one_line(self):
        due = {"main agent": ["figures to draw"], "user": ["the user's next step", "results to decide",
                                                          "a label the page has no words for"]}
        overview = view(self.page(due), "overview")
        self.assertIn('<li><a href="#report-res1">Choose the next step after Result 1</a></li>', overview)
        self.assertIn('<li><a href="#report-res1">Results are proposed for the report', overview)
        self.assertIn('<li><a href="#conversation">a label the page has no words for</a></li>', overview)
        self.assertIn('<p class="muted agent-items">Pertura is working on: figures to draw</p>', overview)
        self.assertIn('<span class="l2">Choose the next step after Result 1</span>', self.page(due))  # the pill
        self.assertIn('<span class="l2">No steps yet</span>', self.page({"main agent": ["skeptic"]}))  # else its step
        self.assertNotIn("Needs your input", view(self.page({"main agent": ["skeptic"]}), "overview"))
        self.assertNotIn("Needs your input", view(self.page(), "overview"))  # the record alone says nothing waits


if __name__ == "__main__":
    unittest.main()
