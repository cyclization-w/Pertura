"""The analysis as ADR 0032 runs it (build step 2), on the synthetic deposit:
- one form, the design's;
- the first look started and the questions A-H listed once the screen object is built, the questions as a coverage
  list;
- replies that name what is missing, never a prescribed next step;
- findings in a sentence, and the user's replies;
- a branch of alternatives tried in workers' notebooks and closed into one table with its figures;
- the moves a module's measured numbers call for;
- the skill and the agents that say so."""
import json
import os
import re
import tempfile
import unittest
from unittest import mock

from test_design_service import answer, make_deposit, proposal

from pertura import design_gate, moves, record as rc, screen_object
from pertura.analysis_service import AnalysisService

try:
    import ipykernel  # noqa: F401
    import jupyter_client  # noqa: F401
    HAVE_KERNEL = True
except ImportError:
    HAVE_KERNEL = False

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PRESCRIBED = ("the chain's next step", "read_run with your reading", "close the rest from the table",
              "propose the one that fits")


def strings(x):
    """Every string in a reply."""
    if isinstance(x, str):
        yield x
    elif isinstance(x, dict):
        for k, v in x.items():
            if k != "_images":
                yield from strings(v)
    elif isinstance(x, (list, tuple)):
        for v in x:
            yield from strings(v)


class Forms:
    """A client that shows forms, keeping each one it was asked to show."""

    def __init__(self):
        self.raised = []

    def __call__(self, message, schema):
        self.raised.append(message)
        return "accept", {}


