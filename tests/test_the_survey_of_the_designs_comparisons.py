"""The survey (docs/adr/0045, step 1): the comparisons a confirmed design calls for, and each one's state from the
record's runs. The development runs on 0adebaa looked at what the agent chose: GSE250558's two kinds of control were
never compared, GSE335887's two models never set side by side, and the proteins of GSE263761 never read."""
import json

import pandas as pd

from pertura import survey


def node(v):
    return {"value": v, "status": "confirmed"}


def screen(tmp_path, cells, antibodies=()):
    cells = pd.DataFrame(cells)
    labels = cells["label"].value_counts().to_dict()
    (tmp_path / "screen.json").write_text(json.dumps({"labels": labels, "antibody_features": list(antibodies)}))
    cells.to_csv(tmp_path / "cells.tsv.gz", sep="\t", index=False)
    return str(tmp_path)


def cells_of(conditions, kinds=("non_targeting",), samples=("s1",), targets=("A", "B", "C")):
    """Each condition and sample: ten cells of each target and of each kind of control."""
    rows = []
    for cond, smp in [(c, s) for c in conditions for s in samples]:
        extra = {k: v for k, v in (cond.items() if isinstance(cond, dict) else [("condition", cond)])}
        for t in targets:
            rows += [{"label": t, "control_kind": "", "sample": smp, **extra}] * 10
        for k in kinds:
            rows += [{"label": "control", "control_kind": k, "sample": smp, **extra}] * 10
    return rows


def ids(design, sd):
    return [c["id"] for c in survey.comparisons(design, sd)]


def test_two_kinds_of_control_and_three_time_points(tmp_path):
    design = {"experiment": {"conditions": node({"column": "time", "names": ["6h", "12h", "18h"]})},
              "units": {"independent_unit": node("capture")}}
    sd = screen(tmp_path, cells_of(["6h", "12h", "18h"], kinds=("non_targeting", "cutting_control")))
    got = survey.comparisons(design, sd)
    by = {c["id"]: c for c in got}
    assert "control_kinds" in by and by["control_kinds"]["module"] == "D.cutting_response"
    # each condition against the first one the design lists, never two against each other
    assert [c["id"] for c in got if c["row"] == "controls_across"] == ["controls_across:6h vs 12h",
                                                                         "controls_across:6h vs 18h"]
    # what is compared only: the module fixes the reference (one kind of control) for every caller alike
    assert by["controls_across:6h vs 12h"]["given"] == {"choices": {"what": "controls"}, "inputs": {"pair": ["6h", "12h"]}}
    assert by["effect_changing:6h vs 12h"]["given"] == {"inputs": {"pair": ["6h", "12h"]}}
    assert by["shifts_per_condition:6h vs 18h"]["reads"] == "states"
    assert got[-1]["id"] == "distance:overview" and got[0]["id"] == "cells_per_target"


def test_crossed_factors_compare_one_axis_with_the_other_held(tmp_path):
    design = {"experiment": {"conditions": node([{"name": "model", "levels": ["iMG", "iTF"], "column": "model"},
                                                 {"name": "time", "levels": ["0h", "6h"], "column": "time"}])}}
    conds = [{"condition_model": m, "condition_time": t, "condition": f"{m}|{t}"}
             for m in ("iMG", "iTF") for t in ("0h", "6h")]
    sd = screen(tmp_path, cells_of(conds))
    scopes = {c["scope"] for c in survey.comparisons(design, sd) if c["row"] == "controls_across"}
    assert scopes == {"model: iTF|0h against iMG|0h, within time 0h", "model: iTF|6h against iMG|6h, within time 6h",
                      "time: iMG|6h against iMG|0h, within model iMG", "time: iTF|6h against iTF|0h, within model iTF"}


