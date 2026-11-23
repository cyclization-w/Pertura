"""Development session 11's small problems (docs/build/development_sessions/
session_11_cd4_rest_activation_gse278572_sonnet_after_session10_batch/problems_found_during_the_session.md):
- 12: a finding's checks find a state by its name, as the states table names it, or by its ID;
- 13 and 14: read_table reads a table the notebook saved, and its `where` takes parentheses, or and not;
- 21 and 9 (session 10's 26): the report's screen is the latest overview's figure and legend;
- 22: each doubt is printed in full once, in the appendix;
- 23: a cell uses what earlier cells gave it, not every name it binds itself, and a failed cell is no step."""
from reading_a_report import report_text
import json
import os
import tempfile
import unittest
from unittest import mock

import numpy as np
import pandas as pd

from pertura import finding_checks, flow, results_report
from pertura import record as rc
from pertura.analysis_service import AnalysisError, rows_where

from test_results_reach_the_agent_at_once import Service


class TestStatesByName(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        run = os.path.join(self.tmp.name, "20261101T165213-C-states_and_composition-1cbf83")
        os.makedirs(run)
        self.table = os.path.join(run, "cell_states.tsv")
        pd.DataFrame({"barcode": ["c1", "c2", "c3"], "state": ["S01", "S09", "S09"]}).to_csv(
            self.table, sep="\t", index=False)
        with open(os.path.join(run, "result.json"), "w", encoding="utf-8") as fh:
            json.dump({"diagnostics": {"state_names": {"S01": "S01 CCR7 SELL", "S09": "S09 FOXP3 BATF"}}}, fh)
        self.screen = mock.Mock()
        self.screen.adata.obs_names = ["c1", "c2", "c3"]

    def tearDown(self):
        self.tmp.cleanup()

    def spec(self, feature):
        return finding_checks.load_spec({"kind": "state", "feature": feature, "state_table": self.table},
                                        self.screen)

    def test_a_state_is_found_by_its_name_or_its_id(self):
        for given, state in (("S09 FOXP3 BATF", "S09"), ("s09  FOXP3 batf", "S09"), ("S09", None)):
            spec = self.spec(given)
            self.assertEqual(spec.get("state"), state, given)
            self.assertEqual(list(spec["state_labels"]), ["S01", "S09", "S09"])
            self.assertEqual(spec["feature"], given)  # the finding's own words stay

    def test_a_state_not_found_is_named_with_the_states_and_their_names(self):
        out = finding_checks.Measure(self.spec("S10 GZMB"), self.tmp.name)._state(None, ["A"])
        self.assertIn("no state 'S10 GZMB'", out["A"]["why"])
        self.assertIn("S09 (S09 FOXP3 BATF)", out["A"]["why"])

    def test_a_labelling_without_names_reads_ids_only(self):
        os.remove(os.path.join(os.path.dirname(self.table), "result.json"))
        self.assertEqual(finding_checks.state_names(self.table), {})
        self.assertNotIn("state", self.spec("S09 FOXP3 BATF"))


class TestNotebookTables(Service):
    def setUp(self):
        super().setUp()
        nb = os.path.join(self.tmp.name, "notebooks", "main", "tables")
        os.makedirs(nb)
        self.path = os.path.join(nb, "per_condition_genes.tsv.gz")
        pd.DataFrame({"target": ["A", "B", "C"], "state": ["S09", "S01", "S09"], "q": [0.01, 0.2, 0.04]}).to_csv(
            self.path, sep="\t", index=False, compression="gzip")
        self.runs["run0012-011f27"] = {"run_id": "run0012-011f27", "job_id": None, "state": "ok", "what": "a cell"}
        self.svc.record.state.return_value = {"runs": self.runs, "objects": {
            "table:per_condition_genes": {"kind": "table", "run_id": "run0012-011f27", "path": self.path}}}
        self.svc._need = lambda: None

    def test_read_table_reads_a_table_the_notebook_saved(self):
        for run_id, name in (("run0012-011f27", "per_condition_genes"), ("run0012-011f27", "table:per_condition_genes"),
                             ("table:per_condition_genes", "result")):
            out = self.svc.read_table(run_id, name)
            self.assertEqual(out["rows_total"], 3, (run_id, name))
        with self.assertRaises(AnalysisError) as e:
            self.svc.read_table("run0012-011f27", "other")
        self.assertIn("table:per_condition_genes", str(e.exception))  # the tables there are, named

    def test_where_takes_parentheses_or_and_not(self):
        t = pd.read_csv(self.path, sep="\t")
        self.assertEqual(list(rows_where(t, "(state == 'S09') and q < 0.05")["target"]), ["A", "C"])
        self.assertEqual(list(rows_where(t, "state == 'S01' or q < 0.02")["target"]), ["A", "B"])
        self.assertEqual(list(rows_where(t, "not (state == 'S09')")["target"]), ["B"])
        self.assertEqual(list(rows_where(t, "q < 0.05 and target == C")["target"]), ["C"])  # the plain form as before
        for bad in ("(state == S09)", "(q.max() > 0)", "(@q > 0)", "(target.__class__ == 1)", "(len(q) > 0)"):
            with self.assertRaises(ValueError, msg=bad):
                rows_where(t, bad)


class TestFirstReads(unittest.TestCase):
    def test_a_cell_reads_only_what_it_reads_before_it_binds_it(self):
        self.assertEqual(flow.first_reads("fig, ax = plt.subplots()\nax.plot(x)\nfig.savefig(p)"), {"plt", "x", "p"})
        self.assertEqual(flow.first_reads("df = df[df.q < 0.05]"), {"df"})
        self.assertEqual(flow.first_reads("n += 1"), {"n"})
        self.assertEqual(flow.first_reads("for i, t in enumerate(ts):\n    ax = axes[i]\n    ax.bar(t, 1)"),
                         {"enumerate", "ts", "axes"})
        self.assertEqual(flow.first_reads("rows = [r for r in table if r > cut]"), {"table", "cut"})
        self.assertEqual(flow.first_reads("def f(a, *b, k=d):\n    return a + b + k + g\nf(1)"), {"d", "g"})
        self.assertEqual(flow.first_reads("import numpy as np\nnp.mean(v)"), {"v"})
        self.assertEqual(flow.first_reads("try:\n    1\nexcept E as e:\n    print(e)"), {"E", "print"})
        self.assertEqual(flow.first_reads("%time m = adata.X.mean()"), {"adata", "get_ipython"})
        self.assertEqual(flow.first_reads("def ("), set())

    def test_a_figure_cell_does_not_take_the_cell_before_it_as_an_input(self):
        k = flow.Kernel()
        k.cell("t = pt.load('table:x')", ["table:x"], [], run_id="run0001")
        k.cell("fig, ax = plt.subplots()\nax.bar(t.target, t.n)\nfig.savefig('a.png')", [], [], run_id="run0002")
        self.assertEqual(k.cell("fig, ax = plt.subplots()\nax.bar(t.target, t.q)\nfig.savefig('b.png')", [], [],
                                run_id="run0003"), ["run0001", "table:x"])


class TestFailedCellsLeaveTheSteps(unittest.TestCase):
    def test_a_failed_cell_is_no_step_and_what_it_used_stays(self):
        st = {"objects": {"screen": {"kind": "screen", "run_id": None, "path": "s", "uses": []}},
              "runs": {"run0001": {"run_id": "run0001", "state": "ok", "what": "load", "uses": ["screen"]},
                       "run0002": {"run_id": "run0002", "state": "error", "what": "draw", "uses": ["run0001"]},
                       "run0003": {"run_id": "run0003", "state": "timeout", "what": "draw", "uses": ["run0002"]},
                       "run0004": {"run_id": "run0004", "state": "ok", "what": "draw again",
                                   "uses": ["run0003", "run0001"]}}}
        steps = flow.chain(st, ["run0004"])
        self.assertEqual([s["step"] for s in steps], ["screen", "run0001", "run0004"])
        self.assertEqual(steps[-1]["uses"], ["run0001"])


def _report_record(folder, with_overview=True):
    """A record with the first look's runs, an overview run with its figure and legend, a finding, a result led by
    it and a doubt on it, not answered."""
    r = rc.Record(os.path.join(folder, "record"))
    T, A = rc.TOOL, rc.AGENT
    for qid in ("A.screen_overview", "B.efficacy", "C.genes"):
        r.append("question_planned", {"question_id": qid, "letter": qid[0], "item": qid, "because": "x"}, rc.PLAN)
    jobs = os.path.join(folder, "jobs")
    runs = [("20261101T153057-B-efficacy-f16855", "module B.efficacy", "B.efficacy",
             {"summary": "28 of 28 targets lower their gene"})]
    if with_overview:
        runs.append(("20261101T164935-A-screen_overview-b677e9", "module A.screen_overview", "A.screen_overview",
                     {"summary": "28 targets by 12 columns of evidence; not given: F.replicates_agree",
                      "diagnostics": {"legend": [
                          {"letter": "a", "text": "each target's energy distance, 28 targets", "input": None,
                           "run": None},
                          {"letter": "b", "text": "each target's own gene against its distance, 27 targets",
                           "input": "efficacy", "run": "20261101T153057-B-efficacy-f16855"}]}}))
    for rid, what, q, result in runs:
        os.makedirs(os.path.join(jobs, rid))
        with open(os.path.join(jobs, rid, "result.json"), "w", encoding="utf-8") as fh:
            json.dump(result, fh)
        with open(os.path.join(jobs, rid, "figure.png"), "wb") as fh:
            fh.write(b"\x89PNG\r\n\x1a\n")
        r.append("run_logged", {"run_id": rid, "job_id": rid, "what": what, "code": "", "inputs": [],
                                "question_id": q}, T)
        r.append("run_finished", {"run_id": rid, "state": "done", "output": ""}, T)
    r.append("finding_recorded", {"finding_id": "f1", "question_id": "C.genes", "target": "A", "feature": "G1",
                                  "direction": "up", "population": "T cells",
                                  "run_ids": ["20261101T153057-B-efficacy-f16855"]}, A)
    r.append("doubt_raised", {"doubt_id": "d1", "text": "the rise may be the stimulated sample's alone",
                              "would_change": "the rise in each sample apart", "finding_id": "f1"}, A)
    return r, jobs


class TestTheReportsForm(unittest.TestCase):
    def build(self, with_overview=True):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        r, jobs = _report_record(self.tmp.name, with_overview)
        st = r.state()
        out = os.path.join(self.tmp.name, "report")
        path = results_report.build(st, jobs, out, "A screen", open_items=rc.critic(st))
        return report_text(path), out, st

    def test_the_screen_is_the_overview_s_figure_and_legend(self):
        text, out, _ = self.build()
        screen = text.split("## The screen", 1)[1].split("## Results", 1)[0]
        self.assertIn("![Figure 1](figures/screen.png)", screen)
        self.assertTrue(os.path.exists(os.path.join(out, "figures", "screen.png")))
        self.assertIn("each target's energy distance, 28 targets", screen)
        self.assertIn("(evidence.md#run-20261101T153057-B-efficacy-f16855)", screen)  # panel b names the run it reads
        self.assertNotIn("28 of 28 targets lower their gene", screen)  # not the modules' summaries
        self.assertNotIn("not given", screen)

    def test_without_an_overview_the_screen_is_the_first_look_s_lines(self):
        text, _, _ = self.build(with_overview=False)
        screen = text.split("## The screen", 1)[1].split("## Results", 1)[0]
        self.assertNotIn("Figure 1", screen)
        self.assertIn("28 of 28 targets lower their gene", screen)

    def test_each_doubt_is_printed_in_full_once(self):
        items = [{"kind": "doubt without an answer", "id": "d1", "what": "the rise may be the stimulated sample's alone"}]
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        r, jobs = _report_record(self.tmp.name)
        path = results_report.build(r.state(), jobs, os.path.join(self.tmp.name, "report"), "A screen",
                                    open_items=items)
        text = report_text(path)
        self.assertEqual(text.count("the rise may be the stimulated sample"), 1)
        appendix = text.split("## Appendix", 1)[1]
        self.assertIn("the rise may be the stimulated sample's alone; what would change the judgement: the rise in "
                      "each sample apart", appendix)
        opened = text.split("## Open questions", 1)[1].split("## Methods", 1)[0]
        self.assertIn("1 alternative explanation not yet settled, each in full in the appendix", opened)
        self.assertIn("a doubt on finding f1; not answered; in full in the appendix", text)


if __name__ == "__main__":
    unittest.main()
