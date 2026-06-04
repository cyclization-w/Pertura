"""Pertura: analysis-graph scientific harness for agentic notebooks.

Public extension surface:
  AnalysisGraph defines analysis nodes and gates.
  Capability defines LLM action/output contracts.
  Domain bundles graph + capabilities + rubrics as a reusable domain pack.

LLMs can explore through tools, but graph-affecting actions are recorded and
checked before they become durable scientific state.
"""

from . import conditions
from pertura.agent.loop import Workbench
from pertura.domain import Domain
from pertura.models import Observation
from pertura.capabilities import Capability, CapabilityRegistry, capability, build_capability_registry
from pertura.spec.builder import (
    AnalysisGraph,
    AnalysisGraphSpec,
    AnalysisNodeSpec,
    AnalysisNodeBuilder,
    ConditionSpec,
    condition,
    load_analysis_graph,
    save_analysis_graph,
    validate_analysis_graph,
    node_contract,
    graph_contract,
    audit_analysis_graph,
)
from pertura.spec.compiler import compile_conditions

__all__ = [
    "Workbench",
    "Domain",
    "Observation",
    "Capability",
    "CapabilityRegistry",
    "capability",
    "build_capability_registry",
    "AnalysisGraph",
    "AnalysisGraphSpec",
    "AnalysisNodeSpec",
    "AnalysisNodeBuilder",
    "ConditionSpec",
    "conditions",
    "condition",
    "load_analysis_graph",
    "save_analysis_graph",
    "validate_analysis_graph",
    "compile_conditions",
    "node_contract",
    "graph_contract",
    "audit_analysis_graph",
]
