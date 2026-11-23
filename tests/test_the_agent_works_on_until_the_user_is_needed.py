"""What development session 6 showed (docs/build/development_sessions/session_06_crisprai_sonnet, problem 5), fixed
for any session (docs/build_goal_session6_fixes.md, item 5, first part): of 48 turn ends, 24 came while a job ran
and 9 with "Waiting on · nothing"; 12 of the user's 25 messages only kept the agent going. Now:
- wait_for_runs waits up to an hour for runs to end; Claude Code moves it to the background and returns it to the
  agent when a run ends, and the replies that start a run say to arm it and work on;
- the wait ends with the session, leaving nothing behind;
- a Stop hook sends the agent back once when its reply says nothing waits on the user, or when a run runs with no
  wait armed; never twice in a row, never when the user is needed, asked a question or said to stop."""
import json
import os
import sys
import tempfile
import threading
import time
import unittest

from test_design_service import make_deposit

from pertura.analysis_service import MEANWHILE, AnalysisService

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "plugin", "status"))
import hooks  # noqa: E402
import status_file as sf  # noqa: E402
import stop_words  # noqa: E402

NOW = 1_800_000_000.0


def opened(root):
    deposit = os.path.join(root, "deposit")
    os.makedirs(deposit)
    make_deposit(deposit)
    svc = AnalysisService({"workspace": os.path.join(root, "ws")})
    ok, out = svc.call("open_project", {"data_folder": deposit})
    assert ok, out
    return svc


def sleeper(svc, seconds, what):
    return svc.record.start_run(what, [sys.executable, "-c", f"import time; time.sleep({seconds})"], [],
                                svc._jobs_root())


LINUX = sys.platform.startswith("linux")  # the runner's jobs: processes, process groups and /proc


