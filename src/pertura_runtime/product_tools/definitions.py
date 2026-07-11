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

    def frozen_contract(self) -> dict[str, Any]:
        return {
            "input": dict(self.input_types),
            "output": {"required": list(self.output_required)},
        }

    def json_input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                name: {"type": type_name}
                for name, type_name in self.input_types.items()
            },
            "additionalProperties": False,
        }


def _spec(
    name: str,
    description: str,
    input_types: dict[str, str],
    output_required: tuple[str, ...],
) -> ProductToolSpec:
    return ProductToolSpec(
        name=name,
        description=description,
        input_types=MappingProxyType(dict(input_types)),
        output_required=output_required,
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
        "Run one registered diagnostic capability through the independent verifier.",
        {
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
    ),
    _spec(
        "run_analysis",
        "Route an objective to a registered analysis capability and execute it through the independent verifier.",
        {
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
    ),
    _spec(
        "evaluate_virtual_model",
        "Evaluate a virtual perturbation model under a fixed split contract. Unsupported evaluators return out_of_scope.",
        {
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
