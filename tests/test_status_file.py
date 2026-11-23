"""The local status file the status line reads (plugin/status): a session on the synthetic deposit through the
analysis server, each call passed to the hook as Claude Code passes it, gives a status file that agrees with the
record; the job check reads the server's job folders with plain shell lines; the hooks copy the status line, say
once how to install it, and never fail a tool call."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

from test_design_service import answer, confirm_the_cells, make_deposit, proposal

from pertura import design_gate, screen_object
from pertura.analysis_service import TOOL_NAMES, AnalysisService

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATUS = os.path.join(ROOT, "plugin", "status")
sys.path.insert(0, STATUS)
import hooks  # noqa: E402
import job_check  # noqa: E402
import status_file as sf  # noqa: E402
import status_line  # noqa: E402
import status_words as words  # noqa: E402

# the shapes a tool's reply may take in a hook's input: the MCP result, its content blocks, or the text alone
SHAPES = (lambda out: {"content": [{"type": "text", "text": json.dumps(out, default=str)}],
                       "structuredContent": json.loads(json.dumps(out, default=str)), "isError": False},
          lambda out: [{"type": "text", "text": json.dumps(out, default=str)}],
          lambda out: json.dumps(out, default=str))


class TestToolNames(unittest.TestCase):
    def test_every_tool_the_status_file_reads_is_one_the_server_lists(self):
        self.assertTrue(set(sf.READS) <= set(TOOL_NAMES), sorted(set(sf.READS) - set(TOOL_NAMES)))

    def test_a_tool_of_another_server_is_not_read(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(sf.record_call({"session_id": "s", "tool_name": "mcp__other__pertura_status",
                                              "tool_response": {"project": "/x/p-1a2b3c4d"}}, d))
            self.assertFalse(os.path.exists(os.path.join(d, "sessions")))


class TestSessionThroughTheServer(unittest.TestCase):
    """A session on the synthetic deposit: the status file after each call agrees with the server's record."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.deposit = os.path.join(cls.tmp.name, "deposit")
        os.makedirs(cls.deposit)
        make_deposit(cls.deposit)
        cls.svc = AnalysisService({"workspace": os.path.join(cls.tmp.name, "ws")})
        cls.home = os.path.join(cls.tmp.name, "home")
        cls.n = 0

    @classmethod
    def tearDownClass(cls):
        cls.svc.close()
        cls.tmp.cleanup()

    def call(self, tool, elicit=None, **args):
        t0 = time.time()
        ok, out = self.svc.call(tool, args, elicit=elicit)
        self.assertTrue(ok, out)
        shape = SHAPES[type(self).n % len(SHAPES)]
        type(self).n += 1
        sf.record_call({"session_id": "s1", "cwd": ".", "tool_name": sf.TOOL_PREFIX + tool, "tool_input": args,
                        "tool_response": shape(out), "duration_ms": int((time.time() - t0) * 1000)}, self.home)
        return out

    def status(self):
        return sf.session_status("s1", self.home)

    def line(self):
        return words.plain(status_line.render(json.dumps({"session_id": "s1"}), self.home))

    def test_the_status_follows_the_record(self):
        self.assertIsNone(sf.record_call({"session_id": "s1", "tool_name": sf.TOOL_PREFIX + "pertura_status",
                                          "tool_response": {"project": None}}, self.home))
        self.call("open_project", data_folder=self.deposit)
        st = self.status()
        self.assertEqual(st["name"], "deposit")
        self.assertIsNone(st["design"]["confirmed"])
        self.call("measure")
        self.call("check_design", proposal=proposal())
        self.call("confirm_design", elicit=answer())
        self.assertTrue(self.status()["design"]["confirmed"])
        design = design_gate.load(self.svc.design.design_file)[0]
        screen_object.build(self.deposit, design, self.svc._screen_dir())  # build_screen_object's job, run here
        confirm_the_cells(self.svc)
        self.call("pertura_status")
        self.assertEqual(self.status()["screen_object"]["state"], "built")
        plan = self.call("plan_questions")["questions"]
        self.call("add_question", letter="C", item="cell cycle", because="the user asked")
        st = self.status()
        self.assertEqual(st["order"], [q["question_id"] for q in plan] + ["C.cell_cycle"])
        self.assertEqual(words.question_counts(st), (len(plan) + 1, 0))

        run = self.call("run_module", module="B.abundance", wait=0)
        job = self.status()["jobs"][run["run_id"]]
        if run["state"] in sf.ACTIVE:
            self.assertEqual(job["state"], run["state"])
            self.assertEqual(job["question_id"], "B.abundance")
            self.assertIn("B.abundance", self.line())
            done = self.call("module_result", run_id=run["run_id"], wait=240)
        else:
            done = run
        self.assertEqual(done["state"], "done", done)
        job = self.status()["jobs"][run["run_id"]]
        self.assertEqual(job["state"], "done")
        self.assertIsNotNone(job["ended"])
        self.assertIn("finished", self.line())

        rid = run["run_id"]
        self.call("record_finding", finding_id="f1", question_id="B.abundance", target="T1", feature="cells",
                  direction="down", population="all", run_ids=[rid], kind="custom",
                  statement="T1's cells are fewer than its guides' share")
        self.call("raise_doubt", doubt_id="d1", question_id="B.abundance", text="one sample carries it",
                  would_change="the effect vanishes in the other sample")
        self.call("open_branch", branch_id="b1", doubt_id="d1", plan="test by sample")
        self.call("close_branch", branch_id="b1", outcome="holds in both", run_ids=[rid])
        self.call("close_question", question_id="B.abundance", status="detected", evidence=[rid], figure="figure.png")
        self.call("close_question", question_id="C.cell_cycle", status="cannot_answer",
                  reason="no cell-cycle genes are measured")
        items = self.call("open_items")
        self.call("pertura_status")

        st, truth = self.status(), self.svc.record.state()
        self.assertEqual({q: v["status"] for q, v in st["questions"].items()},
                         {q: v["status"] for q, v in truth["questions"].items()})
        self.assertEqual(st["open_items"], len(items["open"]))
        self.assertEqual(sorted(st["findings"]), sorted(truth["findings"]))
        self.assertEqual(st["doubts"]["d1"]["branches"], ["b1"])
        self.assertEqual(st["branches"]["b1"]["outcome"], "holds in both")
        self.assertEqual(words.question_counts(st), (len(truth["questions"]), 2))

        self.call("read_record")  # the record itself replaces what the file held, and agrees with it
        st = self.status()
        self.assertEqual({q: v["status"] for q, v in st["questions"].items()},
                         {q: v["status"] for q, v in truth["questions"].items()})
        self.assertEqual(st["branches"]["b1"]["doubt_id"], "d1")
        line = words.plain(words.first_line(st))
        # docs/adr/0032: the line counts the findings and those awaiting the user's reply, not closed questions
        for phrase in ("Pertura · deposit", "design confirmed", "screen object built",
                      "1 finding, 1 awaiting your reply"):
            self.assertIn(phrase, line)
        self.call("note_reply", finding_id="f1", kind="keep", words="Keep it.")
        self.assertIn("1 finding", words.plain(words.first_line(self.status())))
        self.assertNotIn("awaiting", words.plain(words.first_line(self.status())))


