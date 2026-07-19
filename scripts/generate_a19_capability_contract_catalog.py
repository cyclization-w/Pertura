from __future__ import annotations

import argparse
import json
from pathlib import Path

from pertura_bench.capability_availability import build_task_capability_availability
from pertura_runtime.claude.tools.product_tools import PRODUCT_TOOL_NAMES
from pertura_workflow.capabilities import CapabilityRegistry
from pertura_workflow.planner import build_capability_contract_catalog


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate the answer-free a19 capability contract catalog."
    )
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--availability-output", type=Path)
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
    task_catalog = json.loads(args.task_catalog.read_text(encoding="utf-8"))
    availability = build_task_capability_availability(task_catalog, catalog)
    if len(PRODUCT_TOOL_NAMES) != 5:
        raise RuntimeError("a19 P0 changed the five-tool public surface")
    if any(
        set(record["advertised_capability_ids"])
        & {
            item["capability_id"]
            for item in record["structurally_excluded_capabilities"]
        }
        for record in availability["records"]
    ):
        raise RuntimeError("structurally excluded capabilities were advertised")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(catalog, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    if args.availability_output is not None:
        args.availability_output.parent.mkdir(parents=True, exist_ok=True)
        args.availability_output.write_text(
            json.dumps(availability, indent=2, sort_keys=True) + "\n",
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
                "task_capability_availability_hash": availability["canonical_hash"],
                "advertised_capability_count": sum(
                    len(item["advertised_capability_ids"])
                    for item in availability["records"]
                ),
                "structurally_excluded_capability_count": sum(
                    len(item["structurally_excluded_capabilities"])
                    for item in availability["records"]
                ),
                "availability_output": (
                    str(args.availability_output.resolve())
                    if args.availability_output is not None
                    else None
                ),
                "output": str(args.output.resolve()),
                "passed": True,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
