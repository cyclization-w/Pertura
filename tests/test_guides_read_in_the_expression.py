"""Guides read from the expression library, typed as genes (development run on GSE267740, 2026-11-05): one
experiment's guides were rows of its gene expression matrix (gRNA_TLR4_1_gene), the measurement saw them, and the
design never named them, so the build left that experiment's 61,645 cells without guides. The measurement names the
rows that behave like guides, with their library matches by name; the agent reads them as guides in the samples'
cell tables (cell_table.retype, docs/adr/0041 step 1b-3), and the build reads them there. The design key that did this
before is retired (tests_research/test_design_keys_for_guide_rows_and_samples_left_out.py)."""
import gzip

import numpy as np
import pandas as pd
import scipy.io
import scipy.sparse as sp

from pertura import cell_table
from pertura import screen_object as so
from pertura.design_audit import inventory as inv


def node(v):
    return {"value": v, "status": "proposed", "source": "test"}


LIBRARY = ["gRNA_Tlr4_1_capture", "gRNA_Il17a_1_capture", "gRNA_control_1_capture", "gRNA_control_2_capture"]


def test_names_match_the_library_without_case_punctuation_or_the_last_word():
    m = inv.library_matches(["gRNA_TLR4_1_gene", "gRNA_IL-17A_1_gene", "gRNA_control_2_gene", "Tlr4"], LIBRARY)
    assert m == {"gRNA_TLR4_1_gene": "gRNA_Tlr4_1_capture", "gRNA_IL-17A_1_gene": "gRNA_Il17a_1_capture",
                 "gRNA_control_2_gene": "gRNA_control_2_capture"}  # a gene's name matches no guide
    # two library guides with one key: neither is matched
    assert inv.library_matches(["sgCtrl_x"], ["sgCtrl_A", "sgCtrl_B"]) == {}


def test_the_measurement_gives_the_matches_of_guide_like_features():
    rng = np.random.default_rng(2)
    rows = ["gRNA_TLR4_1_gene", "gRNA_IL-17A_1_gene", "gRNA_control_1_gene"]
    ids = [f"ENSMUSG{n:011d}" for n in range(300)] + rows
    X = rng.poisson(0.5, (400, 303)).astype(float)
    X[:, 300:] = 0
    for i in range(400):
        X[i, 300 + i % 3] = 5
    f, q = inv.unannotated_features(ids, ids, sp.csr_matrix(X), set(LIBRARY))
    v = f[0]["value"]
    assert v["guide_like"] and v["library_matches"]["gRNA_TLR4_1_gene"] == "gRNA_Tlr4_1_capture"
    assert any("match the library's guides by name" in x["question"] for x in q)


def test_the_build_reads_them_where_the_agent_retyped_them(tmp_path):
    d = tmp_path / "dep"
    d.mkdir()
    rng = np.random.default_rng(0)
    genes = [f"G{i}" for i in range(50)]
    rows = ["gRNA_A_1_gene", "gRNA_B_1_gene", "gRNA_NT_1_gene"]
    n = 30
    for name, capture in (("s1", True), ("s2", False)):
        Gx = np.zeros((n, 3), int)  # the guides as read in the expression
        Gx[:10, 0], Gx[10:20, 1], Gx[20:25, 2] = 2, 3, 1
        parts, feats = [rng.poisson(3, (n, 50)), Gx], [f"{g}\t{g}\tGene Expression" for g in genes + rows]
        if capture:  # s1 also has a guide capture, its readout: A in every cell
            parts.append(np.full((n, 1), 6))
            feats.append("A_1\tA_1\tCRISPR Guide Capture")
        with gzip.open(d / f"{name}_matrix.mtx.gz", "wb") as fh:
            scipy.io.mmwrite(fh, sp.coo_matrix(np.hstack(parts).T))
        (d / f"{name}_features.tsv").write_text("\n".join(feats) + "\n")
        (d / f"{name}_barcodes.tsv").write_text("\n".join(f"{name}C{i:03d}-1" for i in range(n)) + "\n")
    from pertura.design_audit.readers import discover
    tables = str(tmp_path / "tables")
    cell_table.propose(discover(str(d)), tables)
    # s2 has no guide capture: its guide rows are its guides, by the library's names; in s1 they leave the genes
    assert cell_table.retype(tables, "s2", dict(zip(rows, ["A_1", "B_1", "NT_1"])))["retyped"] == 3
    assert cell_table.retype(tables, "s1", rows, "Guide read in the expression")["ok"]
    assert not cell_table.retype(tables, "s1", ["no_such_row"])["ok"]
    design = {"perturbation": {"target_map": node({"A": ["A_1"], "B": ["B_1"]}),
                               "controls": node([{"kind": "non_targeting", "guides": ["NT_1"]}]),
                               "assignment": node({"rule": "dominance", "min_umis": 1, "ratio": 3})},
              "units": {"independent_unit": node("mouse")}}
    out = tmp_path / "screen"
    so.build(str(d), design, str(out), cell_tables=tables)
    cells = pd.read_csv(out / "cells.tsv.gz", sep="\t")
    by_sample = {s: c["label"].value_counts().to_dict() for s, c in cells.groupby("sample")}
    assert by_sample["s2"] == {"A": 10, "B": 10, "control": 5, "none": 5}
    assert by_sample["s1"] == {"A": n}  # its guide capture, not the expression's reads
    a = so.load(str(out), cache=False)
    assert not set(rows) & set(a.var_names) and len(a.var_names) == 50
