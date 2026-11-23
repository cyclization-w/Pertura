"""The survey says what it waits for (docs/adr/0047, A3). On GSE263761 (194f862) the cells' rule was never confirmed,
the survey never started, and its 16 rows read "queued" in the status and the report."""
from test_the_survey_of_the_designs_comparisons import cells_of, node, screen
from pertura import results_report, survey
from pertura.analysis_service import AnalysisService, _module_and_choices


class Waiting:
    """The analysis service's survey rows over a design, with no run yet and the cells waiting on the user."""
    _survey_rows = AnalysisService._survey_rows

    def __init__(self, comparisons, waits):
        self.comparisons, self.waits = comparisons, waits
        self._module_and_choices = _module_and_choices

    def _survey_comparisons(self):
        return self.comparisons

    def _survey_waits(self):
        return self.waits

    def _run_result(self, run_id):
        return None, None

    class record:
        @staticmethod
        def state():
            return {"runs": {}}


def test_each_row_with_no_run_names_the_wait(tmp_path):
    design = {"experiment": {"conditions": node({"column": "time", "names": ["6h", "12h"]})}}
    comps = survey.comparisons(design, screen(tmp_path, cells_of(["6h", "12h"])))
    queued = {r["id"] for r in Waiting(comps, None)._survey_rows() if r["reason"] == "queued"}
    assert queued
    rows = {r["id"]: r for r in Waiting(comps, "data.qc is not confirmed")._survey_rows()}
    assert all(rows[i]["reason"] == "waits: data.qc is not confirmed" for i in queued)
    assert not any(r["reason"] == "queued" for r in rows.values())


def test_the_report_says_the_survey_has_not_started_and_why(tmp_path):
    rows = [{"id": "cells_per_target", "row": "cells_per_target", "scope": "each target", "state": survey.RUNNING,
             "reason": "waits: data.qc is not confirmed", "run_id": None}]
    text = "\n".join(results_report.survey_lines(
        {"facts": {}, "rows": rows, "complete": False, "ended": 0, "waits": "data.qc is not confirmed"},
        str(tmp_path), str(tmp_path / "out"), results_report.Cited({"runs": {}})))
    assert "The survey has not started, because data.qc is not confirmed." in text
    assert "still running" not in text
