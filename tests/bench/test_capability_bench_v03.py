from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from pertura_bench.capability_bench import (
    CANDIDATE_CAPABILITIES,
    benchmark_specs,
    coverage_matrix,
    resolve_real_artifact_chain,
    run_protocol_cases,
    scientific_result_digest,
    server_benchmark_plan,
    validate_cases,
)
from pertura_bench.capability_models import CapabilityBenchmarkCase
from pertura_bench.cli import main as benchmark_main
from pertura_bench.models import BenchmarkArtifactLock, BenchmarkSubsetLock
from pertura_bench.operations import require_repo_root, source_manifests
from pertura_core.hashing import canonical_hash, file_sha256


def test_candidate_matrix_uses_current_verdicts_not_code_presence() -> None:
    validation = validate_cases()
    matrix = coverage_matrix()
    assert validation["ok"] is True
    assert validation["candidate_count"] == 35
    assert validation["case_count"] == 210
    assert len(CANDIDATE_CAPABILITIES) == 35
    assert len(matrix.entries) == 35
    assert matrix.code_ready is True
    assert matrix.local_fixture_ready is all(
        entry.local_fixture_ready for entry in matrix.entries
    )
    assert matrix.real_benchmark_ready is False
    assert matrix.release_ready is False
    assert all(len(spec.cases) == 6 for spec in benchmark_specs())
    assert (all(len(entry.current_verdict_ids) == 6 for entry in matrix.entries)
            is matrix.local_fixture_ready)


def test_case_catalog_uses_explicit_narrow_expectations() -> None:
    root = Path(__file__).resolve().parents[2]
    catalog = json.loads(
        (
            root
            / "src"
            / "pertura_bench"
            / "cases"
            / "capability_cases.v1.json"
        ).read_text(encoding="utf-8")
    )
    explicit_fields = {
        "scenario",
        "fixture_id",
        "fixture_version",
        "execution_mode",
        "seed",
        "parameters",
        "expected_statuses",
        "expected_blocker_contains",
        "required_outputs",
        "metrics",
    }
    raw_cases = [
        case
        for capability in catalog["capabilities"]
        for case in capability["cases"]
    ]
    assert len(raw_cases) == 210
    assert all(explicit_fields <= set(case) for case in raw_cases)

    cases = [case for spec in benchmark_specs() for case in spec.cases]
    assert len(cases) == 210
    for case in cases:
        assert len(case.expected_statuses) == 1
        assert case.parameters["fixture_profile"]
        assert case.parameters["fixture_variant"]
        assert len({metric.name for metric in case.metrics}) == len(case.metrics)
        if case.scenario in {"blocked", "planted_failure"}:
            assert case.expected_blocker_contains
        else:
            assert not case.expected_blocker_contains
        if case.expected_statuses[0] not in {"blocked", "exception_blocked"}:
            assert case.metrics
        if case.expected_statuses[0] not in {
            "blocked",
            "exception_blocked",
            "protocol_rejected",
            "stale",
        }:
            assert case.required_outputs


def test_synthetic_verdicts_are_deterministic_and_real_tier_requires_locks(
    tmp_path: Path,
) -> None:
    first = run_protocol_cases("guide.assignment.nb_mixture.v1")
    second = run_protocol_cases("guide.assignment.nb_mixture.v1")
    assert first == second
    assert {item["outcome"] for item in first} == {"passed"}

    root = Path(__file__).resolve().parents[2]
    real = run_protocol_cases(
        "guide.assignment.nb_mixture.v1",
        tier="full_dataset",
        repo_root=root,
        dataset_id="papalexi_thp1_eccite",
        split="evaluation",
        cache=tmp_path,
        output=tmp_path / "verdicts",
    )
    assert {item["outcome"] for item in real} == {"not_configured"}