def test_samples_without_an_axis_are_set_side_by_side_and_said(tmp_path):
    design = {"experiment": {"conditions": node([])}, "units": {"independent_unit": node("none")}}
    sd = screen(tmp_path, cells_of(["x"], samples=("iMG", "iTF")), antibodies=("CD9", "CD81"))
    fx = survey.facts(design, sd)
    assert fx["samples"] == ["iMG", "iTF"] and not fx["axis_for_samples"]
    got = {c["id"]: c for c in survey.comparisons(design, sd)}
    assert got["baselines"]["module"] is None and got["baselines"]["reads"] == "states"
    assert got["proteins"]["given"] == {"choices": {"gene_sets": "proteins"}}
    assert not [i for i in got if i.startswith(("controls_across", "control_kinds"))]  # one kind, no conditions


def test_one_condition_and_two_targets_ask_for_less(tmp_path):
    design = {"experiment": {"conditions": node({"column": "time", "names": ["d28"]})}}
    sd = screen(tmp_path, cells_of(["d28"], targets=("A", "B")))
    assert ids(design, sd) == ["cells_per_target", "own_transcript", "distance:genes", "states"]


def test_a_condition_without_control_cells_is_not_comparable(tmp_path):
    """GSE263761: under the confirmed guide rule, 24 h kept one control cell, and its runs read too few (step 7)."""
    design = {"experiment": {"conditions": node({"column": "time", "names": ["0h", "6h", "24h"]})}}
    rows = [r for r in cells_of(["0h", "6h", "24h"]) if not (r["condition"] == "24h" and r["label"] == "control")]
    sd = screen(tmp_path, rows)
    got = {c["id"]: c for c in survey.comparisons(design, sd)}
    assert got["controls_across:0h vs 24h"]["not_comparable"] == "fewer than 10 control cells in 24h"
    assert got["controls_across:0h vs 6h"]["not_comparable"] is None


def run(rid, what, state="done", output=""):
    return {"run_id": rid, "what": what, "state": state, "output": output, "build": None}


def test_each_comparison_takes_its_state_from_the_runs_that_match_it(tmp_path):
    from pertura.analysis_service import _module_and_choices
    design = {"experiment": {"conditions": node({"column": "time", "names": ["6h", "12h"]})}}
    sd = screen(tmp_path, cells_of(["6h", "12h"], samples=("s1", "s2")))
    comps = survey.comparisons(design, sd)
    st = {"runs": {
        "r1": run("r1", "module B.abundance"),
        "r2": run("r2", 'module C.states_and_composition', state="running"),
        "r3": run("r3", 'module E.condition_x_perturbation with {"choices": {"what": "controls"}, '
                        '"inputs": {"pair": ["6h", "12h"]}}'),
        "r4": run("r4", 'module E.condition_x_perturbation with {"choices": {"what": "scores"}, '
                        '"inputs": {"pair": ["6h", "12h"]}}', state="failed", output="Traceback\nValueError: no targets"),
        "r5": run("r5", 'module E.condition_x_perturbation with {"choices": {"what": "controls"}, '
                        '"inputs": {"pair": ["12h", "6h"]}}')}}
    results = {"r1": ("measured", "100 targets"), "r3": ("detected", "120 genes"), "r5": ("detected", "")}
    rows = {r["id"]: r for r in survey.states(comps, st, lambda rid: results[rid], _module_and_choices)}
    assert rows["cells_per_target"]["state"] == survey.DESCRIPTIVE  # counted, nothing tested
    assert rows["states"]["state"] == survey.RUNNING and rows["baselines"]["state"] == survey.RUNNING
    assert rows["controls_across:6h vs 12h"]["state"] == survey.SIGNAL
    assert rows["controls_across:6h vs 12h"]["run_id"] == "r3"  # r5 compares the other way: not this comparison
    # what="scores" is the module's default, so the agent's explicit default still matches the survey's comparison
    assert rows["effect_changing:6h vs 12h"]["state"] == survey.FAILED
    assert rows["effect_changing:6h vs 12h"]["reason"] == "ValueError: no targets"
    assert rows["own_transcript"]["state"] == survey.RUNNING and rows["own_transcript"]["reason"] == "queued"
    assert not survey.complete(rows.values())
    st["runs"]["r2"]["state"] = "done"
    results["r2"] = ("measured", "13 states")
    rows = {r["id"]: r for r in survey.states(comps, st, lambda rid: results[rid], _module_and_choices)}
    assert rows["baselines"]["state"] == survey.DESCRIPTIVE and rows["baselines"]["run_id"] == "r2"
    shares = rows["shifts_per_condition:6h vs 12h"]  # it waited for the states; now it is given their labels
    assert shares["state"] == survey.RUNNING and shares["reason"] == "queued"
    assert shares["given"]["inputs"] == {"pair": ["6h", "12h"], "cell_states": "r2:cell_states"}


