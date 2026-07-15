from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any, Callable

from pertura_core import (
    CapabilityRunRequest,
    DatasetContract,
    DependencyRef,
    ScopeKey,
)
from pertura_core.hashing import canonical_hash, file_sha256, path_sha256
from pertura_workflow.capabilities import CapabilityRegistry
from pertura_workflow.capabilities.executors import execute_capability
from pertura_workflow.environment import environment_lock


SCHEMA_VERSION = "pertura-paper-obs06-v1"
CAPABILITY_ID = "association.sceptre.v1"
REFERENCE_PACK_ID = "REF-06"
OBSERVED_PACK_ID = "OBS-06"
EXPECTED_BINDING_SCHEMA = "pertura-paper-capability-reference-bindings-v1"
EXPECTED_REFERENCE_SCHEMA = "pertura-paper-ref06-v1"


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return payload


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def _reference_tree_sha256(path: Path) -> str:
    """Match the directory digest used by generate_paper_ref06.py."""

    import hashlib

    digest = hashlib.sha256()
    for member in sorted(item for item in path.rglob("*") if item.is_file()):
        digest.update(member.relative_to(path).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(file_sha256(member).encode("ascii"))
        digest.update(b"\0")
    return "sha256:" + digest.hexdigest()


def _require_empty_output(path: Path) -> None:
    if path.exists() and any(path.iterdir()):
        raise ValueError(f"OBS-06 output directory is not empty: {path}")
    path.mkdir(parents=True, exist_ok=True)


def _load_binding(path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    payload = _read_json(path)
    if payload.get("schema_version") != EXPECTED_BINDING_SCHEMA:
        raise ValueError("unsupported capability-reference binding schema")
    if payload.get("passed") is not True or payload.get("problems"):
        raise ValueError("capability-reference bindings did not pass")
    matches = [
        item
        for scenario in payload.get("scenarios") or ()
        if scenario.get("scenario_id") == "CAP-06"
        for item in scenario.get("capability_bindings") or ()
        if item.get("capability_id") == CAPABILITY_ID
    ]
    if len(matches) != 1:
        raise ValueError("OBS-06 requires exactly one CAP-06 SCEPTRE binding")
    binding = matches[0]
    expected = {
        "reference_pack_id": REFERENCE_PACK_ID,
        "target_evidence_level": "passed_planted_reference",
        "scoring_route": "planted_fixture_comparison",
        "release_scope": "primary",
    }
    for key, value in expected.items():
        if binding.get(key) != value:
            raise ValueError(f"SCEPTRE binding {key} drift")
    expected_metrics = {
        "type_i_error",
        "power",
        "fdr",
        "effect_rank_concordance",
        "correct_refusal",
    }
    if set(binding.get("metrics") or ()) != expected_metrics:
        raise ValueError("SCEPTRE binding metric set drift")
    return payload, binding


def _load_reference(ref06_root: Path, binding: dict[str, Any]) -> dict[str, Any]:
    manifest_path = ref06_root / "manifest.json"
    manifest = _read_json(manifest_path)
    if manifest.get("schema_version") != EXPECTED_REFERENCE_SCHEMA:
        raise ValueError("unsupported REF-06 schema")
    if (
        manifest.get("reference_pack_id") != REFERENCE_PACK_ID
        or manifest.get("readiness") != "generated"
        or manifest.get("pending_jobs")
    ):
        raise ValueError("REF-06 is not complete")
    if file_sha256(manifest_path) != binding.get("reference_manifest_sha256"):
        raise ValueError("REF-06 manifest hash disagrees with the capability binding")

    outputs = dict(manifest.get("output_files") or {})
    required = {
        "sceptre_fixture",
        "sceptre_synthetic_truth.tsv",
        "sceptre_reference_results.tsv",
        "sceptre_reference_metrics.json",
        "norman_sceptre_suitability.json",
    }
    if not required.issubset(outputs):
        raise ValueError("REF-06 is missing required outputs")
    for relative in sorted(required - {"sceptre_fixture"}):
        path = ref06_root / relative
        if not path.is_file() or file_sha256(path) != outputs[relative]:
            raise ValueError(f"REF-06 output hash drift: {relative}")
    fixture = ref06_root / "sceptre_fixture"
    if not fixture.is_dir() or _reference_tree_sha256(fixture) != outputs["sceptre_fixture"]:
        raise ValueError("REF-06 SCEPTRE fixture hash drift")
    fixture_manifest = _read_json(fixture / "fixture_manifest.json")
    for name, expected_hash in (fixture_manifest.get("files") or {}).items():
        path = fixture / str(name)
        if not path.is_file() or file_sha256(path) != expected_hash:
            raise ValueError(f"REF-06 fixture file hash drift: {name}")
    return manifest


def _retained_dependency(
    fixture_root: Path, staging: Path
) -> tuple[DependencyRef, dict[str, Any], int]:
    source = fixture_root / "retained_cells.txt"
    cell_ids = [line.strip() for line in source.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not cell_ids or len(cell_ids) != len(set(cell_ids)):
        raise ValueError("REF-06 retained-cell identities are empty or duplicated")
    manifest = staging / "retained_cells.csv"
    manifest.write_text(
        "cell_id,retained\n" + "".join(f"{cell},true\n" for cell in cell_ids),
        encoding="utf-8",
    )
    artifact_hash = file_sha256(manifest)
    result_id = "result_ref06_retained_cells"
    result_hash = canonical_hash(
        {
            "result_id": result_id,
            "artifact_hash": artifact_hash,
            "cell_count": len(cell_ids),
            "scope": "ref06_planted",
        }
    )
    dependency = DependencyRef(
        kind="retained_cell_manifest",
        object_id=result_id,
        object_hash=result_hash,
        role="screen.retained_cells.v1",
    )
    projection = {
        "result_id": result_id,
        "canonical_hash": result_hash,
        "result_kind": "retained_cell_manifest",
        "local_output_paths": [str(manifest)],
        "output_hashes": {manifest.name: artifact_hash},
        "dependency_refs": [{"kind": "retained_cell_manifest"}],
    }
    return dependency, projection, len(cell_ids)


def _design_dependency(dataset_id: str) -> DependencyRef:
    return DependencyRef(
        kind="design_balance",
        object_id=f"result_{dataset_id}_design_balance",
        object_hash=canonical_hash(
            {
                "dataset_id": dataset_id,
                "design_moi": "high",
                "status": "screen_passed",
            }
        ),
        role="diagnostic.design_balance.v1",
    )


def _contract(dataset_id: str, source: Path) -> DatasetContract:
    return DatasetContract(
        dataset_id=dataset_id,
        input_format="csv",
        source_paths=(str(source.resolve()),),
        expression_matrix={"raw_counts_confirmed": True},
        guide_matrix={"cell_by_guide_counts": dataset_id != "norman_k562_crispra_2019"},
        identity_fields={
            "design_moi": {"value": "high", "status": "confirmed"},
            "guide_design": {"value": "combinatorial", "status": "confirmed"},
        },
    )


def _request(
    spec: Any,
    contract: DatasetContract,
    *,
    parameters: dict[str, Any],
    dependencies: tuple[DependencyRef, ...],
    run_id: str,
) -> CapabilityRunRequest:
    return CapabilityRunRequest(
        run_id=run_id,
        capability_id=spec.capability_id,
        capability_version=spec.version,
        contract_id=contract.contract_id,
        contract_hash=contract.canonical_hash,
        scope=ScopeKey(dataset_id=contract.dataset_id),
        objective="OBS-06 planted high-MOI association and Norman correct refusal",
        parameters=parameters,
        dependencies=dependencies,
        requested_at_utc="2026-07-15T00:00:00+00:00",
    )


def _persist_result(staging: Path, result: Any) -> tuple[str, dict[str, str]]:
    payload = result.model_dump(mode="json")
    result_path = staging / "result_envelope.json"
    _write_json(result_path, payload)
    files = {"result_envelope.json": file_sha256(result_path)}
    for relative in result.output_paths:
        path = (staging / str(relative)).resolve()
        if staging.resolve() not in path.parents or not path.is_file():
            raise ValueError(f"capability output escaped staging or is missing: {relative}")
        observed = file_sha256(path)
        expected = result.output_hashes.get(str(relative))
        if observed != expected:
            raise ValueError(f"capability output hash drift: {relative}")
        files[str(relative)] = observed
    return result_path.name, files


def run_observed(
    *,
    bindings_path: Path,
    ref06_root: Path,
    output_root: Path,
    executor: Callable[..., Any] = execute_capability,
    environment_loader: Callable[[str], dict[str, Any]] = environment_lock,
) -> dict[str, Any]:
    _require_empty_output(output_root)
    _, binding = _load_binding(bindings_path)
    reference = _load_reference(ref06_root, binding)
    spec = CapabilityRegistry.load_default(include_external=False).get(CAPABILITY_ID)
    if spec.version != "0.1.0" or spec.executor != "sceptre_association":
        raise ValueError("SCEPTRE capability implementation identity drift")

    environment: dict[str, Any] = {}
    problems: list[str] = []
    try:
        environment = environment_loader("sceptre-v1")
    except Exception as exc:  # preserve a benchmark failure site
        problems.append(f"SCEPTRE environment lock failed: {exc}")

    fixture = ref06_root / "sceptre_fixture"
    planted_staging = output_root / "planted"
    planted_staging.mkdir(parents=True, exist_ok=True)
    retained_dependency, retained_projection, retained_count = _retained_dependency(
        fixture, planted_staging
    )
    _write_json(
        planted_staging / "_dependency_results.json",
        {"results": [retained_projection]},
    )
    planted_contract = _contract("ref06_sceptre_planted", fixture)
    planted_parameters = {
        "moi": "high",
        "response_matrix_path": str((fixture / "response_matrix.csv").resolve()),
        "guide_matrix_path": str((fixture / "guide_matrix.csv").resolve()),
        "guide_target_map_path": str((fixture / "guide_target_map.csv").resolve()),
        "discovery_pairs_path": str((fixture / "discovery_pairs.csv").resolve()),
        "covariates_path": str((fixture / "covariates.csv").resolve()),
        "assignment_method": "mixture",
        "grna_integration_strategy": "union",
        "side": "both",
        "multiple_testing_alpha": float(reference["parameters"]["multiple_testing_alpha"]),
        "calibration_type1_threshold": 0.10,
        "n_calibration_pairs": 500,
        "calibration_group_size": 2,
        "max_memory_gb": 4.0,
        "n_jobs": 1,
        "chunk_rows": 1024,
        "timeout_seconds": int(spec.timeout_seconds),
    }
    planted_request = _request(
        spec,
        planted_contract,
        parameters=planted_parameters,
        dependencies=(
            retained_dependency,
            _design_dependency(planted_contract.dataset_id),
        ),
        run_id="paper-obs06-planted",
    )

    started = time.monotonic()
    planted_result = None
    try:
        planted_result = executor(
            spec,
            planted_request,
            planted_contract,
            planted_staging,
            runtime_context={
                "enforce_dependency_consumption": True,
                "authorized_asset_paths": [str(fixture.resolve())],
            },
        )
    except Exception as exc:  # preserve the complete staging directory
        problems.append(f"planted SCEPTRE execution raised: {exc}")
    planted_seconds = time.monotonic() - started

    planted_files: dict[str, str] = {}
    if planted_result is not None:
        try:
            _, planted_files = _persist_result(planted_staging, planted_result)
        except Exception as exc:
            problems.append(f"planted SCEPTRE output persistence failed: {exc}")

    norman_staging = output_root / "norman_refusal"
    norman_staging.mkdir(parents=True, exist_ok=True)
    norman_contract = _contract("norman_k562_crispra_2019", fixture)
    norman_request = _request(
        spec,
        norman_contract,
        parameters={
            "moi": "high",
            "response_matrix_path": str((fixture / "response_matrix.csv").resolve()),
            # Deliberately omit guide_matrix_path: the frozen Norman source has
            # guide labels but no cell-by-guide count matrix.
            "guide_target_map_path": str((fixture / "guide_target_map.csv").resolve()),
            "discovery_pairs_path": str((fixture / "discovery_pairs.csv").resolve()),
            "max_memory_gb": 4.0,
            "n_jobs": 1,
        },
        dependencies=(),
        run_id="paper-obs06-norman-refusal",
    )
    norman_result = None
    started = time.monotonic()
    try:
        norman_result = executor(
            spec,
            norman_request,
            norman_contract,
            norman_staging,
            runtime_context={
                "enforce_dependency_consumption": True,
                "authorized_asset_paths": [str(fixture.resolve())],
            },
        )
    except Exception as exc:
        problems.append(f"Norman refusal execution raised: {exc}")
    norman_seconds = time.monotonic() - started
    norman_files: dict[str, str] = {}
    if norman_result is not None:
        try:
            _, norman_files = _persist_result(norman_staging, norman_result)
        except Exception as exc:
            problems.append(f"Norman refusal persistence failed: {exc}")

    planted_status = (
        str(getattr(planted_result.status, "value", planted_result.status))
        if planted_result is not None
        else None
    )
    norman_status = (
        str(getattr(norman_result.status, "value", norman_result.status))
        if norman_result is not None
        else None
    )
    consumption_records = (
        list(planted_result.metadata.get("dependency_consumption_records") or ())
        if planted_result is not None
        else []
    )
    retained_records = [
        item
        for item in consumption_records
        if item.get("usage") == "row_filter"
        and item.get("dependency_result_hash") == retained_dependency.object_hash
    ]
    calibration_passed = bool(
        planted_result is not None
        and planted_result.metrics.get("calibration_passed") is True
    )
    norman_blocker = " ".join(
        str(item) for item in (norman_result.blockers if norman_result is not None else ())
    )
    hard_gates = {
        "binding_is_current": True,
        "reference_pack_complete": True,
        "environment_locked": bool(environment.get("lock_hash")),
        "production_executor_completed": planted_status == "completed_with_caution",
        "calibration_passed": calibration_passed,
        "discovery_output_present": "sceptre_results.csv" in planted_files,
        "retained_manifest_applied": bool(
            planted_result is not None
            and planted_result.metadata.get("retained_manifest_applied") is True
        ),
        "retained_artifact_consumed": len(retained_records) == 1
        and int(retained_records[0].get("rows_available") or 0) == retained_count
        and int(retained_records[0].get("rows_consumed") or 0) == retained_count,
        "norman_blocked_before_runner": norman_status == "blocked"
        and "guide_matrix_path is required" in norman_blocker
        and set(norman_files) == {"result_envelope.json"},
        "no_silent_fallback": bool(
            planted_result is not None
            and planted_result.metadata.get("method") == "sceptre_0.99.0"
            and norman_result is not None
            and not norman_result.output_paths
        ),
    }
    if not all(hard_gates.values()):
        problems.extend(
            f"hard gate failed: {name}" for name, passed in hard_gates.items() if not passed
        )

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "observed_pack_id": OBSERVED_PACK_ID,
        "reference_pack_id": REFERENCE_PACK_ID,
        "capability_id": CAPABILITY_ID,
        "capability_version": spec.version,
        "capability_spec_hash": spec.canonical_hash,
        "dependency_policy_hash": canonical_hash(
            dict(spec.metadata.get("dependency_policy") or {})
        ),
        "execution_route": "production_executor_and_validator",
        "execution_hard_gates_passed": not problems and all(hard_gates.values()),
        "scientific_metrics_status": "pending_evaluation",
        "problems": problems,
        "hard_gates": hard_gates,
        "input_files": {
            "capability_reference_bindings": file_sha256(bindings_path),
            "ref06_manifest": file_sha256(ref06_root / "manifest.json"),
            "ref06_fixture": _reference_tree_sha256(fixture),
            "ref06_truth": file_sha256(ref06_root / "sceptre_synthetic_truth.tsv"),
            "ref06_reference_results": file_sha256(
                ref06_root / "sceptre_reference_results.tsv"
            ),
            "norman_suitability": file_sha256(
                ref06_root / "norman_sceptre_suitability.json"
            ),
        },
        "environment": environment,
        "resource_request": {
            "max_memory_gb": 4.0,
            "n_jobs": 1,
            "timeout_seconds": int(spec.timeout_seconds),
        },
        "planted_execution": {
            "status": planted_status,
            "result_id": planted_result.result_id if planted_result is not None else None,
            "result_envelope": "planted/result_envelope.json",
            "files": {
                f"planted/{name}": value for name, value in sorted(planted_files.items())
            },
            "duration_seconds": planted_seconds,
            "dependency_consumption_records": consumption_records,
        },
        "norman_refusal": {
            "status": norman_status,
            "blockers": list(norman_result.blockers) if norman_result is not None else [],
            "result_envelope": "norman_refusal/result_envelope.json",
            "files": {
                f"norman_refusal/{name}": value
                for name, value in sorted(norman_files.items())
            },
            "duration_seconds": norman_seconds,
            "correct_refusal": hard_gates["norman_blocked_before_runner"],
        },
        "limitations": [
            "Positive performance is evaluated on the frozen planted high-MOI REF-06 fixture.",
            "Norman supplies a correct-refusal test because cell-by-guide counts are unavailable.",
            "The exploratory SCEPTRE capability cannot produce an authoritative measured claim.",
        ],
    }
    manifest_path = output_root / "manifest.json"
    _write_json(manifest_path, manifest)
    return {
        "schema_version": "pertura-paper-obs06-execution-validation-v1",
        "observed_pack_id": OBSERVED_PACK_ID,
        "passed": manifest["execution_hard_gates_passed"],
        "problems": problems,
        "hard_gates": hard_gates,
        "output": str(manifest_path),
        "output_sha256": file_sha256(manifest_path),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the production SCEPTRE capability against frozen REF-06 inputs."
    )
    parser.add_argument("--bindings", type=Path, required=True)
    parser.add_argument("--ref06", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = run_observed(
        bindings_path=args.bindings.resolve(),
        ref06_root=args.ref06.resolve(),
        output_root=args.output.resolve(),
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
