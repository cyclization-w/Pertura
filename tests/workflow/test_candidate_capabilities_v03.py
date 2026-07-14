from __future__ import annotations

import csv
import json
from pathlib import Path

import numpy as np

from pertura_core import CapabilityRunRequest, DatasetContract, ScopeKey
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

    batched = _run(
        "target.guide_efficacy.v1",
        contract,
        tmp_path / "batch_efficacy",
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
    )
    assert batched.metrics["target_count"] == 2
    payload = json.loads(
        (tmp_path / "batch_efficacy" / "target_guide_efficacy.json").read_text(
            encoding="utf-8"
        )
    )
    assert payload["schema_version"] == "pertura-target-guide-efficacy-set-v1"
    assert [item["target_gene"] for item in payload["targets"]] == ["TG", "S1"]

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

    def fake_runner(profile, runner, config_path, *, timeout):
        config = json.loads(Path(config_path).read_text(encoding="utf-8"))
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
                "cluster,PropMean,FDR\nS1,0.5,0.1\nS2,0.5,0.2\n",
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
