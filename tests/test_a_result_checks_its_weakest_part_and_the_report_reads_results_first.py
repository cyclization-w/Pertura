"""From the fresh review of 2026-11-13, on the runs of 2b6a17a and 99a1f49:
- #3: in three of three runs the harness's question after the report ("which result would you not yet stand behind?")
  narrowed a result the skeptic and the lead had passed. report_result now asks it once, at a result's first version.
- #6: most findings the results rest on had no checks run; each result now says how many did, where it is read.
- #9: GSE190604's first result came after nine figures of code's. The results follow Figure 1; the survey and the
  first look come after them."""
import os
import tempfile
import unittest
from unittest import mock

from test_design_service import make_deposit

from pertura import record as rc
from pertura.analysis_service import AnalysisService


class TestAResultAndTheReport(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        deposit = os.path.join(self.tmp.name, "deposit")
        os.makedirs(deposit)
        make_deposit(deposit)
        self.svc = AnalysisService({"workspace": os.path.join(self.tmp.name, "ws")})
        ok, out = self.svc.call("open_project", {"data_folder": deposit})
        assert ok, out
        rec = self.svc.record
        rec.append("run_logged", {"run_id": "r1", "what": "a cell", "code": "c", "inputs": []}, rc.TOOL)
        rec.append("run_finished", {"run_id": "r1", "state": "ok", "output": "o"}, rc.TOOL)
        rec.append("figure_saved", {"figure_id": "fig1", "run_id": "r1", "paths": {"png": "f.png"}, "caption": "c"},
                   rc.TOOL)
        for fid in ("f1", "f2"):
            self.ok("record_finding", finding_id=fid, statement=f"Rorc lowers Il17a, {fid} [run:r1].",
                    run_ids=["r1"], kind="description")

    def tearDown(self):
        self.svc.close()
        self.tmp.cleanup()

    def ok(self, tool, **args):
        ok, out = self.svc.call(tool, args)
        self.assertTrue(ok, out)
        return out

    def propose(self):
        return self.ok("report_result", result_id="res1", title="Rorc lowers Il17a", finding_ids=["f1", "f2"],
                       figure="fig1", text="Rorc lowers Il17a [finding:f1].")

    def test_a_result_asks_once_for_its_weakest_part_to_be_checked(self):
        self.assertIn("would not yet stand behind", self.propose()["check_its_weakest_part"])
        out = self.ok("report_result", result_id="res1", title="Rorc lowers Il17a in both experiments",
                      finding_ids=["f1", "f2"], figure="fig1", text="Rorc lowers Il17a [finding:f1].")
        self.assertNotIn("check_its_weakest_part", out)  # once, at its first version

    def test_the_report_reads_the_results_first_and_says_how_many_findings_code_checked(self):
        self.propose()
        section = {"section": "states_map", "condition": None, "run_id": "r1", "run_ids": ["r1"], "entries": None,
                   "png": None, "finding_ids": ["f1"]}
        with mock.patch.object(AnalysisService, "_first_look_sections", return_value=[section]):
            self.ok("write_report", story="Rorc lowers Il17a.")
        with open(os.path.join(self.svc.project, "report", "report.md"), encoding="utf-8") as fh:
            text = fh.read()
        self.assertLess(text.index("## Results"), text.index("## First look"))
        self.assertIn("**Its findings,** 0 of 2 with their checks run in code", text)

    def test_the_status_says_when_the_paper_store_is_missing(self):
        """The knowledge fell back to the hand-written cards unsaid when knowledge.db was absent."""
        from pertura import knowledge_store
        with mock.patch.object(knowledge_store, "store_path", return_value=os.path.join(self.tmp.name, "none.db")):
            self.assertEqual(self.ok("pertura_status")["knowledge"], knowledge_store.CARDS_ONLY)
        with mock.patch.object(knowledge_store, "store_path", return_value=__file__):
            self.assertNotIn("knowledge", self.ok("pertura_status"))


def test_small_fixes_from_the_error_count_of_three_runs():
    """The fresh review's error count over three transcripts: a list in read_table's where (6 errors), a value quoted
    twice (a run silently empty), a quality table's server path in the report."""
    import pandas as pd
    from pertura import quality
    from pertura.analysis_service import folded_arguments, rows_where
    t = pd.DataFrame({"target": ["A", "B", "C"], "q": [0.01, 0.2, 0.03]})
    assert rows_where(t, "target in ['A', 'C'] and q < 0.05")["target"].tolist() == ["A", "C"]
    v = pd.DataFrame({"condition": ["in vivo", "in vitro"]})
    assert rows_where(v, "condition == in vivo")["condition"].tolist() == ["in vivo"]  # a value's word
    args = folded_arguments("run_module", {"module": "C.genes", "inputs": {"genes": '"targets_own"'}})
    assert args["inputs"] == {"genes": "targets_own"}
    said = quality.describe_filter({"metric": "cluster", "drop": ["C12"], "table": "/home/u/screen/design/c.tsv"})
    assert said == "cluster in C12 (design/c.tsv)"


def test_a_failure_a_reply_goes_on_without_is_one_line_of_the_servers_log(capsys):
    """A review of 5a58805: 20 `except Exception` blocks went on without a word anywhere."""
    from pertura.analysis_service import _logged
    try:
        raise ValueError("no overview yet")
    except ValueError as e:
        _logged(e)
    err = capsys.readouterr().err
    assert err.startswith("pertura: went on without: ValueError: no overview yet at ") and err.count("\n") == 1


if __name__ == "__main__":
    unittest.main()
