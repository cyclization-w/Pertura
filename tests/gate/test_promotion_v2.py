from __future__ import annotations

from pertura_core import (
    AnalysisStatus,
    CapabilityRunRequest,
    CapabilitySpec,
    DatasetContract,
    DependencyRef,
    ResultEnvelope,
    ScopeKey,
    ScientificStatement,
)
from pertura_gate.promotion import PromotionPolicy, decide_promotion
from pertura_runtime.verifier.receipts import ReceiptSigner


def _fixture():
    policy = PromotionPolicy()
    scope = ScopeKey(dataset_id="d", perturbation_ids=("KLF1",), control_ids=("NTC",))
    contract = DatasetContract(dataset_id="d", input_format="h5ad")
    dependencies = tuple(
        DependencyRef(kind=kind, object_id=f"{kind}:1", object_hash=f"sha256:{kind}")
        for kind in ("contract", "retained_cell_manifest", "target_reliability", "calibration")
    )
    request = CapabilityRunRequest(
        run_id="run",
        capability_id="de.pseudobulk.edger.v1",
        capability_version="1.0.0",
        contract_id=contract.contract_id,
        contract_hash=contract.canonical_hash,
        scope=scope,
        dependencies=dependencies,
    )
    result = ResultEnvelope(
        run_id="run",
        request_id=request.request_id,
        capability_id=request.capability_id,
        capability_version=request.capability_version,
        capability_trust="builtin_trusted",
        contract_id=contract.contract_id,
        contract_hash=contract.canonical_hash,
        scope=scope,
        status=AnalysisStatus.completed,
        result_kind="differential_expression",
        source_class="measured_result",
        summary="edgeR completed",
        dependencies=dependencies,
        metrics={"n_independent_units_per_arm": 3},
    )
    spec = CapabilitySpec(
        capability_id=request.capability_id,
        version=request.capability_version,
        phase=5,
        kind="analysis",
        summary="edgeR",
        executor="not_implemented",
        validator="standard",
        output_kind="differential_expression",
        source_class="measured_result",
        claim_permissions=("measured_association",),
    )
    statement = ScientificStatement(
        run_id="run",
        text="Free text is not used as authority.",
        source_class="measured_result",
        scope=scope,
        result_ids=(result.result_id,),
        requested_strength="measured_association",
    )
    signer = ReceiptSigner()
    receipt = signer.sign_result(request, result, policy_hash=policy.policy_hash, broker_instance_id="broker")
    return policy, signer, spec, statement, result, receipt


def test_strict_measured_statement_requires_valid_receipt_and_dependencies() -> None:
    policy, signer, spec, statement, result, receipt = _fixture()
    decision = decide_promotion(
        statement,
        results=[result],
        receipts=[receipt],
        capability_specs=[spec],
        authoritative_public_key=signer.public_key_b64,
        policy=policy,
    )
    assert decision.status == "promoted"
    assert decision.max_strength == "measured_association"


def test_receipt_copy_or_insufficient_replicates_cannot_promote() -> None:
    policy, signer, spec, statement, result, receipt = _fixture()
    weak_payload = result.model_dump(mode="json")
    weak_payload["metrics"] = {"n_independent_units_per_arm": 2}
    weak_payload["canonical_hash"] = ""
    weak = ResultEnvelope.model_validate(weak_payload)
    statement_payload = statement.model_dump(mode="json")
    statement_payload["result_ids"] = [weak.result_id]
    statement_payload["canonical_hash"] = ""
    weak_statement = ScientificStatement.model_validate(statement_payload)

    decision = decide_promotion(
        weak_statement,
        results=[weak],
        receipts=[receipt],
        capability_specs=[spec],
        authoritative_public_key=signer.public_key_b64,
        policy=policy,
    )
    assert decision.status == "downgraded"
    assert decision.max_strength == "observation"
    assert any("independent units" in reason for reason in decision.reasons)
    assert any("receipt" in reason for reason in decision.reasons)


def test_statement_wording_cannot_change_declared_source_class() -> None:
    policy, signer, spec, statement, result, receipt = _fixture()
    hypothesis = ScientificStatement(
        run_id="run",
        text="STRONG SIGNIFICANT CAUSAL measured result",
        source_class="hypothesis",
        scope=statement.scope,
        requested_strength="measured_association",
    )
    decision = decide_promotion(
        hypothesis,
        results=[result],
        receipts=[receipt],
        capability_specs=[spec],
        authoritative_public_key=signer.public_key_b64,
        policy=policy,
    )
    assert decision.max_strength == "hypothesis"
