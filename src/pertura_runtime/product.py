from __future__ import annotations

import json
import os
import hashlib
from dataclasses import asdict
from pathlib import Path
from typing import Any

from pertura_core import CapabilityRunRequest, DatasetContract, DependencyRef, DesignConfirmation, PromotionPolicy, ResultEnvelope, RunReceipt, ScientificStatement, ScopeKey, SourceClass, decide_promotion
from pertura_runtime.claude.workspace import ClaudeRunWorkspace
from pertura_runtime.verifier import AuthoritySessionStore, VerifierBroker
from pertura_runtime.verifier.broker import VerifierBrokerError
from pertura_workflow.capabilities import CapabilityRegistry
from pertura_workflow.intake import contract_with_confirmations, inspect_dataset_path
from pertura_workflow.environment import environment_lock
from pertura_workflow.planner import (
    plan_analysis,
    plan_requested_capability,
    resolve_dependencies,
)


class PerturaProductRuntime:
    """One run's capability registry, verifier lifecycle and compact product API."""

    def __init__(
        self,
        workspace: ClaudeRunWorkspace,
        *,
        policy: PromotionPolicy | None = None,
        policy_profile: str | None = None,
    ) -> None:
        self.workspace = workspace
        if policy is not None and policy_profile is not None and policy.profile != policy_profile:
            raise ValueError("policy instance and policy_profile select different policies")
        requested_policy = policy or (
            PromotionPolicy(profile=policy_profile) if policy_profile is not None else None
        )
        self.promotion_policy = _bind_workspace_policy(workspace, requested_policy)
        self.registry = CapabilityRegistry.load_default(include_external=False)
        self.authority_dir = _authority_dir(workspace.root.name, workspace.root)
        self._broker = VerifierBroker(
            authority_dir=self.authority_dir,
            run_id=workspace.root.name,
            policy_hash=self.promotion_policy.policy_hash,
            export_dir=workspace.artifacts_dir / "verified",
            output_root=workspace.outputs_dir / "capabilities",
            workspace_root=workspace.root,
        )
        self._started = False
        self._contracts: dict[str, DatasetContract] = {}

    @property
    def broker(self) -> VerifierBroker:
        if not self._started:
            self._broker.start()
            self._started = True
        return self._broker

    @property
    def started(self) -> bool:
        return self._started

    def close(self, *, graceful: bool = True) -> None:
        if self._started:
            self._broker.stop(graceful=graceful)
            self._started = False

    def inspect_dataset(self, path: str | Path | None = None, *, dataset_id: str | None = None, confirmations: dict[str, Any] | None = None) -> dict[str, Any]:
        source = Path(path).expanduser().resolve() if path else self.workspace.input_source
        if source is None:
            raise ValueError("dataset path is required when the run has no input_source")
        contract = inspect_dataset_path(source, dataset_id=dataset_id)
        if confirmations:
            contract = contract_with_confirmations(contract, confirmations)
        self._persist_contract(contract)
        self.broker.register_contract(contract)
        return _contract_summary(contract, self.workspace)

    def confirm_design(self, contract_id: str, confirmations: dict[str, Any]) -> dict[str, Any]:
        contract = self._get_contract(contract_id)
        revised = contract_with_confirmations(contract, confirmations)
        self._persist_contract(revised)
        self.broker.register_contract(revised)
        confirmation_ids = []
        for field, value in confirmations.items():
            confirmation = DesignConfirmation(
                run_id=self.workspace.root.name,
                contract_id=contract.contract_id,
                field=field,
                value=value,
                rationale="Confirmed through the dashboard/design interface.",
            )
            self.broker.record_confirmation(confirmation)
            confirmation_ids.append(confirmation.confirmation_id)
        return _contract_summary(revised, self.workspace) | {"confirmation_ids": confirmation_ids}

    def run_diagnostic(self, capability_id: str, *, contract_id: str | None = None, scope: dict[str, Any] | None = None, parameters: dict[str, Any] | None = None, dependencies: list[dict[str, Any]] | None = None) -> dict[str, Any]:
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

    def run_analysis(self, objective: str, *, capability_id: str | None = None, contract_id: str | None = None, scope: dict[str, Any] | None = None, parameters: dict[str, Any] | None = None, dependencies: list[dict[str, Any]] | None = None) -> dict[str, Any]:
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



    def evaluate_virtual_model(self, *, capability_id: str = "virtual.evaluate.v1", contract_id: str | None = None, scope: dict[str, Any] | None = None, parameters: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._run(capability_id, kind="virtual", contract_id=contract_id, scope=scope, parameters=parameters)

    def finalize_report(self, run_id: str | None = None) -> dict[str, Any]:
        selected_run = run_id or self.workspace.root.name
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
        payload = {
            "schema_version": "pertura-capability-report-v2",
            "run_id": selected_run,
            "result_count": len(results),
            "results": results,
            "trusted_results": trusted_results,
            "exploratory_results": exploratory_results,
            "unverified_results": unverified_results,
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
        for heading, collection, mode in (
            ("Verified analyses", trusted_results, "trusted"),
            ("Exploratory candidate analyses", exploratory_results, "candidate"),
            ("Unverified historical/session analyses", unverified_results, "unverified"),
        ):
            if not collection:
                continue
            lines.extend([f"## {heading}", ""])
            if mode == "candidate":
                lines.extend([
                    "These results passed their capability validator but have no trusted receipt.",
                    "They may guide exploration and cannot support strong measured claims.",
                    "",
                ])
            elif mode == "unverified":
                lines.extend([
                    "These results belong to a legacy, aborted, unsealed, or invalid authority session.",
                    "They remain viewable and cannot support strong measured claims.",
                    "",
                ])
            for result in collection:
                prefix = "Candidate analysis indicates: " if mode == "candidate" else ""
                state = verification_by_result[result["result_id"]]
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
        return {
            "run_id": selected_run,
            "status": final_status,
            "result_count": len(results),
            "report_paths": [str(report_md.relative_to(self.workspace.root)), str(report_json.relative_to(self.workspace.root))],
            "root_digest": seal.get("root_digest") if seal and verified_result_count else None,
            "promotion_decision_count": len(decisions),
        }

    def capability_list(
        self, *, kind: str | None = None, include_deprecated: bool = False
    ) -> list[dict[str, Any]]:
        return [item.to_dict() for item in self.registry.list(
            kind=kind, include_deprecated=include_deprecated)]

    def _run(self, capability_id: str, *, kind: str, contract_id: str | None, scope: dict[str, Any] | None, parameters: dict[str, Any] | None, dependencies: list[dict[str, Any]] | None = None, objective: str | None = None) -> dict[str, Any]:
        spec = self.registry.get(capability_id)
        if spec.kind != kind:
            raise ValueError(f"{capability_id} is a {spec.kind} capability, not {kind}")
        contract = self._get_contract(contract_id)
        scope_key = ScopeKey.model_validate(scope) if scope else ScopeKey(dataset_id=contract.dataset_id, unresolved_fields=tuple(field for field in contract.unresolved_fields if field in {"control", "replicate", "state_label"}))
        raw_hints = [dict(item) for item in (dependencies or [])]
        result_hints: list[dict[str, Any]] = []
        environment_hints: list[dict[str, Any]] = []
        for hint in raw_hints:
            hint_kind = str(hint.get("kind") or "")
            if hint_kind == "contract":
                if hint.get("object_id") not in {None, "", contract.contract_id}:
                    raise ValueError("caller-supplied contract dependency references another contract")
                if hint.get("object_hash") not in {None, "", contract.canonical_hash}:
                    raise ValueError("caller-supplied contract dependency hash is forged or stale")
            elif hint_kind == "environment":
                environment_hints.append(hint)
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
        request = CapabilityRunRequest(
            run_id=self.workspace.root.name,
            capability_id=spec.capability_id,
            capability_version=spec.version,
            contract_id=contract.contract_id,
            contract_hash=contract.canonical_hash,
            scope=scope_key,
            objective=objective,
            parameters=parameters or {},
            dependencies=tuple(explicit_dependencies),
        )
        response = self.broker.run(request)
        result = response["result"]
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
        }
        if kind == "virtual":
            compact["not_implemented_capabilities"] = [
                item.capability_id for item in self.registry.list(kind="virtual") if not item.implemented
            ]
        return compact

    def read_authority_projection(self, run_id: str | None = None) -> dict[str, Any]:
        selected_run = run_id or self.workspace.root.name
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
        selected_run = run_id or self.workspace.root.name
        database = self.authority_dir / "authority.sqlite3"
        if not database.is_file():
            return []
        return AuthoritySessionStore(database, read_only=True).list_events(
            selected_run, after=after
        )

    def _committed_entries(self) -> list[dict[str, Any]]:
        if self._started:
            return list(self.broker.list_committed(self.workspace.root.name))
        database = self.authority_dir / "authority.sqlite3"
        if not database.is_file():
            return []
        projection = AuthoritySessionStore(database, read_only=True).project_run(
            self.workspace.root.name,
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


    def _persist_contract(self, contract: DatasetContract) -> None:
        self._contracts[contract.contract_id] = contract
        directory = self.workspace.artifacts_dir / "contracts"
        directory.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(contract.model_dump(mode="json"), indent=2, sort_keys=True) + "\n"
        (directory / f"{contract.contract_id}.json").write_text(payload, encoding="utf-8")
        (self.workspace.artifacts_dir / "dataset_contract.latest.json").write_text(payload, encoding="utf-8")

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
