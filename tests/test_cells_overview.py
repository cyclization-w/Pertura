"""The cells overview the build writes (pertura.cells_overview): the measures, the round cuts per sample, a rule's
counts, the deposited object's ranges, the figure, and (with scanpy) the clusters, where low-quality cells form their
own cluster."""
import gzip
import json
import os

import numpy as np
import pandas as pd
import pytest
import scipy.io
import scipy.sparse as sp

from pertura import cells_overview as co
from pertura import screen_object as so


def node(v):
    return {"value": v, "status": "confirmed"}


DESIGN = {"perturbation": {"target_map": node({"A": ["A_1", "A_2"], "B": ["B_1"]}),
                           "controls": node([{"kind": "non_targeting", "guides": ["NT_1"]}]),
                           "assignment": node({"rule": "dominance", "min_umis": 3, "ratio": 3})},
          "units": {"independent_unit": node("mouse")}}


def _deposit(tmp_path, n=120, sick=30):
    """Two samples; in each, its last `sick` cells carry most of their counts on mitochondrial genes and few others,
    as dying cells do, and a gene of their own."""
    d = tmp_path / "dep"
    d.mkdir()
    rng = np.random.default_rng(0)
    genes = [f"G{i}" for i in range(60)] + ["mt-Co1", "mt-Nd1", "mt-Atp6"]
    guides = ["A_1", "A_2", "B_1", "NT_1"]
    for s in ("s1", "s2"):
        X = rng.poisson(4, (n, len(genes)))
        X[:, -3:] = rng.poisson(1, (n, 3))
        X[n - sick:, :60] = rng.poisson(0.4, (sick, 60))
        X[n - sick:, 59] = rng.poisson(6, sick)  # the dying cells' own gene
        X[n - sick:, -3:] = rng.poisson(40, (sick, 3))
        Gm = np.zeros((n, 4), int)
        third = n // 3
        Gm[:third, 0] = 5
        Gm[third:2 * third, 2] = 6
        Gm[2 * third:, 3] = 5
        M = sp.coo_matrix(np.hstack([X, Gm]).T)
        with gzip.open(d / f"{s}_matrix.mtx.gz", "wb") as fh:
            scipy.io.mmwrite(fh, M)
        feats = [f"{g}\t{g}\tGene Expression" for g in genes] + [f"{g}\t{g}\tCRISPR Guide Capture" for g in guides]
        (d / f"{s}_features.tsv").write_text("\n".join(feats) + "\n")
        letter = "C" if s == "s1" else "D"  # each sample its own cells (the same barcodes would be one set of cells)
        (d / f"{s}_barcodes.tsv").write_text("\n".join(f"{letter}{i:03d}-1" for i in range(n)) + "\n")
    return d


@pytest.fixture
def built(tmp_path):
    out = tmp_path / "screen"
    summary = so.build(str(_deposit(tmp_path)), DESIGN, str(out))
    return out, summary


def test_the_build_writes_the_overview_before_its_summary(built):
    out, summary = built
    folder = out / co.FOLDER
    assert summary["cells_overview"] == co.FOLDER
    for f in ("measures.tsv.gz", "clusters.tsv", "cluster_summary.tsv", "cuts.tsv", "deposit_object_ranges.json",
              "note.json", "figure.png", "figure.pdf"):
        assert (folder / f).exists(), f
        assert (folder / f).stat().st_mtime <= (out / "screen.json").stat().st_mtime
    m = pd.read_csv(folder / "measures.tsv.gz", sep="\t")
    sick = m["barcode"].str.extract(r"[CD](\d+)-1")[0].astype(int) >= 90
    assert m.loc[sick, "pct_mito"].min() > 0.3 > m.loc[~sick, "pct_mito"].max()
    cuts = pd.read_csv(folder / "cuts.tsv", sep="\t")
    at10 = cuts[(cuts["measure"] == "pct_mito") & (cuts["cut"] == 0.1)].set_index("sample")
    assert list(at10.index) == ["s1", "s2"] and (at10["cells"] == 120).all() and (at10["cells_left"] == 90).all()
    reply = co.reply(str(out))
    assert reply["cuts"] and reply["figure"].endswith("figure.png")
    assert reply["deposit_object_ranges"] is None


