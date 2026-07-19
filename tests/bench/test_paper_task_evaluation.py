from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from pertura_bench.paper_task_evaluation import evaluate_paper_task
from pertura_core.hashing import file_sha256


def _write_table(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(path, sep="\t", index=False)


def _trans_de_fixture(tmp_path: Path):
    paper = tmp_path / "paper"
    output = tmp_path / "task"
    reference = paper / "task_references/PAPA-06/reference.tsv"
    design_reference = paper / "task_references/PAPA-06/design.tsv"
    eligibility = paper / "task_references/PAPA-06/eligibility.tsv"
    rows = [
        {"target_uid": target, "gene": gene, "logFC": effect, "PValue": pvalue, "FDR": fdr}
        for target in ("T1", "T2")
        for gene, effect, pvalue, fdr in (
            ("G1", 2.0, 0.001, 0.003),
            ("G2", -1.0, 0.01, 0.02),
            ("G3", 0.2, 0.5, 0.6),
        )
    ]
    _write_table(reference, rows)
    _write_table(output / "trans_de_results.tsv", rows)
    design_rows = [
        {
            "target_uid": target,
            "sample_id": f"{target}-{replicate}-{condition}",
            "replicate_label": replicate,
            "condition_label": condition,
            "(Intercept)": 1,
            "replicaterep2": int(replicate == "rep2"),
            "conditiontarget": int(condition == "target"),
        }
        for target in ("T1", "T2")
        for replicate in ("rep1", "rep2")
        for condition in ("control", "target")
    ]
    _write_table(design_reference, design_rows)
    _write_table(output / "trans_de_design_matrices.tsv", design_rows)
    _write_table(
        eligibility,
        [
            {"target_uid": "T1", "eligible": "true"},
            {"target_uid": "T2", "eligible": "true"},
        ],
    )
    (output / "trans_de_design_manifest.json").write_text(
        json.dumps(
            {
                "formula": "~ replicate + condition",
                "baseline": "NTC",
                "robust": True,
                "cell_is_replicate": False,
                "guide_is_replicate": False,
                "minimum_paired_replicates": 2,
                "targets": ["T1", "T2"],
            }
        ),
        encoding="utf-8",
    )
    (output / "trans_de_summary.json").write_text(
        json.dumps({"eligible_targets": ["T1", "T2"], "target_count": 2}),
        encoding="utf-8",
    )
    binding = {
        "task_reference_id": "TREF-PAPA-06",
        "evaluator_id": "task.trans_de_edger.v1",
        "thresholds": {
            "target_macro_rank_concordance_min": 0.95,
            "logfc_mae_max": 1e-6,
            "top_k_overlap_min": 0.95,
            "fdr_agreement_min": 0.99,
        },
        "protocol": {"baseline": "NTC"},
        "bound_evaluator": {
            "observed_output": "trans_de_results.tsv",
            "design_matrices_output": "trans_de_design_matrices.tsv",
            "design_manifest_output": "trans_de_design_manifest.json",
            "summary_output": "trans_de_summary.json",
            "reference_path": "task_references/PAPA-06/reference.tsv",
            "reference_sha256": file_sha256(reference),
            "design_reference_path": "task_references/PAPA-06/design.tsv",
            "design_reference_sha256": file_sha256(design_reference),
            "eligibility_path": "task_references/PAPA-06/eligibility.tsv",
            "eligibility_sha256": file_sha256(eligibility),
            "top_k": 2,
        },
    }
    task = {"task_id": "PAPA-06"}
    result = {
        "analysis_unit": "target_by_replicate_pseudobulk",
        "artifact_roles": ["trans_de_results"],
    }
    return paper, output, task, result, binding, reference


def test_trans_de_reference_passes_and_rejects_analysis_unit_attacks(tmp_path: Path) -> None:
    paper, output, task, result, binding, _ = _trans_de_fixture(tmp_path)
    verdict = evaluate_paper_task(
        task,
        benchmark_result=result,
        task_output_root=output,
        paper_root=paper,
        bindings=[binding],
    )
    assert verdict["status"] == "passed"

    result["analysis_unit"] = "cell"
    attacked = evaluate_paper_task(
        task,
        benchmark_result=result,
        task_output_root=output,
        paper_root=paper,
        bindings=[binding],
    )
    assert attacked["status"] == "failed"

    result["analysis_unit"] = "target-by-replicate pseudobulk"
    noncanonical = evaluate_paper_task(
        task,
        benchmark_result=result,
        task_output_root=output,
        paper_root=paper,
        bindings=[binding],
    )
    assert noncanonical["status"] == "failed"


def test_trans_de_accepts_semantic_condition_roles_and_rejects_unknowns(
    tmp_path: Path,
) -> None:
    paper, output, task, result, binding, _ = _trans_de_fixture(tmp_path)
    design_path = output / "trans_de_design_matrices.tsv"
    design = pd.read_csv(design_path, sep="\t")
    design["condition_label"] = design.apply(
        lambda row: (
            "NTC"
            if str(row["condition_label"]) == "control"
            else str(row["target_uid"])
        ),
        axis=1,
    )
    design.to_csv(design_path, sep="\t", index=False)

    equivalent = evaluate_paper_task(
        task,
        benchmark_result=result,
        task_output_root=output,
        paper_root=paper,
        bindings=[binding],
    )
    assert equivalent["status"] == "passed"

    design.loc[design.index[0], "condition_label"] = "unknown-arm"
    design.to_csv(design_path, sep="\t", index=False)
    attacked = evaluate_paper_task(
        task,
        benchmark_result=result,
        task_output_root=output,
        paper_root=paper,
        bindings=[binding],
    )
    assert attacked["status"] == "failed"
    assert "neither a frozen control nor target role" in str(attacked)


def test_global_effect_literal_phrasing_is_non_dispositive(tmp_path: Path) -> None:
    paper = tmp_path / "paper"
    output = tmp_path / "task"
    evidence = paper / "task_references/PAPA-07/evidence.tsv"
    _write_table(
        evidence,
        [
            {"target_uid": "positive", "FDR": 0.01},
            {"target_uid": "negative", "FDR": 0.2},
        ],
    )
    _write_table(
        output / "global_effect_claims.tsv",
        [
            {
                "target_uid": "positive",
                "claim_class": "detectable",
                "interpretation": "The registered test supports a global association.",
            },
            {
                "target_uid": "negative",
                "claim_class": "not_detected",
                "interpretation": "Evidence was insufficient for a global association.",
            },
        ],
    )
    (output / "global_effect_limitations.json").write_text(
        json.dumps({"limitations": ["This analysis does not establish mechanism."]}),
        encoding="utf-8",
    )
    binding = {
        "task_reference_id": "TREF-PAPA-07",
        "evaluator_id": "task.global_effect_claims.v1",
        "thresholds": {
            "claim_class_macro_f1_min": 1.0,
            "overclaim_count_max": 0,
            "underclaim_count_max": 0,
            "limitation_accuracy_min": 1.0,
        },
        "bound_evaluator": {
            "observed_output": "global_effect_claims.tsv",
            "limitations_output": "global_effect_limitations.json",
            "evidence_path": "task_references/PAPA-07/evidence.tsv",
            "evidence_sha256": file_sha256(evidence),
        },
    }

    verdict = evaluate_paper_task(
        {"task_id": "PAPA-07"},
        benchmark_result={"analysis_unit": "target"},
        task_output_root=output,
        paper_root=paper,
        bindings=[binding],
    )

    assert verdict["status"] == "passed"
    evaluation = verdict["evaluations"][0]
    assert evaluation["lexical_compliance"]["affects_task_status"] is False
    assert evaluation["lexical_compliance"]["missing_scope_phrase_count"] == 1

def test_trans_de_rejects_wrong_baseline_forgery_and_reference_drift(tmp_path: Path) -> None:
    paper, output, task, result, binding, reference = _trans_de_fixture(tmp_path)
    design = json.loads((output / "trans_de_design_manifest.json").read_text())
    design["baseline"] = "target"
    (output / "trans_de_design_manifest.json").write_text(json.dumps(design))
    assert evaluate_paper_task(task, benchmark_result=result, task_output_root=output, paper_root=paper, bindings=[binding])["status"] == "failed"

    design["baseline"] = "NTC"
    design["robust"] = False
    (output / "trans_de_design_manifest.json").write_text(json.dumps(design))
    assert evaluate_paper_task(
        task,
        benchmark_result=result,
        task_output_root=output,
        paper_root=paper,
        bindings=[binding],
    )["status"] == "failed"

    design["robust"] = True
    (output / "trans_de_design_manifest.json").write_text(json.dumps(design))
    observed = pd.read_csv(output / "trans_de_results.tsv", sep="\t")
    observed.loc[0, "logFC"] = 99
    observed.to_csv(output / "trans_de_results.tsv", sep="\t", index=False)
    assert evaluate_paper_task(task, benchmark_result=result, task_output_root=output, paper_root=paper, bindings=[binding])["status"] == "failed"

    pd.read_csv(reference, sep="\t").to_csv(
        output / "trans_de_results.tsv", sep="\t", index=False
    )
    design_table = pd.read_csv(
        output / "trans_de_design_matrices.tsv", sep="\t"
    )
    design_table.loc[0, "conditiontarget"] = 1
    design_table.to_csv(
        output / "trans_de_design_matrices.tsv", sep="\t", index=False
    )
    assert evaluate_paper_task(
        task,
        benchmark_result=result,
        task_output_root=output,
        paper_root=paper,
        bindings=[binding],
    )["status"] == "failed"

    design_reference = paper / binding["bound_evaluator"]["design_reference_path"]
    pd.read_csv(design_reference, sep="\t").to_csv(
        output / "trans_de_design_matrices.tsv", sep="\t", index=False
    )
    reference.write_text(reference.read_text() + "\n", encoding="utf-8")
    assert evaluate_paper_task(task, benchmark_result=result, task_output_root=output, paper_root=paper, bindings=[binding])["status"] == "failed"


def test_global_effect_scores_positive_borderline_and_negative_claims(tmp_path: Path) -> None:
    paper = tmp_path / "paper"
    output = tmp_path / "task"
    evidence = paper / "task_references/PAPA-07/evidence.tsv"
    _write_table(
        evidence,
        [
            {"target_uid": "positive", "FDR": 0.01},
            {"target_uid": "borderline", "FDR": 0.08},
            {"target_uid": "negative", "FDR": 0.2},
        ],
    )
    _write_table(
        output / "global_effect_claims.tsv",
        [
            {"target_uid": "positive", "claim_class": "detectable", "interpretation": "A detectable global association was measured under this test."},
            {"target_uid": "borderline", "claim_class": "borderline", "interpretation": "This is a borderline candidate association under this test."},
            {"target_uid": "negative", "claim_class": "not_detected", "interpretation": "A global effect was not detected under this test."},
        ],
    )
    (output / "global_effect_limitations.json").write_text(
        json.dumps({"limitations": ["E-distance is not a mechanism test."]}),
        encoding="utf-8",
    )
    binding = {
        "task_reference_id": "TREF-PAPA-07",
        "evaluator_id": "task.global_effect_claims.v1",
        "thresholds": {
            "claim_class_macro_f1_min": 1.0,
            "overclaim_count_max": 0,
            "underclaim_count_max": 0,
            "limitation_accuracy_min": 1.0,
        },
        "bound_evaluator": {
            "observed_output": "global_effect_claims.tsv",
            "limitations_output": "global_effect_limitations.json",
            "evidence_path": "task_references/PAPA-07/evidence.tsv",
            "evidence_sha256": file_sha256(evidence),
        },
    }
    verdict = evaluate_paper_task(
        {"task_id": "PAPA-07"},
        benchmark_result={"analysis_unit": "target"},
        task_output_root=output,
        paper_root=paper,
        bindings=[binding],
    )
    assert verdict["status"] == "passed"

    claims = pd.read_csv(output / "global_effect_claims.tsv", sep="\t")
    claims.loc[claims.target_uid == "positive", "claim_class"] = "borderline"
    claims.loc[claims.target_uid == "negative", "interpretation"] = "The effect is absent."
    claims.to_csv(output / "global_effect_claims.tsv", sep="\t", index=False)
    attacked = evaluate_paper_task(
        {"task_id": "PAPA-07"},
        benchmark_result={"analysis_unit": "target"},
        task_output_root=output,
        paper_root=paper,
        bindings=[binding],
    )
    assert attacked["status"] == "failed"


def test_global_effect_macro_f1_does_not_require_an_absent_claim_class(
    tmp_path: Path,
) -> None:
    paper = tmp_path / "paper"
    output = tmp_path / "task"
    evidence = paper / "task_references/PAPA-07/evidence.tsv"
    _write_table(
        evidence,
        [
            {"target_uid": "positive", "FDR": 0.01},
            {"target_uid": "negative", "FDR": 0.2},
        ],
    )
    _write_table(
        output / "global_effect_claims.tsv",
        [
            {
                "target_uid": "positive",
                "claim_class": "detectable",
                "interpretation": "A detectable association was measured under this test.",
            },
            {
                "target_uid": "negative",
                "claim_class": "not_detected",
                "interpretation": "A global effect was not detected under this test.",
            },
        ],
    )
    (output / "global_effect_limitations.json").write_text(
        json.dumps({"limitations": ["E-distance is not a mechanism test."]}),
        encoding="utf-8",
    )
    binding = {
        "task_reference_id": "TREF-PAPA-07",
        "evaluator_id": "task.global_effect_claims.v1",
        "thresholds": {
            "claim_class_macro_f1_min": 1.0,
            "overclaim_count_max": 0,
            "underclaim_count_max": 0,
            "limitation_accuracy_min": 1.0,
        },
        "bound_evaluator": {
            "observed_output": "global_effect_claims.tsv",
            "limitations_output": "global_effect_limitations.json",
            "evidence_path": "task_references/PAPA-07/evidence.tsv",
            "evidence_sha256": file_sha256(evidence),
        },
    }
    verdict = evaluate_paper_task(
        {"task_id": "PAPA-07"},
        benchmark_result={"analysis_unit": "target"},
        task_output_root=output,
        paper_root=paper,
        bindings=[binding],
    )
    assert verdict["status"] == "passed"


def test_protocol_hard_gate_requires_scoped_language_and_rejects_overclaim(
    tmp_path: Path,
) -> None:
    binding = {
        "task_reference_id": "TREF-NORM-03",
        "evaluator_id": "task.correct_refusal.v1",
        "protocol_evaluator": {
            "allowed_status": ["blocked"],
            "allowed_analysis_units": ["dataset"],
            "minimum_limitation_count": 1,
            "required_text_patterns": ["cell-by-guide", "blocked"],
            "forbidden_text_patterns": ["fallback completed"],
        },
    }
    result = {
        "status": "blocked",
        "analysis_unit": "dataset",
        "findings": [
            {
                "text": "The run was blocked because cell-by-guide counts are missing."
            }
        ],
        "limitations": ["No real-data performance claim is available."],
    }
    passed = evaluate_paper_task(
        {"task_id": "NORM-03"},
        benchmark_result=result,
        task_output_root=tmp_path,
        paper_root=tmp_path,
        bindings=[binding],
    )
    assert passed["status"] == "passed"
    assert [route["route"] for route in passed["evaluations"][0]["routes"]] == [
        "structured_protocol_gate",
        "text_pattern_compliance",
    ]
    assert all(
        route["affects_task_status"] is True
        for route in passed["evaluations"][0]["routes"]
    )

    result["findings"][0]["text"] += " A fallback completed."
    failed = evaluate_paper_task(
        {"task_id": "NORM-03"},
        benchmark_result=result,
        task_output_root=tmp_path,
        paper_root=tmp_path,
        bindings=[binding],
    )
    assert failed["status"] == "failed"


def test_scientific_fidelity_records_lexical_miss_without_failing_science(
    tmp_path: Path,
) -> None:
    binding = {
        "task_reference_id": "TREF-PAPA-01",
        "evaluator_id": "task.guide_qc.v1",
        "evaluation_domain": "scientific_fidelity",
        "protocol_evaluator": {
            "allowed_status": ["completed"],
            "allowed_analysis_units": ["cell"],
            "minimum_limitation_count": 1,
            "required_text_patterns": ["multi-guide"],
        },
    }
    result = {
        "status": "completed",
        "analysis_unit": "cell",
        "findings": [
            {
                "text": (
                    "Secondary-guide ambiguity was compared with transcriptomic "
                    "doublet proxies."
                )
            }
        ],
        "limitations": ["Raw empty-droplet evidence is unavailable."],
    }

    evaluation = evaluate_paper_task(
        {"task_id": "PAPA-01"},
        benchmark_result=result,
        task_output_root=tmp_path,
        paper_root=tmp_path,
        bindings=[binding],
    )

    assert evaluation["status"] == "passed"
    routes = evaluation["evaluations"][0]["routes"]
    assert routes[0]["route"] == "structured_protocol_gate"
    assert routes[0]["status"] == "passed"
    assert routes[1]["route"] == "text_pattern_compliance"
    assert routes[1]["status"] == "failed"
    assert routes[1]["affects_task_status"] is False
    assert routes[1]["missing_required_patterns"] == ["multi-guide"]


def test_scientific_fidelity_still_fails_wrong_structured_analysis_unit(
    tmp_path: Path,
) -> None:
    binding = {
        "task_reference_id": "TREF-PAPA-01",
        "evaluator_id": "task.guide_qc.v1",
        "evaluation_domain": "scientific_fidelity",
        "protocol_evaluator": {
            "allowed_status": ["completed"],
            "allowed_analysis_units": ["cell"],
            "minimum_limitation_count": 1,
            "required_text_patterns": ["multi-guide"],
        },
    }
    result = {
        "status": "completed",
        "analysis_unit": "guide_assignment_and_qc",
        "findings": [{"text": "Multi-guide ambiguity was evaluated."}],
        "limitations": ["Raw empty-droplet evidence is unavailable."],
    }

    evaluation = evaluate_paper_task(
        {"task_id": "PAPA-01"},
        benchmark_result=result,
        task_output_root=tmp_path,
        paper_root=tmp_path,
        bindings=[binding],
    )

    assert evaluation["status"] == "failed"
    structured = evaluation["evaluations"][0]["routes"][0]
    assert structured["route"] == "structured_protocol_gate"
    assert structured["status"] == "failed"
    assert structured["problems"] == [
        "analysis unit violates the protocol gate"
    ]


def test_protocol_hard_gate_enforces_exact_table_row_count(tmp_path: Path) -> None:
    exclusions = tmp_path / "missing_state_exclusions.tsv"
    _write_table(exclusions, [{"cell_id": "c1", "reason": "missing state"}])
    (tmp_path / "composition_input_accounting.json").write_text(
        json.dumps(
            {
                "evaluation_cells": 10,
                "analyzed_cells": 9,
                "excluded_missing_state_cells": 1,
                "donor_count": 4,
            }
        ),
        encoding="utf-8",
    )
    binding = {
        "task_reference_id": "TREF-KANG-02",
        "evaluator_id": "task.kang_propeller.v1",
        "protocol_evaluator": {
            "allowed_status": ["completed"],
            "allowed_analysis_units": ["donor_composition"],
            "minimum_limitation_count": 1,
            "required_outputs": [
                "missing_state_exclusions.tsv",
                "composition_input_accounting.json",
            ],
            "required_table_row_counts": {"missing_state_exclusions.tsv": 1},
            "required_json_values": {
                "composition_input_accounting.json": {
                    "excluded_missing_state_cells": 1,
                    "donor_count": 4,
                }
            },
            "required_json_balances": [
                {
                    "output": "composition_input_accounting.json",
                    "total": "evaluation_cells",
                    "parts": ["analyzed_cells", "excluded_missing_state_cells"],
                }
            ],
        },
    }
    result = {
        "status": "completed",
        "analysis_unit": "donor_composition",
        "findings": [],
        "limitations": ["Four donors limit power."],
    }
    assert evaluate_paper_task(
        {"task_id": "KANG-02"},
        benchmark_result=result,
        task_output_root=tmp_path,
        paper_root=tmp_path,
        bindings=[binding],
    )["status"] == "passed"
    _write_table(
        exclusions,
        [
            {"cell_id": "c1", "reason": "missing state"},
            {"cell_id": "c2", "reason": "missing state"},
        ],
    )
    assert evaluate_paper_task(
        {"task_id": "KANG-02"},
        benchmark_result=result,
        task_output_root=tmp_path,
        paper_root=tmp_path,
        bindings=[binding],
    )["status"] == "failed"


def test_generic_task_evaluator_filters_reference_split(tmp_path: Path) -> None:
    paper = tmp_path / "paper"
    output = tmp_path / "output"
    reference = paper / "references/REF-02/retained.tsv"
    rows = [
        {
            "dataset_id": "D",
            "split": split,
            "cell_id": f"{split}-1",
            "expected_state": "retained",
        }
        for split in ("calibration", "evaluation")
    ]
    _write_table(reference, rows)
    _write_table(output / "retained.tsv", [rows[1]])
    binding = {
        "task_reference_id": "TREF-D",
        "evaluator_id": "task.retained.v1",
        "evaluators": [
            {
                "evaluator_id": "retained",
                "type": "classification",
                "observed_output": "retained.tsv",
                "reference_path": "references/REF-02/retained.tsv",
                "reference_sha256": file_sha256(reference),
                "reference_filters": {
                    "dataset_id": "D",
                    "split": "evaluation",
                },
                "key_columns": ["dataset_id", "split", "cell_id"],
                "observed_label_column": "expected_state",
                "reference_label_column": "expected_state",
                "minimum_accuracy": 1.0,
                "minimum_macro_f1": 1.0,
            }
        ],
    }
    result = {
        "status": "completed",
        "analysis_unit": "cell",
        "output_paths": ["retained.tsv"],
    }
    verdict = evaluate_paper_task(
        {"task_id": "D"},
        benchmark_result=result,
        task_output_root=output,
        paper_root=paper,
        bindings=[binding],
    )
    assert verdict["status"] == "passed"
