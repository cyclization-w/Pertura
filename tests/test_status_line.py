"""Pertura's status line and subagent rows (plugin/status): what they say at each stage of a synthetic status file,
that they read only that file and stay fast, and that their output survives a terminal that is not set to UTF-8."""
import ast
import json
import os
import statistics
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATUS = os.path.join(ROOT, "plugin", "status")
sys.path.insert(0, STATUS)
import status_file as sf  # noqa: E402
import status_line  # noqa: E402
import status_words as words  # noqa: E402
import subagent_rows  # noqa: E402

NOW = 1_800_000_000.0
PROJECT = "/raid/pertura/ws/K562_screen-1a2b3c4d"


def write_status(home, st, session="s1"):
    """The files the hooks leave: the project's status and line file, and the session's pointer to them."""
    key = sf.project_key(st["project"])
    sf.save(key, st, home)
    sf.write_json(sf.session_path(session, home), {"project": key, "updated": NOW})
    sf.write_text(sf.session_path(session, home, ".key"), key)


def at_stage(stage):
    """A status file as the hooks would leave it at each stage of an analysis."""
    st = sf.empty(PROJECT)
    st["name"] = sf.display_name(PROJECT)
    if stage == "design":
        st["design"] = {"confirmed": False, "open": ["perturbation.controls", "units.independent_unit"]}
        return st
    st["design"] = {"confirmed": True, "open": []}
    if stage == "building":
        st["screen_object"] = {"state": "building", "job_id": "so1"}
        st["jobs"]["so1"] = {"run_id": "so1", "what": "building the screen object", "kind": "screen_object",
                             "started": NOW - 150, "state": "running", "ended": None, "checked": NOW - 10}
        return st
    st["screen_object"] = {"state": "built", "job_id": "so1"}
    for i, qid in enumerate(("A.what_was_done", "B.efficacy", "B.abundance", "C.any_effect", "C.genes",
                             "C.programmes", "F.robustness", "G.meaning")):
        st["questions"][qid] = {"letter": qid[0], "item": qid[2:], "because": "always",
                                "status": "detected" if i < 3 else None}
        st["order"].append(qid)
    for i, fid in enumerate(("f1", "f2", "f3")):  # three findings, the first answered by the user
        st["findings"][fid] = {"statement": f"finding {fid}", "replies": [{"kind": "keep"}] if i == 0 else []}
    st["open_items"], st["due"] = 4, {"main agent": ["skeptic"], "user": ["results to decide", "the user's next step"]}
    st["jobs"]["r1"] = {"run_id": "r1", "what": "C.genes", "kind": "module", "question_id": "C.genes",
                        "started": NOW - 4320, "state": "running", "ended": None, "checked": NOW - 30}
    st["jobs"]["r0"] = {"run_id": "r0", "what": "B.abundance", "kind": "module", "question_id": "B.abundance",
                        "started": NOW - 900, "state": "done", "ended": NOW - 400, "checked": NOW - 400}
    return st


class TestWhatTheLineSays(unittest.TestCase):
    def render(self, st, now=NOW):
        with tempfile.TemporaryDirectory() as home:
            write_status(home, st)
            return words.plain(status_line.render(json.dumps({"session_id": "s1"}), home, now=now))

    def test_a_session_without_pertura_shows_the_model_and_folder(self):
        with tempfile.TemporaryDirectory() as home:
            text = status_line.render(json.dumps({"session_id": "other", "model": {"display_name": "Opus"},
                                                  "workspace": {"current_dir": "C:\\work\\thesis"}}), home)
        self.assertEqual(text, "Opus · thesis")

    def test_while_the_design_is_open_it_says_how_much_is_left(self):
        text = self.render(at_stage("design"))
        self.assertEqual(text, "Pertura · K562_screen · ◐ design: 2 to confirm ─ ○ screen object ─ ○ no finding yet")

    def test_while_the_screen_object_builds_it_shows_the_job(self):
        lines = self.render(at_stage("building")).split("\n")
        self.assertIn("● design confirmed ─ ◐ building the screen object ─ ○ no finding yet", lines[0])
        self.assertTrue(lines[1].endswith("running  building the screen object 2 min"), lines[1])

    def test_while_findings_come_it_counts_them_and_those_awaiting_a_reply(self):
        # docs/adr/0032 (D6, D7): the findings and the user's replies, not the questions closed
        lines = self.render(at_stage("questions")).split("\n")
        self.assertEqual(lines[0], "Pertura · K562_screen · ● design confirmed ─ ● screen object built ─ "
                                   "◐ 3 findings, 2 awaiting your reply · 4 open items, 1 for the agent, 2 for you")
        self.assertIn("running  C.genes 1 h 12 min", lines[1])
        self.assertNotIn("checked", lines[1])  # checked a moment ago: nothing to say

    def test_a_job_not_checked_for_a_while_is_not_called_running(self):
        """Session 6 (problem 6): jobs that had ended showed as running for an hour."""
        st = at_stage("questions")
        st["jobs"]["r1"]["checked"] = NOW - 600
        line = self.render(st).splitlines()[1]
        self.assertIn(f"C.genes not checked since {time.strftime('%H:%M', time.localtime(NOW - 600))}", line)
        self.assertNotIn("running", line)
        st["jobs_checked"] = {"at": NOW - 90, "ok": False}
        self.assertIn("(server not reached, last tried 1 min ago)", self.render(st))

    def test_a_job_that_ended_is_mentioned_for_a_few_minutes(self):
        st = at_stage("questions")
        del st["jobs"]["r1"]
        self.assertIn("✓ B.abundance finished 6 min ago", self.render(st))
        st["jobs"]["r0"]["state"] = "failed"
        self.assertIn("✗ B.abundance failed 6 min ago", self.render(st))
        self.assertEqual(len(self.render(st, now=NOW + 3600).split("\n")), 1)

    def test_every_finding_answered_and_the_report_written(self):
        st = at_stage("questions")
        for f in st["findings"].values():
            f["replies"] = f["replies"] or [{"kind": "drop"}]
        st["jobs"] = {}
        st["report"] = {"path": "report.md", "at": NOW}
        self.assertIn("◐ 3 findings ─ ● report written", self.render(st))

    def test_the_questions_counts_no_longer_reach_the_line(self):
        st = at_stage("questions")
        st["questions"], st["order"] = {}, []
        st["question_counts"] = {"open": 5, "detected": 2, "not_detected": 1}
        self.assertNotIn("questions", self.render(st))

    def test_the_spinner_turns_each_second(self):
        st = at_stage("questions")
        frames = {self.render(st, now=NOW + s).split("\n")[1][0] for s in range(10)}
        self.assertEqual(len(frames), 10)

    def test_spans_in_plain_words(self):
        self.assertEqual([words.ago(s) for s in (5, 75, 3600, 4320, 200000)],
                         ["5 s", "1 min", "1 h", "1 h 12 min", "2 d 7 h"])


