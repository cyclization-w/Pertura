from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any

from pertura_core.hashing import canonical_hash, file_sha256


DEFAULT_BASELINE = "dc3eaffb15976686db9c6467e594a64a22700852"

_EXACTLY_FROZEN_PATHS = (
    "src/pertura_runtime/product_tools",
    "src/pertura_runtime/product.py",
    "src/pertura_runtime/invocation_bindings.py",
    "src/pertura_runtime/project",
    "src/pertura_runtime/verifier",
    "src/pertura_core/models.py",
    "src/pertura_core/promotion.py",
    "src/pertura_core/receipt_verification.py",
    "src/pertura_core/compatibility/v0.2",
    "src/pertura_bench/paper_agent_execution.py",
    "src/pertura_bench/task_submission.py",
    "src/pertura_bench/resource_evidence.py",
    "src/pertura_bench/capability_availability.py",
    "src/pertura_workflow/environments",
    "benchmarks/paper_v1/task_references.v1.json",
)

_ALLOWED_CHANGED_PATHS = frozenset(
    {
        "benchmarks/paper_v1/agent_tasks.v2.json",
        "scripts/audit_a19_scientific_method_scope.py",
        "scripts/generate_paper_task_references.py",
        "scripts/generate_paper_task_trans_de.R",
        "scripts/qualify_a19_capability_bindings.py",
        "scripts/qualify_a19_scientific_methods.py",
        "scripts/refresh_sherlock_a19_checkpoint.sh",
        "scripts/run_a19_final_science_and_canaries.sbatch",
        "src/pertura_bench/paper_capability_bindings.py",
        "src/pertura_workflow/capabilities/effect_candidates.py",
        "src/pertura_workflow/capabilities/specs/effect.guide_target_sensitivity.v1.yaml",
        "src/pertura_workflow/capabilities/specs/state.reference.fit.v1.yaml",
        "src/pertura_workflow/capabilities/specs/state.reference.map_knn.v1.yaml",
        "src/pertura_workflow/capabilities/specs/target.guide_efficacy.v1.yaml",
        "src/pertura_workflow/capabilities/specs/target.reliability.aggregate.v1.yaml",
        "src/pertura_workflow/capabilities/specs/target.responder.mixscape.v1.yaml",
        "src/pertura_workflow/capabilities/state_candidates.py",
        "src/pertura_workflow/capabilities/target_candidates.py",
        "tests/bench/test_a19_scientific_method_parity.py",
        "tests/bench/test_paper_tasks_v2.py",
        "tests/workflow/test_candidate_capabilities_v03.py",
    }
)


def _git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=repo, text=True)


def _show_json(repo: Path, revision: str, relative: str) -> dict[str, Any]:
    return json.loads(_git(repo, "show", f"{revision}:{relative}"))


def _task_invariants(payload: dict[str, Any]) -> dict[str, Any]:
    tasks = {}
    for workflow in payload.get("workflows") or ():
        for task in workflow.get("turns") or ():
            task_id = str(task["task_id"])
            tasks[task_id] = {
                "depends_on_tasks": task.get("depends_on_tasks") or [],
                "expected_capability_dag": task.get("expected_capability_dag") or [],
                "required_input_roles": task.get("required_input_roles") or [],
                "required_artifact_roles": task.get("required_artifact_roles") or [],
                "output_contract": task.get("output_contract") or {},
                "resources": task.get("resources") or {},
                "execution_mode": task.get("execution_mode"),
                "role": task.get("role"),
            }
    protocol = dict(payload.get("execution_protocol") or {})
    return {
        "task_count": len(tasks),
        "required_scored_turns": protocol.get("required_scored_turns"),
        "required_agent_sessions": protocol.get("required_agent_sessions"),
        "tasks": tasks,
    }


