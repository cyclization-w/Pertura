from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sys
import types

from pertura_bench.task_submission import (
    TaskSubmissionService,
    create_task_submission_mcp_server,
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


def test_mcp_wrapper_returns_visible_rejection_and_acceptance(
    tmp_path: Path,
    monkeypatch,
) -> None:
    def tool(name: str, description: str, schema: dict[str, object]):
        del name, description, schema
        return lambda function: function

    def create_sdk_mcp_server(**kwargs: object) -> dict[str, object]:
        return dict(kwargs)

    monkeypatch.setitem(
        sys.modules,
        "claude_agent_sdk",
        types.SimpleNamespace(
            tool=tool,
            create_sdk_mcp_server=create_sdk_mcp_server,
        ),
    )
    service = TaskSubmissionService(tmp_path)
    service.bind_task(task_id="T-01", dataset_id="D-01")
    server = create_task_submission_mcp_server(service)
    handler = server["tools"][0]

    rejected_result = asyncio.run(
        handler(
            {
                "benchmark_result": _result(),
                "turn_draft": {
                    "schema_version": "pertura-turn-draft-v1",
                    "turn_index": 1,
                    "summary": "legacy shape",
                },
            }
        )
    )
    rejected = json.loads(rejected_result["content"][0]["text"])
    assert rejected["accepted"] is False
    fields = {item["field"] for item in rejected["errors"]}
    assert "turn_draft.headline" in fields
    assert "turn_draft.turn_index" in fields
    assert "turn_draft.summary" in fields

    accepted_result = asyncio.run(
        handler(
            {
                "benchmark_result": _result(),
                "turn_draft": _draft(),
            }
        )
    )
    accepted = json.loads(accepted_result["content"][0]["text"])
    assert accepted["accepted"] is True
    assert accepted["submission_id"].startswith("submission_")
    output = tmp_path / "outputs/tasks/T-01"
    assert (output / "benchmark_result.json").is_file()
    assert (output / "submitted_turn_draft.json").is_file()
    assert (output / "submission_receipt.json").is_file()