class TestWaitForRuns(unittest.TestCase):
    @unittest.skipUnless(LINUX, "the runner's jobs need Linux")
    def test_it_returns_when_a_run_ends_and_names_the_rest(self):
        with tempfile.TemporaryDirectory() as d:
            svc = opened(d)
            try:
                short, long_ = sleeper(svc, 1, "a short run"), sleeper(svc, 120, "a long run")
                t0 = time.monotonic()
                ok, out = svc.call("wait_for_runs", {"minutes": 2})
                self.assertTrue(ok, out)
                self.assertLess(time.monotonic() - t0, 60)
                self.assertEqual([e["run_id"] for e in out["ended"]], [short])
                self.assertEqual([r["run_id"] for r in out["still_running"]], [long_])
                self.assertNotIn("next", out)  # the runs still running are listed; no step is prescribed
                ok, again = svc.call("pertura_status", {})
                self.assertNotIn(short, [e["run_id"] for e in again.get("ended_since_your_last_call", [])])
            finally:
                svc.call("stop_run", {"run_id": long_})
                svc.close()

    @unittest.skipUnless(LINUX, "the runner's jobs need Linux")
    def test_the_end_of_the_session_ends_the_wait(self):
        with tempfile.TemporaryDirectory() as d:
            svc = opened(d)
            try:
                rid = sleeper(svc, 120, "a long run")
                got = {}
                th = threading.Thread(target=lambda: got.update(out=svc.call("wait_for_runs", {"minutes": 30})))
                th.start()
                time.sleep(1)
                svc.stop_waiting()
                th.join(timeout=30)
                self.assertFalse(th.is_alive())
                self.assertEqual([r["run_id"] for r in got["out"][1]["still_running"]], [rid])
            finally:
                svc.call("stop_run", {"run_id": rid})
                svc.close()

    @unittest.skipUnless(LINUX, "the runner's jobs need Linux")
    def test_one_wait_at_a_time_watching_the_runs_started_after_it(self):
        """The nine D runs on 547c5c7: in 44 of 79 waits Claude Code moved to the background, the agent's next call
        was another wait, so two or three waited at once. Then a second wait was refused while the first was armed,
        and on GSE203592 (99a1f49) a first wait whose caller had stopped listening refused the agent's next waits while
        the Stop hook asked for one. A second wait now takes over: the first replies within seconds, saying so, and
        the second watches the runs started while it waits."""
        with tempfile.TemporaryDirectory() as d:
            svc = opened(d)
            try:
                first, later = sleeper(svc, 120, "a long run"), None
                got, again = {}, {}
                th = threading.Thread(target=lambda: got.update(out=svc.call("wait_for_runs", {"minutes": 2})))
                th.start()
                time.sleep(2)
                th2 = threading.Thread(target=lambda: again.update(out=svc.call("wait_for_runs", {"minutes": 2})))
                th2.start()
                th.join(timeout=15)
                self.assertFalse(th.is_alive())  # the first handed over
                self.assertIn("newer wait", got["out"][1]["taken_over"])
                later = sleeper(svc, 120, "a run started while it waits")
                time.sleep(35)  # past the second wait's next look at the runs started since
                svc.stop_waiting()
                th2.join(timeout=30)
                self.assertFalse(th2.is_alive())
                self.assertEqual({r["run_id"] for r in again["out"][1]["still_running"]}, {first, later})
                self.assertNotIn("taken_over", again["out"][1])
                # a worker's wait on every run (GSE203592, the t3 probe) watches without taking over the main agent's
                svc._stop_waits.clear()
                main, worker = {}, {}
                th = threading.Thread(target=lambda: main.update(out=svc.call("wait_for_runs", {"minutes": 1})))
                th.start()
                time.sleep(2)
                th2 = threading.Thread(target=lambda: worker.update(out=svc.call(
                    "wait_for_runs", {"minutes": 1, "pertura_agent": "pertura:worker"})))
                th2.start()
                time.sleep(12)
                self.assertTrue(th.is_alive())  # not handed over
                svc.stop_waiting()
                th.join(timeout=30)
                th2.join(timeout=30)
                self.assertNotIn("taken_over", main["out"][1])
            finally:
                for r in (first, later):
                    if r:
                        svc.call("stop_run", {"run_id": r})
                svc.close()

    def test_it_waits_in_the_servers_pool_of_waits_and_a_started_run_says_to_arm_it(self):
        self.assertIn("wait_for_runs", AnalysisService.WAIT_TOOLS)
        self.assertIn("wait_for_runs returns when a run ends", MEANWHILE)


def transcript(folder, user, reply):
    path = os.path.join(folder, "transcript.jsonl")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"type": "user", "message": {"role": "user", "content": user}}) + "\n")
        fh.write(json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "name": "x", "input": {}}]}}) + "\n")
        fh.write(json.dumps({"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "content": "ok"}]}}) + "\n")
        fh.write(json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "text", "text": reply}]}}) + "\n")
    return path


def block(waiting):
    return ("- **Stage** · The first look\n- **Found** · nothing yet\n- **Next** · Pertura reads the guide calls\n"
            f"- **Waiting on** · {waiting}\n\nThe body.")


