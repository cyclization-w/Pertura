from __future__ import annotations

import csv
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pertura_core import CapabilitySpec, DatasetContract, ResultEnvelope, ScopeKey
from pertura_runtime.invocation_bindings import build_invocation_binding
from pertura_runtime.project.assets import DataAssetRegistry
from pertura_runtime.project.models import (
    AssetBinding,
    CapabilityInvocationBinding,
    DataAssetRef,
)
from pertura_runtime.project.workspace import ProjectWorkspace
from pertura_workflow.capabilities.registry import CapabilityRegistry
from pertura_workflow.environment import environment_lock
from pertura_workflow.knowledge_resources import knowledge_resource_lock
from pertura_workflow.planner import resolve_dependencies


_ROLE_ALIASES = {
    "primary_h5ad": "primary_dataset",
    "construct_metadata": "cell_metadata",
    "donor_metadata": "cell_metadata",
    "target_expression": "expression_table",
    "trans_de_results": "effect_table",
    "frozen_gene_sets": "gene_modules",
}


class _BindingInputUnavailable(ValueError):
    """A task-scoped input was not produced or registered for this turn."""


def build_paper_task_invocation_bindings(
    *,
    run_id: str,
    task: Mapping[str, Any],
    dataset_id: str,
    contract: DatasetContract,
    registry: CapabilityRegistry,
    asset_registry: DataAssetRegistry,
    project: ProjectWorkspace,
    registered_assets: Mapping[str, Mapping[str, Any]],
    committed_results: tuple[ResultEnvelope, ...],
    advertised_capability_ids: tuple[str, ...],
    committed_records: tuple[Mapping[str, Any], ...] = (),
    verify_runtime_dependencies: bool = False,
) -> tuple[CapabilityInvocationBinding, ...]:
    """Compile task-scoped, provider-callable bindings without executing them."""

    task_id = str(task["task_id"])
    candidates = tuple(str(item) for item in advertised_capability_ids)
    assets = dict(registered_assets)
    for source_role, target_role in _ROLE_ALIASES.items():
        if source_role in assets and target_role not in assets:
            _ensure_role_alias(
                source_role=source_role,
                target_role=target_role,
                assets=assets,
                asset_registry=asset_registry,
                project=project,
                run_id=run_id,
            )
    bindings: list[CapabilityInvocationBinding] = []
    for capability_id in candidates:
        spec = registry.get(capability_id)
        if verify_runtime_dependencies:
            _verify_runtime_dependencies(spec)
        try:
            parameters, additional_roles, blockers, allowed_overrides = _recipe(
                task_id=task_id,
                dataset_id=dataset_id,
                capability_id=capability_id,
                contract=contract,
                assets=assets,
                asset_registry=asset_registry,
                project=project,
                run_id=run_id,
            )
        except _BindingInputUnavailable as exc:
            parameters = {}
            additional_roles = ()
            blockers = [
                "required bound asset is unavailable at execution time: " f"{exc}"
            ]
            allowed_overrides = ()
        properties = dict((spec.parameters_schema or {}).get("properties") or {})
        for name, field in properties.items():
            if not isinstance(field, Mapping) or not field.get("x-pertura-asset-role"):
                continue
            expected_role = str(field["x-pertura-asset-role"])
            if name in parameters:
                continue
            source_role = next(
                (
                    role
                    for role, alias in _ROLE_ALIASES.items()
                    if alias == expected_role and role in assets
                ),
                expected_role if expected_role in assets else None,
            )
            if source_role is None:
                if name in set(spec.parameters_schema.get("required") or ()):
                    blockers.append(
                        f"required asset role is unavailable: {expected_role}"
                    )
                continue
            record = _ensure_role_alias(
                source_role=source_role,
                target_role=expected_role,
                assets=assets,
                asset_registry=asset_registry,
                project=project,
                run_id=run_id,
            )
            parameters[name] = record["asset_id"]

        dependencies, missing_dependencies = _bound_dependencies(
            spec=spec,
            candidates=candidates,
            registry=registry,
            contract=contract,
            required_scope=ScopeKey(dataset_id=dataset_id),
            committed_results=committed_results,
            committed_records=committed_records,
        )
        blockers.extend(missing_dependencies)
        blockers.extend(
            f"required provenance artifact is unavailable: {role}"
            for role in additional_roles
            if role not in assets
        )
        resolved_capabilities = {item.capability_id for item in dependencies}
        dependency_binding_ids = tuple(
            binding.binding_id
            for dependency_id in spec.depends_on
            if dependency_id not in resolved_capabilities
            for binding in bindings
            if binding.capability_id == dependency_id
        )
        blocked_predecessors = [
            binding.capability_id
            for binding in bindings
            if binding.binding_id in dependency_binding_ids
            and binding.readiness == "blocked_probe"
        ]
        blockers.extend(
            "bound predecessor is unavailable: " + capability_id
            for capability_id in blocked_predecessors
        )
        additional_assets = tuple(
            _asset_ref(assets[role], project)
            for role in additional_roles
            if role in assets
        )
        probe = capability_id in set(task.get("expected_probe_capabilities") or ())
        if probe:
            blockers = blockers or [
                "the frozen task contract requires an applicability probe"
            ]
            readiness = "blocked_probe"
        elif blockers:
            readiness = "blocked_probe"
        elif any(
            dep in candidates
            for dep in spec.depends_on
            if dep not in {item.capability_id for item in dependencies}
        ):
            readiness = "conditional_ready"
        else:
            readiness = "ready"

        tool_name = {
            "diagnostic": "run_diagnostic",
            "analysis": "run_analysis",
            "virtual": "evaluate_virtual_model",
        }[spec.kind]
        output_mapping = _output_mapping(task_id, spec)
        task_artifact_roles = set(
            str(role)
            for role in (
                (task.get("output_contract") or {}).get("artifact_paths") or {}
            )
        )
        invalid_output_roles = sorted(
            set(output_mapping.values()) - task_artifact_roles
        )
        if invalid_output_roles:
            raise ValueError(
                f"{task_id}: capability output mapping targets unknown artifact "
                f"roles for {spec.capability_id}: {invalid_output_roles}"
            )
        if readiness != "blocked_probe" and not output_mapping:
            raise ValueError(
                f"{task_id}: capability output is not mapped into the task contract: "
                f"{spec.capability_id}"
            )
        bindings.append(
            build_invocation_binding(
                run_id=run_id,
                task_id=task_id,
                spec=spec,
                contract=contract,
                tool_name=tool_name,
                scope={"dataset_id": dataset_id},
                bound_parameters=parameters,
                project=project,
                allowed_overrides=allowed_overrides,
                dependency_results=dependencies,
                dependency_records={
                    str((item.get("result") or {}).get("result_id")): item
                    for item in committed_records
                },
                dependency_binding_ids=dependency_binding_ids,
                additional_assets=additional_assets,
                output_mapping=output_mapping,
                readiness=readiness,
                blockers=tuple(dict.fromkeys(blockers))
                if readiness == "blocked_probe"
                else (),
            )
        )
    return tuple(bindings)


