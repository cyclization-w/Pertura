"""The loop decided by the record, its recorder and Stop side (the design note of 2026-11-20, approved at 21:10).

Arm L's four graded runs on f2c247f held 73 claims, of which 12 were checked, and no doubt: the recorder read the main
agent only once its turn had ended, and the Stop hook decided before that turn was recorded. Now:
- after each main-agent analysis call the recorder reads the turn so far, one reading at a time, a call during a
  reading leaving it a flag to read again; a claim's runs are the turn's from its start; a text the model returns no
  recording for is read again once, then left with a line;
- the skeptic's file is applied after its own turn's, and says what the skeptic was given;
- the paths of the reads over ssh come from the status file where Git Bash rewrote the plugin's options;
- at Stop the turn is recorded and the record read: checks running and a reading that runs send the agent back to
  wait (for at most 20 minutes in a stretch), doubts with no answer and the agent's own task list not done send it
  back up to three times in a turn, and the turn ends while a wait is armed; where the record holds a finding, no task
  list and no Choice or No choice line are each asked once a session, among those returns.
"""
import json
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

from pertura import recording
from test_results_from_findings import Service
from test_the_record_kept_by_code_adr0051 import inbox

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "plugin", "status"))
import hooks  # noqa: E402
import recorder  # noqa: E402
import status_file as sf  # noqa: E402
import stop_words  # noqa: E402

NOW = 1_800_000_000.0
RUN1, RUN2 = "run0001-aaaaaa", "run0002-bbbbbb"


def entry(uuid, kind, content):
    return {"uuid": uuid, "type": kind, "message": {"role": kind, "content": content}}


def write(path, entries):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(json.dumps(e) for e in entries) + "\n")
    return path


