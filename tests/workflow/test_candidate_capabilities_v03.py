from __future__ import annotations

import csv
import json
from pathlib import Path

import numpy as np
import pytest

from pertura_core import CapabilityRunRequest, DatasetContract, DependencyRef, ScopeKey
from pertura_core.hashing import file_sha256
from pertura_workflow.capabilities import CapabilityRegistry
from pertura_workflow.capabilities.executors import execute_capability
from pertura_workflow.capabilities.guide_candidates import (
    _scrublet_subset_plan,
    run_moi_doublet,
)


def _contract(
    root: Path,
    *,
    expression: dict | None = None,
    identity_fields: dict | None = None,
) -> DatasetContract:
    return DatasetContract(
        dataset_id="synthetic",
        input_format="csv",
        source_paths=(str(root),),
        expression_matrix=expression or {"raw_counts_confirmed": True},
        identity_fields=identity_fields or {},
    )


def _run(
    capability_id: str,
    contract: DatasetContract,
    staging: Path,
    parameters: dict,
    *,
    dependencies=(),
):
    registry = CapabilityRegistry.load_default(include_external=False)
    spec = registry.get(capability_id, "0.1.0")
    request = CapabilityRunRequest(
        run_id="synthetic-run",
        capability_id=capability_id,
        capability_version="0.1.0",
        contract_id=contract.contract_id,
        contract_hash=contract.canonical_hash,
        scope=ScopeKey(dataset_id=contract.dataset_id),
        parameters=parameters,
        dependencies=dependencies,
    )
    staging.mkdir(parents=True, exist_ok=True)
    return execute_capability(spec, request, contract, staging)


def test_p0_materialization_integrity_and_design_balance(tmp_path: Path) -> None:
    expression = tmp_path / "expression.csv"
    expression.write_text(
        "cell_id,G1,G2\n"
        "AAAC-1,1,0\n"
        "AAAG-1,0,2\n"
        "AACC-1,3,1\n",
        encoding="utf-8",
    )
    metadata = tmp_path / "metadata.csv"
    metadata.write_text(
        "cell_id,condition,replicate,batch\n"
        "a1,A,A1,B1\n"
        "a2,A,A2,B2\n"
        "a3,A,A3,B1\n"
        "b1,B,B1,B1\n"
        "b2,B,B2,B2\n"
        "b3,B,B3,B1\n",
        encoding="utf-8",
    )
    contract = _contract(tmp_path)
    materialized = _run(
        "intake.materialize.v1",
        contract,
        tmp_path / "materialized",
        {"input_path": str(expression)},
    )
    assert materialized.status.value == "completed"
    assert set(materialized.output_paths) >= {
        "counts.npz",
        "obs.parquet",
        "var.parquet",
        "materialization_manifest.json",
    }
    integrity = _run(
        "diagnostic.dataset_integrity.v1",
        contract,
        tmp_path / "integrity",
        {"input_path": str(expression)},
    )
    assert integrity.status.value == "screen_passed"
    assert integrity.metrics["integer_like"] is True
    design = _run(
        "diagnostic.design_balance.v1",
        contract,
        tmp_path / "design",
        {"metadata_path": str(metadata)},
    )
    assert design.status.value == "screen_passed"
    assert design.metrics["minimum_units_per_condition"] == 3
    assert design.metrics["contrast_estimable"] is True


def test_p0_detects_barcode_collision_and_batch_confounding(tmp_path: Path) -> None:
    expression = tmp_path / "collision.csv"
    expression.write_text(
        "cell_id,G1\n"
        "AAAC-1,1\n"
        "AAAC-2,2\n",
        encoding="utf-8",
    )
    metadata = tmp_path / "confounded.csv"
    metadata.write_text(
        "cell_id,condition,replicate,batch\n"
        "a1,A,A1,BA\n"
        "a2,A,A2,BA\n"
        "b1,B,B1,BB\n"
        "b2,B,B2,BB\n",
        encoding="utf-8",
    )
    contract = _contract(tmp_path)
    integrity = _run(
        "diagnostic.dataset_integrity.v1",
        contract,
        tmp_path / "integrity",
        {"input_path": str(expression)},
    )
    assert integrity.status.value == "blocked"
    assert any("collisions" in item for item in integrity.blockers)
    design = _run(
        "diagnostic.design_balance.v1",
        contract,
        tmp_path / "design",
        {"metadata_path": str(metadata)},
    )
    assert design.status.value == "blocked"
    assert any("confounded" in item for item in design.blockers)


