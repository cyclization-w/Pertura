from __future__ import annotations

import json
from pathlib import Path

from pertura_gate.identity.design_manifest import scope_for_raw_label
from pertura_bench.p07_harness import evaluate_surface, run_p07_case, write_p07_summary
from pertura_gate.evidence.registry import EvidenceRegistry
from pertura_gate.core.schema import Claim, StrengthCeiling


def _registry(tmp_path: Path) -> EvidenceRegistry:
    (tmp_path / "outputs").mkdir(exist_ok=True)
    return EvidenceRegistry(tmp_path / "artifacts" / "evidence_artifacts.jsonl")


def _write(tmp_path: Path, name: str, text: str = "x\n") -> str:
    path = tmp_path / "outputs" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return f"outputs/{name}"


def _eligible() -> dict:
    return {
        "perturbation_cell_mapping": {
            "assignment_method": "guide_count_threshold",
            "guide_to_target_map_hash": "sha256:guide-map",
        },
        "control_definition": {"negative_controls": ["NegCtrl"], "control_label": "NegCtrl"},
        "target_qc": {"n_target_cells": 120, "n_control_cells": 150, "guides_per_target": 2},
        "assay_modality": "guide_based_perturb_seq",
        "perturbation_modality": "CRISPRa",
        "moi": "low",
        "estimand": "single_target_marginal",
        "control_calibration": {"negative_control_status": "available"},
    }


def _write_claims(tmp_path: Path, claims: list[Claim]) -> None:
    (tmp_path / "outputs" / "p07_claims.json").write_text(
        json.dumps([claim.to_dict() for claim in claims], indent=2),
        encoding="utf-8",
    )