class TestSubagentRows(unittest.TestCase):
    def test_pertura_agents_get_a_plain_row_and_others_keep_theirs(self):
        payload = {"columns": 70, "tasks": [
            {"id": "t1", "agentType": "pertura:skeptic", "status": "running", "startTime": (NOW - 240) * 1000,
             "description": "the three findings that lead result 1"},
            {"id": "t2", "agentType": "pertura:worker", "status": "completed",
             "startTime": (NOW - 60) * 1000, "description": "C.genes volcano"},
            {"id": "t3", "agentType": "Explore", "status": "running", "startTime": NOW * 1000}]}
        rows = [json.loads(line) for line in subagent_rows.render(payload, now=NOW)]
        self.assertEqual([r["id"] for r in rows], ["t1", "t2"])
        plain = [words.plain(r["content"]) for r in rows]
        self.assertTrue(plain[0].endswith("skeptic · the three findings that lead result 1 · 4 min"), plain[0])
        self.assertEqual(plain[1], "✓ worker · C.genes volcano · done")

    def test_a_long_description_is_cut_to_the_row(self):
        payload = {"columns": 40, "tasks": [{"id": "t1", "agentType": "pertura:worker", "status": "running",
                                             "startTime": NOW * 1000, "description": "x" * 200}]}
        content = json.loads(subagent_rows.render(payload, now=NOW)[0])["content"]
        self.assertLessEqual(subagent_rows.visible_len(content), 40)
        self.assertIn("…", content)


class TestFastAndSafe(unittest.TestCase):
    def test_the_status_line_imports_no_json(self):
        """json would double the status line's start-up; the hooks compose what it needs."""
        allowed = {"status_line.py": {"os", "sys", "time", "status_words"}, "status_words.py": set(),
                   "subagent_rows.py": {"json", "sys", "time"}}
        for name, ok in allowed.items():
            with open(os.path.join(STATUS, name), encoding="utf-8") as fh:
                tree = ast.parse(fh.read())
            imported = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
            imported |= {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
            self.assertLessEqual(imported, ok, name)

    def test_rendering_takes_a_few_milliseconds(self):
        with tempfile.TemporaryDirectory() as home:
            write_status(home, at_stage("questions"))
            t0 = time.perf_counter()
            stdin = json.dumps({"session_id": "s1", "model": {"display_name": "Opus"}})
            for _ in range(100):
                status_line.render(stdin, home)
            self.assertLess((time.perf_counter() - t0) / 100, 0.005)

    def test_the_script_as_claude_code_runs_it(self):
        """Run as the status line runs: a fresh interpreter each refresh, the session JSON on stdin, a console
        that may not be UTF-8. The time is the interpreter's start plus the read; the bound only catches a slow
        import, since start-up time depends on the machine."""
        with tempfile.TemporaryDirectory() as home:
            write_status(home, at_stage("questions"))
            env = dict(os.environ, **{sf.HOME_VARIABLE: home, "PYTHONIOENCODING": "cp1252"})
            times = []
            for _ in range(5):
                t0 = time.perf_counter()
                r = subprocess.run([sys.executable, "-S", os.path.join(STATUS, "status_line.py")],
                                   input=json.dumps({"session_id": "s1"}).encode(), capture_output=True, env=env)
                times.append(time.perf_counter() - t0)
                self.assertEqual(r.returncode, 0, r.stderr)
            text = r.stdout.decode("utf-8")
            self.assertIn("3 findings, 2 awaiting your reply", text)
            self.assertIn("◐", text)
            self.assertLess(statistics.median(times), 0.5)


if __name__ == "__main__":
    unittest.main()
