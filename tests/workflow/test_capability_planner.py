from __future__ import annotations

from pertura_core import (
    AnalysisStatus,
    CapabilityTrust,
    DatasetContract,
    DependencyRef,
    DiagnosticStatus,
    ResultEnvelope,
    ScopeKey,
    SourceClass,
)
from pertura_core.hashing import canonical_hash
from pertura_workflow.capabilities import CapabilityRegistry
from pertura_workflow.capabilities.registry import capability_scientific_hash
from pertura_workflow.planner import (
    plan_analysis,
    plan_requested_capability,
    resolve_dependencies,
)


def _contract(
    *,
    replicates: tuple[str, ...] = ("r1", "r2", "r3"),
    moi: str = "low",
    guide_design: str = "single",
) -> DatasetContract:
    return DatasetContract(
        dataset_id="dataset",
        input_format="csv",
        guide_matrix={"path": "guides.csv"},
        identity_fields={
            "control": {"status": "confirmed", "value": ["NTC"]},
            "replicate": {"status": "confirmed", "value": list(replicates)},
            "design_moi": {"status": "confirmed", "value": moi},
            "guide_design": {"status": "confirmed", "value": guide_design},
        },
    )


def _result(
    contract: DatasetContract,
    scope: ScopeKey,
    *,
    capability_id: str,
    result_kind: str,
    capability_version: str = "0.1.0",
    trust: CapabilityTrust = CapabilityTrust.exploratory,
    status=DiagnosticStatus.screen_passed,
    stale: bool = False,
    metrics: dict | None = None,
    dependencies: tuple[DependencyRef, ...] = (),
) -> ResultEnvelope:
    result_metadata = {}
    try:
        upstream_spec = CapabilityRegistry.load_default(include_external=False).get(
            capability_id, capability_version
        )
    except Exception:
        upstream_spec = None
    if upstream_spec is not None:
        result_metadata = {
            "capability_spec_hash": capability_scientific_hash(upstream_spec),
            "dependency_policy_hash": canonical_hash(
                dict(upstream_spec.metadata.get("dependency_policy") or {})
            ),
        }
    return ResultEnvelope(
        run_id="run",
        request_id=f"request-{capability_id}",
        capability_id=capability_id,
        capability_version=capability_version,
        capability_trust=trust,
        contract_id=contract.contract_id,
        contract_hash=contract.canonical_hash,
        scope=scope,
        status=status,
        result_kind=result_kind,
        source_class=SourceClass.measured_result,
        summary="fixture",
        stale=stale,
        metrics=metrics or {},
        dependencies=dependencies,
        metadata=result_metadata,
    )


def test_planner_routes_from_design_facts_instead_of_objective_keywords() -> None:
    contract = _contract(moi="high", guide_design="combinatorial")
    scope = ScopeKey(dataset_id=contract.dataset_id)
    retained = _result(
        contract,
        scope,
        capability_id="screen.retained_cells.v1",
        result_kind="retained_cell_manifest",
        metrics={"design_moi": "high"},
    )
    plan = plan_analysis(
        "measured_effect",
        contract=contract,
        committed_results=(retained,),
    )
    assert plan.capability_id == "association.sceptre.v1"
    assert plan.status == "ready"


def test_planner_blocks_non_replicated_low_moi_expression() -> None:
    contract = _contract(replicates=())
    plan = plan_analysis("differential_expression", contract=contract)
    assert plan.status == "blocked"
    assert plan.capability_id == "de.pseudobulk.edger.v1"
    assert "no replicate-aware expression route is available" in plan.blockers


def test_target_reliability_route_guides_diagnostic_dependency_chain() -> None:
    contract = _contract()
    scope = ScopeKey(dataset_id=contract.dataset_id)
    retained = _result(
        contract,
        scope,
        capability_id="screen.retained_cells.v1",
        result_kind="retained_cell_manifest",
    )

    analysis_plan = plan_analysis(
        "target reliability",
        contract=contract,
        committed_results=(retained,),
    )
    assert analysis_plan.capability_id == "target.reliability.aggregate.v1"
    assert analysis_plan.status == "blocked"
    assert any("matching product tool" in item for item in analysis_plan.blockers)
    assert {
        "target.guide_efficacy.v1",
        "target.responder.mixscape.v1",
    }.issubset(analysis_plan.required_upstream)

    diagnostic_plan = plan_requested_capability(
        "target.reliability.aggregate.v1",
        expected_kind="diagnostic",
        contract=contract,
        committed_results=(retained,),
    )
    assert diagnostic_plan.status == "ready"
    assert diagnostic_plan.capability_id == "target.reliability.aggregate.v1"
    assert diagnostic_plan.required_upstream == analysis_plan.required_upstream


