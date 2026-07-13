from __future__ import annotations

from pathlib import Path

from pertura_gate.core.schema import ArtifactKind, EvidenceClass, EvidencePredicate, default_evidence_class, default_evidence_predicate
from pertura_gate.evidence.catalog import EVIDENCE_CATALOG, evidence_type_for_kind
from pertura_gate.evidence.registry import EvidenceRegistry


def test_every_artifact_kind_has_evidence_catalog_entry() -> None:
    covered = {definition.artifact_kind for definition in EVIDENCE_CATALOG.values()}
    assert set(ArtifactKind).issubset(covered)


def test_schema_defaults_are_catalog_lookups() -> None:
    for kind in ArtifactKind:
        definition = evidence_type_for_kind(kind)
        assert definition is not None
        assert default_evidence_predicate(kind) == definition.evidence_predicate
        assert default_evidence_class(kind) == definition.evidence_class


def test_register_evidence_composition_matches_specific_registrar_semantics(tmp_path: Path) -> None:
    registry = EvidenceRegistry(tmp_path / "artifacts" / "evidence_artifacts.jsonl")
    path = tmp_path / "composition.csv"
    path.write_text("state,target,control\nA,5,3\n", encoding="utf-8")

    artifact = registry.register_evidence(
        "composition_effect",
        path=path,
        scope={"dataset_id": "local"},
        state_source="cell_state_reference_abc",
        state_assignment_column="state_label",
        comparison_method="synthetic",
        quality={"state_counts_by_condition": {"state_a": {"target": 5, "control": 3}}},
    )

    assert artifact.kind == ArtifactKind.composition_effect
    assert artifact.effective_evidence_class == EvidenceClass.measured
    assert artifact.effective_evidence_predicate == EvidencePredicate.cell_state_composition_shift
    assert artifact.provenance["created_by_tool"] == "register_composition_effect_artifact"


def test_family_dispatchers_are_catalog_driven() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    registry_source = (repo_root / "src" / "pertura_gate" / "evidence" / "registry.py").read_text(encoding="utf-8")
    assert "if subtype ==" not in registry_source