def provider_binding_contract(
    bindings: tuple[CapabilityInvocationBinding, ...],
) -> list[dict[str, Any]]:
    records = []
    for binding in bindings:
        arguments = minimal_binding_arguments(binding)
        records.append(
            {
                "binding_id": binding.binding_id,
                "capability_id": binding.capability_id,
                "tool": binding.tool_name,
                "readiness": binding.readiness,
                "blockers": list(binding.blockers),
                "allowed_overrides": list(binding.allowed_overrides),
                "output_mapping": dict(binding.output_mapping),
                "minimal_call": {"tool": binding.tool_name, "arguments": arguments},
            }
        )
    return records


def minimal_binding_arguments(
    binding: CapabilityInvocationBinding,
    *,
    objective_prefix: str = "Execute",
) -> dict[str, Any]:
    """Return the exact provider-visible invocation for one frozen binding."""

    arguments = {"binding_id": binding.binding_id}
    if binding.tool_name == "run_analysis":
        arguments[
            "objective"
        ] = f"{objective_prefix} {binding.capability_id} under the frozen task binding"
    return arguments


def _verify_runtime_dependencies(spec: CapabilitySpec) -> None:
    """Reject a ready surface whose frozen environment/resource is unavailable."""

    environment_profile = str(spec.metadata.get("environment_profile") or "")
    if environment_profile:
        try:
            environment_lock(environment_profile)
        except RuntimeError as exc:
            raise ValueError(
                f"{spec.capability_id}: frozen environment is unavailable: "
                f"{environment_profile}"
            ) from exc
    resource_profile = str(spec.metadata.get("resource_profile") or "")
    if resource_profile:
        try:
            knowledge_resource_lock(resource_profile)
        except RuntimeError as exc:
            raise ValueError(
                f"{spec.capability_id}: frozen knowledge resource is unavailable: "
                f"{resource_profile}"
            ) from exc


