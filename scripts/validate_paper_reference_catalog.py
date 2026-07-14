from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "benchmarks" / "paper_v1" / "reference_catalog.v1.json"
CAPABILITY_MATRIX = (
    ROOT / "benchmarks" / "paper_v1" / "capability_matrix.v1.json"
)


def main() -> int:
    payload = json.loads(CATALOG.read_text(encoding="utf-8"))
    matrix = json.loads(CAPABILITY_MATRIX.read_text(encoding="utf-8"))
    known_capabilities = {
        item["capability_id"]
        for scenario in matrix["scenarios"]
        for item in scenario["capabilities"]
    }
    known_datasets = set(matrix["scope"]["primary_datasets"]) | set(
        matrix["scope"]["supplemental_datasets"]
    )

    problems: list[str] = []
    pack_ids: list[str] = []
    capability_ids: list[str] = []
    job_ids: list[str] = []

    packs = payload.get("reference_packs") or []
    for pack in packs:
        pack_id = str(pack.get("reference_pack_id") or "")
        pack_ids.append(pack_id)
        unknown_datasets = set(pack.get("datasets") or ()) - known_datasets
        if unknown_datasets:
            problems.append(
                f"{pack_id}: unknown datasets: {sorted(unknown_datasets)}"
            )
        capabilities = list(pack.get("capabilities") or ())
        capability_ids.extend(capabilities)
        unknown_capabilities = set(capabilities) - known_capabilities
        if unknown_capabilities:
            problems.append(
                f"{pack_id}: unknown capabilities: {sorted(unknown_capabilities)}"
            )
        for field in (
            "reference_types",
            "generator_jobs",
            "independence_rule",
            "output_roles",
            "metric_ids",
            "required_for",
        ):
            if not pack.get(field):
                problems.append(f"{pack_id}: missing {field}")
        if pack.get("readiness") not in {
            "planned",
            "generated",
            "validated",
            "not_available",
        }:
            problems.append(f"{pack_id}: unsupported readiness")
        for job in pack.get("generator_jobs") or ():
            job_id = str(job.get("job_id") or "")
            job_ids.append(job_id)
            for field in ("method", "inputs", "outputs"):
                if not job.get(field):
                    problems.append(f"{job_id}: missing {field}")

    duplicate_packs = sorted(
        pack_id for pack_id in set(pack_ids) if pack_ids.count(pack_id) > 1
    )
    duplicate_jobs = sorted(
        job_id for job_id in set(job_ids) if job_ids.count(job_id) > 1
    )
    duplicate_capabilities = sorted(
        capability_id
        for capability_id in set(capability_ids)
        if capability_ids.count(capability_id) > 1
    )
    if duplicate_packs:
        problems.append(f"duplicate reference packs: {duplicate_packs}")
    if duplicate_jobs:
        problems.append(f"duplicate reference jobs: {duplicate_jobs}")
    if duplicate_capabilities:
        problems.append(
            f"capabilities assigned to multiple primary packs: "
            f"{duplicate_capabilities}"
        )

    missing = sorted(known_capabilities - set(capability_ids))
    extra = sorted(set(capability_ids) - known_capabilities)
    if missing:
        problems.append(f"capabilities without reference pack: {missing}")
    if extra:
        problems.append(f"unknown reference capabilities: {extra}")
    if len(packs) != payload["pack_count"]:
        problems.append("pack_count does not match the catalog")
    if len(capability_ids) != payload["capability_count"]:
        problems.append("capability_count does not match the catalog")

    digest = "sha256:" + hashlib.sha256(CATALOG.read_bytes()).hexdigest()
    print(
        json.dumps(
            {
                "schema_version": payload["schema_version"],
                "pack_count": len(packs),
                "job_count": len(job_ids),
                "capability_count": len(capability_ids),
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
