from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pertura_core.version import package_version
from pertura_runtime.product_tools import (
    PRODUCT_TOOL_CONTRACTS,
    PRODUCT_TOOL_NAMES,
    PRODUCT_TOOL_SPECS,
    dispatch_product_tool,
    product_tool_mcp_result,
)

if TYPE_CHECKING:
    from pertura_runtime.product import PerturaProductRuntime

__all__ = [
    "PRODUCT_TOOL_CONTRACTS",
    "PRODUCT_TOOL_NAMES",
    "create_product_mcp_server",
]


def create_product_mcp_server(runtime: "PerturaProductRuntime"):
    """Wrap the neutral five-tool surface for the Claude Agent SDK."""

    from claude_agent_sdk import create_sdk_mcp_server, tool

    tools = []
    for spec in PRODUCT_TOOL_SPECS:

        async def invoke(
            args: dict[str, Any],
            *,
            _tool_name: str = spec.name,
        ) -> dict[str, Any]:
            response = dispatch_product_tool(runtime, _tool_name, args)
            return product_tool_mcp_result(response)

        tools.append(
            tool(
                spec.name,
                spec.description,
                spec.json_input_schema(),
            )(invoke)
        )

    return create_sdk_mcp_server(
        name="pertura",
        version=package_version(),
        tools=tools,
    )
