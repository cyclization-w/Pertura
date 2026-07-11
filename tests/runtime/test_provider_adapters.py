from __future__ import annotations

from pertura_runtime.adapters.openai import (
    build_openai_dynamic_instructions,
    openai_adapter_status,
    openai_function_schemas,
    provider_surface,
)
from pertura_runtime.claude.tools.product_tools import (
    PRODUCT_TOOL_CONTRACTS as CLAUDE_CONTRACTS,
    PRODUCT_TOOL_NAMES as CLAUDE_NAMES,
)
from pertura_runtime.product_tools import (
    PRODUCT_TOOL_CONTRACTS,
    PRODUCT_TOOL_NAMES,
    dispatch_product_tool,
)


def test_provider_tool_surfaces_share_the_frozen_five_tools() -> None:
    schemas = openai_function_schemas()

    assert CLAUDE_NAMES == PRODUCT_TOOL_NAMES
    assert CLAUDE_CONTRACTS == PRODUCT_TOOL_CONTRACTS
    assert tuple(item["name"] for item in schemas) == PRODUCT_TOOL_NAMES
    for schema, name in zip(schemas, PRODUCT_TOOL_NAMES):
        expected = PRODUCT_TOOL_CONTRACTS[name]["input"]
        observed = {
            key: value["type"]
            for key, value in schema["parameters"]["properties"].items()
        }
        assert observed == expected


def test_openai_skeleton_is_import_safe_and_explicitly_unimplemented() -> None:
    surface = provider_surface()
    status = openai_adapter_status()

    assert surface.provider_id == "openai-agents-sdk"
    assert surface.implemented is False
    assert status["implemented"] is False
    assert status["environment_ready"] is False
    assert status["tool_names"] == list(PRODUCT_TOOL_NAMES)


def test_openai_dynamic_instructions_load_only_selected_neutral_skills() -> None:
    instructions = build_openai_dynamic_instructions(
        ["inspect-perturb-seq-design", "interpret-perturb-seq-results"]
    )

    assert "Loaded Pertura skill: inspect-perturb-seq-design" in instructions
    assert "Loaded Pertura skill: interpret-perturb-seq-results" in instructions
    assert "diagnose-perturb-seq-screen" not in instructions
    lowered = instructions.lower()
    assert "claudeagentoptions" not in lowered
    assert "mcp__" not in lowered


class FakeRuntime:
    def __init__(self) -> None:
        self.calls = []

    def inspect_dataset(self, path, **kwargs):
        self.calls.append(("inspect_dataset", path, kwargs))
        return {"tool": "inspect_dataset"}

    def run_diagnostic(self, capability_id, **kwargs):
        self.calls.append(("run_diagnostic", capability_id, kwargs))
        return {"tool": "run_diagnostic"}

    def run_analysis(self, objective, **kwargs):
        self.calls.append(("run_analysis", objective, kwargs))
        return {"tool": "run_analysis"}

    def evaluate_virtual_model(self, **kwargs):
        self.calls.append(("evaluate_virtual_model", None, kwargs))
        return {"tool": "evaluate_virtual_model"}

    def finalize_report(self, run_id):
        self.calls.append(("finalize_report", run_id, {}))
        return {"tool": "finalize_report"}


def test_neutral_dispatch_is_the_single_product_handler_path() -> None:
    runtime = FakeRuntime()

    assert dispatch_product_tool(runtime, "inspect_dataset", {})["tool"] == "inspect_dataset"
    assert dispatch_product_tool(
        runtime,
        "run_diagnostic",
        {"capability_id": "diagnostic.dataset_integrity.v1"},
    )["tool"] == "run_diagnostic"
    assert dispatch_product_tool(
        runtime,
        "run_analysis",
        {"objective": "replicated expression"},
    )["tool"] == "run_analysis"
    assert dispatch_product_tool(runtime, "evaluate_virtual_model", {})[
        "tool"
    ] == "evaluate_virtual_model"
    assert dispatch_product_tool(runtime, "finalize_report", {})[
        "tool"
    ] == "finalize_report"
    assert [item[0] for item in runtime.calls] == list(PRODUCT_TOOL_NAMES)
