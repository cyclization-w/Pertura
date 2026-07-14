from __future__ import annotations

import csv
import json
import subprocess
from pathlib import Path

import pytest

from pertura_core import CapabilityRunRequest, DependencyRef, ScopeKey
from pertura_core.hashing import file_sha256
from pertura_runtime.claude.workspace import ClaudeRunWorkspace
from pertura_runtime.product import PerturaProductRuntime
from pertura_workflow.capabilities import CapabilityRegistry
from pertura_workflow.capabilities.edger import (
    _read_counts,
    _validate_edger_outputs,
    run_edger_pseudobulk,
)
from pertura_workflow.intake import inspect_dataset_path


def test_edger_capability_never_installs_environment_during_analysis(monkeypatch, tmp_path: Path) -> None:
    source = tmp_path / "screen"
    source.mkdir()
    (source / "counts.csv").write_text("gene,c1,c2\nG1,1,2\n", encoding="utf-8")
    (source / "metadata.csv").write_text("cell_id,condition,replicate\nc1,target,r1\nc2,baseline,r2\n", encoding="utf-8")
    monkeypatch.setenv("PERTURA_CACHE_DIR", str(tmp_path / "empty-cache"))
    monkeypatch.setenv("PERTURA_AUTHORITY_ROOT", str(tmp_path / "authority"))
    workspace = ClaudeRunWorkspace.create(root=tmp_path / "runs", input_source=source, run_id="edger-missing")
    runtime = PerturaProductRuntime(workspace)
    try:
        contract = runtime.inspect_dataset(confirmations={"control": "baseline", "replicate": "replicate"})
        result = runtime.run_analysis(
            "replicate-aware differential expression",
            capability_id="de.pseudobulk.edger.v1",
            contract_id=contract["contract_id"],
            parameters={
                "counts_path": "counts.csv",
                "metadata_path": "metadata.csv",
                "target_condition": "target",
                "baseline_condition": "baseline",
            },
        )
        assert result["status"] == "blocked"
        assert any("pertura env setup edger-v1" in item for item in result["blockers"])
        assert not (tmp_path / "empty-cache").exists()
    finally:
        runtime.close()


def test_edger_raw_count_contract_rejects_fractional_values(tmp_path: Path) -> None:
    path = tmp_path / "counts.csv"
    path.write_text("gene,c1,c2\nG1,1.5,2\n", encoding="utf-8")
    with pytest.raises(ValueError, match="raw nonnegative integer counts"):
        _read_counts(path, "gene")

_EXPECTED_LOCK = {
    "schema_version": "pertura-environment-manifest-v2",
    "profile": "edger-v1",
    "platform": "test-platform",
    "lock_hash": "sha256:" + "a" * 64,
    "resource_hashes": {"runner": "sha256:" + "b" * 64},
    "expected_versions": {
        "R": "4.5.3",
        "Bioconductor": "3.22",
        "edgeR": "4.8.2",
        "limma": "3.66.0",
    },
    "versions": {
        "R": "4.5.3",
        "Bioconductor": "3.22",
        "edgeR": "4.8.2",
        "limma": "3.66.0",
    },
}