def test_server_plan_is_scheduler_neutral_and_has_no_manual_placeholders() -> None:
    plan = server_benchmark_plan()
    assert plan.scheduler == "neutral"
    assert set(plan.datasets) == {
        "replogle_k562_essential_2022",
        "papalexi_thp1_eccite",
        "norman_k562_crispra_2019",
        "kang18_8vs8_pbmc",
    }
    assert len(plan.artifacts) == 16
    assert all("resources" in job and "command" in job for job in plan.jobs)
    assert "<" not in json.dumps(plan.model_dump(mode="json"))
    assert ">" not in json.dumps(plan.model_dump(mode="json"))
    jobs = {job["job_id"]: job for job in plan.jobs}
    assert "prepare:replogle_k562_essential_2022:convert" not in jobs
    assert "prepare:norman_k562_crispra_2019:convert" not in jobs
    assert "artifact:replogle_k562_essential_2022:converted" in jobs[
        "prepare:replogle_k562_essential_2022:fetch"
    ]["produces"]
    assert jobs["prepare:replogle_k562_essential_2022:subset:evaluation"][
        "depends_on"
    ] == ["prepare:replogle_k562_essential_2022:fetch"]
    assert jobs["prepare:papalexi_thp1_eccite:subset:evaluation"][
        "depends_on"
    ] == ["prepare:papalexi_thp1_eccite:convert"]
    assert jobs["prepare:papalexi_thp1_eccite:convert"]["depends_on"] == [
        "prepare:papalexi_thp1_eccite:fetch"
    ]
    artifacts = {item["artifact_id"]: item for item in plan.artifacts}
    assert artifacts["artifact:papalexi_thp1_eccite:source"][
        "lock_relative_path"
    ] == "datasets/papalexi_thp1_eccite/source/artifact.lock.json"
    assert artifacts["artifact:papalexi_thp1_eccite:converted"][
        "lock_relative_path"
    ] == "datasets/papalexi_thp1_eccite/converted/artifact.lock.json"


def test_capability_case_rejects_absolute_fixture_identity() -> None:
    with pytest.raises(ValidationError, match="absolute paths"):
        CapabilityBenchmarkCase(
            capability_id="guide.integrity.v1",
            capability_version="0.1.0",
            tier="synthetic_ci",
            scenario="blocked",
            fixture_id="C:/private/fixture",
        )


def test_scientific_digest_excludes_clock_paths_and_runtime_ids() -> None:
    base = {
        "capability_id": "guide.integrity.v1",
        "capability_version": "0.1.0",
        "status": "screen_passed",
        "result_kind": "guide_integrity",
        "source_class": "observed_metadata",
        "scope": {
            "scope_id": "scope_one",
            "canonical_hash": "sha256:" + "1" * 64,
            "dataset_id": "synthetic",
        },
        "blockers": [],
        "cautions": [],
        "metrics": {"overlap": 1.0},
        "output_paths": ["C:/run-a/output.json"],
        "output_hashes": {"C:/run-a/output.json": "sha256:" + "2" * 64},
        "dependencies": [
            {
                "kind": "contract",
                "object_id": "contract_a",
                "object_hash": "sha256:" + "3" * 64,
                "role": "input",
            }
        ],
        "metadata": {
            "path": "C:/run-a/data.csv",
            "validation_status": "synthetic_only",
            "dependency_consumption_records": [
                {
                    "dependency_result_id": "result_a",
                    "dependency_result_hash": "sha256:" + "4" * 64,
                    "dependency_artifact_hash": "sha256:" + "5" * 64,
                    "usage": "row_filter",
                    "consumer_capability_id": "guide.integrity.v1",
                    "rows_available": 10,
                    "rows_consumed": 8,
                    "columns_consumed": 4,
                    "derived_output_hashes": ["sha256:" + "6" * 64],
                }
            ],
        },
        "run_id": "run_a",
        "request_id": "request_a",
        "result_id": "result_a",
        "completed_at_utc": "2026-01-01T00:00:00Z",
    }
    changed = json.loads(json.dumps(base))
    changed.update(
        {
            "run_id": "run_b",
            "request_id": "request_b",
            "result_id": "result_b",
            "completed_at_utc": "2030-01-01T00:00:00Z",
        }
    )
    changed["metadata"]["dependency_consumption_records"][0].update(
        {
            "dependency_result_id": "result_b",
            "dependency_result_hash": "sha256:" + "7" * 64,
        }
    )
    changed["scope"]["scope_id"] = "scope_two"
    changed["dependencies"][0]["object_id"] = "contract_b"
    changed["dependencies"][0]["object_hash"] = "sha256:" + "3" * 64
    changed["output_paths"] = ["/tmp/run-b/output.json"]
    changed["output_hashes"] = {"/tmp/run-b/output.json": "sha256:" + "2" * 64}
    changed["metadata"]["path"] = "/tmp/run-b/data.csv"
    assert scientific_result_digest(base).canonical_hash == scientific_result_digest(
        changed
    ).canonical_hash
    content_changed = json.loads(json.dumps(changed))
    content_changed["dependencies"][0]["object_hash"] = "sha256:" + "4" * 64
    assert scientific_result_digest(base).canonical_hash != scientific_result_digest(
        content_changed
    ).canonical_hash


