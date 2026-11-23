"""The analysis server (pertura.analysis_service) and the plugin's default path (step 3, bar 6): the skill names only
tools the server has, and none of the held-out exam's; a session on the synthetic deposit runs through the record."""
import importlib.util
import json
import os
import re
import tempfile
import unittest

from test_design_service import answer, confirm_the_cells, make_deposit, proposal

from pertura import design_gate, record as rc, screen_object
from pertura.analysis_service import TOOL_NAMES, AnalysisService

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUGIN = os.path.join(ROOT, "plugin")
EXAM_TOOLS = ("lock_question", "record_claim", "practice_decoys", "describe_screen", "notebook_run",
              "record_judgement", "try_test", "certify_as", "propose_fanout", "make_report")


def named_tools(text):
    """The tool names a plugin text names in backticks."""
    return {t for t in re.findall(r"`([a-z_]+)`", text) if "_" in t}


class TestDefaultPath(unittest.TestCase):
    def test_the_default_skill_names_only_the_analysis_servers_tools(self):
        text = open(os.path.join(PLUGIN, "skills", "pertura", "SKILL.md"), encoding="utf-8").read()
        tools = named_tools(text)
        # names in backticks that are not tools: the checklist's items, a file, an argument, the notebook's helper
        files = {"guides_agree", "how_judgements_become_choices", "question_id", "save_figure", "block_call",
                 "block_calls", "state_run",  # record_finding's argument for a state finding
                 "recipe_as_run",  # a module run's field (docs/adr/0031)
                 "read_beside_it",  # a reply's field: what to read beside a result (docs/adr/0032, amendment 2)
                 "run_ids",  # module_result's argument: the runs whose first to finish it returns
                 "user_words",  # open_thread's and the others' argument: the user's words, relayed
                 "next_of",  # open_thread's argument: the result whose next question it is (docs/adr/0048)
                 "fork_of",  # open_thread's argument: the step a fork redoes (the analysis as a tree, 2026-11-13)
                 "doubt_id",  # open_thread's argument: a thread testing whether a finding holds (B3b, 2026-11-13)
                 "proposed_as",  # open_thread's argument: the research lead's proposal it opens (docs/adr/0050, D3)
                 "figures_to_draw"}  # a reply's item: the lead's planned figures, drawn by workers (B1, 2026-11-13)
        self.assertTrue(tools - files <= TOOL_NAMES, sorted(tools - files - TOOL_NAMES))
        for exam in EXAM_TOOLS:
            self.assertNotIn(exam, text)
        for word in ("decoy", "held out", "held-out", "lock"):  # whole words: "block" is no exam word
            self.assertIsNone(re.search(rf"\b{word}", text.lower()), word)


    def test_agents_and_the_command_name_existing_tools(self):
        for f in ("agents/worker.md", "agents/design-auditor.md", "commands/status.md"):
            text = open(os.path.join(PLUGIN, f), encoding="utf-8").read()
            listed = set(re.findall(r"mcp__plugin_pertura_pertura__([a-z_]+)", text))
            self.assertTrue(listed <= TOOL_NAMES, (f, sorted(listed - TOOL_NAMES)))
            for exam in EXAM_TOOLS:
                self.assertNotIn(exam, text, (f, exam))


    def test_no_command_shares_a_skills_name(self):
        # a plugin command named like a skill shadows it: the Skill tool then loads the command's text, not SKILL.md
        # (development sessions 2 and 3)
        commands = {f[:-3] for f in os.listdir(os.path.join(PLUGIN, "commands")) if f.endswith(".md")}
        skills = {d for d in os.listdir(os.path.join(PLUGIN, "skills"))
                  if os.path.exists(os.path.join(PLUGIN, "skills", d, "SKILL.md"))}
        self.assertEqual(commands & skills, set())
        self.assertIn("pertura", skills)


