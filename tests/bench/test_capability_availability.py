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
        "intake.materialize.v1",
        "diagnostic.contract_integrity.v1",
        "diagnostic.dataset_integrity.v1",
    ]
    assert _excluded_ids(records["REPL-01"]) == {
        "diagnostic.design_balance.v1"
    }
    assert records["PAPA-01"]["advertised_capability_ids"] == []
    assert _excluded_ids(records["PAPA-01"]) == {
        "diagnostic.guide_assignment.v1"
    }
    assert records["PAPA-02"]["advertised_conditional_capability_ids"] == []
    assert _excluded_ids(records["PAPA-02"]) == {
        "reference.state.control_pca_leiden.v1",
        "state.annotation_candidates.v1",
        "state.reference.fit.v1",
    }
    assert records["KANG-01"]["candidate_capability_ids"] == []
    assert records["KANG-01"]["advertised_capability_ids"] == []
    kang_exclusions = {
        item["capability_id"]: item["reasons"]
        for item in records["KANG-02"]["structurally_excluded_capabilities"]
    }
    assert "required asset roles are unavailable: cell_metadata" not in (
        kang_exclusions["diagnostic.design_balance.v1"]
    )
    assert kang_exclusions["diagnostic.design_balance.v1"] == [
        "required capability has no legal producer: "
        "diagnostic.dataset_integrity.v1"
    ]
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
    assert "diagnostic.design_balance.v1" not in serialized
    assert "cell_metadata" not in serialized


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


def _excluded_ids(record: dict) -> set[str]:
    return {
        item["capability_id"]
        for item in record["structurally_excluded_capabilities"]
    }
