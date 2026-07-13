from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pertura_runtime.claude.workspace import ClaudeRunWorkspace
from pertura_gate.evidence.registry import EvidenceRegistry
from pertura_gate.core.policy import GatePolicy, policy_for_profile
from pertura_gate.render.renderer import render_evidence_report
from pertura_gate.resolver.resolver import resolve_artifact_strength, resolve_claims
from pertura_workflow.method_router import route_analysis
from pertura_workflow.runners.control_calibration import run_label_permutation_null, run_ntc_vs_ntc_calibration
from pertura_workflow.runners.pseudobulk_de import run_pseudobulk_de_for_registered_contrast
from pertura_workflow.runners.target_reliability import run_target_reliability_audit as run_target_reliability


def create_evidence_mcp_server(
    workspace: ClaudeRunWorkspace,
    *,
    policy: GatePolicy | None = None,
):
    """Create the in-process MCP server for Pertura evidence tools.

    The import is intentionally local so the rest of Pertura remains importable in
    environments that have not installed claude-agent-sdk.
    """

    from claude_agent_sdk import create_sdk_mcp_server, tool

    registry = EvidenceRegistry.for_run(workspace.root)
    # Direct callers retain the historical smoke default for compatibility.
    # The Claude runtime always injects an explicit immutable policy.
    bound_policy = policy or policy_for_profile("smoke")

    @tool(
        "register_perturbation_design_manifest",
        "Register a canonical perturbation design manifest. The runtime sets evidence_class=observed_metadata and uses the manifest as identity authority.",
        {
            "path": str,
            "adapter_name": str,
            "dataset_id": str,
            "source_column": str,
            "raw_labels": list,
            "conditions": list,
            "guide_to_target_map": dict,
            "provenance_level": str,
            "metadata": dict,
            "notes": str,
        },
    )
    async def register_perturbation_design_manifest(args: dict[str, Any]) -> dict[str, Any]:
        path = _resolve_evidence_source_path(workspace, str(args.get("path") or ""))
        artifact = registry.register_perturbation_design_manifest(
            path=str(path.relative_to(workspace.root)),
            adapter_name=_optional_text(args.get("adapter_name")) or "guide_label_v1",
            dataset_id=_optional_text(args.get("dataset_id")),
            source_column=_optional_text(args.get("source_column")),
            raw_labels=[str(item) for item in args.get("raw_labels") or []],
            conditions=list(args.get("conditions") or []),
            guide_to_target_map=_optional_dict(args.get("guide_to_target_map")),
            provenance_level=_optional_text(args.get("provenance_level")) or "deterministic_rule",
            metadata=_optional_dict(args.get("metadata")),
            notes=_optional_text(args.get("notes")),
        )
        return _registration_result(workspace, registry, artifact)
    @tool(
        "register_experiment_design_artifact",
        "Register structured experiment-design metadata used to build an EligibilityProfile.",
        {
            "path": str,
            "assay": str,
            "perturbation_modality": str,
            "guide_capture": str,
            "moi": str,
            "controls": dict,
            "replication": dict,
            "loading_doublet_policy": str,
            "timepoint": str,
            "scope": dict,
            "quality": dict,
            "metadata": dict,
            "notes": str,
        },
    )
    async def register_experiment_design_artifact(args: dict[str, Any]) -> dict[str, Any]:
        path = _resolve_evidence_source_path(workspace, str(args.get("path") or ""))
        artifact = registry.register_experiment_design(
            path=str(path.relative_to(workspace.root)),
            assay=_optional_text(args.get("assay")),
            perturbation_modality=_optional_text(args.get("perturbation_modality")),
            guide_capture=_optional_text(args.get("guide_capture")),
            moi=args.get("moi"),
            controls=_optional_dict(args.get("controls")),
            replication=_optional_dict(args.get("replication")),
            loading_doublet_policy=_optional_text(args.get("loading_doublet_policy")),
            timepoint=_optional_text(args.get("timepoint")),
            scope=_optional_dict(args.get("scope")),
            quality=_optional_dict(args.get("quality")),
            metadata=_optional_dict(args.get("metadata")),
            notes=_optional_text(args.get("notes")),
        )
        return _registration_result(workspace, registry, artifact)

    @tool(
        "register_guide_assignment_artifact",
        "Register structured guide/treatment assignment metadata used to build an EligibilityProfile.",
        {
            "path": str,
            "assignment_method": str,
            "assigned_count": int,
            "unassigned_count": int,
            "multi_guide_count": int,
            "guide_distribution": dict,
            "ambient_guide_handling": str,
            "moi_inference": str,
            "target_summary": dict,
            "guide_to_target_map_hash": str,
            "scope": dict,
            "quality": dict,
            "metadata": dict,
            "notes": str,
        },
    )
    async def register_guide_assignment_artifact(args: dict[str, Any]) -> dict[str, Any]:
        path = _resolve_evidence_source_path(workspace, str(args.get("path") or ""))
        artifact = registry.register_guide_assignment(
            path=str(path.relative_to(workspace.root)),
            assignment_method=_optional_text(args.get("assignment_method")),
            assigned_count=_optional_int(args.get("assigned_count")),
            unassigned_count=_optional_int(args.get("unassigned_count")),
            multi_guide_count=_optional_int(args.get("multi_guide_count")),
            guide_distribution=_optional_dict(args.get("guide_distribution")),
            ambient_guide_handling=_optional_text(args.get("ambient_guide_handling")),
            moi_inference=args.get("moi_inference"),
            target_summary=_optional_dict(args.get("target_summary")),
            guide_to_target_map_hash=_optional_text(args.get("guide_to_target_map_hash")),
            scope=_optional_dict(args.get("scope")),
            quality=_optional_dict(args.get("quality")),
            metadata=_optional_dict(args.get("metadata")),
            notes=_optional_text(args.get("notes")),
        )
        return _registration_result(workspace, registry, artifact)

    @tool(
        "register_target_qc_artifact",
        "Register structured target/control QC metadata used to build an EligibilityProfile.",
        {
            "path": str,
            "target": str,
            "control": str,
            "n_target_cells": int,
            "n_control_cells": int,
            "guides_per_target": int,
            "cells_per_guide": dict,
            "guide_consistency": str,
            "control_calibration": dict,
            "min_cell_policy": str,
            "batch_coverage": dict,
            "donor_coverage": dict,
            "estimand": str,
            "model_covariates": list,
            "scope": dict,
            "quality": dict,
            "metadata": dict,
            "notes": str,
        },
    )
    async def register_target_qc_artifact(args: dict[str, Any]) -> dict[str, Any]:
        path = _resolve_evidence_source_path(workspace, str(args.get("path") or ""))
        artifact = registry.register_target_qc(
            path=str(path.relative_to(workspace.root)),
            target=_optional_text(args.get("target")),
            control=_optional_text(args.get("control")),
            n_target_cells=_optional_int(args.get("n_target_cells")),
            n_control_cells=_optional_int(args.get("n_control_cells")),
            guides_per_target=_optional_int(args.get("guides_per_target")),
            cells_per_guide=_optional_dict(args.get("cells_per_guide")),
            guide_consistency=_optional_text(args.get("guide_consistency")),
            control_calibration=_optional_dict(args.get("control_calibration")),
            min_cell_policy=_optional_text(args.get("min_cell_policy")),
            batch_coverage=_optional_dict(args.get("batch_coverage")),
            donor_coverage=_optional_dict(args.get("donor_coverage")),
            estimand=_optional_text(args.get("estimand")),
            model_covariates=[str(item) for item in args.get("model_covariates") or []],
            scope=_optional_dict(args.get("scope")),
            quality=_optional_dict(args.get("quality")),
            metadata=_optional_dict(args.get("metadata")),
            notes=_optional_text(args.get("notes")),
        )
        return _registration_result(workspace, registry, artifact)

    @tool(
        "register_measured_de_artifact",
        "Register a runtime-validated measured differential-expression artifact produced by CodeAct.",
        {
            "path": str,
            "contrast_left": str,
            "contrast_baseline": str,
            "method": str,
            "n_left": int,
            "n_baseline": int,
            "multiple_testing": str,
            "has_padj": bool,
            "columns": list,
            "source_data": str,
            "notes": str,
            "scope": dict,
            "predicate": dict,
            "quality": dict,
            "eligibility": dict,
            "code_sha256": str,
            "execution_hash": str,
        },
    )
    async def register_measured_de_artifact(args: dict[str, Any]) -> dict[str, Any]:
        path = _resolve_evidence_source_path(workspace, str(args.get("path") or ""))
        artifact = registry.register_measured_de(
            path=str(path.relative_to(workspace.root)),
            contrast_left=_optional_text(args.get("contrast_left")),
            contrast_baseline=_optional_text(args.get("contrast_baseline")),
            method=_optional_text(args.get("method")),
            n_left=_optional_int(args.get("n_left")),
            n_baseline=_optional_int(args.get("n_baseline")),
            multiple_testing=_optional_text(args.get("multiple_testing")),
            has_padj=bool(args.get("has_padj", False)),
            columns=[str(item) for item in args.get("columns") or []],
            source_data=_optional_text(args.get("source_data")),
            notes=_optional_text(args.get("notes")),
            scope=_optional_dict(args.get("scope")),
            predicate=_optional_dict(args.get("predicate")),
            quality=_optional_dict(args.get("quality")),
            eligibility=_optional_dict(args.get("eligibility")),
            code_sha256=_optional_text(args.get("code_sha256")),
            execution_hash=_optional_text(args.get("execution_hash")),
        )
        return _registration_result(workspace, registry, artifact)

    @tool(
        "register_predicted_effect_artifact",
        "Register a prediction artifact. The runtime sets evidence_class=predicted regardless of file contents.",
        {
            "path": str,
            "model_name": str,
            "model_version": str,
            "prediction_method": str,
            "perturbation": str,
            "target_context": str,
            "readout_type": str,
            "target": str,
            "notes": str,
            "scope": dict,
            "predicate": dict,
            "quality": dict,
        },
    )
    async def register_predicted_effect_artifact(args: dict[str, Any]) -> dict[str, Any]:
        path = _resolve_evidence_source_path(workspace, str(args.get("path") or ""))
        artifact = registry.register_predicted_effect(
            path=str(path.relative_to(workspace.root)),
            model_name=_optional_text(args.get("model_name")),
            model_version=_optional_text(args.get("model_version")),
            prediction_method=_optional_text(args.get("prediction_method")),
            perturbation=_optional_text(args.get("perturbation")),
            target_context=_optional_text(args.get("target_context")),
            readout_type=_optional_text(args.get("readout_type")),
            target=_optional_text(args.get("target")),
            notes=_optional_text(args.get("notes")),
            scope=_optional_dict(args.get("scope")),
            predicate=_optional_dict(args.get("predicate")),
            quality=_optional_dict(args.get("quality")),
        )
        return _registration_result(workspace, registry, artifact)



    @tool(
        "register_virtual_perturbation_prediction_artifact",
        "Register virtual perturbation model output from GEARS, scGPT, CPA/scGen, Geneformer, or custom predictors. This is prediction evidence, not measured evidence.",
        {
            "path": str,
            "tool_name": str,
            "tool_version": str,
            "model_name": str,
            "model_version": str,
            "model_checkpoint_hash": str,
            "prediction_method": str,
            "prediction_type": str,
            "perturbation_query": dict,
            "output_schema": dict,
            "n_predicted_genes": int,
            "n_predicted_cells": int,
            "notes": str,
            "scope": dict,
            "predicate": dict,
            "quality": dict,
            "metadata": dict,
        },
    )
    async def register_virtual_perturbation_prediction_artifact(args: dict[str, Any]) -> dict[str, Any]:
        path = _resolve_evidence_source_path(workspace, str(args.get("path") or ""))
        artifact = registry.register_virtual_perturbation_prediction(
            path=str(path.relative_to(workspace.root)),
            tool_name=_optional_text(args.get("tool_name")),
            tool_version=_optional_text(args.get("tool_version")),
            model_name=_optional_text(args.get("model_name")),
            model_version=_optional_text(args.get("model_version")),
            model_checkpoint_hash=_optional_text(args.get("model_checkpoint_hash")),
            prediction_method=_optional_text(args.get("prediction_method")),
            prediction_type=_optional_text(args.get("prediction_type")),
            perturbation_query=_optional_dict(args.get("perturbation_query")),
            output_schema=_optional_dict(args.get("output_schema")),
            n_predicted_genes=_optional_int(args.get("n_predicted_genes")),
            n_predicted_cells=_optional_int(args.get("n_predicted_cells")),
            notes=_optional_text(args.get("notes")),
            scope=_optional_dict(args.get("scope")),
            predicate=_optional_dict(args.get("predicate")),
            quality=_optional_dict(args.get("quality")),
            metadata=_optional_dict(args.get("metadata")),
        )
        return _registration_result(workspace, registry, artifact)

    @tool(
        "register_prediction_measured_concordance_artifact",
        "Register metric-bound concordance between a virtual perturbation prediction and a registered measured artifact. Concordance is not mechanism validation and does not create measured evidence. Any scope_match input is recorded only as a diagnostic; Pertura computes UID scope compatibility from registered artifacts.",
        {
            "path": str,
            "prediction_artifact_id": str,
            "measured_artifact_id": str,
            "metric": str,
            "metric_value": float,
            "denominator": int,
            "scope_match": str,
            "comparison_method": str,
            "notes": str,
            "scope": dict,
            "predicate": dict,
            "quality": dict,
            "metadata": dict,
        },
    )
    async def register_prediction_measured_concordance_artifact(args: dict[str, Any]) -> dict[str, Any]:
        path = _resolve_evidence_source_path(workspace, str(args.get("path") or ""))
        artifact = registry.register_prediction_measured_concordance(
            path=str(path.relative_to(workspace.root)),
            prediction_artifact_id=_optional_text(args.get("prediction_artifact_id")),
            measured_artifact_id=_optional_text(args.get("measured_artifact_id")),
            metric=_optional_text(args.get("metric")),
            metric_value=_optional_float(args.get("metric_value")),
            denominator=_optional_int(args.get("denominator")),
            scope_match=_optional_text(args.get("scope_match")),
            comparison_method=_optional_text(args.get("comparison_method")),
            notes=_optional_text(args.get("notes")),
            scope=_optional_dict(args.get("scope")),
            predicate=_optional_dict(args.get("predicate")),
            quality=_optional_dict(args.get("quality")),
            metadata=_optional_dict(args.get("metadata")),
        )
        return _registration_result(workspace, registry, artifact)

    @tool(
        "register_virtual_cell_state_transition_artifact",
        "Register CellOracle-style or related virtual cell-state transition output. This is predicted transition evidence, not causal fate conversion.",
        {
            "path": str,
            "tool_name": str,
            "tool_version": str,
            "model_or_network_provenance": dict,
            "transition_type": str,
            "perturbation_query": dict,
            "state_space_reference": dict,
            "notes": str,
            "scope": dict,
            "predicate": dict,
            "quality": dict,
            "metadata": dict,
        },
    )
    async def register_virtual_cell_state_transition_artifact(args: dict[str, Any]) -> dict[str, Any]:
        path = _resolve_evidence_source_path(workspace, str(args.get("path") or ""))
        artifact = registry.register_virtual_cell_state_transition(
            path=str(path.relative_to(workspace.root)),
            tool_name=_optional_text(args.get("tool_name")),
            tool_version=_optional_text(args.get("tool_version")),
            model_or_network_provenance=_optional_dict(args.get("model_or_network_provenance")),
            transition_type=_optional_text(args.get("transition_type")),
            perturbation_query=_optional_dict(args.get("perturbation_query")),
            state_space_reference=_optional_dict(args.get("state_space_reference")),
            notes=_optional_text(args.get("notes")),
            scope=_optional_dict(args.get("scope")),
            predicate=_optional_dict(args.get("predicate")),
            quality=_optional_dict(args.get("quality")),
            metadata=_optional_dict(args.get("metadata")),
        )
        return _registration_result(workspace, registry, artifact)

    @tool(
        "register_curated_prior_artifact",
        "Register a curated prior lookup artifact. This does not register measured pathway activation.",
        {
            "path": str,
            "database": str,
            "database_version": str,
            "term_id": str,
            "term_name": str,
            "target": str,
            "notes": str,
            "scope": dict,
            "predicate": dict,
            "quality": dict,
        },
    )
    async def register_curated_prior_artifact(args: dict[str, Any]) -> dict[str, Any]:
        path = _resolve_evidence_source_path(workspace, str(args.get("path") or ""))
        artifact = registry.register_curated_prior(
            path=str(path.relative_to(workspace.root)),
            database=_optional_text(args.get("database")),
            database_version=_optional_text(args.get("database_version")),
            term_id=_optional_text(args.get("term_id")),
            term_name=_optional_text(args.get("term_name")),
            target=_optional_text(args.get("target")),
            notes=_optional_text(args.get("notes")),
            scope=_optional_dict(args.get("scope")),
            predicate=_optional_dict(args.get("predicate")),
            quality=_optional_dict(args.get("quality")),
        )
        return _registration_result(workspace, registry, artifact)

    @tool(
        "register_perturbation_efficiency_artifact",
        "Register measured perturbation-efficiency or target-engagement evidence with manifest-derived scope. This does not register downstream mechanism.",
        {
            "path": str,
            "perturbation": str,
            "target_gene": str,
            "modality": str,
            "expected_direction": str,
            "observed_direction": str,
            "effect_size": float,
            "pvalue": float,
            "padj": float,
            "method": str,
            "n_target_cells": int,
            "n_control_cells": int,
            "scope": dict,
            "quality": dict,
            "metadata": dict,
            "notes": str,
            "code_sha256": str,
            "execution_hash": str,
        },
    )
    async def register_perturbation_efficiency_artifact(args: dict[str, Any]) -> dict[str, Any]:
        path = _resolve_evidence_source_path(workspace, str(args.get("path") or ""))
        artifact = registry.register_perturbation_efficiency(
            path=str(path.relative_to(workspace.root)),
            perturbation=_optional_text(args.get("perturbation")),
            target_gene=_optional_text(args.get("target_gene")),
            modality=_optional_text(args.get("modality")),
            expected_direction=_optional_text(args.get("expected_direction")),
            observed_direction=_optional_text(args.get("observed_direction")),
            effect_size=_optional_float(args.get("effect_size")),
            pvalue=_optional_float(args.get("pvalue")),
            padj=_optional_float(args.get("padj")),
            method=_optional_text(args.get("method")),
            n_target_cells=_optional_int(args.get("n_target_cells")),
            n_control_cells=_optional_int(args.get("n_control_cells")),
            scope=_optional_dict(args.get("scope")),
            quality=_optional_dict(args.get("quality")),
            metadata=_optional_dict(args.get("metadata")),
            notes=_optional_text(args.get("notes")),
            code_sha256=_optional_text(args.get("code_sha256")),
            execution_hash=_optional_text(args.get("execution_hash")),
        )
        return _registration_result(workspace, registry, artifact)

    @tool(
        "register_curated_enrichment_artifact",
        "Register a curated enrichment result bound to a measured artifact. This adds curated context, not validation.",
        {
            "path": str,
            "input_measured_artifact_id": str,
            "input_gene_set_hash": str,
            "background_universe": str,
            "database": str,
            "database_version": str,
            "term_id": str,
            "term_name": str,
            "method": str,
            "pvalue": float,
            "padj": float,
            "scope": dict,
            "quality": dict,
            "metadata": dict,
            "notes": str,
        },
    )
    async def register_curated_enrichment_artifact(args: dict[str, Any]) -> dict[str, Any]:
        path = _resolve_evidence_source_path(workspace, str(args.get("path") or ""))
        artifact = registry.register_curated_enrichment(
            path=str(path.relative_to(workspace.root)),
            input_measured_artifact_id=_optional_text(args.get("input_measured_artifact_id")),
            input_gene_set_hash=_optional_text(args.get("input_gene_set_hash")),
            background_universe=_optional_text(args.get("background_universe")),
            database=_optional_text(args.get("database")),
            database_version=_optional_text(args.get("database_version")),
            term_id=_optional_text(args.get("term_id")),
            term_name=_optional_text(args.get("term_name")),
            method=_optional_text(args.get("method")),
            pvalue=_optional_float(args.get("pvalue")),
            padj=_optional_float(args.get("padj")),
            scope=_optional_dict(args.get("scope")),
            quality=_optional_dict(args.get("quality")),
            metadata=_optional_dict(args.get("metadata")),
            notes=_optional_text(args.get("notes")),
        )
        return _registration_result(workspace, registry, artifact)

    @tool(
        "register_module_effect_artifact",
        "Register measured module/signature score evidence. This records a module-score association, not mechanism or driver validation.",
        {
            "path": str,
            "module_id": str,
            "module_name": str,
            "module_source": str,
            "module_gene_set_hash": str,
            "scoring_method": str,
            "effect_size": float,
            "method": str,
            "pvalue": float,
            "padj": float,
            "n_target_cells": int,
            "n_control_cells": int,
            "scope": dict,
            "predicate": dict,
            "quality": dict,
            "metadata": dict,
            "notes": str,
            "code_sha256": str,
            "execution_hash": str,
        },
    )
    async def register_module_effect_artifact(args: dict[str, Any]) -> dict[str, Any]:
        path = _resolve_evidence_source_path(workspace, str(args.get("path") or ""))
        artifact = registry.register_module_effect(
            path=str(path.relative_to(workspace.root)),
            module_id=_optional_text(args.get("module_id")),
            module_name=_optional_text(args.get("module_name")),
            module_source=_optional_text(args.get("module_source")),
            module_gene_set_hash=_optional_text(args.get("module_gene_set_hash")),
            scoring_method=_optional_text(args.get("scoring_method")),
            effect_size=_optional_float(args.get("effect_size")),
            method=_optional_text(args.get("method")),
            pvalue=_optional_float(args.get("pvalue")),
            padj=_optional_float(args.get("padj")),
            n_target_cells=_optional_int(args.get("n_target_cells")),
            n_control_cells=_optional_int(args.get("n_control_cells")),
            scope=_optional_dict(args.get("scope")),
            predicate=_optional_dict(args.get("predicate")),
            quality=_optional_dict(args.get("quality")),
            metadata=_optional_dict(args.get("metadata")),
            notes=_optional_text(args.get("notes")),
            code_sha256=_optional_text(args.get("code_sha256")),
            execution_hash=_optional_text(args.get("execution_hash")),
        )
        return _registration_result(workspace, registry, artifact)

    @tool(
        "register_global_effect_artifact",
        "Register measured global perturbation response evidence. This records distribution/embedding shift, not gene-specific or causal fate evidence.",
        {
            "path": str,
            "metric": str,
            "feature_space": str,
            "embedding": str,
            "comparison_method": str,
            "effect_size": float,
            "distance": float,
            "null_model": str,
            "permutation_or_test": str,
            "pvalue": float,
            "padj": float,
            "n_target_cells": int,
            "n_control_cells": int,
            "scope": dict,
            "predicate": dict,
            "quality": dict,
            "metadata": dict,
            "notes": str,
            "code_sha256": str,
            "execution_hash": str,
        },
    )
    async def register_global_effect_artifact(args: dict[str, Any]) -> dict[str, Any]:
        path = _resolve_evidence_source_path(workspace, str(args.get("path") or ""))
        artifact = registry.register_global_effect(
            path=str(path.relative_to(workspace.root)),
            metric=_optional_text(args.get("metric")),
            feature_space=_optional_text(args.get("feature_space")),
            embedding=_optional_text(args.get("embedding")),
            comparison_method=_optional_text(args.get("comparison_method")),
            effect_size=_optional_float(args.get("effect_size")),
            distance=_optional_float(args.get("distance")),
            null_model=_optional_text(args.get("null_model")),
            permutation_or_test=_optional_text(args.get("permutation_or_test")),
            pvalue=_optional_float(args.get("pvalue")),
            padj=_optional_float(args.get("padj")),
            n_target_cells=_optional_int(args.get("n_target_cells")),
            n_control_cells=_optional_int(args.get("n_control_cells")),
            scope=_optional_dict(args.get("scope")),
            predicate=_optional_dict(args.get("predicate")),
            quality=_optional_dict(args.get("quality")),
            metadata=_optional_dict(args.get("metadata")),
            notes=_optional_text(args.get("notes")),
            code_sha256=_optional_text(args.get("code_sha256")),
            execution_hash=_optional_text(args.get("execution_hash")),
        )
        return _registration_result(workspace, registry, artifact)
    @tool(
        "register_composition_effect_artifact",
        "Register measured cell-state composition evidence. This records composition or abundance association, not causal fate conversion, target engagement, or mechanism validation.",
        {
            "path": str,
            "state_source": str,
            "state_assignment_column": str,
            "comparison_method": str,
            "state_counts_by_condition": dict,
            "counts_by_state": dict,
            "state_level_deltas": dict,
            "effect_size": float,
            "pvalue": float,
            "padj": float,
            "n_target_cells": int,
            "n_control_cells": int,
            "scope": dict,
            "predicate": dict,
            "quality": dict,
            "metadata": dict,
            "notes": str,
            "code_sha256": str,
            "execution_hash": str,
        },
    )
    async def register_composition_effect_artifact(args: dict[str, Any]) -> dict[str, Any]:
        path = _resolve_evidence_source_path(workspace, str(args.get("path") or ""))
        artifact = registry.register_composition_effect(
            path=str(path.relative_to(workspace.root)),
            state_source=_optional_text(args.get("state_source")),
            state_assignment_column=_optional_text(args.get("state_assignment_column")),
            comparison_method=_optional_text(args.get("comparison_method")),
            state_counts_by_condition=_optional_dict(args.get("state_counts_by_condition")),
            counts_by_state=_optional_dict(args.get("counts_by_state")),
            state_level_deltas=_optional_dict(args.get("state_level_deltas")),
            effect_size=_optional_float(args.get("effect_size")),
            pvalue=_optional_float(args.get("pvalue")),
            padj=_optional_float(args.get("padj")),
            n_target_cells=_optional_int(args.get("n_target_cells")),
            n_control_cells=_optional_int(args.get("n_control_cells")),
            scope=_optional_dict(args.get("scope")),
            predicate=_optional_dict(args.get("predicate")),
            quality=_optional_dict(args.get("quality")),
            metadata=_optional_dict(args.get("metadata")),
            notes=_optional_text(args.get("notes")),
            code_sha256=_optional_text(args.get("code_sha256")),
            execution_hash=_optional_text(args.get("execution_hash")),
        )
        return _registration_result(workspace, registry, artifact)

    @tool(
        "register_cell_state_reference_artifact",
        "Register transcriptomic state-space, clustering, marker, and annotation context. This is scope/state context, not perturbation effect evidence.",
        {
            "path": str,
            "assignment_column": str,
            "embedding_methods": list,
            "clustering_method": str,
            "annotation_method": str,
            "marker_summary_path": str,
            "source_data_path": str,
            "source_data_sha256": str,
            "scope": dict,
            "quality": dict,
            "metadata": dict,
            "notes": str,
        },
    )
    async def register_cell_state_reference_artifact(args: dict[str, Any]) -> dict[str, Any]:
        path = _resolve_evidence_source_path(workspace, str(args.get("path") or ""))
        artifact = registry.register_cell_state_reference(
            path=str(path.relative_to(workspace.root)),
            assignment_column=_optional_text(args.get("assignment_column")),
            embedding_methods=_optional_list(args.get("embedding_methods")),
            clustering_method=_optional_text(args.get("clustering_method")),
            annotation_method=_optional_text(args.get("annotation_method")),
            marker_summary_path=_optional_text(args.get("marker_summary_path")),
            source_data_path=_optional_text(args.get("source_data_path")),
            source_data_sha256=_optional_text(args.get("source_data_sha256")),
            scope=_optional_dict(args.get("scope")),
            quality=_optional_dict(args.get("quality")),
            metadata=_optional_dict(args.get("metadata")),
            notes=_optional_text(args.get("notes")),
        )
        return _registration_result(workspace, registry, artifact)
    @tool(
        "register_cell_qc_artifact",
        "Register cell-level QC metadata. This is eligibility evidence, not effect evidence.",
        {
            "path": str,
            "n_cells_after_qc": int,
            "qc_policy": str,
            "doublet_policy": str,
            "ambient_policy": str,
            "batch_qc": dict,
            "passed": bool,
            "scope": dict,
            "quality": dict,
            "metadata": dict,
            "notes": str,
        },
    )
    async def register_cell_qc_artifact(args: dict[str, Any]) -> dict[str, Any]:
        path = _resolve_evidence_source_path(workspace, str(args.get("path") or ""))
        artifact = registry.register_cell_qc(
            path=str(path.relative_to(workspace.root)),
            n_cells_after_qc=_optional_int(args.get("n_cells_after_qc")),
            qc_policy=_optional_text(args.get("qc_policy")),
            doublet_policy=_optional_text(args.get("doublet_policy")),
            ambient_policy=_optional_text(args.get("ambient_policy")),
            batch_qc=_optional_dict(args.get("batch_qc")),
            passed=args.get("passed") if isinstance(args.get("passed"), bool) else None,
            scope=_optional_dict(args.get("scope")),
            quality=_optional_dict(args.get("quality")),
            metadata=_optional_dict(args.get("metadata")),
            notes=_optional_text(args.get("notes")),
        )
        return _registration_result(workspace, registry, artifact)
    @tool(
        "register_control_calibration_artifact",
        "Register control-calibration eligibility metadata such as NTC-vs-NTC and label-permutation null checks. This is eligibility evidence, not effect evidence.",
        {
            "path": str,
            "calibration_type": str,
            "scope": dict,
            "negative_control_status": str,
            "ntc_vs_ntc_check": dict,
            "label_permutation_check": dict,
            "alpha": float,
            "n_features_tested": int,
            "n_significant": int,
            "method": str,
            "execution_hash": str,
            "quality": dict,
            "metadata": dict,
            "notes": str,
            "code_sha256": str,
        },
    )
    async def register_control_calibration_artifact(args: dict[str, Any]) -> dict[str, Any]:
        path = _resolve_evidence_source_path(workspace, str(args.get("path") or ""))
        artifact = registry.register_control_calibration(
            path=str(path.relative_to(workspace.root)),
            calibration_type=_optional_text(args.get("calibration_type")),
            scope=_optional_dict(args.get("scope")),
            negative_control_status=_optional_text(args.get("negative_control_status")),
            ntc_vs_ntc_check=_optional_dict(args.get("ntc_vs_ntc_check")),
            label_permutation_check=_optional_dict(args.get("label_permutation_check")),
            alpha=_optional_float(args.get("alpha")),
            n_features_tested=_optional_int(args.get("n_features_tested")),
            n_significant=_optional_int(args.get("n_significant")),
            method=_optional_text(args.get("method")),
            execution_hash=_optional_text(args.get("execution_hash")),
            quality=_optional_dict(args.get("quality")),
            metadata=_optional_dict(args.get("metadata")),
            notes=_optional_text(args.get("notes")),
            code_sha256=_optional_text(args.get("code_sha256")),
        )
        return _registration_result(workspace, registry, artifact)

    @tool(
        "register_replication_artifact",
        "Register a conservative replication summary from already-registered measured artifact IDs only.",
        {
            "measured_artifact_ids": list,
            "replication_type": str,
            "notes": str,
        },
    )
    async def register_replication_artifact(args: dict[str, Any]) -> dict[str, Any]:
        artifact = registry.register_replication(
            measured_artifact_ids=[str(item) for item in args.get("measured_artifact_ids") or []],
            replication_type=_optional_text(args.get("replication_type")),
            notes=_optional_text(args.get("notes")),
        )
        return _registration_result(workspace, registry, artifact)

    @tool(
        "route_analysis_method",
        "Route explicit Perturb-seq design facts to a conservative analysis family. This does not execute analysis or raise claim strength.",
        {"objective": str, "design": dict},
    )
    async def route_analysis_method(args: dict[str, Any]) -> dict[str, Any]:
        return route_analysis(_optional_dict(args.get("design")), objective=str(args.get("objective") or "measured_effect"))

    @tool(
        "run_target_reliability_audit",
        "Run the trusted per-target reliability diagnostic over explicit metadata and a wide target-expression CSV.",
        {
            "expression_csv": str, "metadata_csv": str, "target_uid": str,
            "control_uid": str, "target": str, "target_gene": str, "layer": str,
            "output_path": str, "cell_id_column": str, "condition_column": str,
            "guide_column": str, "batch_column": str, "replicate_column": str,
            "expected_direction": str, "layer_scale": str, "perturbation_modality": str,
            "minimum_cells": int, "minimum_guides": int,
        },
    )
    async def run_target_reliability_audit_tool(args: dict[str, Any]) -> dict[str, Any]:
        return run_target_reliability(
            workspace.root,
            expression_csv=str(args.get("expression_csv") or ""),
            metadata_csv=str(args.get("metadata_csv") or ""),
            target_uid=str(args.get("target_uid") or ""),
            control_uid=str(args.get("control_uid") or ""),
            target=str(args.get("target") or ""),
            target_gene=str(args.get("target_gene") or ""),
            layer=str(args.get("layer") or ""),
            output_path=_optional_text(args.get("output_path")),
            cell_id_column=_optional_text(args.get("cell_id_column")) or "cell_id",
            condition_column=_optional_text(args.get("condition_column")) or "perturbation_uid",
            guide_column=_optional_text(args.get("guide_column")),
            batch_column=_optional_text(args.get("batch_column")),
            replicate_column=_optional_text(args.get("replicate_column")),
            expected_direction=_optional_text(args.get("expected_direction")) or "down",
            layer_scale=_optional_text(args.get("layer_scale")) or "log_normalized",
            perturbation_modality=_optional_text(args.get("perturbation_modality")) or "crispri",
            minimum_cells=_optional_int(args.get("minimum_cells")) or 20,
            minimum_guides=_optional_int(args.get("minimum_guides")) or 2,
        )

    @tool(
        "run_pseudobulk_de",
        "Run the trusted narrow pseudobulk DE runner for an explicit registered contrast. Register its output separately as measured evidence.",
        {
            "expression_csv": str, "metadata_csv": str, "contrast_uid": str,
            "left_uid": str, "baseline_uid": str, "replicate_column": str,
            "layer": str, "output_path": str, "cell_id_column": str,
            "condition_column": str, "gene_columns": list,
        },
    )
    async def run_pseudobulk_de(args: dict[str, Any]) -> dict[str, Any]:
        return run_pseudobulk_de_for_registered_contrast(
            workspace.root,
            expression_csv=str(args.get("expression_csv") or ""),
            metadata_csv=str(args.get("metadata_csv") or ""),
            contrast_uid=str(args.get("contrast_uid") or ""),
            left_uid=str(args.get("left_uid") or ""),
            baseline_uid=str(args.get("baseline_uid") or ""),
            replicate_column=str(args.get("replicate_column") or ""),
            layer=str(args.get("layer") or ""),
            output_path=_optional_text(args.get("output_path")),
            cell_id_column=_optional_text(args.get("cell_id_column")) or "cell_id",
            condition_column=_optional_text(args.get("condition_column")) or "perturbation_uid",
            gene_columns=[str(item) for item in args.get("gene_columns") or []] or None,
        )

    @tool(
        "run_ntc_control_calibration",
        "Run trusted NTC-vs-NTC null calibration and record it in the canonical execution ledger.",
        {"expression_csv": str, "metadata_csv": str, "control_uid": str, "layer": str, "condition_column": str, "gene_columns": list, "alpha": float, "seed": int},
    )
    async def run_ntc_control_calibration(args: dict[str, Any]) -> dict[str, Any]:
        return run_ntc_vs_ntc_calibration(
            workspace.root,
            expression_csv=str(args.get("expression_csv") or ""),
            metadata_csv=str(args.get("metadata_csv") or ""),
            control_uid=str(args.get("control_uid") or ""),
            layer=str(args.get("layer") or ""),
            condition_column=_optional_text(args.get("condition_column")) or "perturbation_uid",
            gene_columns=[str(item) for item in args.get("gene_columns") or []] or None,
            alpha=float(args.get("alpha") or 0.05),
            seed=int(args.get("seed") or 0),
        )

    @tool(
        "run_label_permutation_calibration",
        "Run trusted label-permutation null calibration and record it in the canonical execution ledger.",
        {"expression_csv": str, "metadata_csv": str, "contrast_uid": str, "left_uid": str, "baseline_uid": str, "layer": str, "condition_column": str, "gene_columns": list, "alpha": float, "seed": int},
    )
    async def run_label_permutation_calibration(args: dict[str, Any]) -> dict[str, Any]:
        return run_label_permutation_null(
            workspace.root,
            expression_csv=str(args.get("expression_csv") or ""),
            metadata_csv=str(args.get("metadata_csv") or ""),
            contrast_uid=str(args.get("contrast_uid") or ""),
            left_uid=str(args.get("left_uid") or ""),
            baseline_uid=str(args.get("baseline_uid") or ""),
            layer=str(args.get("layer") or ""),
            condition_column=_optional_text(args.get("condition_column")) or "perturbation_uid",
            gene_columns=[str(item) for item in args.get("gene_columns") or []] or None,
            alpha=float(args.get("alpha") or 0.05),
            seed=int(args.get("seed") or 0),
        )

    @tool(
        "evaluate_claims",
        "Evaluate explicit scientific claims against the registered evidence registry and return ClaimDecision objects.",
        {
            "claims": list,
            "claims_json_path": str,
        },
    )
    async def evaluate_claims(args: dict[str, Any]) -> dict[str, Any]:
        claims = _load_claims(workspace, args)
        _reject_policy_override(args)
        decisions = resolve_claims(claims, registry, policy=bound_policy)
        decisions_payload = [decision.to_dict() for decision in decisions]
        output_path = _write_claim_decisions(workspace, decisions_payload, "claim_decisions.json")
        return {
            "success": True,
            "decisions": decisions_payload,
            "decisions_path": str(output_path.relative_to(workspace.root)) if output_path else None,
            "policy_hash": decisions_payload[0]["policy_hash"] if decisions_payload else None,
            "policy_profile": bound_policy.profile,
        }

    @tool(
        "render_evidence_report",
        "Render a user-visible evidence-calibrated report from registered artifacts and optional explicit claims.",
        {
            "artifact_ids": list,
            "claims": list,
            "claims_json_path": str,
            "title": str,
            "report_filename": str,
        },
    )
    async def render_report(args: dict[str, Any]) -> dict[str, Any]:
        report_filename = str(args.get("report_filename") or "evidence_report.md")
        report_path = _resolve_report_path(workspace, report_filename)
        artifact_ids = [str(item) for item in args.get("artifact_ids") or []]
        claims = _load_claims(workspace, args, required=False)
        _reject_policy_override(args)
        report = render_evidence_report(
            registry=registry,
            artifact_ids=artifact_ids or None,
            claims=claims or None,
            title=str(args.get("title") or "Pertura Evidence Report"),
            write_path=report_path,
            policy=bound_policy,
        )
        decisions_payload = [decision.to_dict() for decision in report.decisions]
        decisions_path = _write_claim_decisions(workspace, decisions_payload, "claim_decisions.json") if decisions_payload else None
        return {
            "success": True,
            "report_path": str(report_path.relative_to(workspace.root)),
            "decisions_path": str(decisions_path.relative_to(workspace.root)) if decisions_path else None,
            "resolutions": [resolution.to_dict() for resolution in report.resolutions],
            "decisions": decisions_payload,
            "markdown_preview": report.markdown[:2000],
            "policy_profile": bound_policy.profile,
            "policy_hash": bound_policy.policy_hash,
            "next_step": "Use reports/evidence_report.md as the scientific surface. Do not re-evaluate claims unless the claims or registry change.",
        }

    return create_sdk_mcp_server(
        name="pertura_evidence",
        version="0.2.0",
        tools=[
            register_perturbation_design_manifest,
            register_experiment_design_artifact,
            register_guide_assignment_artifact,
            register_target_qc_artifact,
            register_measured_de_artifact,
            register_predicted_effect_artifact,
            register_virtual_perturbation_prediction_artifact,
            register_prediction_measured_concordance_artifact,
            register_virtual_cell_state_transition_artifact,
            register_curated_prior_artifact,
            register_perturbation_efficiency_artifact,
            register_curated_enrichment_artifact,
            register_module_effect_artifact,
            register_global_effect_artifact,
            register_composition_effect_artifact,
            register_cell_state_reference_artifact,
            register_cell_qc_artifact,
            register_control_calibration_artifact,
            register_replication_artifact,
            route_analysis_method,
            run_target_reliability_audit_tool,
            run_pseudobulk_de,
            run_ntc_control_calibration,
            run_label_permutation_calibration,
            evaluate_claims,
            render_report,
        ],
    )


