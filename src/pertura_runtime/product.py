from __future__ import annotations

import json
import os
import hashlib
from collections.abc import Mapping
from dataclasses import asdict
from pathlib import Path
from typing import Any

from pertura_core.hashing import canonical_hash
from pertura_core import CapabilityRunRequest, CapabilitySpec, DatasetContract, DependencyRef, DesignConfirmation, PromotionPolicy, ResultEnvelope, RunReceipt, ScientificStatement, ScopeKey, SourceClass, decide_promotion
from pertura_runtime.claude.workspace import ClaudeRunWorkspace
from pertura_runtime.network_policy import NetworkAccessPolicy
from pertura_runtime.invocation_bindings import (
    CapabilityInvocationBindingError,
    binding_dependency_hints,
)
from pertura_runtime.parameter_protocol import (
    CapabilityParameterError,
    validate_and_resolve_parameters,
)
from pertura_runtime.project.assets import DataAssetRegistry
from pertura_runtime.project.models import (
    AssetBinding,
    CapabilityInvocationBinding,
    ReportRevision,
)
from pertura_runtime.project.workspace import ProjectWorkspace
from pertura_runtime.verifier import AuthoritySessionStore, VerifierBroker
from pertura_runtime.verifier.broker import VerifierBrokerError
from pertura_workflow.capabilities import CapabilityRegistry
from pertura_workflow.capabilities.registry import capability_scientific_hash
from pertura_workflow.intake import contract_with_confirmations, inspect_dataset_path
from pertura_workflow.environment import environment_lock
from pertura_workflow.knowledge_resources import knowledge_resource_lock
from pertura_workflow.planner import (
    plan_analysis,
    plan_requested_capability,
    resolve_dependencies,
)


def _normalize_dependencies(
    dependencies: list[DependencyRef],
) -> tuple[DependencyRef, ...]:
    """Return one current dependency per runtime object identity.

    Dependency resolution deliberately carries transitive environment and
    knowledge-resource provenance forward.  The runtime also resolves those
    same locks for the capability being invoked.  Normalize the union before
    constructing the request so a shared upstream/current environment is not
    inserted twice into the authority store.
    """

    normalized: list[DependencyRef] = []
    by_identity: dict[tuple[str, str], DependencyRef] = {}
    for dependency in dependencies:
        identity = (dependency.kind, dependency.object_id)
        existing = by_identity.get(identity)
        if existing is None:
            by_identity[identity] = dependency
            normalized.append(dependency)
            continue
        if (
            existing.object_hash != dependency.object_hash
            or existing.state != dependency.state
            or existing.required != dependency.required
        ):
            raise ValueError(
                "conflicting duplicate dependency for "
                f"{dependency.kind}:{dependency.object_id}"
            )
    return tuple(normalized)