def test_a_clustering_that_fails_leaves_the_build(tmp_path, monkeypatch):
    """The overview decides nothing: when its clustering fails, the build stands with the measures, cuts and
    histograms, and the note says why there are no clusters."""
    pytest.importorskip("scanpy")
    from pertura import states

    def broken(*a, **k):
        raise RuntimeError("no community detection here")
    monkeypatch.setattr(states, "leiden_states", broken)
    out = tmp_path / "screen"
    so.build(str(_deposit(tmp_path)), DESIGN, str(out))
    reply = co.reply(str(out))
    assert reply["note"].startswith("no clusters: RuntimeError: no community detection here")
    assert reply["clusters"] == [] and reply["cuts"] and os.path.exists(reply["figure"])


def test_a_rules_counts_per_sample(built):
    out, _ = built
    t = co.rule_counts(str(out), {"filters": [{"metric": "pct_mito", "max": 0.1}]}).set_index("sample")
    assert (t["cells_with_a_unit"] == 120).all() and (t["kept"] == 90).all()
    assert (t["left out: pct_mito above 0.1"] == 30).all()
    path = co.draw(str(out), {"filters": [{"metric": "pct_mito", "max": 0.1}]}, name="figure_proposed")
    assert os.path.exists(path)


def test_a_processed_objects_ranges(tmp_path):
    import anndata as ad
    a = ad.AnnData(np.zeros((3, 2), np.float32),
                   obs=pd.DataFrame({"percent.mt": [0.5, 2.0, 9.77], "nFeature_RNA": [362, 4000, 9570],
                                     "group": ["x", "y", "z"]}, index=["a", "b", "c"]))
    path = tmp_path / "object.h5ad"
    a.write_h5ad(path)
    r = co.object_ranges(str(path))
    assert r["cells"] == 3 and set(r["columns"]) == {"percent.mt", "nFeature_RNA"}
    assert r["columns"]["percent.mt"] == {"min": 0.5, "median": 2.0, "max": 9.77}


def test_low_quality_cells_form_their_own_cluster(built):
    pytest.importorskip("scanpy")
    out, _ = built
    summary = pd.read_csv(out / co.FOLDER / "cluster_summary.tsv", sep="\t", keep_default_na=False)
    assert len(summary) >= 2
    worst = summary.sort_values("share_pct_mito_over_10").iloc[-1]
    assert worst["share_pct_mito_over_10"] > 0.9 and summary["share_pct_mito_over_10"].min() < 0.1
    assert not any(g.startswith("mt-") for m in summary["markers"] for g in str(m).split())
    clusters = pd.read_csv(out / co.FOLDER / "clusters.tsv", sep="\t")
    rule = {"filters": [{"metric": "cluster", "table": str(out / co.FOLDER / "clusters.tsv"),
                         "drop": [worst["cluster"]]}]}
    t = co.rule_counts(str(out), rule)
    assert int(t["kept"].sum()) == len(clusters) - int(worst["cells"])


def test_the_design_form_asks_the_rule_only_after_the_build(built, tmp_path):
    """After the build, the cells' rule is counted on the overview, and confirming it does not build the screen object
    again (before the build the design's form never holds it: test_design_service)."""
    from pertura.design_service import DesignService, build_sha
    out, _ = built
    state = tmp_path / "state"
    state.mkdir()
    os.replace(out, state / "screen_object")
    svc = DesignService.__new__(DesignService)
    svc.dir, svc.cfg = str(state), {}
    assert svc._built_overview() == f"{state}/screen_object"
    words = svc._rule_counted(svc._built_overview(), {"filters": [{"metric": "pct_mito", "max": 0.1}]})
    assert "keeps 180 of the 240 cells with a unit (75%; by sample 75% to 75%)" in words
    assert "pct_mito above 0.1: 60" in words
    assert (state / "screen_object" / co.FOLDER / "figure_proposed.png").exists()
    import yaml
    design = {"perturbation": {"modality": {"value": "crispr_ko", "status": "confirmed"}},
              "confirmations": [{"key": "perturbation.modality", "value": "crispr_ko"}]}
    path = tmp_path / "design.yaml"
    path.write_text(yaml.safe_dump(design))
    before = build_sha(str(path))
    design["data"] = {"qc": {"value": {"filters": [{"metric": "pct_mito", "max": 0.1}]}, "status": "confirmed"}}
    design["confirmations"].append({"key": "data.qc", "value": design["data"]["qc"]["value"]})
    path.write_text(yaml.safe_dump(design))
    assert build_sha(str(path)) == before
    design["perturbation"]["modality"]["value"] = "crispri"
    path.write_text(yaml.safe_dump(design))
    assert build_sha(str(path)) != before