def _binding_recipe_diff_is_allowed(repo: Path, baseline: str) -> bool:
    diff = _git(
        repo,
        "diff",
        "--unified=0",
        baseline,
        "--",
        "src/pertura_bench/paper_capability_bindings.py",
    )
    changed = [
        line
        for line in diff.splitlines()
        if line.startswith(("+", "-")) and not line.startswith(("+++", "---"))
    ]
    allowed = {
        "-            resolutions=[0.5, 1.0, 2.0],",
        "-            seeds=[42],",
        "+            resolutions=[0.5, 1.0, 1.5],",
        "+            seeds=[1729, 1730, 1731],",
        "-            mapping_probability_threshold=0.5,",
        "+            mapping_probability_threshold=0.60,",
    }
    return set(changed) == allowed


def audit(*, repo: Path, task_catalog: Path, baseline: str) -> dict[str, Any]:
    problems: list[str] = []
    try:
        _git(repo, "cat-file", "-e", f"{baseline}^{{commit}}")
    except subprocess.CalledProcessError:
        problems.append(f"frozen baseline commit is unavailable: {baseline}")

    changed_paths = {
        value.strip()
        for value in _git(repo, "diff", "--name-only", baseline).splitlines()
        if value.strip()
    }
    unexpected_paths = sorted(changed_paths - _ALLOWED_CHANGED_PATHS)
    if unexpected_paths:
        problems.append(
            "out-of-scope paths changed relative to the frozen baseline: "
            + ", ".join(unexpected_paths)
        )

    for relative in _EXACTLY_FROZEN_PATHS:
        completed = subprocess.run(
            ["git", "diff", "--quiet", baseline, "--", relative],
            cwd=repo,
            check=False,
        )
        if completed.returncode != 0:
            problems.append(f"out-of-scope frozen surface changed: {relative}")

    baseline_tasks = _show_json(
        repo, baseline, "benchmarks/paper_v1/agent_tasks.v2.json"
    )
    current_tasks = json.loads(task_catalog.read_text(encoding="utf-8"))
    if _task_invariants(current_tasks) != _task_invariants(baseline_tasks):
        problems.append(
            "task DAG, roles, resources, execution modes, or artifact contracts changed"
        )
    if not _binding_recipe_diff_is_allowed(repo, baseline):
        problems.append(
            "paper capability binding compiler changed outside the three frozen "
            "REF-03 parameter values"
        )

    current_p06 = next(
        task
        for workflow in current_tasks["workflows"]
        for task in workflow["turns"]
        if task["task_id"] == "PAPA-06"
    )
    baseline_p06 = next(
        task
        for workflow in baseline_tasks["workflows"]
        for task in workflow["turns"]
        if task["task_id"] == "PAPA-06"
    )
    changed_keys = {
        key
        for key in set(current_p06) | set(baseline_p06)
        if current_p06.get(key) != baseline_p06.get(key)
    }
    if changed_keys != {"codeact_protocol"}:
        problems.append(
            f"PAPA-06 changed outside its public method contract: {sorted(changed_keys)}"
        )

    commit = _git(repo, "rev-parse", "HEAD").strip()
    payload = {
        "schema_version": "pertura-a19-scientific-method-scope-audit-v1",
        "status": "failed" if problems else "passed",
        "passed": not problems,
        "git_commit": commit,
        "baseline_commit": baseline,
        "task_catalog_sha256": file_sha256(task_catalog),
        "frozen_paths": list(_EXACTLY_FROZEN_PATHS),
        "allowed_changed_paths": sorted(_ALLOWED_CHANGED_PATHS),
        "observed_changed_paths": sorted(changed_paths),
        "allowed_binding_parameter_changes": {
            "PAPA-02.resolutions": [0.5, 1.0, 1.5],
            "PAPA-02.seeds": [1729, 1730, 1731],
            "PAPA-03.mapping_probability_threshold": 0.60,
        },
        "problems": problems,
    }
    payload["canonical_hash"] = canonical_hash(payload)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--task-catalog", type=Path, required=True)
    parser.add_argument("--baseline", default=DEFAULT_BASELINE)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    payload = audit(
        repo=args.repo.resolve(),
        task_catalog=args.task_catalog.resolve(),
        baseline=str(args.baseline),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if payload["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
