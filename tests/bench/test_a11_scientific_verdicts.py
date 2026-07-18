from __future__ import annotations

import json
from pathlib import Path

from pertura_bench.agent_models import AgentNarrativeScore, narrative_passes
from pertura_bench.real_execution import (
    _evaluate_metric_references,
    evaluate_agent_metric_references,
    load_design_confirmation_catalog,
    load_metric_reference_catalog,
    load_real_parameter_catalog,
)
from pertura_bench.server_plan import build_server_plan
from pertura_bench.capability_bench import benchmark_specs
from pertura_core.hashing import file_sha256


def test_execution_success_without_reference_is_not_scientifically_complete() -> None:
    evaluation = _evaluate_metric_references(
        {"status": "completed", "metrics": {"n_rows": 12}, "output_hashes": {}},
        dataset_id="fixture",
        capability_id="fixture.capability.v1",
        capability_version="0.1.0",
        catalog={"datasets": {}},
        catalog_hash="sha256:" + "1" * 64,
    )
    assert evaluation["status"] == "not_available"
    assert evaluation["continuous_metrics"] == {}
    assert evaluation["reference_hashes"]["metric_reference_catalog"].startswith(
        "sha256:"
    )


def test_frozen_and_reported_only_metrics_are_distinct() -> None:
    base = {
        "datasets": {
            "fixture": {
                "capabilities": {
                    "fixture.capability.v1@0.1.0": {
                        "required_outputs": ["table.csv"],
                        "reported_metrics": ["ari"],
                        "metrics": [],
                        "reference_hashes": {},
                    }
                }
            }
        }
    }
    reported = _evaluate_metric_references(
        {"metrics": {"ari": 0.91}},
        dataset_id="fixture",
        capability_id="fixture.capability.v1",
        capability_version="0.1.0",
        catalog=base,
        catalog_hash="sha256:" + "2" * 64,
    )
    assert reported["status"] == "reported_only"
    assert reported["continuous_metrics"] == {"ari": 0.91}
    assert reported["required_outputs"] == ("table.csv",)

    frozen = json.loads(json.dumps(base))
    entry = frozen["datasets"]["fixture"]["capabilities"][
        "fixture.capability.v1@0.1.0"
    ]
    entry["reported_metrics"] = []
    entry["metrics"] = [
        {
            "name": "ari",
            "result_metric": "ari",
            "reference": 0.95,
            "comparison": "absolute_error",
            "tolerance": 0.01,
        }
    ]
    indexed = _evaluate_metric_references(
        {"metrics": {"ari": 0.91}},
        dataset_id="fixture",
        capability_id="fixture.capability.v1",
        capability_version="0.1.0",
        catalog=frozen,
        catalog_hash="sha256:" + "3" * 64,
    )
    assert indexed["status"] == "reported_only"
    assert indexed["continuous_metrics"]["ari__error"] > 0.01
    assert indexed["metric_bindings"] == ()


def test_paper_partial_contracts_are_provenance_backed_and_not_shallow() -> None:
    catalog, digest = load_design_confirmation_catalog()

    assert digest.startswith("sha256:")
    assert catalog["catalog_version"] == "a19-partial-contract-v1"
    assert set(catalog["datasets"]) == {
        "replogle_k562_essential_2022",
        "papalexi_thp1_eccite",
        "norman_k562_crispra_2019",
        "kang18_8vs8_pbmc",
    }
    for dataset_id, record in catalog["datasets"].items():
        contract = record["paper_contract"]
        assert contract["provenance"]["basis"]
        assert contract["provenance"]["confirmed_by"] == "paper_protocol"
        assert contract["asset_availability"]
        assert len(contract["unresolved_fields"]) == len(
            set(contract["unresolved_fields"])
        )
        for fact in contract["identity_fields"].values():
            if fact["status"] == "confirmed":
                assert fact["source"]
        assert "reference" not in json.dumps(contract).lower(), dataset_id
        assert "threshold" not in json.dumps(contract).lower(), dataset_id