class TestAnalysisSession(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.deposit = os.path.join(cls.tmp.name, "deposit")
        os.makedirs(cls.deposit)
        make_deposit(cls.deposit)
        cls.svc = AnalysisService({"workspace": os.path.join(cls.tmp.name, "ws")})

    @classmethod
    def tearDownClass(cls):
        cls.svc.close()
        cls.tmp.cleanup()

    def ok(self, tool, elicit=None, **args):
        ok, out = self.svc.call(tool, args, elicit=elicit)
        self.assertTrue(ok, out)
        return out

    def test_a_session_runs_through_the_record(self):
        ok, out = self.svc.call("plan_questions", {})
        self.assertFalse(ok)  # no project yet
        self.ok("open_project", data_folder=self.deposit)
        ok, out = self.svc.call("plan_questions", {})
        self.assertFalse(ok)
        self.assertIn("not confirmed", out["error"])
        self.ok("measure")
        self.ok("check_design", proposal=proposal())
        self.ok("confirm_design", elicit=answer())
        design = design_gate.load(self.svc.design.design_file)[0]
        screen_object.build(self.deposit, design, self.svc._screen_dir())  # build_screen_object's job, run here
        plan = self.ok("plan_questions")["questions"]
        self.assertIn("A.what_was_done", [q["question_id"] for q in plan])
        added = self.ok("add_question", letter="C", item="cell cycle", because="the user asked")
        run = self.ok("run_code", what="cells per label", question_id="A.what_was_done",
                      code="import pandas as pd\nc = pd.read_csv(screen + '/cells.tsv.gz', sep='\\t')\n"
                           "print(c['label'].value_counts().to_dict())")
        self.assertEqual(run["status"], "ok")
        self.ok("read_run", run_id=run["run_id"], reading="every target has cells")
        out = self.ok("record_finding", finding_id="f1", question_id="A.what_was_done", target="T1", feature="T1",
                      direction="down", population="all", run_ids=[run["run_id"]], kind="custom")
        self.assertIn("own code", out["checks"])  # the agent's own code: its checks are its own
        self.ok("record_check", finding_id="f1", check="own", outcome="agrees", run_id=run["run_id"],
                reason="both donors show it")
        self.ok("raise_doubt", doubt_id="d1", question_id="A.what_was_done", text="one donor carries it",
                would_change="the effect vanishes in the other donor")
        self.ok("open_branch", branch_id="b1", doubt_id="d1", plan="test by donor")
        self.ok("close_branch", branch_id="b1", outcome="holds in both", run_ids=[run["run_id"]])
        self.ok("close_question", question_id="A.what_was_done", status="detected", evidence=[run["run_id"]],
                figure="none.png")
        design = self.ok("get_design")
        self.assertIn("perturbation.controls", design["values"])  # the values, not only which are confirmed
        ok, out = self.svc.call("read_table", {"run_id": "no-such-run", "name": "result"})
        self.assertFalse(ok)
        self.assertIn("no run no-such-run; run IDs come from the tools' replies", out["error"])  # not "no table"
        ok, out = self.svc.call("close_question", {"question_id": added["question_id"], "status": "not_pursued",
                                                   "reason": "out of scope", "by": "user"})
        self.assertFalse(ok)  # the user's decision is asked in chat and relayed with their words (docs/adr/0032)
        self.assertIn("user_words", out["error"])
        out = self.ok("close_question", question_id=added["question_id"], status="not_pursued",
                      reason="out of scope", by="user", user_words="skip the cell cycle for now")
        self.assertTrue(out["closed"])
        st = self.svc.record.state()
        self.assertEqual(st["runs"][run["run_id"]]["reading"], "every target has cells")
        self.assertEqual(st["questions"][added["question_id"]]["user_words"], "skip the cell cycle for now")
        items = self.ok("open_items")
        closed = {"A.what_was_done", added["question_id"]}
        self.assertEqual(items["blocks_report"], [])  # nothing blocks the report (docs/adr/0022, section 13)
        self.assertFalse(any(x["id"] in closed for x in items["open"] if x["kind"] == "question without a status"))
        self.assertFalse(any(x["id"] in ("f1", "d1", run["run_id"]) for x in items["open"]))
        status = self.ok("pertura_status")
        self.assertTrue(status["screen_object"])
        self.assertNotIn("questions", status)  # the digest in place of the questions' count (item 13)
        self.assertIn("line", status["digest"])


class TestRobustnessSwitch(unittest.TestCase):
    """Build step 3, bar 5, for the checks and skeptic pieces (step 5; docs/adr/0025): turned off, a finding's checks
    do not start and the skeptic's null_of_right_unit is refused; on, recording a finding starts its checks.
    check_robustness is no longer a tool."""

    def session(self, root, switches):
        deposit = os.path.join(root, "deposit")
        os.makedirs(deposit)
        make_deposit(deposit)
        svc = AnalysisService({"workspace": os.path.join(root, "ws"), "switches": switches})
        for tool, args, elicit in (("open_project", {"data_folder": deposit}, None), ("measure", {}, None),
                                   ("check_design", {"proposal": proposal()}, None),
                                   ("confirm_design", {}, answer())):
            ok, out = svc.call(tool, args, elicit=elicit)
            self.assertTrue(ok, out)
        screen_object.build(deposit, design_gate.load(svc.design.design_file)[0], svc._screen_dir())
        return svc

    def finding(self, svc, kind="gene", finding_id="f1", **more):
        if "C.genes" not in svc.record.state()["questions"]:
            svc.record.append("question_planned", {"question_id": "C.genes", "letter": "C", "item": "genes",
                                                   "because": "always"}, rc.PLAN)
        return svc.call("record_finding", {"finding_id": finding_id, "question_id": "C.genes", "target": "TA",
                                           "feature": "G1", "direction": "down", "population": "all",
                                           "run_ids": [], "kind": kind, **more})

    def test_off_starts_no_checks_and_on_starts_them_when_a_finding_is_recorded(self):
        from unittest import mock
        with tempfile.TemporaryDirectory() as d:
            off = self.session(os.path.join(d, "off"), {"robustness": False, "skeptic": False})
            ok, out = off.call("check_robustness", {"finding_id": "f1", "kind": "gene", "feature": "G1"})
            self.assertFalse(ok)
            self.assertIn("unknown tool", out["error"])
            ok, out = self.finding(off)
            self.assertTrue(ok, out)
            self.assertEqual(out["checks"], "switched off for this record")
            # the skeptic's null is the finding's calibration check now: switched off with the checks
            ok, out = off.call("null_of_right_unit", {"finding_id": "f1"})
            self.assertTrue(ok, out)
            self.assertEqual(out["checks"], "switched off for this record")
            self.assertFalse([r for r in off.record.state()["runs"].values() if r["what"].startswith("checks")])
            off.close()
            on = self.session(os.path.join(d, "on"), {})
            for more, words in (({"kind": "a guess"}, "kind is one of"),
                                ({"direction": "lower"}, "direction is one of 'up' or 'down'"),
                                ({"target": "NOPE"}, "no target 'NOPE'"), ({"reference": "control"}, "reference is"),
                                ({"finding_id": "a/b"}, "a finding_id is a name")):
                ok, out = self.finding(on, **{"finding_id": "f0", **more})
                self.assertFalse(ok, more)
                self.assertIn(words, out["error"])
            with mock.patch.object(on.record, "start_run", return_value="checks-job") as started:
                ok, out = self.finding(on)
            self.assertTrue(ok, out)
            self.assertEqual(out["checks_run"], "checks-job")
            with mock.patch.object(on.record, "start_run", return_value="again-job"):
                ok, again = on.call("check_finding", {"finding_id": "f1"})  # after a failed or stopped run
            self.assertTrue(ok, again)
            self.assertEqual(again["checks_run"], "again-job")
            ok, out = on.call("check_finding", {"finding_id": "nope"})
            self.assertFalse(ok)
            cmd = started.call_args[0][1]
            spec = cmd[cmd.index("--spec") + 1]
            with open(spec, encoding="utf-8") as fh:
                self.assertEqual(json.load(fh)["direction"], -1)
            on.close()

    def test_a_claim_on_several_targets_and_on_a_score_the_notebook_saved(self):
        """A claim names up to six targets, each checked (13 of the 20 findings on the fair comparison's first screen
        were about 2 to 6 targets), and a per-cell score the agent computed is checked once its table is saved."""
        from unittest import mock
        import pandas as pd
        with tempfile.TemporaryDirectory() as d:
            on = self.session(os.path.join(d, "on"), {})
            with mock.patch.object(on.record, "start_run", return_value="checks-job") as started:
                ok, out = self.finding(on, target=None, targets=["TA", "TB"])
            self.assertTrue(ok, out)
            cmd = started.call_args[0][1]
            with open(cmd[cmd.index("--spec") + 1], encoding="utf-8") as fh:
                self.assertEqual(json.load(fh)["targets"], ["TA", "TB"])
            f = on.record.state()["findings"]["f1"]
            self.assertEqual((f["target"], f["targets"]), ("TA", ["TA", "TB"]))
            ok, out = self.finding(on, finding_id="f2", target=None, targets=["TA", "NOPE"])
            self.assertIn("no target 'NOPE'", out["error"])
            ok, out = self.finding(on, finding_id="f2", targets=["TA", "TB"])
            self.assertIn("target (one) or targets (several), not both", out["error"])
            ok, out = self.finding(on, finding_id="f3", kind="score", feature="table:activation")
            self.assertIn("no saved table 'activation'", out["error"])
            folder = os.path.join(on.project, "notebooks", "main", "tables")
            os.makedirs(folder, exist_ok=True)
            pd.DataFrame({"barcode": ["c1", "c1"], "score": [0.1, 0.2]}).to_csv(
                os.path.join(folder, "activation.tsv.gz"), sep="\t", index=False)
            ok, out = self.finding(on, finding_id="f3", kind="score", feature="table:activation")
            self.assertIn("one row per barcode", out["error"])
            pd.DataFrame({"barcode": ["c1", "c2"], "score": [0.1, 0.2]}).to_csv(
                os.path.join(folder, "activation.tsv.gz"), sep="\t", index=False)
            with mock.patch.object(on.record, "start_run", return_value="checks-job") as started:
                ok, out = self.finding(on, finding_id="f3", kind="score", feature="table:activation")
            self.assertTrue(ok, out)
            cmd = started.call_args[0][1]
            with open(cmd[cmd.index("--spec") + 1], encoding="utf-8") as fh:
                table = json.load(fh)["score_table"]
            # the version the finding read, a copy named by its content (a table saved again changes no check)
            self.assertRegex(os.path.basename(table), r"^activation-[0-9a-f]{12}\.tsv\.gz$")
            self.assertEqual(pd.read_csv(table, sep="\t")["score"].tolist(), [0.1, 0.2])
            on.close()


class TestTheTimeEstimate(unittest.TestCase):
    """docs/adr/0024, D9: a run of a slow method says how long it will take, from the measured times."""

    def test_a_mast_run_says_how_long_it_will_take(self):
        from unittest import mock
        from pertura import limits
        with tempfile.TemporaryDirectory() as d, mock.patch.object(limits, "THREAD_LIMIT", 6), \
                mock.patch.dict(os.environ):
            os.environ.pop("PERTURA_DE_WORKERS", None)  # the job's threads set the comparisons at a time, not the host's
            with open(os.path.join(d, "screen.json"), "w", encoding="utf-8") as fh:
                json.dump({"labels": {"A": 50, "B": 20, "C": 5, "control": 500, "none": 30, "A|B": 40}}, fh)
            svc = AnalysisService({"workspace": os.path.join(d, "ws")})
            try:
                text = svc._estimate("C.genes", {"method": "field"}, d)
                self.assertIn("12 comparisons (2 targets and the calibration groups), 6 at a time", text)
                self.assertTrue(text.startswith("about 0.5 hours"))
                self.assertIsNone(svc._estimate("C.genes", {}, d))  # no project open: the design's tests unknown
            finally:
                svc.close()


class TestPriorKnowledge(unittest.TestCase):
    """Build step 6, bar 3: every item the outside-knowledge tools return (gene sets, a cited source, an enrichment
    run) is in the record tagged as prior knowledge."""

    @unittest.skipUnless(importlib.util.find_spec("gseapy"), "GSEApy (G.meaning) is not installed here")
    def test_every_outside_item_is_recorded_as_prior_knowledge(self):
        from unittest import mock
        with tempfile.TemporaryDirectory() as d:
            sets = os.path.join(d, "sets", "msigdb")
            os.makedirs(sets)
            members = {"SET_A": [f"G{i}" for i in range(10)], "SET_B": [f"G{i}" for i in range(10, 20)],
                       "SET_C": ["G0"] + [f"G{i}" for i in range(20, 29)]}
            with open(os.path.join(sets, "h.all.v2025.1.Hs.symbols.gmt"), "w", encoding="utf-8") as fh:
                for name, genes in members.items():
                    fh.write("\t".join([name, "na", *genes]) + "\n")
            deposit = os.path.join(d, "deposit")
            os.makedirs(deposit)
            make_deposit(deposit)
            with mock.patch.dict(os.environ, {"PERTURA_GENE_SETS": os.path.join(d, "sets")}):
                svc = AnalysisService({"workspace": os.path.join(d, "ws")})
                organism = {"value": "human", "status": "reported", "source": "GEO"}
                for tool, args, elicit in (("open_project", {"data_folder": deposit}, None), ("measure", {}, None),
                                           ("check_design", {"proposal": proposal(experiment__organism=organism)},
                                            None), ("confirm_design", {}, answer())):
                    ok, out = svc.call(tool, args, elicit=elicit)
                    self.assertTrue(ok, out)
                design = design_gate.load(svc.design.design_file)[0]
                screen_object.build(deposit, design, svc._screen_dir())
                confirm_the_cells(svc)  # the modules wait for the cells' rule after the build
                returned = []
                for args in ({"name": "SET_B"}, {"gene": "G0"}):
                    ok, out = svc.call("gene_sets", args)
                    self.assertTrue(ok, out)
                    self.assertEqual(out["tag"], rc.PRIOR)
                    returned.append(sorted(out["sets"]))
                self.assertEqual(returned, [["SET_B"], ["SET_A", "SET_C"]])
                ok, out = svc.call("record_knowledge", {"question_id": "G.meaning", "source": "PubMed",
                                                        "citation": "PMID 1", "text": "T1 is a known regulator"})
                self.assertTrue(ok, out)
                ok, run = svc.call("run_module", {"module": "G.meaning", "inputs": {"genes": [f"G{i}" for i in
                                                                                         range(6)]}, "wait": 300})
                self.assertTrue(ok, run)
                self.assertEqual(run["state"], "done", run)
                self.assertEqual(run["tag"], rc.PRIOR)
                ok, again = svc.call("module_result", {"run_id": run["run_id"]})
                self.assertTrue(ok, again)
                knowledge = list(svc.record.state()["knowledge"])
                svc.close()
        self.assertEqual(len(knowledge), 4)  # two gene-set calls, one citation, one enrichment run, each once
        self.assertTrue(all(k["tag"] == rc.PRIOR for k in knowledge))
        for names, k in zip(returned, knowledge[:2]):
            self.assertTrue(all(n in k["text"] for n in names), (names, k))
        self.assertEqual(knowledge[2]["item"], "PMID 1")
        self.assertEqual(knowledge[3]["run_id"], run["run_id"])
        self.assertIn("SET_A", knowledge[3]["text"])  # the set the run found


def test_check_design_takes_changes_to_the_proposal_checked_last(tmp_path):
    """32 check_design calls in three runs sent 137,000 characters, as the tool showed only the whole proposal; on
    GSE267740 a proposal of the quality rule alone dropped the confirmed rest. The tool now takes changes, with why."""
    deposit = tmp_path / "deposit"
    deposit.mkdir()
    make_deposit(str(deposit))
    svc = AnalysisService({"workspace": str(tmp_path / "ws")})
    try:
        for tool, args in (("open_project", {"data_folder": str(deposit)}), ("measure", {}),
                           ("check_design", {"proposal": proposal()})):
            ok, out = svc.call(tool, args)
            assert ok, out
        ok, out = svc.call("check_design", {"changes": {"perturbation.modality": "CRISPRi"}, "why": "the vector map"})
        assert ok, out
        assert svc.design._proposal(out["proposal_sha256"])["units"]  # the rest is the last proposal's
    finally:
        svc.close()
