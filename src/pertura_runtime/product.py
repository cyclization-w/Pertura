from __future__ import annotations

import json
import os
import hashlib
from pathlib import Path
from typing import Any

from pertura_core import CapabilityRunRequest, DatasetContract, DependencyRef, DesignConfirmation, ResultEnvelope, RunReceipt, ScientificStatement, ScopeKey, SourceClass
from pertura_gate.promotion import PromotionPolicy, decide_promotion
from pertura_runtime.claude.workspace import ClaudeRunWorkspace
from pertura_runtime.verifier import VerifierBroker
from pertura_workflow.capabilities import CapabilityRegistry
from pertura_workflow.intake import contract_with_confirmations, inspect_dataset_path
from pertura_workflow.environment import environment_lock


class PerturaProductRuntime:
    """One run's capability registry, verifier lifecycle and compact product API."""

    def __init__(self, workspace: ClaudeRunWorkspace, *, policy_profile: str = "strict") -> None:
        self.workspace = workspace
        self.registry = CapabilityRegistry.load_default(include_external=True)
        self.promotion_policy = PromotionPolicy(profile=policy_profile)
        self._broker = VerifierBroker(
            authority_dir=_authority_dir(workspace.root.name, workspace.root),
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
        return self._run(capability_id, kind="diagnostic", contract_id=contract_id, scope=scope, parameters=parameters, dependencies=dependencies)

    def run_analysis(self, objective: str, *, capability_id: str | None = None, contract_id: str | None = None, scope: dict[str, Any] | None = None, parameters: dict[str, Any] | None = None, dependencies: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        selected = capability_id or _analysis_capability_for_objective(objective)
        return self._run(selected, kind="analysis", contract_id=contract_id, scope=scope, parameters=parameters, dependencies=dependencies, objective=objective)

    def evaluate_virtual_model(self, *, capability_id: str = "virtual.evaluate.v1", contract_id: str | None = None, scope: dict[str, Any] | None = None, parameters: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._run(capability_id, kind="virtual", contract_id=contract_id, scope=scope, parameters=parameters)

    def finalize_report(self, run_id: str | None = None) -> dict[str, Any]:
        selected_run = run_id or self.workspace.root.name
        if not self._started:
            results: list[dict[str, Any]] = []
            seal = None
        else:
            committed = self.broker.list_committed(selected_run)
            results = [item["result"] for item in committed]
            seal = self.broker.seal_run(selected_run)
        if not self._started:
            committed = []
        result_models = [ResultEnvelope.model_validate(item["result"]) for item in committed]
        receipt_models = [RunReceipt.model_validate(item["receipt"]) for item in committed if item.get("receipt")]
        decisions = []
        statements = []
        specs = [self.registry.get(result.capability_id, result.capability_version) for result in result_models]
        for result, spec in zip(result_models, specs):
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
            decision = decide_promotion(
                statement,
                results=result_models,
                receipts=receipt_models,
                capability_specs=specs,
                authoritative_public_key=self.broker.public_key,
                policy=self.promotion_policy,
            )
            self.broker.commit_promotion(statement, decision)
            statements.append(statement.model_dump(mode="json"))
            decisions.append(decision.model_dump(mode="json"))
        report_json = self.workspace.reports_dir / "capability_report.json"
        report_md = self.workspace.reports_dir / "capability_report.md"
        payload = {
            "schema_version": "pertura-capability-report-v2",
            "run_id": selected_run,
            "result_count": len(results),
            "results": results,
            "trusted_results": [item for item in results if item.get("capability_trust") == "builtin_trusted"],
            "exploratory_results": [item for item in results if item.get("capability_trust") != "builtin_trusted"],
            "statements": statements,
            "promotion_decisions": decisions,
            "run_seal": seal,
            "policy_hash": self.promotion_policy.policy_hash,
        }
        report_json.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        lines = ["# Pertura capability report", "", f"Run: `{selected_run}`", ""]
        if not results:
            lines.append("No committed capability results are available.")
        trusted_results = [
            item for item in results if item.get("capability_trust") == "builtin_trusted"
        ]
        exploratory_results = [
            item for item in results if item.get("capability_trust") != "builtin_trusted"
        ]
        for heading, collection, exploratory in (
            ("Verified analyses", trusted_results, False),
            ("Exploratory candidate analyses", exploratory_results, True),
        ):
            if not collection:
                continue
            lines.extend([f"## {heading}", ""])
            if exploratory:
                lines.extend([
                    "These results passed their capability validator but have no trusted receipt.",
                    "They may guide exploration and cannot support strong measured claims.",
                    "",
                ])
            for result in collection:
                lines.extend([
                    f"### {result['capability_id']}",
                    "",
                    f"Status: {result['status']}  ",
                    f"Result: {result['result_id']}  ",
                    (
                        "Candidate analysis indicates: " + result["summary"]
                        if exploratory else result["summary"]
                    ),
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
            "status": "completed" if seal else "untrusted_no_verifier_results",
            "result_count": len(results),
            "report_paths": [str(report_md.relative_to(self.workspace.root)), str(report_json.relative_to(self.workspace.root))],
            "root_digest": seal.get("root_digest") if seal else None,
            "promotion_decision_count": len(decisions),
        }

    def capability_list(self, *, kind: str | None = None) -> list[dict[str, Any]]:
        return [item.to_dict() for item in self.registry.list(kind=kind)]

    def _run(self, capability_id: str, *, kind: str, contract_id: str | None, scope: dict[str, Any] | None, parameters: dict[str, Any] | None, dependencies: list[dict[str, Any]] | None = None, objective: str | None = None) -> dict[str, Any]:
        spec = self.registry.get(capability_id)
        if spec.kind != kind:
            raise ValueError(f"{capability_id} is a {spec.kind} capability, not {kind}")
        contract = self._get_contract(contract_id)
        scope_key = ScopeKey.model_validate(scope) if scope else ScopeKey(dataset_id=contract.dataset_id, unresolved_fields=tuple(field for field in contract.unresolved_fields if field in {"control", "replicate", "state_label"}))
        explicit_dependencies = [DependencyRef.model_validate(item) for item in dependencies or []]
        environment_profile = str(spec.metadata.get("environment_profile") or "")
        if environment_profile:
            try:
                lock = environment_lock(environment_profile)
            except RuntimeError:
                lock = None
            if lock:
                object_id = f"environment:{environment_profile}"
                self.broker.register_runtime_object(
                    kind="environment",
                    object_id=object_id,
                    object_hash=lock["lock_hash"],
                    payload=lock,
                )
                existing_environment = [item for item in explicit_dependencies if item.kind == "environment" and item.object_id == object_id]
                if existing_environment and any(item.object_hash != lock["lock_hash"] for item in existing_environment):
                    raise ValueError("caller-supplied environment dependency does not match the authoritative local lock")
                if not existing_environment:
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


def _analysis_capability_for_objective(objective: str) -> str:
    normalized = objective.strip().lower().replace("-", " ")
    if "sceptre" in normalized or "high moi" in normalized:
        return "association.sceptre.v1"
    if "composition" in normalized or "proportion" in normalized:
        return "composition.propeller.v1"
    if "state map" in normalized or "map state" in normalized:
        return "state.reference.map_knn.v1"
    if "state" in normalized or "cluster" in normalized or "reference" in normalized:
        return "state.reference.fit.v1"
    if "module" in normalized or "nmf" in normalized:
        return "module.learn.control_nmf.v1"
    if "guide sensitivity" in normalized or "leave one guide" in normalized:
        return "effect.guide_target_sensitivity.v1"
    if "module effect" in normalized or "global effect" in normalized:
        return "effect.module_global.v1"
    if "null calibration" in normalized:
        return "calibration.method_null.v1"
    if any(token in normalized for token in ("differential", "de ", "pseudobulk", "effect", "association")):
        return "de.pseudobulk.edger.v1"
    raise ValueError("objective does not map to an implemented analysis capability; inspect capabilities first")

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
