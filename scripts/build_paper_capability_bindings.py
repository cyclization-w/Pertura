from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "pertura-paper-capability-reference-bindings-v1"
EXPECTED_MATRIX_SCHEMA = "pertura-paper-capability-matrix-v1"
EXPECTED_REFERENCE_SCHEMA = "pertura-paper-reference-catalog-v1"
EXPECTED_INDEX_SCHEMA = "pertura-paper-reference-pack-index-v1"
EVIDENCE_ROUTES = {
    "passed_real_reference": "real_artifact_comparison",
    "passed_planted_reference": "planted_fixture_comparison",
    "passed_protocol": "protocol_hard_gate",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return payload


def _release_scope(
    scenario_id: str,
    datasets: list[str],
    supplemental_datasets: set[str],
) -> str:
    # P5 was frozen as optional/supplemental for this paper. Its controlled
    # fixture validates the protocol but cannot become a primary release gate.
    if scenario_id == "CAP-10":
        return "optional_supplemental"
    if datasets and set(datasets).issubset(supplemental_datasets):
        return "supplemental"
    return "primary"


def build_bindings(
    *,
    matrix_path: Path,
    reference_catalog_path: Path,
    reference_index_path: Path,
    references_root: Path,
    output_path: Path,
) -> dict[str, Any]:
    matrix = _read_json(matrix_path)
    reference_catalog = _read_json(reference_catalog_path)
    reference_index = _read_json(reference_index_path)
    problems: list[str] = []

    if matrix.get("schema_version") != EXPECTED_MATRIX_SCHEMA:
        problems.append("unsupported paper capability matrix schema")
    if reference_catalog.get("schema_version") != EXPECTED_REFERENCE_SCHEMA:
        problems.append("unsupported paper reference catalog schema")
    if reference_index.get("schema_version") != EXPECTED_INDEX_SCHEMA:
        problems.append("unsupported generated reference index schema")
    if reference_index.get("passed") is not True:
        problems.append("generated reference index did not pass")

    index_packs = {
        str(item.get("reference_pack_id")): item
        for item in reference_index.get("reference_packs") or ()
    }
    catalog_packs = {
        str(item.get("reference_pack_id")): item
        for item in reference_catalog.get("reference_packs") or ()
    }
    if len(index_packs) != 10:
        problems.append(f"generated reference index has {len(index_packs)} packs, expected 10")
    if set(index_packs) != set(catalog_packs):
        problems.append("generated and declared reference pack identities disagree")

    capability_to_pack: dict[str, str] = {}
    materialized_packs: dict[str, dict[str, Any]] = {}
    for pack_id, declared in sorted(catalog_packs.items()):
        indexed = index_packs.get(pack_id)
        if indexed is None:
            continue
        pack_root = references_root / pack_id
        manifest_path = pack_root / "manifest.json"
        if not manifest_path.is_file():
            problems.append(f"{pack_id}: generated manifest is missing")
            continue
        manifest_hash = _sha256(manifest_path)
        if manifest_hash != indexed.get("manifest_sha256"):
            problems.append(f"{pack_id}: generated manifest hash disagrees with index")
        manifest = _read_json(manifest_path)
        if manifest.get("reference_pack_id") != pack_id:
            problems.append(f"{pack_id}: generated manifest identity mismatch")
        if manifest.get("readiness") != "generated" or manifest.get("pending_jobs"):
            problems.append(f"{pack_id}: generated pack is not complete")

        outputs = []
        for relative, content_hash in sorted((manifest.get("output_files") or {}).items()):
            path = pack_root / relative
            if not path.exists():
                problems.append(f"{pack_id}: reference output is missing: {relative}")
                continue
            outputs.append(
                {
                    "logical_name": str(relative),
                    "reference_path": f"{pack_id}/{relative}",
                    "content_sha256": str(content_hash),
                    "artifact_kind": "directory" if path.is_dir() else "file",
                }
            )

        capabilities = [str(item) for item in declared.get("capabilities") or ()]
        for capability_id in capabilities:
            previous = capability_to_pack.get(capability_id)
            if previous is not None:
                problems.append(
                    f"{capability_id}: assigned to both {previous} and {pack_id}"
                )
            capability_to_pack[capability_id] = pack_id
        materialized_packs[pack_id] = {
            "reference_pack_id": pack_id,
            "manifest_sha256": manifest_hash,
            "pack_tree_sha256": indexed.get("pack_tree_sha256"),
            "git_commit": indexed.get("git_commit"),
            "datasets": [str(item) for item in declared.get("datasets") or ()],
            "reference_types": [
                str(item) for item in declared.get("reference_types") or ()
            ],
            "required_for": [str(item) for item in declared.get("required_for") or ()],
            "output_roles": [str(item) for item in declared.get("output_roles") or ()],
            "metric_ids": [str(item) for item in declared.get("metric_ids") or ()],
            "reference_artifacts": outputs,
        }

    supplemental_datasets = {
        str(item) for item in matrix.get("scope", {}).get("supplemental_datasets") or ()
    }
    seen_capabilities: set[str] = set()
    scenarios = []
    evidence_counts: Counter[str] = Counter()
    route_counts: Counter[str] = Counter()
    release_counts: Counter[str] = Counter()
    reference_metric_count = 0
    execution_metric_count = 0

    for scenario in matrix.get("scenarios") or ():
        scenario_id = str(scenario.get("scenario_id") or "")
        bindings = []
        for capability in scenario.get("capabilities") or ():
            capability_id = str(capability.get("capability_id") or "")
            if capability_id in seen_capabilities:
                problems.append(f"duplicate matrix capability: {capability_id}")
            seen_capabilities.add(capability_id)
            pack_id = capability_to_pack.get(capability_id)
            if pack_id is None or pack_id not in materialized_packs:
                problems.append(f"{capability_id}: no generated reference pack")
                continue
            pack = materialized_packs[pack_id]
            evidence_level = str(capability.get("target_evidence_level") or "")
            scoring_route = EVIDENCE_ROUTES.get(evidence_level)
            if scoring_route is None:
                problems.append(
                    f"{capability_id}: unsupported evidence level {evidence_level!r}"
                )
                continue
            metrics = [str(item) for item in capability.get("metrics") or ()]
            if not metrics:
                problems.append(f"{capability_id}: no declared metrics")
            pack_metrics = set(pack["metric_ids"])
            reference_metrics = [item for item in metrics if item in pack_metrics]
            execution_metrics = [item for item in metrics if item not in pack_metrics]
            reference_metric_count += len(reference_metrics)
            execution_metric_count += len(execution_metrics)
            release_scope = _release_scope(
                scenario_id, pack["datasets"], supplemental_datasets
            )
            evidence_counts[evidence_level] += 1
            route_counts[scoring_route] += 1
            release_counts[release_scope] += 1
            bindings.append(
                {
                    "capability_id": capability_id,
                    "target_evidence_level": evidence_level,
                    "scoring_route": scoring_route,
                    "release_scope": release_scope,
                    "reference_pack_id": pack_id,
                    "reference_manifest_sha256": pack["manifest_sha256"],
                    "reference_pack_tree_sha256": pack["pack_tree_sha256"],
                    "reference_datasets": pack["datasets"],
                    "reference_types": pack["reference_types"],
                    "reference_output_roles": pack["output_roles"],
                    "reference_artifacts": pack["reference_artifacts"],
                    "metrics": metrics,
                    "reference_catalog_metrics": reference_metrics,
                    "execution_or_hard_gate_metrics": execution_metrics,
                    "positive_expectation": capability.get("positive_expectation"),
                    "negative_expectation": capability.get("negative_expectation"),
                    "reference_description": capability.get("reference"),
                }
            )
        scenarios.append(
            {
                "scenario_id": scenario_id,
                "title": scenario.get("title"),
                "datasets": scenario.get("datasets") or [],
                "scientific_questions": scenario.get("scientific_questions") or [],
                "critical_failures": scenario.get("critical_failures") or [],
                "capability_bindings": bindings,
            }
        )

    declared_capabilities = {
        str(item.get("capability_id"))
        for scenario in matrix.get("scenarios") or ()
        for item in scenario.get("capabilities") or ()
    }
    if seen_capabilities != declared_capabilities:
        problems.append("matrix traversal lost capability identities")
    if set(capability_to_pack) != declared_capabilities:
        missing = sorted(declared_capabilities - set(capability_to_pack))
        extra = sorted(set(capability_to_pack) - declared_capabilities)
        problems.append(
            f"reference assignment mismatch: missing={missing}, extra={extra}"
        )
    expected_scenarios = int(matrix.get("scope", {}).get("scenario_count", 0))
    expected_capabilities = int(matrix.get("scope", {}).get("capability_count", 0))
    if len(scenarios) != expected_scenarios:
        problems.append(
            f"scenario count is {len(scenarios)}, expected {expected_scenarios}"
        )
    if len(seen_capabilities) != expected_capabilities:
        problems.append(
            f"capability count is {len(seen_capabilities)}, expected {expected_capabilities}"
        )

    payload = {
        "schema_version": SCHEMA_VERSION,
        "status": "bound" if not problems else "invalid",
        "passed": not problems,
        "problems": problems,
        "source_hashes": {
            "capability_matrix": _sha256(matrix_path),
            "reference_catalog": _sha256(reference_catalog_path),
            "reference_pack_index": _sha256(reference_index_path),
        },
        "scenario_count": len(scenarios),
        "capability_count": len(seen_capabilities),
        "reference_pack_count": len(materialized_packs),
        "reference_metric_count": reference_metric_count,
        "execution_or_hard_gate_metric_count": execution_metric_count,
        "evidence_level_counts": dict(sorted(evidence_counts.items())),
        "scoring_route_counts": dict(sorted(route_counts.items())),
        "release_scope_counts": dict(sorted(release_counts.items())),
        "release_rule": {
            "primary": "contributes to the primary capability benchmark",
            "supplemental": "reported separately and does not block the primary result",
            "optional_supplemental": "controlled protocol result only; absence of a real prediction bundle does not block primary release",
        },
        "metric_rule": {
            "reference_catalog_metrics": "bound directly to the pack-level scientific reference catalog",
            "execution_or_hard_gate_metrics": "computed from the observed capability execution, artifact identity, or negative-control hard gate",
            "missing_metrics_are_not_passed": True,
        },
        "reference_packs": [materialized_packs[key] for key in sorted(materialized_packs)],
        "scenarios": scenarios,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return {
        "schema_version": "pertura-paper-capability-reference-binding-validation-v1",
        "passed": not problems,
        "problems": problems,
        "scenario_count": len(scenarios),
        "capability_count": len(seen_capabilities),
        "reference_pack_count": len(materialized_packs),
        "reference_metric_count": reference_metric_count,
        "execution_or_hard_gate_metric_count": execution_metric_count,
        "output": str(output_path),
        "output_sha256": _sha256(output_path),
    }


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(
        description="Bind paper capability scenarios to generated reference packs."
    )
    parser.add_argument(
        "--matrix",
        type=Path,
        default=root / "benchmarks" / "paper_v1" / "capability_matrix.v1.json",
    )
    parser.add_argument(
        "--reference-catalog",
        type=Path,
        default=root / "benchmarks" / "paper_v1" / "reference_catalog.v1.json",
    )
    parser.add_argument("--reference-index", type=Path, required=True)
    parser.add_argument("--references-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = build_bindings(
        matrix_path=args.matrix.resolve(),
        reference_catalog_path=args.reference_catalog.resolve(),
        reference_index_path=args.reference_index.resolve(),
        references_root=args.references_root.resolve(),
        output_path=args.output.resolve(),
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
