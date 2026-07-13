from __future__ import annotations

import json
from pathlib import Path

from pertura_workflow.runners import run_label_permutation_null, run_ntc_vs_ntc_calibration


def _write_inputs(tmp_path: Path) -> None:
    (tmp_path / "metadata.csv").write_text(
        "cell_id,perturbation_uid\n"
        "c1,control:negative_control_pool\n"
        "c2,control:negative_control_pool\n"
        "c3,control:negative_control_pool\n"
        "c4,control:negative_control_pool\n"
        "c5,target:KLF1\n"
        "c6,target:KLF1\n"
        "c7,target:KLF1\n"
        "c8,target:KLF1\n",
        encoding="utf-8",
    )
    (tmp_path / "expression.csv").write_text(
        "cell_id,G1,G2\n"
        "c1,1,2\n"
        "c2,1,2\n"
        "c3,1,2\n"
        "c4,1,2\n"
        "c5,1,2\n"
        "c6,1,2\n"
        "c7,1,2\n"
        "c8,1,2\n",
        encoding="utf-8",
    )


def test_ntc_vs_ntc_calibration_writes_deterministic_json_without_registry(tmp_path: Path) -> None:
    _write_inputs(tmp_path)

    result = run_ntc_vs_ntc_calibration(
        tmp_path,
        expression_csv="expression.csv",
        metadata_csv="metadata.csv",
        control_uid="control:negative_control_pool",
        layer="normalized_counts",
        seed=7,
    )

    payload = json.loads(Path(result["path"]).read_text(encoding="utf-8"))
    assert payload["calibration_type"] == "ntc_vs_ntc_check"
    assert payload["ntc_vs_ntc_check"]["passed"] is True
    assert payload["n_features_tested"] == 2
    assert payload["n_significant"] == 0
    assert payload["execution_hash"].startswith("sha256:")
    assert not (tmp_path / "artifacts" / "evidence_artifacts.jsonl").exists()


def test_label_permutation_null_is_deterministic_for_fixed_seed(tmp_path: Path) -> None:
    _write_inputs(tmp_path)

    first = run_label_permutation_null(
        tmp_path,
        expression_csv="expression.csv",
        metadata_csv="metadata.csv",
        contrast_uid="contrast:KLF1_vs_NTC",
        left_uid="target:KLF1",
        baseline_uid="control:negative_control_pool",
        layer="normalized_counts",
        seed=11,
    )
    second = run_label_permutation_null(
        tmp_path,
        expression_csv="expression.csv",
        metadata_csv="metadata.csv",
        contrast_uid="contrast:KLF1_vs_NTC",
        left_uid="target:KLF1",
        baseline_uid="control:negative_control_pool",
        layer="normalized_counts",
        seed=11,
        output_path="outputs/repeated_label_permutation.json",
    )

    first_payload = json.loads(Path(first["path"]).read_text(encoding="utf-8"))
    second_payload = json.loads(Path(second["path"]).read_text(encoding="utf-8"))
    assert first_payload["label_permutation_check"]["passed"] is True
    assert first_payload["execution_hash"] == second_payload["execution_hash"]


def test_calibration_runners_reject_missing_uid_identity(tmp_path: Path) -> None:
    _write_inputs(tmp_path)
    try:
        run_label_permutation_null(
            tmp_path,
            expression_csv="expression.csv",
            metadata_csv="metadata.csv",
            contrast_uid="",
            left_uid="target:KLF1",
            baseline_uid="control:negative_control_pool",
            layer="normalized_counts",
        )
    except ValueError as exc:
        assert "contrast_uid" in str(exc)
    else:
        raise AssertionError("missing contrast_uid should be rejected")


def test_calibration_runners_reject_paths_outside_workspace(tmp_path: Path) -> None:
    _write_inputs(tmp_path)
    outside = tmp_path.parent / "outside_expression.csv"
    outside.write_text("cell_id,G1\nc1,1\n", encoding="utf-8")
    try:
        run_ntc_vs_ntc_calibration(
            tmp_path,
            expression_csv=outside,
            metadata_csv="metadata.csv",
            control_uid="control:negative_control_pool",
            layer="normalized_counts",
        )
    except ValueError as exc:
        assert "outside workspace" in str(exc)
    else:
        raise AssertionError("outside expression_csv should be rejected")
