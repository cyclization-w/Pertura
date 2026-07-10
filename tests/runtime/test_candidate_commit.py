from __future__ import annotations

from pathlib import Path

from pertura_core import (
    CapabilityRunRequest,
    DatasetContract,
    ResultEnvelope,
    ScientificStatement,
    ScopeKey,
    SourceClass,
)
from pertura_gate.promotion import decide_promotion
from pertura_runtime.verifier import VerifierBroker
from pertura_workflow.capabilities import CapabilityRegistry


def test_exploratory_result_is_committed_without_receipt_and_replayed(tmp_path: Path) -> None:
    source = tmp_path / "expression.csv"
    source.write_text(
        "cell_id,G1,G2\n"
        "AAAC-1,1,0\n"
        "AAAG-1,0,2\n",
        encoding="utf-8",
    )
    contract = DatasetContract(
        dataset_id="candidate",
        input_format="csv",
        source_paths=(str(source),),
        expression_matrix={"raw_counts_confirmed": True},
    )
    request = CapabilityRunRequest(
        run_id="candidate-run",
        capability_id="diagnostic.dataset_integrity.v1",
        capability_version="0.1.0",
        contract_id=contract.contract_id,
        contract_hash=contract.canonical_hash,
        scope=ScopeKey(dataset_id=contract.dataset_id),
        parameters={"input_path": str(source)},
    )
    with VerifierBroker(
        authority_dir=tmp_path / "authority",
        output_root=tmp_path / "outputs",
        policy_hash="sha256:policy",
    ) as broker:
        broker.register_contract(contract)
        first = broker.run(request)
        second = broker.run(request)
        assert first["receipt"] is None
        assert first["result"]["metadata"]["verification_state"] == "validated_untrusted"
        assert second["replayed"] is True
        assert len(broker.list_results("candidate-run")) == 1
        assert broker.list_committed("candidate-run")[0]["receipt"] is None
        assert not (tmp_path / "authority" / "signing.key").exists()

        result = ResultEnvelope.model_validate(first["result"])
        statement = ScientificStatement(
            run_id="candidate-run",
            text="Candidate integrity result is a strong measured association.",
            source_class=SourceClass.measured_result,
            scope=result.scope,
            result_ids=(result.result_id,),
            requested_strength="measured_association",
        )
        capability = CapabilityRegistry.load_default(include_external=False).get(
            result.capability_id,
            result.capability_version,
        )
        decision = decide_promotion(
            statement,
            results=(result,),
            receipts=(),
            capability_specs=(capability,),
            authoritative_public_key=broker.public_key,
        )
        assert decision.status != "promoted"
        assert decision.receipt_ids == ()
        assert any("not bundled trusted" in reason for reason in decision.reasons)

def test_product_report_separates_candidate_results(monkeypatch, tmp_path: Path) -> None:
    from pertura_runtime.claude.workspace import ClaudeRunWorkspace
    from pertura_runtime.product import PerturaProductRuntime

    monkeypatch.setenv("PERTURA_AUTHORITY_ROOT", str(tmp_path / "authority-root"))
    source = tmp_path / "expression.csv"
    source.write_text("cell_id,G1\nAAAC-1,1\nAAAG-1,2\n", encoding="utf-8")
    workspace = ClaudeRunWorkspace.create(
        root=tmp_path / "runs",
        input_source=source,
        run_id="candidate-product",
    )
    runtime = PerturaProductRuntime(workspace)
    try:
        contract = runtime.inspect_dataset()
        result = runtime.run_diagnostic(
            "diagnostic.dataset_integrity.v1",
            contract_id=contract["contract_id"],
            parameters={"input_path": str(source)},
        )
        assert result["receipt_id"] is None
        assert result["validation_status"] == "synthetic_only"
        report = runtime.finalize_report()
        assert report["result_count"] == 1
        rendered = (workspace.reports_dir / "capability_report.md").read_text(encoding="utf-8")
        assert "Exploratory candidate analyses" in rendered
        assert "cannot support strong measured claims" in rendered
    finally:
        runtime.close()
