from __future__ import annotations

import pytest

from pertura_core import DatasetContract, ScopeComparison, ScopeKey, compare_scope_keys, scope_can_support


def test_contract_identity_is_deterministic_and_tamper_evident() -> None:
    first = DatasetContract(dataset_id="fixture", input_format="csv", source_paths=("expression.csv",))
    second = DatasetContract(dataset_id="fixture", input_format="csv", source_paths=("expression.csv",))

    assert first.contract_id == second.contract_id
    assert first.canonical_hash == second.canonical_hash

    payload = first.model_dump(mode="json")
    payload["dataset_id"] = "tampered"
    with pytest.raises(ValueError, match="canonical_hash mismatch"):
        DatasetContract.model_validate(payload)


def test_scope_comparator_fails_closed_for_unresolved_and_broad_scopes() -> None:
    required = ScopeKey(dataset_id="d", perturbation_ids=("KLF1",), control_ids=("NTC",), state_ids=("state_a",))
    exact = ScopeKey.model_validate(required.model_dump(mode="json"))
    broad = ScopeKey(dataset_id="d", perturbation_ids=("KLF1",), control_ids=("NTC",))
    unresolved = ScopeKey(dataset_id="d", perturbation_ids=("KLF1",), unresolved_fields=("control",))

    assert compare_scope_keys(required, exact) == ScopeComparison.exact
    assert compare_scope_keys(required, broad) == ScopeComparison.broader
    assert compare_scope_keys(required, unresolved) == ScopeComparison.unresolved
    assert scope_can_support(required, exact)
    assert not scope_can_support(required, broad)
    assert not scope_can_support(required, unresolved)


def test_json_schema_is_generated_for_public_contracts() -> None:
    schema = DatasetContract.model_json_schema()
    assert schema["title"] == "DatasetContract"
    assert "canonical_hash" in schema["properties"]
