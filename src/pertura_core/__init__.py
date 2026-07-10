"""Stable, runtime-neutral contracts for the capability-first Pertura kernel."""

from pertura_core.models import (
    AnalysisStatus,
    CapabilityRunRequest,
    CapabilitySpec,
    CapabilityTrust,
    DatasetContract,
    DependencyRef,
    DesignConfirmation,
    DiagnosticStatus,
    PromotionDecision,
    ResultEnvelope,
    RunReceipt,
    ScopeKey,
    ScientificStatement,
    SourceClass,
    VirtualStatus,
)
from pertura_core.scope import ScopeComparison, compare_scope_keys, scope_can_support
from pertura_core.receipt_verification import verify_receipt

__all__ = [
    "AnalysisStatus",
    "CapabilityRunRequest",
    "CapabilitySpec",
    "CapabilityTrust",
    "DatasetContract",
    "DependencyRef",
    "DesignConfirmation",
    "DiagnosticStatus",
    "PromotionDecision",
    "ResultEnvelope",
    "RunReceipt",
    "ScopeComparison",
    "ScopeKey",
    "ScientificStatement",
    "SourceClass",
    "VirtualStatus",
    "compare_scope_keys",
    "scope_can_support",
    "verify_receipt",
]