def test_a_comparison_that_takes_the_states_waits_for_them(tmp_path):
    from pertura.analysis_service import _module_and_choices
    design = {"experiment": {"conditions": node({"column": "time", "names": ["6h", "12h"]})}}
    sd = screen(tmp_path, cells_of(["6h", "12h"]))
    st = {"runs": {"r2": run("r2", "module C.states_and_composition", state="running")}}
    rows = {r["id"]: r for r in survey.states(survey.comparisons(design, sd), st, None, _module_and_choices)}
    assert (rows["shifts_per_condition:6h vs 12h"]["state"], rows["shifts_per_condition:6h vs 12h"]["reason"]) == (
        survey.RUNNING, "waits for states")


def test_a_run_with_too_few_cells_reads_not_comparable(tmp_path):
    from pertura.analysis_service import _module_and_choices
    sd = screen(tmp_path, cells_of(["x"]))
    comps = [c for c in survey.comparisons({}, sd) if c["id"] == "alike_opposite"]
    st = {"runs": {"r1": run("r1", "module E.similar_targets")}}
    rows = survey.states(comps, st, lambda rid: ("not_enough_data", "two targets with 30 cells"), _module_and_choices)
    assert (rows[0]["state"], rows[0]["reason"]) == (survey.NOT_COMPARABLE, "two targets with 30 cells")
    assert survey.complete(rows)


def test_each_state_shows_the_targets_that_raise_it_and_those_that_lower_it():
    """GSE342764 on 0adebaa: 37 targets shifted shares, 8 were shown by how many states each moved, and Zmynd8, which
    lowered one state, was not among them."""
    from pertura import candidates
    rows = [{"target": f"T{i}", "state": s, "share": 0.5, "control_share": 0.1, "q": 0.001}
            for i in range(10) for s in ("S1", "S2", "S3")]
    rows.append({"target": "Zmynd8", "state": "S4", "share": 0.065, "control_share": 0.132, "q": 0.02})
    out = candidates.states_shifting(pd.DataFrame(rows))
    shown = [t["target"] for t in out["targets"]]
    assert out["count"] == 11 and "Zmynd8" in shown
    assert out["targets"][shown.index("Zmynd8")]["states"] == {"S4": {"share": 0.065, "control_share": 0.132}}
    assert len(shown) <= candidates.SHOWN_BY_STATE


def test_with_many_states_each_state_keeps_its_first_places():
    """The review of d6bcc7b: places filled state by state and cut at 24 left S07 to S13 of 13 states none."""
    from pertura import candidates
    rows = [{"target": f"{s}{w}{i}", "state": s, "share": 0.5 if w == "u" else 0.01, "control_share": 0.1, "q": 0.001}
            for s in [f"S{k:02d}" for k in range(1, 14)] for w in "ud" for i in range(2)]
    shown = [t["target"] for t in candidates.states_shifting(pd.DataFrame(rows))["targets"]]
    assert all(f"S{k:02d}u0" in shown and f"S{k:02d}d0" in shown for k in range(1, 14))  # every state's first
    assert len(shown) == 26 and not [t for t in shown if t.endswith("1")]  # the later places only up to 24


def test_the_opposite_pairs_keep_their_own_lines():
    """GSE342764's similar targets had 1,155 pairs alike past the line: the opposite pairs got no line of the 25."""
    from pertura.question_modules.e import similar_biology
    alike = [{"target": f"A{i}", "other": f"B{i}", "correlation": 0.9 - i / 100, "control_pairs_as_high": 0.0,
              "control_pairs_as_low": 1.0} for i in range(30)]
    opposite = [{"target": "Zmynd8", "other": "Ep300", "correlation": -0.4, "control_pairs_as_high": 1.0,
                 "control_pairs_as_low": 0.0}]
    lines = similar_biology(pd.DataFrame(alike + opposite), most=25)
    assert "Zmynd8 and Ep300: r -0.40, opposite" in lines
    assert lines[-1] == "6 more pairs past the lines: the result table holds them"