def _recipe(
    *,
    task_id: str,
    dataset_id: str,
    capability_id: str,
    contract: DatasetContract,
    assets: dict[str, Mapping[str, Any]],
    asset_registry: DataAssetRegistry,
    project: ProjectWorkspace,
    run_id: str,
) -> tuple[dict[str, Any], tuple[str, ...], list[str], tuple[str, ...]]:
    parameters: dict[str, Any] = {"max_memory_gb": 4.0, "n_jobs": 1}
    additional: list[str] = []
    blockers: list[str] = []
    overrides: tuple[str, ...] = ()

    if capability_id == "diagnostic.contract_integrity.v1":
        return parameters, (), blockers, overrides
    if capability_id == "diagnostic.dataset_integrity.v1":
        parameters["input_path"] = _asset_id(assets, "primary_dataset")
        return parameters, (), blockers, overrides
    if capability_id == "diagnostic.design_balance.v1":
        parameters["metadata_path"] = _asset_id(assets, "cell_metadata")
        if dataset_id == "kang18_8vs8_pbmc":
            parameters.update(
                condition_column="stim",
                replicate_column="ind",
                donor_column="ind",
                state_column="cell",
                paired=True,
            )
        # For REPL/NORM the independent-unit facts are intentionally unresolved.
        # Execute the diagnostic with its documented defaults so the capability,
        # rather than the binding compiler, returns the structured design block.
        return parameters, (), blockers, overrides
    if capability_id == "state.reference.fit.v1":
        control_fact = _confirmed_fact_value(contract, "control")
        parameters.update(
            h5ad_path=_asset_id(assets, "primary_dataset"),
            selection_path=_task_alias(
                assets,
                "calibration_split",
                "cell_selection",
                asset_registry,
                project,
                run_id,
            ),
            control_column=str(control_fact["primary_h5ad_column"]),
            control_values=[str(control_fact["primary_h5ad_label"])],
            resolutions=[0.5, 1.0, 1.5],
            seeds=[1729, 1730, 1731],
        )
        additional.append("retained_cell_manifest")
        return parameters, tuple(additional), blockers, overrides
    if capability_id == "state.reference.map_knn.v1":
        parameters.update(
            h5ad_path=_asset_id(assets, "primary_dataset"),
            selection_path=_task_alias(
                assets,
                "evaluation_split",
                "cell_selection",
                asset_registry,
                project,
                run_id,
            ),
            mapping_probability_threshold=0.60,
        )
        additional.append("retained_cell_manifest")
        return parameters, tuple(additional), blockers, overrides
    if capability_id == "target.guide_efficacy.v1":
        if task_id == "REPL-03":
            blockers.append(
                "cell-by-guide counts and a validated guide-level effect table are unavailable"
            )
            return {}, tuple(additional), blockers, overrides
        parameters.update(
            expression_path=_asset_id(assets, "expression_table"),
            metadata_path=_asset_id(assets, "cell_metadata"),
            cell_column="cell_id",
            guide_column="guide_ID",
            condition_column="gene",
            replicate_column="replicate",
            targets=_papa_targets(assets),
        )
        additional.append("retained_cell_manifest")
        return parameters, tuple(additional), blockers, overrides
    if capability_id == "effect.guide_target_sensitivity.v1":
        return parameters, (), blockers, overrides
    if capability_id == "target.responder.mixscape.v1":
        parameters.update(
            h5ad_path=_asset_id(assets, "primary_dataset"),
            pert_key="gene",
            control="NT",
            perturbation_type="KO",
            split_by="replicate",
        )
        additional.append("retained_cell_manifest")
        return parameters, tuple(additional), blockers, overrides
    if capability_id == "target.reliability.aggregate.v1":
        additional.append("retained_cell_manifest")
        return parameters, tuple(additional), blockers, overrides
    if capability_id == "association.sceptre.v1":
        blockers.append(
            "cell-by-guide counts, discovery pairs, and an independent-unit count model are unavailable"
        )
        return {}, (), blockers, overrides
    if capability_id == "effect.matrix.assemble.v1":
        parameters.update(
            effect_table_paths=[_asset_id(assets, "effect_table")],
            effect_scale="logFC",
            estimand="target_by_replicate_pseudobulk",
            min_perturbations=5,
            min_features=200,
        )
        return parameters, (), blockers, overrides
    if capability_id == "enrichment.ora.v1":
        # The effect matrix is a receipt-backed same-turn dependency. Gene
        # sets are an answer-free, provider-visible frozen task asset.
        parameters["gene_sets_path"] = _asset_id(assets, "gene_modules")
        return parameters, (), blockers, overrides
    if capability_id == "interpretation.evidence_map.v1":
        provenance_assets = [
            _asset_ref(record, project)
            for role, record in sorted(assets.items())
            if role not in {"primary_dataset", "primary_h5ad"}
        ]
        parameters["records"] = [
            {
                "role": (
                    "prior" if asset.source_class == "curated_prior" else "derived"
                ),
                "text": f"Registered task evidence artifact: {asset.role}",
                "artifact_ids": [asset.asset_id],
                "limitations": [
                    "artifact provenance does not create measured scientific authority"
                ],
            }
            for asset in provenance_assets
        ]
        if not parameters["records"]:
            blockers.append("no registered evidence artifacts are available")
        additional.extend(
            role for role in assets if role not in {"primary_dataset", "primary_h5ad"}
        )
        return parameters, tuple(dict.fromkeys(additional)), blockers, overrides
    if capability_id == "design.next_panel.v1":
        candidate_roles = [
            role
            for role in sorted(assets)
            if role not in {"primary_dataset", "primary_h5ad"}
        ]
        parameters.update(
            candidates=[
                {
                    "candidate_id": f"resolve_{role}",
                    "cost": 1.0,
                    "uncertainty": 1.0,
                    "information_gain": 0.75,
                    "program_coverage": 0.5,
                    "biological_diversity": 0.5,
                    "feasibility": 0.75,
                }
                for role in candidate_roles
            ],
            budget=float(max(1, min(3, len(candidate_roles)))),
        )
        if not candidate_roles:
            blockers.append("no evidence gap candidates are available")
        additional.extend(candidate_roles)
        return parameters, tuple(additional), blockers, ("weights",)
    if capability_id == "composition.propeller.v1":
        parameters.update(
            metadata_path=_asset_id(assets, "cell_metadata"),
            selection_path=_task_alias(
                assets,
                "evaluation_split",
                "cell_selection",
                asset_registry,
                project,
                run_id,
            ),
            cell_id_column="cell_id",
            selection_cell_id_column="cell_id",
            sample_column="ind",
            pairing_column="ind",
            state_column="cell",
            condition_column="stim",
            contrast=["ctrl", "stim"],
        )
        return parameters, (), blockers, overrides

    # Optional virtual chains retain a safe structured block unless every
    # method-specific input is explicitly configured by the checkpoint.
    blockers.append(f"no validated invocation recipe is configured for {capability_id}")
    return {}, (), blockers, overrides


