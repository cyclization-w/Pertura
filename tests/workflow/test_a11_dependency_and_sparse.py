from __future__ import annotations

import csv
from pathlib import Path

import pytest
from scipy import sparse

from pertura_core import CapabilitySpec, DatasetContract
from pertura_core.hashing import canonical_hash
from pertura_workflow.capabilities import CapabilityRegistry, CapabilityRegistryError
from pertura_workflow.capabilities.guide_counts import open_guide_count_source
from pertura_workflow.capabilities.registry import capability_scientific_hash
from pertura_workflow.planner import plan_analysis


def _fixture_spec(
    capability_id: str,
    *,
    phase: int,
    depends_on: tuple[str, ...] = (),
) -> CapabilitySpec:
    policy = {
        dependency: {
            "scope": "exact",
            "usage": "scientific_input",
            "accepted_statuses": ["completed"],
        }
        for dependency in depends_on
    }
    return CapabilitySpec(
        capability_id=capability_id,
        version="0.1.0",
        phase=phase,
        kind="analysis",
        summary="fixture",
        trust_level="exploratory",
        executor="contract_integrity",
        validator="standard",
        output_kind=f"{capability_id}_result",
        source_class="measured_result",
        depends_on=depends_on,
        metadata={"dependency_policy": policy},
    )


def test_cross_phase_dependency_is_legal_and_cycle_is_not() -> None:
    late = _fixture_spec("fixture.late.v1", phase=6)
    early = _fixture_spec(
        "fixture.early.v1", phase=1, depends_on=(late.capability_id,)
    )
    registry = CapabilityRegistry((early, late))
    assert registry.get(early.capability_id).depends_on == (late.capability_id,)

    first = _fixture_spec(
        "fixture.cycle.first.v1", phase=1, depends_on=("fixture.cycle.second.v1",)
    )
    second = _fixture_spec(
        "fixture.cycle.second.v1", phase=6, depends_on=(first.capability_id,)
    )
    with pytest.raises(CapabilityRegistryError, match="cycle"):
        CapabilityRegistry((first, second))


def test_phase_is_excluded_from_scientific_capability_identity() -> None:
    spec = CapabilityRegistry.load_default(include_external=False).get(
        "effect.guide_target_sensitivity.v1"
    )
    moved = spec.model_copy(update={"phase": spec.phase + 40})
    assert capability_scientific_hash(moved) == capability_scientific_hash(spec)
    assert canonical_hash(dict(moved.metadata["dependency_policy"])) == canonical_hash(
        dict(spec.metadata["dependency_policy"])
    )


def test_every_declared_dependency_has_explicit_scientific_policy() -> None:
    registry = CapabilityRegistry.load_default(include_external=False)
    for spec in registry.specs():
        policy = dict(spec.metadata.get("dependency_policy") or {})
        assert set(policy) == set(spec.depends_on)
        for dependency in spec.depends_on:
            assert policy[dependency]["scope"] in {
                "exact", "dataset", "same_dataset_context", "compatible"
            }
            assert policy[dependency]["usage"] in {
                "scientific_input", "row_filter", "validation_gate",
                "parameter_source", "provenance_only",
            }
            assert policy[dependency]["accepted_statuses"]


def test_unknown_or_inferred_moi_never_selects_effect_backend() -> None:
    for status, value in (("confirmed", "unknown"), ("inferred", "low")):
        contract = DatasetContract(
            dataset_id=f"moi-{status}",
            input_format="h5ad",
            guide_matrix={"path": "guide.h5ad"},
            identity_fields={
                "control": {"status": "confirmed", "value": ["NTC"]},
                "replicate": {"status": "confirmed", "value": ["r1", "r2", "r3"]},
                "design_moi": {"status": status, "value": value},
                "guide_design": {"status": "confirmed", "value": "single"},
            },
        )
        plan = plan_analysis("differential_expression", contract=contract)
        assert plan.status == "blocked"
        assert plan.capability_id is None
        assert any("explicitly" in blocker for blocker in plan.blockers)


def test_guide_count_source_streams_sparse_chunks_and_blocks_text_over_budget(
    tmp_path: Path,
) -> None:
    path = tmp_path / "guides.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["barcode", "g1", "g2", "g3"])
        for index in range(20):
            writer.writerow([f"cell-{index}", 4 if index % 3 == 0 else 0, 2 if index % 5 == 0 else 0, 0])

    source = open_guide_count_source(path, chunk_rows=4, max_memory_gb=0.01)
    try:
        chunks = list(source.iter_row_chunks(4))
        assert source.shape == (20, 3)
        assert all(sparse.isspmatrix_csr(chunk) for _, chunk in chunks)
        assert sum(chunk.shape[0] for _, chunk in chunks) == 20
        assert sum(chunk.nnz for _, chunk in chunks) == source.matrix.nnz
    finally:
        source.close()

    with pytest.raises(MemoryError, match="compatibility-table budget"):
        open_guide_count_source(path, max_memory_gb=1e-9)