class TestFolding(unittest.TestCase):
    def test_a_read_of_some_ids_keeps_the_rest(self):
        st = sf.empty("/w/p-1a2b3c4d")
        sf.apply_call(st, "record_finding", {"finding_id": "f1", "question_id": "C.genes", "target": "T1"}, {})
        sf.apply_call(st, "record_finding", {"finding_id": "f2", "question_id": "C.genes", "target": "T2"}, {})
        sf.apply_call(st, "read_record", {"what": "findings", "ids": ["f2"]},
                      {"findings": {"f2": {"question_id": "C.genes", "target": "T2b", "robustness": {}}}})
        self.assertEqual(sorted(st["findings"]), ["f1", "f2"])
        self.assertEqual(st["findings"]["f2"]["target"], "T2b")

    def test_a_job_started_inside_a_call_starts_when_the_call_began(self):
        st = sf.empty("/w/p-1a2b3c4d")
        sf.apply_call(st, "run_module", {"module": "C.genes"}, {"run_id": "r1", "state": "running"}, now=1000.0,
                      seconds=60.0)
        self.assertEqual(st["jobs"]["r1"]["started"], 940.0)
        sf.apply_call(st, "module_result", {"run_id": "r1"}, {"run_id": "r1", "state": "running"}, now=2000.0,
                      seconds=5.0)
        self.assertEqual(st["jobs"]["r1"]["started"], 940.0)  # a later look does not move the start

    def test_the_choices_of_a_finding_fill_in_from_its_robustness_run(self):
        st = sf.empty("/w/p-1a2b3c4d")
        sf.apply_call(st, "record_finding", {"finding_id": "f1", "question_id": "C.genes"}, {})
        sf.apply_call(st, "check_robustness", {"finding_id": "f1"}, {"run_id": "r2", "state": "running"})
        self.assertEqual(st["findings"]["f1"]["choices"]["state"], "running")
        sf.apply_call(st, "module_result", {"run_id": "r2"},
                      {"run_id": "r2", "state": "done", "variants": ["test: wilcoxon", "observations: pseudobulk"],
                       "counts": {"holds": 2, "contradicted": 1}})
        ch = st["findings"]["f1"]["choices"]
        self.assertEqual((ch["state"], ch["analyses"][0], ch["counts"]["holds"]), ("done", "primary", 2))

    def test_finished_jobs_are_pruned_after_a_day_and_running_ones_kept(self):
        st = sf.empty("/w/p-1a2b3c4d")
        sf.apply_call(st, "run_module", {"module": "C.genes"}, {"run_id": "old", "state": "done"}, now=0.0)
        sf.apply_call(st, "run_module", {"module": "C.genes"}, {"run_id": "live", "state": "running"}, now=1.0)
        sf.apply_call(st, "open_items", {}, {"open": []}, now=2 * sf.DAY)
        self.assertEqual(sorted(st["jobs"]), ["live"])

    def test_a_failed_reply_changes_nothing_but_the_last_call(self):
        st = sf.empty("/w/p-1a2b3c4d")
        sf.apply_call(st, "close_question", {}, {"error": "no question X"}, now=5.0)
        self.assertEqual((st["questions"], st["last_call"]["tool"]), ({}, "close_question"))


