from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from pertura_bench.models import BenchmarkArtifactLock, BenchmarkSplitManifest, TargetVerdict
from pertura_bench.operations import fetch_benchmark, finalize_conversion, load_source_manifest, stable_target_split, validate_repository


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def test_all_v020_source_manifests_validate() -> None:
    root = Path(__file__).resolve().parents[2]
    result = validate_repository(root)
    assert result["valid"] is True
    assert result["dataset_count"] == 4


def test_portable_lock_rejects_absolute_paths() -> None:
    with pytest.raises(ValueError, match="absolute paths"):
        BenchmarkArtifactLock(
            dataset_id="d", source_manifest_hash="sha256:" + "1" * 64,
            artifact_sha256="sha256:" + "2" * 64, size_bytes=1,
            parameters={"input": "C:\\data\\raw.h5ad"}, license_status="reviewed",
        )


def test_fetch_detects_size_checksum_and_duplicate_lock(tmp_path: Path) -> None:
    payload = b"fixture"
    md5 = __import__("hashlib").md5(payload).hexdigest()
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps({
        "schema_version": "pertura-benchmark-source-v1",
        "dataset_id": "fixture",
        "file": {"name": "fixture.bin", "download_url": "https://example.test/fixture", "supplied_md5": md5, "size_bytes": len(payload)},
        "intended_uses": ["test"],
        "license_review_url": "https://example.test/license",
    }), encoding="utf-8")
    manifest = load_source_manifest(manifest_path)
    opener = lambda *args, **kwargs: _Response(payload)
    first, _ = fetch_benchmark(manifest, tmp_path / "cache", opener=opener)
    second, _ = fetch_benchmark(manifest, tmp_path / "cache", opener=opener)
    assert first.canonical_hash == second.canonical_hash
    assert first.license_status == "required"
    lock_path = tmp_path / "cache" / "fixture.lock.json"
    tampered = json.loads(lock_path.read_text(encoding="utf-8"))
    tampered["artifact_sha256"] = "sha256:" + "f" * 64
    tampered["canonical_hash"] = ""
    lock_path.write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(ValueError, match="different content"):
        fetch_benchmark(manifest, tmp_path / "cache", opener=opener)


def test_stable_split_is_disjoint_and_deterministic() -> None:
    first = stable_target_split([f"T{i:03d}" for i in range(50)], "crispri")
    second = stable_target_split([f"T{i:03d}" for i in reversed(range(50))], "crispri")
    assert first.canonical_hash == second.canonical_hash
    assert not (set(first.calibration_ids) & set(first.evaluation_ids))
    assert len(first.calibration_ids) == 30
    assert len(first.evaluation_ids) == 20


def test_conversion_lock_binds_script_without_local_paths(tmp_path: Path) -> None:
    output = tmp_path / "converted.h5ad"
    output.write_bytes(b"converted")
    script = tmp_path / "convert.R"
    script.write_text("print('fixture')\n", encoding="utf-8")
    manifest_path = Path(__file__).resolve().parents[2] / "benchmarks" / "manifests" / "papalexi_thp1_eccite.json"
    lock = finalize_conversion(load_source_manifest(manifest_path), output, script, package_versions={"R": "4.5.3"})
    rendered = json.dumps(lock.model_dump(mode="json"))
    assert str(tmp_path) not in rendered
    assert lock.conversion_script_hash.startswith("sha256:")


def test_kang_conversion_uses_native_r_h5ad_writer() -> None:
    root = Path(__file__).resolve().parents[2]
    script = (root / "scripts" / "convert_kang_to_h5ad.R").read_text(
        encoding="utf-8"
    )

    assert "library(anndataR)" in script
    assert "anndataR::write_h5ad(" in script
    assert 'x_mapping = "counts"' in script
    assert 'writer = "anndataR::write_h5ad"' in script
    assert "packages = list(" in script
    assert "packages = c(" not in script
    assert "zellkonverter" not in script


def test_papalexi_conversion_writes_mapping_sidecar() -> None:
    root = Path(__file__).resolve().parents[2]
    script = (root / "scripts" / "convert_papalexi_to_h5ad.R").read_text(
        encoding="utf-8"
    )

    assert 'schema_version = "pertura-benchmark-conversion-sidecar-v1"' in script
    assert 'writer = "SeuratDisk::Convert"' in script
    assert "packages = as.list(c(" in script
    assert "if (!file.exists(output))" in script


def test_expert_split_minimums_and_proxy_validation_fail_closed() -> None:
    with pytest.raises(ValueError, match="at least 50"):
        BenchmarkSplitManifest(
            modality="crispri", calibration_ids=("a",), evaluation_ids=("b",), label_class="expert_adjudicated"
        )
    with pytest.raises(ValueError, match="never be production validated"):
        TargetVerdict(
            modality="crispri", dataset_id="d", target_id="T", expected_direction="down",
            verdict="screen_passed", reason_codes=("published",), label_source="published_proxy",
            validated=True, doi="10.1/test", supplement="table", importer_version="1",
            importer_hash="sha256:" + "1" * 64,
        )


def test_license_cannot_be_marked_reviewed_without_human_attestation(
    tmp_path: Path,
) -> None:
    manifest_path = tmp_path / "reviewed.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": "pertura-benchmark-source-v1",
                "dataset_id": "fixture-reviewed",
                "source": "manual",
                "intended_uses": ["test"],
                "license_review_url": "https://example.test/license",
                "license_status": "reviewed",
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="require reviewer and review basis"):
        load_source_manifest(manifest_path)
