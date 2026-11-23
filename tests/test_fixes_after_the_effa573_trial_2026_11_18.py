"""The problems the GSE342764 trial on effa573 showed (pertura_trial notes, what_the_effa573_trial_showed.md):
1. a turn a background task's notice begins names its runs in that notice;
2. one message of the user's may hold two decisions ("Keep r2. Yes, the analysis is enough.");
3. a decided result restated on the same findings stays as decided;
5. the reviewer reads the claims with their turn's runs, and the checks runs' words, the analysis runs first;
6. the screen handed over with no question to quote takes the user's own messages as the question's words (one
   question still quotes the user, test_the_record_kept_by_code_adr0051);
and the page: complete once the report is written and the user's enough stands."""
import os
import sys
import unittest
from unittest import mock

import test_the_record_kept_by_code_adr0051 as inbox_tests
from test_results_from_findings import TEXT, Service

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "plugin", "status"))
import recorder  # noqa: E402

inbox = inbox_tests.inbox


class TestTheRecorder(unittest.TestCase):
    def test_a_task_notice_names_the_turns_runs(self):
        notice = ("<task-notification><status>completed</status><result>{\"ended\": [{\"run_id\": "
                  "\"20261118T113436-F-guides_agree-699d64\"}]}</result></task-notification>")
        entries = [{"uuid": "a", "type": "user", "message": {"role": "user", "content": notice}}]
        self.assertEqual(recorder.turn_runs(entries), ["20261118T113436-F-guides_agree-699d64"])

    def test_one_message_keeps_both_its_decisions(self):
        said = "Keep r2 (version 4) and r3 as they stand. Yes, the analysis is enough."
        fixed = recorder.fixed_forms("", {"m1": said})
        model = {"user_decisions": [{"kind": "keep", "words": "Keep r2", "message_uuid": "m1", "refers_to": "r2"},
                                    {"kind": "enough", "words": "Yes, the analysis is enough.", "message_uuid": "m1"}]}
        kinds = sorted(u["kind"] for u in recorder.merged(fixed, model)["user_decisions"])
        self.assertEqual(kinds, ["enough", "keep"])  # the keep once, from code; the enough from the model


class TestThePromptHooksMessage(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.home = tempfile.mkdtemp()
        env = mock.patch.dict(os.environ, {"PERTURA_STATUS_HOME": self.home})
        env.start()
        self.addCleanup(env.stop)

    def test_what_the_prompt_hook_did_not_read_is_taken_from_the_turn(self):
        import json
        said = "Keep r2 (version 4) and r3 as they stand. Yes, the analysis is enough."
        path = os.path.join(self.home, "t.jsonl")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"uuid": "u1", "type": "user", "message": {"content": said}}) + "\n"
                     + json.dumps({"uuid": "u2", "type": "assistant", "message": {"content": [
                         {"type": "text", "text": "Kept. The report is written."}]}}))
        with mock.patch.object(recorder, "send"):
            recorder.record_prompt({"session_id": "s1", "prompt": said}, host="host", project="/w/p-1")
        model = {"user_decisions": [{"kind": "keep", "words": "Keep r2", "message_uuid": "u1", "refers_to": "r2"},
                                    {"kind": "keep", "words": "r3 as they stand", "message_uuid": "u1",
                                     "refers_to": "r3"},
                                    {"kind": "enough", "words": "Yes, the analysis is enough.", "message_uuid": "u1"}]}
        sent = []
        with mock.patch.object(recorder, "send", lambda h, p, tag, rec: sent.append(rec)), \
                mock.patch.object(recorder, "digest_of", return_value="{}"):
            recorder.record_turn("s1", path, host="host", project="/w/p-1", ask=lambda *a, **k: model)
        got = sorted((u["kind"], u.get("refers_to")) for u in sent[0]["user_decisions"])
        self.assertEqual(got, [("enough", None), ("keep", "r3")])  # the hook applied the keep of r2


class TestTheReviewersInput(unittest.TestCase):
    def test_the_claims_carry_their_turns_runs_and_the_checks_runs_follow_the_analysis_runs(self):
        rec = {"claims": [{"statement": "x", "targets": ["A"], "feature": {"kind": "state", "name": "S01"},
                           "direction": "down", "run_ids": []}],
               "results": [{"title": "t", "text": "x", "claims": [0]}], "turn_runs": ["run0047-3fa9c1"],
               "range": {"session": "s", "agent": "main", "first": "u1", "last": "u2"}}
        checks = {"t1-c1": {"lead": {"outcome": "agrees", "reason": "", "run_id": "20261118T113722-checks-915d58"},
                            "guides_agree": {"outcome": "agree", "reason": "", "run_id": "20261118T113722-checks-915d58"}}}
        outputs = {"20261118T113722-checks-915d58": {"output": "each guide alone: 3 of 3"},
                   "run0047-3fa9c1": {"output": "states"}}
        seen = {}

        def ask(model, instructions, text, schema, timeout=0):
            seen["text"] = text
            return {"doubts": []}

        with mock.patch.object(recorder, "checks_of", return_value=checks), \
                mock.patch.object(recorder, "run_outputs", side_effect=lambda h, p, ids: seen.setdefault("ids", ids)
                                  and outputs), \
                mock.patch.object(recorder, "reader_facts", return_value={}), mock.patch.object(recorder, "send"):
            recorder.read_by_the_skeptic("host", "/p", "t1", rec, ask=ask)
        self.assertEqual(seen["ids"], ["run0047-3fa9c1", "20261118T113722-checks-915d58"])
        self.assertIn('"run_ids": [\n   "run0047-3fa9c1"', seen["text"])  # the claim as the record applies it
        claims, runs = seen["text"].split("RUNS:", 1)
        # GSE203592 on 308d820: checks listed apart, under a repeated claim's first ID, read as none; now in the claim
        self.assertIn('"in_words": "each guide alone: 3 of 3"', claims)
        self.assertIn('"guides_agree": "agree"', claims)
        self.assertNotIn("each guide alone", runs)


