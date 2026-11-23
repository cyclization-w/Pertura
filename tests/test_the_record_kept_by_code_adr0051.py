"""ADR 0051: no agent writes the record. A recorder reads each turn and sends a recording to the project's inbox; the
service applies it before its next call, through its own methods, so every record rule holds and one process writes.
Code reads the fixed forms (a doubt's answer, a claim's correction, a next step's pick, a result kept or dropped);
a small model reads the rest. A range applied before is refused, and a user's decision whose words are not in the
user's message is refused: the user decides each result and the end."""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

from test_results_from_findings import TEXT, Service

from pertura import analysis_service as asv, record as rc, recording

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "plugin", "status"))
import recorder  # noqa: E402

RECORD_TOOLS = ("record_finding", "reword_finding", "open_thread", "thread_status", "note_reply", "raise_doubt",
                "answer_doubt", "report_result", "record_skeptic_check")  # the lead's and the figure reviewer's retired


def inbox(svc, tag, data):
    folder = os.path.join(svc.project, "record", "inbox")
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, tag + ".json"), "w", encoding="utf-8") as fh:
        json.dump(data, fh)


class TestNoAgentSeesARecordTool(unittest.TestCase):
    def test_the_record_tools_are_retired_for_the_agents_and_kept_for_the_inbox(self):
        shown = {t["name"] for t in asv.SHOWN}
        self.assertFalse(shown & set(RECORD_TOOLS))
        self.assertTrue(set(RECORD_TOOLS) <= asv.TOOL_NAMES)  # the inbox and the tests still call them
        for name in ("worker",):  # skeptic.md left the plugin (ADR 0052), the lead and the figure reviewer on 11-20
            with open(os.path.join(ROOT, "plugin", "agents", name + ".md"), encoding="utf-8") as fh:
                text = fh.read()
            self.assertFalse([t for t in RECORD_TOOLS if t in text], name)

    def test_the_plugins_schemas_are_the_models(self):
        for name, schema in (("recording_schema.json", recording.RECORDING_SCHEMA),
                             ("doubts_schema.json", recording.DOUBTS_SCHEMA)):
            with open(os.path.join(ROOT, "plugin", "status", name), encoding="utf-8") as fh:
                self.assertEqual(json.load(fh), schema, name)


