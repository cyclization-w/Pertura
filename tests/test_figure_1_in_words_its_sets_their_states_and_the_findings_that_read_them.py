"""Figure 1 in words (results_report.screen_reading): the targets past their line, each set of targets alike with the
targets opposite it, the states each side moves and the finding that reads it, and the largest shifts of the other
targets, all written by code from the runs the figure draws."""
import itertools
import json

import pandas as pd

from pertura import results_report


def _states(folder, rows):
    folder.mkdir()
    pd.DataFrame(rows, columns=["target", "state", "q", "share", "control_share"]).to_csv(
        folder / "result.tsv", sep="\t", index=False)


def _runs(tmp_path):
    (tmp_path / "ov").mkdir()
    pd.DataFrame({"target": list("ABCDEFG"), "e_distance": [0.9, 0.8, 0.7, 0.1, 0.1, 0.1, 0.1],
                  "past_the_line": [True, True, True, False, False, False, False]}).to_csv(
        tmp_path / "ov" / "targets.tsv", sep="\t", index=False)
    (tmp_path / "ov" / "result.json").write_text(json.dumps({"diagnostics": {"legend": [
        {"letter": "d", "input": "similar", "run": "sim"}, {"letter": "e", "input": "states", "run": "states"}]}}),
        encoding="utf-8")
    r = {("A", "B"): 0.8, ("A", "C"): 0.75, ("B", "C"): 0.7, ("D", "E"): 0.6,
         ("A", "F"): -0.5, ("B", "F"): -0.5, ("C", "F"): -0.5}
    pairs = [(a, b, r.get((a, b), 0.0)) for a, b in itertools.combinations("ABCDEF", 2)]
    (tmp_path / "sim").mkdir()
    pd.DataFrame({"target": [a for a, _, _ in pairs], "other": [b for _, b, _ in pairs],
                  "correlation": [c for _, _, c in pairs],
                  "control_pairs_as_high": [0.0 if c > 0.3 else 1.0 for _, _, c in pairs],
                  "control_pairs_as_low": [0.0 if c < -0.2 else 1.0 for _, _, c in pairs]}).to_csv(
        tmp_path / "sim" / "result.tsv", sep="\t", index=False)
    (tmp_path / "sim" / "result.json").write_text(json.dumps(
        {"diagnostics": {"control_pairs_95th_percentile": 0.3, "control_pairs_5th_percentile": -0.2}}),
        encoding="utf-8")
    _states(tmp_path / "states", [("A", "S01", 0.01, 0.4, 0.1), ("B", "S01", 0.02, 0.3, 0.1),
                                  ("C", "S03", 0.5, 0.2, 0.2), ("F", "S02", 0.001, 0.05, 0.2),
                                  ("G", "S02", 0.01, 0.5, 0.2)])
    # a later rerun of the states, numbered anew: Figure 1 draws the first, so its words read the first
    _states(tmp_path / "states2", [("A", "S09", 0.01, 0.4, 0.1)])
    return {rid: {"run_id": rid, "job_id": rid, "state": "done", "what": f"module {module}"}
            for rid, module in (("ov", "A.screen_overview"), ("sim", "E.similar_targets"),
                                ("states", "C.states_and_composition"), ("states2", "C.states_and_composition"))}


def test_each_set_with_the_states_each_side_moves_and_who_reads_it(tmp_path):
    st = {"runs": _runs(tmp_path),
          "findings": {"f1": {"statement": "A, B and C act alike. They share many genes.", "targets": ["A", "B", "C"]},
                       "f2": {"statement": "D changes few genes.", "target": "D"},
                       "f3": {"statement": "A alone " + "moves " * 60 + "much.", "target": "A"},
                       "f4": {"statement": "B alone.", "target": "B"},
                       # on a later states run, numbered anew: its S02 is not the figure's, so it is not quoted
                       "f5": {"statement": "G fills S02.", "target": "G", "run_ids": ["states2"]}}}
    lines = results_report.screen_reading(st, str(tmp_path))
    assert lines[0] == ("**The screen's structure.** 3 of 7 targets lie past their line, the farthest first: A, B, C "
                        "[run:ov]."), lines
    assert lines[1] == "2 sets of targets alike, each read against the control-guide pairs [run:sim]:", lines
    first, second, rest = lines[2:]
    assert first.startswith("- Alike: A, B, C (mean r 0.75; 3 of 3 pairs past the control pairs); opposite them: "
                            "F -0.50."), first
    assert "Its targets move S01 (A 40% against 10%, B 30% against 10%)." in first, first
    assert "S09" not in first, first
    assert "Those opposite it move S02 (F 5% against 20%)." in first, first
    # the finding reading most of its targets, its first sentence alone; the others counted
    assert "Read in: A, B and C act alike [finding:f1], and 2 more findings." in first, first
    assert first.endswith("No finding reads those opposite it."), first
    assert second.startswith("- Alike: D, E (mean r 0.60"), second
    assert "No state share of its targets moves at q < 0.05." in second, second
    assert "Read in: D changes few genes [finding:f2]." in second, second
    # panel e: the largest shift of a target no set line names, with the finding that reads it
    assert rest == ("- The largest state shifts of the other targets [run:states]: G in S02, 50% against 20% "
                    "(no finding reads it)."), rest
    st["findings"] = {"f3": st["findings"]["f3"]}  # a long first sentence is cut at a word
    quoted = results_report.screen_reading(st, str(tmp_path))[2].split("Read in: ")[1]
    assert quoted.startswith("A alone moves") and " ... [finding:f3]." in quoted, quoted
    assert len(quoted.split(" ... ")[0]) <= results_report.READINGS_QUOTED, quoted


def test_nothing_where_neither_the_overview_nor_the_similarity_ran(tmp_path):
    runs = _runs(tmp_path)
    st = {"runs": {"states": runs["states"]}, "findings": {}}
    assert results_report.screen_reading(st, str(tmp_path)) == []
    st["runs"]["sim"] = runs["sim"]  # the sets without the overview: no count of targets past their line
    lines = results_report.screen_reading(st, str(tmp_path))
    assert lines[0].startswith("**The screen's structure.** 2 sets of targets alike"), lines
    assert "No finding reads it." in lines[1], lines
