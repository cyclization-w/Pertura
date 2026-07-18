from __future__ import annotations

import json
from pathlib import Path

from pertura_bench.task_submission import (
    TaskSubmissionService,
    validate_submission_receipt,
)


def _result(**updates: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": "pertura-agent-benchmark-result-v1",
        "case_id": "T-01",
        "dataset_id": "D-01",
        "result_type": "fixture",
        "analysis_unit": "donor",
        "status": "completed",
        "findings": [],
        "metrics": {"paired_units": 4},
        "limitations": ["fixture"],
        "artifact_roles": ["de_results"],
    }
    payload.update(updates)
    return payload


def _draft(**updates: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": "pertura-turn-draft-v1",
        "headline": "Fixture completed",
        "findings": [],
        "limitations": ["fixture"],
    }
    payload.update(updates)
    return payload


def test_submission_validates_both_objects_before_writing(tmp_path: Path) -> None:
    service = TaskSubmissionService(tmp_path)
    service.bind_task(task_id="T-01", dataset_id="D-01")

    rejected = service.submit_task_bundle(
        {
            "benchmark_result": _result(metrics={"nested": {"bad": 1}}),
            "turn_draft": _draft(
                findings=[
                    {
                        "finding_id": "F-01",
                        "text": "bad role",
                        "declared_role": "invented",
                    }
                ]
            ),
        }
    )

    assert rejected["accepted"] is False
    fields = {item["field"] for item in rejected["errors"]}
    assert any(field.startswith("benchmark_result.metrics.nested") for field in fields)
    assert any(field.endswith("declared_role") for field in fields)
    output = tmp_path / "outputs/tasks/T-01"
    assert not (output / "submission_receipt.json").exists()
    assert not (output / "submitted_turn_draft.json").exists()


def test_submission_is_atomic_receipted_and_tamper_evident(tmp_path: Path) -> None:
    service = TaskSubmissionService(tmp_path)
    service.bind_task(task_id="T-01", dataset_id="D-01")

    response = service.submit_task_bundle(
        {"benchmark_result": _result(), "turn_draft": _draft()}
    )

    assert response["accepted"] is True
    output = tmp_path / "outputs/tasks/T-01"
    receipt, problem = validate_submission_receipt(
        output, task_id="T-01", dataset_id="D-01"
    )
    assert problem is None
    assert receipt is not None
    assert receipt["canonical_hash"] == response["canonical_hash"]
    assert json.loads(service.submitted_turn_draft() or "null")["headline"] == (
        "Fixture completed"
    )
    assert not list(output.glob("*.tmp"))

    (output / "benchmark_result.json").write_text("{}\n", encoding="utf-8")
    receipt, problem = validate_submission_receipt(
        output, task_id="T-01", dataset_id="D-01"
    )
    assert receipt is None
    assert problem == "benchmark result no longer matches its submission receipt"


def test_submission_rejects_wrong_bound_identity(tmp_path: Path) -> None:
    service = TaskSubmissionService(tmp_path)
    service.bind_task(task_id="T-01", dataset_id="D-01")

    response = service.submit_task_bundle(
        {
            "benchmark_result": _result(case_id="T-02", dataset_id="D-02"),
            "turn_draft": _draft(),
        }
    )

    assert response["accepted"] is False
    assert {item["type"] for item in response["errors"]} == {"identity_mismatch"}
