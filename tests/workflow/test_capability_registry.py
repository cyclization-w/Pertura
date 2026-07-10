from __future__ import annotations

import pytest

from pertura_core import CapabilitySpec
from pertura_workflow.capabilities import CapabilityRegistry, CapabilityRegistryError


def test_bundled_capabilities_are_discoverable_without_loading_runners() -> None:
    registry = CapabilityRegistry.load_default(include_external=False)
    summaries = registry.list()

    assert {item.capability_id for item in summaries} >= {
        "diagnostic.contract_integrity.v1",
        "diagnostic.guide_assignment.v1",
        "reference.state.control_pca_leiden.v1",
        "module.learn.nmf.v1",
        "target.reliability.v2",
        "de.pseudobulk.edger.v1",
        "virtual.evaluate.v1",
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
