from __future__ import annotations

from typing import Any

from pertura_runtime.product import PerturaProductRuntime


PRODUCT_TOOL_NAMES = (
    "inspect_dataset",
    "run_diagnostic",
    "run_analysis",
    "evaluate_virtual_model",
    "finalize_report",
)

# JSON-serializable public contracts are kept separately from the SDK's Python
# type declarations so the v0.2 tool surface can be compatibility-snapshotted
# without importing claude_agent_sdk.
PRODUCT_TOOL_CONTRACTS: dict[str, dict[str, Any]] = {
    "inspect_dataset": {
        "input": {"path": "string", "dataset_id": "string", "confirmations": "object"},
        "output": {"required": ["contract_id", "contract_hash", "dataset_id", "format", "version", "unresolved_fields", "contract_path"]},
    },
    "run_diagnostic": {
        "input": {"capability_id": "string", "contract_id": "string", "scope": "object", "parameters": "object", "dependencies": "array"},
        "output": {"required": ["result_id", "receipt_id", "status", "blockers", "cautions", "summary", "output_paths", "scope_id"]},
    },
    "run_analysis": {
        "input": {"objective": "string", "capability_id": "string", "contract_id": "string", "scope": "object", "parameters": "object", "dependencies": "array"},
        "output": {"required": ["result_id", "receipt_id", "status", "blockers", "cautions", "summary", "output_paths", "scope_id"]},
    },
    "evaluate_virtual_model": {
        "input": {"capability_id": "string", "contract_id": "string", "scope": "object", "parameters": "object"},
        "output": {"required": ["result_id", "receipt_id", "status", "blockers", "cautions", "summary", "output_paths", "scope_id", "not_implemented_capabilities"]},
    },
    "finalize_report": {
        "input": {"run_id": "string"},
        "output": {"required": ["run_id", "status", "result_count", "report_paths", "root_digest", "promotion_decision_count"]},
    },
}


def create_product_mcp_server(runtime: PerturaProductRuntime):
    from claude_agent_sdk import create_sdk_mcp_server, tool

    @tool(
        "inspect_dataset",
        "Inspect dataset structure and create a versioned DatasetContract. This does not run scientific analysis.",
        {"path": str, "dataset_id": str, "confirmations": dict},
    )
    async def inspect_dataset(args: dict[str, Any]) -> dict[str, Any]:
        return runtime.inspect_dataset(
            args.get("path") or None,
            dataset_id=args.get("dataset_id") or None,
            confirmations=dict(args.get("confirmations") or {}),
        )

    @tool(
        "run_diagnostic",
        "Run one registered diagnostic capability through the independent verifier.",
        {"capability_id": str, "contract_id": str, "scope": dict, "parameters": dict, "dependencies": list},
    )
    async def run_diagnostic(args: dict[str, Any]) -> dict[str, Any]:
        return runtime.run_diagnostic(
            str(args.get("capability_id") or ""),
            contract_id=args.get("contract_id") or None,
            scope=dict(args.get("scope") or {}) or None,
            parameters=dict(args.get("parameters") or {}),
            dependencies=list(args.get("dependencies") or []),
        )

    @tool(
        "run_analysis",
        "Route an objective to a registered analysis capability and execute it through the independent verifier.",
        {"objective": str, "capability_id": str, "contract_id": str, "scope": dict, "parameters": dict, "dependencies": list},
    )
    async def run_analysis(args: dict[str, Any]) -> dict[str, Any]:
        return runtime.run_analysis(
            str(args.get("objective") or ""),
            capability_id=args.get("capability_id") or None,
            contract_id=args.get("contract_id") or None,
            scope=dict(args.get("scope") or {}) or None,
            parameters=dict(args.get("parameters") or {}),
            dependencies=list(args.get("dependencies") or []),
        )

    @tool(
        "evaluate_virtual_model",
        "Evaluate a virtual perturbation model under a fixed split contract. Unsupported evaluators return out_of_scope.",
        {"capability_id": str, "contract_id": str, "scope": dict, "parameters": dict},
    )
    async def evaluate_virtual_model(args: dict[str, Any]) -> dict[str, Any]:
        return runtime.evaluate_virtual_model(
            capability_id=args.get("capability_id") or "virtual.evaluate.v1",
            contract_id=args.get("contract_id") or None,
            scope=dict(args.get("scope") or {}) or None,
            parameters=dict(args.get("parameters") or {}),
        )

    @tool(
        "finalize_report",
        "Seal verified run receipts and render the capability report from the authoritative commit store.",
        {"run_id": str},
    )
    async def finalize_report(args: dict[str, Any]) -> dict[str, Any]:
        return runtime.finalize_report(args.get("run_id") or None)

    return create_sdk_mcp_server(
        name="pertura",
        version="0.2.0a3",
        tools=[inspect_dataset, run_diagnostic, run_analysis, evaluate_virtual_model, finalize_report],
    )
