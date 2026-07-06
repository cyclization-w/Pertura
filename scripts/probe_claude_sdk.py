from __future__ import annotations

import argparse
import asyncio
import dataclasses
import inspect
from typing import Any

PERTURA_OPTION_FIELDS = {
    "cwd",
    "model",
    "system_prompt",
    "tools",
    "allowed_tools",
    "disallowed_tools",
    "mcp_servers",
    "strict_mcp_config",
    "permission_mode",
    "max_turns",
    "max_budget_usd",
    "can_use_tool",
    "hooks",
    "include_hook_events",
    "setting_sources",
    "env",
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Probe the installed Claude Agent SDK API shape.")
    parser.add_argument("--live", action="store_true", help="Run a tiny live query with hooks.")
    args = parser.parse_args(argv)

    try:
        import claude_agent_sdk as sdk
    except ModuleNotFoundError:
        print("claude_agent_sdk is not installed")
        return 1

    print("claude_agent_sdk version:", getattr(sdk, "__version__", "unknown"))
    for name in [
        "ClaudeAgentOptions",
        "ClaudeSDKClient",
        "query",
        "tool",
        "create_sdk_mcp_server",
        "HookMatcher",
    ]:
        obj = getattr(sdk, name, None)
        print(f"{name}: {obj}")
        if obj is not None:
            try:
                print("  signature:", inspect.signature(obj))
            except (TypeError, ValueError):
                pass

    _validate_options_fields(sdk)
    if args.live:
        asyncio.run(_live_probe(sdk))
    return 0


def _options_fields(sdk: Any) -> set[str]:
    options_cls = getattr(sdk, "ClaudeAgentOptions", None)
    if options_cls is None:
        return set()
    if dataclasses.is_dataclass(options_cls):
        return {field.name for field in dataclasses.fields(options_cls)}
    return set(getattr(options_cls, "__annotations__", {}).keys())


def _validate_options_fields(sdk: Any) -> None:
    fields = _options_fields(sdk)
    print("ClaudeAgentOptions fields:", ", ".join(sorted(fields)) if fields else "<unknown>")
    missing = sorted(PERTURA_OPTION_FIELDS - fields) if fields else []
    supported = sorted(PERTURA_OPTION_FIELDS & fields) if fields else sorted(PERTURA_OPTION_FIELDS)
    print("Pertura supported option fields:", ", ".join(supported))
    if missing:
        print("Pertura option fields that will be filtered by options.py:", ", ".join(missing))
    else:
        print("All Pertura option fields are supported by installed SDK.")


async def _live_probe(sdk: Any) -> None:
    async def hook(input_data: dict[str, Any], tool_use_id: str | None, context: Any) -> dict[str, Any]:
        print("hook fired:", input_data.get("hook_event_name"), input_data.get("tool_name"))
        return {}

    options = sdk.ClaudeAgentOptions(
        system_prompt="Reply briefly.",
        hooks={"PreToolUse": [sdk.HookMatcher(hooks=[hook])]},
        max_turns=1,
    )
    async for message in sdk.query(prompt="Say hello without tools.", options=options):
        print(type(message).__name__)


if __name__ == "__main__":
    raise SystemExit(main())