def _bound_dependencies(
    *,
    spec: CapabilitySpec,
    candidates: tuple[str, ...],
    registry: CapabilityRegistry,
    contract: DatasetContract,
    required_scope: ScopeKey,
    committed_results: tuple[ResultEnvelope, ...],
    committed_records: tuple[Mapping[str, Any], ...],
) -> tuple[tuple[ResultEnvelope, ...], list[str]]:
    records_by_id = {
        str((item.get("result") or {}).get("result_id")): item
        for item in committed_records
    }
    current: list[ResultEnvelope] = []
    for item in committed_results:
        record = records_by_id.get(item.result_id) or {}
        state = str(
            record.get("verification_state")
            or item.metadata.get("verification_state")
            or ("trusted_receipt" if item.receipt_id else "")
        )
        if item.stale or state not in {"trusted_receipt", "validated_untrusted"}:
            continue
        if spec.trust_level.value == "builtin_trusted" and state != "trusted_receipt":
            continue
        current.append(item)
    selected: list[ResultEnvelope] = []
    missing: list[str] = []
    for dependency_id in spec.depends_on:
        matches = [item for item in current if item.capability_id == dependency_id]
        if matches:
            selected.append(matches[-1])
        elif dependency_id not in candidates:
            missing.append(
                f"required verified ancestor result is unavailable: {dependency_id}"
            )
    for group in spec.metadata.get("dependency_sets") or ():
        kinds = set(str(item) for item in group.get("result_kinds") or ())
        sources = set(str(item) for item in group.get("source_classes") or ())
        compatible = [
            item
            for item in current
            if (not kinds or item.result_kind in kinds)
            and (
                not sources
                or getattr(item.source_class, "value", str(item.source_class))
                in sources
            )
        ]
        minimum = int(group.get("min_count", 1))
        for item in compatible:
            if item not in selected:
                selected.append(item)
        if len(compatible) < minimum:
            producers = [
                candidate
                for candidate in candidates
                if (not kinds or registry.get(candidate).output_kind in kinds)
            ]
            if not producers:
                missing.append(
                    "dependency set "
                    f"{group.get('name', 'unnamed')} lacks {minimum} verified result(s)"
                )
    # Compile ancestor dependencies through the same resolver used at runtime.
    # Same-turn predecessors remain represented by dependency_binding_ids and
    # are intentionally absent from this ancestor-only validation spec.
    ancestor_dependencies = tuple(
        dependency_id
        for dependency_id in spec.depends_on
        if dependency_id not in candidates
    )
    if ancestor_dependencies:
        validation_spec = spec.model_copy(update={"depends_on": ancestor_dependencies})
        selected_ids = {item.result_id for item in selected}
        resolution = resolve_dependencies(
            validation_spec,
            contract=contract,
            required_scope=required_scope,
            committed_results=tuple(current),
            dependency_hints=tuple(
                {
                    "object_id": item.result_id,
                    "object_hash": item.canonical_hash,
                    "state": "current",
                }
                for item in selected
                if item.capability_id in ancestor_dependencies
            ),
            trusted_receipt_result_ids=tuple(
                item.result_id
                for item in current
                if (records_by_id.get(item.result_id) or {}).get("verification_state")
                == "trusted_receipt"
            ),
            registry=registry,
        )
        if not resolution.ok:
            missing.extend(resolution.blockers)
        resolved_ids = {
            dependency.object_id
            for dependency in resolution.dependencies
            if dependency.object_id in selected_ids
        }
        selected = [
            item
            for item in selected
            if item.capability_id not in ancestor_dependencies
            or item.result_id in resolved_ids
        ]
    return tuple(selected), list(dict.fromkeys(missing))


