"""Item 5's second part (with item 10) of docs/build_goal_session6_fixes.md: the user must know, after being away,
what the agent is after. Development session 6: 12 of the user's 25 messages only kept the agent going, and nothing
told them, coming back, where things stood. Now the digest that pertura_status gives reaches them:
- each step records a one-line why and the thread it serves, and the digest tells the runs under their thread;
- the replies that end a step carry the digest's line, which the status line shows first;
- the page opens with the digest, and marks what is new since the user last looked;
- when the user writes after ten minutes or more, or after a finding, the agent is asked for the digest first;
- /pertura:catchup gives it on demand."""
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "plugin", "status"))

import hooks  # noqa: E402
import page as user_page  # noqa: E402
import status_file as sf  # noqa: E402
import status_words  # noqa: E402

from pertura import digest, record as rc  # noqa: E402


class TestTheDigestTellsRunsByThread(unittest.TestCase):
    def test_a_run_naming_its_thread_is_told_under_it_with_its_why(self):
        with tempfile.TemporaryDirectory() as d:
            r = rc.Record(os.path.join(d, "rec"))
            r.append("thread_opened", {"thread_id": "t1", "about": "GATA1-i's cells differ", "raised_by": "r0",
                                       "strength": 5.0}, rc.TOOL)
            r.append("run_logged", {"run_id": "c1", "what": "SPI1 by guide", "code": "c", "inputs": [],
                                    "why": "does every guide raise SPI1?", "thread_id": "t1"}, rc.TOOL)
            r.append("run_finished", {"run_id": "c1", "state": "ok", "output": "o"}, rc.TOOL)
            told = digest.digest(r.state(), r.events(), True)
            self.assertEqual(len(told["since"]), 1)
            self.assertIn("SPI1 by guide ok (why: does every guide raise SPI1?)", told["since"][0])
            self.assertTrue(told["since"][0].startswith("t1 (GATA1-i's cells differ)"))


class TestWhereTheUserSeesIt(unittest.TestCase):
    def test_the_status_line_leads_with_the_digests_line(self):
        st = sf.empty("/p/screen-1a2b3c4d")
        st["name"] = "screen"
        sf.apply_call(st, "module_result", {}, {"run_id": "r1", "state": "done",
                                                "digest_line": "on t1: GATA1-i's cells differ"})
        self.assertIn("on t1: GATA1-i's cells differ", status_words.first_line(st))
        sf.apply_call(st, "pertura_status", {}, {"project": "/p/screen-1a2b3c4d", "digest": {
            "line": "on t2: the pairs", "waits_on_you": ["nothing: the agent goes on"], "on": None, "since": [],
            "next": []}})
        self.assertIn("on t2: the pairs", status_words.first_line(st))
        self.assertEqual(st["digest"]["line"], "on t2: the pairs")

    def test_the_page_opens_with_the_digest_and_marks_what_is_new(self):
        page = user_page.fold(None, "pertura_status", {}, {"digest": {
            "line": "on t1", "waits_on_you": ["1 result proposed for the report, to confirm: GATA1 raises SPI1"],
            "on": {"thread_id": "t1", "about": "GATA1-i's cells differ", "why": "the strongest"},
            "since": ["t1 (GATA1-i's cells differ): finding f1"], "next": ["t2: the pairs"]}}, [], now=100.0)
        page = user_page.fold(page, "record_finding", {"finding_id": "f1", "statement": "GATA1-i raises SPI1"},
                              {"checks": "none"}, [], now=200.0)
        record = {"results": {"r1": {"title": "GATA1 raises SPI1", "decision": None, "finding_ids": ["f1"]}}}
        html = user_page.render(page, "screen", record, due={"user": ["results to decide"]})
        overview = html[html.index('id="overview"'):]
        overview = overview[:overview.index("</section>")]
        self.assertIn("Needs your input", overview)  # the user's item of the pending list: a result to decide
        self.assertIn('<a href="#report-r1">Results are proposed for the report', overview)
        self.assertNotIn("1 result proposed", overview)  # the digest's own sentence stays the terminal's
        top = overview[overview.index("<b>Status</b>"):]
        for said in ("GATA1-i&#x27;s cells differ", "t2: the pairs"):
            self.assertIn(said, top)
        self.assertIn('data-time="200"', html)
        self.assertIn('id="mark-read"', html)  # seen only when the user marks it so, not at every reload
        self.assertGreater(html.rindex("localStorage"), html.rindex('data-time="200"'))  # after the cards

    def test_the_user_coming_back_gets_the_digest_first(self):
        with tempfile.TemporaryDirectory() as d:
            key = "s1"
            st = sf.empty("/p/screen")
            sf.write_json(sf.session_path(key, d), {"project": "k1"})  # a session points to its project's status
            sf.write_json(sf.project_path("k1", d), st)
            event = {"session_id": key}
            self.assertIsNone(hooks.user_prompt(event, d, now=1000.0))  # no turn of the agent has ended yet
            hooks.turn_ended(event, d, now=1000.0)
            self.assertIsNone(hooks.user_prompt(event, d, now=1300.0))  # five minutes after the agent's turn
            self.assertIn("pertura_status", hooks.user_prompt(event, d, now=1600.0)["hookSpecificOutput"][
                "additionalContext"])  # ten minutes after it
            self.assertIsNone(hooks.user_prompt({"session_id": "other"}, d, now=5000.0))  # not a Pertura session

    def test_a_new_session_in_the_folder_of_an_earlier_project_starts_with_its_digest(self):
        with tempfile.TemporaryDirectory() as d:
            sf.write_json(sf.project_path("k1", d), {**sf.empty("/p/screen"), "name": "screen"})
            sf.write_json(sf.session_path("s0", d), {"project": "k1", "updated": 10.0, "cwd": "C:/work/screen"})
            hooks.turn_ended({"session_id": "s0"}, d, now=20.0)  # its .turn file is no session of its own
            told = hooks.user_prompt({"session_id": "s1", "cwd": "C:/work/screen"}, d, now=5000.0)
            self.assertIn("pertura_status", told["hookSpecificOutput"]["additionalContext"])
            self.assertIn("screen", told["hookSpecificOutput"]["additionalContext"])
            self.assertIsNone(hooks.user_prompt({"session_id": "s1", "cwd": "C:/work/screen"}, d, now=5010.0))  # once
            self.assertIsNone(hooks.user_prompt({"session_id": "s2", "cwd": "C:/work/other"}, d, now=5000.0))
            os.remove(sf.project_path("k1", d))  # the project's status no longer kept: nothing to give
            self.assertIsNone(hooks.user_prompt({"session_id": "s3", "cwd": "C:/work/screen"}, d, now=5000.0))

    def test_the_hook_and_the_command_are_in_the_plugin(self):
        with open(os.path.join(ROOT, "plugin", "hooks", "hooks.json"), encoding="utf-8") as fh:
            hooks_json = json.load(fh)
        self.assertIn("user-prompt", json.dumps(hooks_json["hooks"]["UserPromptSubmit"]))
        with open(os.path.join(ROOT, "plugin", "commands", "catchup.md"), encoding="utf-8") as fh:
            self.assertIn("pertura_status", fh.read())


if __name__ == "__main__":
    unittest.main()
