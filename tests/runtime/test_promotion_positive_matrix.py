from __future__ import annotations

import pytest

from pertura_core import (
    AnalysisStatus,
    CapabilityRunRequest,
    CapabilityTrust,
    DependencyRef,
    PromotionPolicy,
    ResultEnvelope,
    ScientificStatement,
    ScopeKey,
    SourceClass,
    decide_promotion,
)
from pertura_runtime.verifier.receipts import ReceiptSigner
from pertura_workflow.capabilities import CapabilityRegistry


@pytest.mark.parametrize(
    ("independent_unit_metric", "independent_unit_count"),
    [
        ("n_paired_units", 3),
        ("n_paired_units", 4),
        ("n_independent_units_per_arm", 3),
        ("n_independent_units_per_arm", 4),
    ],
)
def test_strict_promotion_accepts_complete_trusted_measured_evidence(
    independent_unit_metric: str,
    independent_unit_count: int,
) -> None:
    policy = PromotionPolicy()
    capability = CapabilityRegistry.load_default(include_external=False).get(
        "de.pseudobulk.edger.v1"
    )
    scope = ScopeKey(
        dataset_id="promotion-fixture",
        donor_ids=("d1", "d2", "d3", "d4"),
        contrast_id="stim-vs-control",
        estimand="donor-level mean response",
    )
    dependencies = tuple(
        DependencyRef(
            kind=kind,
            object_id=f"{kind}:promotion-fixture",
            object_hash="sha256:" + str(index) * 64,
        )
        for index, kind in enumerate(capability.dependency_kinds, start=1)
    )
    request = CapabilityRunRequest(
        run_id="promotion-positive-run",
        capability_id=capability.capability_id,
        capability_version=capability.version,
        contract_id="contract_promotion_fixture",
        contract_hash="sha256:" + "a" * 64,
        scope=scope,
        dependencies=dependencies,
    )
    result = ResultEnvelope(
        run_id=request.run_id,
        request_id=request.request_id,
        capability_id=request.capability_id,
        capability_version=request.capability_version,
        capability_trust=CapabilityTrust.builtin_trusted,
        contract_id=request.contract_id,
        contract_hash=request.contract_hash,
        scope=scope,
        status=AnalysisStatus.completed,
        result_kind=capability.output_kind,
        source_class=SourceClass.measured_result,
        summary="Trusted measured fixture completed.",
        metrics={independent_unit_metric: independent_unit_count},
        output_hashes={"de_results.tsv": "sha256:" + "b" * 64},
        dependencies=dependencies,
    )
    signer = ReceiptSigner()
    receipt = signer.sign_result(
        request,
        result,
        policy_hash=policy.policy_hash,
        broker_instance_id="broker_promotion_fixture",
    )
    statement = ScientificStatement(
        run_id=request.run_id,
        text="The donor-level contrast supports a measured association.",
        source_class=SourceClass.measured_result,
        scope=scope,
        result_ids=(result.result_id,),
        requested_strength="measured_association",
    )

    decision = decide_promotion(
        statement,
        results=(result,),
        receipts=(receipt,),
        capability_specs=(capability,),
        authoritative_public_key=signer.public_key_b64,
        policy=policy,
    )

    assert decision.status == "promoted"
    assert decision.max_strength == statement.requested_strength
    assert decision.result_ids == (result.result_id,)
    assert decision.receipt_ids == (receipt.receipt_id,)
    assert set(decision.dependency_ids) == {
        dependency.dependency_id for dependency in dependencies
    }
    assert decision.policy_hash == policy.policy_hash
    assert decision.reasons == ()
