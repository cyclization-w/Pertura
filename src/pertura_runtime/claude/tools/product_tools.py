from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pertura_core.version import package_version
from pertura_runtime.product_tools import (
    PRODUCT_TOOL_CONTRACTS,
    PRODUCT_TOOL_NAMES,
    PRODUCT_TOOL_SPECS,
    dispatch_product_tool,
)

if TYPE_CHECKING:
    from pertura_runtime.product import PerturaProductRuntime

__all__ = [
    "PRODUCT_TOOL_CONTRACTS",
    "PRODUCT_TOOL_NAMES",
    "create_product_mcp_server",
]


def _claude_input_schema(input_types: dict[str, str]) -> dict[str, type]:
    type_map = {"string": str, "object": dict, "array": list}
    return {name: type_map[type_name] for name, type_name in input_types.items()}


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
            return dispatch_product_tool(runtime, _tool_name, args)

        tools.append(
            tool(
                spec.name,
                spec.description,
                _claude_input_schema(dict(spec.input_types)),
            )(invoke)
        )

    return create_sdk_mcp_server(
        name="pertura",
        version=package_version(),
        tools=tools,
    )
