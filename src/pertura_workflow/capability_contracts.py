from __future__ import annotations

from typing import Any

from pertura_core.hashing import canonical_hash
from pertura_workflow.capabilities.registry import (
    CapabilityRegistry,
    capability_scientific_hash,
)


def build_capability_contract_catalog(
    registry: CapabilityRegistry | None = None,
) -> dict[str, Any]:
    """Return the hash-bound, answer-free capability contract catalog."""

    registry = registry or CapabilityRegistry.load_default(include_external=False)
    capabilities = []
    for spec in registry.specs():
        capabilities.append(
            {
                "capability_id": spec.capability_id,
                "version": spec.version,
                "kind": spec.kind,
                "summary": spec.summary,
                "scientific_hash": capability_scientific_hash(spec),
                "deprecated": bool(spec.metadata.get("deprecated", False)),
                "parameters_schema": dict(spec.parameters_schema or {}),
                "input_requirements": list(spec.input_requirements),
                "depends_on": list(spec.depends_on),
                "dependency_kinds": list(spec.dependency_kinds),
                "dependency_policy": dict(spec.metadata.get("dependency_policy") or {}),
                "environment_profile": spec.metadata.get("environment_profile"),
                "output_kind": spec.output_kind,
                "source_class": spec.source_class.value,
                "claim_permissions": list(spec.claim_permissions),
            }
        )
    payload = {
        "schema_version": "pertura-capability-contract-catalog-v1",
        "capability_count": len(capabilities),
        "active_capability_count": sum(not item["deprecated"] for item in capabilities),
        "capabilities": capabilities,
    }
    return {**payload, "catalog_hash": canonical_hash(payload)}
