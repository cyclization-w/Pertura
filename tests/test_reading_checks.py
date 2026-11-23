"""The reading checks (docs/adr/0041, step 1a): what a screen object read, against facts any correctly read screen
satisfies, whoever read it. Each test is a misread a development run made, as the cells it left."""
import numpy as np
import pandas as pd

from pertura import reading_checks


def node(v):
    return {"value": v, "status": "confirmed", "source": "test"}


DESIGN = {"perturbation": {"target_map": node({"A": ["A_1"], "B": ["B_1"], "C": ["C_1"]}),
                           "controls": node([{"kind": "non_targeting", "guides": ["NT_1"]}])},
          "experiment": {"conditions": node({"factor": "time", "levels": ["6h", "12h"],
                                             "by_sample": {"s1": "6h", "s2": "12h"}})}}
TABLE = {"A_1": ("A", "targeting"), "B_1": ("B", "targeting"), "C_1": ("C", "targeting"),
         "NT_1": ("NT_1", "non_targeting")}


def cells(samples, labels, umis=2000, barcodes=None, condition=None):
    rows = []
    for s in samples:
        for i, lab in enumerate(labels):
            bc = (barcodes or {}).get(s, s)
            rows.append({"barcode": f"{s}:{bc}C{i:05d}-1", "sample": s, "unit": s, "label": lab,
                         "condition": (condition or {}).get(s, {"s1": "6h", "s2": "12h"}.get(s, "6h")), "umis": umis})
    return pd.DataFrame(rows)


def kinds(found):
    return {c["check"] for c in found}


def test_a_screen_read_right_passes_every_check():
    two_each = {"s1": "6h", "s2": "12h", "s3": "6h", "s4": "12h"}  # two units in each condition
    found = reading_checks.check(cells(["s1", "s2", "s3", "s4"], ["A", "B", "C", "control", "none"],
                                       condition=two_each), DESIGN, TABLE)
    assert found == []


def test_targets_without_cells_gse263761():  # the deposit's calls made nearly every cell multi-guide
    found = reading_checks.check(cells(["s1", "s2"], ["A", "multi", "multi", "control"]), DESIGN, TABLE)
    c = next(c for c in found if c["check"] == "library targets without cells")
    assert c["targets"] == ["B", "C"] and c["text"].startswith("Library targets without cells: 2 of 3 (B, C)")


def test_samples_mostly_of_several_targets_and_a_condition_without_one_gse263761():
    # the deposit's calls left the 24-hour captures with a few cells of one target, while every target kept cells in
    # the other captures, so "library targets without cells" said nothing
    c = pd.concat([cells(["s1"], ["A", "B", "C", "control", "multi"]),
                   cells(["s2"], ["multi"] * 39 + ["A"], condition={"s2": "12h"})])
    found = reading_checks.check(c, DESIGN, TABLE)
    assert "library targets without cells" not in kinds(found)
    most = next(x for x in found if x["check"] == "samples whose cells mostly carry several targets")
    assert most["samples"] == ["s2"] and most["text"].startswith(
        "Samples whose cells mostly carry guides of several targets: 1 of 2 (s2, 98% of 40 cells)")
    assert "rule dominance" in most["text"]
    empty = next(x for x in found if x["check"] == "conditions with almost no cell of one target")
    assert empty["conditions"] == ["12h"] and "12h, 1 of 40 cells" in empty["text"]


def test_a_sample_whose_cells_carry_no_guide_ntn_1():  # guides as rows of its expression matrix, never read
    c = pd.concat([cells(["s1"], ["A", "B", "C", "control"]), cells(["s2"], ["none"] * 4)])
    found = reading_checks.check(c, DESIGN, TABLE)
    hit = next(x for x in found if x["check"] == "samples whose cells carry no guide")
    assert hit["samples"] == ["s2"] and "or guides the reading missed" in hit["text"]


def test_two_samples_holding_the_same_cells_gse250558():  # two libraries of one capture
    one = cells(["s1"], ["A", "B", "C", "control"] * 30)
    twin = one.assign(sample="s1_untargeted", unit="s1_untargeted",
                      barcode=one["barcode"].str.replace("s1:", "s1_untargeted:", regex=False))
    other = cells(["s2"], ["A", "B", "C", "control"] * 30, barcodes={"s2": "s2x"})
    found = reading_checks.check(pd.concat([one, twin, other]), DESIGN, TABLE)
    hit = next(x for x in found if x["check"] == "samples holding the same cells")
    assert hit["pairs"] == [["s1", "s1_untargeted"]]


