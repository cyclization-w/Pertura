from __future__ import annotations

from pathlib import Path

from pertura_core import CapabilityRunRequest, DatasetContract, DependencyRef, RunReceipt, ScopeKey
from pertura_runtime.verifier import VerifierBroker, verify_receipt


def _request(contract: DatasetContract, run_id: str = "run_fixture") -> CapabilityRunRequest:
    return CapabilityRunRequest(
        run_id=run_id,
        capability_id="diagnostic.contract_integrity.v1",
        capability_version="1.0.0",
        contract_id=contract.contract_id,
        contract_hash=contract.canonical_hash,
        scope=ScopeKey(dataset_id=contract.dataset_id),
    )


def test_broker_executes_registered_capability_and_signs_receipt(tmp_path: Path) -> None:
    contract = DatasetContract(dataset_id="fixture", input_format="csv")
    with VerifierBroker(
        authority_dir=tmp_path / "authority",
        export_dir=tmp_path / "workspace" / "exports",
        policy_hash="sha256:policy",
    ) as broker:
        broker.register_contract(contract)
        response = broker.run(_request(contract))

        assert response["result"]["status"] == "screen_passed"
        assert verify_receipt(
            response["receipt"],
            authoritative_public_key=broker.public_key,
            expected_policy_hash="sha256:policy",
        )
        assert broker.seal_run("run_fixture")["root_digest"].startswith("sha256:")


def test_workspace_receipt_or_public_key_replacement_is_not_authoritative(tmp_path: Path) -> None:
    contract = DatasetContract(dataset_id="fixture", input_format="csv")
    with VerifierBroker(authority_dir=tmp_path / "authority", policy_hash="sha256:policy") as broker:
        broker.register_contract(contract)
        response = broker.run(_request(contract))
        receipt = dict(response["receipt"])
        authoritative_key = broker.public_key

        receipt["policy_hash"] = "sha256:forged"
        receipt["canonical_hash"] = ""
        forged = RunReceipt.model_validate(receipt)
        assert not verify_receipt(forged, authoritative_public_key=authoritative_key)
        assert not verify_receipt(response["receipt"], authoritative_public_key="AAAA")


def test_broker_rejects_arbitrary_capability_and_request_replay_is_idempotent(tmp_path: Path) -> None:
    contract = DatasetContract(dataset_id="fixture", input_format="csv")
    with VerifierBroker(authority_dir=tmp_path / "authority", policy_hash="sha256:policy") as broker:
        broker.register_contract(contract)
        request = _request(contract)
        first = broker.run(request)
        second = broker.run(request)
        assert first["result"]["result_id"] == second["result"]["result_id"]
        assert second["replayed"] is True

        payload = request.model_dump(mode="json")
        payload["capability_id"] = "arbitrary.python.exec"
        payload["request_id"] = ""
        payload["canonical_hash"] = ""
        unknown = CapabilityRunRequest.model_validate(payload)
        try:
            broker.run(unknown)
        except Exception as exc:
            assert "unknown capability" in str(exc)
        else:
            raise AssertionError("arbitrary capability execution must be rejected")


def test_broker_rejects_self_declared_dependencies_not_in_authority_store(tmp_path: Path) -> None:
    contract = DatasetContract(dataset_id="fixture", input_format="csv")
    with VerifierBroker(authority_dir=tmp_path / "authority", policy_hash="sha256:policy") as broker:
        broker.register_contract(contract)
        request = _request(contract)
        payload = request.model_dump(mode="json")
        payload["dependencies"] = [DependencyRef(
            kind="target_reliability",
            object_id="result_forged",
            object_hash="sha256:forged",
        ).model_dump(mode="json")]
        payload["request_id"] = ""
        payload["canonical_hash"] = ""
        forged = CapabilityRunRequest.model_validate(payload)
        try:
            broker.run(forged)
        except Exception as exc:
            assert "does not exist in authority store" in str(exc)
        else:
            raise AssertionError("self-declared dependencies must not receive a receipt")