class TestTheInbox(Service):
    claim = inbox_tests.TestTheInbox.claim

    def setUp(self):
        super().setUp()
        parts = {"cells": None, "reference": "control_guides", "kind": "gene", "genes": None, "state_table": None,
                 "lead": None}
        for p in (mock.patch.object(self.svc, "_finding_parts", return_value=parts),
                  mock.patch.object(self.svc, "_start_checks", return_value={"checks_run": "c1"}),
                  mock.patch.object(self.svc, "_confirmed", return_value={})):
            p.start()
            self.addCleanup(p.stop)

    def result(self, **more):
        return {"title": "GATA1 knockdown raises SPI1", "text": TEXT, "figure": "fig1", **more}

    def test_the_users_first_question_in_the_recorders_words_takes_their_own(self):
        said = "Here is my screen. Please analyse it."
        inbox(self.svc, "t1", {"threads": [{"about": "Analyse the whole screen", "raised_by": "the user",
                                            "scope": "the whole screen"}], "user_messages": {"m1": said}})
        self.ok("pertura_status")
        asked = self.svc.record.state()["asked"]
        self.assertEqual([(q["text"], q.get("scope")) for q in asked], [(said, "the whole screen")])
        self.assertTrue(self.svc._survey_on())
        inbox(self.svc, "t2", {"threads": [{"about": "Analyse the whole screen again", "raised_by": "the user",
                                            "scope": "the whole screen"}], "user_messages": {"m2": "Go ahead"}})
        out = self.ok("pertura_status")
        self.assertIn("its words are not in a message", str(out["recorded"]))  # a question stands already

    def test_a_decided_result_restated_on_the_same_findings_stays_decided(self):
        inbox(self.svc, "t1", {"claims": [self.claim()], "results": [self.result(claims=[0])]})
        self.ok("pertura_status")
        inbox(self.svc, "t2", {"user_decisions": [{"kind": "keep", "words": "Keep r1", "message_uuid": "m1",
                                                   "refers_to": "r1"}], "user_messages": {"m1": "Keep r1"}})
        self.ok("pertura_status")
        inbox(self.svc, "t3", {"results": [self.result(result_id="r1", text="A shorter summary.")]})
        self.ok("pertura_status")
        r = self.svc.record.state()["results"]["r1"]
        self.assertEqual((r["decision"], r["text"]), ("confirmed", TEXT))
        inbox(self.svc, "t4", {"claims": [self.claim(feature={"kind": "gene", "name": "CEBPA"})],
                               "results": [self.result(claims=[0], result_id="r1")]})
        self.ok("pertura_status")
        r = self.svc.record.state()["results"]["r1"]
        self.assertEqual((r["finding_ids"], r["decision"]), (["t1-c1", "t4-c1"], None))  # a new finding: decided again


class TestThePage(unittest.TestCase):
    def test_complete_once_the_report_is_written_and_the_users_enough_stands(self):
        from pertura import page_view, record as rc
        st = rc.empty()
        st["story"] = {"text": "The story.", "seq": 5}
        self.assertFalse(page_view.view(st)["complete"])  # written, but the user has not said enough
        st["enough"] = {"kind": "enough", "seq": 6}
        self.assertTrue(page_view.view(st)["complete"])
        st["threads"]["t9"] = {"thread_id": "t9", "seq": 7}
        self.assertFalse(page_view.view(st)["complete"])  # a thread opened after the enough

    def test_the_lead_and_plan_parts_show_once_they_hold_anything(self):
        import page
        c = mock.Mock(threads=[], results=[], methods=[], fids=[], steps=[], link=None, catalog={}, name="s",
                      rec={"reviews": []}, page={"design": {}, "plan": {}})
        with mock.patch.object(page, "_state", return_value=("still", "")):
            side = page._side(c, lambda x: x)
        self.assertNotIn("Research lead", side)
        self.assertNotIn(">Plan<", side)
        self.assertEqual(page._agent("pertura:checks"), "the checks")


if __name__ == "__main__":
    unittest.main()