def _ensure_role_alias(
    *,
    source_role: str,
    target_role: str,
    assets: dict[str, Mapping[str, Any]],
    asset_registry: DataAssetRegistry,
    project: ProjectWorkspace,
    run_id: str,
) -> Mapping[str, Any]:
    if target_role in assets:
        return assets[target_role]
    if source_role not in assets:
        raise _BindingInputUnavailable(source_role)
    source_record = assets[source_role]
    source = _asset_ref(source_record, project)
    alias = asset_registry.register(
        Path(str(source_record["path"])),
        role=target_role,
        kind=source.kind,
        source_class=source.source_class,
        created_by_turn=source.created_by_turn,
        dependencies=source.dependencies,
        origin_task_id=source.origin_task_id,
        submission_id=source.submission_id,
        schema_validation_status=source.schema_validation_status,
    )
    project.store.put_asset_binding(
        AssetBinding(run_id=run_id, asset_id=alias.asset_id, role=alias.role)
    )
    record = {
        "asset_id": alias.asset_id,
        "path": str(asset_registry.resolve(alias.asset_id, expected_role=target_role)),
        "content_sha256": alias.content_sha256,
        "kind": str(alias.kind),
        "source_class": str(alias.source_class),
    }
    assets[target_role] = record
    return record


def _task_alias(
    assets: dict[str, Mapping[str, Any]],
    source_role: str,
    target_role: str,
    asset_registry: DataAssetRegistry,
    project: ProjectWorkspace,
    run_id: str,
) -> str:
    return str(
        _ensure_role_alias(
            source_role=source_role,
            target_role=target_role,
            assets=assets,
            asset_registry=asset_registry,
            project=project,
            run_id=run_id,
        )["asset_id"]
    )