def test_the_report_says_the_facts_used_and_each_comparison_with_its_whole_table(tmp_path):
    from pertura import results_report
    jobs, out = tmp_path / "jobs", tmp_path / "report"
    (jobs / "r1").mkdir(parents=True)
    (jobs / "r1" / "result.json").write_text(json.dumps({"status": "detected", "summary": "in the control cells, "
                                                         "120 genes change from 6h to 12h at q < 0.05",
                                                         "biology": ["higher in 12h: EGR1, FOS", "lower in 12h: ID4"]}))
    pd.DataFrame({"gene": ["EGR1", "ID4"], "effect": [1.2, -0.8], "q": [1e-4, 0.01]}).to_csv(
        jobs / "r1" / "result.tsv", sep="\t", index=False)
    facts = {"factors": [{"name": "time", "levels": ["6h", "12h"]}], "control_kinds": ["cutting_control",
             "non_targeting"], "samples": ["s1", "s2"], "axis_for_samples": True, "antibodies": 0}
    rows = [{"id": "controls_across:6h vs 12h", "row": "controls_across", "scope": "time: 12h against 6h",
             "state": survey.SIGNAL, "reason": None, "run_id": "r1"},
            {"id": "controls_across:6h vs 24h", "row": "controls_across", "scope": "time: 24h against 6h",
             "state": survey.NOT_COMPARABLE, "reason": "no control cells in 24h", "run_id": None}]
    st = {"runs": {"r1": {}}, "findings": {}, "results": {}, "figures": {}, "dossiers": {}, "doubts": {},
          "threads": {}, "questions": {}, "order": []}
    lines = results_report.survey_lines({"facts": facts, "rows": rows}, str(jobs), str(out),
                                        results_report.Cited(st))
    text = "\n".join(lines)
    assert "## The survey" in text and "conditions: time (6h, 12h); kinds of control: cutting control, non targeting" \
        in text
    assert "**The control cells across conditions**, time: 12h against 6h: computed, with a signal. in the control " \
           "cells, 120 genes change" in text
    assert "  - higher in 12h: EGR1, FOS" in text
    assert "time: 24h against 6h: not comparable (no control cells in 24h)" in text
    assert (out / "survey" / "controls_across_6h_vs_12h.tsv").exists()
    assert "[survey/controls_across_6h_vs_12h.tsv](survey/controls_across_6h_vs_12h.tsv)" in text
    assert results_report.survey_lines(None, str(jobs), str(out), results_report.Cited({})) == []
    still = results_report.survey_lines({"facts": facts, "rows": rows, "complete": False, "ended": 1}, str(jobs),
                                        str(out), results_report.Cited(st))
    assert "The survey is still running: 1 of 2 comparisons have ended." in "\n".join(still)


def test_the_reports_survey_draws_its_maps_beside_their_rows(tmp_path):
    """The build goal of 2026-11-11, step 4.1: on GSE263761 the survey's heatmaps (the interferon axis against Spi1,
    the targets alike) stood in no report; a map's figure is drawn beside its row, the checks stay tables."""
    from pertura import results_report
    jobs, out = tmp_path / "jobs", tmp_path / "report"
    for rid in ("r1", "r2"):
        (jobs / rid).mkdir(parents=True)
        (jobs / rid / "result.json").write_text(json.dumps({"status": "detected", "summary": "s"}))
        (jobs / rid / "figure.png").write_bytes(b"png")
    rows = [{"id": "alike_opposite", "row": "alike_opposite", "scope": "the screen", "state": survey.SIGNAL,
             "reason": None, "run_id": "r1"},
            {"id": "cells_per_target", "row": "cells_per_target", "scope": "the screen", "state": survey.SIGNAL,
             "reason": None, "run_id": "r2"}]
    st = {"runs": {"r1": {}, "r2": {}}, "findings": {}, "results": {}, "figures": {}, "dossiers": {}, "doubts": {},
          "threads": {}, "questions": {}, "order": []}
    facts = {"factors": [], "control_kinds": ["non_targeting"], "samples": ["s1"], "antibodies": 0}
    lines = results_report.survey_lines({"facts": facts, "rows": rows}, str(jobs), str(out),
                                        results_report.Cited(st), number=2)
    assert "![Figure 2](figures/survey-alike_opposite.png)" in lines
    assert sum(x.startswith("![Figure") for x in lines) == 1  # the cells per target stay a table
    assert (out / "figures" / "survey-alike_opposite.png").exists()
    assert not any(x.startswith("![Figure") for x in results_report.survey_lines(
        {"facts": facts, "rows": rows}, str(jobs), str(out), results_report.Cited(st)))


