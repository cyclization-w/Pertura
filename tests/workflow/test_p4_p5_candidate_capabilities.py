from __future__ import annotations

import io
import json
from pathlib import Path

import numpy as np
import pytest

from pertura_core import CapabilityRunRequest, DatasetContract, ScopeKey
from pertura_workflow.capabilities import CapabilityRegistry
from pertura_workflow.capabilities.executors import execute_capability
from pertura_workflow.capabilities.prediction_store import (
    FEATURE_TABLE_NAME, METADATA_NAME, ROW_TABLE_NAME, STANDARD_BUNDLE_NAME,
    open_chunked_prediction_bundle,
)
from pertura_workflow.capabilities.p5_candidates import _prediction_format


def _contract(root: Path) -> DatasetContract:
    return DatasetContract(
        dataset_id="synthetic-p45",
        input_format="csv",
        source_paths=(str(root),),
        expression_matrix={"raw_counts_confirmed": True},
    )


def _run(
    capability_id: str,
    contract: DatasetContract,
    staging: Path,
    parameters: dict | None = None,
    *,
    runtime_context: dict | None = None,
):
    spec = CapabilityRegistry.load_default().get(capability_id, "0.1.0")
    request = CapabilityRunRequest(
        run_id="p45-run",
        capability_id=capability_id,
        capability_version="0.1.0",
        contract_id=contract.contract_id,
        contract_hash=contract.canonical_hash,
        scope=ScopeKey(dataset_id=contract.dataset_id),
        parameters=parameters or {},
    )
    staging.mkdir(parents=True, exist_ok=True)
    return execute_capability(
        spec, request, contract, staging, runtime_context=runtime_context
    )


def _project(staging: Path, results: list[dict]) -> None:
    staging.mkdir(parents=True, exist_ok=True)
    (staging / "_dependency_results.json").write_text(
        json.dumps({"results": results}), encoding="utf-8"
    )
    (staging / "_runtime_dependencies.json").write_text(
        json.dumps({"dependencies": []}), encoding="utf-8"
    )


def _result(result_id: str, kind: str, paths: list[Path], **extra) -> dict:
    return {
        "result_id": result_id,
        "canonical_hash": "sha256:" + result_id[-1] * 64,
        "capability_id": extra.pop("capability_id", "fixture.effect.v1"),
        "capability_version": "0.1.0",
        "result_kind": kind,
        "status": extra.pop("status", "completed"),
        "source_class": extra.pop("source_class", "measured_result"),
        "scope": extra.pop("scope", {"dataset_id": "synthetic-p45"}),
        "blockers": [],
        "cautions": [],
        "metrics": {},
        "local_output_paths": [str(path) for path in paths],
        **extra,
    }


def _effect_fixture(root: Path) -> list[dict]:
    results = []
    genes = [f"G{i}" for i in range(8)]
    for index in range(5):
        directory = root / f"effect_{index}"
        directory.mkdir()
        path = directory / "edger_results.csv"
        rows = ["gene,logFC,FDR"]
        rows.extend(
            f"{gene},{(index + 1) * (column - 3) / 10:.3f},0.05"
            for column, gene in enumerate(genes)
            if not (index == 4 and column == 7)
        )
        path.write_text("\n".join(rows) + "\n", encoding="utf-8")
        results.append(
            _result(
                f"result_effect_{index}",
                "differential_expression",
                [path],
                capability_id="de.pseudobulk.edger.v1",
                scope={
                    "dataset_id": "synthetic-p45",
                    "perturbation_ids": [f"P{index}"],
                },
            )
        )
    return results


