"""A.screen_overview (pertura.question_modules.overview): its table from the first look's runs, its columns' states,
the order of the targets, the states of the user's question first; the service starting it when the runs it reads
have ended and again when a slow module's run ends; the threads of the targets past their own line. The distance itself is tested on the module tests' synthetic screen
(test_question_modules), with scanpy and pertpy."""
import json
import os
from unittest import mock

import numpy as np
import pandas as pd
import pytest

from pertura import screen_object as so
from pertura.question_modules import overview as ov
from pertura.question_modules.data import Screen

from test_cells_overview import DESIGN, _deposit


@pytest.fixture
def screen(tmp_path):
    out = tmp_path / "screen"
    so.build(str(_deposit(tmp_path)), DESIGN, str(out))
    return Screen(str(out), DESIGN)


def _tables():
    return {
        "efficacy": pd.DataFrame({"guide": ["A_1", "A_1", "A_2", "B_1"], "target": ["A", "A", "A", "B"],
                                  "unit": ["all units", "s1", "all units", "all units"],
                                  "log2fc": [-1.2, -1.5, -0.2, -0.1]}),
        "states": pd.DataFrame({"target": ["A", "A", "B", "B"], "state": ["S01", "S02", "S01", "S02"],
                                "state_name": ["S01 Gzmb", "S02 Ccr7", "S01 Gzmb", "S02 Ccr7"],
                                "mean_difference": [0.30, -0.05, 0.01, 0.02]}),
        "similar": pd.DataFrame({"target": ["A"], "other": ["B"], "correlation": [0.4]}),
        "guides": pd.DataFrame({"target": ["A"], "median_correlation": [0.6], "control_pairs_as_high": [0.02]}),
        "replicates": pd.DataFrame({"target": ["A", "A", "B"], "unit_a": ["s1", "s1", "s1"],
                                    "unit_b": ["s2", "s2", "s2"], "r": [0.5, 0.3, 0.1]}),
    }


def test_the_table_reads_each_run_and_says_which_columns_are_filled(screen):
    strength = pd.DataFrame({"target": ["A", "B"], "e_distance": [2.0, 0.5], "effect_line": [1.0, 0.9]})
    t, columns, sim = ov.overview_table(screen, _tables(), strength, axis=["S02"])
    row = t.set_index("target")
    assert set(row.index) == {"A", "B"}
    assert row.loc["A", "target_gene_change"] == -1.2 and row.loc["A", "target_gene_guide"] == "A_1"
    assert bool(row.loc["A", "past_the_line"]) and not bool(row.loc["B", "past_the_line"])
    assert row.loc["A", "units_median_r"] == 0.4 and row.loc["A", "unit_pairs"] == 2
    assert row.loc["A", "similar"] == "B 0.40"
    assert [c for c in t.columns if c.startswith("share:")] == ["share:S02", "share:S01"]  # the question's first
    assert columns["share:S02"]["the_users_question"] and columns["share:S02"]["state_name"] == "S02 Ccr7"
    assert columns["target_gene_change"]["state"] == "filled" and columns["genes_changed"]["state"] == "not run"
    assert columns["cells_per_guide_vs_control"]["state"] == "not run"
    assert sim.loc["A", "B"] == 0.4
    json.dumps(columns)  # columns.json is plain JSON


def test_the_target_gene_is_read_the_way_each_guide_is_expected_to_move_it():
    """CRISPRi or a knockout lowers its gene, CRISPRa raises it (B.efficacy's expected): each target's gene is
    read under the guide moving it furthest its own way, and furthest either way where no direction is given."""
    eff = pd.DataFrame({"guide": ["I_1", "I_2", "A_1", "A_2"], "target": ["I", "I", "Aa", "Aa"],
                        "unit": ["all units"] * 4, "log2fc": [-1.0, 0.3, 0.2, 3.9], "expected": [-1, -1, 1, 1]})
    assert ov._target_gene(eff) == {"I": (-1.0, "I_1"), "Aa": (3.9, "A_2")}
    plain = eff.drop(columns="expected")
    assert ov._target_gene(plain) == {"I": (-1.0, "I_1"), "Aa": (3.9, "A_2")}