def test_a_matrix_of_every_droplet_gse335887():  # the raw matrix beside the filtered one
    raw = cells(["s1"], ["none"] * 60_000, umis=3)
    raw.loc[:999, "label"] = "A"
    raw.loc[:999, "umis"] = 5000
    found = reading_checks.check(pd.concat([raw, cells(["s2"], ["A", "B", "C", "control"])]), DESIGN, TABLE)
    assert "matrices of every droplet" in kinds(found)


def test_labels_outside_the_design_gse203592():  # the call file's zMulti and zNone read as targets
    found = reading_checks.check(cells(["s1", "s2"], ["A", "B", "C", "control", "zMulti-1", "A+B"]), DESIGN, TABLE)
    hit = next(x for x in found if x["check"] == "labels outside the design")
    assert hit["labels"] == ["zMulti-1"]  # a set of the design's targets is known


def test_cells_without_a_unit_or_a_condition():
    c = cells(["s1", "s2"], ["A", "B", "C", "control"])
    c.loc[0, "unit"] = "s1:unassigned"
    c.loc[1, "condition"] = ""
    assert {"cells without a unit", "cells without a condition"} <= kinds(reading_checks.check(c, DESIGN, TABLE))


def test_the_build_writes_them_and_the_form_after_the_build_shows_them(tmp_path):
    import gzip

    import scipy.io
    import scipy.sparse as sp

    from pertura import screen_object as so
    d = tmp_path / "dep"
    d.mkdir()
    rng = np.random.default_rng(0)
    genes = [f"G{i}" for i in range(50)]
    n = 30
    for name, guided in (("s1", True), ("s2", False)):
        G = np.zeros((n, 2), int)
        if guided:
            G[:15, 0], G[15:, 1] = 4, 4
        feats = [f"{g}\t{g}\tGene Expression" for g in genes] + ["A_1\tA_1\tCRISPR Guide Capture",
                                                                 "NT_1\tNT_1\tCRISPR Guide Capture"]
        with gzip.open(d / f"{name}_matrix.mtx.gz", "wb") as fh:
            scipy.io.mmwrite(fh, sp.coo_matrix(np.hstack([rng.poisson(3, (n, 50)), G]).T))
        (d / f"{name}_features.tsv").write_text("\n".join(feats) + "\n")
        (d / f"{name}_barcodes.tsv").write_text("\n".join(f"{name}C{i:03d}-1" for i in range(n)) + "\n")
    design = {"perturbation": {"target_map": node({"A": ["A_1"], "B": ["B_1"]}),
                               "controls": node([{"kind": "non_targeting", "guides": ["NT_1"]}]),
                               "assignment": node({"rule": "dominance", "min_umis": 1, "ratio": 3})},
              "units": {"independent_unit": node("mouse")}}
    summary = so.build(str(d), design, str(tmp_path / "screen"))
    found = {c["check"]: c for c in summary["reading_checks"]}
    assert found["samples whose cells carry no guide"]["samples"] == ["s2"]
    assert found["library targets without cells"]["targets"] == ["B"]  # B_1 is in no matrix


def test_the_form_after_the_build_shows_them(tmp_path):
    import json
    import os

    from test_design_service import answer, make_deposit, proposal

    from pertura import design_gate
    from pertura import screen_object as so
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
        path = os.path.join(svc._screen_dir(), "screen.json")
        with open(path, encoding="utf-8") as fh:
            sm = json.load(fh)
        sm["reading_checks"] = [{"check": "samples whose cells carry no guide", "samples": ["s2"],
                                 "text": "Samples whose cells carry no guide: 1 (s2, 30 cells)"}]
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(sm, fh)
        ok, out = svc.call("check_design", {"proposal": proposal()})
        assert any(f["key"] == "data.qc" and f.get("on_the_form")
                   and f["text"] == "Read from the deposit: Samples whose cells carry no guide: 1 (s2, 30 cells)"
                   for f in out["flags"]), out["flags"]
    finally:
        svc.close()
