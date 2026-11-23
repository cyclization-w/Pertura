"""A capture's matrix of every droplet beside its filtered matrix is the same capture, not another sample (development
run on GSE335887, 2026-11-05: Cell Ranger's raw_feature_bc_matrix.h5 of both captures, 3.6 million barcodes, were
read as two more samples, and the screen object's build ran past 170 GB before it was stopped)."""
import os

from pertura.design_audit import readers


def test_the_raw_twin_is_left_out_and_a_lone_raw_matrix_stays(tmp_path):
    for n in ("GSE1_img_filtered_feature_bc_matrix.h5", "GSE1_img_raw_feature_bc_matrix.h5",
              "GSE1_itf_raw_feature_bc_matrix.h5"):
        (tmp_path / n).write_bytes(b"")
    out = readers.discover(str(tmp_path))
    names = [s["name"] for s in out["samples"]]
    assert len(names) == 2 and not any("img_raw" in n for n in names)  # img: its filtered matrix only
    assert any("itf_raw" in n for n in names)  # no filtered twin: its cells are called as before
    assert out["duplicates"] == [{"matrix": "GSE1_img_raw_feature_bc_matrix.h5",
                                  "same_capture_as": "its filtered_feature_bc_matrix, which holds the called cells"}]
