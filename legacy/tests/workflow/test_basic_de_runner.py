from __future__ import annotations

import csv
from pathlib import Path

from pertura_workflow.runners import run_basic_de_for_registered_contrast


def test_basic_de_runner_requires_uid_linked_contrast_and_writes_de_table(tmp_path: Path) -> None:
    expression = tmp_path / "expression.csv"
    metadata = tmp_path / "metadata.csv"
    expression.write_text(
        "cell_id,KLF1,GYPA\n"
        "c1,10,6\n"
        "c2,12,5\n"
        "c3,2,1\n"
        "c4,1,2\n",
        encoding="utf-8",
    )
    metadata.write_text(
        "cell_id,perturbation_uid\n"
        "c1,target:KLF1\n"
        "c2,target:KLF1\n"
        "c3,control:negative_control_pool\n"
        "c4,control:negative_control_pool\n",
        encoding="utf-8",
    )

    result = run_basic_de_for_registered_contrast(
        tmp_path,
        expression_csv="expression.csv",
        metadata_csv="metadata.csv",
        contrast_uid="contrast:target:KLF1:vs:control:negative_control_pool",
        left_uid="target:KLF1",
        baseline_uid="control:negative_control_pool",
        layer="normalized_counts",
    )

    output_path = Path(result["path"])
    assert output_path.exists()
    assert result["method"] == "basic_mean_difference_v1"
    assert result["n_left"] == 2
    assert result["n_baseline"] == 2
    with output_path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert rows
    assert {"gene", "logfc", "pvalue", "padj"}.issubset(rows[0])
    assert rows[0]["gene"] == "KLF1"


def test_basic_de_runner_rejects_missing_scope_identity(tmp_path: Path) -> None:
    (tmp_path / "expression.csv").write_text("cell_id,KLF1\nc1,1\n", encoding="utf-8")
    (tmp_path / "metadata.csv").write_text("cell_id,perturbation_uid\nc1,target:KLF1\n", encoding="utf-8")

    try:
        run_basic_de_for_registered_contrast(
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