def test_external_catalog_hashes_bind_server_plan(tmp_path: Path) -> None:
    parameters, _ = load_real_parameter_catalog()
    parameters["datasets"]["norman_k562_crispra_2019"]["agent_assets"] = [
        {
            "role": "prediction_bundle",
            "relative_path": "agent-assets/norman/predictions.zarr",
            "content_sha256": "sha256:" + "9" * 64,
            "kind": "external_resource",
            "source_class": "prediction",
        }
    ]
    design, _ = load_design_confirmation_catalog()
    metrics, _ = load_metric_reference_catalog()
    parameter_path = tmp_path / "parameters.json"
    design_path = tmp_path / "design.json"
    metric_path = tmp_path / "metrics.json"
    for path, payload in (
        (parameter_path, parameters),
        (design_path, design),
        (metric_path, metrics),
    ):
        path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")

    root = Path(__file__).resolve().parents[2]
    plan = build_server_plan(
        benchmark_specs(),
        root,
        parameter_catalog_path=parameter_path,
        design_confirmations_path=design_path,
        metric_reference_catalog_path=metric_path,
    )
    assert plan.checkpoint_binding["parameter_catalog_hash"].startswith("sha256:")
    assert plan.checkpoint_binding["design_confirmation_catalog_hash"].startswith(
        "sha256:"
    )
    assert plan.checkpoint_binding["metric_reference_catalog_hash"].startswith(
        "sha256:"
    )
    agent_jobs = [job for job in plan.jobs if job["kind"] == "agent_workflow"]
    assert len(agent_jobs) == 48
    assert sum(job["benchmark_track"] == "primary" for job in agent_jobs) == 36
    assert sum(job["benchmark_track"] == "supplemental" for job in agent_jobs) == 12
    prediction_artifact = next(
        item
        for item in plan.artifacts
        if item["artifact_id"]
        == "artifact:norman_k562_crispra_2019:evaluation:agent:prediction_bundle"
    )
    assert prediction_artifact["content_sha256"] == "sha256:" + "9" * 64
    norman_primary_jobs = [
        item
        for item in plan.jobs
        if item.get("case_id") == "agent_norman_sceptre_refusal"
    ]
    assert norman_primary_jobs
    assert all(
        prediction_artifact["artifact_id"] not in item["consumes"]
        for item in norman_primary_jobs
    )
    optional_p5_jobs = [
        item
        for item in plan.jobs
        if item.get("capability_id") == "virtual.evaluate.comprehensive.v1"
    ]
    assert optional_p5_jobs
    assert all(
        item["optional_execution_gate"]["release_blocking"] is False
        for item in optional_p5_jobs
    )


def test_agent_narrative_automatic_failure_overrides_high_scores() -> None:
    score = AgentNarrativeScore(
        scientific_completeness=4,
        clarity=4,
        limitations_uncertainty=4,
        actionability=4,
        rationale="Fluent but invalid.",
        automatic_failures=("cell_as_independent_replicate",),
    )
    assert narrative_passes(score) is False


def test_artifact_numeric_reference_is_compared_before_workspace_cleanup(tmp_path: Path) -> None:
    output_dir = tmp_path / "outputs"
    reference_dir = tmp_path / "references"
    output_dir.mkdir()
    reference_dir.mkdir()
    observed = output_dir / "edger.csv"
    reference = reference_dir / "edger-reference.csv"
    observed.write_text(
        "gene_id,logFC,PValue,FDR\ng1,1.5,0.01,0.02\ng2,-0.5,0.2,0.3\n",
        encoding="utf-8",
    )
    reference.write_text(
        "gene_id,logFC,PValue,FDR\ng2,-0.5,0.2,0.3\ng1,1.5,0.01,0.02\n",
        encoding="utf-8",
    )
    catalog = {
        "datasets": {
            "fixture": {
                "capabilities": {
                    "fixture.capability.v1@0.1.0": {
                        "required_outputs": [],
                        "metrics": [],
                        "reported_metrics": [],
                        "reference_hashes": {},
                        "evaluators": [
                            {
                                "evaluator_id": "direct_r",
                                "type": "table_numeric",
                                "observed_output": "edger.csv",
                                "reference_path": "edger-reference.csv",
                                "reference_sha256": file_sha256(reference),
                                "key_columns": ["gene_id"],
                                "value_columns": ["logFC", "PValue", "FDR"],
                                "absolute_tolerance": 1e-7,
                                "relative_tolerance": 1e-7,
                            }
                        ],
                    }
                }
            }
        }
    }

    evaluation = _evaluate_metric_references(
        {"metrics": {}, "output_paths": ["outputs/edger.csv"]},
        dataset_id="fixture",
        capability_id="fixture.capability.v1",
        capability_version="0.1.0",
        catalog=catalog,
        catalog_hash="sha256:" + "4" * 64,
        output_root=tmp_path,
        reference_root=reference_dir,
    )

    assert evaluation["status"] == "passed"
    assert evaluation["continuous_metrics"]["direct_r.logFC.failed_values"] == 0
    assert evaluation["reference_hashes"]["metric_reference:direct_r"] == file_sha256(reference)


