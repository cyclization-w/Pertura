from __future__ import annotations

import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

from pertura_bench.models import BenchmarkArtifactLock, BenchmarkSplitManifest, TargetVerdict
from pertura_bench.operations import fetch_benchmark, finalize_conversion, load_source_manifest, run_conversion, stable_target_split, validate_repository


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


def test_mixed_source_conversion_uses_distinct_bound_locks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = b"frozen source package"
    md5 = __import__("hashlib").md5(payload).hexdigest()
    script = tmp_path / "convert.R"
    script.write_text("print('fixture')\n", encoding="utf-8")
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": "pertura-benchmark-source-v1",
                "dataset_id": "mixed_fixture",
                "file": {
                    "name": "source.tar.gz",
                    "download_url": "https://example.test/source.tar.gz",
                    "supplied_md5": md5,
                    "size_bytes": len(payload),
                },
                "conversion": script.name,
                "intended_uses": ["test"],
                "license_review_url": "https://example.test/license",
            }
        ),
        encoding="utf-8",
    )
    manifest = load_source_manifest(manifest_path)
    cache = tmp_path / "cache"
    source_lock, source_path = fetch_benchmark(
        manifest, cache, opener=lambda *args, **kwargs: _Response(payload)
    )

    captured: dict[str, object] = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        destination = Path(command[2])
        destination.write_bytes(b"converted")
        destination.with_suffix(destination.suffix + ".manifest.json").write_text(
            json.dumps({"packages": {"R": "4.5.3"}}), encoding="utf-8"
        )
        return type("Completed", (), {"returncode": 0, "stderr": ""})()

    monkeypatch.setattr("pertura_bench.operations.subprocess.run", fake_run)
    converted_lock, converted_path = run_conversion(
        manifest,
        repo_root=tmp_path,
        cache=cache,
        rscript="Rscript",
    )

    assert source_path == cache / "datasets/mixed_fixture/source/source.tar.gz"
    assert converted_path == cache / "datasets/mixed_fixture/converted/artifact.h5ad"
    assert captured["command"][-1] == str(source_path.resolve())
    assert converted_lock.upstream_lock_hash == source_lock.canonical_hash
    assert (source_path.parent / "artifact.lock.json").is_file()
    assert (converted_path.parent / "artifact.lock.json").is_file()


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
    lock = finalize_conversion(
        load_source_manifest(manifest_path),
        output,
        script,
        package_versions={"R": "4.5.3"},
        upstream_lock_hash="sha256:" + "9" * 64,
    )
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
    assert "library(anndataR)" in script
    assert "anndataR::write_h5ad(" in script
    assert 'x_mapping = "counts"' in script
    assert 'layers_mapping = c(data = "data")' in script
    assert 'writer = "anndataR::write_h5ad"' in script
    assert 'SeuratData = "3e51f44303069b64f5dc4d68e6a3d4a343f55c39"' in script
    assert "source_commits = as.list(observed_commits)" in script
    assert "install.packages(source_package, repos = NULL, type = \"source\")" in script
    assert "InstallData(" not in script
    assert "library(SeuratDisk)" not in script
    assert "SaveH5Seurat(" not in script
    assert "Convert(temporary" not in script
    assert "expected_source_md5" in script
    assert "expected_source_sha256" in script
    assert "packages = list(" in script
    assert 'R = paste(R.version$major, R.version$minor, sep = ".")' in script
    assert "if (!file.exists(output))" in script


def test_papalexi_guide_asset_export_is_source_bound_and_sparse() -> None:
    root = Path(__file__).resolve().parents[2]
    script = (root / "scripts" / "export_papalexi_guide_assets.R").read_text(
        encoding="utf-8"
    )

    assert 'expected_source_md5 <- "4884b7c5175a9e88dfe0d16f17965d43"' in script
    assert (
        'expected_source_sha256 <- "ed137f933f93c416b4480e970bd6937505c20c7dccaee0244b3c94d2c8f0ba1e"'
        in script
    )
    assert 'LayerData(object = object[["GDO"]], layer = "counts")' in script
    assert "Matrix::writeMM(guide_counts, matrix_path)" in script
    assert '"guide_matrix/matrix.mtx"' in script
    assert '"guide_matrix/barcodes.tsv"' in script
    assert '"guide_matrix/features.tsv"' in script
    assert '"rna_barcodes.tsv"' in script
    assert '"guide_map.tsv"' in script
    assert '"cell_metadata.tsv"' in script
    assert "identical(colnames(rna_counts), colnames(guide_counts))" in script
    assert 'requireNamespace("thp1.eccite.SeuratData", quietly = TRUE)' in script
    assert 'SeuratData::LoadData(ds = "thp1.eccite")' in script
    assert "install.packages(" not in script
    assert "InstallData(" not in script
    assert "download.file(" not in script


def test_h5ad_benchmark_table_export_is_backed_bounded_and_hashes_outputs() -> None:
    root = Path(__file__).resolve().parents[2]
    script = (root / "scripts" / "export_h5ad_benchmark_tables.py").read_text(
        encoding="utf-8"
    )

    assert 'ad.read_h5ad(source, backed="r")' in script
    assert "selected expression estimate" in script
    assert 'output / "cell_metadata.tsv"' in script
    assert 'output / "target_expression.tsv"' in script
    assert '"source_sha256": _sha256(source)' in script
    assert "data.X if args.layer == \"X\" else data.layers[args.layer]" in script