BASH = shutil.which("bash")  # by its path: Windows would otherwise start WSL's bash from System32 first


class TestJobCheck(unittest.TestCase):
    @unittest.skipUnless(BASH and "system32" not in BASH.lower(), "no POSIX shell here")
    def test_the_shell_lines_read_each_jobs_end(self):
        with tempfile.TemporaryDirectory() as d:
            root = d.replace("\\", "/")
            for rid, files in {"j-done": {"exit_code.json": '{"exit_code": 0}'},
                               "j-failed": {"exit_code.json": '{\n "exit_code": 1\n}'},
                               "j-stopped": {"stopped": ""}, "j-running": {"job.log": "step 3"}}.items():
                os.makedirs(os.path.join(d, rid))
                for name, text in files.items():
                    with open(os.path.join(d, rid, name), "w") as fh:
                        fh.write(text)
            text = job_check.script(root, ["j-done", "j-failed", "j-stopped", "j-running", "j-gone"])
            out = subprocess.run([BASH, "-c", text], capture_output=True, text=True).stdout
            self.assertEqual(job_check.read_reply(out), {"j-done": "done", "j-failed": "failed",
                                                         "j-stopped": "stopped", "j-running": "active"})

    def test_a_round_marks_the_jobs_that_ended_and_counts_the_rest(self):
        with tempfile.TemporaryDirectory() as home:
            project = "/raid/pertura/ws/screen-1a2b3c4d"
            sf.record_call({"session_id": "s1", "tool_name": sf.TOOL_PREFIX + "open_project",
                            "tool_response": {"project": project}}, home, now=100.0)
            for rid in ("j1", "j2"):
                sf.record_call({"session_id": "s1", "tool_name": sf.TOOL_PREFIX + "run_module",
                                "tool_input": {"module": "C.genes"}, "tool_response": {"run_id": rid,
                                                                                       "state": "running"}},
                               home, now=100.0)
            asked = []

            def ssh(host, text):
                asked.append((host, text))
                return "j1 exit {\"exit_code\": 0}\nj2 active\n"

            self.assertEqual(job_check.check_once("server", home, ssh=ssh, now=200.0), 1)
            self.assertIn(f"{project}/design/jobs/j1", asked[0][1])
            st = sf.session_status("s1", home)
            self.assertEqual((st["jobs"]["j1"]["state"], st["jobs"]["j1"]["ended"]), ("done", 200.0))
            self.assertEqual(st["jobs"]["j2"]["state"], "running")
            self.assertTrue(st["jobs_checked"]["ok"])

            def down(host, text):
                raise subprocess.TimeoutExpired("ssh", 45)

            self.assertEqual(job_check.check_once("server", home, ssh=down, now=300.0), 1)
            self.assertFalse(sf.session_status("s1", home)["jobs_checked"]["ok"])
            hooks.session_end({"session_id": "s1"}, home)  # an ended session's jobs are no longer checked
            self.assertEqual(job_check.check_once("server", home, ssh=ssh, now=400.0), 0)

    def test_a_tool_by_the_fields_kinds_is_its_modules_run(self):
        """docs/adr/0044: the eleven tools start module runs, and run_result is module_result renamed."""
        with tempfile.TemporaryDirectory() as home:
            sf.record_call({"session_id": "s1", "tool_name": sf.TOOL_PREFIX + "open_project",
                            "tool_response": {"project": "/raid/pertura/ws/screen-1a2b3c4d"}}, home, now=100.0)
            sf.record_call({"session_id": "s1", "tool_name": sf.TOOL_PREFIX + "states",
                            "tool_input": {"view": "within"},
                            "tool_response": {"run_id": "r1", "state": "running",
                                              "analysis": "states, view within: C.within_states"}}, home, now=100.0)
            st = sf.session_status("s1", home)
            self.assertEqual((st["jobs"]["r1"]["what"], st["jobs"]["r1"]["state"]), ("C.within_states", "running"))
            sf.record_call({"session_id": "s1", "tool_name": sf.TOOL_PREFIX + "run_result",
                            "tool_input": {"run_id": "r1"},
                            "tool_response": {"run_id": "r1", "state": "done", "job_id": "r1"}}, home, now=200.0)
            self.assertEqual(sf.session_status("s1", home)["jobs"]["r1"]["state"], "done")

    def test_names_that_are_not_plain_are_never_put_in_a_command(self):
        self.assertFalse(job_check.start("host; rm -rf /", root=tempfile.gettempdir()))
        with tempfile.TemporaryDirectory() as home:
            sf.record_call({"session_id": "s1", "tool_name": sf.TOOL_PREFIX + "open_project",
                            "tool_response": {"project": "/w/p'$(x)-1a2b3c4d"}}, home)
            sf.record_call({"session_id": "s1", "tool_name": sf.TOOL_PREFIX + "run_module",
                            "tool_input": {"module": "C.genes"},
                            "tool_response": {"run_id": "r1", "state": "running"}}, home)
            asked = []
            job_check.check_once("server", home, ssh=lambda h, t: asked.append(t) or "", now=time.time())
            self.assertEqual(asked, [])


