from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from jsonschema import Draft202012Validator

from pertura_core import CapabilitySpec, DatasetContract, ResultEnvelope
from pertura_core.hashing import canonical_hash
from pertura_runtime.project.models import (
    CapabilityInvocationAsset,
    CapabilityInvocationBinding,
    DataAssetRef,
)
from pertura_runtime.project.workspace import ProjectWorkspace
from pertura_workflow.capabilities.registry import capability_scientific_hash


class CapabilityInvocationBindingError(ValueError):
    """A frozen invocation binding is invalid, stale, or used out of scope."""


def build_invocation_binding(
    *,
    run_id: str,
    task_id: str,
    spec: CapabilitySpec,
    contract: DatasetContract,
    tool_name: str,
    scope: Mapping[str, Any],
    bound_parameters: Mapping[str, Any],
    project: ProjectWorkspace | None,
    allowed_overrides: tuple[str, ...] = (),
    dependency_results: tuple[ResultEnvelope, ...] = (),
    dependency_records: Mapping[str, Mapping[str, Any]] | None = None,
    dependency_binding_ids: tuple[str, ...] = (),
    additional_assets: tuple[DataAssetRef, ...] = (),
    output_mapping: Mapping[str, str] | None = None,
    readiness: str = "ready",
    blockers: tuple[str, ...] = (),
) -> CapabilityInvocationBinding:
    """Build and preflight a task-scoped invocation without executing it."""

    if spec.kind == "diagnostic" and tool_name != "run_diagnostic":
        raise CapabilityInvocationBindingError("diagnostic capability uses the wrong tool")
    if spec.kind == "analysis" and tool_name != "run_analysis":
        raise CapabilityInvocationBindingError("analysis capability uses the wrong tool")
    if spec.kind == "virtual" and tool_name != "evaluate_virtual_model":
        raise CapabilityInvocationBindingError("virtual capability uses the wrong tool")

    schema = dict(spec.parameters_schema or {"type": "object", "properties": {}})
    errors = sorted(
        Draft202012Validator(schema).iter_errors(dict(bound_parameters)),
        key=lambda item: list(item.path),
    )
    if errors and readiness != "blocked_probe":
        details = "; ".join(
            f"{'.'.join(str(part) for part in error.path) or '<root>'}: {error.message}"
            for error in errors
        )
        raise CapabilityInvocationBindingError(
            f"invalid bound parameters for {spec.capability_id}: {details}"
        )

    properties = dict(schema.get("properties") or {})
    unknown_overrides = sorted(set(allowed_overrides) - set(properties))
    if unknown_overrides:
        raise CapabilityInvocationBindingError(
            f"unknown allowed overrides for {spec.capability_id}: {unknown_overrides}"
        )
    locked_asset_parameters = {
        name
        for name, field in properties.items()
        if isinstance(field, Mapping) and field.get("x-pertura-asset-role")
    }
    if locked_asset_parameters & set(allowed_overrides):
        raise CapabilityInvocationBindingError("asset-valued parameters cannot be overridden")

    input_assets: list[CapabilityInvocationAsset] = []
    if project is not None:
        for name in sorted(locked_asset_parameters):
            if name not in bound_parameters and name not in set(
                schema.get("required") or ()
            ):
                continue
            value = bound_parameters.get(name)
            field = properties[name]
            values = value if isinstance(value, list) else [value]
            if (
                not values
                or not all(
                    isinstance(item, str) and item.startswith("asset_")
                    for item in values
                )
            ):
                if readiness == "blocked_probe":
                    continue
                raise CapabilityInvocationBindingError(
                    f"asset parameter {name} is not bound to an asset ID"
                )
            if field.get("type") == "array" and not isinstance(value, list):
                raise CapabilityInvocationBindingError(
                    f"asset parameter {name} must be bound to an array of asset IDs"
                )
            expected_role = str(field["x-pertura-asset-role"])
            for asset_id in values:
                asset = project.store.get_asset(asset_id)
                if asset is None:
                    raise CapabilityInvocationBindingError(
                        f"unknown bound asset: {asset_id}"
                    )
                if asset.role != expected_role:
                    raise CapabilityInvocationBindingError(
                        f"asset {asset_id} has role {asset.role!r}, expected {expected_role!r}"
                    )
                input_assets.append(
                    CapabilityInvocationAsset(
                        parameter=name,
                        asset_id=asset.asset_id,
                        role=asset.role,
                        asset_identity_hash=asset.identity_hash,
                        content_sha256=asset.content_sha256,
                    )
                )
        for asset in additional_assets:
            stored = project.store.get_asset(asset.asset_id)
            if stored is None or stored.identity_hash != asset.identity_hash:
                raise CapabilityInvocationBindingError(
                    f"supplemental bound asset is missing or stale: {asset.asset_id}"
                )
            if all(item.asset_id != asset.asset_id for item in input_assets):
                input_assets.append(
                    CapabilityInvocationAsset(
                        parameter="",
                        asset_id=asset.asset_id,
                        role=asset.role,
                        asset_identity_hash=asset.identity_hash,
                        content_sha256=asset.content_sha256,
                    )
                )

    dependency_records = dict(dependency_records or {})
    dependency_receipt_ids: list[str | None] = []
    dependency_result_ids: list[str] = []
    dependency_result_hashes: list[str] = []
    dependency_verification_states: list[str] = []
    for result in dependency_results:
        record = dict(dependency_records.get(result.result_id) or {})
        state = str(
            record.get("verification_state")
            or result.metadata.get("verification_state")
            or ("trusted_receipt" if result.receipt_id else "")
        )
        if state not in {"trusted_receipt", "validated_untrusted"}:
            raise CapabilityInvocationBindingError(
                "dependency result lacks a verified commit state: "
                f"{result.result_id}"
            )
        receipt_payload = record.get("receipt") or {}
        receipt_id = str(
            (
                receipt_payload.get("receipt_id")
                if isinstance(receipt_payload, Mapping)
                else ""
            )
            or result.receipt_id
            or ""
        ) or None
        if state == "trusted_receipt" and receipt_id is None:
            raise CapabilityInvocationBindingError(
                f"trusted dependency result lacks its receipt: {result.result_id}"
            )
        if state == "validated_untrusted":
            receipt_id = None
        dependency_result_ids.append(result.result_id)
        dependency_result_hashes.append(result.canonical_hash)
        dependency_verification_states.append(state)
        dependency_receipt_ids.append(receipt_id)

    turn_sequence = _binding_turn_sequence(project, run_id)

    raw = {
        "schema_version": "pertura-capability-invocation-binding-v1",
        "run_id": run_id,
        "task_id": task_id,
        "turn_sequence": turn_sequence,
        "capability_id": spec.capability_id,
        "capability_version": spec.version,
        "capability_scientific_hash": capability_scientific_hash(spec),
        "tool_name": tool_name,
        "contract_id": contract.contract_id,
        "contract_hash": contract.canonical_hash,
        "scope": dict(scope),
        "bound_parameters": dict(bound_parameters),
        "allowed_overrides": tuple(allowed_overrides),
        "input_assets": tuple(input_assets),
        "dependency_result_ids": tuple(dependency_result_ids),
        "dependency_result_hashes": tuple(dependency_result_hashes),
        "dependency_verification_states": tuple(dependency_verification_states),
        "dependency_receipt_ids": tuple(dependency_receipt_ids),
        "dependency_binding_ids": tuple(dependency_binding_ids),
        "output_mapping": dict(output_mapping or {}),
        "readiness": readiness,
        "blockers": tuple(blockers),
    }
    binding_hash = canonical_hash(
        {
            key: (
                [item.model_dump(mode="json") for item in value]
                if key == "input_assets"
                else value
            )
            for key, value in raw.items()
        }
    )
    return CapabilityInvocationBinding(
        **raw,
        binding_hash=binding_hash,
        binding_id="capbinding_" + binding_hash.split(":", 1)[1][:32],
    )


