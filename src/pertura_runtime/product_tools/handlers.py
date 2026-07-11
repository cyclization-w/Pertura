from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pertura_runtime.product_tools.definitions import get_product_tool_spec

if TYPE_CHECKING:
    from pertura_runtime.product import PerturaProductRuntime


def dispatch_product_tool(
    runtime: "PerturaProductRuntime",
    tool_name: str,
    args: dict[str, Any] | None,
) -> dict[str, Any]:
    """Invoke one product tool without importing a provider SDK."""

    get_product_tool_spec(tool_name)
    payload = dict(args or {})
    if tool_name == "inspect_dataset":
        return runtime.inspect_dataset(
            payload.get("path") or None,
            dataset_id=payload.get("dataset_id") or None,
            confirmations=dict(payload.get("confirmations") or {}),
        )
    if tool_name == "run_diagnostic":
        return runtime.run_diagnostic(
            str(payload.get("capability_id") or ""),
            contract_id=payload.get("contract_id") or None,
            scope=dict(payload.get("scope") or {}) or None,
            parameters=dict(payload.get("parameters") or {}),
            dependencies=list(payload.get("dependencies") or []),
        )
    if tool_name == "run_analysis":
        return runtime.run_analysis(
            str(payload.get("objective") or ""),
            capability_id=payload.get("capability_id") or None,
            contract_id=payload.get("contract_id") or None,
            scope=dict(payload.get("scope") or {}) or None,
            parameters=dict(payload.get("parameters") or {}),
            dependencies=list(payload.get("dependencies") or []),
        )
    if tool_name == "evaluate_virtual_model":
        return runtime.evaluate_virtual_model(
            capability_id=payload.get("capability_id") or "virtual.evaluate.v1",
            contract_id=payload.get("contract_id") or None,
            scope=dict(payload.get("scope") or {}) or None,
            parameters=dict(payload.get("parameters") or {}),
        )
    if tool_name == "finalize_report":
        return runtime.finalize_report(payload.get("run_id") or None)
    raise AssertionError(f"unhandled Pertura product tool: {tool_name}")
