"""Each cell's unit is a column the agent writes into the cell tables, and the design names it (docs/adr/0041, step
4a): whatever tells the units apart (hashtags, a genotype demultiplexer, the deposit's cell metadata, a barcode's
lane), the build reads the column, the check sees that it exists and which units it makes, and the reading checks
count what the build made of them. The ways of telling units apart as a fixed list are research
(pertura_research.retired.unit_sources_from_a_fixed_list)."""
import os

import anndata as ad
import numpy as np
import pandas as pd
import pytest
import scipy.sparse as sp

from test_design_audit import make_deposit
from test_the_deposit_read_into_cell_tables_as_proposed import _propose

from pertura import cell_table, hashtags, reading_checks
from pertura import screen_object as so
from pertura.design_audit import checks


def node(v):
    return {"value": v, "status": "confirmed", "source": "test"}


def design(**units):
    return {"perturbation": {"target_map": node("feature_reference"),
                             "controls": node([{"kind": "non_targeting", "guides": ["NTC_1", "NTC_2"]}]),
                             "assignment": node({"rule": "deposit"})},
            "units": {"independent_unit": node("mouse"), **{k: node(v) for k, v in units.items()}}}


def barcodes(s):
    return list(ad.read_h5ad(s["matrix"]).obs_names.astype(str))


def test_a_column_written_into_the_tables_is_each_cells_unit(tmp_path):
    root, tables = tmp_path / "deposit", str(tmp_path / "tables")
    root.mkdir()
    make_deposit(str(root), n_cells=60)
    _propose(root, tables)
    s1, s2 = cell_table.samples(tables)
    b1 = barcodes(s1)
    mice = pd.Series(["M1" if i % 2 else "M2" for i in range(len(b1) - 1)] + [""], index=b1)  # one cell unplaced
    r = cell_table.annotate(tables, "mouse", {"S1": mice, "S2": ["M3"] * len(barcodes(s2))}, made_by="run:r1",
                            note="the deposit's cell metadata")
    assert r["ok"] and r["values"] == ["M1", "M2", "M3"] and r["samples"]["S1"]["without_value"] == 1, r
    # each value is a unit of its own, said with its cells, so a word for "not placed" would show as one (GSE190604)
    assert r["cells_per_value"]["M3"] == len(barcodes(s2)) and "a cell without a value has none" in r["read_as"]
    assert any(n.startswith("mouse: written into S1, S2 by run:r1") for n in cell_table.manifest(tables)["notes"])
    summary = so.build(str(root), design(unit_column="mouse"), str(tmp_path / "screen"), cell_tables=tables)
    cells = pd.read_csv(os.path.join(tmp_path, "screen", "cells.tsv.gz"), sep="\t")
    assert summary["unit_column"] == "mouse"
    assert set(cells["unit"]) == {"M1", "M2", "M3", "S1:unassigned"}
    assert [c["check"] for c in summary["reading_checks"]].count("cells without a unit") == 1
    without = so.build(str(root), design(), str(tmp_path / "samples"), cell_tables=tables)
    assert set(without["units"]) == {"S1", "S2"}  # no column: each sample is one unit


def test_a_column_that_names_no_cell_of_the_table_is_refused(tmp_path):
    root, tables = tmp_path / "deposit", str(tmp_path / "tables")
    root.mkdir()
    make_deposit(str(root), n_cells=40)
    _propose(root, tables)
    s1, _ = cell_table.samples(tables)
    stripped = pd.Series("M1", index=[b.rsplit("-", 1)[0] for b in barcodes(s1)])  # '-1' written another way
    r = cell_table.annotate(tables, "mouse", {"S1": stripped})
    assert not r["ok"] and "none of the values' barcodes" in r["problems"][0]
    assert not cell_table.annotate(tables, "mouse", {"S9": ["M1"]})["ok"]
    assert "values for" in cell_table.annotate(tables, "mouse", {"S1": ["M1"]})["problems"][0]


def test_the_build_refuses_units_told_apart_the_retired_ways(tmp_path):
    root = tmp_path / "deposit"
    root.mkdir()
    make_deposit(str(root), n_cells=30)
    for d in (design(unit_identification="hashtag", hashtags=["HTO_1"]), design(unit_identification="lanes")):
        with pytest.raises(ValueError, match="cell_table.annotate"):
            so.build(str(root), d, str(tmp_path / "screen"))
    d = design()
    d["units"]["independent_unit"] = node({"source": "separate_samples"})  # an operator's rule of its own
    with pytest.raises(ValueError, match="units.unit_column"):
        so.build(str(root), d, str(tmp_path / "screen"))