def test_the_order_runs_the_first_looks_own_analyses_first_and_the_heaviest_last(tmp_path):
    """D2: the agent's first look runs the changed genes and the targets alike, so its calls take those runs; the
    states, what reads them and the proteins come last (one heavy module at a time in a workspace)."""
    design = {"experiment": {"conditions": node({"column": "time", "names": ["6h", "12h", "18h"]})}}
    sd = screen(tmp_path, cells_of(["6h", "12h", "18h"], kinds=("non_targeting", "cutting_control"),
                                    samples=("s1", "s2")), antibodies=("CD9",))
    rows = [c["row"] for c in survey.comparisons(design, sd)]
    assert rows[:5] == ["cells_per_target", "own_transcript", "distance", "alike_opposite", "control_kinds"]
    assert rows.index("states") > max(i for i, r in enumerate(rows) if r in ("controls_across", "effect_changing"))
    assert rows[rows.index("states") + 1] == "baselines"
    assert rows[-3:] == ["shifts_per_condition", "proteins", "distance"]


def test_each_targets_support_runs_where_the_library_and_the_samples_allow(tmp_path):
    """Each target's guides agreeing and its profile across samples, which the overview sets beside its distance: a
    weak effect every guide and sample share is worth following."""
    design = {"experiment": {"conditions": node({"column": "time", "names": ["6h", "12h"]})}}
    sd = screen(tmp_path, cells_of(["6h", "12h"], samples=("s1", "s2")))
    pd.DataFrame({"guide": ["A_1", "A_2", "B_1", "B_2", "C_1", "NTC_1", "NTC_2"],
                  "target": ["A", "A", "B", "B", "C", "NTC", "NTC"],
                  "kind": ["targeting"] * 5 + ["non_targeting"] * 2}).to_csv(tmp_path / "guides.tsv", sep="\t",
                                                                             index=False)
    got = {c["id"]: c for c in survey.comparisons(design, sd)}
    assert got["guides_agree"]["module"] == "F.guides_agree" and got["guides_agree"]["scope"] == "up to 2 guides a target"
    assert got["units_agree"]["module"] == "F.replicates_agree"
    order = list(got)
    assert order.index("units_agree") < order.index("distance:overview")  # the overview reads them
    one = tmp_path / "one"
    one.mkdir()
    sd = screen(one, cells_of(["6h", "12h"]))  # one sample, no guide table
    assert not {"guides_agree", "units_agree"} & {c["id"] for c in survey.comparisons(design, sd)}


def test_a_layer_without_the_reference_keeps_its_comparisons_not_comparable(tmp_path):
    """D1 and D4: each time against the first the design lists; model B has no 0 h, so its 24 h is not compared with
    its 6 h instead (GPT's synthetic design, the review of d6bcc7b)."""
    design = {"experiment": {"conditions": node([{"name": "model", "levels": ["A", "B"], "column": "model"},
                                                 {"name": "time", "levels": ["0h", "6h", "24h"], "column": "time"}])}}
    conds = [{"condition_model": m, "condition_time": t, "condition": f"{m}|{t}"}
             for m, t in (("A", "0h"), ("A", "6h"), ("A", "24h"), ("B", "6h"), ("B", "24h"))]
    sd = screen(tmp_path, cells_of(conds))
    got = {c["id"]: c for c in survey.comparisons(design, sd) if c["row"] == "controls_across"}
    assert "controls_across:A|0h vs A|24h" in got and got["controls_across:A|0h vs A|24h"]["not_comparable"] is None
    assert not [i for i in got if i == "controls_across:B|6h vs B|24h"]
    b = got["controls_across:0h (model B) vs B|24h"]
    assert b["not_comparable"] == "no cells at 0h within model B, the reference"
    assert b["scope"] == "time: B|24h against 0h (model B), within model B"


