from __future__ import annotations

from pathlib import Path

from pertura_gate.core.schema import Claim, StrengthCeiling
from pertura_gate.evidence.registry import EvidenceRegistry
from pertura_gate.resolver.resolver import resolve_artifact_strength, resolve_claim


def test_cell_state_reference_registers_as_context_only(tmp_path: Path) -> None:
    outputs = tmp_path / "outputs"
    outputs.mkdir()
    summary = outputs / "state_reference_summary.json"
    summary.write_text('{"assignment_column":"leiden"}\n', encoding="utf-8")
    registry = EvidenceRegistry(tmp_path / "artifacts" / "evidence_artifacts.jsonl")

    artifact = registry.register_cell_state_reference(
        path="outputs/state_reference_summary.json",
        assignment_column="leiden",
        embedding_methods=["PCA", "UMAP"],
        clustering_method="leiden",
        annotation_method="marker_summary",
        marker_summary_path="outputs/cluster_markers.csv",
        source_data_path="outputs/annotated.h5ad",
        scope={"dataset_id": "synthetic"},
    )

    resolved = resolve_artifact_strength(artifact)
    assert artifact.kind.value == "cell_state_reference"
    assert artifact.effective_evidence_class.value == "observed_metadata"
    assert "scope_definition" in [role.value if hasattr(role, "value") else role for role in artifact.artifact_roles]
    assert "state_context" in [role.value if hasattr(role, "value") else role for role in artifact.artifact_roles]
    assert resolved.ceiling == StrengthCeiling.observation

def test_cell_state_reference_preserves_structured_embedding_metadata(tmp_path: Path) -> None:
    outputs = tmp_path / "outputs"
    outputs.mkdir()
    summary = outputs / "state_reference_summary.json"
    summary.write_text('{"assignment_column":"leiden"}\n', encoding="utf-8")
    registry = EvidenceRegistry(tmp_path / "artifacts" / "evidence_artifacts.jsonl")

    artifact = registry.register_cell_state_reference(
        path="outputs/state_reference_summary.json",
        assignment_column="leiden",
        embedding_methods=[
            {"method": "PCA", "n_components": 20},
            {"method": "UMAP", "basis": "X_umap"},
        ],
        clustering_method="leiden",
        scope={"dataset_id": "synthetic"},
    )

    assert artifact.quality["embedding_methods"] == [
        {"method": "PCA", "n_components": 20},
        {"method": "UMAP", "basis": "X_umap"},
    ]
    assert artifact.metadata["state_reference"]["embedding_methods"][0]["method"] == "PCA"


def test_cell_state_reference_alone_cannot_support_effect_claim(tmp_path: Path) -> None:
    outputs = tmp_path / "outputs"
    outputs.mkdir()
    summary = outputs / "state_reference_summary.json"
    summary.write_text('{"assignment_column":"leiden"}\n', encoding="utf-8")
    registry = EvidenceRegistry(tmp_path / "artifacts" / "evidence_artifacts.jsonl")
    artifact = registry.register_cell_state_reference(
        path="outputs/state_reference_summary.json",
        assignment_column="leiden",
        embedding_methods=["PCA", "UMAP"],
        clustering_method="leiden",
        scope={"dataset_id": "synthetic"},
    )
    claim = Claim(
        claim_id="cell_state_as_effect",
        text="The Leiden state reference proves KLF1 causes a perturbation effect.",
        subject={"id": "KLF1"},
        object={"id": "leiden"},
        scope={"dataset_id": "synthetic"},
        requested_strength=StrengthCeiling.measured_association,
        evidence_refs=[artifact.artifact_id],
    )

    decision = resolve_claim(claim, registry)

    assert decision.max_strength == StrengthCeiling.observation
    assert "mechanism" not in decision.allowed_surface.lower()
    assert "validates" not in decision.allowed_surface.lower()