def test_without_a_distance_its_columns_say_not_run(screen):
    t, columns, _ = ov.overview_table(screen, {}, None)
    assert columns["e_distance"]["state"] == "not run" and t["e_distance"].isna().all()
    assert len(t) == 2 and (t["cells"] > 0).all()


def test_the_figure_opens_with_the_design_counted_from_the_screen(screen):
    """Session 12's report had no design figure once the overview's figure became the report's Figure 1, and the
    figure it replaced miscounted (session 11: 0 control guides, every unit a sample): the design's stages count the
    control guides by their kind and the samples without the cells whose sample was not told."""
    t, _, _ = ov.overview_table(screen, {}, None)
    stages = dict(ov.design_stages(screen, DESIGN, t))
    assert stages["Library"][0] == "2 targets" and "1 control guide with cells" in stages["Library"]
    assert stages["Samples"][0].startswith("2 units") and "replicate" not in " ".join(stages["Samples"])
    assert stages["Readout"] == ["single-cell RNA-seq", "guide capture"]
    # development runs on GSE250558: the six files quality control emptied were drawn as units, with their cells
    import types
    one = types.SimpleNamespace(**{k: getattr(screen, k) for k in ("guides", "top_guide", "unit", "meta")},
                                label=np.where(np.asarray(screen.unit).astype(str) == str(screen.unit[0]),
                                               "failed QC", np.asarray(screen.label)))
    kept = dict(ov.design_stages(one, DESIGN, t))
    assert kept["Samples"][0] == "1 unit" and kept["Cells"][-1].endswith("after quality control")


def test_a_unit_written_as_the_cell_is_no_replicate_in_the_design_or_the_tests(screen):
    """D on GSE342764: a unit confirmed as "cell (no biological replicate: ...)" was read as a replicate, so the
    figure said "each a cell ... told apart by not separable" and the tests by replicate ran on technical captures.
    The unit is now one word (check_design), here "cell". The figure counts the units and says nothing on replicates
    (docs/adr/0041, step 2): that is the agent's sentence."""
    from test_cells_overview import node
    design = {**DESIGN, "units": {"independent_unit": node("cell"), "pooled_from": node("4 mice")}}
    t, _, _ = ov.overview_table(screen, {}, None)
    samples = dict(ov.design_stages(screen, design, t))["Samples"]
    assert samples[0].startswith("2 units") and not any("replicate" in s for s in samples)
    assert not any("told apart by" in s for s in samples)
    assert screen.independent_units() and not Screen(screen.dir, design).independent_units()


def test_the_agents_overview_reads_this_builds_latest_runs_of_what_it_is_not_given(tmp_path):
    """Code started the overview when the first look's runs had ended, and it failed on both builds of a screen; the
    agent now runs it, and each input it does not give is the latest finished run of its module on this build."""
    from pertura.analysis_service import AnalysisService, OVERVIEW
    svc = AnalysisService({"workspace": str(tmp_path / "ws")})
    runs = {}

    def run(rid, module, state):
        runs[rid] = {"run_id": rid, "job_id": rid, "what": f"module {module}", "state": state, "uses": []}
    st = {"runs": runs, "builds": {}, "order": []}
    try:
        with mock.patch("pertura.record.on_previous_screen", return_value=False):
            run("r1", "B.efficacy", "done")
            run("r2", "C.states_and_composition", "running")
            run("r3", "F.guides_agree", "failed")  # a failed one is left out
            assert svc._overview_inputs(st, None) == {"efficacy": "r1"}
            runs["r2"]["state"] = "done"
            run("r4", "C.genes", "done")
            run("r5", "C.genes with {\"targets\": [\"A\"]}", "done")  # a run on some targets is not read
            assert svc._overview_inputs(st, None) == {"efficacy": "r1", "states": "r2", "genes": "r4"}
            # nor one on every target with other choices: GSE267740's Figure 1 took one experiment's counts from a
            # run by condition
            run("r6", "C.genes with {\"by\": \"condition\"}", "done")
            assert svc._overview_inputs(st, None)["genes"] == "r4"
            run("ov1", OVERVIEW, "done")  # the overview before it keeps its distances
            assert svc._overview_inputs(st, {"genes": "mine"}) == {"efficacy": "r1", "states": "r2",
                                                                   "genes": "mine", "previous": "ov1"}
    finally:
        svc.close()


