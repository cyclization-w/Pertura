from __future__ import annotations

import ast
from pathlib import Path

from pertura_runtime.product_tools.definitions import PRODUCT_TOOL_NAMES
from pertura_workflow.capabilities.executors import has_executor, has_validator
from pertura_workflow.capabilities.registry import CapabilityRegistry
from pertura_workflow.environment import SUPPORTED_PROFILES


INTERNAL_PROFILE_EXECUTORS = {
    "edger_pseudobulk",
    "sceptre_association",
    "propeller_composition",
    "enrichment_gsea_prerank",
    "regulator_activity_ulm",
}


def audit_capabilities(repo_root: str | Path) -> dict[str, object]:
    root = Path(repo_root).resolve()
    registry = CapabilityRegistry.load_default(include_external=False)
    specs = registry.specs()
    findings: list[str] = []
    if len(PRODUCT_TOOL_NAMES) != 5:
        findings.append("Pertura domain tool surface is not exactly five")
    visible = {item.capability_id for item in registry.list()}
    if "virtual.evaluate.v1" in visible:
        findings.append("superseded virtual.evaluate.v1 is visible by default")
    if "virtual.evaluate.comprehensive.v1" not in visible:
        findings.append("comprehensive virtual evaluator is not visible")
    for spec in specs:
        if not has_executor(spec.executor):
            findings.append(f"{spec.capability_id}: executor is missing")
        if not has_validator(spec.validator):
            findings.append(f"{spec.capability_id}: validator is missing")
        if spec.trust_level.value == "exploratory":
            if spec.version != "0.1.0":
                findings.append(f"{spec.capability_id}: exploratory version is not 0.1.0")
            if spec.claim_permissions:
                findings.append(f"{spec.capability_id}: exploratory capability has claim permission")
            if spec.metadata.get("validation_status") != "synthetic_only":
                findings.append(f"{spec.capability_id}: synthetic-only status is missing")
            if spec.validator != "candidate_standard":
                findings.append(f"{spec.capability_id}: candidate validator is not enforced")
        profile = str(spec.metadata.get("environment_profile") or "")
        mode = str(spec.metadata.get("execution_mode") or "")
        if profile and profile not in SUPPORTED_PROFILES:
            findings.append(f"{spec.capability_id}: unknown environment profile {profile}")
        if mode == "isolated_python" and not profile:
            findings.append(f"{spec.capability_id}: isolated execution lacks environment profile")
        if profile and mode != "isolated_python" and spec.executor not in INTERNAL_PROFILE_EXECUTORS:
            findings.append(
                f"{spec.capability_id}: environment is recorded but execution is not bound"
            )
    ulm = root / "src/pertura_workflow/capabilities/runners/ulm_runner.py"
    try:
        tree = ast.parse(ulm.read_text(encoding="utf-8"))
        calls = {
            ast.unparse(node.func)
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
        }
        if "dc.mt.ulm" not in calls:
            findings.append("ULM runner does not call decoupler.mt.ulm")
    except (OSError, SyntaxError):
        findings.append("ULM runner cannot be parsed")
    return {
        "schema_version": "pertura-capability-audit-v1",
        "passed": not findings,
        "capability_count": len(specs),
        "visible_capability_count": len(visible),
        "domain_tool_count": len(PRODUCT_TOOL_NAMES),
        "findings": findings,
    }
