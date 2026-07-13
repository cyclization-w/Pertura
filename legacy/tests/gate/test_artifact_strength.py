from __future__ import annotations

import json
from pathlib import Path

from pertura_gate.evidence.registry import EvidenceRegistry
from pertura_gate.render.renderer import render_evidence_report
from pertura_gate.resolver.resolver import resolve_artifact_strength
from pertura_gate.core.schema import EvidenceArtifact, EvidencePredicate, EvidenceTier, StrengthCeiling


def test_register_measured_de_artifact_success(tmp_path: Path) -> None:
    artifact_path = tmp_path / "outputs" / "de.csv"
    artifact_path.parent.mkdir()
    artifact_path.write_text("gene,pvals_adj\nA,0.01\n", encoding="utf-8")
    registry = EvidenceRegistry(tmp_path / "artifacts" / "evidence_artifacts.jsonl")

    artifact = registry.register_measured_de(
        path="outputs/de.csv",
        contrast_left="DUSP9_NegCtrl0__DUSP9_NegCtrl0",
        contrast_baseline="NegCtrl0_NegCtrl0__NegCtrl0_NegCtrl0",
        method="scanpy.tl.rank_genes_groups",
        n_left=754,
        n_baseline=2560,
        multiple_testing="benjamini-hochberg",
        has_padj=True,
        columns=["gene", "pvals_adj"],
    )

    loaded = registry.get(artifact.artifact_id)
    assert loaded is not None
    assert loaded.contrast_baseline == "NegCtrl0_NegCtrl0__NegCtrl0_NegCtrl0"


def test_artifact_evidence_predicate_serializes_and_infers_legacy_payload(tmp_path: Path) -> None:
    registry = EvidenceRegistry(tmp_path / "artifacts" / "evidence_artifacts.jsonl")
    artifact = registry.register_module_effect(
        path="outputs/module_effect.json",
        module_id="erythroid_module",
        module_source="curated_gene_set",
        module_gene_set_hash="sha256:module",
        scoring_method="score_genes",
        effect_size=0.5,
        method="wilcoxon",
        padj=0.01,
        n_target_cells=100,
        n_control_cells=120,
    )

    payload = artifact.to_dict()
    assert payload["evidence_predicate"] == EvidencePredicate.module_score_shift.value

    legacy_payload = dict(payload)
    legacy_payload.pop("evidence_predicate")
    loaded = EvidenceArtifact.from_dict(legacy_payload)

    assert loaded.effective_evidence_predicate == EvidencePredicate.module_score_shift


def test_resolver_measured_de_to_association(tmp_path: Path) -> None:
    registry = EvidenceRegistry(tmp_path / "evidence.jsonl")
    artifact = registry.register_measured_de(
        path="outputs/de.csv",
        contrast_left="left",
        contrast_baseline="baseline",
        method="wilcoxon",
        n_left=20,
        n_baseline=30,
        multiple_testing="bh",
        has_padj=True,
    )

    resolved = resolve_artifact_strength(artifact)

    assert resolved.tier == EvidenceTier.measured
    assert resolved.ceiling == StrengthCeiling.measured_association


def test_resolver_missing_baseline_to_observation(tmp_path: Path) -> None:
    registry = EvidenceRegistry(tmp_path / "evidence.jsonl")
    artifact = registry.register_measured_de(
        path="outputs/de.csv",
        contrast_left="left",
        contrast_baseline=None,
        method="wilcoxon",
        n_left=20,
        n_baseline=30,
        multiple_testing="bh",
        has_padj=True,
    )

    resolved = resolve_artifact_strength(artifact)

    assert resolved.tier == EvidenceTier.measured
    assert resolved.ceiling == StrengthCeiling.observation
    assert "contrast.baseline" in resolved.reasons[0]


def test_renderer_measured_association_no_causal_wording(tmp_path: Path) -> None:
    registry = EvidenceRegistry(tmp_path / "artifacts" / "evidence_artifacts.jsonl")
    artifact = registry.register_measured_de(
        path="outputs/de.csv",
        contrast_left="left",
        contrast_baseline="baseline",
        method="wilcoxon",
        n_left=20,
        n_baseline=30,
        multiple_testing="bh",
        has_padj=True,
        notes="model said this drives a phenotype",
    )

    report = render_evidence_report(
        registry=registry,
        artifact_ids=[artifact.artifact_id],
        write_path=tmp_path / "reports" / "evidence_report.md",
    )

    text = report.markdown.lower()
    assert "measured association" in text
    assert "drives" not in text
    assert "causal" not in text
    assert (tmp_path / "reports" / "evidence_report.md").exists()


def test_unsupported_missing_artifact(tmp_path: Path) -> None:
    registry = EvidenceRegistry(tmp_path / "evidence.jsonl")

    report = render_evidence_report(registry=registry)

    assert report.resolutions[0].tier == EvidenceTier.unsupported
    assert report.resolutions[0].ceiling == StrengthCeiling.unsupported
    assert "No registered measured evidence" in report.markdown