class TestTheInbox(Service):
    def setUp(self):
        super().setUp()
        parts = {"cells": None, "reference": "control_guides", "kind": "gene", "genes": None, "state_table": None,
                 "lead": None}
        for p in (mock.patch.object(self.svc, "_finding_parts", return_value=parts),
                  mock.patch.object(self.svc, "_start_checks", return_value={"checks_run": "c1"}),
                  mock.patch.object(self.svc, "_confirmed", return_value={})):
            p.start()  # the claims' checks need a built screen object; the inbox's path is what is tested here
            self.addCleanup(p.stop)

    def claim(self, **more):
        return {"statement": "GATA1 knockdown raises SPI1 (217%) [run:r1]", "targets": ["GATA1"],
                "feature": {"kind": "gene", "name": "SPI1"}, "direction": "up", "run_ids": ["r1"], **more}

    def test_a_recording_is_applied_before_the_next_call_and_the_reply_names_it(self):
        inbox(self.svc, "t1", {"claims": [self.claim()],
                               "results": [{"title": "GATA1 knockdown raises SPI1", "text": TEXT, "claims": [0],
                                            "figure": "fig1"}],
                               "range": {"session": "s", "agent": "main", "first": "u1", "last": "u2"}})
        out = self.ok("pertura_status")
        self.assertEqual(out["recorded"][0]["file"], "t1")
        st = self.svc.record.state()
        self.assertIn("t1-c1", st["findings"])
        self.assertEqual(st["results"]["r1"]["finding_ids"], ["t1-c1"])
        applied = os.path.join(self.svc.project, "record", "inbox", "applied")
        self.assertTrue(os.path.exists(os.path.join(applied, "t1.json")))
        self.assertTrue(os.path.exists(os.path.join(applied, "t1.outcome.json")))
        with open(os.path.join(self.svc.project, "record", "digest.json"), encoding="utf-8") as fh:
            self.assertIn("t1-c1", json.load(fh)["findings"])

    def test_a_range_applied_before_is_refused_whole(self):
        rng = {"session": "s", "agent": "main", "first": "u1", "last": "u2"}
        inbox(self.svc, "t1", {"claims": [self.claim()], "range": rng})
        self.ok("pertura_status")
        inbox(self.svc, "t2", {"claims": [self.claim()], "range": rng})
        out = self.ok("pertura_status")
        self.assertIn("applied before", out["recorded"][0]["refused"][0]["why"])
        self.assertNotIn("t2-c1", self.svc.record.state()["findings"])

    def test_a_users_decision_needs_their_words_in_their_message(self):
        inbox(self.svc, "t1", {"claims": [self.claim()],
                               "results": [{"title": "GATA1 knockdown raises SPI1", "text": TEXT, "claims": [0],
                                            "figure": "fig1"}]})
        self.ok("pertura_status")
        inbox(self.svc, "t2", {"user_decisions": [{"kind": "keep", "words": "keep r1", "message_uuid": "m1",
                                                   "refers_to": "r1"}],
                               "user_messages": {"m1": "I'm not sure yet"}})
        out = self.ok("pertura_status")
        self.assertIn("not in the user's message", out["recorded"][0]["refused"][0]["why"])
        self.assertIsNone(self.svc.record.state()["results"]["r1"].get("decision"))
        inbox(self.svc, "t3", {"user_decisions": [{"kind": "keep", "words": "keep r1", "message_uuid": "m2",
                                                   "refers_to": "r1"}],
                               "user_messages": {"m2": "Yes, keep r1 as it is."}})
        self.ok("pertura_status")
        self.assertEqual(self.svc.record.state()["results"]["r1"]["decision"], "confirmed")

    def test_a_claim_without_runs_is_refused_and_a_correction_rewords_the_finding(self):
        inbox(self.svc, "t1", {"claims": [self.claim(run_ids=[]), self.claim()]})
        out = self.ok("pertura_status")
        self.assertEqual(out["recorded"][0]["refused"][0]["why"], "a claim names the runs it rests on")
        self.assertIn("t1-c2", self.svc.record.state()["findings"])
        inbox(self.svc, "t2", {"claims": [self.claim(statement="GATA1 knockdown lowers SPI1", direction="down",
                                                     run_ids=[], replaces="t1-c2")]})
        self.ok("pertura_status")
        self.assertEqual(self.svc.record.state()["findings"]["t1-c2"]["direction"], "down")

    def test_the_skeptics_doubts_are_raised_and_its_check_recorded(self):
        inbox(self.svc, "t1", {"claims": [self.claim()]})
        self.ok("pertura_status")
        inbox(self.svc, "t1-z-doubts", {"doubts": [{"finding_id": "t1-c1", "text": "rests on one guide",
                                                    "would_change": "the other guide lowering SPI1"}]})
        self.ok("pertura_status")
        st = self.svc.record.state()
        self.assertEqual(st["doubts"]["D1"]["opened_by"], "pertura:skeptic")
        inbox(self.svc, "t2", {"doubt_answers": [{"doubt_id": "D1", "outcome": "holds, narrowed",
                                                  "reason": "both guides raise it [run:r1]", "run_ids": ["r1"]}]})
        self.ok("pertura_status")
        self.assertEqual(self.svc.record.state()["doubts"]["D1"]["answer"]["outcome"],
                         "holds, narrowed")

    def test_a_gene_set_claim_carries_the_genes_the_agent_defined(self):
        inbox(self.svc, "t1", {"claims": [self.claim(feature={"kind": "gene_set", "name": "DAM_up",
                                                              "genes": ["APOE", "TREM2", "CD9"]})]})
        with mock.patch.object(self.svc, "record_finding", wraps=self.svc.record_finding) as rec:
            self.ok("pertura_status")
        self.assertEqual(rec.call_args.kwargs["genes"], ["APOE", "TREM2", "CD9"])

    def test_the_users_question_must_quote_their_message(self):
        """The second live trial: 'I don't know why the methods say 31 genes' became the user's question."""
        inbox(self.svc, "t1", {"threads": [{"about": "Why the methods say 31 genes", "raised_by": "the user",
                                            "scope": "one question"}],
                               "user_messages": {"m1": "I don't know why the methods say 31 genes and the reference 30."}})
        out = self.ok("pertura_status")
        self.assertEqual(out["recorded"][0]["refused"][0]["what"], "the user's question")

    def test_the_users_question_keeps_what_they_hand_over(self):
        """ADR 0045, D2: the survey runs once the user hands over the whole screen; one question runs only what
        answers it. The recorder carries the scope the agent once gave open_thread."""
        self.assertFalse(self.svc._survey_on())
        inbox(self.svc, "t1", {"threads": [{"about": "Analyse the whole screen for me", "raised_by": "the user",
                                            "scope": "the whole screen"}],
                               "user_messages": {"m1": "Analyse the whole screen for me, please."}})
        self.ok("pertura_status")
        self.assertTrue(self.svc._survey_on())

    def test_a_claim_whose_words_name_no_run_rests_on_the_turns_runs(self):
        """The live trial of 2026-11-17: nine claims named no run, and all were refused. The recorder now gives the
        turn's run IDs, read by code; the record keeps those it holds."""
        inbox(self.svc, "t1", {"claims": [self.claim(run_ids=[])], "turn_runs": ["r1", "run9999-abcdef"]})
        out = self.ok("pertura_status")
        self.assertFalse(out["recorded"][0]["refused"])
        self.assertEqual(self.svc.record.state()["findings"]["t1-c1"]["run_ids"], ["r1"])

    def test_a_result_takes_a_module_runs_own_figure(self):
        """The live trial of 2026-11-17: three results were refused, as the states run drew its figure but no saved
        figure named it."""
        rec = self.svc.record
        rec.append("run_logged", {"run_id": "r3", "what": "C.states_and_composition", "code": "c", "inputs": []},
                   rc.TOOL)
        rec.append("run_finished", {"run_id": "r3", "state": "done", "output": "o", "figure": "states.png"}, rc.TOOL)
        inbox(self.svc, "t1", {"claims": [self.claim(run_ids=["r3"])],
                               "results": [{"title": "GATA1 knockdown raises SPI1", "text": TEXT, "claims": [0]}]})
        out = self.ok("pertura_status")
        self.assertFalse(out["recorded"][0]["refused"], out["recorded"])
        self.assertEqual(self.svc.record.state()["results"]["r1"]["figure_run"], "r3")

    def test_every_unanswered_doubt_is_to_answer_and_the_reviews_line_is_said_once(self):
        """The loop decided by the record (2026-11-20): a review's doubts are doubts to answer, as the checks' are,
        and pending; the review's line on what the question lacks is information, said once; the checks open one
        doubt for a claim they say fails, never again when it is checked again."""
        inbox(self.svc, "t1", {"claims": [self.claim()]})
        self.ok("pertura_status")
        inbox(self.svc, "t1-z-doubts", {"doubts": [{"finding_id": "t1-c1", "text": "one guide",
                                                    "would_change": "the other guide"}],
                                        "the_question_lacks": "CEBPA, which the user named, has no result yet",
                                        "cited": ["t1-c1"]})
        out = self.ok("record_finding", finding_id="f9", statement="a description", kind="description", run_ids=["r1"])
        review = out["doubts_to_answer"]["doubts"]
        self.assertEqual([d["doubt_id"] for d in review], ["D1"])
        self.assertEqual((review[0]["opened_by"], review[0]["would_change"]), ("pertura:skeptic", "the other guide"))
        self.assertIn("doubts to answer", out["pending"][asv.MAIN])
        self.assertEqual(set(out["the_review"]), {"the_question_lacks", "do"})
        self.assertIn("CEBPA", out["the_review"]["the_question_lacks"])
        out = self.ok("record_finding", finding_id="f10", statement="another description", kind="description", run_ids=["r1"])
        self.assertNotIn("the_review", out)  # said once
        self.assertNotIn("doubts_to_answer", out)  # said already; named again when a new doubt comes
        self.svc.record.append("check_recorded", {"finding_id": "t1-c1", "check": "guides_agree",
                                                  "outcome": "disagree", "reason": "one guide of two"}, rc.TOOL)
        out = self.ok("record_finding", finding_id="f11", statement="a third description", kind="description", run_ids=["r1"])
        doubts = out["doubts_to_answer"]["doubts"]
        self.assertEqual([d["doubt_id"] for d in doubts], ["D1", "D2"])
        self.assertEqual(doubts[1]["opened_by"], asv.CHECKS_DOUBT)
        self.assertNotIn("would_change", doubts[1])
        self.assertIn("doubts to answer", out["pending"][asv.MAIN])
        self.assertIn("'Doubt <id>:", out["doubts_to_answer"]["do"])
        self.svc.record.append("check_recorded", {"finding_id": "t1-c1", "check": "guides_agree",
                                                  "outcome": "disagree", "reason": "checked again"}, rc.TOOL)
        inbox(self.svc, "t2", {"doubt_answers": [{"doubt_id": "D2", "outcome": "holds, narrowed",
                                                  "reason": "narrowed to the guide that works"}]})
        out = self.ok("record_finding", finding_id="f12", statement="a fourth description", kind="description", run_ids=["r1"])
        self.assertNotIn("doubts_to_answer", out)  # D1, still open, was said
        self.assertEqual([did for did, d in self.svc.record.state()["doubts"].items() if not d.get("answer")], ["D1"])
        opened = [d for d in self.svc.record.state()["doubts"].values() if d["opened_by"] == asv.CHECKS_DOUBT]
        self.assertEqual(len(opened), 1)  # one for the claim, never again when it is checked again

    def test_a_claim_holds_in_the_cells_the_agent_named(self):
        """The live trial of 2026-11-17: iMG-only claims were checked on iMG and iTF-MG cells together."""
        inbox(self.svc, "t1", {"claims": [self.claim(cells={"sample": "img"})]})
        with mock.patch.object(self.svc, "record_finding", wraps=self.svc.record_finding) as rec:
            with mock.patch.object(self.svc, "_kept_slice", return_value="sample_img"):
                self.ok("pertura_status")
        self.assertEqual(rec.call_args.kwargs["cells"], {"sample": "img"})

    def test_a_file_that_is_no_recording_is_refused_whole(self):
        inbox(self.svc, "t1", {"claims": [{"statement": "no targets"}]})
        out = self.ok("pertura_status")
        self.assertEqual(out["recorded"][0]["refused"][0]["what"], "the whole file")