class PerturaProductRuntime:
    """One run's capability registry, verifier lifecycle and compact product API."""

    def __init__(
        self,
        workspace: ClaudeRunWorkspace,
        *,
        policy: PromotionPolicy | None = None,
        policy_profile: str | None = None,
        network_policy: NetworkAccessPolicy | None = None,
        project_workspace: ProjectWorkspace | None = None,
        run_id: str | None = None,
    ) -> None:
        self.workspace = workspace
        self.project_workspace = project_workspace
        self.run_id = run_id or workspace.root.name
        if policy is not None and policy_profile is not None and policy.profile != policy_profile:
            raise ValueError("policy instance and policy_profile select different policies")
        requested_policy = policy or (
            PromotionPolicy(profile=policy_profile) if policy_profile is not None else None
        )
        self.promotion_policy = _bind_workspace_policy(workspace, requested_policy)
        self.registry = CapabilityRegistry.load_default(include_external=False)
        self.network_policy = network_policy or NetworkAccessPolicy.offline()
        self.authority_dir = _authority_dir(self.run_id, workspace.root)
        self._broker = VerifierBroker(
            authority_dir=self.authority_dir,
            run_id=self.run_id,
            policy_hash=self.promotion_policy.policy_hash,
            export_dir=workspace.artifacts_dir / "verified",
            output_root=workspace.outputs_dir / "capabilities",
            workspace_root=workspace.root,
            network_policy=self.network_policy,
        )
        self._started = False
        self._contracts: dict[str, DatasetContract] = {}
        self._invocation_bindings: dict[str, CapabilityInvocationBinding] = {}
        self._invocation_task_id: str | None = None
        self.asset_registry = (
            DataAssetRegistry(
                project_id=project_workspace.project.project_id,
                store=project_workspace.store,
                object_root=project_workspace.objects_dir,
            )
            if project_workspace is not None
            else None
        )
        self._invocation_binding_results: dict[str, str] = {}

    @property
    def broker(self) -> VerifierBroker:
        if not self._started:
            self._broker.start()
            self._started = True
        return self._broker

    @property
    def started(self) -> bool:
        return self._started

    def planning_material(
        self, contract_id: str | None = None
    ) -> tuple[DatasetContract, tuple[ResultEnvelope, ...]]:
        """Expose read-only registry inputs for the deterministic plan compiler."""

        contract = self._get_contract(contract_id)
        _, committed, _ = self._resolution_material()
        return contract, committed

    def planning_commit_records(self) -> tuple[dict[str, Any], ...]:
        """Expose verified commit metadata for deterministic binding compilation.

        Open-session entries are normalized to the state they receive after a
        valid seal.  No receipt is created here: exploratory results remain
        ``validated_untrusted`` and cannot acquire measured claim authority.
        """

        current_session_id = self.broker.session_id if self._started else None
        records: list[dict[str, Any]] = []
        for item in self._committed_entries():
            record = dict(item)
            session = record.get("authority_session") or {}
            is_current_open = bool(
                current_session_id
                and session.get("session_id") == current_session_id
            )
            state = str(record.get("verification_state") or "")
            if is_current_open:
                state = (
                    "trusted_receipt"
                    if record.get("receipt")
                    else "validated_untrusted"
                )
            if state not in {"trusted_receipt", "validated_untrusted"}:
                continue
            record["verification_state"] = state
            records.append(record)
        return tuple(records)

    def close(self, *, graceful: bool = True) -> None:
        if self._started:
            self._broker.stop(graceful=graceful)
            self._started = False

    def inspect_dataset(self, path: str | Path | None = None, *, dataset_id: str | None = None, confirmations: dict[str, Any] | None = None) -> dict[str, Any]:
        source = Path(path).expanduser().resolve() if path else self.workspace.input_source
        primary = None
        if self.project_workspace is not None and self.asset_registry is not None:
            primary = next(
                (
                    item for item in self.project_workspace.store.list_assets(self.project_workspace.project.project_id)
                    if item.role == "primary_dataset" and item.status == "current"
                ),
                None,
            )
            if source == self.project_workspace.root:
                source = (
                    self.asset_registry.resolve(primary.asset_id, expected_role="primary_dataset")
                    if primary is not None
                    else _implicit_project_dataset_source(self.project_workspace.root)
                )
            elif source is None and primary is not None:
                source = self.asset_registry.resolve(primary.asset_id, expected_role="primary_dataset")
        if source is None:
            raise ValueError("dataset path is required when the project has no primary_dataset asset")
        contract = inspect_dataset_path(source, dataset_id=dataset_id)
        if confirmations:
            contract = contract_with_confirmations(contract, confirmations)
        self._persist_contract(contract)
        self.broker.register_contract(contract)
        summary = _contract_summary(contract, self.workspace)
        if self.asset_registry is not None:
            asset = self.asset_registry.register(
                source, role="primary_dataset", kind="observed", source_class="observed_metadata"
            )
            self.project_workspace.store.put_asset_binding(
                AssetBinding(run_id=self.run_id, asset_id=asset.asset_id, role="primary_dataset")
            )
            summary["primary_asset_id"] = asset.asset_id
        return summary

    def register_dataset_contract(self, contract: DatasetContract) -> dict[str, Any]:
        """Register a prevalidated contract without rediscovering dataset facts.

        This is the product boundary used after a curator or resumed user session
        has already resolved the available design identity.  Registration records
        the supplied canonical contract; it does not infer or promote any fact.
        """

        validated = DatasetContract.model_validate(contract)
        self._persist_contract(validated)
        self.broker.register_contract(validated)
        return _contract_summary(validated, self.workspace)

    def confirm_design(self, contract_id: str, confirmations: dict[str, Any]) -> dict[str, Any]:
        contract = self._get_contract(contract_id)
        revised = contract_with_confirmations(contract, confirmations)
        self._persist_contract(revised)
        self.broker.register_contract(revised)
        confirmation_ids = []
        for field, value in confirmations.items():
            confirmation = DesignConfirmation(
                run_id=self.run_id,
                contract_id=contract.contract_id,
                field=field,
                value=value,
                rationale="Confirmed through the dashboard/design interface.",
            )
            self.broker.record_confirmation(confirmation)
            confirmation_ids.append(confirmation.confirmation_id)
        return _contract_summary(revised, self.workspace) | {"confirmation_ids": confirmation_ids}

    def replace_invocation_bindings(
        self,
        *,
        task_id: str,
        bindings: tuple[CapabilityInvocationBinding, ...],
    ) -> None:
        """Activate exactly one task's prevalidated invocation surface."""

        observed: dict[str, CapabilityInvocationBinding] = {}
        for binding in bindings:
            if binding.run_id != self.run_id:
                raise CapabilityInvocationBindingError(
                    "invocation binding belongs to another analysis run"
                )
            if binding.task_id != task_id:
                raise CapabilityInvocationBindingError(
                    "invocation binding belongs to another task"
                )
            if self._binding_turn_sequence() != binding.turn_sequence:
                raise CapabilityInvocationBindingError(
                    "invocation binding belongs to another or inactive turn"
                )
            if binding.binding_id in observed:
                raise CapabilityInvocationBindingError(
                    f"duplicate invocation binding: {binding.binding_id}"
                )
            observed[binding.binding_id] = binding
        order = {binding.binding_id: index for index, binding in enumerate(bindings)}
        for binding in bindings:
            for predecessor_id in binding.dependency_binding_ids:
                if predecessor_id not in observed:
                    raise CapabilityInvocationBindingError(
                        f"invocation predecessor is outside the active task: {predecessor_id}"
                    )
                if order[predecessor_id] >= order[binding.binding_id]:
                    raise CapabilityInvocationBindingError(
                        "invocation predecessor bindings must appear earlier in the task"
                    )
        if task_id != self._invocation_task_id:
            self._invocation_binding_results = {}
        self._invocation_task_id = task_id
        self._invocation_bindings = observed

    def invocation_binding(self, binding_id: str) -> CapabilityInvocationBinding:
        try:
            binding = self._invocation_bindings[binding_id]
        except KeyError as exc:
            raise CapabilityInvocationBindingError(
                f"unknown or inactive capability invocation binding: {binding_id}"
            ) from exc
        if binding.task_id != self._invocation_task_id:
            raise CapabilityInvocationBindingError(
                "capability invocation binding is outside the active task"
            )
        return binding

    def _record_invocation_binding_result(
        self, binding_id: str, response: Mapping[str, Any]
    ) -> None:
        result_id = str(response.get("result_id") or "")
        if result_id:
            self._invocation_binding_results[binding_id] = result_id

    def _binding_turn_sequence(self) -> int:
        if self.project_workspace is None:
            raise CapabilityInvocationBindingError(
                "invocation binding requires a project workspace"
            )
        run = self.project_workspace.store.get_run(self.run_id)
        if run is None:
            raise CapabilityInvocationBindingError(
                "invocation binding run is unavailable"
            )
        if run.active_turn_id:
            active = self.project_workspace.store.get_turn(run.active_turn_id)
            if active is not None:
                return active.sequence
        turns = [
            turn
            for conversation in self.project_workspace.store.list_conversations(
                self.project_workspace.project.project_id
            )
            if conversation.run_id == self.run_id
            for turn in self.project_workspace.store.list_turns(
                conversation.conversation_id
            )
        ]
        return max((turn.sequence for turn in turns), default=0) + 1

    def run_diagnostic(self, capability_id: str | None = None, *, binding_id: str | None = None, contract_id: str | None = None, scope: dict[str, Any] | None = None, parameters: dict[str, Any] | None = None, dependencies: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        if binding_id:
            capability_id, contract_id, scope, parameters, dependencies, blocked = (
                self._resolve_invocation_binding(
                    binding_id,
                    tool_name="run_diagnostic",
                    capability_id=capability_id,
                    contract_id=contract_id,
                    scope=scope,
                    parameters=parameters,
                    dependencies=dependencies,
                )
            )
            if blocked is not None:
                return blocked
            response = self._run(
                capability_id,
                kind="diagnostic",
                contract_id=contract_id,
                scope=scope,
                parameters=parameters,
                dependencies=dependencies,
            )
            self._record_invocation_binding_result(binding_id, response)
            binding = self.invocation_binding(binding_id)
            return response | {
                "binding_id": binding_id,
                "binding_hash": binding.binding_hash,
                "output_mapping": dict(binding.output_mapping),
            }
        if not capability_id:
            raise ValueError("capability_id or binding_id is required")
        contract = self._get_contract(contract_id)
        _, committed, _ = self._resolution_material()
        plan = plan_requested_capability(
            capability_id,
            expected_kind="diagnostic",
            contract=contract,
            committed_results=committed,
            registry=self.registry,
        )
        if plan.blockers:
            spec = self.registry.get(capability_id)
            return _blocked_runtime_response(
                capability_id=capability_id,
                scope_id=None,
                blockers=plan.blockers,
                required_upstream=plan.required_upstream,
                summary=(
                    "Diagnostic planning was blocked by the confirmed design; "
                    "no prerequisite was executed automatically."
                ),
                trust_level=spec.trust_level.value,
                validation_status=spec.metadata.get("validation_status"),
                plan=plan.to_dict(),
            )
        return self._run(
            capability_id,
            kind="diagnostic",
            contract_id=contract.contract_id,
            scope=scope,
            parameters=parameters,
            dependencies=dependencies,
        )

    def run_analysis(self, objective: str, *, binding_id: str | None = None, capability_id: str | None = None, contract_id: str | None = None, scope: dict[str, Any] | None = None, parameters: dict[str, Any] | None = None, dependencies: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        if binding_id:
            capability_id, contract_id, scope, parameters, dependencies, blocked = (
                self._resolve_invocation_binding(
                    binding_id,
                    tool_name="run_analysis",
                    capability_id=capability_id,
                    contract_id=contract_id,
                    scope=scope,
                    parameters=parameters,
                    dependencies=dependencies,
                )
            )
            if blocked is not None:
                return blocked
            response = self._run(
                capability_id,
                kind="analysis",
                contract_id=contract_id,
                scope=scope,
                parameters=parameters,
                dependencies=dependencies,
                objective=objective,
            )
            self._record_invocation_binding_result(binding_id, response)
            binding = self.invocation_binding(binding_id)
            return response | {
                "binding_id": binding_id,
                "binding_hash": binding.binding_hash,
                "output_mapping": dict(binding.output_mapping),
            }
        contract = self._get_contract(contract_id)
        _, committed, _ = self._resolution_material()
        plan = plan_analysis(
            objective,
            contract=contract,
            committed_results=committed,
            registry=self.registry,
            requested_capability_id=capability_id,
        )
        plan_blockers = list(plan.blockers)
        planned_spec = None
        if plan.capability_id:
            planned_spec = self.registry.get(plan.capability_id)
            environment_profile = str(
                planned_spec.metadata.get("environment_profile") or ""
            )
            if environment_profile:
                try:
                    lock = environment_lock(environment_profile)
                except RuntimeError:
                    lock = None
                if not lock:
                    plan_blockers.append(
                        "required environment is unavailable; "
                        f"run `pertura env setup {environment_profile}` explicitly"
                    )
        if plan_blockers or not plan.capability_id:
            return _blocked_runtime_response(
                capability_id=plan.capability_id,
                scope_id=None,
                blockers=tuple(plan_blockers),
                required_upstream=plan.required_upstream,
                summary="Analysis planning was blocked by the confirmed design or missing prerequisites.",
                trust_level=planned_spec.trust_level.value if planned_spec else None,
                validation_status=(planned_spec.metadata.get("validation_status") if planned_spec else None),
                plan=plan.to_dict() | {"blockers": plan_blockers, "status": "blocked"},
            )
        return self._run(
            plan.capability_id, kind="analysis", contract_id=contract.contract_id,
            scope=scope, parameters=parameters, dependencies=dependencies,
            objective=objective,
        )



    def evaluate_virtual_model(self, *, binding_id: str | None = None, capability_id: str | None = "virtual.evaluate.comprehensive.v1", contract_id: str | None = None, scope: dict[str, Any] | None = None, parameters: dict[str, Any] | None = None) -> dict[str, Any]:
        if binding_id:
            capability_id, contract_id, scope, parameters, dependencies, blocked = (
                self._resolve_invocation_binding(
                    binding_id,
                    tool_name="evaluate_virtual_model",
                    capability_id=capability_id,
                    contract_id=contract_id,
                    scope=scope,
                    parameters=parameters,
                    dependencies=None,
                )
            )
            if blocked is not None:
                return blocked
            response = self._run(
                capability_id,
                kind="virtual",
                contract_id=contract_id,
                scope=scope,
                parameters=parameters,
                dependencies=dependencies,
            )
            self._record_invocation_binding_result(binding_id, response)
            binding = self.invocation_binding(binding_id)
            return response | {
                "binding_id": binding_id,
                "binding_hash": binding.binding_hash,
                "output_mapping": dict(binding.output_mapping),
            }
        if not capability_id:
            raise ValueError("capability_id or binding_id is required")
        return self._run(capability_id, kind="virtual", contract_id=contract_id, scope=scope, parameters=parameters)

    def _resolve_invocation_binding(
        self,
        binding_id: str,
        *,
        tool_name: str,
        capability_id: str | None,
        contract_id: str | None,
        scope: dict[str, Any] | None,
        parameters: dict[str, Any] | None,
        dependencies: list[dict[str, Any]] | None,
    ) -> tuple[
        str,
        str,
        dict[str, Any],
        dict[str, Any],
        list[dict[str, Any]],
        dict[str, Any] | None,
    ]:
        binding = self.invocation_binding(binding_id)
        run = self.project_workspace.store.get_run(self.run_id)
        active = (
            self.project_workspace.store.get_turn(run.active_turn_id)
            if run is not None and run.active_turn_id
            else None
        )
        if active is None or active.sequence != binding.turn_sequence:
            raise CapabilityInvocationBindingError(
                "capability invocation binding is stale for the active turn"
            )
        if binding.tool_name != tool_name:
            raise CapabilityInvocationBindingError(
                f"binding {binding_id} requires {binding.tool_name}, not {tool_name}"
            )
        if capability_id and capability_id != binding.capability_id:
            raise CapabilityInvocationBindingError(
                "caller cannot replace the bound capability"
            )
        if contract_id and contract_id != binding.contract_id:
            raise CapabilityInvocationBindingError(
                "caller cannot replace the bound DatasetContract"
            )
        if scope and dict(scope) != binding.scope:
            raise CapabilityInvocationBindingError("caller cannot replace the bound scope")
        if dependencies:
            raise CapabilityInvocationBindingError(
                "caller cannot replace bound capability dependencies"
            )
        contract = self._get_contract(binding.contract_id)
        if contract.canonical_hash != binding.contract_hash:
            raise CapabilityInvocationBindingError("bound DatasetContract has drifted")
        spec = self.registry.get(binding.capability_id, binding.capability_version)
        if capability_scientific_hash(spec) != binding.capability_scientific_hash:
            raise CapabilityInvocationBindingError("bound capability contract has drifted")

        supplied = dict(parameters or {})
        forbidden = sorted(set(supplied) - set(binding.allowed_overrides))
        if forbidden:
            raise CapabilityInvocationBindingError(
                f"caller attempted to override locked parameters: {forbidden}"
            )
        merged = {**binding.bound_parameters, **supplied}
        for expected in binding.input_assets:
            asset = (
                self.project_workspace.store.get_asset(expected.asset_id)
                if self.project_workspace is not None
                else None
            )
            if asset is None or asset.identity_hash != expected.asset_identity_hash:
                raise CapabilityInvocationBindingError(
                    f"bound asset is missing or has drifted: {expected.asset_id}"
                )
            if asset.role != expected.role:
                raise CapabilityInvocationBindingError(
                    f"bound asset role has drifted: {expected.asset_id}"
                )

        _, committed, _ = self._resolution_material()
        by_id = {item.result_id: item for item in committed}
        commit_by_id = {
            str((item.get("result") or {}).get("result_id")): item
            for item in self.planning_commit_records()
        }
        for result_id, result_hash, state, receipt_id in zip(
            binding.dependency_result_ids,
            binding.dependency_result_hashes,
            binding.dependency_verification_states,
            binding.dependency_receipt_ids,
            strict=True,
        ):
            result = by_id.get(result_id)
            record = commit_by_id.get(result_id) or {}
            receipt = record.get("receipt") or {}
            observed_receipt_id = (
                receipt.get("receipt_id") if isinstance(receipt, Mapping) else None
            )
            if (
                result is None
                or result.canonical_hash != result_hash
                or result.stale
                or record.get("verification_state") != state
                or observed_receipt_id != receipt_id
            ):
                raise CapabilityInvocationBindingError(
                    f"bound dependency is missing or stale: {result_id}"
                )

        if binding.readiness == "blocked_probe":
            return (
                binding.capability_id,
                binding.contract_id,
                dict(binding.scope),
                merged,
                binding_dependency_hints(binding),
                _blocked_runtime_response(
                    capability_id=binding.capability_id,
                    scope_id=None,
                    blockers=binding.blockers,
                    required_upstream=(),
                    trust_level=spec.trust_level.value,
                    validation_status=spec.metadata.get("validation_status"),
                    summary=(
                        "The bound applicability probe identified missing "
                        "prerequisites."
                    ),
                )
                | {
                    "binding_id": binding.binding_id,
                    "binding_hash": binding.binding_hash,
                    "output_mapping": dict(binding.output_mapping),
                },
            )

        dependency_hints = binding_dependency_hints(binding)
        for predecessor_id in binding.dependency_binding_ids:
            predecessor = self.invocation_binding(predecessor_id)
            result_id = self._invocation_binding_results.get(predecessor_id)
            result = by_id.get(str(result_id or ""))
            if result is None or result.stale:
                raise CapabilityInvocationBindingError(
                    "bound predecessor has not produced a current committed result: "
                    f"{predecessor.capability_id}"
                )
            dependency_hints.append(
                {
                    "object_id": result.result_id,
                    "object_hash": result.canonical_hash,
                    "state": "current",
                }
            )

        return (
            binding.capability_id,
            binding.contract_id,
            dict(binding.scope),
            merged,
            dependency_hints,
            None,
        )

    def finalize_report(self, run_id: str | None = None) -> dict[str, Any]:
        selected_run = run_id or self.run_id
        self._refresh_asset_state()
        if self._started:
            try:
                self.broker.seal_run(selected_run)
            except (VerifierBrokerError, OSError, EOFError, ConnectionError):
                self.close(graceful=False)

        database = self.authority_dir / "authority.sqlite3"
        if database.is_file():
            projection = AuthoritySessionStore(database, read_only=True).project_run(
                selected_run,
                expected_policy_hash=self.promotion_policy.policy_hash,
            )
            committed = list(projection.committed)
        else:
            projection = None
            committed = []

        results = [item["result"] for item in committed]
        statements: list[dict[str, Any]] = []
        decisions: list[dict[str, Any]] = []
        verification_by_result = {
            item["result"]["result_id"]: item["verification_state"] for item in committed
        }
        for item in committed:
            if item.get("verification_state") not in {"trusted_receipt", "validated_untrusted"}:
                continue
            result = ResultEnvelope.model_validate(item["result"])
            try:
                spec = self.registry.get(result.capability_id, result.capability_version)
            except (KeyError, ValueError):
                # Retired/legacy capability rows remain reportable historical
                # records but cannot be reinterpreted for promotion.
                continue
            if result.source_class != SourceClass.measured_result or "measured_association" not in spec.claim_permissions:
                continue
            statement = ScientificStatement(
                run_id=selected_run,
                text=f"A replicate-aware measured association result is available for scope {result.scope.scope_id}.",
                source_class=SourceClass.measured_result,
                scope=result.scope,
                result_ids=(result.result_id,),
                requested_strength="measured_association",
                limitations=result.cautions,
            )
            trusted_session = item["verification_state"] == "trusted_receipt"
            receipt = RunReceipt.model_validate(item["receipt"]) if trusted_session and item.get("receipt") else None
            session = item.get("authority_session") or {}
            decision = decide_promotion(
                statement,
                results=(result,),
                receipts=(receipt,) if receipt else (),
                capability_specs=(spec,),
                authoritative_public_key=str(session.get("public_key") or ""),
                policy=self.promotion_policy,
            )
            if self._started:
                self.broker.commit_promotion(statement, decision)
            statements.append(statement.model_dump(mode="json"))
            decisions.append(decision.model_dump(mode="json"))

        trusted_results = [
            item["result"] for item in committed if item["verification_state"] == "trusted_receipt"
        ]
        exploratory_results = [
            item["result"] for item in committed if item["verification_state"] == "validated_untrusted"
        ]
        unverified_results = [
            item["result"]
            for item in committed
            if item["verification_state"] not in {"trusted_receipt", "validated_untrusted"}
        ]
        prediction_results = [item for item in results if item.get("source_class") == "prediction"]
        prior_results = [item for item in results if item.get("source_class") == "curated_prior"]
        hypothesis_results = [item for item in results if item.get("source_class") == "hypothesis"]
        stale_results = [
            item for item in results
            if bool(item.get("stale"))
            or bool((item.get("metadata") or {}).get("stale"))
            or (item.get("metadata") or {}).get("dependency_state") == "stale"
        ]
        stale_ids = {item["result_id"] for item in stale_results}
        non_supporting_statuses = {"blocked", "failed", "unresolved", "out_of_scope"}
        current_entries = [
            item for item in committed
            if item["result"]["result_id"] not in stale_ids
        ]
        current_supporting_entries = [
            item for item in current_entries
            if item["result"].get("status") not in non_supporting_statuses
        ]
        current_trusted_measured_results = [
            item["result"] for item in current_supporting_entries
            if item["result"].get("source_class") == "measured_result"
            and item["verification_state"] == "trusted_receipt"
        ]
        current_candidate_measured_results = [
            item["result"] for item in current_supporting_entries
            if item["result"].get("source_class") == "measured_result"
            and item["verification_state"] == "validated_untrusted"
        ]
        current_candidate_results = [
            item["result"] for item in current_supporting_entries
            if item["verification_state"] == "validated_untrusted"
            and item["result"].get("source_class")
            in {"measured_result", "observed_metadata"}
        ]
        current_unverified_measured_results = [
            item["result"] for item in current_supporting_entries
            if item["result"].get("source_class") == "measured_result"
            and item["verification_state"] not in {"trusted_receipt", "validated_untrusted"}
        ]
        observed_metadata_results = [
            item["result"] for item in current_supporting_entries
            if item["result"].get("source_class") == "observed_metadata"
            and item["verification_state"] != "validated_untrusted"
        ]
        current_prediction_results = [
            item["result"] for item in current_supporting_entries
            if item["result"].get("source_class") == "prediction"
        ]
        current_prior_results = [
            item["result"] for item in current_supporting_entries
            if item["result"].get("source_class") == "curated_prior"
        ]
        current_hypothesis_results = [
            item["result"] for item in current_supporting_entries
            if item["result"].get("source_class") == "hypothesis"
        ]
        blocking_results = [
            item["result"] for item in current_entries
            if item["result"].get("status") in non_supporting_statuses
        ]
        authority_projection = projection.to_dict() if projection else {
            "schema_version": "pertura-run-aggregate-v1",
            "run_id": selected_run,
            "sessions": (),
            "committed": (),
            "aggregate_digest": None,
            "legacy_unverified_result_ids": (),
            "invalid_session_ids": (),
        }
        verified_states = {"trusted_receipt", "validated_untrusted"}
        verified_result_count = sum(
            item["verification_state"] in verified_states for item in committed
        )
        unverified_result_count = len(committed) - verified_result_count
        seal = {
            "schema_version": "pertura-run-aggregate-v1",
            "run_id": selected_run,
            "root_digest": projection.aggregate_digest,
            "sessions": list(projection.sessions),
            "legacy_unverified_result_ids": list(projection.legacy_unverified_result_ids),
            "invalid_session_ids": list(projection.invalid_session_ids),
        } if projection and projection.sessions else None
        if verified_result_count and not unverified_result_count:
            final_status = "completed"
        elif verified_result_count:
            final_status = "completed_with_unverified_results"
        else:
            final_status = "untrusted_no_verified_results"

        report_json = self.workspace.reports_dir / "capability_report.json"
        report_md = self.workspace.reports_dir / "capability_report.md"

        turn_finals = []
        if self.project_workspace is not None:
            store = self.project_workspace.store
            for conversation in store.list_conversations(self.project_workspace.project.project_id):
                if conversation.run_id != selected_run:
                    continue
                for turn in store.list_turns(conversation.conversation_id):
                    final = store.get_turn_final(turn.turn_id)
                    if final is not None:
                        turn_finals.append(final)
        report_hypotheses = list(dict.fromkeys(
            text for final in turn_finals for text in final.hypotheses
        ))
        report_limitations = list(dict.fromkeys(
            [text for item in current_supporting_entries for text in item["result"].get("cautions") or ()]
            + [text for final in turn_finals for text in final.limitations]
        ))
        next_experiments = list(dict.fromkeys(
            text for final in turn_finals for text in final.next_steps
        ))
        payload = {
            "schema_version": "pertura-capability-report-v2",
            "run_id": selected_run,
            "result_count": len(results),
            "results": results,
            "trusted_results": trusted_results,
            "exploratory_results": exploratory_results,
            "unverified_results": unverified_results,
            "prediction_results": prediction_results,
            "curated_prior_results": prior_results,
            "hypothesis_results": hypothesis_results,
            "stale_results": stale_results,
            "current_trusted_measured_results": current_trusted_measured_results,
            "current_candidate_measured_results": current_candidate_measured_results,
            "current_candidate_results": current_candidate_results,
            "current_unverified_measured_results": current_unverified_measured_results,
            "observed_metadata_results": observed_metadata_results,
            "current_prediction_results": current_prediction_results,
            "current_curated_prior_results": current_prior_results,
            "current_hypothesis_results": current_hypothesis_results,
            "blocking_results": blocking_results,
            "report_hypotheses": report_hypotheses,
            "report_limitations": report_limitations,
            "next_experiments": next_experiments,
            "turn_final_ids": [final.turn_id for final in turn_finals],
            "verification_state_by_result": verification_by_result,
            "authority_projection": authority_projection,
            "statements": statements,
            "promotion_decisions": decisions,
            "run_seal": seal,
            "policy_hash": self.promotion_policy.policy_hash,
        }
        report_json.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        lines = ["# Pertura capability report", "", f"Run: `{selected_run}`", ""]
        if not results:
            lines.append("No committed capability results are available.")
        for heading, collection, introduction, prefix in (
            (
                "Current trusted measured findings",
                current_trusted_measured_results,
                "Execution is receipt-verified. Only separately promoted statements carry strong measured authority.",
                "",
            ),
            (
                "Exploratory candidate analyses",
                current_candidate_results,
                "These validator-passed results have no trusted receipt and cannot support strong measured claims.",
                "Candidate analysis indicates: ",
            ),
            (
                "Observed design and diagnostic context",
                observed_metadata_results,
                "These records describe observed or confirmed dataset context; they are not measured effects.",
                "",
            ),
            (
                "Predictions",
                current_prediction_results,
                "Predictions remain predictions even when their evaluation completes successfully.",
                "Prediction: ",
            ),
            (
                "Curated priors",
                current_prior_results,
                "Curated resources provide prior context and do not establish a measured effect in this run.",
                "Prior context: ",
            ),
            (
                "Capability-generated hypotheses",
                current_hypothesis_results,
                "These outputs are hypotheses or design proposals and have no measured claim authority.",
                "Hypothesis: ",
            ),
            (
                "Unverified historical/session analyses",
                current_unverified_measured_results,
                "These results belong to a legacy, aborted, unsealed, or invalid authority session and cannot support strong claims.",
                "Unverified analysis: ",
            ),
            (
                "Stale historical results",
                stale_results,
                "An upstream contract, asset, scope, or dependency changed. These records are audit history only.",
                "Historical result: ",
            ),
        ):
            if not collection:
                continue
            lines.extend([f"## {heading}", "", introduction, ""])
            for result in collection:
                state = verification_by_result.get(result["result_id"], "unverified")
                lines.extend([
                    f"### {result['capability_id']}",
                    "",
                    f"Status: {result['status']}  ",
                    f"Authority: {state}  ",
                    f"Result: {result['result_id']}  ",
                    prefix + result["summary"],
                    "",
                ])
                for blocker in result.get("blockers") or []:
                    lines.append(f"- Blocker: {blocker}")
                for caution in result.get("cautions") or []:
                    lines.append(f"- Limitation: {caution}")
        if blocking_results:
            lines.extend(["", "## Blockers", ""])
            for result in blocking_results:
                reasons = list(result.get("blockers") or ()) or [result["summary"]]
                for reason in reasons:
                    lines.append(f"- `{result['capability_id']}`: {reason}")
        if report_hypotheses:
            lines.extend(["", "## Hypotheses and contradictions", ""])
            lines.extend(f"- {item}" for item in report_hypotheses)
        if report_limitations:
            lines.extend(["", "## Limitations", ""])
            lines.extend(f"- {item}" for item in report_limitations)
        if next_experiments:
            lines.extend(["", "## Next experiments", ""])
            lines.extend(f"- {item}" for item in next_experiments)
        if decisions:
            lines.extend(["", "## Scientific statements", ""])
            for statement, decision in zip(statements, decisions):
                if decision["status"] == "promoted":
                    lines.append(
                        f"- {statement['text']} (result `{decision['result_ids'][0]}`, receipt `{decision['receipt_ids'][0]}`, scope `{statement['scope']['scope_id']}`)"
                    )
                else:
                    lines.append(f"- Not promoted: {statement['text']} ({'; '.join(decision['reasons'])})")
        report_md.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
        (self.workspace.reports_dir / "pertura_final.md").write_text(report_md.read_text(encoding="utf-8"), encoding="utf-8")
        revision = self._persist_versioned_report(
            selected_run=selected_run, payload=payload, markdown=report_md.read_text(encoding="utf-8")
        )
        response = {
            "run_id": selected_run,
            "status": final_status,
            "result_count": len(results),
            "report_paths": [str(report_md.relative_to(self.workspace.root)), str(report_json.relative_to(self.workspace.root))],
            "root_digest": seal.get("root_digest") if seal and verified_result_count else None,
            "promotion_decision_count": len(decisions),
        }
        if revision is not None:
            response.update({
                "report_id": revision.report_id,
                "revision": revision.revision,
                "report_digest": revision.digest,
                "report_paths": [revision.markdown_path, revision.json_path],
            })
        return response

    def _persist_versioned_report(
        self, *, selected_run: str, payload: dict[str, Any], markdown: str
    ) -> ReportRevision | None:
        if self.project_workspace is None:
            return None
        store = self.project_workspace.store
        turn_finals = []
        for conversation in store.list_conversations(self.project_workspace.project.project_id):
            if conversation.run_id != selected_run:
                continue
            for turn in store.list_turns(conversation.conversation_id):
                final = store.get_turn_final(turn.turn_id)
                if final is not None:
                    turn_finals.append(final)
        latest_contract = self.workspace.artifacts_dir / "dataset_contract.latest.json"
        contract_hash = None
        if latest_contract.is_file():
            contract_hash = DatasetContract.model_validate_json(
                latest_contract.read_text(encoding="utf-8")
            ).canonical_hash
        digest_payload = {
            "schema_version": "pertura-report-digest-v1",
            "contract_hash": contract_hash,
            "authority_aggregate_digest": (payload.get("authority_projection") or {}).get("aggregate_digest"),
            "result_ids": sorted(item.get("result_id") for item in payload.get("results", [])),
            "stale_result_ids": sorted(item.get("result_id") for item in payload.get("stale_results", [])),
            "policy_hash": self.promotion_policy.policy_hash,
            "turn_finals": [
                final.model_dump(mode="json", exclude={"created_at", "markdown"})
                for final in sorted(turn_finals, key=lambda item: item.turn_id)
            ],
            "skill_provider_provenance": _read_manifest_provenance(self.workspace.root),
        }
        digest = canonical_hash(digest_payload)
        existing = store.report_for_digest(selected_run, digest)
        if existing is not None:
            return existing
        revision_number = len(store.list_report_revisions(selected_run)) + 1
        revision_root = self.workspace.reports_dir / "revisions" / f"{revision_number:04d}"
        revision_root.mkdir(parents=True, exist_ok=True)
        json_path = revision_root / "report.json"
        markdown_path = revision_root / "report.md"
        versioned_payload = dict(payload)
        versioned_payload.update({
            "report_schema_version": "pertura-versioned-report-v1",
            "revision": revision_number,
            "report_digest": digest,
            "turn_final_ids": [final.turn_id for final in turn_finals],
        })
        json_path.write_text(json.dumps(versioned_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        markdown_path.write_text(markdown.rstrip() + "\n", encoding="utf-8")
        (self.workspace.reports_dir / "latest.json").write_text(json_path.read_text(encoding="utf-8"), encoding="utf-8")
        (self.workspace.reports_dir / "latest.md").write_text(markdown_path.read_text(encoding="utf-8"), encoding="utf-8")
        revision = ReportRevision(
            report_id=f"report_{digest.split(':', 1)[1][:24]}",
            run_id=selected_run,
            revision=revision_number,
            digest=digest,
            turn_final_ids=tuple(final.turn_id for final in turn_finals),
            json_path=str(json_path.relative_to(self.project_workspace.root)),
            markdown_path=str(markdown_path.relative_to(self.project_workspace.root)),
        )
        store.put_report_revision(revision)
        return revision


    def capability_list(
        self, *, kind: str | None = None, include_deprecated: bool = False
    ) -> list[dict[str, Any]]:
        return [item.to_dict() for item in self.registry.list(
            kind=kind, include_deprecated=include_deprecated)]

    def _run(self, capability_id: str, *, kind: str, contract_id: str | None, scope: dict[str, Any] | None, parameters: dict[str, Any] | None, dependencies: list[dict[str, Any]] | None = None, objective: str | None = None) -> dict[str, Any]:
        self._refresh_asset_state()
        spec = self.registry.get(capability_id)
        submitted_parameters = self._register_workspace_parameter_assets(
            spec, dict(parameters or {})
        )
        parameters = validate_and_resolve_parameters(
            spec,
            submitted_parameters,
            asset_registry=self.asset_registry,
            workspace_root=self.workspace.root,
        )
        if spec.metadata.get("network_required"):
            host = str(spec.metadata.get("network_host") or "")
            if not host or not self.network_policy.allows(spec.capability_id, host):
                return _blocked_runtime_response(
                    capability_id=capability_id,
                    scope_id=None,
                    blockers=(
                        "network access is disabled for this capability; restart with "
                        "explicit literature-network authorization",
                    ),
                    required_upstream=(),
                    trust_level=spec.trust_level.value,
                    validation_status=spec.metadata.get("validation_status"),
                    summary="Networked literature retrieval is opt-in and remains disabled.",
                )
        if spec.kind != kind:
            raise ValueError(f"{capability_id} is a {spec.kind} capability, not {kind}")
        contract = self._get_contract(contract_id)
        scope_key = ScopeKey.model_validate(scope) if scope else ScopeKey(dataset_id=contract.dataset_id, unresolved_fields=tuple(field for field in contract.unresolved_fields if field in {"control", "replicate", "state_label"}))
        raw_hints = [dict(item) for item in (dependencies or [])]
        result_hints: list[dict[str, Any]] = []
        environment_hints: list[dict[str, Any]] = []
        data_asset_hints: list[dict[str, Any]] = []
        for hint in raw_hints:
            hint_kind = str(hint.get("kind") or "")
            if hint_kind == "contract":
                if hint.get("object_id") not in {None, "", contract.contract_id}:
                    raise ValueError("caller-supplied contract dependency references another contract")
                if hint.get("object_hash") not in {None, "", contract.canonical_hash}:
                    raise ValueError("caller-supplied contract dependency hash is forged or stale")
            elif hint_kind == "environment":
                environment_hints.append(hint)
            elif hint_kind == "data_asset":
                data_asset_hints.append(hint)
            else:
                result_hints.append(hint)

        _, committed, trusted_result_ids = self._resolution_material()
        resolution = resolve_dependencies(
            spec,
            contract=contract,
            required_scope=scope_key,
            committed_results=committed,
            dependency_hints=result_hints,
            trusted_receipt_result_ids=trusted_result_ids,
        )
        if not resolution.ok:
            return _blocked_runtime_response(
                capability_id=capability_id,
                scope_id=scope_key.scope_id,
                blockers=resolution.blockers,
                required_upstream=resolution.required_upstream,
                ambiguous_result_ids=resolution.ambiguous_result_ids,
                candidate_result_ids=resolution.candidate_result_ids,
                dependency_verdicts=resolution.dependency_verdicts,
                trust_level=spec.trust_level.value,
                validation_status=spec.metadata.get("validation_status"),
                summary="Capability execution was blocked by missing or incompatible dependencies.",
            )
        explicit_dependencies = list(resolution.dependencies)
        if self.asset_registry is not None:
            for hint in data_asset_hints:
                asset_id = str(hint.get("object_id") or "")
                asset = self.project_workspace.store.get_asset(asset_id)
                if asset is None:
                    raise ValueError(f"unknown registered asset: {asset_id}")
                supplied_hash = str(hint.get("object_hash") or "")
                if supplied_hash and supplied_hash != asset.identity_hash:
                    raise ValueError(f"data asset dependency hash mismatch: {asset_id}")
                resolved_path = self.asset_registry.resolve(asset_id)
                self.broker.register_runtime_object(
                    kind="data_asset",
                    object_id=asset.asset_id,
                    object_hash=asset.identity_hash,
                    payload=asset.model_dump(mode="json")
                    | {"resolved_path": str(resolved_path)},
                )
                dependency = DependencyRef(
                    kind="data_asset",
                    object_id=asset.asset_id,
                    object_hash=asset.identity_hash,
                    role=f"asset:{asset.role}",
                )
                if dependency not in explicit_dependencies:
                    explicit_dependencies.append(dependency)
            properties = (spec.parameters_schema or {}).get("properties") or {}
            for name, field_schema in properties.items():
                role = field_schema.get("x-pertura-asset-role") if isinstance(field_schema, dict) else None
                value = submitted_parameters.get(name)
                asset_ids = value if isinstance(value, list) else [value]
                if not role:
                    continue
                for asset_id in asset_ids:
                    if not isinstance(asset_id, str) or not asset_id.startswith("asset_"):
                        continue
                    asset = self.project_workspace.store.get_asset(asset_id)
                    if asset is None:
                        raise ValueError(f"unknown registered asset: {asset_id}")
                    resolved_path = self.asset_registry.resolve(
                        asset_id, expected_role=str(role)
                    )
                    self.broker.register_runtime_object(
                        kind="data_asset",
                        object_id=asset.asset_id,
                        object_hash=asset.identity_hash,
                        payload=asset.model_dump(mode="json")
                        | {"resolved_path": str(resolved_path)},
                    )
                    dependency = DependencyRef(
                        kind="data_asset",
                        object_id=asset.asset_id,
                        object_hash=asset.identity_hash,
                        role=f"asset:{role}",
                    )
                    if dependency not in explicit_dependencies:
                        explicit_dependencies.append(dependency)
        environment_profile = str(spec.metadata.get("environment_profile") or "")
        if environment_profile:
            try:
                lock = environment_lock(environment_profile)
            except RuntimeError:
                lock = None
            if not lock:
                return _blocked_runtime_response(
                    capability_id=capability_id,
                    scope_id=scope_key.scope_id,
                    blockers=(f"required environment is unavailable: {environment_profile}",),
                    required_upstream=(),
                    trust_level=spec.trust_level.value,
                    validation_status=spec.metadata.get("validation_status"),
                    summary=(
                        "Capability execution requires an installed, verified environment; "
                        f"run `pertura env setup {environment_profile}` explicitly."
                    ),
                )
            if lock:
                object_id = f"environment:{environment_profile}"
                self.broker.register_runtime_object(
                    kind="environment",
                    object_id=object_id,
                    object_hash=lock["lock_hash"],
                    payload=lock,
                )
                for item in environment_hints:
                    if item.get("object_id") not in {None, "", object_id}:
                        raise ValueError("caller-supplied environment dependency references another profile")
                    if item.get("object_hash") not in {None, "", lock["lock_hash"]}:
                        raise ValueError(
                            "caller-supplied environment dependency does not match "
                            "the authoritative local lock"
                        )
                explicit_dependencies.append(DependencyRef(
                    kind="environment",
                    object_id=object_id,
                    object_hash=lock["lock_hash"],
                    role="scientific_execution_environment",
                ))
        resource_profile = str(spec.metadata.get("resource_profile") or "")
        if resource_profile:
            try:
                resource_lock = knowledge_resource_lock(resource_profile)
            except RuntimeError:
                resource_lock = None
            if not resource_lock:
                return _blocked_runtime_response(
                    capability_id=capability_id,
                    scope_id=scope_key.scope_id,
                    blockers=(f"required knowledge resource is unavailable: {resource_profile}",),
                    required_upstream=(),
                    trust_level=spec.trust_level.value,
                    validation_status=spec.metadata.get("validation_status"),
                    summary=(
                        "Capability execution requires a locked knowledge resource; "
                        f"run pertura resources setup {resource_profile} explicitly."
                    ),
                )
            object_id = f"knowledge_resource:{resource_profile}"
            self.broker.register_runtime_object(
                kind="knowledge_resource",
                object_id=object_id,
                object_hash=resource_lock["lock_hash"],
                payload=resource_lock,
            )
            explicit_dependencies.append(DependencyRef(
                kind="knowledge_resource",
                object_id=object_id,
                object_hash=resource_lock["lock_hash"],
                role="scientific_knowledge_resource",
            ))
        request = CapabilityRunRequest(
            run_id=self.run_id,
            capability_id=spec.capability_id,
            capability_version=spec.version,
            contract_id=contract.contract_id,
            contract_hash=contract.canonical_hash,
            scope=scope_key,
            objective=objective,
            parameters=parameters or {},
            dependencies=_normalize_dependencies(explicit_dependencies),
        )
        response = self.broker.run(request)
        result = response["result"]
        output_asset_ids: list[str] = []
        if self.asset_registry is not None:
            run_record = self.project_workspace.store.get_run(self.run_id)
            active_turn_id = run_record.active_turn_id if run_record else None
            for relative in result.get("output_paths") or []:
                path = (self.workspace.root / relative).resolve()
                if not path.exists() or self.workspace.root.resolve() not in path.parents:
                    continue
                asset = self.asset_registry.register(
                    path,
                    role=f"capability_output:{result['result_kind']}",
                    kind="derived",
                    source_class=result["source_class"],
                    created_by_turn=active_turn_id,
                    dependencies=(result["result_id"],),
                )
                self.project_workspace.store.put_asset_binding(
                    AssetBinding(run_id=self.run_id, asset_id=asset.asset_id, role=asset.role)
                )
                output_asset_ids.append(asset.asset_id)
        compact = {
            "result_id": result["result_id"],
            "receipt_id": response["receipt"]["receipt_id"] if response.get("receipt") else None,
            "status": result["status"],
            "blockers": result.get("blockers") or [],
            "cautions": result.get("cautions") or [],
            "summary": result["summary"],
            "output_paths": result.get("output_paths") or [],
            "scope_id": result["scope"]["scope_id"],
            "trust_level": result["capability_trust"],
            "validation_status": (result.get("metadata") or {}).get("validation_status"),
            "asset_ids": output_asset_ids,
        }
        if kind == "virtual":
            compact["not_implemented_capabilities"] = [
                item.capability_id for item in self.registry.list(kind="virtual") if not item.implemented
            ]
        return compact

    def _register_workspace_parameter_assets(
        self,
        spec: CapabilitySpec,
        parameters: dict[str, Any],
    ) -> dict[str, Any]:
        """Bind workspace-owned path parameters as hash-addressed data assets.

        Registration attests only to the input identity used by a later
        capability run.  It does not create a scientific result or satisfy a
        receipt-backed capability-result dependency.
        """

        if self.asset_registry is None or self.project_workspace is None:
            return parameters
        normalized = dict(parameters)
        properties = (spec.parameters_schema or {}).get("properties") or {}
        workspace_root = self.workspace.root.resolve()
        run_record = self.project_workspace.store.get_run(self.run_id)
        active_turn_id = run_record.active_turn_id if run_record else None
        for name, field_schema in properties.items():
            if not isinstance(field_schema, dict):
                continue
            role = str(field_schema.get("x-pertura-asset-role") or "")
            if not role or name not in normalized:
                continue
            value = normalized.get(name)
            if value is None:
                # Optional asset-valued parameters are absent, not explicit
                # JSON nulls.  Preserve compatibility with callers that omit
                # them while still rejecting null for a required parameter in
                # the normal schema-validation step.
                normalized.pop(name, None)
                continue
            values = value if isinstance(value, list) else [value]
            if not values:
                continue
            normalized_values: list[Any] = []
            for item in values:
                if not isinstance(item, str) or not item:
                    normalized_values.append(item)
                    continue
                if item.startswith("asset_"):
                    normalized_values.append(item)
                    continue
                candidate = Path(item).expanduser()
                resolved = (
                    candidate.resolve()
                    if candidate.is_absolute()
                    else (workspace_root / candidate).resolve()
                )
                try:
                    resolved.relative_to(workspace_root)
                except ValueError as exc:
                    raise CapabilityParameterError(
                        f"external path for {name} is not registered: {resolved}"
                    ) from exc
                if resolved == workspace_root:
                    raise CapabilityParameterError(
                        f"asset parameter {name} must name a workspace artifact"
                    )
                if not resolved.exists():
                    raise CapabilityParameterError(
                        f"workspace asset parameter {name} is missing: {resolved}"
                    )
                asset = self.asset_registry.register(
                    resolved,
                    role=role,
                    kind="derived",
                    source_class="derived_artifact",
                    created_by_turn=active_turn_id,
                )
                self.project_workspace.store.put_asset_binding(
                    AssetBinding(
                        run_id=self.run_id,
                        asset_id=asset.asset_id,
                        role=asset.role,
                    )
                )
                normalized_values.append(asset.asset_id)
            normalized[name] = (
                normalized_values if isinstance(value, list) else normalized_values[0]
            )
        return normalized

    def resolve_result_for_turn(self, result_id: str) -> dict[str, Any] | None:
        """Return policy-derived rendering metadata for one committed result."""

        for item in self._committed_entries():
            raw = item.get("result") or {}
            if raw.get("result_id") != result_id:
                continue
            result = ResultEnvelope.model_validate(raw)
            payload = dict(raw)
            payload["verification_state"] = item.get("verification_state")
            payload["rendering_role"] = {
                SourceClass.measured_result: "measured",
                SourceClass.prediction: "prediction",
                SourceClass.curated_prior: "prior",
                SourceClass.observed_metadata: "derived",
                SourceClass.hypothesis: "hypothesis",
            }[result.source_class]
            if result.source_class != SourceClass.measured_result:
                payload["rendering_ceiling"] = result.source_class.value
                return payload
            try:
                spec = self.registry.get(result.capability_id, result.capability_version)
            except (KeyError, ValueError):
                payload["rendering_ceiling"] = "exploratory_measured"
                payload["promotion_reasons"] = ["capability spec is unavailable"]
                return payload
            statement = ScientificStatement(
                run_id=self.run_id,
                text="Structured measured-result rendering request.",
                source_class=SourceClass.measured_result,
                scope=result.scope,
                result_ids=(result.result_id,),
                requested_strength="measured_association",
                limitations=result.cautions,
            )
            receipt = (
                RunReceipt.model_validate(item["receipt"])
                if item.get("verification_state") == "trusted_receipt" and item.get("receipt")
                else None
            )
            session = item.get("authority_session") or {}
            decision = decide_promotion(
                statement,
                results=(result,),
                receipts=(receipt,) if receipt else (),
                capability_specs=(spec,),
                authoritative_public_key=str(session.get("public_key") or ""),
                policy=self.promotion_policy,
            )
            payload["rendering_ceiling"] = (
                "strong_measured" if decision.status == "promoted" else "exploratory_measured"
            )
            payload["promotion_reasons"] = list(decision.reasons)
            return payload
        return None

    def read_authority_projection(self, run_id: str | None = None) -> dict[str, Any]:
        selected_run = run_id or self.run_id
        database = self.authority_dir / "authority.sqlite3"
        if not database.is_file():
            return {
                "schema_version": "pertura-run-aggregate-v1",
                "run_id": selected_run,
                "sessions": (),
                "committed": (),
                "aggregate_digest": None,
                "legacy_unverified_result_ids": (),
                "invalid_session_ids": (),
            }
        return AuthoritySessionStore(database, read_only=True).project_run(
            selected_run,
            expected_policy_hash=self.promotion_policy.policy_hash,
        ).to_dict()

    def read_authority_events(
        self, run_id: str | None = None, *, after: int = 0
    ) -> list[dict[str, Any]]:
        selected_run = run_id or self.run_id
        database = self.authority_dir / "authority.sqlite3"
        if not database.is_file():
            return []
        return AuthoritySessionStore(database, read_only=True).list_events(
            selected_run, after=after
        )

    def _committed_entries(self) -> list[dict[str, Any]]:
        if self._started:
            return list(self.broker.list_committed(self.run_id))
        database = self.authority_dir / "authority.sqlite3"
        if not database.is_file():
            return []
        projection = AuthoritySessionStore(database, read_only=True).project_run(
            self.run_id,
            expected_policy_hash=self.promotion_policy.policy_hash,
        )
        return list(projection.committed)

    def _resolution_material(
        self,
    ) -> tuple[list[dict[str, Any]], tuple[ResultEnvelope, ...], frozenset[str]]:
        entries = self._committed_entries()
        current_session_id = self.broker.session_id if self._started else None
        results: list[ResultEnvelope] = []
        trusted: set[str] = set()
        for item in entries:
            session = item.get("authority_session") or {}
            session_id = session.get("session_id")
            state = str(item.get("verification_state") or "")
            is_current_open = bool(
                current_session_id and session_id == current_session_id
            )
            if state not in {"trusted_receipt", "validated_untrusted"} and not is_current_open:
                continue
            result = ResultEnvelope.model_validate(item["result"])
            results.append(result)
            if state == "trusted_receipt" or (is_current_open and item.get("receipt")):
                trusted.add(result.result_id)
        return entries, tuple(results), frozenset(trusted)


    def _refresh_asset_state(self) -> None:
        if self.asset_registry is None:
            return
        for asset in self.asset_registry.doctor_all():
            if asset.status == "current":
                continue
            drift_hash = canonical_hash({
                "asset_id": asset.asset_id,
                "status": asset.status,
            })
            self.broker.register_runtime_object(
                kind="data_asset",
                object_id=asset.asset_id,
                object_hash=drift_hash,
                payload=asset.model_dump(mode="json"),
            )

    def _persist_contract(self, contract: DatasetContract) -> None:
        self._contracts[contract.contract_id] = contract
        directory = self.workspace.artifacts_dir / "contracts"
        directory.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(contract.model_dump(mode="json"), indent=2, sort_keys=True) + "\n"
        (directory / f"{contract.contract_id}.json").write_text(payload, encoding="utf-8")
        (self.workspace.artifacts_dir / "dataset_contract.latest.json").write_text(payload, encoding="utf-8")
        if self.project_workspace is not None:
            run = self.project_workspace.store.get_run(self.run_id)
            if run is not None:
                self.project_workspace.store.put_run(
                    run.model_copy(update={"contract_id": contract.contract_id})
                )

    def _get_contract(self, contract_id: str | None) -> DatasetContract:
        if contract_id and contract_id in self._contracts:
            return self._contracts[contract_id]
        if contract_id:
            path = self.workspace.artifacts_dir / "contracts" / f"{contract_id}.json"
        else:
            path = self.workspace.artifacts_dir / "dataset_contract.latest.json"
        if not path.exists():
            raise ValueError("no DatasetContract exists; call inspect_dataset first")
        contract = DatasetContract.model_validate_json(path.read_text(encoding="utf-8"))
        self._contracts[contract.contract_id] = contract
        return contract


def _implicit_project_dataset_source(root: Path) -> Path | None:
    """Return an unambiguous dataset inside a newly initialized project root."""

    root = Path(root).resolve()
    names = {item.name.lower() for item in root.iterdir()}
    cell_ranger_members = {"filtered_feature_bc_matrix", "raw_feature_bc_matrix"}
    mex_matrix = {"matrix.mtx", "matrix.mtx.gz"}
    mex_barcodes = {"barcodes.tsv", "barcodes.tsv.gz"}
    mex_features = {"features.tsv", "features.tsv.gz", "genes.tsv", "genes.tsv.gz"}
    if names & cell_ranger_members or (
        names & mex_matrix and names & mex_barcodes and names & mex_features
    ):
        return root

    files = [item for item in root.iterdir() if item.is_file()]
    for suffixes in (
        {".h5ad", ".h5mu", ".mudata", ".h5", ".hdf5"},
        {".csv", ".tsv", ".txt"},
    ):
        candidates = sorted(item for item in files if item.suffix.lower() in suffixes)
        if len(candidates) == 1:
            return candidates[0]
        if len(candidates) > 1:
            rendered = ", ".join(item.name for item in candidates)
            raise ValueError(
                f"multiple dataset candidates were found in the project ({rendered}); inspect an explicit dataset path"
            )
    return None


def _read_manifest_provenance(workspace_root: Path) -> dict[str, Any]:
    path = workspace_root / "manifest.json"
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    return {
        "agent_provider": manifest.get("agent_provider"),
        "skill_bundle_hash": manifest.get("skill_bundle_hash"),
        "additional_skill_plugin_hashes": manifest.get("additional_skill_plugin_hashes") or [],
        "provider_configuration_hash": manifest.get("provider_configuration_hash"),
    }


def _bind_workspace_policy(
    workspace: ClaudeRunWorkspace,
    requested: PromotionPolicy | None,
) -> PromotionPolicy:
    manifest_path = workspace.root / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        manifest = {}
    except json.JSONDecodeError as exc:
        raise ValueError("workspace manifest is not valid JSON") from exc

    stored = manifest.get("trust_policy")
    if stored is not None and not isinstance(stored, dict):
        raise ValueError("workspace trust_policy must be an object")
    bound: PromotionPolicy
    if stored is not None:
        payload = stored.get("payload")
        if payload is not None:
            if not isinstance(payload, dict):
                raise ValueError("workspace trust_policy payload must be an object")
            normalized = dict(payload)
            if "required_measured_dependency_kinds" in normalized:
                normalized["required_measured_dependency_kinds"] = tuple(
                    normalized["required_measured_dependency_kinds"]
                )
            try:
                recorded = PromotionPolicy(**normalized)
            except (TypeError, ValueError) as exc:
                raise ValueError("workspace trust_policy payload is invalid") from exc
        else:
            profile = stored.get("profile")
            if not isinstance(profile, str) or not profile:
                raise ValueError("workspace trust_policy profile is missing")
            recorded = PromotionPolicy(profile=profile)
        if stored.get("profile") not in {None, recorded.profile}:
            raise ValueError("workspace trust_policy profile does not match its payload")
        if stored.get("version") not in {None, recorded.version}:
            raise ValueError("workspace trust_policy version does not match its payload")
        if stored.get("policy_hash") != recorded.policy_hash:
            raise ValueError("workspace trust_policy hash does not match its payload")
        if requested is not None and requested.policy_hash != recorded.policy_hash:
            raise ValueError("requested promotion policy conflicts with the workspace-bound policy")
        bound = requested or recorded
    else:
        bound = requested or PromotionPolicy()

    workspace.update_manifest(
        {
            "trust_policy": {
                "schema_version": "pertura-promotion-policy-binding-v1",
                "profile": bound.profile,
                "version": bound.version,
                "policy_hash": bound.policy_hash,
                "payload": asdict(bound),
                "selected_by": "runtime",
            }
        }
    )
    return bound


def _blocked_runtime_response(
    *,
    capability_id: str | None,
    scope_id: str | None,
    blockers: tuple[str, ...] | list[str],
    required_upstream: tuple[str, ...] | list[str],
    summary: str,
    ambiguous_result_ids: tuple[str, ...] | list[str] = (),
    candidate_result_ids: tuple[str, ...] | list[str] = (),
    dependency_verdicts: tuple[dict[str, Any], ...] | list[dict[str, Any]] = (),
    trust_level: str | None = None,
    validation_status: str | None = None,
    plan: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = {
        "result_id": None,
        "receipt_id": None,
        "status": "blocked",
        "blockers": list(blockers),
        "cautions": [],
        "summary": summary,
        "output_paths": [],
        "scope_id": scope_id,
        "trust_level": trust_level,
        "validation_status": validation_status,
        "selected_capability": capability_id,
        "required_upstream": list(required_upstream),
        "ambiguous_result_ids": list(ambiguous_result_ids),
        "candidate_result_ids": list(candidate_result_ids),
        "dependency_verdicts": [dict(item) for item in dependency_verdicts],
    }
    if plan is not None:
        payload["plan"] = plan
    return payload



def _contract_summary(contract: DatasetContract, workspace: ClaudeRunWorkspace) -> dict[str, Any]:
    return {
        "contract_id": contract.contract_id,
        "contract_hash": contract.canonical_hash,
        "dataset_id": contract.dataset_id,
        "format": contract.input_format,
        "version": contract.contract_version,
        "unresolved_fields": list(contract.unresolved_fields),
        "identity_status": {key: value.get("status") for key, value in contract.identity_fields.items()},
        "contract_path": str((workspace.artifacts_dir / "contracts" / f"{contract.contract_id}.json").relative_to(workspace.root)),
        "recommended_next_capabilities": [
            "intake.materialize.v1",
            "diagnostic.dataset_integrity.v1",
            "diagnostic.design_balance.v1",
        ],
    }



def _authority_dir(run_id: str, workspace_root: Path) -> Path:
    override = os.environ.get("PERTURA_AUTHORITY_ROOT")
    if override:
        base = Path(override).expanduser()
    elif os.name == "nt" and os.environ.get("LOCALAPPDATA"):
        base = Path(os.environ["LOCALAPPDATA"]) / "Pertura" / "authority"
    else:
        base = Path.home() / ".local" / "share" / "pertura" / "authority"
    namespace = hashlib.sha256(str(workspace_root.resolve()).encode("utf-8")).hexdigest()[:12]
    return (base / f"{run_id}-{namespace}").resolve()