def test_registry_jsonl_is_append_only(tmp_path: Path) -> None:
    registry = EvidenceRegistry(tmp_path / "evidence.jsonl")
    registry.register_measured_de(
        path="outputs/a.csv",
        contrast_left="a",
        contrast_baseline="ctrl",
        method="wilcoxon",
        n_left=1,
        n_baseline=1,
        multiple_testing="bh",
        has_padj=True,
    )
    registry.register_measured_de(
        path="outputs/b.csv",
        contrast_left="b",
        contrast_baseline="ctrl",
        method="wilcoxon",
        n_left=1,
        n_baseline=1,
        multiple_testing="bh",
        has_padj=True,
    )

    rows = [json.loads(line) for line in (tmp_path / "evidence.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 2
    assert [row["contrast"]["left"] for row in rows] == ["a", "b"]


def test_renderer_accepts_artifact_path_reference(tmp_path: Path) -> None:
    registry = EvidenceRegistry(tmp_path / "artifacts" / "evidence_artifacts.jsonl")
    artifact = registry.register_measured_de(
        path="outputs/DE_KLF1_vs_NegCtrl.csv",
        contrast_left="KLF1",
        contrast_baseline="NegCtrl",
        method="wilcoxon",
        n_left=1980,
        n_baseline=12015,
        multiple_testing="bh",
        has_padj=True,
    )

    report = render_evidence_report(
        registry=registry,
        artifact_ids=["outputs/DE_KLF1_vs_NegCtrl.csv"],
    )

    assert report.artifacts == [artifact]
    assert report.resolutions[0].ceiling == StrengthCeiling.measured_association
    assert "No registered measured evidence" not in report.markdown


def test_renderer_accepts_unique_artifact_basename_reference(tmp_path: Path) -> None:
    registry = EvidenceRegistry(tmp_path / "artifacts" / "evidence_artifacts.jsonl")
    artifact = registry.register_measured_de(
        path="outputs/de_klf1_vs_negctrl.csv",
        contrast_left="KLF1",
        contrast_baseline="NegCtrl",
        method="wilcoxon",
        n_left=1980,
        n_baseline=12015,
        multiple_testing="bh",
        has_padj=True,
    )

    report = render_evidence_report(
        registry=registry,
        artifact_ids=["de_klf1_vs_negctrl.csv"],
    )

    assert report.artifacts == [artifact]
    assert report.resolutions[0].ceiling == StrengthCeiling.measured_association
    assert "Unresolved artifact references" not in report.markdown


def test_renderer_does_not_guess_ambiguous_artifact_basename(tmp_path: Path) -> None:
    registry = EvidenceRegistry(tmp_path / "artifacts" / "evidence_artifacts.jsonl")
    registry.register_measured_de(
        path="outputs/a/de.csv",
        contrast_left="A",
        contrast_baseline="NegCtrl",
        method="wilcoxon",
        n_left=20,
        n_baseline=30,
        multiple_testing="bh",
        has_padj=True,
    )
    registry.register_measured_de(
        path="outputs/b/de.csv",
        contrast_left="B",
        contrast_baseline="NegCtrl",
        method="wilcoxon",
        n_left=20,
        n_baseline=30,
        multiple_testing="bh",
        has_padj=True,
    )

    report = render_evidence_report(
        registry=registry,
        artifact_ids=["de.csv"],
    )

    assert report.artifacts == []
    assert "Unresolved artifact references" in report.markdown

def test_renderer_uses_composition_effect_artifact_wording(tmp_path: Path) -> None:
    registry = EvidenceRegistry(tmp_path / "artifacts" / "evidence_artifacts.jsonl")
    artifact = registry.register_composition_effect(
        path="outputs/composition_effect_summary.json",
        state_source="cell_state_reference_1",
        state_assignment_column="state_label",
        comparison_method="fisher_exact",
        state_counts_by_condition={
            "KLF1": {"erythroid": 42, "other": 18},
            "negative_control_pool": {"erythroid": 20, "other": 40},
        },
        state_level_deltas={"erythroid": 0.37},
        pvalue=0.002,
        n_target_cells=60,
        n_control_cells=60,
    )

    report = render_evidence_report(
        registry=registry,
        artifact_ids=[artifact.artifact_id],
    )

    text = report.markdown.lower()
    assert report.resolutions[0].ceiling == StrengthCeiling.measured_association
    assert "cell-state composition" in text
    assert "differential-expression artifact" not in text
    assert "using `none`" not in text



def test_virtual_prediction_predicate_serializes_and_artifact_report_is_prediction_specific(tmp_path: Path) -> None:
    registry = EvidenceRegistry(tmp_path / "artifacts" / "evidence_artifacts.jsonl")
    artifact = registry.register_virtual_perturbation_prediction(
        path="outputs/virtual_prediction.json",
        tool_name="GEARS",
        model_name="toy-gears",
        model_checkpoint_hash="sha256:model",
        prediction_method="inference",
        prediction_type="delta_expression",
        perturbation_query={"perturbation": "KLF1"},
        output_schema={"columns": ["gene", "delta"]},
        n_predicted_genes=10,
    )

    payload = artifact.to_dict()
    assert payload["evidence_predicate"] == EvidencePredicate.predicted_perturbation_response.value
    report = render_evidence_report(registry=registry, artifact_ids=[artifact.artifact_id])

    assert report.resolutions[0].ceiling == StrengthCeiling.predicted_effect
    text = report.markdown.lower()
    assert "virtual perturbation prediction" in text
    assert "experimental result" in text