class Home(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp()
        env = mock.patch.dict(os.environ, {"PERTURA_STATUS_HOME": self.home})
        env.start()
        self.addCleanup(env.stop)


class TestTheRecorderReadsAsTheAgentWorks(Home):
    def test_only_a_main_agent_analysis_call_starts_a_queued_reading(self):
        started = []
        call = {"session_id": "s1", "transcript_path": "t.jsonl", "tool_name": sf.TOOL_PREFIX + "genes"}
        hooks.start_reading(call, start=lambda *a, **k: started.append((a, k)))
        hooks.start_reading({**call, "agent_id": "w1"}, start=lambda *a, **k: started.append((a, k)))
        hooks.start_reading({**call, "tool_name": sf.TOOL_PREFIX + "pertura_status"},
                            start=lambda *a, **k: started.append((a, k)))
        self.assertEqual(started, [(("s1", "t.jsonl"), {"queue": True})])

    def test_a_reading_during_a_reading_leaves_a_flag_that_the_running_one_reads(self):
        lock = os.path.join(recorder._folder(), "s1.lock")
        open(lock, "w").close()  # a reading runs
        read = []
        with mock.patch.object(recorder, "record_turn", lambda s, t, a, i, **k: read.append((t, a))):
            self.assertEqual(recorder.main(["--session", "s1", "--transcript", "main.jsonl", "--queue"]), 0)
            self.assertEqual(read, [])  # it ended at once, leaving its flag
            self.assertEqual(len(recorder._flags("s1")), 1)
            os.remove(lock)  # the running reading ends; the next one reads the flag after its own text
            recorder.main(["--session", "s1", "--transcript", "worker.jsonl", "--agent", "pertura:worker",
                           "--agent-id", "w1", "--queue"])
        self.assertEqual(read, [("worker.jsonl", "pertura:worker"), ("main.jsonl", "main")])
        self.assertEqual(recorder._flags("s1"), [])
        self.assertFalse(os.path.exists(lock))

    def test_a_text_with_no_recording_is_read_again_once_then_left(self):
        path = write(os.path.join(self.home, "t.jsonl"), [
            entry("u1", "user", "Analyse it: the regulators."),
            entry("a1", "assistant", [{"type": "text", "text": "GATA1 knockdown raises SPI1."}])])
        sent = []
        with mock.patch.object(recorder, "send", lambda h, p, tag, rec: sent.append(rec)), \
                mock.patch.object(recorder, "digest_of", return_value="{}"):
            first = recorder.record_turn("s1", path, host="host", project="/w/p-1", ask=lambda *a, **k: None)
            self.assertIsNone(first)
            self.assertEqual(sent, [])
            self.assertTrue(recorder.unread("s1", path))  # the mark did not move
            again = recorder.record_turn("s1", path, host="host", project="/w/p-1", ask=lambda *a, **k: None)
        self.assertIsNotNone(again)  # the second failure sends code's fixed forms alone and moves the mark
        self.assertEqual(len(sent), 1)
        self.assertFalse(recorder.unread("s1", path))

    def test_a_claims_runs_are_the_turns_from_its_start(self):
        """With a reading at each call, a claim summing up earlier runs named only the last call's run, or none."""
        entries = [entry("u1", "user", "Analyse it: the regulators."),
                   entry("a1", "assistant", [{"type": "tool_use", "name": "genes", "input": {}}]),
                   entry("r1", "user", [{"type": "tool_result", "content": json.dumps({"run_id": RUN1})}])]
        path = write(os.path.join(self.home, "t.jsonl"), entries)
        sent, model = [], {"claims": []}
        with mock.patch.object(recorder, "send", lambda h, p, tag, rec: sent.append(rec)), \
                mock.patch.object(recorder, "digest_of", return_value="{}"):
            recorder.record_turn("s1", path, host="host", project="/w/p-1", ask=lambda *a, **k: model)
            write(path, entries + [entry("a2", "assistant", [{"type": "text", "text": "GATA1 raises SPI1."}])])
            recorder.record_turn("s1", path, host="host", project="/w/p-1", ask=lambda *a, **k: model)
            write(path, entries + [entry("a2", "assistant", [{"type": "text", "text": "GATA1 raises SPI1."}]),
                                   entry("u2", "user", "Now the programmes."),
                                   entry("r2", "user", [{"type": "tool_result", "content": RUN2}])])
            recorder.record_turn("s1", path, host="host", project="/w/p-1", ask=lambda *a, **k: model)
        self.assertEqual([r["turn_runs"] for r in sent], [[RUN1], [RUN1], [RUN2]])  # a new message starts a turn


class TestTheSkepticsFile(Home):
    def test_it_is_applied_after_its_own_turns_file(self):
        with tempfile.TemporaryDirectory() as project:
            inbox = os.path.join(project, "record", "inbox")
            os.makedirs(inbox)
            names = ["20261120-101000-aaaaaaaa-z-doubts.json", "20261120-101000-aaaaaaaa.json",
                     "20261120-101500-bbbbbbbb.json"]
            for n in names:
                open(os.path.join(inbox, n), "w").close()
            self.assertEqual([os.path.basename(p) for p in recording.inbox_files(project)],
                             [names[1], names[0], names[2]])

    def test_it_says_what_the_skeptic_was_given_and_apply_leaves_that_out(self):
        rec = {"claims": [{"statement": "s", "targets": ["A"], "feature": {"kind": "gene", "name": "G"},
                           "direction": "up", "run_ids": [RUN1]}], "results": [{"claims": [0]}],
               "range": {"session": "s1"}}
        sent = []
        with mock.patch.object(recorder, "checks_of", return_value={"t-c1": {"lead": {"outcome": "holds"}}}), \
                mock.patch.object(recorder, "run_outputs", return_value={RUN1: {"code": "c", "output": "o"}}), \
                mock.patch.object(recorder, "reader_facts", return_value={"the_users_words": ["w"],
                                                                         "doubts_the_record_holds": []}), \
                mock.patch.object(recorder, "send", lambda h, p, tag, d: sent.append(d)):
            recorder.read_by_the_skeptic("host", "/w/p-1", "t", rec, ask=lambda *a, **k: {"doubts": [],
                                                                                         "the_question_lacks": ""})
        self.assertEqual(sent[0]["received"], {"claims_with_checks": 1, "runs": 1, "users_words": 1,
                                               "record_doubts": 0})  # sent though it raised nothing
        svc = mock.Mock()
        svc.project = self.home
        os.makedirs(os.path.join(self.home, "record"), exist_ok=True)
        svc.record.state.return_value = {"findings": {}, "runs": {}}
        out = recording.apply(svc, dict(sent[0]), "t-z-doubts")
        self.assertNotIn("the whole file", json.dumps(out["refused"]))


class TestTheSkepticWaitsOnlyForChecksThatWillCome(Home):
    """A claim kept as a description gets no checks, and the skeptic waited the full 900 s for it (dc's map, §3)."""

    def read(self, described):
        rec = {"claims": [{"statement": "s", "targets": ["A"], "feature": {"kind": "gene", "name": "G"},
                           "direction": "up", "run_ids": [RUN1]}], "results": [{"claims": [0]}],
               "range": {"session": "s1"}}
        slept = []
        with mock.patch.object(recorder, "checks_of", return_value={"t-c1": {}}), \
                mock.patch.object(recorder, "read_stop_facts", return_value={"described": described}), \
                mock.patch.object(recorder, "run_outputs", return_value={}), \
                mock.patch.object(recorder, "reader_facts", return_value={}), \
                mock.patch.object(recorder, "send", lambda *a: None), \
                mock.patch.object(recorder.time, "sleep", lambda s: slept.append(s)):
            recorder.read_by_the_skeptic("host", "/w/p-1", "t", rec, wait=65,
                                         ask=lambda *a, **k: {"doubts": [], "the_question_lacks": ""})
        return slept

    def test_a_description_is_not_waited_for_and_a_claim_whose_checks_are_queued_is(self):
        self.assertEqual(self.read([{"finding_id": "t-c1", "why": "no feature", "to_check_it": "..."}]), [])
        self.assertTrue(self.read([]))


class TestThePathsOfTheReadsOverSsh(Home):
    def test_a_path_git_bash_rewrote_gives_way_to_the_ones_the_server_named(self):
        sf.record_call({"session_id": "s1", "tool_name": sf.TOOL_PREFIX + "open_project",
                        "tool_response": {"project": "/w/p-1", "remote_code": "/srv/pertura-core",
                                          "remote_python": "/srv/envs/bin/python"}}, self.home, now=NOW)
        rewritten = {"CLAUDE_PLUGIN_OPTION_REMOTE_CODE": "D:/Git/Git/srv/pertura-core",
                     "CLAUDE_PLUGIN_OPTION_REMOTE_PYTHON": "D:/Git/Git/srv/envs/bin/python"}
        with mock.patch.dict(os.environ, rewritten):
            self.assertEqual(recorder.remote_paths("/w/p-1"), ("/srv/pertura-core", "/srv/envs/bin/python"))
        given = {"CLAUDE_PLUGIN_OPTION_REMOTE_CODE": "/opt/code", "CLAUDE_PLUGIN_OPTION_REMOTE_PYTHON": "/opt/py"}
        with mock.patch.dict(os.environ, given):
            self.assertEqual(recorder.remote_paths("/w/p-1"), ("/opt/code", "/opt/py"))
        with mock.patch.dict(os.environ, rewritten):
            self.assertEqual(recorder.remote_paths("/w/another"), (None, None))


def facts(**open_):
    return lambda event, st: {"checks_running": [], "doubts_open": [], "choices_open": [], "described": [],
                              "reading": False, **open_}


class TestTheStopReadsTheRecord(Home):
    def setUp(self):
        super().setUp()
        sf.record_call({"session_id": "s1", "tool_name": sf.TOOL_PREFIX + "open_project",
                        "tool_response": {"project": "/w/p-1"}}, self.home, now=NOW)
        self.path = write(os.path.join(self.home, "t.jsonl"), [
            entry("u1", "user", "Analyse it: the regulators."),
            entry("a1", "assistant", [{"type": "text", "text": "I recorded the first findings."}])])

    def stop(self, found, now=NOW, path=None):
        return hooks.stop({"session_id": "s1", "transcript_path": path or self.path}, self.home, now=now, facts=found)

    def test_checks_running_send_it_back_to_arm_a_wait_and_an_armed_wait_ends_the_turn(self):
        out = self.stop(facts(checks_running=["f1", "f2"]))
        self.assertIn("checks still running on f1, f2: arm wait_for_runs", out["reason"])
        hooks.before_wait({"session_id": "s1", "tool_name": sf.TOOL_PREFIX + "wait_for_runs",
                           "tool_input": {"minutes": 20}}, self.home)
        self.assertIsNone(self.stop(facts(checks_running=["f1"]), now=time.time()))

    def test_waiting_is_capped_at_twenty_minutes_in_a_stretch_and_not_counted(self):
        for i in range(5):  # waits are not among the three returns
            self.assertEqual(self.stop(facts(checks_running=["f1"]), now=NOW + i)["decision"], "block")
        self.assertIsNone(self.stop(facts(checks_running=["f1"]), now=NOW + hooks.STOP_WAITS + 1))
        self.stop(facts(), now=NOW + hooks.STOP_WAITS + 2)  # nothing left to wait on: the stretch ends
        self.assertEqual(self.stop(facts(checks_running=["f3"]), now=NOW + 2 * hooks.STOP_WAITS)["decision"],
                         "block")

    def test_doubts_with_no_answer_send_it_back_three_times_in_a_turn(self):
        found = facts(doubts_open=["d1"])
        reasons = [self.stop(found) for _ in range(4)]
        self.assertEqual([r and r["decision"] for r in reasons], ["block", "block", "block", None])
        self.assertIn("Doubt <id>: <holds | holds, narrowed | weakens | overturns | stated as a limit>",
                      reasons[0]["reason"])
        later = write(os.path.join(self.home, "t2.jsonl"), [entry("u9", "user", "Go on with the programmes.")])
        self.assertEqual(self.stop(found, path=later)["decision"], "block")  # the user's new message: a new turn

    def test_descriptions_inform_and_never_block(self):
        described = [{"finding_id": "f4", "why": "no feature", "to_check_it": "Claim f4: targets A; feature gene G; "
                                                                              "direction up"}]
        self.assertIsNone(self.stop(facts(described=described)))
        out = self.stop(facts(doubts_open=["d1"], described=described))
        self.assertIn("kept as descriptions, with no checks (information): f4: Claim f4", out["reason"])

    def test_a_reading_that_runs_sends_it_back_to_wait(self):
        self.assertIn("a fresh reading of your results is still running", self.stop(facts(reading=True))["reason"])

    def test_its_own_feedback_is_not_read_as_the_users_message(self):
        """Its words say "wait" (arm wait_for_runs), which read as the user's stop and let the next Stop through."""
        path = write(os.path.join(self.home, "t3.jsonl"), [
            entry("u1", "user", "Analyse it: the regulators."),
            entry("a1", "assistant", [{"type": "text", "text": "Done for now."}]),
            entry("u2", "user", "Stop hook feedback:\n" + stop_words.ARM),
            entry("a2", "assistant", [{"type": "text", "text": "Done."}])])
        self.assertEqual(stop_words.turn_key({"transcript_path": path}), "u1")
        self.assertEqual(self.stop(facts(doubts_open=["d1"]), path=path)["decision"], "block")

    def test_with_no_record_to_read_the_old_rules_stand(self):
        self.assertIsNone(self.stop(lambda event, st: None))
        self.assertIsNone(hooks.record_facts({"session_id": "s1"}, {"project": "/w/p-1"}))  # no paths: no ssh

    def test_the_hooks_file_gives_the_stop_time_to_record(self):
        with open(os.path.join(ROOT, "plugin", "hooks", "hooks.json"), encoding="utf-8") as fh:
            stop = json.load(fh)["hooks"]["Stop"][0]["hooks"][0]
        self.assertGreaterEqual(stop["timeout"], hooks.STOP_BUDGET + 10)


class TestTheStopsRecordingNeverHoldsTheLock(Home):
    def test_it_waits_for_the_recording_to_be_sent_and_no_longer(self):
        class Ended:
            def poll(self):
                return 0
        with mock.patch.object(recorder, "start", lambda *a, done=None, **k: open(done, "w").close() or Ended()):
            self.assertTrue(recorder.record_now("s1", "t.jsonl", wait=5))
        with mock.patch.object(recorder, "start", lambda *a, **k: Ended()):  # it ended without sending
            t0 = time.time()
            self.assertFalse(recorder.record_now("s1", "t.jsonl", wait=30))
            self.assertLess(time.time() - t0, 5)


class TestAChoiceIsTriedByAWorker(Service):
    """The second trial on f2c247f: every threshold and definition was the agent's own, one of each, and no worker was
    sent to try another (docs/adr/0032, D9, revived at 21:50)."""

    def apply(self, tag, data):
        inbox(self.svc, tag, {"range": {"session": "s", "agent": "main", "first": tag + "a", "last": tag + "b"},
                              **data})
        return self.ok("pertura_status")["recorded"][0]

    def test_the_choice_line_opens_one_thread_that_tries_the_other_value_and_a_repeat_none(self):
        line = "**Choice:** the q cut-off for changed genes = 0.05; other: 0.1."
        choices = recorder.fixed_forms(line, {})["choice_lines"]
        tries = {"what": "the q cut-off for changed genes", "value": "0.05", "other": "0.1"}
        self.assertEqual(choices, [tries])
        self.apply("t1", {"choice_lines": choices})
        opened = [th for th in self.svc.record.state()["threads"].values() if th.get("tries")]
        self.assertEqual([(th["tries"], th["about"]) for th in opened],
                         [(tries, "the q cut-off for changed genes: 0.1 instead of 0.05")])
        out = self.apply("t2", {"choice_lines": choices})
        self.assertEqual(len([th for th in self.svc.record.state()["threads"].values() if th.get("tries")]), 1)
        self.assertIn("its thread is open already", json.dumps(out.get("notes")))

    def test_a_fixed_line_may_be_an_item_of_a_list(self):
        """Dev run 5 on GSE342764: the agent answered the ask for choices with a list ("- Choice: ...", "- No choice:
        ..."), and no line of it was read."""
        forms = recorder.fixed_forms("\n".join([
            "AGENT: The choices:",
            "- Choice: strongest shift = largest share shift; other: most significant.",
            "* No choice: the observation unit. The captures are technical replicates.",
            "1. Doubt D3: holds. Each guide alone agrees [run0060-f1e908].",
            "2) Claim f4: targets A; feature gene G; direction up",
            "+ From r2: which programme moves"]), {})
        self.assertEqual(forms["choice_lines"], [{"what": "strongest shift", "value": "largest share shift",
                                                  "other": "most significant"}])
        self.assertEqual(forms["no_choice_lines"],
                         [{"reason": "the observation unit. The captures are technical replicates."}])
        self.assertEqual([(a["doubt_id"], a["outcome"]) for a in forms["doubt_answers"]], [("D3", "holds")])
        self.assertEqual(forms["claims"][0]["statement"], "Claim f4: targets A; feature gene G; direction up")
        self.assertEqual(forms["from_lines"], [{"raised_by": "r2", "answers": "which programme moves"}])
        self.assertEqual(recorder.fixed_forms("- choices matter here\n-Choice: a = b; other: c", {})["choice_lines"],
                         [])

    def test_a_worker_reading_declares_no_choice_for_the_record(self):
        out = self.apply("t1", {"range": {"session": "s", "agent": "w1", "first": "a", "last": "b"},
                                "choice_lines": [{"what": "min cells", "value": "30", "other": "10"}]})
        self.assertFalse([th for th in self.svc.record.state()["threads"].values() if th.get("tries")])
        self.assertNotIn("the choice", json.dumps(out))

    def test_the_stop_names_the_choices_no_worker_has_tried(self):
        reason = stop_words.open_items({"choices_open": [{"thread_id": "t3", "what": "the q cut-off"}]}, False)
        self.assertIn("choices no worker has tried yet: t3 (the q cut-off). Hand each to a worker", reason)


class TestTheStopAfterDevRun1(TestTheStopReadsTheRecord):
    def decided(self):
        return sf.read_json(sf.session_path("s1", self.home, ".stop.json"))

    def test_claims_not_yet_in_the_record_send_it_back_to_make_one_call(self):
        out = self.stop(facts(inbox=2))
        self.assertIn("your last claims are not in the record yet: make one call (pertura_status)", out["reason"])
        self.assertEqual((self.decided()["decision"], self.decided()["returns"]), ("back", 0))  # a wait: uncounted

    def test_with_the_record_readable_its_items_alone_send_it_back_and_not_the_pending_list(self):
        sf.record_call({"session_id": "s1", "tool_name": sf.TOOL_PREFIX + "pertura_status",
                        "tool_response": {"project": "/w/p-1",
                                          "pending": {"main agent": ["threads waiting"]}}},
                       self.home, now=NOW)
        self.assertIsNone(self.stop(facts()))  # nothing open in the record: the old item says nothing to it
        self.assertEqual(self.decided()["decision"], "end")
        self.assertIn("threads waiting", self.stop(lambda e, st: None)["reason"])  # no record: as before

    def test_the_decision_file_says_back_wait_or_end_with_what_is_open(self):
        found = facts(doubts_open=["d1"])
        self.stop(found)
        d = self.decided()
        self.assertEqual((d["decision"], d["returns"], d["open"]["doubts_open"]), ("back", 1, ["d1"]))
        self.assertTrue({"decision", "open", "returns", "waits_since", "at"} <= set(d))
        self.stop(found), self.stop(found)
        self.assertIsNone(self.stop(found))  # the caps are spent: the turn ends, and says so
        self.assertEqual(self.decided()["decision"], "end")
        hooks.before_wait({"session_id": "s1", "tool_name": sf.TOOL_PREFIX + "wait_for_runs",
                           "tool_input": {"minutes": 20}}, self.home)
        self.assertIsNone(self.stop(facts(checks_running=["f1"]), now=time.time()))
        d = self.decided()
        self.assertEqual((d["decision"], d["open"]["checks_running"], d["open"]["wait_armed"]), ("wait", ["f1"], True))


class TestTheSkepticReadsATurnsClaimsAtItsEnd(Home):
    def test_the_stops_recording_with_claims_and_no_result_is_read_citing_them(self):
        path = write(os.path.join(self.home, "t.jsonl"), [
            entry("u1", "user", "Analyse it."), entry("a1", "assistant", [{"type": "text", "text": "Found."}])])
        claim = {"statement": "s", "targets": ["A"], "feature": {"kind": "gene", "name": "G"}, "direction": "up",
                 "run_ids": [RUN1]}
        later, cited = [], []
        with mock.patch.object(recorder, "send", lambda *a: None), \
                mock.patch.object(recorder, "digest_of", return_value="{}"):
            recorder.record_turn("s1", path, host="host", project="/w/p-1", later=later,
                                 ask=lambda *a, **k: {"claims": [claim, claim]})
            self.assertEqual(later, [])  # a reading during the turn: no result, no skeptic
            write(path, [entry("u1", "user", "Analyse it."), entry("a2", "assistant", [{"type": "text", "text": "x"}])])
            recorder.record_turn("s1", path, host="host", project="/w/p-1", later=later, at_stop=True,
                                 ask=lambda *a, **k: {"claims": [claim, claim]})
        self.assertEqual(len(later), 1)
        host, project, tag, rec = later[0]
        with mock.patch.object(recorder, "checks_of", lambda h, p, ids: cited.extend(ids) or {}), \
                mock.patch.object(recorder, "run_outputs", return_value={}), \
                mock.patch.object(recorder, "reader_facts", return_value={}), \
                mock.patch.object(recorder, "send", lambda *a: None):
            recorder.read_by_the_skeptic(host, project, tag, rec, ask=lambda *a, **k: {"doubts": []})
        self.assertEqual(cited, [f"{tag}-c1", f"{tag}-c2"])

    def test_a_turn_has_one_fresh_reading_at_its_end_and_a_later_stops_recording_still_goes(self):
        """Dev run 5 on GSE342764: each Stop's recording started a reading, the next Stop waited on it, its doubts were
        answered and the next Stop started another, about 8 times, to the budget's cap."""
        claim = {"statement": "s", "targets": ["A"], "feature": {"kind": "gene", "name": "G"}, "direction": "up",
                 "run_ids": [RUN1]}
        turn = [entry("u1", "user", "Analyse it.")]
        path = os.path.join(self.home, "t.jsonl")
        later, sent = [], []
        with mock.patch.object(recorder, "send", lambda *a: sent.append(a[2])), \
                mock.patch.object(recorder, "digest_of", return_value="{}"):
            for i, results in enumerate(([], [], [{"finding_ids": ["f1"]}])):  # a result too, at a later Stop
                turn.append(entry(f"a{i}", "assistant", [{"type": "text", "text": f"answered {i}"}]))
                write(path, turn)
                recorder.record_turn("s1", path, host="host", project="/w/p-1", later=later, at_stop=True,
                                     ask=lambda *a, **k: {"claims": [claim], "results": results})
            self.assertEqual((len(sent), len(later)), (3, 1))
            turn.append(entry("u2", "user", "Now the programmes."))  # the user's next turn: a reading again
            turn.append(entry("a9", "assistant", [{"type": "text", "text": "found"}]))
            write(path, turn)
            recorder.record_turn("s1", path, host="host", project="/w/p-1", later=later, at_stop=True,
                                 ask=lambda *a, **k: {"claims": [claim]})
        self.assertEqual(len(later), 2)


class TestStopFactsCountsTheInbox(Service):
    def test_a_file_the_record_has_not_taken_is_counted(self):
        record_dir = os.path.join(self.svc.project, "record")
        self.assertEqual(recording.stop_facts(record_dir)["inbox"], 0)
        inbox(self.svc, "t1", {"claims": []})
        self.assertEqual(recording.stop_facts(record_dir)["inbox"], 1)


def todo_write(uuid, todos, sidechain=False):
    return {"uuid": uuid, "type": "assistant", **({"isSidechain": True} if sidechain else {}), "message": {
        "role": "assistant", "content": [{"type": "tool_use", "name": "TodoWrite", "input": {"todos": [
            {"content": c, "status": s, "activeForm": c} for c, s in todos]}}]}}


class TestTheAgentsTodoList(TestTheStopReadsTheRecord):
    """The question as the agent's own todo list (Claude Code's TodoWrite), approved 2026-11-21: in dev run 3 on
    GSE342764 the recorder's parts took an instruction as a part, dropped each question's details, and every part was
    closed by citing a run."""

    def test_the_main_agents_last_list_holds_the_turn_until_each_item_is_completed(self):
        listed = [entry("u1", "user", "Analyse it: the regulators."),
                  todo_write("a1", [("which targets shift states", "completed"),
                                    ("the programme of the strongest shift", "in_progress")]),
                  todo_write("w1", [("a worker's own step", "pending")], sidechain=True),  # a subagent's list
                  entry("a2", "assistant", [{"type": "text", "text": "Done for now."}])]
        path = write(os.path.join(self.home, "todo.jsonl"), listed)
        self.assertEqual(stop_words.todos_open({"transcript_path": path}), ["the programme of the strongest shift"])
        out = self.stop(facts(), path=path)
        self.assertIn("your task list still has: the programme of the strongest shift. Finish each, or mark it "
                      "completed with why it cannot be done.", out["reason"])
        self.assertEqual((self.decided()["decision"], self.decided()["returns"]), ("back", 1))
        done = write(os.path.join(self.home, "done.jsonl"), listed + [
            todo_write("a3", [("which targets shift states", "completed"),
                              ("the programme of the strongest shift", "completed")])])
        self.assertEqual(stop_words.todos_open({"transcript_path": done}), [])
        self.assertIsNone(self.stop(facts(), path=done))

    def test_with_no_todo_list_nothing_is_required(self):
        self.assertIsNone(stop_words.todos_open({"transcript_path": self.path}))
        self.assertIsNone(self.stop(facts()))

    def test_the_task_tools_make_the_list_and_each_tasks_last_update_is_its_state(self):
        """Dev run 4 on GSE342764: its Claude Code had TaskCreate and TaskUpdate and no TodoWrite, so the Stop found no
        list."""
        listed = [entry("u1", "user", "Analyse it: the regulators."),
                  task_call("a1", "TaskCreate", {"subject": "the targets that shift states", "description": "a list"},
                            "c1"),
                  task_result("r1", "c1", "Task #1 created successfully: the targets that shift states"),
                  task_call("a2", "TaskCreate", {"subject": "the programme of the strongest shift",
                                                 "description": "a test"}, "c2"),
                  task_result("r2", "c2", "created", task={"id": "7"}),  # the id in the result's record
                  task_call("a3", "TaskCreate", {"subject": "the threshold", "description": "a number"}, "c3"),
                  task_result("r3", "c3", "created"),  # no id named: the third made is task 3
                  task_call("a4", "TaskCreate", {"subject": "never made", "description": "x"}, "c4"),
                  task_result("r4", "c4", "invalid input", error=True),
                  task_call("a5", "TaskUpdate", {"taskId": "1", "status": "completed"}, "c5"),
                  task_call("a6", "TaskUpdate", {"taskId": "3", "status": "deleted"}, "c6"),
                  task_call("w1", "TaskUpdate", {"taskId": "7", "status": "completed"}, "c7", sidechain=True),
                  entry("a7", "assistant", [{"type": "text", "text": "Done for now."}])]
        path = write(os.path.join(self.home, "tasks.jsonl"), listed)
        self.assertEqual(stop_words.todos_open({"transcript_path": path}), ["the programme of the strongest shift"])
        self.assertIn("your task list still has: the programme of the strongest shift.",
                      self.stop(facts(), path=path)["reason"])
        done = write(os.path.join(self.home, "tasks_done.jsonl"), listed + [
            task_call("a8", "TaskUpdate", {"taskId": "7", "status": "completed"}, "c8")])
        self.assertEqual(stop_words.todos_open({"transcript_path": done}), [])
        self.assertIsNone(self.stop(facts(), path=done))

    def test_a_requests_copy_of_the_calls_makes_no_task(self):
        """Dev run 5 on GSE342764: Claude Code 2.1.329 writes each request's messages again as an api-request-blob
        entry, its calls with the same ids; read as calls, its four tasks were made twice and four stayed open."""
        made = task_call("a1", "TaskCreate", {"subject": "the targets", "description": "a list"}, "c1")
        result = task_result("r1", "c1", "Task #1 created successfully: the targets")
        blob = {"type": "api-request-blob", "sessionId": "s1", "message": {"role": "user", "content": [
            made["message"]["content"][0], result["message"]["content"][0]]}}
        path = write(os.path.join(self.home, "blob.jsonl"), [
            entry("u1", "user", "Analyse it."), made, result, blob, {**made, "uuid": "a1-again"},
            task_call("a2", "TaskUpdate", {"taskId": "1", "status": "completed"}, "c2")])
        self.assertEqual(stop_words.todos_open({"transcript_path": path}), [])

    def decided(self):
        return sf.read_json(sf.session_path("s1", self.home, ".stop.json"))


def task_call(uuid, name, inp, call_id, sidechain=False):
    return {"uuid": uuid, "type": "assistant", **({"isSidechain": True} if sidechain else {}), "message": {
        "role": "assistant", "content": [{"type": "tool_use", "id": call_id, "name": name, "input": inp}]}}


def task_result(uuid, call_id, text, task=None, error=False):
    return {"uuid": uuid, "type": "user", **({"toolUseResult": {"task": task}} if task else {}), "message": {
        "role": "user", "content": [{"type": "tool_result", "tool_use_id": call_id, "content": text,
                                     **({"is_error": True} if error else {})}]}}


class TestTheTwoAsksOnceASession(TestTheStopReadsTheRecord):
    """Approved 2026-11-21 (about 13:50), after dev run 4 on GSE342764: the agent made no task list, and in four dev
    runs it never declared a choice."""

    def decided(self):
        return sf.read_json(sf.session_path("s1", self.home, ".stop.json"))

    def test_findings_with_no_task_list_and_no_choice_said_ask_each_once_in_one_counted_return(self):
        found = facts(findings=2, choice_said=False, inbox=0)
        out = self.stop(found)
        self.assertEqual(out["reason"], stop_words.NO_TASK_LIST + "\n\n" + stop_words.NO_CHOICE)
        self.assertIn("No choice: <why none could>", out["reason"])
        d = self.decided()
        self.assertEqual((d["decision"], d["returns"], d["asked"], d["open"]["asked"]),
                         ("back", 1, ["task_list", "choice"], ["task_list", "choice"]))
        self.assertIsNone(self.stop(found))  # asked once: never again, whatever the agent answered
        later = write(os.path.join(self.home, "t2.jsonl"), [entry("u9", "user", "Go on with the programmes.")])
        self.assertIsNone(self.stop(found, path=later))  # nor in a later turn

    def test_none_is_asked_before_a_finding_and_the_choice_waits_until_the_inbox_is_taken(self):
        self.assertIsNone(self.stop(facts(findings=0, choice_said=False)))
        out = self.stop(facts(findings=1, choice_said=False, inbox=1))  # the line may be in the inbox
        self.assertIn("your last claims are not in the record yet", out["reason"])
        self.assertTrue(out["reason"].startswith(stop_words.NO_TASK_LIST))
        self.assertEqual(self.stop(facts(findings=1, choice_said=False, inbox=0))["reason"], stop_words.NO_CHOICE)

    def test_an_open_item_comes_with_the_ask_in_the_same_return_the_ask_first(self):
        """Dev run 5 on GSE342764: the asks came under a long list of open items; the agent made its tasks and wrote
        no choice line."""
        out = self.stop(facts(findings=1, choice_said=True, doubts_open=["d1"]))
        self.assertTrue(out["reason"].startswith(stop_words.NO_TASK_LIST + "\n\nThe record still holds open items:"))
        self.assertEqual(self.decided()["returns"], 1)

    def test_a_task_list_and_a_choice_said_ask_nothing(self):
        path = write(os.path.join(self.home, "t4.jsonl"), [
            entry("u1", "user", "Analyse it."),
            task_call("a1", "TaskCreate", {"subject": "the targets", "description": "a list"}, "c1"),
            task_result("r1", "c1", "Task #1 created successfully: the targets"),
            task_call("a2", "TaskUpdate", {"taskId": "1", "status": "completed"}, "c2")])
        self.assertIsNone(self.stop(facts(findings=3, choice_said=True), path=path))


class TestTheNoChoiceLine(Service):
    """"No choice: <reason>" is read by code like the Choice line, the main agent's alone, and kept as an event."""

    def apply(self, tag, data):
        inbox(self.svc, tag, {"range": {"session": "s", "agent": "main", "first": tag + "a", "last": tag + "b"},
                              **data})
        return self.ok("pertura_status")["recorded"][0]

    def test_the_line_is_recorded_as_an_event_and_says_a_choice_was_said(self):
        record_dir = os.path.join(self.svc.project, "record")
        forms = recorder.fixed_forms("Done.\n**No choice:** each threshold is the field's default.", {})
        self.assertEqual(forms["no_choice_lines"], [{"reason": "each threshold is the field's default."}])
        self.assertEqual(forms["choice_lines"], [])
        facts_before = recording.stop_facts(record_dir)
        self.assertEqual((facts_before["choice_said"], facts_before["findings"]),
                         (False, len(self.svc.record.state()["findings"])))
        self.apply("t1", {"no_choice_lines": forms["no_choice_lines"]})
        self.assertEqual([n["reason"] for n in self.svc.record.state()["no_choice"]],
                         ["each threshold is the field's default."])
        self.assertTrue(recording.stop_facts(record_dir)["choice_said"])

    def test_a_workers_line_is_not_recorded_and_a_line_with_no_reason_is_refused(self):
        self.apply("t1", {"range": {"session": "s", "agent": "w1", "first": "a", "last": "b"},
                          "no_choice_lines": [{"reason": "none"}]})
        self.assertFalse(self.svc.record.state().get("no_choice"))
        out = self.apply("t2", {"no_choice_lines": [{"reason": " "}]})
        self.assertIn("a No choice line gives its reason", json.dumps(out["refused"]))

    def test_a_choice_line_is_not_a_no_choice_line(self):
        forms = recorder.fixed_forms("Choice: min cells = 30; other: 10", {})
        self.assertEqual((len(forms["choice_lines"]), forms["no_choice_lines"]), (1, []))


class TestAnAnswerThatNarrowsAFindingAsksForWhatRestsOnIt(Service):
    def test_the_reply_and_the_stop_say_to_update_what_was_written_until_it_is_written_again(self):
        from pertura import record as rc
        record_dir = os.path.join(self.svc.project, "record")
        self.ok("raise_doubt", doubt_id="D1", text="one guide alone", would_change="each guide alone",
                finding_id="f1")
        inbox(self.svc, "t1", {"range": {"session": "s", "agent": "main", "first": "a", "last": "b"},
                               "doubt_answers": [{"doubt_id": "D1", "outcome": "weakens", "reason": "one guide [r1]"}]})
        out = self.ok("pertura_status")["recorded"][0]
        self.assertIn("update what you wrote that rests on f1: the answer or report", json.dumps(out["notes"]))
        self.assertEqual(recording.stop_facts(record_dir)["answers_to_update"],
                         [{"doubt_id": "D1", "finding_id": "f1", "outcome": "weakens"}])
        line = stop_words.open_items({"answers_to_update": recording.stop_facts(record_dir)["answers_to_update"]},
                                     False)
        self.assertIn("doubt D1 answered weakens: update what you wrote that rests on f1: the answer or report.", line)
        self.svc.record.append("object_registered", {"object_id": "file:answers.json", "kind": "file",
                                                     "run_id": "r1", "path": "answers.json"}, rc.TOOL)
        self.assertEqual(recording.stop_facts(record_dir)["answers_to_update"], [])  # written again since

    def test_the_servers_instructions_end_the_session_on_the_todo_list(self):
        from pertura import analysis_service as asv
        self.assertIn("the session ends when none is open and your task list is done, or at its cap", asv.INSTRUCTIONS)
        self.assertNotIn("parts of the question", asv.INSTRUCTIONS)


if __name__ == "__main__":
    unittest.main()
