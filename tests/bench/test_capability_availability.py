from __future__ import annotations

import json
from pathlib import Path

from pertura_bench import paper_agent_execution as execution
from pertura_bench.capability_availability import (
    availability_by_task,
    build_task_capability_availability,
)
from pertura_workflow.capability_contracts import (
    build_capability_contract_catalog,
)


ROOT = Path(__file__).resolve().parents[2]
TASKS = json.loads(
    (ROOT / "benchmarks/paper_v1/agent_tasks.v2.json").read_text(
        encoding="utf-8"
    )
)


def _availability():
    catalog = build_capability_contract_catalog()
    manifest = build_task_capability_availability(TASKS, catalog)
    return catalog, manifest, availability_by_task(manifest)


def test_static_availability_excludes_impossible_roles_and_dependencies() -> None:
    _, manifest, records = _availability()

    assert manifest["task_count"] == 21
    assert manifest["canonical_hash"].startswith("sha256:")
    assert records["REPL-01"]["advertised_direct_capability_ids"] == [
        "diagnostic.contract_integrity.v1",
        "diagnostic.dataset_integrity.v1",
        "diagnostic.design_balance.v1",
    ]
    assert _excluded_ids(records["REPL-01"]) == set()
    assert records["PAPA-01"]["advertised_capability_ids"] == []
    assert _excluded_ids(records["PAPA-01"]) == set()
    assert records["PAPA-02"]["advertised_conditional_capability_ids"] == []
    assert records["PAPA-02"]["advertised_capability_ids"] == [
        "state.reference.fit.v1"
    ]
    assert _excluded_ids(records["PAPA-02"]) == set()
    assert records["KANG-01"]["candidate_capability_ids"] == []
    assert records["KANG-01"]["advertised_capability_ids"] == []
    assert records["KANG-02"]["advertised_capability_ids"] == [
        "diagnostic.design_balance.v1",
        "composition.propeller.v1",
    ]
    assert _excluded_ids(records["KANG-02"]) == set()
    assert "virtual.prediction.ingest.v1" in _excluded_ids(records["VIRT-01"])
    deprecated = {
        item["capability_id"]
        for item in build_capability_contract_catalog()["capabilities"]
        if item["deprecated"]
    }
    for record in manifest["records"]:
        task = next(
            task
            for workflow in TASKS["workflows"]
            for task in workflow["turns"]
            if task["task_id"] == record["task_id"]
        )
        assert record["audited_codeact_fallback"] is (
            task["execution_mode"] == "capability_or_codeact"
        )
        assert not (set(record["advertised_capability_ids"]) & deprecated)
        assert not (
            set(record["advertised_capability_ids"])
            & _excluded_ids(record)
        )


def test_provider_subset_contains_only_advertised_contracts() -> None:
    catalog, _, records = _availability()
    task = next(
        task
        for workflow in TASKS["workflows"]
        for task in workflow["turns"]
        if task["task_id"] == "REPL-01"
    )
    subset = execution._task_capability_contract_subset(
        task=task,
        contract_catalog=catalog,
        availability=records["REPL-01"],
    )

    assert subset["schema_version"] == (
        "pertura-paper-capability-contract-subset-v2"
    )
    assert subset["candidate_capability_ids"] == (
        records["REPL-01"]["advertised_capability_ids"]
    )
    assert subset["audited_codeact_fallback"] is True
    assert subset["structurally_excluded_capabilities"] == []
    serialized = json.dumps(subset)
    assert "diagnostic.design_balance.v1" in serialized
    assert "cell_metadata" in serialized