def test_real_lock_chain_detects_tampering(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    dataset_id = "replogle_k562_essential_2022"
    manifest = source_manifests(root)[dataset_id][1]
    converted = tmp_path / "datasets" / dataset_id / "converted"
    converted.mkdir(parents=True)
    artifact = converted / "artifact.h5ad"
    artifact.write_bytes(b"small locked fixture")
    lock = BenchmarkArtifactLock(
        dataset_id=dataset_id,
        source_manifest_hash=manifest.canonical_hash,
        artifact_sha256=file_sha256(artifact),
        size_bytes=artifact.stat().st_size,
        license_status="reviewed",
    )
    (converted / "artifact.lock.json").write_text(
        json.dumps(lock.model_dump(mode="json")), encoding="utf-8"
    )
    (converted / "artifact.local.json").write_text(
        json.dumps({"artifact_path": str(artifact), "lock_id": lock.lock_id}),
        encoding="utf-8",
    )
    selected, hashes = resolve_real_artifact_chain(
        root,
        dataset_id=dataset_id,
        tier="full_dataset",
        split="evaluation",
        cache=tmp_path,
    )
    assert selected == artifact.resolve()
    assert hashes["artifact_lock"] == lock.canonical_hash
    sidecar_path = converted / "artifact.local.json"
    sidecar_path.write_text(
        json.dumps({"artifact_path": str(artifact), "lock_id": "wrong"}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="sidecar lock identity mismatch"):
        resolve_real_artifact_chain(
            root,
            dataset_id=dataset_id,
            tier="full_dataset",
            split="evaluation",
            cache=tmp_path,
        )
    sidecar_path.write_text(
        json.dumps({"artifact_path": str(artifact), "lock_id": lock.lock_id}),
        encoding="utf-8",
    )
    artifact.write_bytes(b"tampered")
    with pytest.raises(ValueError, match="size mismatch|checksum mismatch"):
        resolve_real_artifact_chain(
            root,
            dataset_id=dataset_id,
            tier="full_dataset",
            split="evaluation",
            cache=tmp_path,
        )


def test_real_lock_chain_rejects_conversion_script_drift(
    tmp_path: Path,
) -> None:
    root = Path(__file__).resolve().parents[2]

    converted_dataset = "papalexi_thp1_eccite"
    converted_manifest = source_manifests(root)[converted_dataset][1]
    converted_root = tmp_path / "datasets" / converted_dataset / "converted"
    converted_root.mkdir(parents=True)
    converted_artifact = converted_root / "artifact.h5ad"
    converted_artifact.write_bytes(b"converted fixture")
    converted_lock = BenchmarkArtifactLock(
        dataset_id=converted_dataset,
        source_manifest_hash=converted_manifest.canonical_hash,
        artifact_sha256=file_sha256(converted_artifact),
        size_bytes=converted_artifact.stat().st_size,
        conversion_script_hash="sha256:" + "0" * 64,
        license_status="reviewed",
    )
    (converted_root / "artifact.lock.json").write_text(
        json.dumps(converted_lock.model_dump(mode="json")), encoding="utf-8"
    )
    (converted_root / "artifact.local.json").write_text(
        json.dumps(
            {
                "artifact_path": str(converted_artifact),
                "lock_id": converted_lock.lock_id,
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="conversion script hash drift"):
        resolve_real_artifact_chain(
            root,
            dataset_id=converted_dataset,
            tier="full_dataset",
            split="evaluation",
            cache=tmp_path,
        )


def test_converted_artifact_binds_source_and_rejects_subset_script_drift(
    tmp_path: Path,
) -> None:
    root = Path(__file__).resolve().parents[2]
    dataset_id = "papalexi_thp1_eccite"
    manifest = source_manifests(root)[dataset_id][1]

    source_root = tmp_path / "datasets" / dataset_id / "source"
    source_root.mkdir(parents=True)
    source_artifact = source_root / "source.tar.gz"
    source_artifact.write_bytes(b"frozen source")
    source_lock = BenchmarkArtifactLock(
        dataset_id=dataset_id,
        source_manifest_hash=manifest.canonical_hash,
        artifact_sha256=file_sha256(source_artifact),
        size_bytes=source_artifact.stat().st_size,
        license_status="required",
    )
    (source_root / "artifact.lock.json").write_text(
        json.dumps(source_lock.model_dump(mode="json")), encoding="utf-8"
    )
    (source_root / "artifact.local.json").write_text(
        json.dumps(
            {"artifact_path": str(source_artifact), "lock_id": source_lock.lock_id}
        ),
        encoding="utf-8",
    )

    converted_root = tmp_path / "datasets" / dataset_id / "converted"
    converted_root.mkdir(parents=True)
    converted_artifact = converted_root / "artifact.h5ad"
    converted_artifact.write_bytes(b"converted")

    def write_converted_lock(upstream_lock_hash: str) -> None:
        converted_lock = BenchmarkArtifactLock(
            dataset_id=dataset_id,
            source_manifest_hash=manifest.canonical_hash,
            artifact_sha256=file_sha256(converted_artifact),
            size_bytes=converted_artifact.stat().st_size,
            upstream_lock_hash=upstream_lock_hash,
            conversion_script_hash=file_sha256(root / manifest.conversion),
            license_status="required",
        )
        (converted_root / "artifact.lock.json").write_text(
            json.dumps(converted_lock.model_dump(mode="json")), encoding="utf-8"
        )
        (converted_root / "artifact.local.json").write_text(
            json.dumps(
                {
                    "artifact_path": str(converted_artifact),
                    "lock_id": converted_lock.lock_id,
                }
            ),
            encoding="utf-8",
        )

    write_converted_lock("sha256:" + "f" * 64)
    with pytest.raises(ValueError, match="not bound to the current source lock"):
        resolve_real_artifact_chain(
            root,
            dataset_id=dataset_id,
            tier="full_dataset",
            split="evaluation",
            cache=tmp_path,
        )

    write_converted_lock(source_lock.canonical_hash)
    selected, hashes = resolve_real_artifact_chain(
        root,
        dataset_id=dataset_id,
        tier="full_dataset",
        split="evaluation",
        cache=tmp_path,
    )
    assert selected == converted_artifact.resolve()
    assert hashes["source_artifact_lock"] == source_lock.canonical_hash

    direct_dataset = "replogle_k562_essential_2022"
    direct_manifest = source_manifests(root)[direct_dataset][1]
    direct_root = tmp_path / "datasets" / direct_dataset / "converted"
    direct_root.mkdir(parents=True)
    direct_artifact = direct_root / "artifact.h5ad"
    direct_artifact.write_bytes(b"direct fixture")
    direct_lock = BenchmarkArtifactLock(
        dataset_id=direct_dataset,
        source_manifest_hash=direct_manifest.canonical_hash,
        artifact_sha256=file_sha256(direct_artifact),
        size_bytes=direct_artifact.stat().st_size,
        license_status="reviewed",
    )
    (direct_root / "artifact.lock.json").write_text(
        json.dumps(direct_lock.model_dump(mode="json")), encoding="utf-8"
    )
    (direct_root / "artifact.local.json").write_text(
        json.dumps(
            {"artifact_path": str(direct_artifact), "lock_id": direct_lock.lock_id}
        ),
        encoding="utf-8",
    )
    subset_root = tmp_path / "datasets" / direct_dataset / "subset" / "evaluation"
    subset_root.mkdir(parents=True)
    subset_artifact = subset_root / "artifact.h5ad"
    subset_artifact.write_bytes(b"subset fixture")
    subset_lock = BenchmarkSubsetLock(
        dataset_id=direct_dataset,
        subset_spec_hash="sha256:" + "1" * 64,
        source_lock_hash=direct_lock.canonical_hash,
        output_sha256=file_sha256(subset_artifact),
        n_cells=2,
        n_genes=3,
        subset_script_hash="sha256:" + "0" * 64,
    )
    (subset_root / "subset.lock.json").write_text(
        json.dumps(subset_lock.model_dump(mode="json")), encoding="utf-8"
    )
    (subset_root / "subset.local.json").write_text(
        json.dumps(
            {
                "artifact_path": str(subset_artifact),
                "lock_id": subset_lock.subset_lock_id,
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="subset lock script hash drift"):
        resolve_real_artifact_chain(
            root,
            dataset_id=direct_dataset,
            tier="frozen_subset",
            split="evaluation",
            cache=tmp_path,
        )


def test_subset_from_lock_chain_uses_portable_spec_and_persists_sidecars(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import pertura_bench.cli as bench_cli
    import pertura_bench.real_execution as real_execution

    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / "pyproject.toml").write_text(
        "[project]\nname='pertura'\nversion='0.2.0a4'\n", encoding="utf-8"
    )
    dataset_id = "fixture_dataset"
    spec_dir = repo / "benchmarks" / "subsets"
    spec_dir.mkdir(parents=True)
    (spec_dir / f"{dataset_id}.evaluation.json").write_text(
        json.dumps(
            {
                "schema_version": "pertura-benchmark-subset-spec-v2",
                "split_id": "fixture-evaluation-v2",
                "unit_id_column": "replicate",
                "group_column": "condition",
                "strata_columns": ["replicate"],
                "control_selector": {
                    "column": "condition",
                    "op": "eq",
                    "value": "control",
                },
                "selected_groups": ["perturbed"],
                "selected_control_units": ["r2", "r4"],
                "max_cells_per_stratum": 10,
                "minimum_units_per_arm": 2,
                "seed": 1729,
            }
        ),
        encoding="utf-8",
    )
    cache = tmp_path / "cache"
    source = cache / "source.h5ad"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"source")
    source_lock_hash = "sha256:" + "a" * 64
    monkeypatch.setattr(
        real_execution,
        "resolve_real_artifact_chain",
        lambda *args, **kwargs: (source, {"artifact_lock": source_lock_hash}),
    )
    captured: dict[str, object] = {}

    def fake_subset(input_path: Path, output_path: Path, spec: object) -> BenchmarkSubsetLock:
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"subset")
        captured.update(
            {"input": Path(input_path), "output": output, "spec": spec}
        )
        return BenchmarkSubsetLock(
            dataset_id=dataset_id,
            subset_spec_hash=spec.canonical_hash,
            source_lock_hash=spec.source_lock_hash,
            output_sha256=file_sha256(output),
            n_cells=2,
            n_genes=3,
            subset_script_hash="sha256:" + "b" * 64,
        )

    monkeypatch.setattr(bench_cli, "subset_h5ad", fake_subset)
    assert (
        benchmark_main(
            [
                "subset",
                dataset_id,
                "--split",
                "evaluation",
                "--cache",
                str(cache),
                "--repo",
                str(repo),
                "--from-lock-chain",
            ]
        )
        == 0
    )
    expected_root = cache / "datasets" / dataset_id / "subset" / "evaluation"
    assert captured["input"] == source
    assert captured["output"] == expected_root / "artifact.h5ad"
    assert captured["spec"].source_lock_hash == source_lock_hash
    lock_payload = json.loads(
        (expected_root / "subset.lock.json").read_text(encoding="utf-8")
    )
    sidecar_payload = json.loads(
        (expected_root / "subset.local.json").read_text(encoding="utf-8")
    )
    assert lock_payload["source_lock_hash"] == source_lock_hash
    assert sidecar_payload["lock_id"] == lock_payload["subset_lock_id"]
    assert Path(sidecar_payload["artifact_path"]) == expected_root / "artifact.h5ad"

def test_repo_root_guard_points_outer_checkout_to_inner_repo(tmp_path: Path) -> None:
    nested = tmp_path / "pertura"
    nested.mkdir()
    (nested / "pyproject.toml").write_text(
        "[project]\nname='pertura'\nversion='0.0.0'\n", encoding="utf-8"
    )
    with pytest.raises(ValueError, match="detected nested Pertura checkout"):
        require_repo_root(tmp_path)


def test_real_lock_chain_rejects_overlapping_calibration_and_evaluation_cells(
    tmp_path: Path,
) -> None:
    import pertura_bench.operations as benchmark_operations

    root = Path(__file__).resolve().parents[2]
    dataset_id = "replogle_k562_essential_2022"
    manifest = source_manifests(root)[dataset_id][1]
    converted = tmp_path / "datasets" / dataset_id / "converted"
    converted.mkdir(parents=True)
    artifact = converted / "artifact.h5ad"
    artifact.write_bytes(b"locked full artifact")
    artifact_lock = BenchmarkArtifactLock(
        dataset_id=dataset_id,
        source_manifest_hash=manifest.canonical_hash,
        artifact_sha256=file_sha256(artifact),
        size_bytes=artifact.stat().st_size,
        license_status="reviewed",
    )
    (converted / "artifact.lock.json").write_text(
        json.dumps(artifact_lock.model_dump(mode="json")),
        encoding="utf-8",
    )
    (converted / "artifact.local.json").write_text(
        json.dumps(
            {
                "artifact_path": str(artifact),
                "lock_id": artifact_lock.lock_id,
            }
        ),
        encoding="utf-8",
    )

    selections = {
        "calibration": ["cell-a", "cell-overlap"],
        "evaluation": ["cell-overlap", "cell-b"],
    }
    for split, cell_ids in selections.items():
        subset_root = (
            tmp_path / "datasets" / dataset_id / "subset" / split
        )
        subset_root.mkdir(parents=True)
        subset = subset_root / "artifact.h5ad"
        subset.write_bytes(f"subset-{split}".encode("utf-8"))
        selection = subset_root / "selection.ids.json"
        selection.write_text(
            json.dumps(cell_ids, indent=2) + "\n",
            encoding="utf-8",
        )
        lock = BenchmarkSubsetLock(
            dataset_id=dataset_id,
            subset_spec_hash="sha256:" + ("1" if split == "calibration" else "2") * 64,
            source_lock_hash=artifact_lock.canonical_hash,
            output_sha256=file_sha256(subset),
            n_cells=len(cell_ids),
            n_genes=3,
            subset_script_hash=file_sha256(Path(benchmark_operations.__file__)),
            selected_ids_sha256=canonical_hash(cell_ids),
            selection_manifest_sha256=file_sha256(selection),
        )
        (subset_root / "subset.lock.json").write_text(
            json.dumps(lock.model_dump(mode="json")),
            encoding="utf-8",
        )
        (subset_root / "subset.local.json").write_text(
            json.dumps(
                {
                    "artifact_path": str(subset),
                    "lock_id": lock.subset_lock_id,
                }
            ),
            encoding="utf-8",
        )

    with pytest.raises(ValueError, match="overlap by 1 cell identities"):
        resolve_real_artifact_chain(
            root,
            dataset_id=dataset_id,
            tier="frozen_subset",
            split="evaluation",
            cache=tmp_path,
        )