def binding_dependency_hints(
    binding: CapabilityInvocationBinding,
) -> list[dict[str, Any]]:
    result_hints = [
        {
            "object_id": result_id,
            "object_hash": result_hash,
            "state": "current",
        }
        for result_id, result_hash in zip(
            binding.dependency_result_ids,
            binding.dependency_result_hashes,
            strict=True,
        )
    ]
    asset_hints = [
        {
            "kind": "data_asset",
            "object_id": asset.asset_id,
            "object_hash": asset.asset_identity_hash,
            "role": f"asset:{asset.role}",
        }
        for asset in binding.input_assets
    ]
    return result_hints + asset_hints


def _binding_turn_sequence(
    project: ProjectWorkspace | None, run_id: str
) -> int:
    if project is None:
        raise CapabilityInvocationBindingError(
            "invocation binding requires a project workspace"
        )
    run = project.store.get_run(run_id)
    if run is None:
        raise CapabilityInvocationBindingError(
            f"invocation binding run is unavailable: {run_id}"
        )
    if run.active_turn_id:
        active = project.store.get_turn(run.active_turn_id)
        if active is not None:
            return active.sequence
    turns = [
        turn
        for conversation in project.store.list_conversations(
            project.project.project_id
        )
        if conversation.run_id == run_id
        for turn in project.store.list_turns(conversation.conversation_id)
    ]
    return max((turn.sequence for turn in turns), default=0) + 1