def test_effect_matrix_signed_program_cluster_and_ora(tmp_path: Path) -> None:
    contract = _contract(tmp_path)
    assemble_stage = tmp_path / "assemble"
    _project(assemble_stage, _effect_fixture(tmp_path))
    assembled = _run(
        "effect.matrix.assemble.v1",
        contract,
        assemble_stage,
        {"min_perturbations": 5, "min_features": 8},
    )
    assert assembled.status.value == "completed"
    data = np.load(assemble_stage / "effect_matrix.npz", allow_pickle=False)
    assert data["observed_mask"].shape == (5, 8)
    assert not bool(data["observed_mask"][4, 7])
    assert float(data["effects"][4, 7]) == 0.0

    matrix_result = _result(
        "result_matrix_1",
        "effect_matrix",
        [
            assemble_stage / "effect_matrix.npz",
            assemble_stage / "effect_matrix_manifest.json",
        ],
        capability_id="effect.matrix.assemble.v1",
    )
    program_stage = tmp_path / "program"
    _project(program_stage, [matrix_result])
    program = _run(
        "program.response.signed_nmf.v1",
        contract,
        program_stage,
        {"ranks": [2, 3], "n_seeds": 3, "stability_threshold": 0.0},
    )
    assert program.status.value == "completed"
    assert program.metrics["selected_rank"] in {2, 3}
    assert (program_stage / "response_programs.npz").is_file()

    cluster_stage = tmp_path / "cluster"
    _project(cluster_stage, [matrix_result])
    clustered = _run(
        "program.perturbation.cluster.v1",
        contract,
        cluster_stage,
        {"bootstraps": 10},
    )
    assert clustered.status.value in {"completed", "completed_with_caution"}
    assert clustered.metrics["n_clusters"] >= 2

    modules = tmp_path / "gmt_modules.json"
    modules.write_text(
        json.dumps(
            {
                "modules": {
                    "UP": ["G4", "G5", "G6", "G7"],
                    "DOWN": ["G0", "G1", "G2", "G3"],
                }
            }
        ),
        encoding="utf-8",
    )
    ora_stage = tmp_path / "ora"
    _project(
        ora_stage,
        [
            matrix_result,
            _result(
                "result_module_1",
                "reference_modules",
                [modules],
                capability_id="module.import.gmt.v1",
                source_class="curated_prior",
            ),
        ],
    )
    ora = _run(
        "enrichment.ora.v1",
        contract,
        ora_stage,
        {
            "min_gene_set_size": 2,
            "max_gene_set_size": 10,
            "effect_threshold": 0.1,
        },
    )
    assert ora.status.value in {"completed", "completed_with_caution"}
    assert (ora_stage / "ora_results.csv").is_file()
    assert (ora_stage / "ora_manifest.json").is_file()