def test_the_check_reads_the_column_and_says_the_units_it_makes():
    measured = {"facts": [], "library": {"samples": ["S1", "S2"], "guides": [],
                                         "cell_table_columns": {"mouse": ["", "M1", "M2", "M3"]}}}

    def units_said(**units):
        proposed = {k: {"value": v, "status": "proposed", "source": "test"} for k, v in units.items()}
        p = {"schema": "pertura.design/v1", "units": {"independent_unit": {"value": "mouse", "status": "proposed",
                                                                           "source": "test"}, **proposed}}
        r = checks.check(p, measured)
        return [x for x in r["problems"] if x.startswith("units.")], [f["text"] for f in r["flags"]
                                                                       if f["key"] == "units.independent_unit"]
    problems, flags = units_said(unit_column="mouse")
    assert problems == [] and "the build will count 3 mice: M1, M2, M3, from the column 'mouse'" in flags
    assert "one per sample" in units_said()[1][0]
    none = units_said(independent_unit="none")[1][0]  # it said "the build will count 2 nones"
    assert none.startswith("the build will count 2 samples, with no biological replicate"), none
    assert "no column 'donor'" in units_said(unit_column="donor")[0][0]
    assert "units.n_units: 4 mice" in units_said(unit_column="mouse", n_units=4)[0][0]
    retired = units_said(unit_identification="hashtag", unit_column="mouse")[0]
    assert retired and "not part of the design any more" in retired[0]


def test_captures_told_apart_by_barcode_prefixes_carry_their_capture(tmp_path):
    from test_design_audit import write_triplet
    root, tables = tmp_path / "deposit", str(tmp_path / "tables")
    root.mkdir()
    bcs = [f"D1_AAAC{i:04d}-1" for i in range(40)] + [f"S1_CCCG{i:04d}-1" for i in range(40)]
    feats = [["G1", "G1", "Gene Expression"], ["G2", "G2", "Gene Expression"], ["g1", "g1", "CRISPR Guide Capture"]]
    write_triplet(str(root / "GSM001_pool_"), np.random.default_rng(0).poisson(3, (80, 3)), feats, bcs)
    for s, stem in (("D1", "AAAC"), ("S1", "CCCG")):  # each capture's calls, without the matrix's prefix
        rows = ["cell_barcode,num_features,feature_call,num_umis"] + [f"{stem}{i:04d}-1,1,g1,5" for i in range(40)]
        (root / f"{s}_protospacer_calls_per_cell.csv").write_text("\n".join(rows) + "\n")
    m = _propose(root, tables)
    (pool,) = cell_table.samples(tables)
    assert pool.get("calls") and ad.read_h5ad(pool["matrix"]).obs["capture"].value_counts().to_dict() == {"D1": 40,
                                                                                                         "S1": 40}
    assert any(n.startswith("capture: written into pool by pertura:discover") for n in m["notes"]), m["notes"]


def test_hashtags_called_into_the_tables_are_a_unit_column(tmp_path):
    tables = str(tmp_path / "tables")
    rng = np.random.default_rng(0)
    H = np.zeros((30, 2), dtype=int)
    H[:12, 0], H[12:24, 1], H[24:, :] = 40, 40, 30  # the last six carry both hashtags
    X = sp.csr_matrix(np.hstack([rng.poisson(3, (30, 3)), H]))
    features = pd.DataFrame({"gene_ids": ["G1", "G2", "G3", "HTO_1", "HTO_2"], "name": ["G1", "G2", "G3", "HTO_1",
                                                                                       "HTO_2"],
                             "feature_types": ["Gene Expression"] * 3 + ["Antibody Capture"] * 2})
    for s in ("S1", "S2"):
        assert cell_table.write(tables, s, X, features, [f"C{i}-1" for i in range(30)])["ok"]
    top = {"method": "top_ratio", "min_counts": 5, "ratio": 3}
    assert not hashtags.write_into_cell_tables(tables, ["HTO_1", "HTO_2"], None, value=top)["ok"]  # said, not assumed
    r = hashtags.write_into_cell_tables(tables, ["HTO_1", "HTO_2"], hashtags.PER_SAMPLE, value=top, made_by="run:r2")
    assert r["ok"] and r["calls"]["S1"] == {"one": 24, "several": 6, "none": 0}, r
    assert r["values"] == ["S1:HTO_1", "S1:HTO_2", "S2:HTO_1", "S2:HTO_2"]
    one = hashtags.write_into_cell_tables(tables, ["HTO_1", "HTO_2"], hashtags.ONE_UNIT, value=top)
    assert one["values"] == ["HTO_1", "HTO_2"] and one["samples"]["S2"]["without_value"] == 6


def test_conditions_holding_one_unit_are_a_reading_check():
    conditions = node([{"name": "treatment", "levels": ["ctrl", "stim"], "by_unit": {}}])
    d = {"experiment": {"conditions": conditions}, "perturbation": {"target_map": node({"A": ["A_1"]})}}

    def cells(units):
        return pd.DataFrame({"barcode": [f"S1:c{i}" for i in range(len(units))], "sample": "S1", "unit": units,
                             "label": "A", "condition": ["ctrl", "stim"] * (len(units) // 2), "umis": 1000})
    said = [c for c in reading_checks.check(cells(["M1", "M2"]), d, {}) if c["check"] == "conditions holding one unit"]
    assert said and said[0]["text"].startswith("Conditions holding one unit: 2 of 2 (ctrl: M1, stim: M2)")
    paired = reading_checks.check(cells(["M1", "M1", "M2", "M2"]), d, {})  # each mouse in both conditions
    assert "conditions holding one unit" not in [c["check"] for c in paired]
