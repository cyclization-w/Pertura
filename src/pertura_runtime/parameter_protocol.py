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


def parameter_protocol_issues(spec: CapabilitySpec) -> tuple[str, ...]:
    from jsonschema.exceptions import SchemaError, ValidationError
    from pertura_workflow.capabilities.parameter_schema import (
        expected_executor_parameter_names,
    )

    schema = dict(spec.parameters_schema or {})
    issues: list[str] = []
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        issues.append(f"invalid JSON Schema: {exc.message}")
        return tuple(issues)
    if schema.get("type") != "object":
        issues.append("parameter schema type must be object")
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        issues.append("parameter schema properties must be an object")
        return tuple(issues)
    if schema.get("additionalProperties") is not False:
        issues.append("additionalProperties must be false")
    required = schema.get("required")
    if not isinstance(required, list) or any(name not in properties for name in required):
        issues.append("required must be a list of declared properties")
    examples = schema.get("examples")
    if not isinstance(examples, list) or not examples or not all(
        isinstance(item, dict) and item for item in examples
    ):
        issues.append("at least one non-empty object example is required")
    else:
        validator = Draft202012Validator(schema)
        for index, example in enumerate(examples):
            try:
                validator.validate(example)
            except ValidationError as exc:
                issues.append(f"example {index} is invalid: {exc.message}")
    for name, field in properties.items():
        if not isinstance(field, dict) or "type" not in field:
            issues.append(f"property {name} lacks a valid type")
        if name.endswith("_path") and not field.get("x-pertura-asset-role"):
            issues.append(f"asset path property {name} lacks x-pertura-asset-role")
    expected = set(expected_executor_parameter_names(spec.executor))
    observed = set(properties)
    for name in sorted(expected - observed):
        issues.append(f"runner parameter is missing from schema: {name}")
    for name in sorted(observed - expected):
        issues.append(f"schema property is not consumed by runner/runtime: {name}")
    return tuple(issues)


def parameter_protocol_complete(spec: CapabilitySpec) -> bool:
    return not parameter_protocol_issues(spec)
