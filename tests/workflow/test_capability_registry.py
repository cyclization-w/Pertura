from __future__ import annotations

import pytest

from pertura_core import CapabilitySpec
from pertura_workflow.capabilities import CapabilityRegistry, CapabilityRegistryError
from pertura_workflow.capabilities import registry as registry_module


def test_bundled_capabilities_are_discoverable_without_loading_runners() -> None:
    registry = CapabilityRegistry.load_default(include_external=False)
    summaries = registry.list()

    assert {item.capability_id for item in summaries} >= {
        "diagnostic.contract_integrity.v1",
        "de.pseudobulk.edger.v1",
        "virtual.evaluate.v1",
    }
    assert all(not item.deprecated for item in summaries)

    audit_summaries = registry.list(include_deprecated=True)
    assert {item.capability_id for item in audit_summaries} >= {
        "diagnostic.guide_assignment.v1",
        "reference.state.control_pca_leiden.v1",
        "module.learn.nmf.v1",
        "target.reliability.v2",
    }
    assert registry.get("de.pseudobulk.edger.v1").claim_permissions == ("measured_association",)


def test_duplicate_capability_id_and_version_is_rejected() -> None:
    spec = CapabilitySpec(
        capability_id="diagnostic.fixture.v1",
        version="1.0.0",
        phase=1,
        kind="diagnostic",
        summary="fixture",
        executor="contract_integrity",
        validator="standard",
        output_kind="fixture",
        source_class="observed_metadata",
    )
    with pytest.raises(CapabilityRegistryError, match="duplicate"):
        CapabilityRegistry([spec, spec])

def test_installed_capability_is_discovery_only_without_local_runner(monkeypatch) -> None:
    class EntryPoint:
        def load(self):
            return {
                "capability_id": "external.fixture.v1",
                "version": "1.0.0",
                "phase": 2,
                "kind": "diagnostic",
                "summary": "external fixture",
                "trust_level": "builtin_trusted",
                "executor": "external_missing_executor",
                "validator": "external_missing_validator",
                "output_kind": "external_fixture",
                "source_class": "observed_metadata",
                "claim_permissions": ["measured_association"],
                "implemented": True,
            }

    monkeypatch.setattr(
        registry_module.metadata,
        "entry_points",
        lambda **_: [EntryPoint()],
    )
    registry = CapabilityRegistry.load_default(include_external=True)
    spec = registry.get("external.fixture.v1")

    assert spec.trust_level.value == "installed_untrusted"
    assert spec.claim_permissions == ()
    assert spec.implemented is False
    summary = next(
        item for item in registry.list() if item.capability_id == spec.capability_id
    )
    assert summary.broker_executable is False

\n