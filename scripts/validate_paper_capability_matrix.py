from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MATRIX = ROOT / "benchmarks" / "paper_v1" / "capability_matrix.v1.json"
SPECS = ROOT / "src" / "pertura_workflow" / "capabilities" / "specs"


def _capability_ids_from_specs() -> set[str]:
    ids: set[str] = set()
    pattern = re.compile(r"^capability_id:\s*([^\s#]+)\s*$", re.MULTILINE)
    for path in sorted(SPECS.glob("*.yaml")):
        match = pattern.search(path.read_text(encoding="utf-8"))
        if match is None:
            raise ValueError(f"missing capability_id in {path}")
        capability_id = match.group(1)
        if capability_id in ids:
            raise ValueError(f"duplicate capability spec: {capability_id}")
        ids.add(capability_id)
    return ids


def main() -> int:
    payload = json.loads(MATRIX.read_text(encoding="utf-8"))
    expected_levels = set(payload["evidence_levels"])
    observed: list[str] = []
    problems: list[str] = []

    scenarios = payload.get("scenarios") or []
    if len(scenarios) != payload["scope"]["scenario_count"]:
        problems.append("scenario_count does not match the scenario catalog")

    known_datasets = set(payload["scope"]["primary_datasets"]) | set(
        payload["scope"]["supplemental_datasets"]
    )
    for scenario in scenarios:
        scenario_id = str(scenario.get("scenario_id") or "")
        unknown_datasets = set(scenario.get("datasets") or ()) - known_datasets
        if unknown_datasets:
            problems.append(
                f"{scenario_id}: unknown datasets: {sorted(unknown_datasets)}"
            )
        if not scenario.get("scientific_questions"):
            problems.append(f"{scenario_id}: missing scientific_questions")
        if not scenario.get("critical_failures"):
            problems.append(f"{scenario_id}: missing critical_failures")
        for item in scenario.get("capabilities") or ():
            capability_id = str(item.get("capability_id") or "")
            observed.append(capability_id)
            if item.get("target_evidence_level") not in expected_levels:
                problems.append(
                    f"{capability_id}: unsupported target_evidence_level"
                )
            for field in (
                "positive_expectation",
                "negative_expectation",
                "reference",
                "metrics",
            ):
                if not item.get(field):
                    problems.append(f"{capability_id}: missing {field}")

    duplicates = sorted(
        capability_id
        for capability_id in set(observed)
        if observed.count(capability_id) > 1
    )
    if duplicates:
        problems.append(f"duplicate matrix capabilities: {duplicates}")

    spec_ids = _capability_ids_from_specs()
    observed_ids = set(observed)
    missing = sorted(spec_ids - observed_ids)
    extra = sorted(observed_ids - spec_ids)
    if missing:
        problems.append(f"missing capabilities: {missing}")
    if extra:
        problems.append(f"unknown capabilities: {extra}")
    if len(observed) != payload["scope"]["capability_count"]:
        problems.append("capability_count does not match the matrix")

    digest = "sha256:" + hashlib.sha256(MATRIX.read_bytes()).hexdigest()
    print(
        json.dumps(
            {
                "schema_version": payload["schema_version"],
                "scenario_count": len(scenarios),
                "capability_count": len(observed),
                "spec_capability_count": len(spec_ids),
                "matrix_sha256": digest,
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