def test_p1_granular_guide_pipeline_keeps_multiguide_separate_from_doublets(tmp_path: Path) -> None:
    guide_counts = tmp_path / "guide_counts.csv"
    guide_counts.write_text(
        "barcode,g1,g2\n"
        "AAAA-1,12,0\n"
        "AAAC-1,11,0\n"
        "AAAG-1,10,0\n"
        "AACA-1,0,13\n"
        "AACC-1,0,12\n"
        "AACG-1,0,11\n"
        "AAGA-1,9,9\n"
        "AAGC-1,0,0\n",
        encoding="utf-8",
    )
    raw_counts = tmp_path / "raw_counts.csv"
    raw_counts.write_text(
        guide_counts.read_text(encoding="utf-8")
        + "TTTT-1,1,0\n"
        + "TTTC-1,0,1\n",
        encoding="utf-8",
    )
    rna = tmp_path / "rna_barcodes.csv"
    rna.write_text(
        "barcode\n"
        + "\n".join(
            ["AAAA-1", "AAAC-1", "AAAG-1", "AACA-1", "AACC-1", "AACG-1", "AAGA-1", "AAGC-1"]
        )
        + "\n",
        encoding="utf-8",
    )
    guide_map = tmp_path / "guide_map.csv"
    guide_map.write_text("guide,target\ng1,T1\ng2,T2\n", encoding="utf-8")
    contract = _contract(
        tmp_path,
        identity_fields={
            "design_moi": {"value": "high", "status": "confirmed"},
            "guide_design": {"value": "combinatorial", "status": "confirmed"},
        },
    )
    integrity = _run(
        "guide.integrity.v1",
        contract,
        tmp_path / "guide-integrity",
        {
            "guide_counts_path": str(guide_counts),
            "rna_barcodes_path": str(rna),
            "guide_map_path": str(guide_map),
        },
    )
    assert integrity.status.value == "screen_passed"
    assigned = _run(
        "guide.assignment.nb_mixture.v1",
        contract,
        tmp_path / "assignment",
        {"guide_counts_path": str(guide_counts)},
    )
    assert assigned.status.value in {"screen_passed", "caution"}
    ambient = _run(
        "guide.ambient.v1",
        contract,
        tmp_path / "ambient",
        {
            "raw_guide_counts_path": str(raw_counts),
            "filtered_guide_counts_path": str(guide_counts),
        },
    )
    assert ambient.metrics["n_empty_droplets"] == 2
    moi = _run(
        "screen.moi_doublet.v1",
        contract,
        tmp_path / "moi",
        {"assignment_path": str(tmp_path / "assignment" / "guide_assignments.json")},
    )
    assert moi.metrics["n_multi_guide"] >= 1
    assert moi.metrics["doublet_status"] == "unresolved"
    retained = _run(
        "screen.retained_cells.v1",
        contract,
        tmp_path / "retained",
        {
            "assignment_path": str(tmp_path / "assignment" / "guide_assignments.json"),
            "moi_doublet_path": str(tmp_path / "moi" / "moi_doublet.json"),
            "design_moi": "high",
        },
    )
    assert retained.metrics["design_moi"] == "high"
    assert retained.metadata["retained_cell_manifest_hash_bound"] is True
    with (tmp_path / "retained" / "retained_cells.csv").open(
        "r", encoding="utf-8", newline=""
    ) as handle:
        retained_rows = list(csv.DictReader(handle))
    combinatorial = next(
        row for row in retained_rows if row["raw_barcode"] == "AAGA-1"
    )
    assert combinatorial["multi_guide"].lower() == "true"
    assert combinatorial["transcriptomic_doublet"].lower() == "false"
    assert combinatorial["retained"].lower() == "true"