@unittest.skipUnless(HAVE_KERNEL, "needs jupyter_client and ipykernel")
class TestTheFlow(unittest.TestCase):  # one session, in order: the tests share its record
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.deposit = os.path.join(cls.tmp.name, "deposit")
        os.makedirs(cls.deposit)
        make_deposit(cls.deposit)
        cls.svc = AnalysisService({"workspace": os.path.join(cls.tmp.name, "ws")})
        cls.forms = Forms()
        cls.replies = []
        for tool, args in (("open_project", {"data_folder": cls.deposit}), ("measure", {}),
                           ("check_design", {"proposal": proposal()})):
            ok, out = cls.svc.call(tool, args, elicit=cls.forms)
            assert ok, out
            cls.replies.append((tool, out))
        ok, out = cls.svc.call("confirm_design", {}, elicit=answer())  # the one form
        assert ok, out
        design = design_gate.load(cls.svc.design.design_file)[0]
        screen_object.build(cls.deposit, design, cls.svc._screen_dir())  # build_screen_object's job, run here
        real = cls.svc.design.call

        def built(tool, args=None, elicit=None):  # the build's job has ended
            return (True, {"job_id": "j", "state": "done", "cells": 1}) if tool == "build_screen_object" else \
                real(tool, args, elicit=elicit)
        with mock.patch.object(cls.svc.design, "call", side_effect=built), \
                mock.patch.object(cls.svc, "run_module", side_effect=lambda q, **k: {"run_id": f"r-{q}"}) as runs:
            ok, cls.built = cls.svc.call("build_screen_object", {"wait": 60}, elicit=cls.forms)
            assert ok, cls.built
            cls.before_the_rule = list(runs.call_args_list)
        # the cells' rule, proposed from the build's cells overview and confirmed on the form: the first look starts
        rule = {"value": {"filters": [{"metric": "n_genes", "min": 1}], "reasons": {"n_genes below 1": "empty"}},
                "status": "proposed", "source": "the cells overview"}
        with_rule = {**proposal(), "data": {"qc": rule}}
        ok, out = cls.svc.call("check_design", {"proposal": with_rule}, elicit=cls.forms)
        assert ok and out["ok"], out
        with mock.patch.object(cls.svc, "run_module", side_effect=lambda q, **k: {"run_id": f"r-{q}"}) as runs:
            ok, cls.cells_confirmed = cls.svc.call("confirm_design", {}, elicit=answer())
            assert ok, cls.cells_confirmed
            cls.first_look = sorted(c.args[0] for c in runs.call_args_list)

    @classmethod
    def tearDownClass(cls):
        cls.svc.close()
        cls.tmp.cleanup()

    def ok(self, tool, **args):
        ok, out = self.svc.call(tool, args, elicit=self.forms)
        self.assertTrue(ok, out)
        self.replies.append((tool, out))
        return out

    def fails(self, tool, expect, **args):
        ok, out = self.svc.call(tool, args, elicit=self.forms)
        self.assertFalse(ok, out)
        self.assertIn(expect, out["error"])

    def test_1_building_the_screen_object_starts_the_first_look_and_plans_nothing(self):
        # the build gives the cells overview and waits on the cells' rule; its confirmation starts the first look
        self.assertIn("cells_overview", self.built)
        self.assertIn("quality rule", self.built["waits_on_user"])
        self.assertEqual(self.before_the_rule, [])
        self.assertIn("first_look", self.cells_confirmed)
        self.assertTrue(self.first_look, self.first_look)
        # what every reading needs, and nothing else: the agent starts the rest (SKILL.md)
        self.assertEqual(sorted(self.first_look), ["A.what_was_done", "B.abundance", "B.efficacy",
                                                   "B.guide_calls_and_their_evidence"])
        st = self.svc.record.state()
        self.assertFalse(st["order"])  # no question plan from code: the agent writes its own (write_plan)
        plan = self.ok("plan_questions")  # the retired tool still lists them, for a record that asks
        self.assertTrue(all("seen" in q for q in plan["questions"]))
        self.assertIn("ask them in chat", plan["missing"])  # what is missing, not a next step
        self.ok("plan_questions", question="what does each regulator do to the cells?")
        self.assertTrue(self.svc.record.state()["question"]["relayed"])
        status = self.ok("pertura_status")  # the digest in place of the coverage count (item 13)
        # the coverage is the turn-end line's lists (decision F4), information with no count of questions seen
        self.assertEqual(sorted(status["coverage"]), ["failed", "running", "stopped",
                                                      "unanswered", "waits_on_user"])
        self.assertEqual(status["digest"]["waits_on_you"][0], "nothing: the agent goes on")

    def test_2_a_cell_shows_its_figure_and_a_finding_is_a_sentence(self):
        cell = self.ok("run_code", what="cells per capture, drawn", question_id="A.what_was_done",
                       code="import pandas as pd, matplotlib.pyplot as plt\n"
                            "c = pt.screen().adata.obs\nc['unit'].value_counts().plot.bar()\nplt.show()")
        self.assertEqual(cell["status"], "ok", cell)
        self.assertEqual(len(cell["_images"]), 1)
        self.assertTrue(self.ok("plan_questions")["questions"])
        seen = {q["question_id"]: q["seen"] for q in self.ok("plan_questions")["questions"]}
        self.assertTrue(seen["A.what_was_done"])  # a run served it
        out = self.ok("record_finding", finding_id="f-units", run_ids=[cell["run_id"]],
                      statement="The two samples hold about as many cells each, so they compare.",
                      figure=cell["figures_shown"][0], kind="description")
        self.assertIn("none", out["checks"])  # no target, feature and direction: no checks, and it says so
        self.assertNotIn("read_beside_it", out)  # a reply carries its call's result; the base is read on request
        self.fails("record_finding", "a sentence", finding_id="f-empty", run_ids=[cell["run_id"]])
        f = self.ok("read_record", what="findings", ids=["f-units"])["findings"]["f-units"]
        self.assertEqual(f["statement"], "The two samples hold about as many cells each, so they compare.")
        self.assertNotIn("finding whose checks have not run", {i["kind"] for i in self.svc.record.critic()})

    def test_3_the_users_replies_are_kept_with_their_finding(self):
        out = self.ok("note_reply", finding_id="f-units", kind="keep", words="Keep this one.")
        self.assertNotIn("f-units", out.get("skeptic_due") or [])  # the skeptic reads what a result rests on
        self.ok("note_reply", finding_id="f-units", kind="knowledge", words="We pooled the donors before sequencing.")
        self.fails("note_reply", "answers a finding", kind="deepen", words="go deeper")
        self.fails("note_reply", "kind is one of", finding_id="f-units", kind="maybe", words="hm")
        f = self.ok("read_record", what="findings", ids=["f-units"])["findings"]["f-units"]
        self.assertEqual([r["kind"] for r in f["replies"]], ["keep", "knowledge"])
        status = self.ok("pertura_status")
        shown = next(x for x in status["findings"] if x["finding_id"] == "f-units")
        self.assertEqual(shown["replies"][0], {"kind": "keep", "words": "Keep this one."})
        self.assertIn("the user", {k["source"] for k in self.svc.record.state()["knowledge"]})
        self.ok("record_skeptic_check", finding_ids=["f-units"], doubt_ids=[])
        self.assertNotIn("f-units", [i["id"] for i in self.ok("open_items")["due"]])

    def test_4_a_branch_of_alternatives_closes_into_one_table(self):
        # the retired open_branch keeps the alternatives (open_thread no longer offers them: B3b, 2026-11-13)
        out = self.ok("open_branch", branch_id="hvg", plan="other numbers of variable genes",
                      alternatives=[{"name": "n1000", "change": "1,000 variable genes"},
                                    {"name": "n4000", "change": "4,000 variable genes"}],
                      base="n_top = 2000\nmixing = 0.9", measures=["mixing"])
        self.assertEqual([w["worker"] for w in out["workers"]], ["hvg-n1000", "hvg-n4000"])
        self.assertIn("hvg__main", out["your_row"])
        self.assertNotIn("read_beside_it", out)
        runs = []
        for worker, value in ((None, 0.9), ("hvg-n1000", 0.8), ("hvg-n4000", 0.95)):
            name = f"hvg__{worker.split('-')[1] if worker else 'main'}"
            cell = self.ok("run_code", what=f"the way {name}", worker=worker,
                           code=f"import matplotlib.pyplot as plt\nplt.plot([0, {value}])\nplt.show()\n"
                                f"pt.save_table({name!r}, {{'mixing': {value}}})")
            self.assertEqual(cell["status"], "ok", cell)
            runs.append(cell["run_id"])
        closed = self.ok("close_branch", branch_id="hvg", outcome="the mixing barely moves", run_ids=runs)
        self.assertIn("comparison", closed, closed)
        rows = {r["way"]: r for r in closed["comparison"]}
        self.assertEqual(list(rows), ["main", "n1000", "n4000"])
        self.assertEqual((rows["main"]["mixing"], rows["n1000"]["mixing"]), (0.9, 0.8))
        self.assertEqual(rows["n4000"]["change"], "4,000 variable genes")
        self.assertEqual(len(closed["_images"]), 3)
        self.assertEqual(sorted(closed["notebook_shut"]), ["hvg-n1000", "hvg-n4000"])  # the workers' notebooks
        st = self.svc.record.state()["branches"]["hvg"]
        self.assertEqual((st["origin"], len(st["comparison"])), ("alternatives", 3))

    def test_4b_a_modules_result_carries_its_result_and_its_entry_is_read_on_request(self):
        folder = os.path.join(self.svc._jobs_root(), "job-efficacy-moves")
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, "result.json"), "w", encoding="utf-8") as fh:
            json.dump({"status": "detected", "summary": "5 guides work",
                       "diagnostics": {"verdicts": {"works": 5, "does not work": 2}}}, fh)
        with open(os.path.join(folder, "result.tsv"), "w", encoding="utf-8") as fh:
            fh.write("guide\tverdict\n")
        self.svc.record.append("run_logged", {"run_id": "job-efficacy-moves", "job_id": "job-efficacy-moves",
                                              "what": "module B.efficacy", "code": "c", "inputs": [],
                                              "question_id": "B.efficacy"}, rc.TOOL)
        self.svc.record.append("run_finished", {"run_id": "job-efficacy-moves", "state": "done", "output": "o"},
                               rc.TOOL)
        out = self.ok("module_result", run_id="job-efficacy-moves")
        self.assertEqual(out["summary"], "5 guides work")
        for field in ("moves", "read_beside_it"):
            self.assertNotIn(field, out)  # a reply carries its call's result
        self.assertTrue(self.ok("read_knowledge", module="B.efficacy"))  # the module's entry, when the agent asks

    def test_5_no_reply_prescribes_a_next_step_and_only_the_design_raises_a_form(self):
        self.assertNotIn("next", dict(self.replies)["open_project"])  # the skill says to launch the auditor
        self.ok("propose_dossier", dossier_id="d1", title="The samples compare", lead_findings=["f-units"],
                main_run=self.svc.record.state()["findings"]["f-units"]["run_ids"][0], why="one finding")
        self.fails("choose_dossiers", "user_words", chosen=["d1"], user_words="")
        self.ok("choose_dossiers", chosen=["d1"], user_words="that one")
        self.assertEqual(self.svc.record.state()["chosen_by"]["user_words"], "that one")
        self.assertEqual(self.forms.raised, [])  # the design's form went to its own elicit; none was raised here
        for tool, out in self.replies:
            for text in strings(out):
                for words in PRESCRIBED:
                    self.assertNotIn(words, text, (tool, text[:200]))

    def test_6_the_threads_replace_the_menu_and_the_status_always_holds_them(self):
        """docs/adr/0035, D1 and D2: the threads replace the menu of the design's questions."""
        opened = self.ok("open_thread", about="TA's cells differ from the control cells, 3.2 times the control-guide "
                                              "groups' 95th percentile", raised_by="r-C.any_effect")
        status = self.ok("pertura_status")
        self.assertIn(opened["thread_id"], [t["thread_id"] for t in status["threads"]["open"]])
        self.assertFalse([tool for tool, out in self.replies if "menu" in out])


