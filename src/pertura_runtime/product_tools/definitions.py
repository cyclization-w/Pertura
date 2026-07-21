from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping


@dataclass(frozen=True)
class ProductToolSpec:
    """Provider-neutral definition for one frozen Pertura product tool."""

    name: str
    description: str
    input_types: Mapping[str, str]
    output_required: tuple[str, ...]
    required_inputs: tuple[str, ...] = ()
    binding_excluded_inputs: tuple[str, ...] = ()
    unbound_required_inputs: tuple[str, ...] = ()

    def frozen_contract(self) -> dict[str, Any]:
        return {
            "input": dict(self.input_types),
            "output": {"required": list(self.output_required)},
        }

    def json_input_schema(self) -> dict[str, Any]:
        schema: dict[str, Any] = {
            "type": "object",
            "properties": {
                name: {"type": type_name}
                for name, type_name in self.input_types.items()
            },
            "additionalProperties": False,
        }
        if self.required_inputs:
            schema["required"] = list(self.required_inputs)
        if "binding_id" in self.input_types:
            binding_mode: dict[str, Any] = {"required": ["binding_id"]}
            if self.binding_excluded_inputs:
                binding_mode["not"] = {
                    "anyOf": [
                        {"required": [name]} for name in self.binding_excluded_inputs
                    ]
                }
            unbound_mode: dict[str, Any] = {"not": {"required": ["binding_id"]}}
            if self.unbound_required_inputs:
                unbound_mode["required"] = list(self.unbound_required_inputs)
            schema["oneOf"] = [binding_mode, unbound_mode]
        return schema


def _spec(
    name: str,
    description: str,
    input_types: dict[str, str],
    output_required: tuple[str, ...],
    *,
    required_inputs: tuple[str, ...] = (),
    binding_excluded_inputs: tuple[str, ...] = (),
    unbound_required_inputs: tuple[str, ...] = (),
) -> ProductToolSpec:
    return ProductToolSpec(
        name=name,
        description=description,
        input_types=MappingProxyType(dict(input_types)),
        output_required=output_required,
        required_inputs=required_inputs,
        binding_excluded_inputs=binding_excluded_inputs,
        unbound_required_inputs=unbound_required_inputs,
    )


PRODUCT_TOOL_SPECS = (
    _spec(
        "inspect_dataset",
        "Inspect dataset structure and create a versioned DatasetContract. This does not run scientific analysis.",
        {"path": "string", "dataset_id": "string", "confirmations": "object"},
        (
            "contract_id",
            "contract_hash",
            "dataset_id",
            "format",
            "version",
            "unresolved_fields",
            "contract_path",
        ),
    ),
    _spec(
        "run_diagnostic",
        "Run one registered diagnostic capability through the independent verifier. With binding_id, omit capability_id, contract_id, scope, parameters, and dependencies unless the binding explicitly permits an override.",
        {
            "binding_id": "string",
            "capability_id": "string",
            "contract_id": "string",
            "scope": "object",
            "parameters": "object",
            "dependencies": "array",
        },
        (
            "result_id",
            "receipt_id",
            "status",
            "blockers",
            "cautions",
            "summary",
            "output_paths",
            "scope_id",
        ),
        binding_excluded_inputs=(
            "capability_id",
            "contract_id",
            "scope",
            "dependencies",
        ),
        unbound_required_inputs=("capability_id",),
    ),
    _spec(
        "run_analysis",
        "Route an objective to a registered analysis capability and execute it through the independent verifier. With binding_id, provide only binding_id and objective unless the binding explicitly permits an override.",
        {
            "binding_id": "string",
            "objective": "string",
            "capability_id": "string",
            "contract_id": "string",
            "scope": "object",
            "parameters": "object",
            "dependencies": "array",
        },
        (
            "result_id",
            "receipt_id",
            "status",
            "blockers",
            "cautions",
            "summary",
            "output_paths",
            "scope_id",
        ),
        required_inputs=("objective",),
        binding_excluded_inputs=(
            "capability_id",
            "contract_id",
            "scope",
            "dependencies",
        ),
    ),
    _spec(
        "evaluate_virtual_model",
        "Evaluate a virtual perturbation model under a fixed split contract. With binding_id, omit capability_id, contract_id, scope, and parameters unless the binding explicitly permits an override. Unsupported evaluators return out_of_scope.",
        {
            "binding_id": "string",
            "capability_id": "string",
            "contract_id": "string",
            "scope": "object",
            "parameters": "object",
        },
        (
            "result_id",
            "receipt_id",
            "status",
            "blockers",
            "cautions",
            "summary",
            "output_paths",
            "scope_id",
            "not_implemented_capabilities",
        ),
        binding_excluded_inputs=(
            "capability_id",
            "contract_id",
            "scope",
        ),
    ),
    _spec(
        "finalize_report",
        "Seal verified run receipts and render the capability report from the authoritative commit store.",
        {"run_id": "string"},
        (
            "run_id",
            "status",
            "result_count",
            "report_paths",
            "root_digest",
            "promotion_decision_count",
        ),
    ),
)

PRODUCT_TOOL_NAMES = tuple(spec.name for spec in PRODUCT_TOOL_SPECS)
PRODUCT_TOOL_CONTRACTS = {
    spec.name: spec.frozen_contract() for spec in PRODUCT_TOOL_SPECS
}
_SPECS_BY_NAME = {spec.name: spec for spec in PRODUCT_TOOL_SPECS}


def get_product_tool_spec(name: str) -> ProductToolSpec:
    try:
        return _SPECS_BY_NAME[name]
    except KeyError as exc:
        raise ValueError(f"unknown Pertura product tool: {name}") from exc
