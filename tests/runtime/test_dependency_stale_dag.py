from __future__ import annotations

from pathlib import Path

from pertura_core import AnalysisStatus, CapabilityRunRequest, DatasetContract, DependencyRef, ResultEnvelope, ScopeKey
from pertura_runtime.verifier.receipts import ReceiptSigner
from pertura_runtime.verifier.store import AuthorityStore
from pertura_runtime.product import _normalize_dependencies


def _result(request: CapabilityRunRequest, dependencies: tuple[DependencyRef, ...]) -> ResultEnvelope:
    return ResultEnvelope(
        run_id=request.run_id,
        request_id=request.request_id,
        capability_id=request.capability_id,
        capability_version=request.capability_version,
        capability_trust="builtin_trusted",
        contract_id=request.contract_id,
        contract_hash=request.contract_hash,
        scope=request.scope,
        status=AnalysisStatus.completed,
        result_kind="fixture",
        source_class="measured_result",
        summary="fixture",
        dependencies=dependencies,
    )


def test_stale_propagates_through_explicit_dependency_dag(tmp_path: Path) -> None:
    store = AuthorityStore(tmp_path / "authority.sqlite3")
    signer = ReceiptSigner()
    contract = DatasetContract(dataset_id="d", input_format="csv")
    store.put_contract(contract)
    scope = ScopeKey(dataset_id="d")
    contract_dependency = DependencyRef(kind="contract", object_id=contract.contract_id, object_hash=contract.canonical_hash)
    first_request = CapabilityRunRequest(
        run_id="run", capability_id="fixture.first", capability_version="1", contract_id=contract.contract_id,
        contract_hash=contract.canonical_hash, scope=scope, dependencies=(contract_dependency,),
    )
    first = _result(first_request, first_request.dependencies)
    store.commit_result(first, signer.sign_result(first_request, first, policy_hash="sha256:policy", broker_instance_id="broker"))
    result_dependency = DependencyRef(kind="upstream_result", object_id=first.result_id, object_hash=first.canonical_hash)
    second_request = CapabilityRunRequest(
        run_id="run", capability_id="fixture.second", capability_version="1", contract_id=contract.contract_id,
        contract_hash=contract.canonical_hash, scope=scope, dependencies=(result_dependency,),
    )
    second = _result(second_request, second_request.dependencies)
    store.commit_result(second, signer.sign_result(second_request, second, policy_hash="sha256:policy", broker_instance_id="broker"))

    assert store.mark_stale_for_dependency(contract.contract_id, "sha256:new-contract") == 2
    assert store.get_result(first.result_id).stale is True
    assert store.get_result(second.result_id).stale is True


def test_environment_lock_change_marks_dependent_results_stale(tmp_path: Path) -> None:
    store = AuthorityStore(tmp_path / "authority.sqlite3")
    signer = ReceiptSigner()
    contract = DatasetContract(dataset_id="d", input_format="csv")
    store.put_contract(contract)
    store.put_runtime_object(kind="environment", object_id="environment:edger-v1", object_hash="sha256:old", payload={})
    dependency = DependencyRef(kind="environment", object_id="environment:edger-v1", object_hash="sha256:old")
    request = CapabilityRunRequest(
        run_id="run", capability_id="de.pseudobulk.edger.v1", capability_version="1.0.0",
        contract_id=contract.contract_id, contract_hash=contract.canonical_hash,
        scope=ScopeKey(dataset_id="d"), dependencies=(dependency,),
    )
    result = _result(request, request.dependencies)
    store.commit_result(result, signer.sign_result(request, result, policy_hash="sha256:policy", broker_instance_id="broker"))

    assert store.put_runtime_object(
        kind="environment", object_id="environment:edger-v1", object_hash="sha256:new", payload={}
    ) == 1
    assert store.get_result(result.result_id).stale is True


def test_runtime_dependency_normalization_deduplicates_transitive_environment(
    tmp_path: Path,
) -> None:
    environment = DependencyRef(
        kind="environment",
        object_id="environment:perturbseq-python-v1",
        object_hash="sha256:locked",
        role="scientific_execution_environment",
    )
    dependencies = _normalize_dependencies([environment, environment])

    assert dependencies == (environment,)

    store = AuthorityStore(tmp_path / "normalized-authority.sqlite3")
    contract = DatasetContract(dataset_id="d", input_format="csv")
    store.put_contract(contract)
    store.put_runtime_object(
        kind="environment",
        object_id=environment.object_id,
        object_hash=environment.object_hash,
        payload={},
    )
    request = CapabilityRunRequest(
        run_id="run",
        capability_id="state.reference.map_knn.v1",
        capability_version="1.0.0",
        contract_id=contract.contract_id,
        contract_hash=contract.canonical_hash,
        scope=ScopeKey(dataset_id="d"),
        dependencies=dependencies,
    )
    result = _result(request, request.dependencies)

    store.commit_result(result)

    assert store.get_result(result.result_id).dependencies == dependencies


def test_runtime_dependency_normalization_rejects_hash_conflict() -> None:
    first = DependencyRef(
        kind="environment",
        object_id="environment:perturbseq-python-v1",
        object_hash="sha256:first",
    )
    second = DependencyRef(
        kind="environment",
        object_id="environment:perturbseq-python-v1",
        object_hash="sha256:second",
    )

    import pytest

    with pytest.raises(ValueError, match="conflicting duplicate dependency"):
        _normalize_dependencies([first, second])