@unittest.skipUnless(HAVE_KERNEL, "needs jupyter_client and ipykernel")
class TestTheDesignStepsNotebook(unittest.TestCase):
    def test_before_the_build_the_auditor_looks_and_reads_into_cell_tables(self):
        """The design auditor's notebook opens before the screen object exists: it reads the deposit, draws, and
        writes a cell table where Pertura's readers cannot read the files."""
        with tempfile.TemporaryDirectory() as d:
            deposit = os.path.join(d, "deposit")
            os.makedirs(deposit)
            make_deposit(deposit)
            svc = AnalysisService({"workspace": os.path.join(d, "ws")})
            try:
                ok, out = svc.call("open_project", {"data_folder": deposit})
                self.assertTrue(ok, out)
                code = ("import os, numpy as np, pandas as pd, scipy.sparse as sp\n"
                        "from pertura import cell_table\n"
                        "print(sorted(os.listdir(deposit))[:3])\n"
                        "X = sp.csr_matrix(np.ones((3, 2), dtype=int))\n"
                        "f = pd.DataFrame({'gene_ids': ['g1', 'g2'], 'name': ['G1', 'G2'], "
                        "'feature_types': ['Gene Expression'] * 2})\n"
                        "print(cell_table.write(cell_tables, 'S9', X, f, ['a', 'b', 'c'], made_by=f'run:{run_id}'))\n")
                ok, out = svc.call("run_code", {"code": code, "what": "the deposit's files, and one cell table"})
                self.assertTrue(ok, out)
                self.assertNotIn("build the screen object first", str(out))
                self.assertTrue(os.path.exists(os.path.join(svc.project, "design", "cell_tables", "S9.h5ad")), out)
            finally:
                svc.close()