def _reject_policy_override(args: dict[str, Any]) -> None:
    if args.get("policy_profile") not in (None, ""):
        raise ValueError(
            "policy_profile is runtime-owned and cannot be selected by an MCP tool call"
        )


def _write_registration_handoff(workspace: ClaudeRunWorkspace, payload: dict[str, Any]) -> None:
    workspace.artifacts_dir.mkdir(parents=True, exist_ok=True)
    latest_path = workspace.artifacts_dir / "latest_registration.json"
    latest_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    log_path = workspace.artifacts_dir / "registration_handoffs.jsonl"
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
    if payload.get("next_claim_template") is not None:
        claimable_path = workspace.artifacts_dir / "claimable_artifacts.json"
        existing: list[dict[str, Any]] = []
        if claimable_path.exists():
            loaded = json.loads(claimable_path.read_text(encoding="utf-8"))
            existing = list(loaded.get("artifacts") or []) if isinstance(loaded, dict) else []
        compact = {
            "artifact_id": payload.get("artifact_id"),
            "artifact_path": payload.get("artifact_path"),
            "evidence_class": payload.get("evidence_class"),
            "evidence_predicate": payload.get("evidence_predicate"),
            "artifact_intrinsic_ceiling": payload.get("artifact_intrinsic_ceiling"),
            "next_claim_template": payload.get("next_claim_template"),
        }
        updated = [item for item in existing if item.get("artifact_id") != compact["artifact_id"]]
        updated.append(compact)
        claimable_path.write_text(json.dumps({"artifacts": updated}, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_claim_decisions(workspace: ClaudeRunWorkspace, decisions_payload: list[dict[str, Any]], filename: str = "claim_decisions.json") -> Path:
    output_path = _resolve_artifact_output_path(workspace, filename)
    output_path.write_text(json.dumps({"decisions": decisions_payload}, ensure_ascii=False, indent=2), encoding="utf-8")
    return output_path
def _registration_result(workspace: ClaudeRunWorkspace, registry: EvidenceRegistry, artifact) -> dict[str, Any]:
    intrinsic = resolve_artifact_strength(artifact)
    claim_template = _next_claim_template(artifact)
    claim_usage = (
        "direct_evidence_ref" if claim_template is not None
        else "scope_or_eligibility_only; do not put this artifact_id in evidence_refs for effect claims"
    )
    payload = {
        "success": True,
        "artifact_id": artifact.artifact_id,
        "artifact_path": artifact.path,
        "evidence_class": artifact.effective_evidence_class.value,
        "evidence_predicate": artifact.effective_evidence_predicate.value,
        "artifact_roles": [item.value if hasattr(item, "value") else str(item) for item in artifact.artifact_roles],
        "artifact_intrinsic_ceiling": intrinsic.ceiling.value,
        "artifact": artifact.to_dict(),
        "next_claim_template": claim_template,
        "claim_template_policy": "Bookkeeping only: copy scope and evidence_refs; fill claim_id/text/subject/object/requested_strength yourself. This template does not suggest claim strength.",
        "claim_usage": claim_usage,
        "registry_path": str(registry.path.relative_to(registry.run_root)),
        "handoff_path": "artifacts/latest_registration.json",
        "handoff_log_path": "artifacts/registration_handoffs.jsonl",
        "claimable_artifacts_path": "artifacts/claimable_artifacts.json" if claim_template is not None else None,
        "next_step": "If this tool result is visible, copy next_claim_template.scope and next_claim_template.evidence_refs into explicit claims when present. If the tool result is not visible, read artifacts/claimable_artifacts.json for claimable evidence or artifacts/latest_registration.json for the most recent registration. Then call render_evidence_report with explicit claims; it will write artifacts/claim_decisions.json.",
    }
    _write_registration_handoff(workspace, payload)
    return payload


def _next_claim_template(artifact) -> dict[str, Any] | None:
    claimable_kinds = {
        "measured_de",
        "perturbation_efficiency",
        "predicted_effect",
        "virtual_perturbation_prediction",
        "prediction_measured_concordance",
        "virtual_cell_state_transition",
        "curated_prior_lookup",
        "curated_enrichment_result",
        "module_effect",
        "global_effect",
        "composition_effect",
        "replication_summary",
    }
    kind = artifact.kind.value if hasattr(artifact.kind, "value") else str(artifact.kind)
    if kind not in claimable_kinds:
        return None
    return {
        "scope": dict(artifact.scope or {}),
        "evidence_refs": [artifact.artifact_id],
    }


def _load_claims(workspace: ClaudeRunWorkspace, args: dict[str, Any], *, required: bool = True) -> list[dict[str, Any]]:
    claims: list[dict[str, Any]] = _coerce_claim_items(args.get("claims"), field_name="claims")
    raw_path = _optional_text(args.get("claims_json_path"))
    if raw_path:
        path = _resolve_claims_source_path(workspace, raw_path)
        payload = json.loads(path.read_text(encoding="utf-8"))
        loaded = payload.get("claims") if isinstance(payload, dict) and "claims" in payload else payload
        claims.extend(_coerce_claim_items(loaded, field_name="claims_json_path"))
    if required and not claims:
        raise ValueError("claims or claims_json_path is required")
    return _dedupe_claims(claims)


def _coerce_claim_items(value: Any, *, field_name: str) -> list[dict[str, Any]]:
    if value in (None, ""):
        return []
    if isinstance(value, dict):
        if "claims" in value and isinstance(value.get("claims"), list):
            return _coerce_claim_items(value.get("claims"), field_name=field_name)
        return [dict(value)]
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{field_name} must be a JSON object or an array of JSON objects") from exc
        return _coerce_claim_items(parsed, field_name=field_name)
    if isinstance(value, list):
        claims: list[dict[str, Any]] = []
        for index, item in enumerate(value):
            if isinstance(item, dict):
                claims.append(dict(item))
                continue
            if isinstance(item, str):
                claims.extend(_coerce_claim_items(item, field_name=f"{field_name}[{index}]"))
                continue
            raise ValueError(f"{field_name}[{index}] must be a JSON object, not {type(item).__name__}")
        return claims
    raise ValueError(f"{field_name} must be a JSON object or an array of JSON objects, not {type(value).__name__}")


def _dedupe_claims(claims: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, claim in enumerate(claims):
        claim_id = str(claim.get("claim_id") or "").strip()
        key = claim_id or json.dumps(claim, sort_keys=True, ensure_ascii=True, default=str)
        if key in seen:
            continue
        seen.add(key)
        if not claim_id:
            claim = dict(claim)
            claim["claim_id"] = f"claim_{index + 1}"
        deduped.append(claim)
    return deduped
def _resolve_evidence_source_path(workspace: ClaudeRunWorkspace, raw_path: str) -> Path:
    if not raw_path.strip():
        raise ValueError("path is required")
    path = Path(raw_path)
    if not path.is_absolute():
        path = workspace.root / path
    resolved = path.resolve()
    allowed_roots = [workspace.outputs_dir.resolve(), workspace.artifacts_dir.resolve()]
    if not any(_is_relative_to(resolved, root) for root in allowed_roots):
        raise ValueError("evidence artifacts must be under outputs/ or artifacts/; reports/ cannot be registered as evidence")
    if not resolved.exists() or not resolved.is_file():
        raise ValueError(f"evidence artifact file does not exist: {resolved}")
    return resolved


def _resolve_claims_source_path(workspace: ClaudeRunWorkspace, raw_path: str) -> Path:
    path = _resolve_evidence_source_path(workspace, raw_path)
    if path.suffix.lower() not in {".json", ".jsonl"}:
        raise ValueError("claims_json_path must point to a JSON or JSONL file under outputs/ or artifacts/")
    return path


def _resolve_artifact_output_path(workspace: ClaudeRunWorkspace, filename: str) -> Path:
    name = filename.strip() or "claim_decisions.json"
    path = Path(name)
    if not path.is_absolute() and path.parts and path.parts[0].lower() == "artifacts":
        path = Path(*path.parts[1:]) if len(path.parts) > 1 else Path("claim_decisions.json")
    resolved = path.resolve() if path.is_absolute() else (workspace.artifacts_dir / path).resolve()
    if not _is_relative_to(resolved, workspace.artifacts_dir.resolve()):
        raise ValueError("claim decisions must be written under artifacts/")
    resolved.parent.mkdir(parents=True, exist_ok=True)
    return resolved


def _resolve_report_path(workspace: ClaudeRunWorkspace, filename: str) -> Path:
    name = filename.strip() or "evidence_report.md"
    path = Path(name)
    if not path.is_absolute() and path.parts and path.parts[0].lower() == "reports":
        path = Path(*path.parts[1:]) if len(path.parts) > 1 else Path("evidence_report.md")
    if path.is_absolute():
        resolved = path.resolve()
    else:
        resolved = (workspace.reports_dir / path).resolve()
    if not _is_relative_to(resolved, workspace.reports_dir.resolve()):
        raise ValueError("evidence reports must be written under reports/")
    return resolved


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _optional_text(value: Any) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    return int(value)



def _optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    return float(value)
def _optional_dict(value: Any) -> dict:
    return dict(value or {}) if isinstance(value, dict) else {}


def _optional_list(value: Any) -> list:
    return list(value or []) if isinstance(value, list) else []






