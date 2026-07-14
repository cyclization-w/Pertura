from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "benchmarks" / "paper_v1" / "agent_tasks.v1.json"
CAPABILITY_MATRIX = (
    ROOT / "benchmarks" / "paper_v1" / "capability_matrix.v1.json"
)


def main() -> int:
    payload = json.loads(CATALOG.read_text(encoding="utf-8"))
    capability_payload = json.loads(
        CAPABILITY_MATRIX.read_text(encoding="utf-8")
    )
    known_capabilities = {
        item["capability_id"]
        for scenario in capability_payload["scenarios"]
        for item in scenario["capabilities"]
    }
    known_scenarios = {
        scenario["scenario_id"]
        for scenario in capability_payload["scenarios"]
    }
    known_datasets = set(capability_payload["scope"]["primary_datasets"]) | set(
        capability_payload["scope"]["supplemental_datasets"]
    )

    problems: list[str] = []
    task_ids: list[str] = []
    primary_count = 0
    supplemental_count = 0
    workflow_count = 0
    forbidden_objective_tokens = (
        "pertura_full",
        "free_codeact",
        "prompt_only",
        "receipt",
        "claim_ceiling",
        "mcp__",
    )

    for workflow in payload.get("workflows") or ():
        workflow_count += 1
        workflow_id = str(workflow.get("workflow_id") or "")
        role = str(workflow.get("role") or "")
        dataset_id = str(workflow.get("dataset_id") or "")
        if role not in {"primary", "supplemental"}:
            problems.append(f"{workflow_id}: invalid role")
        if dataset_id not in known_datasets:
            problems.append(f"{workflow_id}: unknown dataset {dataset_id}")
        expected_turn = 1
        prior_tasks: set[str] = set()
        for task in workflow.get("turns") or ():
            task_id = str(task.get("task_id") or "")
            task_ids.append(task_id)
            if role == "primary":
                primary_count += 1
            else:
                supplemental_count += 1
            if task.get("turn_index") != expected_turn:
                problems.append(f"{task_id}: nonsequential turn_index")
            expected_turn += 1
            unknown_dependencies = set(task.get("depends_on_tasks") or ()) - prior_tasks
            if unknown_dependencies:
                problems.append(
                    f"{task_id}: dependencies are not prior workflow turns: "
                    f"{sorted(unknown_dependencies)}"
                )
            prior_tasks.add(task_id)
            unknown_scenarios = set(task.get("capability_scenarios") or ()) - known_scenarios
            if unknown_scenarios:
                problems.append(
                    f"{task_id}: unknown capability scenarios: "
                    f"{sorted(unknown_scenarios)}"
                )
            unknown_capabilities = set(task.get("expected_capability_dag") or ()) - known_capabilities
            if unknown_capabilities:
                problems.append(
                    f"{task_id}: unknown capabilities: "
                    f"{sorted(unknown_capabilities)}"
                )
            objective = str(task.get("objective") or "")
            if not objective:
                problems.append(f"{task_id}: missing objective")
            objective_lower = objective.lower()
            for token in forbidden_objective_tokens:
                if token in objective_lower:
                    problems.append(
                        f"{task_id}: condition-specific token in objective: {token}"
                    )
            for field in (
                "required_artifact_roles",
                "task_hard_gates",
                "metric_ids",
                "reference_requirement",
            ):
                if not task.get(field):
                    problems.append(f"{task_id}: missing {field}")

    duplicates = sorted(
        task_id
        for task_id in set(task_ids)
        if task_ids.count(task_id) > 1
    )
    if duplicates:
        problems.append(f"duplicate task ids: {duplicates}")

    protocol = payload["execution_protocol"]
    expected_conditions = {"free_codeact", "prompt_only", "pertura_full"}
    if set(protocol["conditions"]) != expected_conditions:
        problems.append("execution conditions do not match the frozen three-condition design")
    if protocol["repeats"] != 2:
        problems.append("formal protocol requires two repeats")
    if primary_count != protocol["primary_task_turns"]:
        problems.append("primary_task_turns does not match the task catalog")
    if supplemental_count != protocol["supplemental_task_turns"]:
        problems.append("supplemental_task_turns does not match the task catalog")
    calculated_primary_runs = primary_count * len(expected_conditions) * protocol["repeats"]
    calculated_supplemental_runs = (
        supplemental_count * len(expected_conditions) * protocol["repeats"]
    )
    if calculated_primary_runs != protocol["primary_scored_runs"]:
        problems.append("primary_scored_runs is inconsistent")
    if calculated_supplemental_runs != protocol["supplemental_scored_runs"]:
        problems.append("supplemental_scored_runs is inconsistent")
    calculated_sessions = workflow_count * len(expected_conditions) * protocol["repeats"]
    if calculated_sessions != protocol["total_agent_sessions"]:
        problems.append("total_agent_sessions is inconsistent")

    digest = "sha256:" + hashlib.sha256(CATALOG.read_bytes()).hexdigest()
    print(
        json.dumps(
            {
                "schema_version": payload["schema_version"],
                "workflow_count": workflow_count,
                "primary_task_turns": primary_count,
                "supplemental_task_turns": supplemental_count,
                "primary_scored_runs": calculated_primary_runs,
                "supplemental_scored_runs": calculated_supplemental_runs,
                "total_scored_runs": calculated_primary_runs + calculated_supplemental_runs,
                "total_agent_sessions": calculated_sessions,
                "catalog_sha256": digest,
                "problems": problems,
                "passed": not problems,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(main())