def test_artifact_classification_reference_reports_macro_f1_and_false_block(tmp_path: Path) -> None:
    output_dir = tmp_path / "outputs"
    reference_dir = tmp_path / "references"
    output_dir.mkdir()
    reference_dir.mkdir()
    observed = output_dir / "verdicts.csv"
    reference = reference_dir / "verdict-reference.csv"
    observed.write_text(
        "target,verdict\nT1,screen_passed\nT2,blocked\nT3,caution\n",
        encoding="utf-8",
    )
    reference.write_text(
        "target,label\nT1,screen_passed\nT2,caution\nT3,caution\n",
        encoding="utf-8",
    )
    evaluator = {
        "evaluator_id": "target_labels",
        "type": "classification",
        "observed_output": "verdicts.csv",
        "reference_path": "verdict-reference.csv",
        "reference_sha256": file_sha256(reference),
        "key_columns": ["target"],
        "observed_label_column": "verdict",
        "reference_label_column": "label",
        "minimum_macro_f1": 0.9,
        "blocked_label": "blocked",
        "maximum_false_block_rate": 0.1,
    }
    catalog = {
        "datasets": {
            "fixture": {
                "capabilities": {
                    "fixture.capability.v1@0.1.0": {
                        "evaluators": [evaluator]
                    }
                }
            }
        }
    }

    evaluation = _evaluate_metric_references(
        {"metrics": {}, "output_paths": ["outputs/verdicts.csv"]},
        dataset_id="fixture",
        capability_id="fixture.capability.v1",
        capability_version="0.1.0",
        catalog=catalog,
        catalog_hash="sha256:" + "5" * 64,
        output_root=tmp_path,
        reference_root=reference_dir,
    )

    assert evaluation["status"] == "failed"
    assert evaluation["continuous_metrics"]["target_labels.false_block_rate"] == 1 / 3
    assert evaluation["continuous_metrics"]["target_labels.non_blocked_reference_count"] == 3
    assert evaluation["continuous_metrics"]["target_labels.macro_f1"] < 0.9

def test_agent_common_result_uses_frozen_case_specific_metric_reference() -> None:
    catalog = {
        "datasets": {
            "fixture": {
                "agent_cases": {
                    "agent-case": {
                        "runs": {
                            "frozen_subset:evaluation": {
                                "metrics": [
                                    {
                                        "name": "effect",
                                        "result_metric": "effect",
                                        "reference": 1.5,
                                        "comparison": "absolute_error",
                                        "tolerance": 0.01,
                                    }
                                ]
                            }
                        }
                    }
                }
            }
        }
    }
    passed = evaluate_agent_metric_references(
        {"metrics": {"effect": 1.505}},
        dataset_id="fixture",
        case_id="agent-case",
        catalog=catalog,
        catalog_hash="sha256:" + "6" * 64,
    )
    failed = evaluate_agent_metric_references(
        {"metrics": {"effect": 2.0}},
        dataset_id="fixture",
        case_id="agent-case",
        catalog=catalog,
        catalog_hash="sha256:" + "6" * 64,
    )
    assert passed["status"] == "reported_only"
    assert failed["status"] == "reported_only"


def test_metric_catalog_requires_bound_reference_generator_and_provenance(
    tmp_path: Path,
) -> None:
    payload, _ = load_metric_reference_catalog()
    payload["datasets"]["replogle_k562_essential_2022"]["agent_cases"] = {
        "agent-case": {
            "runs": {
                "frozen_subset:evaluation": {
                    "metrics": [
                        {
                            "name": "effect",
                            "reference": 1.0,
                            "comparison": "absolute_error",
                            "tolerance": 0.1,
                        }
                    ],
                    "reference_generator_id": "published_official_output_v1",
                }
            }
        }
    }
    path = tmp_path / "metrics.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    import pytest

    with pytest.raises(ValueError, match="provenance is incomplete"):
        load_metric_reference_catalog(path)

    entry = payload["datasets"]["replogle_k562_essential_2022"][
        "agent_cases"
    ]["agent-case"]["runs"]["frozen_subset:evaluation"]
    entry["reference_provenance"] = {
        "doi_or_pmid": "PMID:fixture",
        "table_or_supplement": "Table S1",
        "source_artifact_sha256": "sha256:" + "a" * 64,
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    _, digest = load_metric_reference_catalog(path)
    assert digest.startswith("sha256:")