def test_retained_cells_accepts_unresolved_ambient_only_as_validation_gate() -> None:
    contract = _contract()
    scope = ScopeKey(dataset_id=contract.dataset_id)
    results = (
        _result(
            contract,
            scope,
            capability_id="guide.assignment.nb_mixture.v1",
            result_kind="guide_assignment",
        ),
        _result(
            contract,
            scope,
            capability_id="screen.moi_doublet.v1",
            result_kind="moi_doublet",
        ),
        _result(
            contract,
            scope,
            capability_id="guide.ambient.v1",
            result_kind="guide_ambient",
            status=DiagnosticStatus.unresolved,
        ),
    )

    resolution = resolve_dependencies(
        CapabilityRegistry.load_default(include_external=False).get(
            "screen.retained_cells.v1"
        ),
        contract=contract,
        required_scope=scope,
        committed_results=results,
        registry=CapabilityRegistry.load_default(include_external=False),
    )

    assert resolution.ok
    ambient = next(
        item
        for item in resolution.dependency_verdicts
        if item["capability_id"] == "guide.ambient.v1"
    )
    assert ambient["usable"] is True


def test_dependency_resolution_rebuilds_hash_kind_and_state() -> None:
    registry = CapabilityRegistry.load_default(include_external=False)
    spec = registry.get("effect.guide_target_sensitivity.v1")
    contract = _contract()
    scope = ScopeKey(dataset_id=contract.dataset_id)
    upstream = _result(
        contract,
        scope,
        capability_id="target.guide_efficacy.v1",
        result_kind="target_guide_efficacy",
    )

    forged = resolve_dependencies(
        spec,
        contract=contract,
        required_scope=scope,
        committed_results=(upstream,),
        dependency_hints=(
            {
                "object_id": upstream.result_id,
                "object_hash": "sha256:forged",
                "kind": upstream.result_kind,
                "state": "current",
            },
        ),
    )
    assert forged.status == "blocked"
    assert any("hash mismatch" in blocker for blocker in forged.blockers)

    resolved = resolve_dependencies(
        spec,
        contract=contract,
        required_scope=scope,
        committed_results=(upstream,),
        dependency_hints=({"object_id": upstream.result_id},),
    )
    assert resolved.ok
    assert [item.kind for item in resolved.dependencies] == [
        "contract",
        "target_guide_efficacy",
    ]


def test_dependency_resolution_blocks_stale_ambiguous_and_untrusted() -> None:
    registry = CapabilityRegistry.load_default(include_external=False)
    contract = _contract()
    scope = ScopeKey(dataset_id=contract.dataset_id)

    candidate_spec = registry.get("effect.guide_target_sensitivity.v1")
    first = _result(
        contract,
        scope,
        capability_id="target.guide_efficacy.v1",
        result_kind="target_guide_efficacy",
    )
    second = _result(
        contract,
        scope,
        capability_id="target.guide_efficacy.v1",
        result_kind="target_guide_efficacy",
        metrics={"variant": 2},
    )
    ambiguous = resolve_dependencies(
        candidate_spec,
        contract=contract,
        required_scope=scope,
        committed_results=(first, second),
    )
    assert ambiguous.status == "blocked"
    assert len(ambiguous.ambiguous_result_ids) == 2

    stale = _result(
        contract,
        scope,
        capability_id="target.guide_efficacy.v1",
        result_kind="target_guide_efficacy",
        stale=True,
    )
    missing = resolve_dependencies(
        candidate_spec,
        contract=contract,
        required_scope=scope,
        committed_results=(stale,),
    )
    assert missing.required_upstream == ("target.guide_efficacy.v1",)

    trusted_spec = registry.get("de.pseudobulk.edger.v1")
    target = _result(
        contract,
        scope,
        capability_id="target.reliability.v2",
        result_kind="target_reliability",
        capability_version="2.0.0",
        trust=CapabilityTrust.builtin_trusted,
        status=DiagnosticStatus.screen_passed,
    )
    calibration = _result(
        contract,
        scope,
        capability_id="calibration.replicate_null.v1",
        result_kind="calibration",
        capability_version="1.0.0",
        trust=CapabilityTrust.builtin_trusted,
        status=AnalysisStatus.completed,
    )
    unreceipted = resolve_dependencies(
        trusted_spec,
        contract=contract,
        required_scope=scope,
        committed_results=(target, calibration),
    )
    assert unreceipted.status == "blocked"

    receipted = resolve_dependencies(
        trusted_spec,
        contract=contract,
        required_scope=scope,
        committed_results=(target, calibration),
        trusted_receipt_result_ids=(target.result_id, calibration.result_id),
    )
    assert receipted.ok

    cautious_target = _result(
        contract,
        scope,
        capability_id="target.reliability.v2",
        result_kind="target_reliability",
        capability_version="2.0.0",
        trust=CapabilityTrust.builtin_trusted,
        status=DiagnosticStatus.caution,
    )
    cautious_resolution = resolve_dependencies(
        trusted_spec,
        contract=contract,
        required_scope=scope,
        committed_results=(cautious_target, calibration),
        trusted_receipt_result_ids=(cautious_target.result_id, calibration.result_id),
    )
    assert cautious_resolution.status == "blocked"
    target_verdict = next(
        item
        for item in cautious_resolution.dependency_verdicts
        if item["capability_id"] == "target.reliability.v2"
    )
    assert "status_not_accepted" in target_verdict["reasons"]


