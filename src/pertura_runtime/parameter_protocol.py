from __future__ import annotations

from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from pertura_core import CapabilitySpec
from pertura_runtime.project.assets import DataAssetRegistry


class CapabilityParameterError(ValueError):
    pass


def validate_and_resolve_parameters(
    spec: CapabilitySpec,
    parameters: dict[str, Any] | None,
    *,
    asset_registry: DataAssetRegistry | None,
    workspace_root: Path,
) -> dict[str, Any]:
    schema = dict(spec.parameters_schema or {"type": "object", "properties": {}})
    schema.setdefault("type", "object")
    schema.setdefault("properties", {})
    schema.setdefault("additionalProperties", False)
    payload = dict(parameters or {})
    errors = sorted(Draft202012Validator(schema).iter_errors(payload), key=lambda item: list(item.path))
    if errors:
        details = "; ".join(
            f"{'.'.join(str(part) for part in error.path) or '<root>'}: {error.message}"
            for error in errors
        )
        raise CapabilityParameterError(f"invalid parameters for {spec.capability_id}: {details}")

    resolved = dict(payload)
    for name, property_schema in schema.get("properties", {}).items():
        if name not in payload or not isinstance(property_schema, dict):
            continue
        role = property_schema.get("x-pertura-asset-role")
        if not role:
            continue
        value = payload[name]
        if not isinstance(value, str):
            raise CapabilityParameterError(f"asset parameter {name} must be an asset ID")
        if asset_registry is None:
            # Standalone compatibility runs keep their existing DatasetContract path adapter.
            continue
        if value.startswith("asset_"):
            resolved[name] = str(asset_registry.resolve(value, expected_role=str(role)))
            continue
        path = Path(value).expanduser()
        if path.is_absolute():
            raise CapabilityParameterError(
                f"external path for {name} is not registered; run `pertura assets add <project> {path} --role {role} --kind external_resource`"
            )
        candidate = (workspace_root / path).resolve()
        try:
            candidate.relative_to(workspace_root.resolve())
        except ValueError as exc:
            raise CapabilityParameterError(f"asset parameter {name} escapes the run workspace") from exc
        resolved[name] = str(candidate)
    return resolved


def parameter_protocol_complete(spec: CapabilitySpec) -> bool:
    schema = spec.parameters_schema or {}
    return (
        schema.get("type") == "object"
        and isinstance(schema.get("properties"), dict)
        and schema.get("additionalProperties") is False
    )