def _asset_id(assets: Mapping[str, Mapping[str, Any]], role: str) -> str:
    try:
        return str(assets[role]["asset_id"])
    except KeyError as exc:
        raise _BindingInputUnavailable(role) from exc


def _confirmed_fact_value(contract: DatasetContract, field: str) -> dict[str, Any]:
    fact = dict(contract.identity_fields.get(field) or {})
    if str(fact.get("status") or "") != "confirmed":
        raise ValueError(f"required frozen design fact is unresolved: {field}")
    value = fact.get("value")
    if not isinstance(value, Mapping):
        raise ValueError(f"required frozen design fact is not structured: {field}")
    return dict(value)


def _asset_ref(record: Mapping[str, Any], project: ProjectWorkspace) -> DataAssetRef:
    asset = project.store.get_asset(str(record["asset_id"]))
    if asset is None:
        raise ValueError(f"registered paper asset is missing: {record['asset_id']}")
    return asset


def _papa_targets(assets: Mapping[str, Mapping[str, Any]]) -> list[dict[str, str]]:
    if "expression_table" not in assets:
        raise _BindingInputUnavailable("expression_table")
    path = Path(str(assets["expression_table"]["path"]))
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle, delimiter="\t")
        header = next(reader)
    genes = [name for name in header if name not in {"cell_id", "NT", "NTC"}]
    if not genes:
        raise ValueError("target expression table has no eligible target columns")
    return [
        {
            "target_uid": gene,
            "control_uid": "NT",
            "target_gene": gene,
            "expected_direction": "down",
        }
        for gene in genes
    ]


def _output_mapping(task_id: str, spec: CapabilitySpec) -> dict[str, str]:
    overrides = {
        ("REPL-01", "diagnostic.contract_integrity.v1"): "design_audit",
        ("REPL-01", "diagnostic.dataset_integrity.v1"): "dataset_profile",
        ("REPL-01", "diagnostic.design_balance.v1"): "supported_analysis_plan",
        ("NORM-01", "diagnostic.contract_integrity.v1"): "construct_design_audit",
        ("NORM-01", "diagnostic.dataset_integrity.v1"): "dataset_profile",
        ("NORM-01", "diagnostic.design_balance.v1"): "supported_analysis_plan",
        ("PAPA-02", "state.reference.fit.v1"): "state_reference_model",
        ("PAPA-03", "state.reference.map_knn.v1"): "state_mapping",
        ("PAPA-04", "target.guide_efficacy.v1"): "target_efficacy",
        ("PAPA-04", "effect.guide_target_sensitivity.v1"): "guide_sensitivity",
        ("PAPA-05", "target.responder.mixscape.v1"): "mixscape_cells",
        ("PAPA-05", "target.reliability.aggregate.v1"): "target_reliability",
        ("REPL-04", "interpretation.evidence_map.v1"): "evidence_map",
        ("REPL-04", "design.next_panel.v1"): "next_panel",
        ("PAPA-08", "effect.matrix.assemble.v1"): "checkpoint_synthesis",
        ("PAPA-08", "enrichment.ora.v1"): "checkpoint_synthesis",
        ("PAPA-08", "interpretation.evidence_map.v1"): "evidence_map",
        ("PAPA-08", "design.next_panel.v1"): "next_panel",
        ("NORM-04", "interpretation.evidence_map.v1"): "stability_summary",
        ("NORM-05", "interpretation.evidence_map.v1"): "evidence_map",
        ("NORM-06", "interpretation.evidence_map.v1"): "missing_evidence_map",
        ("NORM-06", "design.next_panel.v1"): "next_experiment",
        ("VIRT-01", "virtual.split.contract.v1"): "leakage_audit",
        ("VIRT-01", "virtual.evaluate.v1"): "prediction_metrics",
        ("KANG-02", "diagnostic.design_balance.v1"): "composition_input_accounting",
        ("KANG-02", "composition.propeller.v1"): "propeller_results",
    }
    role = overrides.get((task_id, spec.capability_id))
    return {spec.output_kind: role} if role else {}
