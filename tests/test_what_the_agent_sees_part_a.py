"""F4, part A's side: a finding's choices read from its runs (nothing notes alternatives tried since 2026-11-13); the
screen-level signals (the units' and the guides' agreement, the cells overview's clusters the rule keeps) open
threads."""
from unittest import mock

import pandas as pd

from pertura import record as rc
from pertura.analysis_service import AnalysisService


def test_a_finding_rests_on_its_runs_choices(tmp_path):
    rec = rc.Record(tmp_path / "rec")
    run = '{"choices": {"resolution": 1.0}}'
    rec.append("run_logged", {"run_id": "r1", "job_id": "r1", "what": f"module C.states_and_composition with {run}",
                              "code": "c", "inputs": []}, rc.TOOL)
    rec.append("run_finished", {"run_id": "r1", "state": "done", "output": "ok"}, rc.TOOL)
    svc = AnalysisService({"workspace": str(tmp_path / "ws")})
    svc.record = rec
    try:
        choices = svc._choices_of(["r1"])
        by = {c["choice"]: c for c in choices}
        assert by["resolution"]["value"] == 1.0 and by["resolution"]["module"] == "C.states_and_composition"
        assert by["resolution"]["alternatives"]  # the contract's other values
        assert by["cells"]["value"] == "the cells the quality rule keeps"
        rec.append("finding_recorded", {"finding_id": "f1", "statement": "Hdac1 cells gather in S04",
                                        "run_ids": ["r1"], "choices": choices}, rc.AGENT)
        # a run naming no value took the module's own default
        from pertura import question_modules as qm
        rec.append("run_logged", {"run_id": "r4", "job_id": "r4", "what": "module C.states_and_composition",
                                  "code": "c", "inputs": []}, rc.TOOL)
        unnamed = {c["choice"]: c for c in svc._choices_of(["r4"])}
        assert unnamed["resolution"]["value"] == qm.REGISTRY["C.states_and_composition"].default("resolution")
    finally:
        svc.record = None
        svc.close()


def test_a_where_with_and_inside_a_quoted_value():
    from pertura.analysis_service import rows_where
    t = pd.DataFrame({"state_name": ["naive and memory", "effector"], "q": [0.01, 0.2]})
    assert list(rows_where(t, "state_name == 'naive and memory' and q < 0.05")["q"]) == [0.01]
    assert rows_where(t, "q < 0.05 and state_name == effector").empty


def test_a_result_says_how_its_runs_tested_their_targets(tmp_path):
    from pertura import results_report
    folder = tmp_path / "r1"
    folder.mkdir()
    pd.DataFrame({"target": ["Hdac1", "Hdac1", "Il2rb", "Dimt1"], "state": ["S04", "S10", "S04", "S10"],
                  "test": ["propeller", "propeller", "propeller", "fisher_same_samples"]}).to_csv(
        folder / "result.tsv", sep="\t", index=False)
    words = results_report.tests_sentence(str(tmp_path), ["r1", "r1", "missing"])
    assert words == ("In its runs, of its 3 targets, 2 by propeller (speckle) on the samples' proportions, 1 by "
                     "Fisher's exact test [run:r1].")
    assert results_report.tests_sentence(str(tmp_path), ["missing"]) is None
