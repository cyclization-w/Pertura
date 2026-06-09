"""Event sourcing core: SQLite store, replay reducer, graph builder, response cache."""

from .errors import PerturaError
from .store import Store
from .controller import GraphController, GraphMutationError
from .policy import PolicyDecision, PolicyEngine
from .views import (
    build_context_view, build_view, build_attempt_view, build_observation_view,
    build_artifact_view, build_branch_view, build_trace_view, build_impact_view,
)
from .behaviors import Behavior, BehaviorRegistry, default_behaviors
from .reducer import reduce, reduce_incremental
from .graph import (
    build_graph, validate_graph, graph_violations_to_findings,
    trace_upstream, impact_of_change,
)
from .cache import ResponseCache, hash_llm_request, hash_tool_call, hash_code_execution
from .fixtures import RecordedLLMFixtures, FixtureMiss, llm_fixture_hash
from .replay import (
    ReplayError, ReplayResult, ForkResult,
    replay_store, fork_store, diff_stores,
    run_integrity, stable_json, stable_json_sha256,
)
from .relations import (
    RelationEffect, relation_effect, enrich_edge,
    edge_propagates_change, relation_summary,
)
from .observation_memory import (
    observation_key, build_observation_memory_view,
    build_memory_entries, build_coverage_entries,
)
from .audit import audit_run
from .evidence_chain import review_evidence_chain
from .rethinking import plan_rethinking
from .audit_toolbox import build_audit_toolbox
from .claims import (
    CORE_CLAIMS, core_claim_ids, core_claim,
    capsule_claim_id, standalone_claim_command, standalone_claim_command_array,
    source_tree_claim_command, claim_id_for_script,
)
from .harness_manifest import (
    build_harness_manifest, harness_thesis, harness_vocabulary,
)
from .work_order import build_active_work_order, render_active_work_order
from .execution_state import compile_execution_state, compile_runtime_issues
from .candidate_actions import compile_candidate_actions
from .node_navigation import evaluate_node_navigation
from .workflow_controller import evaluate_workflow_autopilot, workflow_gap
from pertura.spec.gating import GateEvaluator, GateDecision

__all__ = [
    "PerturaError", "Store", "GraphController", "GraphMutationError",
    "PolicyDecision", "PolicyEngine",
    "build_context_view", "build_view", "build_attempt_view", "build_observation_view",
    "build_artifact_view", "build_branch_view", "build_trace_view", "build_impact_view",
    "Behavior", "BehaviorRegistry", "default_behaviors",
    "reduce", "reduce_incremental",
    "build_graph", "validate_graph", "graph_violations_to_findings",
    "trace_upstream", "impact_of_change",
    "ResponseCache", "hash_llm_request", "hash_tool_call", "hash_code_execution",
    "RecordedLLMFixtures", "FixtureMiss", "llm_fixture_hash",
    "ReplayError", "ReplayResult", "ForkResult", "replay_store", "fork_store", "diff_stores",
    "run_integrity", "stable_json", "stable_json_sha256",
    "RelationEffect", "relation_effect", "enrich_edge",
    "edge_propagates_change", "relation_summary",
    "observation_key", "build_observation_memory_view",
    "build_memory_entries", "build_coverage_entries",
    "audit_run",
    "review_evidence_chain",
    "plan_rethinking",
    "build_audit_toolbox",
    "CORE_CLAIMS", "core_claim_ids", "core_claim",
    "capsule_claim_id", "standalone_claim_command", "standalone_claim_command_array",
    "source_tree_claim_command", "claim_id_for_script",
    "build_harness_manifest", "harness_thesis", "harness_vocabulary",
    "build_active_work_order", "render_active_work_order",
    "compile_execution_state", "compile_runtime_issues", "compile_candidate_actions",
    "evaluate_node_navigation", "evaluate_workflow_autopilot", "workflow_gap",
    "GateEvaluator", "GateDecision",
]
