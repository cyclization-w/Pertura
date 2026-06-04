"""Compact self-audit toolbox for LLM/tool-loop deliberation."""

from __future__ import annotations

from typing import Any

from pertura.core.harness_manifest import harness_thesis


def build_audit_toolbox(snap: Any = None, purpose: str = "deliberation") -> dict:
    safe_purpose = purpose if purpose in {"deliberation", "codegen", "critic", "audit", "report"} else "deliberation"
    active_node = getattr(snap, "active_node_id", "") if snap is not None else ""
    has_observations = bool(getattr(snap, "observations", []) or []) if snap is not None else False
    has_conclusions = bool(getattr(snap, "conclusions", []) or []) if snap is not None else False
    has_findings = bool(getattr(snap, "findings", []) or []) if snap is not None else False
    toolbox = [
        {
            "tool": "get_context_review",
            "use_when": "Start of deliberation/codegen/critic, or whenever context feels stale.",
            "returns": "Bounded dashboard with runtime symbols, working set, provenance index, audit preview, risks, affordances, and budget.",
            "default_args": {"purpose": safe_purpose, "max_items": 6, "token_budget": 6000},
            "expansion_cost": "medium",
            "cli": "pertura context <run_dir> --json",
        },
        {
            "tool": "get_node_contract",
            "use_when": "Before choosing a capability or moving through an analysis node gate.",
            "returns": "Active node goal, gate status, missing inputs, allowed/ready capabilities, and next actions.",
            "default_args": {"node_id": active_node},
            "expansion_cost": "low",
            "cli": "pertura spec contract <graph.json> --domain <domain.json> --node <node_id> --json",
        },
        {
            "tool": "get_capability_template",
            "use_when": "Before execute_code for a capability, especially perturb-seq code.",
            "returns": "Bounded code skeleton, required inputs, readiness, expected observations/artifacts, and execute metadata.",
            "default_args": {"capability_id": "<capability_id>"},
            "expansion_cost": "medium",
            "cli": "",
        },
        {
            "tool": "audit_run",
            "use_when": "Before finish/report decisions or when audit_preview reports errors, blockers, stale evidence, or missing outputs.",
            "returns": "Deterministic run audit with errors, warnings, coverage, and next_actions repair menu.",
            "default_args": {},
            "expansion_cost": "medium",
            "cli": "pertura audit <run_dir> --json",
        },
        {
            "tool": "review_evidence_chain",
            "use_when": "Before reusing or reporting a conclusion, observation, or artifact as evidence.",
            "returns": "Whether the evidence is supported by successful, non-stale execution provenance plus next actions.",
            "default_args": {"node_id": "<conclusion_or_observation_id>"},
            "expansion_cost": "low",
            "cli": "pertura evidence <run_dir> <node_id> --json",
        },
        {
            "tool": "plan_rethinking",
            "use_when": "When an attempt fails, a result is suspicious/negative/weak, evidence is stale, or a conclusion should be traced back before deciding the next move.",
            "returns": "A compact trace-driven plan: evidence review, upstream roots, downstream impact, and recommended repair/branch/intervention tools.",
            "default_args": {"node_id": "<finding_or_conclusion_or_observation_id>", "issue": "<why this needs review>"},
            "expansion_cost": "medium",
            "cli": "",
        },
        {
            "tool": "trace_upstream",
            "use_when": "Only after compact review/audit/evidence review says a dependency path needs expansion.",
            "returns": "Bounded upstream provenance graph for a specific node.",
            "default_args": {"node_id": "<node_id>", "depth": 4},
            "expansion_cost": "high",
            "cli": "pertura trace <run_dir> <node_id> --json",
        },
        {
            "tool": "impact_of_change",
            "use_when": "After a design/input/artifact change to see downstream stale or affected evidence.",
            "returns": "Bounded downstream impact graph and affected observations/conclusions.",
            "default_args": {"node_id": "<changed_node_id>", "depth": 4},
            "expansion_cost": "high",
            "cli": "pertura trace <run_dir> <node_id> --impact --json",
        },
        {
            "tool": "query_observation_memory",
            "use_when": "When a target/metric has repeated observations, conflict, coverage gaps, or branch divergence.",
            "returns": "Variable-level observation memory with conflicts, divergences, coverage, methods, branches, and prior values.",
            "default_args": {"target": "<target>", "metric": "<metric>"},
            "expansion_cost": "medium",
            "cli": "",
        },
        {
            "tool": "inspect_artifact_summary",
            "use_when": "Before trusting a registered file, plot, table, or reported artifact.",
            "returns": "Bounded file/table/figure summary or missing-file signal.",
            "default_args": {"artifact_id": "<artifact_id>"},
            "expansion_cost": "low",
            "cli": "",
        },
    ]
    recommended = ["get_context_review"]
    if active_node:
        recommended.append("get_node_contract")
    if has_findings:
        recommended.extend(["audit_run", "plan_rethinking"])
    if has_observations or has_conclusions:
        recommended.extend(["review_evidence_chain", "plan_rethinking"])
    return {
        "view_type": "audit_toolbox",
        "purpose": safe_purpose,
        "harness_thesis": harness_thesis(),
        "policy": {
            "context_strategy": "compact-first-expand-on-demand",
            "evidence_strategy": "review_evidence_chain before trace_upstream",
            "commit_strategy": "free reasoning, gated/audited commit",
        },
        "active_node_id": active_node,
        "recommended_first_tools": recommended,
        "tools": toolbox,
        "operator_commands": [
            "pertura claims --json",
            "pertura context <run_dir> --json",
            "pertura audit <run_dir> --json",
            "pertura evidence <run_dir> <node_id> --json",
            "pertura trace <run_dir> <node_id> --json",
            "pertura capsule <run_dir> --json",
        ],
    }
