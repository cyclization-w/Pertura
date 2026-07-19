from __future__ import annotations

import argparse
import json
from pathlib import Path

from pertura_runtime.claude.tools.product_tools import PRODUCT_TOOL_NAMES
from pertura_workflow.capabilities import CapabilityRegistry
from pertura_workflow.planner import build_capability_contract_catalog


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate the answer-free a19 capability contract catalog."
    )
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--task-catalog",
        type=Path,
        default=(
            Path(__file__).resolve().parents[1]
            / "benchmarks/paper_v1/agent_tasks.v2.json"
        ),
    )
    args = parser.parse_args()

    registry = CapabilityRegistry.load_default(include_external=False)
    catalog = build_capability_contract_catalog(registry)
    task_dependency_gaps = _task_dependency_gaps(args.task_catalog, registry)
    if len(PRODUCT_TOOL_NAMES) != 5:
        raise RuntimeError("a19 P0 changed the five-tool public surface")
    if len(task_dependency_gaps) != 26:
        raise RuntimeError(
            "a19 frozen task dependency gaps drifted: expected 26, observed "
            f"{len(task_dependency_gaps)}"
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(catalog, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "schema_version": catalog["schema_version"],
                "capability_count": catalog["capability_count"],
                "active_capability_count": catalog[
                    "active_capability_count"
                ],
                "catalog_hash": catalog["catalog_hash"],
                "mcp_tool_count": len(PRODUCT_TOOL_NAMES),
                "task_dependency_gap_count": len(task_dependency_gaps),
                "output": str(args.output.resolve()),
                "passed": True,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def _task_dependency_gaps(
    path: Path, registry: CapabilityRegistry
) -> set[tuple[str, str, str]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    gaps: set[tuple[str, str, str]] = set()
    for workflow in payload["workflows"]:
        earlier_candidates: set[str] = set()
        for task in workflow["turns"]:
            current = set(task.get("expected_capability_dag") or ())
            for capability_id in current:
                for dependency in registry.get(capability_id).depends_on:
                    if dependency not in current | earlier_candidates:
                        gaps.add(
                            (
                                str(workflow["workflow_id"]),
                                capability_id,
                                dependency,
                            )
                        )
            earlier_candidates.update(current)
    return gaps


if __name__ == "__main__":
    raise SystemExit(main())
