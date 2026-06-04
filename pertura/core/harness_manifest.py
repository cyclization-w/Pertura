"""Machine-readable harness positioning and developer vocabulary."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


HARNESS_THESIS: dict[str, Any] = {
    "name": "Pertura-v2",
    "positioning": "scientific_agent_harness",
    "core_principle": "free_reasoning_gated_commit",
    "one_sentence": (
        "Pertura-v2 lets an LLM explore scientific analysis freely, then checks "
        "committed actions and conclusions against an editable analysis graph, "
        "capability contracts, observation memory, evidence-chain audit, and "
        "trace-driven rethinking plans."
    ),
    "distinctive_primitives": [
        {
            "primitive_id": "analysis_graph_gate",
            "label": "User-editable analysis graph + gate",
            "why_it_matters": (
                "Users define scientific analysis nodes and gate conditions, while "
                "the LLM chooses which valid node/capability to pursue."
            ),
            "developer_surfaces": [
                "AnalysisGraphSpec",
                "pertura spec audit",
                "pertura spec contract",
                "get_node_contract",
                "request_node_transition",
            ],
        },
        {
            "primitive_id": "scientific_observation_memory",
            "label": "Scientific observation memory",
            "why_it_matters": (
                "Repeated observations are grouped by scientific variable, exposing "
                "conflict, coverage, intent, method, branch, and divergence."
            ),
            "developer_surfaces": [
                "query_observation_memory",
                "context_envelope.observation_memory",
                "coverage/conflict/divergence labels",
            ],
        },
        {
            "primitive_id": "deliberative_audit",
            "label": "Deliberative LLM exploration with evidence-chain audit",
            "why_it_matters": (
                "The LLM keeps analysis agency, but execute/complete/finish commits "
                "are checked by gates, schema validation, run audit, and provenance."
            ),
            "developer_surfaces": [
                "get_audit_toolbox",
                "get_context_review",
                "audit_run",
                "review_evidence_chain",
                "plan_rethinking",
                "pertura capsule --verify",
            ],
        },
        {
            "primitive_id": "trace_driven_rethinking",
            "label": "Trace-driven rethinking loop",
            "why_it_matters": (
                "Suspicious, stale, weak, failed, or unsupported results are "
                "converted into a compact plan that combines evidence review, "
                "upstream roots, downstream impact, and repair/branch/intervention "
                "actions for the next LLM turn."
            ),
            "developer_surfaces": [
                "plan_rethinking",
                "context_envelope.trace_driven_rethinking",
                "context_envelope.affordances",
                "trace_upstream",
                "impact_of_change",
            ],
        },
    ],
}


HARNESS_VOCABULARY: list[dict[str, Any]] = [
    {
        "common_term": "Workflow/state graph",
        "pertura_term": "Analysis graph / analysis node",
        "developer_surface": [
            "AnalysisGraphSpec",
            "pertura spec audit",
            "get_node_contract",
        ],
    },
    {
        "common_term": "Tool contract",
        "pertura_term": "Capability contract",
        "developer_surface": [
            "list_capabilities",
            "get_capability_template",
        ],
    },
    {
        "common_term": "Guardrail / human approval",
        "pertura_term": "Gate / interrupt",
        "developer_surface": [
            "request_node_transition",
            "update_design",
            "GraphController",
        ],
    },
    {
        "common_term": "Agent dashboard",
        "pertura_term": "Context review / audit toolbox",
        "developer_surface": [
            "get_audit_toolbox",
            "get_context_review",
            "pertura context",
            "pertura toolbox",
        ],
    },
    {
        "common_term": "Trace / observability",
        "pertura_term": "Event log / provenance graph",
        "developer_surface": [
            "pertura inspect",
            "pertura trace",
            "trace_upstream",
        ],
    },
    {
        "common_term": "Memory",
        "pertura_term": "Scientific observation memory",
        "developer_surface": [
            "query_observation_memory",
            "coverage/conflict/divergence labels",
        ],
    },
    {
        "common_term": "Run audit",
        "pertura_term": "Evidence-chain audit",
        "developer_surface": [
            "audit_run",
            "review_evidence_chain",
            "pertura evidence",
        ],
    },
    {
        "common_term": "Trace-driven recovery",
        "pertura_term": "Rethinking plan",
        "developer_surface": [
            "plan_rethinking",
            "context_envelope.trace_driven_rethinking",
            "context_envelope.affordances",
        ],
    },
    {
        "common_term": "Portable artifact",
        "pertura_term": "Run capsule",
        "developer_surface": [
            "pertura capsule",
            "pertura capsule --verify",
        ],
    },
]


def harness_thesis() -> dict[str, Any]:
    """Return a copy of the Pertura-v2 thesis manifest."""
    return deepcopy(HARNESS_THESIS)


def harness_vocabulary() -> list[dict[str, Any]]:
    """Return a copy of the common harness vocabulary mapping."""
    return deepcopy(HARNESS_VOCABULARY)


def build_harness_manifest() -> dict[str, Any]:
    """Return the public positioning manifest used by CLI, docs, and capsules."""
    return {
        "view_type": "harness_manifest",
        "thesis": harness_thesis(),
        "developer_vocabulary": harness_vocabulary(),
    }
