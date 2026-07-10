from __future__ import annotations

from pathlib import Path

from pertura_gate.core.schema import ArtifactKind, EvidenceClass, StrengthCeiling
from pertura_gate.evidence.registry import EvidenceRegistry
from pertura_gate.resolver.resolver import resolve_artifact_strength


def _write(path: Path, name: str, text: str = "x\n") -> Path:
    target = path / name
    target.write_text(text, encoding="utf-8")
    return target


def test_family_measured_de_subtype_preserves_p1_artifact_semantics(tmp_path: Path) -> None:
    registry = EvidenceRegistry(tmp_path / "artifacts" / "evidence_artifacts.jsonl")
    artifact = registry.register_measured_effect_artifact(
        path=_write(tmp_path, "de.csv", "gene,logfc,padj\nKLF1,-1,0.01\n"),
        artifact_subtype="measured_de",
        contrast_left="KLF1",
        contrast_baseline="NegCtrl",
        method="wilcoxon",
        n_left=20,
        n_baseline=20,
        multiple_testing="BH",
        has_padj=True,
    )

    resolution = resolve_artifact_strength(artifact)

    assert artifact.kind == ArtifactKind.measured_de
    assert artifact.effective_evidence_class == EvidenceClass.measured
    assert artifact.provenance["created_by_tool"] == "register_measured_de_artifact"
    assert resolution.ceiling == StrengthCeiling.measured_association


def test_family_prediction_subtype_preserves_predicted_effect_semantics(tmp_path: Path) -> None:
    registry = EvidenceRegistry(tmp_path / "artifacts" / "evidence_artifacts.jsonl")
    artifact = registry.register_prediction_artifact(
        path=_write(tmp_path, "prediction.csv", "target,score\nKLF1,0.8\n"),
        artifact_subtype="predicted_effect",
        model_name="mock_model",
        model_version="v1",
        prediction_method="synthetic",
        perturbation="KLF1",
        target="GENE_X",
        metadata={"evidence_class": "measured", "validated_mechanism": True},
    )

    resolution = resolve_artifact_strength(artifact)

    assert artifact.kind == ArtifactKind.predicted_effect
    assert artifact.effective_evidence_class == EvidenceClass.predicted
    assert resolution.ceiling == StrengthCeiling.predicted_effect


def test_family_composition_effect_subtype_uses_specific_registrar(tmp_path: Path) -> None:
    registry = EvidenceRegistry(tmp_path / "artifacts" / "evidence_artifacts.jsonl")
    artifact = registry.register_measured_effect_artifact(
        path=_write(tmp_path, "composition.csv"),
        artifact_subtype="composition_effect",
        scope={"dataset_id": "local"},
        state_source="cell_state_reference_abc",
        state_assignment_column="state_label",
        comparison_method="synthetic",
        quality={"state_counts_by_condition": {"state_a": {"target": 5, "control": 3}}},
    )

    resolution = resolve_artifact_strength(artifact)

    assert artifact.kind == ArtifactKind.composition_effect
    assert artifact.provenance["created_by_tool"] == "register_composition_effect_artifact"
    assert artifact.effective_evidence_class == EvidenceClass.measured
    assert resolution.ceiling == StrengthCeiling.observation
