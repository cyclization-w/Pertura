from __future__ import annotations

import json
from pathlib import Path

from pertura_core import (
    CapabilityRunRequest,
    CapabilityTrust,
    DependencyRef,
    DiagnosticStatus,
    ResultEnvelope,
    ScopeKey,
    SourceClass,
)
from pertura_core.hashing import file_sha256
from pertura_runtime.verifier.broker import (
    _validate_declared_dependencies,
    _write_dependency_projection,
)
from pertura_workflow.capabilities import CapabilityRegistry


def _request(spec, dependencies=()):
    return CapabilityRunRequest(
        run_id="declared-dependency-test",
        capability_id=spec.capability_id,
        capability_version=spec.version,
        contract_id="contract_test",
        contract_hash="sha256:" + "a" * 64,
        scope=ScopeKey(dataset_id="dataset_test"),
        dependencies=dependencies,
    )


def test_trusted_verifier_rejects_missing_declared_scientific_dependencies() -> None:
    spec = CapabilityRegistry.load_default(include_external=False).get(
        "target.reliability.v2"
    )

    issues = _validate_declared_dependencies(spec, _request(spec))

    assert "missing capability dependency diagnostic.guide_assignment.v1" in issues
    assert "missing dependency kind retained_cell_manifest" in issues


def test_trusted_verifier_accepts_primary_and_provided_dependency_bindings() -> None:
    spec = CapabilityRegistry.load_default(include_external=False).get(
        "target.reliability.v2"
    )
    result_id = "result_guide_assignment"
    dependencies = (
        DependencyRef(
            kind="guide_assignment",
            object_id=result_id,
            object_hash="sha256:" + "b" * 64,
            role="diagnostic.guide_assignment.v1",
        ),
        DependencyRef(
            kind="retained_cell_manifest",
            object_id=result_id,
            object_hash="sha256:" + "b" * 64,
            role="diagnostic.guide_assignment.v1:provided",
        ),
    )

    assert _validate_declared_dependencies(spec, _request(spec, dependencies)) == ()


def test_dependency_projection_groups_aliases_and_preserves_authoritative_refs(
    tmp_path: Path,
) -> None:
    source = tmp_path / "retained_cells.csv"
    source.write_text("cell_id,retained\nc1,true\n", encoding="utf-8")
    scope = ScopeKey(dataset_id="dataset_test")
    result = ResultEnvelope(
        run_id="declared-dependency-test",
        request_id="guide-assignment-request",
        capability_id="diagnostic.guide_assignment.v1",
        capability_version="1.0.0",
        capability_trust=CapabilityTrust.builtin_trusted,
        contract_id="contract_test",
        contract_hash="sha256:" + "a" * 64,
        scope=scope,
        status=DiagnosticStatus.screen_passed,
        result_kind="guide_assignment",
        source_class=SourceClass.measured_result,
        summary="Guide assignment completed.",
        output_paths=(str(source),),
        output_hashes={source.name: file_sha256(source)},
    )
    dependencies = (
        DependencyRef(
            kind="guide_assignment",
            object_id=result.result_id,
            object_hash=result.canonical_hash,
            role="diagnostic.guide_assignment.v1",
        ),
        DependencyRef(
            kind="retained_cell_manifest",
            object_id=result.result_id,
            object_hash=result.canonical_hash,
            role="diagnostic.guide_assignment.v1:provided",
        ),
    )
    request = _request(
        CapabilityRegistry.load_default(include_external=False).get(
            "target.reliability.v2"
        ),
        dependencies,
    )

    class Store:
        @staticmethod
        def get_result(object_id: str):
            return result if object_id == result.result_id else None

    staging = tmp_path / "staging"
    staging.mkdir()
    _write_dependency_projection(Store(), request, staging, None)
    payload = json.loads(
        (staging / "_dependency_results.json").read_text(encoding="utf-8")
    )

    assert len(payload["results"]) == 1
    projected = payload["results"][0]
    assert {item["kind"] for item in projected["dependency_refs"]} == {
        "guide_assignment",
        "retained_cell_manifest",
    }
    assert len(projected["local_output_paths"]) == 1
    assert Path(projected["local_output_paths"][0]).read_text(encoding="utf-8") == source.read_text(encoding="utf-8")
