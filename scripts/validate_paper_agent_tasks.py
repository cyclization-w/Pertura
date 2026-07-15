from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pertura_bench.paper_tasks import (
    load_paper_task_catalog,
    validate_paper_anchor_catalog,
    validate_task_reference_catalog,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--catalog",
        type=Path,
        default=ROOT / "benchmarks" / "paper_v1" / "agent_tasks.v2.json",
    )
    parser.add_argument(
        "--task-references",
        type=Path,
        default=ROOT / "benchmarks" / "paper_v1" / "task_references.v1.json",
    )
    parser.add_argument(
        "--paper-anchors",
        type=Path,
        default=ROOT / "benchmarks" / "paper_v1" / "paper_anchors.v1.json",
    )
    args = parser.parse_args(argv)
    catalog = load_paper_task_catalog(args.catalog, validate=False)
    problems = []
    from pertura_bench.paper_tasks import validate_paper_task_catalog

    problems.extend(validate_paper_task_catalog(catalog.payload))
    references = json.loads(args.task_references.read_text(encoding="utf-8"))
    anchors = json.loads(args.paper_anchors.read_text(encoding="utf-8"))
    tasks = catalog.tasks()
    problems.extend(validate_task_reference_catalog(references, tasks))
    problems.extend(validate_paper_anchor_catalog(anchors, tasks))
    primary = sum(
        1
        for workflow in catalog.workflows
        for task in workflow.get("turns") or ()
        if workflow.get("role") == "primary" and task.get("role") != "optional"
    )
    supplemental = sum(
        1
        for workflow in catalog.workflows
        for task in workflow.get("turns") or ()
        if workflow.get("role") == "supplemental" and task.get("role") != "optional"
    )
    optional = sum(1 for task in tasks if task.get("role") == "optional")
    print(
        json.dumps(
            {
                "schema_version": catalog.payload["schema_version"],
                "workflow_count": len(catalog.workflows),
                "primary_task_turns": primary,
                "supplemental_task_turns": supplemental,
                "optional_task_turns": optional,
                "required_scored_turns": (primary + supplemental) * 3 * 2,
                "required_agent_sessions": len(catalog.workflows) * 3 * 2,
                "catalog_sha256": catalog.sha256,
                "task_reference_sha256": "sha256:"
                + hashlib.sha256(args.task_references.read_bytes()).hexdigest(),
                "paper_anchor_sha256": "sha256:"
                + hashlib.sha256(args.paper_anchors.read_bytes()).hexdigest(),
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