class TestTheStopHook(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp()
        sf.record_call({"session_id": "s1", "tool_name": sf.TOOL_PREFIX + "open_project",
                        "tool_response": {"project": "/w/p-1a2b3c4d"}}, self.home, now=NOW)

    def stop(self, user, reply, now=NOW, **more):
        path = transcript(self.home, user, reply)
        return hooks.stop({"session_id": "s1", "transcript_path": path, **more}, self.home, now=now)

    def run_a_job(self):
        sf.record_call({"session_id": "s1", "tool_name": sf.TOOL_PREFIX + "run_module",
                        "tool_input": {"module": "C.genes"}, "tool_response": {"run_id": "r1", "state": "running"}},
                       self.home, now=NOW)

    def test_nothing_waiting_on_the_user_sends_it_back_up_to_three_times_in_a_turn(self):
        out = self.stop("继续吧", block("nothing: going on with the guide calls"))
        self.assertEqual(out, {"decision": "block", "reason": stop_words.CARRY_ON})
        self.assertEqual(self.stop("继续吧", block("nothing"), stop_hook_active=True)["reason"], stop_words.CARRY_ON)
        self.assertEqual(self.stop("继续吧", block("nothing"), stop_hook_active=True)["reason"], stop_words.CARRY_ON)
        self.assertIsNone(self.stop("继续吧", block("nothing"), stop_hook_active=True))

    def test_in_a_chinese_chat_too(self):
        reply = "- **阶段** · 初看\n- **已发现** · 还没有\n- **下一步** · Pertura 读 guide 调用\n- **在等** · 没有：接着读"
        self.assertEqual(self.stop("开始吧", reply)["reason"], stop_words.CARRY_ON)

    def test_the_users_own_decision_a_question_or_a_stop_ends_the_turn(self):
        self.assertIsNone(self.stop("开始吧", block("you: the design, on its form")))
        self.assertIsNone(self.stop("现在在run什么？", block("nothing")))
        self.assertIsNone(self.stop("先停一下", block("nothing")))

    def test_a_run_with_no_wait_armed_asks_for_the_wait_and_an_armed_one_does_not(self):
        self.run_a_job()
        self.assertEqual(self.stop("开始吧", block("a job: C.genes, running for 2 min"))["reason"], stop_words.ARM)
        hooks.before_wait({"session_id": "s1", "tool_name": sf.TOOL_PREFIX + "wait_for_runs",
                           "tool_input": {"minutes": 30}}, self.home)
        self.assertIsNone(self.stop("开始吧", block("a job: C.genes, running for 2 min"), now=time.time()))
        st = sf.session_status("s1", self.home)
        self.assertTrue(sf.wait_armed(st))
        sf.apply_call(st, "wait_for_runs", {}, {"ended": [], "still_running": [{"run_id": "r1"}],
                                                "taken_over": "a newer wait watches the runs now"}, now=NOW + 30)
        self.assertTrue(sf.wait_armed(st))  # the wait it handed over to is still armed
        sf.apply_call(st, "wait_for_runs", {}, {"ended": [{"run_id": "r1", "state": "done"}], "still_running": []},
                      now=NOW + 60)
        self.assertFalse(sf.wait_armed(st))
        self.assertEqual(st["jobs"]["r1"]["state"], "done")

    def test_a_subagents_wait_neither_arms_nor_clears_the_main_agents(self):
        """A worker's wait on GSE203592 (6a87acd) would have armed a wait the main agent did not hold, or cleared the
        main agent's while it still ran."""
        self.run_a_job()
        wait = {"session_id": "s1", "tool_name": sf.TOOL_PREFIX + "wait_for_runs", "tool_input": {"minutes": 30}}
        hooks.before_wait({**wait, "agent_id": "a1"}, self.home)
        self.assertFalse(sf.wait_armed(sf.session_status("s1", self.home)))
        hooks.before_wait(wait, self.home)
        sf.record_call({**wait, "agent_id": "a1", "tool_response": {"ended": [], "still_running": [{"run_id": "r1"}]}},
                       self.home)
        self.assertTrue(sf.wait_armed(sf.session_status("s1", self.home)))

    def test_not_a_pertura_session(self):
        path = transcript(self.home, "hi", block("nothing"))
        self.assertIsNone(hooks.stop({"session_id": "another", "transcript_path": path}, self.home))

    def test_the_hooks_file_names_the_stop_and_the_wait(self):
        with open(os.path.join(ROOT, "plugin", "hooks", "hooks.json"), encoding="utf-8") as fh:
            h = json.load(fh)["hooks"]
        self.assertIn("hooks.py\" stop", h["Stop"][0]["hooks"][0]["command"])
        self.assertEqual(h["PreToolUse"][0]["matcher"], sf.TOOL_PREFIX + "wait_for_runs")


if __name__ == "__main__":
    unittest.main()