class TestTheRolesRetired(Service):
    def test_a_switch_naming_a_retired_role_is_ignored_and_no_item_of_theirs_is_due(self):
        # the research lead and the figure reviewer, off by default since docs/adr/0052, moved to research on
        # 2026-11-20 with the advisor and the complete check: a workspace that still passes their switches opens
        arm = rc.Record(os.path.join(self.tmp.name, "arm"), {"research_lead": True, "figure_review": True})
        self.assertEqual(sorted(arm.switches), sorted(rc.PIECES))
        with self.assertRaises(rc.RecordError):  # a switch the record never had is still refused
            rc.Record(os.path.join(self.tmp.name, "typo"), {"robustnes": False})
        out = self.ok("pertura_status")
        for key in ("research_lead_due", "figure_set_review_due", "figures_to_draw", "results_the_lead_named",
                    "threads_the_lead_named"):
            self.assertNotIn(key, out)
        self.assertFalse(out["pending"].get("main agent"))


ENTRIES = [
    {"uuid": "u1", "type": "user", "message": {"role": "user", "content": "Analyse it: which regulators matter?"}},
    {"uuid": "a1", "type": "assistant", "message": {"role": "assistant", "content": [
        {"type": "text", "text": "GATA1 knockdown raises SPI1 by 217% [run:r1].\nDoubt d3: holds, narrowed. Both "
                                 "guides raise it [run:r1].\nClaim c1: targets GATA1; feature gene SPI1; direction up"},
        {"type": "tool_use", "name": "mcp__plugin_pertura_pertura__genes", "input": {"targets": ["GATA1"]}}]}},
    {"uuid": "u2", "type": "user", "message": {"role": "user", "content": [
        {"type": "tool_result", "content": "{\"run_id\": \"r1\"}"}]}},
    {"uuid": "u3", "type": "user", "message": {"role": "user", "content":
        "Let's take your recommended next step, option 2: Which programmes do they share? And keep r1."}},
]