def _write_csv(path: Path, fields: list[str], rows: list[dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _write_valid_edger_outputs(staging: Path) -> None:
    sample_ids = ["s1", "s2", "s3", "s4"]
    _write_csv(
        staging / "pseudobulk_samples.csv",
        ["sample_id", "replicate", "condition", "state", "n_cells"],
        [
            {"sample_id": "s1", "replicate": "b1", "condition": "baseline", "state": "all", "n_cells": 10},
            {"sample_id": "s2", "replicate": "b2", "condition": "baseline", "state": "all", "n_cells": 11},
            {"sample_id": "s3", "replicate": "t1", "condition": "target", "state": "all", "n_cells": 12},
            {"sample_id": "s4", "replicate": "t2", "condition": "target", "state": "all", "n_cells": 13},
        ],
    )
    _write_csv(
        staging / "pseudobulk_counts.csv",
        ["gene", *sample_ids],
        [
            {"gene": "G1", "s1": 10, "s2": 11, "s3": 20, "s4": 21},
            {"gene": "G2", "s1": 8, "s2": 9, "s3": 7, "s4": 8},
        ],
    )
    _write_csv(
        staging / "edger_results.csv",
        ["gene", "logFC", "F", "PValue", "FDR", "dispersion"],
        [
            {"gene": "G1", "logFC": 1.0, "F": 12.0, "PValue": 0.001, "FDR": 0.002, "dispersion": 0.1},
            {"gene": "G2", "logFC": -0.2, "F": 1.5, "PValue": 0.2, "FDR": 0.25, "dispersion": 0.12},
        ],
    )
    _write_csv(
        staging / "design_matrix.csv",
        ["sample_id", "(Intercept)", "conditiontarget"],
        [
            {"sample_id": "s1", "(Intercept)": 1, "conditiontarget": 0},
            {"sample_id": "s2", "(Intercept)": 1, "conditiontarget": 0},
            {"sample_id": "s3", "(Intercept)": 1, "conditiontarget": 1},
            {"sample_id": "s4", "(Intercept)": 1, "conditiontarget": 1},
        ],
    )
    _write_csv(
        staging / "mds.csv",
        ["sample_id", "leading_logFC_1", "leading_logFC_2"],
        [
            {"sample_id": sample_id, "leading_logFC_1": index, "leading_logFC_2": -index}
            for index, sample_id in enumerate(sample_ids)
        ],
    )
    (staging / "r_environment.json").write_text(
        json.dumps(
            {
                "R": "4.5.3",
                "Bioconductor": "3.22",
                "edgeR": "4.8.2",
                "limma": "3.66.0",
                "jsonlite": "2.0.0",
                "sessionInfo": ["R version 4.5.3", "edgeR_4.8.2"],
            }
        ),
        encoding="utf-8",
    )
    (staging / "environment_lock.json").write_text(
        json.dumps(_EXPECTED_LOCK), encoding="utf-8"
    )


def test_edger_output_validation_accepts_complete_bound_outputs(tmp_path: Path) -> None:
    _write_valid_edger_outputs(tmp_path)

    metrics = _validate_edger_outputs(
        tmp_path,
        baseline="baseline",
        target="target",
        expected_environment_lock=_EXPECTED_LOCK,
    )

    assert metrics == {
        "validated_result_gene_count": 2,
        "validated_pseudobulk_sample_count": 4,
        "validated_design_column_count": 2,
    }


@pytest.mark.parametrize(
    "mutation, message",
    [
        ("missing_result_column", "missing required columns"),
        ("duplicate_gene", "duplicate identities"),
        ("nonfinite_result", "not finite"),
        ("invalid_probability", "must lie in"),
        ("duplicate_sample", "duplicate identities"),
        ("condition_scope", "requested contrast"),
        ("counts_alignment", "does not align"),
        ("design_alignment", "does not align"),
        ("design_rank", "not full rank"),
        ("mds_nonfinite", "not finite"),
        ("environment_missing", "missing jsonlite"),
        ("environment_drift", "version mismatch"),
        ("lock_drift", "does not match"),
    ],
)
def test_edger_output_validation_rejects_malformed_outputs(
    tmp_path: Path, mutation: str, message: str
) -> None:
    _write_valid_edger_outputs(tmp_path)
    if mutation == "missing_result_column":
        _write_csv(
            tmp_path / "edger_results.csv",
            ["gene", "logFC", "F", "PValue", "FDR"],
            [{"gene": "G1", "logFC": 1, "F": 2, "PValue": 0.1, "FDR": 0.2}],
        )
    elif mutation == "duplicate_gene":
        rows = list(csv.DictReader((tmp_path / "edger_results.csv").open(encoding="utf-8")))
        rows[1]["gene"] = "G1"
        _write_csv(tmp_path / "edger_results.csv", list(rows[0]), rows)
    elif mutation == "nonfinite_result":
        content = (tmp_path / "edger_results.csv").read_text(encoding="utf-8")
        (tmp_path / "edger_results.csv").write_text(content.replace("G1,1.0", "G1,nan"), encoding="utf-8")
    elif mutation == "invalid_probability":
        content = (tmp_path / "edger_results.csv").read_text(encoding="utf-8")
        (tmp_path / "edger_results.csv").write_text(content.replace(",0.001,", ",1.001,"), encoding="utf-8")
    elif mutation == "duplicate_sample":
        content = (tmp_path / "pseudobulk_samples.csv").read_text(encoding="utf-8")
        (tmp_path / "pseudobulk_samples.csv").write_text(content.replace("s2,b2", "s1,b2"), encoding="utf-8")
    elif mutation == "condition_scope":
        content = (tmp_path / "pseudobulk_samples.csv").read_text(encoding="utf-8")
        (tmp_path / "pseudobulk_samples.csv").write_text(content.replace(",target,", ",other,"), encoding="utf-8")
    elif mutation == "counts_alignment":
        content = (tmp_path / "pseudobulk_counts.csv").read_text(encoding="utf-8")
        (tmp_path / "pseudobulk_counts.csv").write_text(content.replace("gene,s1,s2", "gene,s2,s1"), encoding="utf-8")
    elif mutation == "design_alignment":
        content = (tmp_path / "design_matrix.csv").read_text(encoding="utf-8")
        (tmp_path / "design_matrix.csv").write_text(content.replace("s1,1,0", "other,1,0"), encoding="utf-8")
    elif mutation == "design_rank":
        content = (tmp_path / "design_matrix.csv").read_text(encoding="utf-8")
        (tmp_path / "design_matrix.csv").write_text(content.replace(",1\n", ",0\n"), encoding="utf-8")
    elif mutation == "mds_nonfinite":
        content = (tmp_path / "mds.csv").read_text(encoding="utf-8")
        (tmp_path / "mds.csv").write_text(content.replace("s1,0,0", "s1,inf,0"), encoding="utf-8")
    elif mutation in {"environment_missing", "environment_drift"}:
        payload = json.loads((tmp_path / "r_environment.json").read_text(encoding="utf-8"))
        if mutation == "environment_missing":
            payload.pop("jsonlite")
        else:
            payload["edgeR"] = "0.0.0"
        (tmp_path / "r_environment.json").write_text(json.dumps(payload), encoding="utf-8")
    elif mutation == "lock_drift":
        payload = json.loads((tmp_path / "environment_lock.json").read_text(encoding="utf-8"))
        payload["lock_hash"] = "sha256:" + "0" * 64
        (tmp_path / "environment_lock.json").write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(RuntimeError, match=message):
        _validate_edger_outputs(
            tmp_path,
            baseline="baseline",
            target="target",
            expected_environment_lock=_EXPECTED_LOCK,
        )


@pytest.mark.parametrize(
    ("column", "value", "message"),
    [
        ("F", -0.01, r"F must be nonnegative for gene G1: -0.01"),
        (
            "dispersion",
            -0.01,
            r"dispersion must be nonnegative for gene G1: -0.01",
        ),
    ],
)
def test_edger_output_validation_reports_gene_for_negative_statistics(
    tmp_path: Path, column: str, value: float, message: str
) -> None:
    _write_valid_edger_outputs(tmp_path)
    rows = list(
        csv.DictReader((tmp_path / "edger_results.csv").open(encoding="utf-8"))
    )
    rows[0][column] = str(value)
    _write_csv(tmp_path / "edger_results.csv", list(rows[0]), rows)

    with pytest.raises(RuntimeError, match=message):
        _validate_edger_outputs(
            tmp_path,
            baseline="baseline",
            target="target",
            expected_environment_lock=_EXPECTED_LOCK,
        )


def _write_small_edger_input(source: Path) -> None:
    source.mkdir()
    (source / "counts.csv").write_text(
        "gene,b1,b2,t1,t2\nG1,10,11,20,21\nG2,8,9,7,8\n",
        encoding="utf-8",
    )
    (source / "metadata.csv").write_text(
        "cell_id,condition,replicate\n"
        "b1,baseline,b1\n"
        "b2,baseline,b2\n"
        "t1,target,t1\n"
        "t2,target,t2\n",
        encoding="utf-8",
    )


def _edger_request(contract, spec, dependencies=()):
    return CapabilityRunRequest(
        run_id="edgeR-grounding-test",
        capability_id=spec.capability_id,
        capability_version=spec.version,
        contract_id=contract.contract_id,
        contract_hash=contract.canonical_hash,
        scope=ScopeKey(dataset_id=contract.dataset_id),
        parameters={
            "counts_path": "counts.csv",
            "metadata_path": "metadata.csv",
            "target_condition": "target",
            "baseline_condition": "baseline",
            "minimum_cells_per_pseudobulk": 1,
        },
        dependencies=dependencies,
    )


def test_edger_applies_retained_cell_dependency_before_design(
    monkeypatch, tmp_path: Path
) -> None:
    source = tmp_path / "screen"
    _write_small_edger_input(source)
    contract = inspect_dataset_path(source)
    spec = CapabilityRegistry.load_default(include_external=False).get(
        "de.pseudobulk.edger.v1"
    )
    staging = tmp_path / "staging"
    staging.mkdir()
    manifest = staging / "dependency" / "retained_cells.csv"
    manifest.parent.mkdir()
    manifest.write_text(
        "cell_id,retained\nb1,true\nb2,true\nt1,false\nt2,false\n",
        encoding="utf-8",
    )
    result_id = "result_retained_cells"
    dependency = DependencyRef(
        kind="retained_cell_manifest",
        object_id=result_id,
        object_hash="sha256:" + "1" * 64,
        role="screen.retained_cells.v1:provided",
    )
    (staging / "_dependency_results.json").write_text(
        json.dumps(
            {
                "results": [
                    {
                        "result_id": result_id,
                        "output_hashes": {"retained_cells.csv": file_sha256(manifest)},
                        "local_output_paths": [str(manifest)],
                        "dependency_refs": [
                            {"kind": "retained_cell_manifest", "object_id": result_id}
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "pertura_workflow.capabilities.edger.doctor_environment",
        lambda _profile: {"ok": True, "problems": []},
    )
    monkeypatch.setattr(
        "pertura_workflow.capabilities.edger.environment_lock",
        lambda _profile: _EXPECTED_LOCK,
    )
    monkeypatch.setattr(
        "pertura_workflow.capabilities.edger.subprocess.run",
        lambda *args, **kwargs: pytest.fail("R must not run for a retained-cell-invalid design"),
    )

    result = run_edger_pseudobulk(
        spec, _edger_request(contract, spec, (dependency,)), contract, staging
    )

    assert result.status.value == "blocked"
    assert result.metrics["retained_manifest_applied"] is True
    assert result.metrics["retained_manifest_cell_count"] == 2
    assert result.metrics["selected_retained_cell_count"] == 2
    assert any("independent units" in blocker for blocker in result.blockers)


def test_edger_without_retained_dependency_preserves_direct_runner_behavior(
    monkeypatch, tmp_path: Path
) -> None:
    source = tmp_path / "screen"
    _write_small_edger_input(source)
    contract = inspect_dataset_path(source)
    spec = CapabilityRegistry.load_default(include_external=False).get(
        "de.pseudobulk.edger.v1"
    )
    staging = tmp_path / "staging"
    staging.mkdir()
    monkeypatch.setattr(
        "pertura_workflow.capabilities.edger.doctor_environment",
        lambda _profile: {"ok": True, "problems": []},
    )
    monkeypatch.setattr(
        "pertura_workflow.capabilities.edger.environment_lock",
        lambda _profile: _EXPECTED_LOCK,
    )
    monkeypatch.setattr(
        "pertura_workflow.capabilities.edger.micromamba_path", lambda: Path("mamba")
    )
    monkeypatch.setattr(
        "pertura_workflow.capabilities.edger.environment_prefix", lambda: Path("env")
    )

    def fake_r(command, **kwargs):
        config = json.loads(Path(command[-1]).read_text(encoding="utf-8"))
        samples = list(csv.DictReader(Path(config["samples_path"]).open(encoding="utf-8")))
        sample_ids = [row["sample_id"] for row in samples]
        _write_csv(
            Path(config["result_path"]),
            ["gene", "logFC", "F", "PValue", "FDR", "dispersion"],
            [
                {"gene": "G1", "logFC": 1, "F": 4, "PValue": 0.01, "FDR": 0.02, "dispersion": 0.1},
                {"gene": "G2", "logFC": -0.1, "F": 1, "PValue": 0.2, "FDR": 0.2, "dispersion": 0.1},
            ],
        )
        _write_csv(
            Path(config["design_path"]),
            ["sample_id", "(Intercept)", "conditiontarget"],
            [
                {
                    "sample_id": row["sample_id"],
                    "(Intercept)": 1,
                    "conditiontarget": int(row["condition"] == "target"),
                }
                for row in samples
            ],
        )
        _write_csv(
            Path(config["mds_path"]),
            ["sample_id", "leading_logFC_1", "leading_logFC_2"],
            [
                {"sample_id": sample_id, "leading_logFC_1": index, "leading_logFC_2": -index}
                for index, sample_id in enumerate(sample_ids)
            ],
        )
        Path(config["environment_path"]).write_text(
            json.dumps(
                {
                    "R": "4.5.3",
                    "Bioconductor": "3.22",
                    "edgeR": "4.8.2",
                    "limma": "3.66.0",
                    "jsonlite": "2.0.0",
                    "sessionInfo": ["test session"],
                }
            ),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("pertura_workflow.capabilities.edger.subprocess.run", fake_r)

    result = run_edger_pseudobulk(
        spec, _edger_request(contract, spec), contract, staging
    )

    assert result.status.value == "completed_with_caution"
    assert result.metrics["retained_manifest_applied"] is False
    assert result.metrics["selected_retained_cell_count"] == 4
    assert result.metadata["execution_grounding"]["retained_manifest_applied"] is False

def test_edger_runner_aligns_dispersion_to_filtered_gene_order() -> None:
    runner = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "pertura_workflow"
        / "capabilities"
        / "runners"
        / "edger_ql.R"
    ).read_text(encoding="utf-8")

    assert "rownames(fit$coefficients)" not in runner
    assert "names(dispersion) <- rownames(y)" in runner
    assert "dispersion <- rep(dispersion, nrow(y))" in runner
    assert "any(!is.finite(table$dispersion))" in runner
    assert "unname(as.character(capture.output(sessionInfo())))" in runner