def test_the_module_writes_its_table_columns_and_figure(screen, tmp_path):
    from pertura import question_modules as qm
    paths = {}
    for k, t in _tables().items():
        folder = tmp_path / f"run-{k}"
        folder.mkdir()
        t.to_csv(folder / "result.tsv", sep="\t", index=False)
        paths[k] = str(folder / "result.tsv")
    out = tmp_path / "overview"
    r = qm.REGISTRY["A.screen_overview"].run(screen, DESIGN, str(out), axis=["S02"], **paths)
    for f in ("result.tsv", "targets.tsv", "columns.json", "figure.png"):
        assert (out / f).exists(), f
    columns = json.load(open(out / "columns.json", encoding="utf-8"))
    assert columns["target_gene_change"]["run"] == "run-efficacy"  # each column names the run it came from
    assert r.status == "measured" and "not given: B.abundance" in r.summary
    assert r.diagnostics["axis"] == ["S02"]
    # its legend, for the report's Figure 1: each panel drawn, lettered in order, with its n and the run it reads
    legend = r.diagnostics["legend"]
    assert [x["letter"] for x in legend] == list("abcde")[:len(legend)] and legend
    assert legend[0]["text"].startswith("the screen's design") and legend[0]["input"] is None
    assert all(any(ch.isdigit() for ch in x["text"]) for x in legend)
    runs = {x["input"]: x["run"] for x in legend}
    assert runs.get("states", "run-states") == "run-states" and runs.get("similar", "run-similar") == "run-similar"
    assert {x["input"] for x in legend} <= {None, "efficacy", "similar", "states"}


def test_the_overview_maps_the_cells_by_state_with_their_markers_and_by_condition(screen, tmp_path):
    # development runs of 2026-11-05: no report drew the cells by state or by condition, the first figure of the
    # papers their screens came from (the knowledge base's FG1, the states)
    from pertura import question_modules as qm
    folder = tmp_path / "run-states"
    folder.mkdir()
    _tables()["states"].to_csv(folder / "result.tsv", sep="\t", index=False)
    n = len(screen.barcodes)
    rng = np.random.default_rng(0)
    odd = np.arange(n) % 2 == 1
    states = pd.DataFrame({"barcode": screen.barcodes, "umap_1": rng.normal(size=n), "umap_2": rng.normal(size=n),
                           "state": np.where(odd, "S01", "S02"), "state_name": np.where(odd, "S01", "S02")})
    # cells the quality rule left out: no state and empty coordinates (GSE267740 on a15df12: the overview failed)
    states.loc[:4, ["umap_1", "umap_2"]] = np.nan
    states.loc[:4, ["state", "state_name"]] = ""
    states.to_csv(folder / "cell_states.tsv", sep="\t", index=False)
    pd.DataFrame({"state": ["S01", "S01", "S02", "S02"], "gene": ["Gzmb", "Ccr7", "Gzmb", "Ccr7"],
                  "fraction": [0.9, 0.1, 0.2, 0.8], "mean_expression": [2.0, 0.1, 0.3, 1.5]}).to_csv(
        folder / "marker_dots.tsv", sep="\t", index=False)
    screen.condition = np.where(np.arange(n) % 3 == 0, "stimulated", "resting")
    r = qm.REGISTRY["A.screen_overview"].run(screen, DESIGN, str(tmp_path / "overview"),
                                             states=str(folder / "result.tsv"))
    texts = [x["text"] for x in r.diagnostics["legend"]]
    assert any(t.startswith("the cells on the states run's embedding (UMAP), coloured by their 2 states")
               and "marker genes" in t for t in texts), texts
    assert any(t.startswith("the same cells coloured by condition, 2 conditions") for t in texts), texts
    assert {x["run"] for x in r.diagnostics["legend"] if x["input"] == "states"} == {"run-states"}
    # Figure 1's legend says the conditions its measures pool (the fresh review of 2026-11-13, #1: GSE267740's two
    # experiments, read apart in the results, were one column in Figure 1)
    from pertura import results_report
    st = {"runs": {"ov": {"run_id": "ov", "job_id": "ov", "state": "done", "what": "module A.screen_overview"}}}
    os.replace(tmp_path / "overview", tmp_path / "ov")
    legend = results_report.screen_figure(st, str(tmp_path), str(tmp_path / "report"))
    assert "Its measures pool the cells of the screen's 2 conditions (resting, stimulated)." in legend, legend