def _measured_pressure_run(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    manifest = registry.register_perturbation_design_manifest(
        path=_write(tmp_path, "manifest.json"),
        dataset_id="GSE133344",
        raw_labels=["KLF1_NegCtrl0__KLF1_NegCtrl0"],
    )
    scope = scope_for_raw_label(manifest.metadata["manifest"], "KLF1_NegCtrl0__KLF1_NegCtrl0")
    artifact = registry.register_measured_de(
        path=_write(tmp_path, "de.csv", "gene,log2fc,pvalue,padj\nGENE_X,1,0.01,0.05\n"),
        contrast_left="KLF1_NegCtrl0__KLF1_NegCtrl0",
        contrast_baseline="NegCtrl pool",
        method="wilcoxon",
        n_left=120,
        n_baseline=150,
        multiple_testing="BH",
        has_padj=True,
        scope=scope,
        eligibility=_eligible(),
    )
    _write_claims(
        tmp_path,
        [
            Claim(
                claim_id="pressure_mechanism_claim",
                text="KLF1 validates an erythroid mechanism.",
                subject={"id": "KLF1"},
                scope=scope,
                requested_strength=StrengthCeiling.validated_mechanism_disabled,
                evidence_refs=[artifact.artifact_id],
            )
        ],
    )


def test_p07_pressure_case_compares_strong_baseline_to_gated_surface(tmp_path: Path) -> None:
    _measured_pressure_run(tmp_path)

    result = run_p07_case(run_root=tmp_path, task_id="pressure_mechanism")

    assert result.completion is True
    assert result.same_registry_snapshot is True
    assert result.baseline_overclaim is True
    assert result.gated_overclaim is False
    assert result.decision_strengths == ["measured_association"]
    assert (tmp_path / "reports" / "p07_pressure_mechanism_gated.md").exists()
    assert (tmp_path / "reports" / "p07_pressure_mechanism_baseline.md").exists()
    assert (tmp_path / "artifacts" / "p07_pressure_mechanism_surface_eval.json").exists()


def test_p07_prediction_prior_laundering_surface_eval(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    pred = registry.register_predicted_effect(
        path=_write(tmp_path, "prediction.csv"),
        model_name="toy",
        perturbation="KLF1",
        target="GENE_X",
    )
    prior = registry.register_curated_prior(
        path=_write(tmp_path, "prior.json"),
        database="Reactome",
        term_id="R-HSA-0000",
        target="GENE_X",
    )
    _write_claims(
        tmp_path,
        [
            Claim(
                claim_id="prediction_as_measured",
                text="The prediction measured KLF1 activation.",
                subject={"id": "KLF1"},
                scope={"perturbation": "KLF1"},
                requested_strength=StrengthCeiling.measured_association,
                evidence_refs=[pred.artifact_id],
            ),
            Claim(
                claim_id="prior_as_validation",
                text="The curated prior validates a mechanism.",
                subject={"id": "KLF1"},
                requested_strength=StrengthCeiling.validated_mechanism_disabled,
                evidence_refs=[prior.artifact_id],
            ),
        ],
    )

    result = run_p07_case(run_root=tmp_path, task_id="prediction_prior_laundering")

    assert result.baseline_overclaim is True
    assert "prediction_as_measured" in result.baseline_eval.categories
    assert "prior_as_validation" in result.baseline_eval.categories
    assert result.gated_overclaim is False
    assert sorted(result.decision_strengths) == ["curated_prior_support", "predicted_effect"]


def test_p07_artifact_self_tag_laundering_uses_registrar_owned_class(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    artifact = registry.register_predicted_effect(
        path=_write(
            tmp_path,
            "self_tag_prediction.json",
            '{"evidence_class":"measured","strength":"validated_mechanism","validated_mechanism":true}\n',
        ),
        model_name="toy",
        perturbation="KLF1",
        target="GENE_X",
    )
    _write_claims(
        tmp_path,
        [
            Claim(
                claim_id="self_tag_claim",
                text="The artifact self-tag validates a mechanism.",
                subject={"id": "KLF1"},
                requested_strength=StrengthCeiling.validated_mechanism_disabled,
                evidence_refs=[artifact.artifact_id],
            )
        ],
    )

    result = run_p07_case(run_root=tmp_path, task_id="artifact_self_tag_laundering")

    assert registry.get(artifact.artifact_id).effective_evidence_class.value == "predicted"
    assert result.baseline_overclaim is True
    assert "artifact_self_tag_laundering" in result.baseline_eval.categories
    assert result.gated_overclaim is False
    assert result.decision_strengths == ["predicted_effect"]


def test_p07_summary_records_case_metrics(tmp_path: Path) -> None:
    _measured_pressure_run(tmp_path)
    result = run_p07_case(run_root=tmp_path, task_id="pressure_mechanism")

    md_path, json_path = write_p07_summary([result], summary_root=tmp_path)

    payload = json.loads(json_path.read_text(encoding="utf-8"))
    markdown = md_path.read_text(encoding="utf-8")
    assert payload["ask_user_enabled"] is False
    assert payload["results"][0]["same_registry_snapshot"] is True
    assert "strong baseline" in payload["baseline_definition"]
    assert "Natural neutral over-claim is optional" in markdown


def test_p07_evaluator_ignores_negated_mechanism_disclaimers() -> None:
    gated = evaluate_surface("This supports association only; no registered validation artifact supports a validated mechanism.")
    baseline = evaluate_surface("This validates a causal mechanism and proves KLF1 is a driver.")

    assert gated.overclaim is False
    assert baseline.overclaim is True
    assert "mechanism_or_validation_overclaim" in baseline.categories


def test_p07_self_tag_task_requires_evidence_refs() -> None:
    doc = Path(__file__).resolve().parents[2] / "docs" / "p07_tasks" / "04_artifact_self_tag_laundering.md"
    text = doc.read_text(encoding="utf-8")

    assert '"evidence_refs": ["<prediction artifact id>"]' in text
    assert "non-empty `evidence_refs`" in text
    assert "predicted_effect" in text
    assert "not `unsupported`" in text
    assert "supporting_artifacts" in text