def test_target_guide_efficacy_and_leakage_blocking(tmp_path: Path) -> None:
    expression = tmp_path / "target_expression.csv"
    metadata = tmp_path / "target_metadata.csv"
    expression_lines = ["cell_id,TG,S1,S2"]
    metadata_lines = ["cell_id,perturbation_uid,guide,replicate,batch"]
    for replicate in range(1, 4):
        for index in range(10):
            cell = f"t{replicate}_{index}"
            guide = f"g{(index % 3) + 1}"
            expression_lines.append(f"{cell},1,1,1")
            metadata_lines.append(f"{cell},TARGET,{guide},r{replicate},b{replicate}")
        for index in range(10):
            cell = f"c{replicate}_{index}"
            expression_lines.append(f"{cell},5,0,0")
            metadata_lines.append(f"{cell},NTC,NTC,r{replicate},b{replicate}")
    expression.write_text("\n".join(expression_lines) + "\n", encoding="utf-8")
    metadata.write_text("\n".join(metadata_lines) + "\n", encoding="utf-8")
    contract = _contract(tmp_path)
    result = _run(
        "target.guide_efficacy.v1",
        contract,
        tmp_path / "efficacy",
        {
            "expression_path": str(expression),
            "metadata_path": str(metadata),
            "target_uid": "TARGET",
            "control_uid": "NTC",
            "target_gene": "TG",
            "expected_direction": "down",
            "bootstrap_iterations": 50,
            "guide_bootstrap_iterations": 25,
        },
    )
    assert result.status.value in {"screen_passed", "caution"}
    assert result.metrics["n_shared_replicates"] == 3
    assert result.metrics["guide_concordance"] == 1.0

    leaked = _run(
        "target.guide_efficacy.v1",
        contract,
        tmp_path / "leaked",
        {
            "expression_path": str(expression),
            "metadata_path": str(metadata),
            "target_uid": "TARGET",
            "control_uid": "NTC",
            "target_gene": "S1",
            "expected_direction": "down",
            "signature_genes": ["S2"],
            "signature_learned_from_same_perturbation": True,
            "bootstrap_iterations": 10,
        },
    )
    assert leaked.metrics["signature_confirmation_allowed"] is False
    assert any("leakage" in item for item in leaked.cautions)

    retained_manifest = tmp_path / "retained_cells.tsv"
    retained_manifest.write_text(
        "cell_id\texpected_state\n"
        + "".join(
            f"{line.split(',', 1)[0]}\tretain_for_external_label_proxy\n"
            for line in metadata_lines[1:]
        ),
        encoding="utf-8",
    )
    batch_staging = tmp_path / "batch_efficacy"
    batch_staging.mkdir()
    retained_asset_id = "asset_retained_fixture"
    retained_asset_hash = "sha256:" + "a" * 64
    (batch_staging / "_runtime_dependencies.json").write_text(
        json.dumps(
            {
                "dependencies": [
                    {
                        "kind": "data_asset",
                        "object_id": retained_asset_id,
                        "object_hash": retained_asset_hash,
                        "payload": {
                            "asset_id": retained_asset_id,
                            "role": "retained_cell_manifest",
                            "resolved_path": str(retained_manifest),
                            "content_sha256": file_sha256(retained_manifest),
                            "schema_validation_status": "validated",
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    batched = _run(
        "target.guide_efficacy.v1",
        contract,
        batch_staging,
        {
            "expression_path": str(expression),
            "metadata_path": str(metadata),
            "targets": [
                {
                    "target_uid": "TARGET",
                    "control_uid": "NTC",
                    "target_gene": "TG",
                    "expected_direction": "down",
                },
                {
                    "target_uid": "TARGET2",
                    "control_uid": "NTC",
                    "target_gene": "S1",
                    "expected_direction": "up",
                },
            ],
            "bootstrap_iterations": 10,
            "guide_bootstrap_iterations": 5,
        },
        dependencies=(
            DependencyRef(
                kind="data_asset",
                object_id=retained_asset_id,
                object_hash=retained_asset_hash,
                role="asset:retained_cell_manifest",
            ),
        ),
    )
    assert batched.metrics["target_count"] == 2
    payload = json.loads(
        (tmp_path / "batch_efficacy" / "target_guide_efficacy.json").read_text(
            encoding="utf-8"
        )
    )
    assert payload["schema_version"] == "pertura-target-guide-efficacy-set-v1"
    assert [item["target_gene"] for item in payload["targets"]] == ["TG", "S1"]
    assert payload["retained_manifest_applied"] is True
    assert all(
        (
            batch_staging
            / f"target_{index:04d}"
            / "_runtime_dependencies.json"
        ).is_file()
        for index in (1, 2)
    )

    mixed = _run(
        "target.guide_efficacy.v1",
        contract,
        tmp_path / "mixed_efficacy",
        {
            "expression_path": str(expression),
            "metadata_path": str(metadata),
            "target_uid": "TARGET",
            "targets": [
                {
                    "target_uid": "TARGET",
                    "control_uid": "NTC",
                    "target_gene": "TG",
                    "expected_direction": "down",
                }
            ],
        },
    )
    assert mixed.status.value == "blocked"
    assert "cannot be combined" in mixed.blockers[0]


def test_dependency_only_target_aggregate_never_becomes_production_pass(tmp_path: Path) -> None:
    staging = tmp_path / "aggregate"
    staging.mkdir()
    efficacy_output = tmp_path / "target_guide_efficacy.json"
    efficacy_output.write_text(
        json.dumps(
            {
                "schema_version": "pertura-target-guide-efficacy-set-v1",
                "targets": [
                    {
                        "target_uid": "TARGET",
                        "target_gene": "TG",
                        "status": "screen_passed",
                        "blockers": [],
                        "cautions": [],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    dependencies = []
    for index, capability_id in enumerate(
        (
            "screen.retained_cells.v1",
            "diagnostic.design_balance.v1",
            "target.guide_efficacy.v1",
            "target.responder.mixscape.v1",
        )
    ):
        dependencies.append(
            {
                "result_id": f"result_{index}",
                "canonical_hash": "sha256:" + str(index + 1) * 64,
                "capability_id": capability_id,
                "status": "screen_passed",
                "blockers": [],
                "cautions": [],
                "metrics": {},
                "local_output_paths": (
                    [str(efficacy_output)]
                    if capability_id == "target.guide_efficacy.v1"
                    else []
                ),
            }
        )
    (staging / "_dependency_results.json").write_text(
        json.dumps({"results": dependencies}),
        encoding="utf-8",
    )
    contract = _contract(tmp_path)
    result = _run(
        "target.reliability.aggregate.v1",
        contract,
        staging,
        {},
    )
    assert result.status.value == "caution"
    assert result.metrics["profile_validated"] is False
    assert result.metrics["raw_data_recomputed"] is False
    assert result.metrics["target_count"] == 1


def test_pure_python_effect_sensitivity_module_and_null_calibration(tmp_path: Path) -> None:
    effects = tmp_path / "guide_effects.csv"
    effects.write_text(
        "guide,target,effect\n"
        "g1,T1,-1.0\n"
        "g2,T1,-0.8\n"
        "g3,T2,0.5\n"
        "g4,T2,-0.5\n",
        encoding="utf-8",
    )
    gene_effects = tmp_path / "gene_effects.csv"
    gene_effects.write_text(
        "gene,logFC,FDR\n"
        "A,-1.0,0.01\n"
        "B,-0.5,0.03\n"
        "C,0.2,0.5\n",
        encoding="utf-8",
    )
    modules = tmp_path / "modules.gmt"
    modules.write_text("M1\tdesc\tA\tB\nM2\tdesc\tC\n", encoding="utf-8")
    nulls = tmp_path / "nulls.csv"
    nulls.write_text(
        "p_value\n0.2\n0.4\n0.6\n0.8\n0.9\n",
        encoding="utf-8",
    )
    contract = _contract(tmp_path)
    sensitivity = _run(
        "effect.guide_target_sensitivity.v1",
        contract,
        tmp_path / "sensitivity",
        {"effect_table_path": str(effects)},
    )
    assert sensitivity.metrics["n_targets"] == 2
    assert sensitivity.metrics["unstable_target_count"] == 1
    module_stage = tmp_path / "module"
    module_stage.mkdir()
    effect_bundle = tmp_path / "effect_matrix.npz"
    np.savez_compressed(
        effect_bundle,
        effects=np.asarray([[-1.0, -0.5, 0.2]]),
        observed_mask=np.asarray([[True, True, True]]),
        perturbations=np.asarray(["P1"]),
        features=np.asarray(["A", "B", "C"]),
    )
    module_reference = tmp_path / "gmt_modules.json"
    module_reference.write_text(
        json.dumps({"modules": {"M1": ["A", "B"], "M2": ["C"]}}),
        encoding="utf-8",
    )
    (module_stage / "_dependency_results.json").write_text(
        json.dumps({
            "results": [
                {
                    "result_id": "effect_matrix_result",
                    "result_kind": "effect_matrix",
                    "local_output_paths": [str(effect_bundle)],
                },
                {
                    "result_id": "module_reference_result",
                    "result_kind": "module_reference",
                    "local_output_paths": [str(module_reference)],
                },
            ]
        }),
        encoding="utf-8",
    )
    module = _run(
        "effect.module_global.v1",
        contract,
        module_stage,
        {},
    )
    assert module.metrics["n_module_summaries"] == 2
    assert module.metadata["new_significance_tests_performed"] is False
    calibration = _run(
        "calibration.method_null.v1",
        contract,
        tmp_path / "calibration",
        {
            "null_results_path": str(nulls),
            "permutation_unit": "replicate_label",
        },
    )
    assert calibration.metrics["calibration_passed"] is True
    blocked_calibration = _run(
        "calibration.method_null.v1",
        contract,
        tmp_path / "bad-calibration",
        {
            "null_results_path": str(nulls),
            "permutation_unit": "cell",
        },
    )
    assert blocked_calibration.status.value == "blocked"


def test_ora_accepts_registered_frozen_gene_set_json(tmp_path: Path) -> None:
    staging = tmp_path / "ora"
    staging.mkdir()
    features = np.asarray([f"G{i}" for i in range(20)])
    matrix_path = tmp_path / "effect_matrix.npz"
    np.savez_compressed(
        matrix_path,
        effects=np.asarray([[1.0] * 10 + [0.0] * 10]),
        observed_mask=np.asarray([[True] * 20]),
        perturbations=np.asarray(["T1"]),
        features=features,
    )
    (staging / "_dependency_results.json").write_text(
        json.dumps({
            "results": [{
                "result_id": "effect_matrix_result",
                "result_kind": "effect_matrix",
                "local_output_paths": [str(matrix_path)],
            }]
        }),
        encoding="utf-8",
    )
    gene_sets = tmp_path / "frozen_gene_sets.json"
    gene_sets.write_text(
        json.dumps({"valid_modules": {"UP": [f"G{i}" for i in range(10)]}}),
        encoding="utf-8",
    )

    result = _run(
        "enrichment.ora.v1",
        _contract(tmp_path),
        staging,
        {"gene_sets_path": str(gene_sets), "min_gene_set_size": 2},
    )

    assert result.status.value == "completed"
    assert result.metrics["n_gene_sets"] == 1
    assert result.metrics["n_tests"] == 1


def test_optional_adapters_fail_closed_when_environments_are_missing(tmp_path: Path) -> None:
    contract = _contract(tmp_path)
    mixscape = _run(
        "target.responder.mixscape.v1",
        contract,
        tmp_path / "mixscape",
        {},
    )
    assert mixscape.status.value == "blocked"
    assert any("pertura env setup perturbseq-python-v1" in item for item in mixscape.blockers)

    metadata = tmp_path / "composition.csv"
    metadata.write_text(
        "cell,state,condition,replicate,batch\n"
        "a1,S1,A,A1,B1\n"
        "a2,S2,A,A2,B2\n"
        "b1,S1,B,B1,B1\n"
        "b2,S2,B,B2,B2\n",
        encoding="utf-8",
    )
    propeller = _run(
        "composition.propeller.v1",
        contract,
        tmp_path / "propeller",
        {"metadata_path": str(metadata)},
    )
    assert propeller.status.value == "blocked"
    assert any("environment is missing" in item for item in propeller.blockers)

def test_state_module_leakage_is_blocked_before_fitting(tmp_path: Path) -> None:
    contract = _contract(tmp_path)
    leaked = _run(
        "module.learn.control_nmf.v1",
        contract,
        tmp_path / "leaked-module",
        {
            "perturbation_labels_used": True,
            "test_split_used": True,
        },
    )
    assert leaked.status.value == "blocked"
    assert leaked.metadata["leakage_detected"] is True
    state = _run(
        "state.reference.fit.v1",
        contract,
        tmp_path / "state",
        {},
    )
    assert state.status.value == "blocked"
    assert any("pertura env setup perturbseq-python-v1" in item for item in state.blockers)

def test_candidate_r_adapters_accept_complete_protocol_outputs(monkeypatch, tmp_path: Path) -> None:
    from types import SimpleNamespace
    from pertura_workflow.capabilities import effect_candidates

    response = tmp_path / "response.csv"
    response.write_text("id,c1,c2\nG1,1,2\n", encoding="utf-8")
    guides = tmp_path / "guides.csv"
    guides.write_text("id,c1,c2\ng1,2,0\n", encoding="utf-8")
    guide_map = tmp_path / "guide_map_sceptre.csv"
    guide_map.write_text("grna_id,grna_target\ng1,T1\n", encoding="utf-8")
    pairs = tmp_path / "pairs.csv"
    pairs.write_text("grna_target,response_id\nT1,G1\n", encoding="utf-8")
    metadata = tmp_path / "composition_protocol.csv"
    metadata.write_text(
        "cell,state,condition,replicate,batch\n"
        "a1,S1,A,A1,B1\n"
        "a2,S2,A,A2,B2\n"
        "a3,S1,A,A3,B1\n"
        "b1,S1,B,B1,B1\n"
        "b2,S2,B,B2,B2\n"
        "b3,S2,B,B3,B1\n",
        encoding="utf-8",
    )

    captured_configs = {}

    def fake_runner(profile, runner, config_path, *, timeout):
        config = json.loads(Path(config_path).read_text(encoding="utf-8"))
        captured_configs[profile] = config
        output = Path(config["output_dir"])
        if profile == "sceptre-v1":
            (output / "sceptre_metadata.json").write_text(
                json.dumps({
                    "calibration_passed": True,
                    "calibration_type1_rate": 0.04,
                    "discovery_executed": True,
                }),
                encoding="utf-8",
            )
            (output / "sceptre_calibration.csv").write_text(
                "response_id,grna_target,p_value\nG1,NTC,0.4\n",
                encoding="utf-8",
            )
            (output / "sceptre_results.csv").write_text(
                "response_id,grna_target,p_value,fold_change,se_fold_change,FDR\n"
                "G1,T1,0.01,-1.0,0.2,0.02\n",
                encoding="utf-8",
            )
        else:
            (output / "propeller_results.csv").write_text(
                "cluster,baseline_proportion,target_proportion,effect,PValue,FDR\n"
                "S1,0.5,0.6,0.1,0.05,0.1\n"
                "S2,0.5,0.4,-0.1,0.1,0.2\n",
                encoding="utf-8",
            )
            (output / "sample_state_proportions.csv").write_text(
                "sample_id,S1,S2,condition\nA1,0.5,0.5,A\n",
                encoding="utf-8",
            )
            (output / "propeller_metadata.json").write_text(
                json.dumps({"speckle_version": "1.10.0"}),
                encoding="utf-8",
            )
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(effect_candidates, "_run_r_profile", fake_runner)
    contract = _contract(tmp_path)
    sceptre = _run(
        "association.sceptre.v1",
        contract,
        tmp_path / "sceptre",
        {
            "response_matrix_path": str(response),
            "guide_matrix_path": str(guides),
            "guide_target_map_path": str(guide_map),
            "discovery_pairs_path": str(pairs),
            "moi": "high",
        },
    )
    assert sceptre.status.value == "completed_with_caution"
    assert sceptre.metrics["calibration_passed"] is True
    assert sceptre.metrics["n_pairs"] == 1

    propeller = _run(
        "composition.propeller.v1",
        contract,
        tmp_path / "propeller-protocol",
        {"metadata_path": str(metadata)},
    )
    assert propeller.status.value == "completed_with_caution"
    assert propeller.metrics["n_independent_units_per_arm"] == 3
    assert propeller.metrics["n_states"] == 2

    paired_metadata = tmp_path / "paired_composition.tsv"
    paired_metadata.write_text(
        "cell\tstate\tstim\tdonor\n"
        "a1\tS1\tctrl\tD1\n"
        "a2\tS2\tstim\tD1\n"
        "b1\tS1\tctrl\tD2\n"
        "b2\tS2\tstim\tD2\n",
        encoding="utf-8",
    )
    paired = _run(
        "composition.propeller.v1",
        contract,
        tmp_path / "propeller-paired",
        {
            "metadata_path": str(paired_metadata),
            "sample_column": "donor",
            "pairing_column": "donor",
            "state_column": "state",
            "condition_column": "stim",
            "contrast": ["ctrl", "stim"],
        },
    )
    assert paired.status.value == "completed_with_caution"
    assert paired.metrics["n_independent_units_per_arm"] == 2
    assert captured_configs["composition-v1"]["pairing_column"] == "donor"


def test_mixscape_uses_state_mapped_evaluation_subset_of_retained_cells() -> None:
    from pertura_workflow.capabilities.target_candidates import (
        _mixscape_evaluation_rows,
    )

    rows, scope = _mixscape_evaluation_rows(
        ["cal-1", "eval-1", "cal-2", "eval-2"],
        {"cal-1", "cal-2", "eval-1", "eval-2"},
        ["eval-1", "eval-2"],
    )

    assert rows == [1, 3]
    assert scope == {
        "retained_manifest_applied": True,
        "retained_manifest_cell_count": 4,
        "mapped_evaluation_cell_count": 2,
        "excluded_nonmapped_retained_cell_count": 2,
    }


def test_mixscape_rejects_state_mapping_outside_retained_or_input_cells() -> None:
    from pertura_workflow.capabilities.target_candidates import (
        _mixscape_evaluation_rows,
    )

    with pytest.raises(ValueError, match="outside the retained-cell manifest"):
        _mixscape_evaluation_rows(
            ["eval-1", "eval-2"],
            {"eval-1"},
            ["eval-1", "eval-2"],
        )
    with pytest.raises(ValueError, match="missing 1 state-mapped evaluation cells"):
        _mixscape_evaluation_rows(
            ["eval-1"],
            {"eval-1", "eval-2"},
            ["eval-1", "eval-2"],
        )


def test_mixscape_uses_global_controls_when_any_requested_stratum_is_empty() -> None:
    from pertura_workflow.capabilities.target_candidates import (
        _mixscape_control_split_policy,
    )

    policy = _mixscape_control_split_policy(
        ["rep1"] * 20 + ["rep2"] * 20,
        [True] * 20 + [False] * 20,
        requested_split_by="replicate",
        n_neighbors=20,
    )

    assert policy["split_by"] is None
    assert policy["mode"] == "evaluation_control_global"
    assert policy["control_counts_by_stratum"] == {"rep1": 20, "rep2": 0}


def test_mixscape_stratifies_only_when_every_stratum_has_enough_controls() -> None:
    from pertura_workflow.capabilities.target_candidates import (
        _mixscape_control_split_policy,
    )

    policy = _mixscape_control_split_policy(
        ["rep1"] * 20 + ["rep2"] * 20,
        [True] * 40,
        requested_split_by="replicate",
        n_neighbors=20,
    )

    assert policy["split_by"] == "replicate"
    assert policy["mode"] == "stratified"

    with pytest.raises(ValueError, match="at least 20 evaluation controls"):
        _mixscape_control_split_policy(
            ["rep1"] * 19,
            [True] * 19,
            requested_split_by="replicate",
            n_neighbors=20,
        )


def test_propeller_applies_selection_and_excludes_missing_states(tmp_path: Path) -> None:
    from pertura_workflow.capabilities.effect_candidates import (
        _invalid_propeller_rows,
        _propeller_analysis_rows,
        _read_selection_cell_ids,
    )

    selection = tmp_path / "evaluation.cells.tsv.gz"
    import gzip
    with gzip.open(selection, "wt", encoding="utf-8", newline="") as handle:
        handle.write("cell_id\ncell-3\ncell-1\ncell-2\n")
    selection_ids = _read_selection_cell_ids(selection, "cell_id")
    fields = ["cell_id", "cell", "ind", "stim"]
    rows = [
        {"cell_id": "outside", "cell": "T", "ind": "D0", "stim": "ctrl"},
        {"cell_id": "cell-1", "cell": "B", "ind": "D1", "stim": "ctrl"},
        {"cell_id": "cell-2", "cell": "NA", "ind": "D1", "stim": "stim"},
        {"cell_id": "cell-3", "cell": "T", "ind": "D2", "stim": "ctrl"},
    ]

    analyzed, excluded, accounting = _propeller_analysis_rows(
        fields,
        rows,
        selection_ids=selection_ids,
        cell_id_column="cell_id",
        state_column="cell",
    )

    assert [row["cell_id"] for row in analyzed] == ["cell-3", "cell-1"]
    assert excluded == [{"cell_id": "cell-2", "reason": "missing_cell_state"}]
    assert accounting == {
        "selection_applied": True,
        "evaluation_cells": 3,
        "analyzed_cells": 2,
        "excluded_missing_state_cells": 1,
    }
    assert _invalid_propeller_rows([
        {
            "cluster": "T",
            "baseline_proportion": "0.2",
            "target_proportion": "0.3",
            "effect": "0.10000000000000003",
            "PValue": "0.05",
            "FDR": "0.1",
        }
    ]) == []


def test_propeller_rejects_incomplete_or_duplicate_selection() -> None:
    from pertura_workflow.capabilities.effect_candidates import (
        _propeller_analysis_rows,
    )

    fields = ["cell_id", "cell"]
    rows = [{"cell_id": "cell-1", "cell": "T"}]
    with pytest.raises(ValueError, match="missing 1 selected cells"):
        _propeller_analysis_rows(
            fields,
            rows,
            selection_ids=["cell-1", "cell-2"],
            cell_id_column="cell_id",
            state_column="cell",
        )


def test_scrublet_plan_scans_only_selected_cells_and_detected_features() -> None:
    class Inspection:
        X = np.asarray(
            [
                [1, 0, 9, 0],
                [0, 7, 9, 0],
                [2, 0, 9, 0],
                [0, 8, 9, 0],
                [3, 0, 9, 0],
            ],
            dtype=float,
        )
        n_vars = 4

    feature_mask, nonzero_count, dense_source = _scrublet_subset_plan(
        Inspection(), np.asarray([0, 2, 4]), chunk_rows=2
    )

    assert dense_source is True
    assert nonzero_count == 6
    assert feature_mask.tolist() == [True, False, True, False]
    import inspect

    assert "inspection.to_memory()" not in inspect.getsource(run_moi_doublet)
    assert "inspection[selected_indices, feature_mask].to_memory()" in inspect.getsource(
        run_moi_doublet
    )
