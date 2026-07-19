from __future__ import annotations

import importlib.util
from pathlib import Path

import pandas as pd

from pertura_bench.paper_task_evaluation import evaluate_paper_task
from pertura_core.hashing import file_sha256


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "qualify_a19_evaluators",
    ROOT / "scripts/qualify_a19_evaluators.py",
)
assert SPEC is not None and SPEC.loader is not None
qualification = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(qualification)


def _generic_fixture(tmp_path: Path, *, numeric: bool):
    paper = tmp_path / "paper"
    output = tmp_path / "positive"
    paper.mkdir()
    output.mkdir()
    reference = paper / "reference.tsv"
    rows = (
        [{"id": "a", "effect": 1.0}, {"id": "b", "effect": -1.0}]
        if numeric
        else [{"id": "a", "label": "x"}, {"id": "b", "label": "y"}]
    )
    pd.DataFrame(rows).to_csv(reference, sep="\t", index=False)
    observed = output / "observed.tsv"
    pd.DataFrame(rows).to_csv(observed, sep="\t", index=False)
    evaluator = {
        "evaluator_id": "fixture",
        "type": "effect_error" if numeric else "classification",
        "observed_output": "observed.tsv",
        "reference_path": "reference.tsv",
        "reference_sha256": file_sha256(reference),
        "key_columns": ["id"],
        **(
            {
                "observed_value_column": "effect",
                "reference_value_column": "effect",
                "maximum_mae": 0.0,
                "maximum_rmse": 0.0,
            }
            if numeric
            else {
                "observed_label_column": "label",
                "reference_label_column": "label",
                "minimum_accuracy": 1.0,
                "minimum_macro_f1": 1.0,
            }
        ),
    }
    binding = {
        "task_reference_id": "fixture",
        "evaluator_id": "task.fixture.v1",
        "evaluation_domain": "scientific_fidelity",
        "evaluators": [evaluator],
        "protocol_evaluator": {
            "allowed_status": ["completed"],
            "allowed_analysis_units": ["donor"],
        },
    }
    task = {"task_id": "FIXTURE"}
    result = {
        "status": "completed",
        "analysis_unit": "donor",
        "findings": [],
        "limitations": [],
    }
    positive = evaluate_paper_task(
        task,
        benchmark_result=result,
        task_output_root=output,
        paper_root=paper,
        bindings=[binding],
    )
    assert positive["status"] == "passed"
    return paper, output, task, result, binding, observed, evaluator


def test_qualification_executes_structural_negative_controls(tmp_path: Path) -> None:
    paper, output, task, result, binding, observed, evaluator = _generic_fixture(
        tmp_path, numeric=False
    )

    controls = qualification._negative_controls(
        task=task,
        result=result,
        binding=binding,
        positive_root=output,
        control_root=tmp_path / "negative",
        observed=[(observed, ("id",), evaluator)],
        paper_root=paper,
    )

    assert set(controls["artifact_controls"]["observed.tsv"]) == {
        "missing_artifact",
        "missing_key",
        "duplicate_key",
        "wrong_row_universe",
    }
    assert controls["result_controls"]["wrong_analysis_unit"]["status"] == "failed"
    assert all(
        item["status"] == "failed"
        for item in controls["artifact_controls"]["observed.tsv"].values()
    )


def test_qualification_executes_numeric_negative_controls(tmp_path: Path) -> None:
    paper, output, task, result, binding, observed, evaluator = _generic_fixture(
        tmp_path, numeric=True
    )

    controls = qualification._negative_controls(
        task=task,
        result=result,
        binding=binding,
        positive_root=output,
        control_root=tmp_path / "negative",
        observed=[(observed, ("id",), evaluator)],
        paper_root=paper,
    )

    observed_controls = controls["artifact_controls"]["observed.tsv"]
    assert observed_controls["nonfinite_value"]["status"] == "failed"
    assert observed_controls["wrong_effect_or_probability"]["status"] == "failed"


def test_qualification_records_only_executed_controls() -> None:
    source = (ROOT / "scripts/qualify_a19_evaluators.py").read_text(encoding="utf-8")
    assert '"reference_leakage_audit"' not in source
    assert "negative_control_coverage" in source
    assert "_require_failed" in source


def test_qualification_materializes_protocol_balances(tmp_path: Path) -> None:
    task = {
        "output_contract": {
            "artifact_paths": {"accounting": "accounting.json"},
        }
    }
    binding = {
        "protocol_evaluator": {
            "required_json_values": {
                "accounting.json": {"excluded": 6, "donor_count": 4}
            },
            "required_json_balances": [
                {
                    "output": "accounting.json",
                    "total": "evaluation_cells",
                    "parts": ["analyzed", "excluded"],
                }
            ],
        }
    }
    qualification._fill_protocol_outputs(task, binding, tmp_path)

    payload = qualification._read_json(tmp_path / "accounting.json")
    assert payload == {
        "analyzed": 0,
        "donor_count": 4,
        "evaluation_cells": 6.0,
        "excluded": 6,
    }