def test_the_targets_changes_of_each_others_genes_from_the_gene_test(tmp_path):
    # development runs on GSE250558: EGR1 and FOS acting on each other and on TCF7 were never read, in three runs;
    # the knowledge base's P2.6: regulators expressed in the cells hold a network among them
    import types
    from pertura import blocks
    run = tmp_path / "run-genes"
    (run / "blocks" / "outputs" / "c1").mkdir(parents=True)
    with open(run / "blocks" / blocks.LOG, "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"block": "genes", "call_id": "c1"}) + "\n")
    targets = ["EGR1", "FOS", "TCF7"]
    rows = [(t, g, -0.8 if (t, g) == ("EGR1", "TCF7") else 0.05, 1e-4 if (t, g) == ("EGR1", "TCF7") else 0.5)
            for t in targets for g in targets + ["GAPDH"]]
    pd.DataFrame(rows, columns=["target", "gene", "effect", "q"]).to_csv(
        run / "blocks" / "outputs" / "c1" / "table.tsv.gz", sep="\t", index=False)
    (run / "result.tsv").write_text("target\n", encoding="utf-8")
    s = types.SimpleNamespace(own_gene_of=lambda t: t if t in targets else None)
    net = ov._targets_on_targets(s, str(run / "result.tsv"), targets + ["RAF1"])
    assert net["rows"] == targets and net["cols"] == targets  # RAF1's own gene is not measured
    M = pd.DataFrame(net["M"], index=net["rows"], columns=net["cols"])
    assert M.loc["EGR1", "TCF7"] == -0.8 and net["marks"][0][2] == "***" and net["marks"][1][2] == ""
    assert ov._targets_on_targets(s, str(run / "result.tsv"), ["EGR1", "FOS", "RAF1"]) is None  # two measured


def test_the_shares_heatmap_draws_its_own_values_beside_the_targets_network(screen, tmp_path):
    # GSE342764 on 0adebaa: the shares panel, drawn last, read the network's matrix and failed the overview
    table, columns, sim = ov.overview_table(screen, _tables(), None, ["S02"])
    network = {"M": np.array([[0.0, -0.8, 0.1], [0.2, 0.0, 0.0], [0.0, 0.3, 0.0]]),
               "rows": ["EGR1", "FOS", "TCF7"], "cols": ["EGR1", "FOS", "TCF7"],
               "marks": [["", "***", ""], ["", "", ""], ["", "", ""]]}
    checks, legend = ov.draw(table, columns, str(tmp_path / "figure.png"), sim, None, _tables()["states"], None,
                             network)
    assert (tmp_path / "figure.png").exists()
    assert {"states", "genes"} <= {x["input"] for x in legend}


def test_the_report_draws_the_design_where_no_overview_ran(screen, tmp_path):
    """Session 13 never ran A.screen_overview, and its report had no Figure 1: the design is drawn from the screen
    object's files alone."""
    from pertura import results_report
    stages = dict(ov.design_stages_from_files(screen.dir, DESIGN))
    assert stages["Library"][0] == "2 targets" and "1 control guide with cells" in stages["Library"]
    legend = results_report.design_figure(DESIGN, screen.dir, str(tmp_path))
    assert legend.startswith("**Figure 1.** The screen's design") and (tmp_path / "figures" / "screen.png").exists()
