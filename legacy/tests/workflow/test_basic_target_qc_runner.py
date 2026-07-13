from __future__ import annotations

import json
from pathlib import Path

from pertura_workflow.runners import run_basic_target_qc


def test_basic_target_qc_summarizes_uid_linked_cells_and_guides(tmp_path: Path) -> None:
    (tmp_path / "metadata.csv").write_text(
        "cell_id,perturbation_uid,guide\n"
        "c1,target:KLF1,sgKLF1_1\n"
        "c2,target:KLF1,sgKLF1_2\n"
        "c3,control:negative_control_pool,NegCtrl0\n"
        "c4,control:negative_control_pool,NegCtrl1\n"
        "c5,target:OTHER,sgOTHER\n",
        encoding="utf-8",
    )
    (tmp_path / "guide_map.csv").write_text(
        "guide,target\nsgKLF1_1,KLF1\nsgKLF1_2,KLF1\nNegCtrl0,negative_control\nNegCtrl1,negative_control\n",
        encoding="utf-8",
    )

    result = run_basic_target_qc(
        tmp_path,
        metadata_csv="metadata.csv",
        target_uid="target:KLF1",
        control_uid="control:negative_control_pool",
        target="KLF1",
        control="NegCtrl0",
        guide_column="guide",
        guide_to_target_csv="guide_map.csv",
        minimum_cells=2,
    )

    output_path = Path(result["path"])
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["n_target_cells"] == 2
    assert payload["n_control_cells"] == 2
    assert payload["guides_per_target"] == 2
    assert payload["passes_min_cells"] is True
    assert payload["control_calibration"]["negative_control_available"] is True
    assert payload["skipped_cells"] == 1


def test_basic_target_qc_requires_uid_identity(tmp_path: Path) -> None:
    (tmp_path / "metadata.csv").write_text("cell_id,perturbation_uid\nc1,target:KLF1\n", encoding="utf-8")

    try:
        run_basic_target_qc(
            tmp_path,
            metadata_csv="metadata.csv",
            target_uid="",
            control_uid="control:negative_control_pool",
            target="KLF1",
        )
    except ValueError as exc:
        assert "target_uid" in str(exc)
    else:
        raise AssertionError("missing target_uid should be rejected")