def test_dependency_resolution_rejects_obsolete_upstream_version() -> None:
    registry = CapabilityRegistry.load_default(include_external=False)
    contract = _contract()
    scope = ScopeKey(dataset_id=contract.dataset_id)
    obsolete = _result(
        contract,
        scope,
        capability_id="target.guide_efficacy.v1",
        capability_version="999.0.0",
        result_kind="target_guide_efficacy",
    )

    resolution = resolve_dependencies(
        registry.get("effect.guide_target_sensitivity.v1"),
        contract=contract,
        required_scope=scope,
        committed_results=(obsolete,),
        registry=registry,
    )

    assert resolution.status == "blocked"
    assert resolution.required_upstream == ("target.guide_efficacy.v1",)
    assert resolution.dependency_verdicts[0]["reasons"] == [
        "capability_version_mismatch"
    ]


def test_wrong_kind_and_scope_mismatch_fail_closed() -> None:
    spec = CapabilityRegistry.load_default(include_external=False).get(
        "effect.guide_target_sensitivity.v1"
    )
    contract = _contract()
    required = ScopeKey(dataset_id=contract.dataset_id, state_ids=("state-a",))
    wrong = _result(
        contract,
        ScopeKey(dataset_id=contract.dataset_id, state_ids=("state-b",)),
        capability_id="target.guide_efficacy.v1",
        result_kind="wrong_kind",
        status=AnalysisStatus.completed,
    )
    resolution = resolve_dependencies(
        spec,
        contract=contract,
        required_scope=required,
        committed_results=(wrong,),
    )
    assert resolution.status == "blocked"

def test_trusted_effect_resolution_flattens_scientific_dependencies() -> None:
    registry = CapabilityRegistry.load_default(include_external=False)
    contract = _contract()
    scope = ScopeKey(dataset_id=contract.dataset_id)
    retained = DependencyRef(
        kind="retained_cell_manifest",
        object_id="result_guide_assignment",
        object_hash="sha256:" + "a" * 64,
        role="diagnostic.guide_assignment.v1:provided",
    )
    target = _result(
        contract,
        scope,
        capability_id="target.reliability.v2",
        result_kind="target_reliability",
        capability_version="2.0.0",
        trust=CapabilityTrust.builtin_trusted,
        status=DiagnosticStatus.screen_passed,
        dependencies=(retained,),
    )
    calibration = _result(
        contract,
        scope,
        capability_id="calibration.replicate_null.v1",
        result_kind="calibration",
        capability_version="1.0.0",
        trust=CapabilityTrust.builtin_trusted,
        status=AnalysisStatus.completed,
        dependencies=(retained,),
    )

    resolution = resolve_dependencies(
        registry.get("de.pseudobulk.edger.v1"),
        contract=contract,
        required_scope=scope,
        committed_results=(target, calibration),
        trusted_receipt_result_ids=(target.result_id, calibration.result_id),
        registry=registry,
    )

    assert resolution.ok
    assert {item.kind for item in resolution.dependencies} >= {
        "contract",
        "retained_cell_manifest",
        "target_reliability",
        "calibration",
    }
    assert all(item["usable"] for item in resolution.dependency_verdicts)


def test_planner_does_not_route_substring_objectives() -> None:
    contract = _contract()
    plan = plan_analysis(
        "please perform differential expression and explain it",
        contract=contract,
    )
    assert plan.status == "blocked"
    assert plan.capability_id is None
    assert any("exactly match" in blocker for blocker in plan.blockers)


def test_explicit_capability_is_validated_without_objective_keyword_veto() -> None:
    contract = _contract()
    retained = _result(
        contract,
        ScopeKey(dataset_id=contract.dataset_id),
        capability_id="screen.retained_cells.v1",
        result_kind="retained_cell_manifest",
    )
    plan = plan_analysis(
        "custom user objective with no route alias",
        contract=contract,
        committed_results=(retained,),
        requested_capability_id="de.pseudobulk.edger.v1",
    )
    assert plan.status == "ready"
    assert plan.capability_id == "de.pseudobulk.edger.v1"