class TestTheRecorder(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp()
        self.env = mock.patch.dict(os.environ, {"PERTURA_STATUS_HOME": self.home})
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_code_reads_the_fixed_forms(self):
        text, users, first, last = recorder.turn_of(ENTRIES)
        self.assertEqual((first, last), ("u1", "u3"))
        self.assertIn("CALL genes", text)
        fixed = recorder.fixed_forms(text, users)
        self.assertEqual(fixed["doubt_answers"][0], {"doubt_id": "d3", "outcome": "holds, narrowed",
                                                     "reason": "Both guides raise it [run:r1]."})
        self.assertEqual(fixed["claims"][0]["replaces"], "c1")
        kinds = sorted((u["kind"], u["refers_to"]) for u in fixed["user_decisions"])
        self.assertEqual(kinds, [("keep", "r1"), ("next_step", "2")])

    def test_a_turn_is_recorded_once_with_its_range_and_the_users_messages(self):
        path = os.path.join(self.home, "t.jsonl")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(json.dumps(e) for e in ENTRIES))
        sent = []
        model = {"claims": [{"statement": "GATA1 knockdown raises SPI1", "targets": ["GATA1"],
                             "feature": {"kind": "gene", "name": "SPI1"}, "direction": "up", "run_ids": ["r1"]}],
                 "doubt_answers": [{"doubt_id": "d3", "outcome": "holds", "reason": "a reading of its own"}]}
        with mock.patch.object(recorder, "send", lambda h, p, tag, rec: sent.append((tag, rec))), \
                mock.patch.object(recorder, "digest_of", return_value="{}"):
            tag = recorder.record_turn("s1", path, host="host", project="/w/p-1",
                                       ask=lambda *a, **k: model)
            again = recorder.record_turn("s1", path, host="host", project="/w/p-1",
                                         ask=lambda *a, **k: model)
        self.assertIsNotNone(tag)
        self.assertIsNone(again)  # the mark moved: nothing new since
        rec = sent[0][1]
        self.assertEqual(rec["range"], {"session": "s1", "agent": "main", "first": "u1", "last": "u3"})
        self.assertIn("u3", rec["user_messages"])
        self.assertEqual([a["outcome"] for a in rec["doubt_answers"]], ["holds, narrowed"])  # code's line first
        self.assertEqual(len(rec["claims"]), 2)  # the correction, then the model's claim
        recording.Recording.model_validate({k: v for k, v in rec.items()
                                            if k not in ("range", "user_messages", "turn_runs")})

    def test_a_prompts_fixed_words_go_at_once_and_are_not_taken_again(self):
        sent = []
        with mock.patch.object(recorder, "send", lambda h, p, tag, rec: sent.append((tag, rec))):
            tag = recorder.record_prompt({"session_id": "s1", "prompt": ENTRIES[3]["message"]["content"]},
                                         host="host", project="/w/p-1")
        self.assertIn("prompt-", tag)
        self.assertEqual(sorted(u["kind"] for u in sent[0][1]["user_decisions"]), ["keep", "next_step"])
        handled = {recorder.prompt_hash(ENTRIES[3]["message"]["content"])}
        text, users, _, _ = recorder.turn_of(ENTRIES, handled=handled)
        self.assertIn("applied already)", text)
        self.assertFalse(recorder.fixed_forms(text, users, handled)["user_decisions"])


    def test_each_role_records_only_its_own_part_and_hook_feedback_is_not_the_user(self):
        """The trial of 2026-11-17: the lead's restated claims were recorded as new claims and its remarks on doubts
        as their answers; the Stop hook's feedback came as a user turn."""
        path = os.path.join(self.home, "lead.jsonl")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(json.dumps(e) for e in ENTRIES))
        model = {"claims": [{"statement": "x", "targets": ["A"], "feature": {"kind": "gene", "name": "B"},
                             "direction": "up", "run_ids": ["r1"]}],
                 "doubt_answers": [{"doubt_id": "d1", "outcome": "holds", "reason": "r"}],
                 "threads": [{"about": "probe the states", "raised_by": "a worker"}]}
        sent = []
        with (mock.patch.object(recorder, "send", lambda h, p, tag, rec: sent.append(rec)),
              mock.patch.object(recorder, "digest_of", return_value="{}")):
            # the research lead's own part left with it (2026-11-20): an agent with no part of its own records none
            for agent in ("pertura:research-lead", "pertura:worker"):
                recorder.record_turn("s1", path, agent=agent, agent_id=agent, host="host", project="/w/p-1",
                                     ask=lambda *a, **k: model)
        retired, worker = sent
        self.assertEqual((retired["claims"], retired["doubt_answers"], retired["threads"]), ([], [], []))
        # the worker keeps its claims: the model's, and the correction line its text holds
        self.assertEqual((len(worker["claims"]), worker["doubt_answers"]), (2, []))
        self.assertNotIn("lead_reviews", worker)
        entries = ENTRIES + [{"uuid": "u9", "type": "user", "message": {"content": "Stop hook feedback:\nStill due"}}]
        self.assertNotIn("u9", recorder.turn_of(entries)[1])

    def test_the_main_agents_relayed_review_is_not_recorded_and_a_read_answer_says_so(self):
        merged = recorder.merged({"claims": [], "doubt_answers": [], "user_decisions": []},
                                 {"doubt_answers": [{"doubt_id": "d1", "outcome": "holds", "reason": "both guides"}],
                                  "lead_reviews": [{"verdict": "not yet", "story": "s"}]})
        self.assertTrue(merged["doubt_answers"][0]["reason"].startswith(recorder.READ_FROM_PROSE))
        self.assertNotIn("lead_reviews", recorder.ROLE_FIELDS["main"])

    def test_the_turns_runs_are_read_by_code(self):
        entries = [{"uuid": "a", "type": "assistant", "message": {"content": [
                       {"type": "tool_use", "name": "x", "input": {"run_ids": ["run0001-a1b2c3"]}}]}},
                   {"uuid": "b", "type": "user", "message": {"content": [
                       {"type": "tool_result", "content": "{\"run_id\": \"20261117T015243-C-genes-1ecf5e\"}"}]}}]
        self.assertEqual(recorder.turn_runs(entries), ["run0001-a1b2c3", "20261117T015243-C-genes-1ecf5e"])
        self.assertEqual(recorder.turn_runs(entries, after="a"), ["20261117T015243-C-genes-1ecf5e"])

    def test_a_subagents_brief_is_never_the_users_words(self):
        """The trial of 2026-11-17: the design auditor's brief was read as the user's main line, its measurements as
        claims and its summary as a result. A subagent's user turn is the main agent's brief."""
        path = os.path.join(self.home, "worker.jsonl")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(json.dumps(e) for e in ENTRIES))
        sent = []
        model = {"claims": [], "results": [{"title": "t", "text": "x"}],
                 "user_decisions": [{"kind": "main_line", "words": "keep r1", "message_uuid": "u3"}],
                 "threads": [{"about": "the whole screen", "raised_by": "the user", "scope": "the whole screen"}]}
        with (mock.patch.object(recorder, "send", lambda h, p, tag, rec: sent.append(rec)),
              mock.patch.object(recorder, "digest_of", return_value="{}")):
            recorder.record_turn("s1", path, agent="pertura:worker", agent_id="w1", host="host", project="/w/p-1",
                                 ask=lambda *a, **k: model)
            skipped = recorder.record_turn("s1", path, agent="pertura:design-auditor", agent_id="d1", host="host",
                                           project="/w/p-1", ask=lambda *a, **k: model)
        rec = sent[0]
        self.assertEqual((rec["results"], rec["user_decisions"], rec["threads"], rec["user_messages"]), ([], [], [], {}))
        self.assertIsNone(skipped)
        text, users, _, _ = recorder.turn_of(ENTRIES, subagent=True)
        self.assertIn("BRIEF: Analyse it", text)
        self.assertEqual(users, {})

    def test_a_finished_run_is_recorded_offline_turn_by_turn(self):
        path = os.path.join(self.home, "run.jsonl")
        rows = [{k: v for k, v in e.items() if k != "uuid"} for e in ENTRIES[:2]] + [{"type": "result"}] + \
               [{k: v for k, v in e.items() if k != "uuid"} for e in ENTRIES[3:]]
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(json.dumps(r) for r in rows))
        out = os.path.join(self.home, "recordings")
        files = recorder.record_offline(path, out, ask=lambda *a, **k: {"claims": []})
        self.assertEqual([os.path.basename(f) for f in files], ["turn001.json", "turn002.json"])
        with open(files[1], encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["user_decisions"][0]["kind"], "next_step")


if __name__ == "__main__":
    unittest.main()