class TestTheSummary(unittest.TestCase):
    """pertura_status leads with the findings, in plain words and without codes (docs/adr/0032, D14; build step 3,
    bar 1)."""

    CODES = re.compile(r"\b[A-H]\.[a-z][a-z_]+\b|\bP\d+(\.\d+)?\b")

    def test_the_summary_says_what_was_found_how_sure_what_runs_and_what_is_next(self):
        with tempfile.TemporaryDirectory() as d:
            deposit = os.path.join(d, "deposit")
            os.makedirs(deposit)
            make_deposit(deposit)
            svc = AnalysisService({"workspace": os.path.join(d, "ws")})
            try:
                self.assertTrue(svc.call("open_project", {"data_folder": deposit})[0])
                ok, out = svc.call("pertura_status", {})
                self.assertTrue(ok, out)
                self.assertIn("The design is not settled yet", out["summary"])
                self.assertIn("No finding recorded yet", out["summary"])
                svc.record.append("run_logged", {"run_id": "r1", "what": "module C.genes", "code": "c",
                                                 "inputs": []}, rc.TOOL)
                svc.record.append("finding_recorded", {
                    "finding_id": "f1", "run_ids": ["r1"], "question_id": None,
                    "statement": "GATA1 repression lowers the globins (HBZ, HBA1) past the control range."}, rc.AGENT)
                svc.record.append("finding_recorded", {
                    "finding_id": "f2", "run_ids": ["r1"], "question_id": None,
                    "statement": "The two days differ in about 126 genes, not from depth."}, rc.AGENT)
                svc.record.append("reply_recorded", {"kind": "keep", "words": "keep", "finding_id": "f1"}, rc.AGENT)
                summary = svc.call("pertura_status", {})[1]["summary"]
                self.assertIn("2 findings so far", summary)
                self.assertIn("1 kept by the user", summary)
                self.assertIn("1 not yet answered by the user", summary)
                self.assertIn("The latest: The two days differ", summary)
                self.assertIn("Nothing is running", summary)
                self.assertIn("Next: the user's question", summary)
                self.assertIsNone(self.CODES.search(summary), summary)
            finally:
                svc.close()




