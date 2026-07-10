from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from pertura_core import (
    CapabilityRunRequest,
    CapabilitySpec,
    DatasetContract,
    DependencyRef,
    DesignConfirmation,
    PromotionDecision,
    ResultEnvelope,
    RunReceipt,
    ScientificStatement,
    ScopeKey,
)
from pertura_gate.promotion import PromotionPolicy
from pertura_runtime.claude.tools.product_tools import PRODUCT_TOOL_CONTRACTS, PRODUCT_TOOL_NAMES
from pertura_workflow.capabilities import CapabilityRegistry


PUBLIC_MODELS = (
    DatasetContract,
    ScopeKey,
    CapabilitySpec,
    CapabilityRunRequest,
    ResultEnvelope,
    RunReceipt,
    ScientificStatement,
    PromotionDecision,
    DependencyRef,
    DesignConfirmation,
)


def compatibility_payloads() -> dict[str, Any]:
    policy = PromotionPolicy()
    registry = CapabilityRegistry.load_default(include_external=False)
    receipt = RunReceipt(
        run_id="freeze-run",
        request_id="request_freeze",
        result_id="result_freeze",
        result_hash="sha256:" + "1" * 64,
        capability_id="de.pseudobulk.edger.v1",
        capability_version="1.0.0",
        contract_id="contract_freeze",
        contract_hash="sha256:" + "2" * 64,
        scope_hash="sha256:" + "3" * 64,
        policy_hash=policy.policy_hash,
        dependency_hashes={"contract_freeze": "sha256:" + "2" * 64},
        output_hashes={"edger_results.csv": "sha256:" + "4" * 64},
        broker_instance_id="broker_freeze",
        signed_at_utc="2026-01-01T00:00:00+00:00",
        public_key="freeze-public-key",
    )
    return {
        "core-schemas.json": {
            "schema_version": "pertura-v020-schema-freeze-v1",
            "models": {model.__name__: model.model_json_schema() for model in PUBLIC_MODELS},
        },
        "tool-surface.json": {
            "schema_version": "pertura-v020-tool-freeze-v1",
            "tool_names": list(PRODUCT_TOOL_NAMES),
            "contracts": PRODUCT_TOOL_CONTRACTS,
        },
        "capability-surface.json": {
            "schema_version": "pertura-v020-capability-freeze-v1",
            "capabilities": [
                {
                    "capability_id": item.capability_id,
                    "version": item.version,
                    "phase": item.phase,
                    "kind": item.kind,
                    "trust_level": item.trust_level.value,
                    "claim_permissions": list(item.claim_permissions),
                    "implemented": item.implemented,
                }
                for item in sorted(registry.specs(), key=lambda value: (value.capability_id, value.version))
            ],
        },
        "promotion-policy.json": {
            "schema_version": "pertura-v020-promotion-freeze-v1",
            "policy": asdict(policy),
            "policy_hash": policy.policy_hash,
        },
        "receipt-fixture.json": {
            "schema_version": "pertura-v020-receipt-freeze-v1",
            "receipt": receipt.model_dump(mode="json"),
            "signing_payload": receipt.signing_payload(),
        },
        "scope-truth-table.json": _scope_truth_table(),
    }


def freeze_contracts(repo_root: str | Path, *, check: bool) -> list[str]:
    root = Path(repo_root).resolve()
    destinations = (
        root / "compatibility" / "v0.2",
        root / "src" / "pertura_core" / "compatibility" / "v0.2",
    )
    payloads = compatibility_payloads()
    drift: list[str] = []
    for destination in destinations:
        for name, payload in payloads.items():
            rendered = json.dumps(payload, indent=2, sort_keys=True) + "\n"
            path = destination / name
            label = f"{destination.relative_to(root).as_posix()}/{name}"
            if check:
                if not path.is_file():
                    drift.append(label)
                elif name == "capability-surface.json":
                    try:
                        frozen = json.loads(path.read_text(encoding="utf-8"))
                    except json.JSONDecodeError:
                        drift.append(label)
                    else:
                        if not _capability_surface_is_additively_compatible(frozen, payload):
                            drift.append(label)
                elif path.read_text(encoding="utf-8") != rendered:
                    drift.append(label)
            else:
                destination.mkdir(parents=True, exist_ok=True)
                path.write_text(rendered, encoding="utf-8")
    return drift


def _capability_surface_is_additively_compatible(frozen: dict[str, Any], current: dict[str, Any]) -> bool:
    """Keep frozen capabilities exact while allowing non-authoritative additions."""

    frozen_items = {
        (item["capability_id"], item["version"]): item
        for item in frozen.get("capabilities", [])
    }
    current_items = {
        (item["capability_id"], item["version"]): item
        for item in current.get("capabilities", [])
    }
    if any(current_items.get(identity) != item for identity, item in frozen_items.items()):
        return False
    additions = [item for identity, item in current_items.items() if identity not in frozen_items]
    return all(
        item.get("trust_level") == "exploratory"
        and not item.get("claim_permissions")
        for item in additions
    )


def _scope_truth_table() -> dict[str, Any]:
    from pertura_core import compare_scope_keys

    required = ScopeKey(dataset_id="dataset", perturbation_ids=("KLF1",), control_ids=("NTC",), state_ids=("state_a",))
    candidates = {
        "exact": ScopeKey.model_validate(required.model_dump(mode="json")),
        "broader": ScopeKey(dataset_id="dataset", perturbation_ids=("KLF1",), control_ids=("NTC",)),
        "narrower": ScopeKey(dataset_id="dataset", perturbation_ids=("KLF1",), control_ids=("NTC",), state_ids=("state_a",), replicate_ids=("r1",)),
        "mismatch": ScopeKey(dataset_id="dataset", perturbation_ids=("TP53",), control_ids=("NTC",), state_ids=("state_a",)),
        "unresolved": ScopeKey(dataset_id="dataset", perturbation_ids=("KLF1",), unresolved_fields=("control",)),
    }
    return {
        "schema_version": "pertura-v020-scope-freeze-v1",
        "required": required.model_dump(mode="json"),
        "comparisons": {name: compare_scope_keys(required, value).value for name, value in candidates.items()},
    }
