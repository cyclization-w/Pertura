"""Every deposit is read into cell tables before it is measured (docs/adr/0041, step 1b): for a deposit Pertura's
readers know, the tables are written at once from their reading, a proposal the agent checks and rewrites where it is
wrong; what the readers decided (a file not read, calls outside the matrix) stands in the manifest's notes, never
silently."""
import os

import pandas as pd

from test_design_audit import make_deposit

from pertura import cell_table
from pertura import screen_object as so
from pertura.design_audit.readers import declared_types, discover, read_feature_reference


def _propose(root, folder):
    dep = discover(str(root))
    ref = read_feature_reference(dep["feature_references"][0]) if dep["feature_references"] else None
    return cell_table.propose(dep, str(folder), declared_types(ref))


def test_the_readers_reading_is_written_as_tables_with_their_decisions_as_notes(tmp_path):
    root = tmp_path / "deposit"
    root.mkdir()
    make_deposit(str(root), n_cells=60)
    calls = root / "GSM011_S1_protospacer_calls_per_cell.csv.gz"
    t = pd.read_csv(calls)
    pd.concat([t, t.head(1).assign(cell_barcode="TTTT00000-1")]).to_csv(calls, index=False)  # a cell outside
    m = _propose(root, tmp_path / "tables")
    assert sorted(m["samples"]) == ["S1", "S2"] and m["proposed_by"] == "pertura:discover"
    assert all(e["made_by"] == "pertura:10x" and e["calls"] for e in m["samples"].values())
    assert any(n.startswith("S1: 1 calls name cells its matrix does not hold") for n in m["notes"]), m["notes"]
    assert _propose(root, tmp_path / "tables") is None  # tables written once; the agent's rewrites stay


def test_a_raw_matrix_beside_its_filtered_one_is_a_note_not_a_sample(tmp_path):
    root = tmp_path / "deposit"
    root.mkdir()
    for n in ("GSE1_img_filtered_feature_bc_matrix.h5", "GSE1_img_raw_feature_bc_matrix.h5"):
        (root / n).write_bytes(b"")
    dep = discover(str(root))
    notes = [f"{d['matrix']}: not read" for d in dep["duplicates"]]
    assert notes == ["GSE1_img_raw_feature_bc_matrix.h5: not read"]  # propose writes these notes as they are


def test_the_screen_object_from_the_proposed_tables_is_the_screen_object_read_directly(tmp_path):
    root = tmp_path / "deposit"
    root.mkdir()
    make_deposit(str(root), n_cells=80)
    _propose(root, tmp_path / "tables")
    design = {"perturbation": {"target_map": {"value": "feature_reference", "status": "confirmed", "source": "t"},
                               "controls": {"value": [{"kind": "non_targeting", "guides": ["NTC_1", "NTC_2"]}],
                                            "status": "confirmed", "source": "t"},
                               "assignment": {"value": {"rule": "deposit"}, "status": "confirmed", "source": "t"}},
              "units": {"independent_unit": {"value": "mouse", "status": "confirmed",
                                             "source": "t"}}}
    direct = so.build(str(root), design, str(tmp_path / "direct"))
    tabled = so.build(str(root), design, str(tmp_path / "tabled"), cell_tables=str(tmp_path / "tables"))
    a = pd.read_csv(os.path.join(tmp_path, "direct", "cells.tsv.gz"), sep="\t").sort_values("barcode")
    b = pd.read_csv(os.path.join(tmp_path, "tabled", "cells.tsv.gz"), sep="\t").sort_values("barcode")
    cols = ["barcode", "sample", "unit", "label", "umis"]
    pd.testing.assert_frame_equal(a[cols].reset_index(drop=True), b[cols].reset_index(drop=True), check_dtype=False)
    assert direct["labels"] == tabled["labels"]


def test_the_agent_changes_the_reading_and_the_user_sees_it(tmp_path):
    """The reading is the agent's (docs/adr/0041, step 1b-2): it takes a sample out with why, or reads one from other
    files; the measurement's answer and describe_deposit give the reading with what to do, and the design form shows
    which files made which samples, with the notes."""
    from test_design_service import make_deposit as make_service_deposit, proposal

    from pertura.analysis_service import AnalysisService
    deposit = tmp_path / "deposit"
    deposit.mkdir()
    make_service_deposit(str(deposit))
    svc = AnalysisService({"workspace": str(tmp_path / "ws")})
    try:
        ok, out = svc.call("open_project", {"data_folder": str(deposit)})
        assert ok, out
        ok, measured = svc.call("measure", {})
        assert ok, measured
        read = measured["read_into_cell_tables"]
        assert sorted(read["samples"]) == ["S1", "S2"] and "cell_table.remove" in read["do"]
        assert all(s["read_by"] == "pertura:10x" and s["from"] for s in read["samples"].values())
        tables = os.path.join(svc.design.dir, "cell_tables")
        files = read["samples"]["S2"]["from"]
        assert cell_table.remove(tables, "S2", "the same cells as S1, the user said")["ok"]
        assert not cell_table.remove(tables, "S9", "x")["ok"]
        assert [s["name"] for s in cell_table.samples(tables)] == ["S1"]
        assert "S2: not read (the same cells as S1, the user said)" in cell_table.manifest(tables)["notes"]
        assert any(f.endswith("matrix.mtx.gz") for f in files) and any(f.endswith("barcodes.tsv.gz") for f in files)
        mtx, feats, bcs = (os.path.join(str(deposit), f) for f in files[:3])
        back = cell_table.read_10x(tables, "S2", mtx, features=feats, barcodes=bcs, made_by="run:test")
        assert back["ok"], back
        assert svc.call("describe_deposit", {})[1]["read_into_cell_tables"]["samples"]["S2"]["read_by"] == "run:test"
        ok, out = svc.call("check_design", {"proposal": proposal()})
        said = [f["text"] for f in out["flags"] if f["key"] == "data.read_by"]
        assert any(t.startswith("the deposit read as 2 samples (S1 from ") and "S2: not read" in t for t in said), said
    finally:
        svc.close()


def test_matrices_of_the_same_barcodes_read_as_one_sample_are_a_note(tmp_path):
    """The readers join triplets of the same barcodes into one sample (a guide or protein library deposited apart);
    the proposal says so, since two expression libraries of the same cells would be joined the same way."""
    import gzip

    import numpy as np
    import scipy.io
    import scipy.sparse as sp
    root = tmp_path / "deposit"
    root.mkdir()
    rng = np.random.default_rng(0)
    for name, feats in (("s1_gex", [f"G{i}\tG{i}\tGene Expression" for i in range(40)]),
                        ("s1_guides", ["A_1\tA_1\tCRISPR Guide Capture", "NT_1\tNT_1\tCRISPR Guide Capture"])):
        with gzip.open(root / f"{name}_matrix.mtx.gz", "wb") as fh:
            scipy.io.mmwrite(fh, sp.coo_matrix(rng.poisson(2, (20, len(feats))).T))
        (root / f"{name}_features.tsv").write_text("\n".join(feats) + "\n")
        (root / f"{name}_barcodes.tsv").write_text("\n".join(f"C{i:03d}-1" for i in range(20)) + "\n")
    m = _propose(root, tmp_path / "tables")
    assert sorted(m["samples"]) == ["s1_gex"]
    assert any(n.startswith("s1_guides_matrix.mtx.gz: read with s1_gex as further features") for n in m["notes"])
