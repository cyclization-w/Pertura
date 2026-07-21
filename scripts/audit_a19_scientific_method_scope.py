from __future__ import annotations

import argparse
import ast
import json
import subprocess
from pathlib import Path
from typing import Any

from pertura_core.hashing import canonical_hash, file_sha256


DEFAULT_BASELINE = "6e53adb82e91a8076373b71f39b04f0d3af14078"

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
    "src/pertura_bench/capability_availability.py",
    "src/pertura_workflow/environments",
    "benchmarks/paper_v1/task_references.v1.json",
)

_ALLOWED_CHANGED_PATHS = frozenset(
    {
        "benchmarks/paper_v1/agent_tasks.v2.json",
        "scripts/audit_a19_scientific_method_scope.py",
        "scripts/export_h5ad_benchmark_tables.py",
        "scripts/qualify_a19_capability_bindings.py",
        "scripts/refresh_sherlock_a19_checkpoint.sh",
        "scripts/run_a19_final_science_and_canaries.sbatch",
        "src/pertura_bench/paper_capability_bindings.py",
        "src/pertura_bench/resource_evidence.py",
        "src/pertura_workflow/capabilities/specs/target.guide_efficacy.v1.yaml",
        "src/pertura_workflow/capabilities/target_candidates.py",
        "tests/bench/test_a19_scientific_method_parity.py",
        "tests/bench/test_a17_prebench_protocol.py",
        "tests/bench/test_benchmark_protocol.py",
        "tests/bench/test_paper_capability_invocation_bindings.py",
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
            output_contract = dict(task.get("output_contract") or {})
            output_contract.pop("input_semantics", None)
            tasks[task_id] = {
                "depends_on_tasks": task.get("depends_on_tasks") or [],
                "expected_capability_dag": task.get("expected_capability_dag") or [],
                "required_input_roles": task.get("required_input_roles") or [],
                "required_artifact_roles": task.get("required_artifact_roles") or [],
                "output_contract": output_contract,
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
    relative = "src/pertura_bench/paper_capability_bindings.py"
    current_source = (repo / relative).read_text(encoding="utf-8")
    baseline_source = _git(repo, "show", f"{baseline}:{relative}")
    allowed_functions = {
        "build_paper_task_invocation_bindings",
        "_recipe",
        "_papa_targets",
        "_read_tabular_rows",
    }

    def frozen_definitions(source: str) -> dict[str, str]:
        tree = ast.parse(source)
        return {
            node.name: ast.dump(node, include_attributes=False)
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            and node.name not in allowed_functions
        }

    if frozen_definitions(current_source) != frozen_definitions(baseline_source):
        return False
    required_markers = (
        'resolutions=[0.5, 1.0, 1.5]',
        'seeds=[1729, 1730, 1731]',
        'mapping_probability_threshold=0.60',
        'selection_path=_task_alias(',
        'layer_scale="log_normalized"',
        'targets=_papa_targets(',
        '"evaluation_split"',
        '"cell_metadata"',
    )
    return all(marker in current_source for marker in required_markers)


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
    papa04 = next(
        task
        for workflow in current_tasks["workflows"]
        for task in workflow["turns"]
        if task["task_id"] == "PAPA-04"
    )
    expected_input_semantics = {
        "target_expression": {
            "source_layer": "X",
            "normalization": "library_size",
            "target_sum": 10000,
            "transform": "log1p",
            "normalization_universe": "all_features_in_source_layer",
            "gene_aliases": {"PDL1": "CD274"},
        },
        "analysis_cells": {
            "row_scope": "evaluation_selection_intersect_retained_manifest"
        },
    }
    if (
        (papa04.get("output_contract") or {}).get("input_semantics")
        != expected_input_semantics
    ):
        problems.append("PAPA-04 public input semantics are missing or drifted")
    if not _binding_recipe_diff_is_allowed(repo, baseline):
        problems.append(
            "paper capability binding compiler changed outside the frozen "
            "REF-03 and PAPA-04 input recipes"
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
    if changed_keys:
        problems.append(
            f"PAPA-06 changed during the PAPA-04 input repair: {sorted(changed_keys)}"
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
            "PAPA-04.expression_scale": "library_size_10000_log1p",
            "PAPA-04.analysis_cells": (
                "evaluation_selection_intersect_retained_manifest"
            ),
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