def test_h5ad_benchmark_table_export_writes_selected_portable_tables(
    tmp_path: Path,
) -> None:
    ad = pytest.importorskip("anndata")
    np = pytest.importorskip("numpy")
    pd = pytest.importorskip("pandas")
    root = Path(__file__).resolve().parents[2]
    source = tmp_path / "fixture.h5ad"
    output = tmp_path / "exported"
    guide_map = tmp_path / "guide_map.tsv"
    data = ad.AnnData(
        X=np.asarray([[1, 2], [3, 4]], dtype=np.float32),
        obs=pd.DataFrame(
            {"condition": ["control", "target"], "replicate": ["r1", "r1"]},
            index=["c1", "c2"],
        ),
        var=pd.DataFrame(index=["GENE1", "CD274"]),
    )
    data.write_h5ad(source)
    guide_map.write_text(
        "guide\ttarget\tmapping_source\n"
        "g1\tGENE1\tobserved_assignment\n"
        "g2\tPDL1\tfeature_name_rule\n"
        "g3\teGFP\tfeature_name_rule\n",
        encoding="utf-8",
    )

    completed = subprocess.run(
        [
            sys.executable,
            str(root / "scripts" / "export_h5ad_benchmark_tables.py"),
            str(source),
            str(output),
            "--obs-column",
            "condition",
            "--obs-column",
            "replicate",
            "--expression-genes-file",
            str(guide_map),
            "--gene-alias",
            "PDL1=CD274",
            "--exclude-gene",
            "eGFP",
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    metadata = pd.read_csv(output / "cell_metadata.tsv", sep="\t")
    expression = pd.read_csv(output / "target_expression.tsv", sep="\t")
    manifest = json.loads(
        (output / "benchmark_tables_manifest.json").read_text(encoding="utf-8")
    )
    assert list(metadata.columns) == ["cell_id", "condition", "replicate"]
    assert list(expression.columns) == ["cell_id", "GENE1", "CD274"]
    assert manifest["expression_columns"] == ["GENE1", "CD274"]
    assert set(manifest["files"]) == {"cell_metadata.tsv", "target_expression.tsv"}


def test_server_agent_cases_match_observed_dataset_semantics() -> None:
    root = Path(__file__).resolve().parents[2]
    catalog = json.loads(
        (root / "src/pertura_bench/cases/server_agent_cases.v1.json").read_text(
            encoding="utf-8"
        )
    )
    cases = {item["case_id"]: item for item in catalog["cases"]}

    replogle_qc = cases["agent_replogle_qc"]
    assert set(replogle_qc["expected_capability_dag"]) == {
        "intake.materialize.v1",
        "diagnostic.dataset_integrity.v1",
    }
    assert "guide.assignment.nb_mixture.v1" not in replogle_qc["expected_capability_dag"]

    papalexi = cases["agent_papalexi_target"]
    assert {
        "guide_matrix",
        "guide_map",
        "rna_barcodes",
        "cell_metadata",
    }.issubset(papalexi["required_artifact_roles"])
    assert "target.reliability.aggregate.v1" in papalexi["expected_capability_dag"]

    norman = cases["agent_norman_design"]
    assert "association.sceptre.v1" not in norman["expected_capability_dag"]
    assert "predefined dual-sgRNA" in norman["objective"]

    kang = cases["agent_kang_design"]
    assert "de.pseudobulk.edger.v1" not in kang["expected_capability_dag"]
    assert "not Perturb-seq" in kang["objective"]

    kang_propeller = cases["agent_kang_propeller"]
    assert kang_propeller["expected_capability_dag"] == [
        "diagnostic.design_balance.v1",
        "composition.propeller.v1",
    ]
    assert "state.reference.map_knn.v1" not in kang_propeller["expected_capability_dag"]


def test_real_capability_policy_matches_available_artifacts() -> None:
    root = Path(__file__).resolve().parents[2]
    catalog = json.loads(
        (root / "src/pertura_bench/cases/capability_cases.v1.json").read_text(
            encoding="utf-8"
        )
    )
    datasets = {
        item["capability_id"]: set(item["required_real_datasets"])
        for item in catalog["capabilities"]
    }

    assert datasets["association.sceptre.v1"] == set()
    assert datasets["guide.ambient.v1"] == set()
    for capability_id in (
        "guide.integrity.v1",
        "guide.assignment.nb_mixture.v1",
        "screen.moi_doublet.v1",
        "screen.retained_cells.v1",
        "target.guide_efficacy.v1",
        "target.reliability.aggregate.v1",
    ):
        assert datasets[capability_id] == {"papalexi_thp1_eccite"}
    assert datasets["diagnostic.design_balance.v1"] == {
        "papalexi_thp1_eccite",
        "kang18_8vs8_pbmc",
    }
    assert datasets["intake.materialize.v1"] == {
        "replogle_k562_essential_2022",
        "papalexi_thp1_eccite",
        "norman_k562_crispra_2019",
        "kang18_8vs8_pbmc",
    }

    policy = json.loads(
        (root / "src/pertura_bench/cases/real_run_policy.v1.json").read_text(
            encoding="utf-8"
        )
    )
    excluded = set(policy["excluded_capabilities"])
    assert "association.sceptre.v1" in excluded
    assert "calibration.method_null.v1" in excluded
    assert {
        "effect.matrix.assemble.v1",
        "effect.module_global.v1",
        "program.response.signed_nmf.v1",
        "program.perturbation.cluster.v1",
    }.issubset(excluded)

    propeller = (
        root
        / "src/pertura_workflow/capabilities/specs/composition.propeller.v1.yaml"
    ).read_text(encoding="utf-8")
    assert "- diagnostic.design_balance.v1" in propeller
    assert "state.reference.map_knn.v1" not in propeller


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