class TestHooks(unittest.TestCase):
    def test_session_start_copies_the_scripts_and_says_once_how_to_install(self):
        with tempfile.TemporaryDirectory() as d:
            home, settings = os.path.join(d, "home"), os.path.join(d, "settings.json")
            out = hooks.session_start({"source": "startup"}, home, settings)
            self.assertIn("statusLine", out["systemMessage"])
            self.assertIn("/output-style", out["systemMessage"])
            for name in hooks.COPIED:
                self.assertTrue(os.path.exists(os.path.join(home, name)))
            self.assertIsNone(hooks.session_start({"source": "startup"}, home, settings))

    def test_no_message_once_the_status_line_is_installed_or_on_resume(self):
        with tempfile.TemporaryDirectory() as d:
            settings = os.path.join(d, "settings.json")
            with open(settings, "w") as fh:
                json.dump({"statusLine": {"type": "command",
                                          "command": 'python -S "$HOME/.claude/pertura/status_line.py"'}}, fh)
            self.assertIsNone(hooks.session_start({"source": "startup"}, os.path.join(d, "a"), settings))
            self.assertIsNone(hooks.session_start({"source": "resume"}, os.path.join(d, "b"),
                                                  os.path.join(d, "none.json")))

    def test_after_a_compaction_a_pertura_session_is_asked_to_read_where_it_stands(self):
        with tempfile.TemporaryDirectory() as d:
            home = os.path.join(d, "home")
            self.assertIsNone(hooks.session_start({"source": "compact", "session_id": "s1"}, home))  # not Pertura's
            key = sf.project_key("/w/screen-1a2b3c4d")
            sf.save(key, {**sf.empty("/w/screen-1a2b3c4d"), "project": "/w/screen-1a2b3c4d"}, home)
            sf.write_json(sf.session_path("s1", home), {"project": key})
            out = hooks.session_start({"source": "compact", "session_id": "s1"}, home)
            self.assertEqual(out["hookSpecificOutput"], {"hookEventName": "SessionStart",
                                                         "additionalContext": hooks.COMPACTED})
            self.assertIn("pertura_status", hooks.COMPACTED)

    def test_the_first_run_message_gives_a_command_that_works(self):
        settings = "{" + hooks.FIRST_RUN.split("\n")[1].strip() + "}"
        command = json.loads(settings)["statusLine"]["command"]
        self.assertEqual(command, 'python -S "$HOME/.claude/pertura/status_line.py"')

    def test_the_job_check_starts_only_while_a_job_runs_and_a_host_is_known(self):
        with tempfile.TemporaryDirectory() as home:
            started = []
            event = {"session_id": "s1", "tool_name": sf.TOOL_PREFIX + "open_project",
                     "tool_response": {"project": "/w/p-1a2b3c4d"}}
            os.environ[hooks.HOST_VARIABLE] = "server"
            try:
                hooks.after_tool_call(event, home, start_check=lambda h, r: started.append(h))
                self.assertEqual(started, [])
                hooks.after_tool_call({**event, "tool_name": sf.TOOL_PREFIX + "run_module",
                                       "tool_input": {"module": "C.genes"},
                                       "tool_response": {"run_id": "r1", "state": "running"}},
                                      home, start_check=lambda h, r: started.append(h))
                self.assertEqual(started, ["server"])
            finally:
                del os.environ[hooks.HOST_VARIABLE]

    def test_the_hook_as_claude_code_runs_it_writes_the_file_and_exits_0_even_on_bad_input(self):
        with tempfile.TemporaryDirectory() as home:
            env = dict(os.environ, **{sf.HOME_VARIABLE: home, sf.OPEN_PAGE_VARIABLE: "false"})  # no browser here
            env.pop(hooks.HOST_VARIABLE, None)
            event = {"session_id": "s1", "tool_name": sf.TOOL_PREFIX + "open_project",
                     "tool_response": [{"type": "text", "text": json.dumps({"project": "/w/screen-1a2b3c4d"})}]}
            script = os.path.join(STATUS, "hooks.py")
            r = subprocess.run([sys.executable, "-S", script, "after-tool-call"], input=json.dumps(event).encode(),
                               capture_output=True, env=env)
            self.assertEqual((r.returncode, r.stdout), (0, b""))
            self.assertEqual(sf.session_status("s1", home)["name"], "screen")
            r = subprocess.run([sys.executable, "-S", script, "after-tool-call"], input=b"not json",
                               capture_output=True, env=env)
            self.assertEqual((r.returncode, r.stdout), (0, b""))
            self.assertTrue(os.path.exists(os.path.join(home, "hook_errors.log")))

    def test_the_hooks_file_matches_every_pertura_tool_and_names_the_entry_points(self):
        with open(os.path.join(ROOT, "plugin", "hooks", "hooks.json"), encoding="utf-8") as fh:
            cfg = json.load(fh)["hooks"]
        post = cfg["PostToolUse"][0]
        self.assertEqual(post["matcher"], sf.TOOL_PREFIX + ".*")
        self.assertIn("after-tool-call", post["hooks"][0]["command"])
        self.assertIn("session-start", cfg["SessionStart"][0]["hooks"][0]["command"])
        self.assertIn("session-end", cfg["SessionEnd"][0]["hooks"][0]["command"])


class TestOneStatusPerProject(unittest.TestCase):
    def test_one_deposits_projects_in_two_workspaces_keep_two_statuses(self):
        a = "/raid/pertura/trials/session_7_workspace/screen2_57genes_HTO-0ab6f534"
        b = "/raid/pertura/trials/session_8_workspace/screen2_57genes_HTO-0ab6f534"
        self.assertNotEqual(sf.project_key(a), sf.project_key(b))
        self.assertEqual(sf.project_key(a), sf.project_key(a + "/"))
        self.assertTrue(sf.project_key(a).startswith("screen2_57genes_HTO-0ab6f534--"))
        with tempfile.TemporaryDirectory() as home:
            for session, project in (("s7", a), ("s8", b)):
                sf.record_call({"session_id": session, "tool_name": sf.TOOL_PREFIX + "open_project",
                                "tool_input": {}, "tool_response": {"project": project}}, home)
            kept = [f for f in os.listdir(os.path.join(home, "projects")) if f.endswith(".json")]
            self.assertEqual(len(kept), 2)
            self.assertEqual({sf.session_status(s, home)["project"] for s in ("s7", "s8")}, {a, b})


if __name__ == "__main__":
    unittest.main()