def test_the_overview_and_the_design_are_given_once(tmp_path):
    """GSE267740 on 667f20a: the cells overview (23,600 characters and a figure) came back in every reply while the
    cells waited, and the design (7,800 characters) in every status; each is given once, then pointed to."""
    from test_design_service import answer, make_deposit, proposal
    from pertura import design_gate
    from pertura.analysis_service import AnalysisService
    deposit = tmp_path / "deposit"
    deposit.mkdir()
    make_deposit(str(deposit))
    svc = AnalysisService({"workspace": str(tmp_path / "ws")})
    try:
        for tool, args in (("open_project", {"data_folder": str(deposit)}), ("measure", {}),
                           ("check_design", {"proposal": proposal()})):
            ok, out = svc.call(tool, args)
            assert ok, out
        assert svc.call("confirm_design", {}, elicit=answer())[0]
        so.build(str(deposit), design_gate.load(svc.design.design_file)[0], svc._screen_dir())
        first, again = svc._after_build(), svc._after_build()
        assert isinstance(first["cells_overview"], dict)
        assert isinstance(again["cells_overview"], str) and "earlier reply" in again["cells_overview"]
        assert again["waits_on_user"] == first["waits_on_user"]
        one, two = (svc.call("pertura_status", {})[1] for _ in range(2))
        assert "unchanged" not in (one.get("design") or {})
        assert two["design"] == {"unchanged": "as an earlier reply gave it; get_design gives it in full"}
        assert "unchanged" not in svc.call("pertura_status", {"detail": True})[1]["design"]
    finally:
        svc.close()


def test_modules_wait_for_the_cells_rule_and_the_notebook_does_not(tmp_path):
    """After the build every module waits until the user confirms the cells' rule; the reply says what to do."""
    from test_design_service import answer, confirm_the_cells, make_deposit, proposal
    from pertura import design_gate
    from pertura.analysis_service import AnalysisService
    deposit = tmp_path / "deposit"
    deposit.mkdir()
    make_deposit(str(deposit))
    svc = AnalysisService({"workspace": str(tmp_path / "ws")})
    try:
        for tool, args in (("open_project", {"data_folder": str(deposit)}), ("measure", {}),
                           ("check_design", {"proposal": proposal()})):
            ok, out = svc.call(tool, args)
            assert ok, out
        assert svc.call("confirm_design", {}, elicit=answer())[0]
        so.build(str(deposit), design_gate.load(svc.design.design_file)[0], svc._screen_dir())
        ok, out = svc.call("run_module", {"module": "B.abundance"})
        assert not ok and "the cells wait on the user (data.qc is not confirmed)" in out["error"]
        assert "check_design" in out["error"] and "notebook" in out["error"]
        confirm_the_cells(svc, {"filters": [{"metric": "n_genes", "min": 1}]})
        assert svc._cells_wait() is None
        # a rebuild numbers the clusters anew: the rule confirmed before it is asked again, not waited for
        import time
        time.sleep(1.1)  # the build's time is kept to the second
        so.build(str(deposit), design_gate.load(svc.design.design_file)[0], svc._screen_dir())
        assert svc._cells_wait() == "data.qc was confirmed before this build"
        form = answer()
        assert svc.call("confirm_design", {}, elicit=form)[0]
        assert any(message.startswith("Pertura: is this quality rule for the cells right?") for message, _ in form.calls)
        assert svc._cells_wait() is None
    finally:
        svc.close()