def test_effect_matrix_accepts_public_trans_de_target_uid_rows(
    tmp_path: Path,
) -> None:
    contract = _contract(tmp_path)
    effect_table = tmp_path / "trans_de_results.tsv"
    lines = ["target_uid\tgene\tlogFC\tPValue\tFDR"]
    lines.extend(
        f"T{target}\tG{gene}\t{(target - gene) / 10:.3f}\t0.01\t0.05"
        for target in range(5)
        for gene in range(200)
    )
    effect_table.write_text("\n".join(lines) + "\n", encoding="utf-8")

    staging = tmp_path / "target_uid_effect_matrix"
    staging.mkdir()
    (staging / "_runtime_dependencies.json").write_text(
        json.dumps(
            {
                "dependencies": [
                    {
                        "kind": "data_asset",
                        "object_id": "asset_effect_table_fixture",
                        "object_hash": "sha256:" + "b" * 64,
                        "payload": {
                            "asset_id": "asset_effect_table_fixture",
                            "role": "effect_table",
                            "resolved_path": str(effect_table.resolve()),
                            "schema_validation_status": "validated",
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    assembled = _run(
        "effect.matrix.assemble.v1",
        contract,
        staging,
        {
            "effect_table_paths": [str(effect_table)],
            "effect_scale": "logFC",
            "estimand": "target_by_replicate_pseudobulk",
            "min_perturbations": 5,
            "min_features": 200,
        },
    )

    assert assembled.status.value == "completed"
    assert assembled.metrics["n_perturbations"] == 5
    assert assembled.metrics["n_features"] == 200


def test_interpretation_provenance_and_literature_opt_in(
    tmp_path: Path, monkeypatch
) -> None:
    contract = _contract(tmp_path)
    result = _result(
        "result_measured_1",
        "differential_expression",
        [],
        capability_id="de.pseudobulk.edger.v1",
    )
    stage = tmp_path / "map"
    _project(stage, [result])
    mapped = _run(
        "interpretation.evidence_map.v1",
        contract,
        stage,
        {
            "records": [
                {
                    "role": "derived",
                    "text": "A response program summarizes the measured effects.",
                    "result_ids": ["result_measured_1"],
                },
                {
                    "role": "measured",
                    "text": "Unsupported claim.",
                    "result_ids": ["missing"],
                },
            ]
        },
    )
    assert mapped.status.value == "completed_with_caution"
    assert mapped.metrics == {"accepted_records": 1, "rejected_records": 1}

    offline = _run(
        "literature.europepmc.v1",
        contract,
        tmp_path / "literature-offline",
        {"query": "Perturb-seq"},
    )
    assert offline.status.value == "blocked"

    response = {
        "resultList": {
            "result": [
                {"pmid": "1", "doi": "10.1/example", "title": "Perturb-seq study"}
            ]
        }
    }
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *args, **kwargs: io.BytesIO(json.dumps(response).encode("utf-8")),
    )
    online = _run(
        "literature.europepmc.v1",
        contract,
        tmp_path / "literature-online",
        {"query": "Perturb-seq", "max_records": 10},
        runtime_context={
            "network_policy": {
                "allowed_capabilities": ["literature.europepmc.v1"],
                "allowed_hosts": ["www.ebi.ac.uk"],
            }
        },
    )
    assert online.status.value == "completed_with_caution"
    assert online.metrics["n_records"] == 1


def _virtual_dependencies(tmp_path: Path, contract: DatasetContract):
    pytest.importorskip("zarr", reason="virtual-eval-v1 optional environment is not installed")
    split_stage = tmp_path / "split"
    split = _run(
        "virtual.split.contract.v1",
        contract,
        split_stage,
        {
            "axes": {
                "perturbation": {
                    "train": ["P1", "P2"],
                    "validation": [],
                    "test": ["P3", "P4"],
                }
            },
            "heldout_axes": ["perturbation"],
        },
    )
    assert split.status.value == "completed"
    split_result = _result(
        "result_split_1",
        "virtual_split_contract",
        [split_stage / "virtual_split_contract.json"],
        capability_id="virtual.split.contract.v1",
        source_class="observed_metadata",
    )

    rows = np.asarray(["P1", "P2", "P3", "P4"])
    features = np.asarray(["G1", "G2", "G3", "G4"])
    observed = np.asarray(
        [[0.0, 0.1, 0.0, 0.2], [0.1, 0.0, 0.2, 0.0],
         [1.0, -1.0, 0.5, -0.5], [-1.0, 1.0, -0.5, 0.5]]
    )
    prediction = observed + 0.05
    source = tmp_path / "predictions.npz"
    np.savez_compressed(
        source, predictions=prediction, observed=observed,
        row_ids=rows, feature_ids=features,
        metadata_json=np.asarray([json.dumps({"context": ["C1"] * 4})]),
        lower=prediction - 0.2, upper=prediction + 0.2,
    )
    ingest_stage = tmp_path / "ingest"
    _project(ingest_stage, [split_result])
    ingested = _run(
        "virtual.prediction.ingest.v1",
        contract,
        ingest_stage,
        {
            "prediction_path": str(source),
            "format": "matrix_bundle",
            "model_id": "fixture-model",
            "model_version": "1",
            "model_training_ids": ["P1", "P2"],
        },
    )
    assert ingested.status.value == "completed_with_caution"
    assert _prediction_format(ingest_stage) == "zarr_bundle"
    prediction_result = _result(
        "result_prediction_1",
        "prediction_bundle",
        [
            ingest_stage / STANDARD_BUNDLE_NAME,
            ingest_stage / ROW_TABLE_NAME,
            ingest_stage / FEATURE_TABLE_NAME,
            ingest_stage / METADATA_NAME,
            ingest_stage / "prediction_bundle_contract.json",
        ],
        capability_id="virtual.prediction.ingest.v1",
        source_class="prediction",
    )
    return split_result, prediction_result


def test_virtual_leakage_baselines_evaluation_and_next_panel(tmp_path: Path) -> None:
    contract = _contract(tmp_path)
    split_result, prediction_result = _virtual_dependencies(tmp_path, contract)
    audit_stage = tmp_path / "audit"
    _project(audit_stage, [split_result, prediction_result])
    audit = _run(
        "virtual.leakage.audit.v1",
        contract,
        audit_stage,
        {
            "state_reference_training_ids": ["P1", "P2"],
            "module_reference_training_ids": ["P1"],
            "preprocessing_training_ids": ["P1", "P2"],
        },
    )
    assert audit.status.value == "supported"
    audit_result = _result(
        "result_audit_1",
        "virtual_leakage_audit",
        [audit_stage / "virtual_leakage_audit.json"],
        capability_id="virtual.leakage.audit.v1",
        source_class="observed_metadata",
        status="supported",
    )

    baseline_stage = tmp_path / "baselines"
    _project(baseline_stage, [split_result, prediction_result, audit_result])
    baselines = _run(
        "virtual.baselines.v1",
        contract,
        baseline_stage,
        {"control_ids": ["P1"]},
    )
    assert baselines.status.value in {"supported", "limited"}
    baseline_result = _result(
        "result_baseline_1",
        "virtual_baselines",
        [
            baseline_stage / "virtual_baselines.npz",
            baseline_stage / "virtual_baseline_results.json",
        ],
        capability_id="virtual.baselines.v1",
        source_class="prediction",
        status=baselines.status.value,
    )

    evaluation_stage = tmp_path / "evaluation"
    _project(
        evaluation_stage,
        [split_result, prediction_result, audit_result, baseline_result],
    )
    evaluation = _run(
        "virtual.evaluate.comprehensive.v1",
        contract,
        evaluation_stage,
        {"bootstrap_iterations": 100},
    )
    assert evaluation.status.value in {"supported", "limited"}
    assert "baseline_win_rate" in evaluation.metrics
    assert (evaluation_stage / "virtual_evaluation.json").is_file()

    evaluation_result = _result(
        "result_evaluation_1",
        "virtual_evaluation",
        [evaluation_stage / "virtual_evaluation.json"],
        capability_id="virtual.evaluate.comprehensive.v1",
        source_class="prediction",
        status=evaluation.status.value,
    )
    panel_stage = tmp_path / "panel"
    _project(panel_stage, [evaluation_result])
    panel = _run(
        "design.next_panel.v1",
        contract,
        panel_stage,
        {
            "budget": 2.0,
            "candidates": [
                {
                    "candidate_id": "A", "cost": 1.0, "uncertainty": 0.9,
                    "information_gain": 0.8, "program_coverage": 0.5,
                    "biological_diversity": 0.4, "feasibility": 1.0,
                },
                {
                    "candidate_id": "B", "cost": 2.0, "uncertainty": 0.1,
                    "information_gain": 0.2, "program_coverage": 0.3,
                    "biological_diversity": 0.5, "feasibility": 0.5,
                },
            ],
        },
    )
    assert panel.status.value == "completed_with_caution"
    assert panel.metrics["selected_count"] == 1


def test_virtual_leakage_blocks_test_contact(tmp_path: Path) -> None:
    contract = _contract(tmp_path)
    split_result, prediction_result = _virtual_dependencies(tmp_path, contract)
    contract_path = next(
        Path(value)
        for value in prediction_result["local_output_paths"]
        if Path(value).name == "prediction_bundle_contract.json"
    )
    manifest = json.loads(contract_path.read_text(encoding="utf-8"))
    manifest["model_training_ids"] = ["P1", "P3"]
    manifest.pop("canonical_hash", None)
    contract_path.write_text(json.dumps(manifest), encoding="utf-8")
    stage = tmp_path / "leakage"
    _project(stage, [split_result, prediction_result])
    result = _run("virtual.leakage.audit.v1", contract, stage)
    assert result.status.value == "out_of_scope"
    assert result.metrics["leakage_reason_count"] == 1


def test_virtual_multi_axis_split_is_row_level_not_axis_id_union(tmp_path: Path) -> None:
    pytest.importorskip("zarr", reason="virtual-eval-v1 optional environment is not installed")
    contract = _contract(tmp_path)
    split_stage = tmp_path / "multi-split"
    split = _run(
        "virtual.split.contract.v1",
        contract,
        split_stage,
        {
            "axes": {
                "perturbation": {
                    "train": ["A"],
                    "validation": [],
                    "test": ["B"],
                },
                "context": {
                    "train": ["C1"],
                    "validation": [],
                    "test": ["C2"],
                },
            },
            "heldout_axes": ["perturbation", "context"],
        },
    )
    split_result = _result(
        "result_multi_split",
        "virtual_split_contract",
        [split_stage / "virtual_split_contract.json"],
        capability_id="virtual.split.contract.v1",
        source_class="observed_metadata",
    )
    source = tmp_path / "multi_predictions.npz"
    rows = np.asarray(["row1", "row2", "row3", "row4"])
    features = np.asarray(["G1", "G2"])
    observed = np.asarray([[0.0, 0.0], [0.1, 0.1], [1.0, -1.0], [1.1, -0.9]])
    np.savez_compressed(
        source,
        predictions=observed + 0.01,
        observed=observed,
        row_ids=rows,
        feature_ids=features,
        metadata_json=np.asarray([json.dumps({
            "perturbation": ["A", "A", "B", "B"],
            "context": ["C1", "C2", "C1", "C2"],
        })]),
    )
    ingest_stage = tmp_path / "multi-ingest"
    _project(ingest_stage, [split_result])
    ingested = _run(
        "virtual.prediction.ingest.v1",
        contract,
        ingest_stage,
        {
            "prediction_path": str(source),
            "model_training_ids": ["row1"],
        },
    )
    assert ingested.metrics["n_train_rows"] == 1
    assert ingested.metrics["n_test_rows"] == 3
    _, _, _, _, metadata, _ = open_chunked_prediction_bundle(
        ingest_stage / STANDARD_BUNDLE_NAME,
        ingest_stage / ROW_TABLE_NAME,
        ingest_stage / FEATURE_TABLE_NAME,
        ingest_stage / METADATA_NAME,
    )
    assert metadata["__row_partition"] == ["train", "test", "test", "test"]
    assert metadata["__axis_partitions"]["perturbation"] == [
        "train", "train", "test", "test"
    ]
    assert metadata["__axis_partitions"]["context"] == [
        "train", "test", "train", "test"
    ]