class TestTheTexts(unittest.TestCase):
    def read(self, *parts):
        with open(os.path.join(ROOT, *parts), encoding="utf-8") as fh:
            return fh.read()

    def test_the_skill_teaches_how_to_analyse_without_stages(self):
        skill = self.read("plugin", "skills", "pertura", "SKILL.md")
        # item 13: a third of the skill and its tools guide (21 KB); 8100 since 2026-11-15, for the audit of 01f3de3's
        # sentences (the context left is no reason to stop, a doubt's test run by the main agent, the skeptic's return)
        self.assertLessEqual(len(skill), 8100)
        self.assertFalse(os.path.exists(os.path.join(ROOT, "plugin", "skills", "pertura", "using_the_tools.md")))
        for heading in ("## Who decides, and when a turn ends", "## 1. The design", "## 2. The analysis",
                        "## 3. The report", "## Rules"):
            self.assertIn(heading, skill)
        for gone in ("## The session", "The chain.", "Work through the card's steps in order", "A module first",
                     "Read every run"):
            self.assertNotIn(gone, skill)
        self.assertIn("Then the form (`confirm_design`)", skill)


    def test_the_auditor_returns_and_never_raises_the_form(self):
        auditor = self.read("plugin", "agents", "design-auditor.md")
        tools = auditor.split("\n---", 1)[0]
        self.assertNotIn("confirm_design", tools)
        self.assertIn("When `check_design` passes, return to the main agent", auditor)

    def test_the_workers_file_says_what_a_worker_may_not_do(self):
        worker = self.read("plugin", "agents", "worker.md")
        # D9 revived: a Choice line opens a thread of alternatives that a worker reruns (the loop decided by the
        # record, 2026-11-20; in the second trial no run tried another threshold or definition)
        self.assertIn("thread of alternatives", worker.lower())
        self.assertIn("Propose results or write the report", worker)  # what it may not do (docs/adr/0038, D3)


if __name__ == "__main__":
    unittest.main()