def test_capability_calls_are_observed_without_becoming_a_route_gate(
    tmp_path: Path,
) -> None:
    event_log = tmp_path / "events.jsonl"
    event_log.write_text(
        json.dumps(
            {
                "message_type": "AssistantMessage",
                "payload": {
                    "content": [
                        "ToolUseBlock(id='call_1', "
                        "name='mcp__pertura__run_analysis', "
                        "input={'capability_id': "
                        "'reference.state.control_pca_leiden.v1'})"
                    ]
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )

    audit = execution._audit_capability_treatment_uptake(
        event_log,
        start_offset=0,
        advertised_capability_ids=(
            "reference.state.control_pca_leiden.v1",
        ),
    )

    assert audit["actual_mcp_capability_ids"] == [
        "reference.state.control_pca_leiden.v1"
    ]
    assert audit["calls"][0]["advertised"] is True
    assert "status" not in audit


def test_bound_call_errors_separate_runner_incidents_from_model_overrides(
    tmp_path: Path,
) -> None:
    event_log = tmp_path / "events.jsonl"
    records = [
        {
            "message_type": "AssistantMessage",
            "payload": {
                "content": [
                    "ToolUseBlock(id='call_runner', "
                    "name='mcp__pertura__run_analysis', "
                    "input={'binding_id': 'binding_ready'})",
                    "ToolUseBlock(id='call_model', "
                    "name='mcp__pertura__run_analysis', "
                    "input={'binding_id': 'binding_ready', "
                    "'parameters': {'h5ad_path': 'wrong'}})",
                    "ToolUseBlock(id='call_order', "
                    "name='mcp__pertura__run_analysis', "
                    "input={'binding_id': 'binding_ready'})",
                ]
            },
        },
        {
            "message_type": "UserMessage",
            "payload": {
                "content": [
                    "ToolResultBlock(tool_use_id='call_runner', "
                    "content='environment lock drifted', is_error=True)",
                    "ToolResultBlock(tool_use_id='call_model', "
                    "content='caller attempted to override locked parameters', "
                    "is_error=True)",
                    "ToolResultBlock(tool_use_id='call_order', "
                    "content='bound predecessor has not produced a result', "
                    "is_error=True)",
                ]
            },
        },
    ]
    event_log.write_text(
        "\n".join(json.dumps(item) for item in records) + "\n",
        encoding="utf-8",
    )

    audit = execution._audit_capability_treatment_uptake(
        event_log,
        start_offset=0,
        advertised_capability_ids=("state.reference.fit.v1",),
        invocation_bindings=(
            {
                "binding_id": "binding_ready",
                "capability_id": "state.reference.fit.v1",
                "tool": "run_analysis",
            },
        ),
    )

    assert len(audit["runner_binding_integration_errors"]) == 1
    assert len(audit["model_binding_errors"]) == 2
    assert audit["model_binding_error_counts"] == {"binding_ready": 2}
    assert audit["model_binding_retry_status"] == "failed"


def test_checkpoint_requires_self_hashed_binding_qualification(
    tmp_path: Path,
) -> None:
    binding = {
        "git_commit": "a" * 40,
        "wheel_sha256": "sha256:" + "1" * 64,
        "paper_task_catalog_hash": "sha256:" + "2" * 64,
        "paper_task_reference_catalog_hash": "sha256:" + "3" * 64,
        "paper_asset_catalog_hash": "sha256:" + "4" * 64,
        "capability_contract_catalog_hash": "sha256:" + "5" * 64,
    }
    payload = {
        "schema_version": "pertura-capability-binding-qualification-v1",
        "status": "passed",
        "passed": True,
        "git_commit": binding["git_commit"],
        "wheel_sha256": binding["wheel_sha256"],
        "task_catalog_sha256": binding["paper_task_catalog_hash"],
        "task_reference_catalog_sha256": binding[
            "paper_task_reference_catalog_hash"
        ],
        "paper_asset_catalog_sha256": binding[
            "paper_asset_catalog_hash"
        ],
        "capability_contract_catalog_sha256": binding[
            "capability_contract_catalog_hash"
        ],
        "qualified_binding_count": 3,
        "records": [
            {
                "binding_id": f"binding_{index}",
                "qualification_status": status,
            }
            for index, status in enumerate(
                (
                    "executed",
                    "expected_blocked_probe",
                    "executed_terminal_diagnostic_block",
                )
            )
        ],
    }
    payload["canonical_hash"] = execution.canonical_hash(payload)
    path = tmp_path / "capability-binding-qualification.a19.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    observed = execution._verify_capability_binding_qualification(
        path, checkpoint_binding=binding
    )
    assert observed["qualified_binding_count"] == 3

    payload["qualified_binding_count"] = 2
    path.write_text(json.dumps(payload), encoding="utf-8")
    try:
        execution._verify_capability_binding_qualification(
            path, checkpoint_binding=binding
        )
    except ValueError as error:
        assert "not valid" in str(error)
    else:
        raise AssertionError("tampered qualification was accepted")


def test_checkpoint_rejects_binding_qualification_input_drift(
    tmp_path: Path,
) -> None:
    binding = {
        "git_commit": "a" * 40,
        "wheel_sha256": "sha256:" + "1" * 64,
        "paper_task_catalog_hash": "sha256:" + "2" * 64,
        "paper_task_reference_catalog_hash": "sha256:" + "3" * 64,
        "paper_asset_catalog_hash": "sha256:" + "4" * 64,
        "capability_contract_catalog_hash": "sha256:" + "5" * 64,
    }
    payload = {
        "schema_version": "pertura-capability-binding-qualification-v1",
        "status": "passed",
        "passed": True,
        "git_commit": binding["git_commit"],
        "wheel_sha256": binding["wheel_sha256"],
        "task_catalog_sha256": binding["paper_task_catalog_hash"],
        "task_reference_catalog_sha256": binding[
            "paper_task_reference_catalog_hash"
        ],
        "paper_asset_catalog_sha256": binding[
            "paper_asset_catalog_hash"
        ],
        "capability_contract_catalog_sha256": "sha256:" + "9" * 64,
        "qualified_binding_count": 1,
        "records": [
            {
                "binding_id": "binding_1",
                "qualification_status": "executed",
            }
        ],
    }
    payload["canonical_hash"] = execution.canonical_hash(payload)
    path = tmp_path / "capability-binding-qualification.a19.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    try:
        execution._verify_capability_binding_qualification(
            path, checkpoint_binding=binding
        )
    except ValueError as error:
        assert "capability_contract_catalog_sha256" in str(error)
    else:
        raise AssertionError("drifted qualification was accepted")


def _excluded_ids(record: dict) -> set[str]:
    return {
        item["capability_id"]
        for item in record["structurally_excluded_capabilities"]
    }