def test_cells_outside_a_factor_make_no_comparison(tmp_path):
    """Crossed factors where one reaches part of the cells: no comparison of empty conditions."""
    design = {"experiment": {"conditions": node([{"name": "model", "levels": ["A", "B"], "column": "model"},
                                                 {"name": "time", "levels": ["0h", "6h"], "column": "time"}])}}
    conds = [{"condition_model": m, "condition_time": t, "condition": f"{m}|{t}"} for m in ("A", "B") for t in ("0h", "6h")]
    conds.append({"condition_model": "", "condition_time": "6h", "condition": ""})
    sd = screen(tmp_path, cells_of(conds))
    scopes = [c["scope"] for c in survey.comparisons(design, sd) if c["row"] == "controls_across"]
    assert scopes and not [s for s in scopes if " against " in s and (s.endswith("against ") or ":  " in s)]
    assert all("model " in s for s in scopes if s.startswith("time"))


def test_a_comparison_whose_states_ended_without_a_result_ends_too(tmp_path):
    """D3: the shifts per condition took the states' labels and waited; a failed states run left them waiting forever
    and the survey never complete (the review of d6bcc7b)."""
    from pertura.analysis_service import _module_and_choices
    design = {"experiment": {"conditions": node({"column": "time", "names": ["6h", "12h"]})}}
    sd = screen(tmp_path, cells_of(["6h", "12h"]))
    st = {"runs": {"r2": run("r2", "module C.states_and_composition", state="failed", output="MemoryError")}}
    rows = {r["id"]: r for r in survey.states(survey.comparisons(design, sd), st, None, _module_and_choices)}
    shares = rows["shifts_per_condition:6h vs 12h"]
    assert shares["state"] == survey.FAILED
    assert shares["reason"] == "the states and their shares it reads: failed (MemoryError)"


def test_a_run_whose_result_cannot_be_read_is_failed(tmp_path):
    from pertura.analysis_service import _module_and_choices
    sd = screen(tmp_path, cells_of(["x"]))
    comps = [c for c in survey.comparisons({}, sd) if c["id"] == "alike_opposite"]
    rows = survey.states(comps, {"runs": {"r1": run("r1", "module E.similar_targets")}}, lambda rid: (None, ""),
                         _module_and_choices)
    assert (rows[0]["state"], rows[0]["reason"]) == (survey.FAILED, "its result could not be read")


def test_the_samples_baselines_are_their_control_cells_across_one_labelling(tmp_path):
    """D1: each sample's control cells across the states of one states run, read from its cell states; the review of
    d6bcc7b found the row showed the states run's own table."""
    rows = [{**r, "barcode": f"c{i}"} for i, r in enumerate(cells_of(["x"], samples=("iMG", "iTF")))]
    sd = screen(tmp_path, rows)
    itf = iter(["S1"] * 5 + ["S2"] * 3 + ["S3"] * 2)  # iTF's ten control cells; iMG's all in S1
    states = pd.DataFrame({"barcode": [r["barcode"] for r in rows],
                           "state": [next(itf) if r["label"] == "control" and r["sample"] == "iTF" else "S1"
                                     for r in rows]})
    path = tmp_path / "cell_states.tsv"
    states.to_csv(path, sep="\t", index=False)
    table, summary = survey.baselines(str(path), sd)
    t = table.set_index("state")
    assert (t.loc["S1", "iMG"], t.loc["S1", "iTF"], t.loc["S2", "iMG"], t.loc["S3", "iTF"]) == (1.0, 0.5, 0.0, 0.2)
    assert summary.startswith("the control cells of 2 samples (iMG 10, iTF 10 cells) across 3 states of one labelling")
    assert "the widest spread in S1, a share from 0.50 to 1.00" in summary
