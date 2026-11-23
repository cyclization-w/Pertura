"""Samples holding one capture's cells (development runs on GSE250558): each capture was deposited as two libraries
of the same cells (21,676 barcodes in common of 21,695 and 21,804), the user said to read the pooled one, and the
design had no place to say it, so the screen object held 12 samples and the results counted 12 replicates. The
measurement finds samples holding the same cells and asks which to read; the agent takes the other out of the reading
(cell_table.remove, docs/adr/0041 step 1b-3), and the build reads the tables left. The design key that did this
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


def test_samples_holding_one_captures_cells_are_found_and_others_are_not():
    one = {f"C{i:05d}-1" for i in range(1000)}
    other = {f"C{i:05d}-1" for i in range(990, 1990)}  # ten barcodes in common, as different captures share a few
    f, q = inv.same_cells({"h6": one, "h6_untargeted": set(list(one)[:990]) | {"X-1"}, "h12": other})
    assert f[0]["key"] == "data.same_cells_in_several_samples"
    assert [p["samples"] for p in f[0]["value"]] == [["h6", "h6_untargeted"]]
    assert q[0]["key"] == "data.same_cells_in_several_samples" and "990 barcodes in common" in q[0]["question"]
    assert "cell_table.remove" in q[0]["question"]
    assert inv.same_cells({"a": one, "b": other}) == ([], [])


def test_the_build_leaves_out_what_the_agent_took_out_of_the_reading(tmp_path):
    d = tmp_path / "dep"
    d.mkdir()
    rng = np.random.default_rng(0)
    genes = [f"G{i}" for i in range(50)]
    n = 30
    for name in ("s1", "s1_untargeted", "s2"):
        G = np.zeros((n, 2), int)
        G[:15, 0], G[15:, 1] = 4, 4
        feats = [f"{g}\t{g}\tGene Expression" for g in genes] + ["A_1\tA_1\tCRISPR Guide Capture",
                                                                 "NT_1\tNT_1\tCRISPR Guide Capture"]
        with gzip.open(d / f"{name}_matrix.mtx.gz", "wb") as fh:
            scipy.io.mmwrite(fh, sp.coo_matrix(np.hstack([rng.poisson(3, (n, 50)), G]).T))
        (d / f"{name}_features.tsv").write_text("\n".join(feats) + "\n")
        capture = name.split("_")[0]  # s1's two files hold the same cells, one called differently (GSE250558)
        bcs = [f"{capture}C{i:03d}-1" for i in range(n)]
        if name.endswith("untargeted"):
            bcs[-1] = f"{capture}X999-1"
        (d / f"{name}_barcodes.tsv").write_text("\n".join(bcs) + "\n")
    from pertura.design_audit.readers import discover
    tables = str(tmp_path / "tables")
    cell_table.propose(discover(str(d)), tables)
    assert cell_table.remove(tables, "s1_untargeted", "s1's cells, read twice")["ok"]
    design = {"perturbation": {"target_map": node({"A": ["A_1"]}),
                               "controls": node([{"kind": "non_targeting", "guides": ["NT_1"]}]),
                               "assignment": node({"rule": "dominance", "min_umis": 1, "ratio": 3})},
              "units": {"independent_unit": node("mouse")}}
    out = tmp_path / "screen"
    so.build(str(d), design, str(out), cell_tables=tables)
    cells = pd.read_csv(out / "cells.tsv.gz", sep="\t")
    assert sorted(cells["sample"].unique()) == ["s1", "s2"] and len(cells) == 2 * n
