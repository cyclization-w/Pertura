from __future__ import annotations

import json
import multiprocessing
import os
import secrets
import socket
import shutil
import sqlite3
import tempfile
import time
from multiprocessing.connection import Client, Listener
from pathlib import Path
from typing import Any
from uuid import uuid4

from pertura_core import CapabilityRunRequest, CapabilityTrust, DatasetContract, DesignConfirmation, PromotionDecision, ResultEnvelope, ScientificStatement
from pertura_core.hashing import canonical_json, file_sha256
from pertura_runtime.verifier.receipts import ReceiptSigner, verify_receipt
from pertura_runtime.verifier.session_store import AuthoritySessionStore
from pertura_runtime.verifier.store import AuthorityStore


class VerifierBrokerError(RuntimeError):
    pass


class VerifierBroker:
    """Lifecycle wrapper for the local, separately keyed verifier process."""

    def __init__(self, *, authority_dir: str | Path, policy_hash: str, run_id: str | None = None, export_dir: str | Path | None = None, output_root: str | Path | None = None, workspace_root: str | Path | None = None) -> None:
        self.authority_dir = Path(authority_dir).resolve()
        self.policy_hash = policy_hash
        self.run_id = run_id
        self.export_dir = Path(export_dir).resolve() if export_dir else None
        self.output_root = Path(output_root).resolve() if output_root else (self.authority_dir / "published")
        self.workspace_root = Path(workspace_root).resolve() if workspace_root else None
        self._authkey = secrets.token_bytes(32)
        self._address, self._family = _new_address(self.authority_dir)
        self._process: multiprocessing.Process | None = None
        self._public_key: str | None = None
        self._instance_id: str | None = None
        self._session_id: str | None = None
        self._launch_instance_id: str | None = None

    @property
    def public_key(self) -> str:
        if not self._public_key:
            raise VerifierBrokerError("verifier broker is not running")
        return self._public_key

    @property
    def instance_id(self) -> str:
        if not self._instance_id:
            raise VerifierBrokerError("verifier broker is not running")
        return self._instance_id

    @property
    def session_id(self) -> str:
        if not self._session_id:
            raise VerifierBrokerError("verifier broker has not opened an authority session")
        return self._session_id

    def start(self, *, timeout: float = 15.0) -> "VerifierBroker":
        if self._process:
            if self._process.is_alive():
                return self
            self._abort_owned_sessions(reason="broker_dead_before_restart")
            self._reset_client_state()
        self.authority_dir.mkdir(parents=True, exist_ok=True)
        self._launch_instance_id = f"broker_{uuid4().hex}"
        self._spawn_process()
        deadline = time.monotonic() + timeout
        last_error: Exception | None = None
        attempted_pipe_fallback = False
        while time.monotonic() < deadline:
            if not self._process or not self._process.is_alive():
                exitcode = self._process.exitcode if self._process else None
                self._abort_owned_sessions(reason="broker_exited_during_startup")
                if self._family == "AF_PIPE" and not attempted_pipe_fallback:
                    attempted_pipe_fallback = True
                    self._address, self._family = _new_loopback_address()
                    self._spawn_process()
                    continue
                self._reset_client_state()
                raise VerifierBrokerError(f"verifier broker exited during startup ({exitcode})")
            try:
                response = self._call({"action": "ping"})
                self._public_key = response["public_key"]
                self._instance_id = response["broker_instance_id"]
                self._session_id = response.get("session_id")
                return self
            except (OSError, EOFError, ConnectionError) as exc:
                last_error = exc
                time.sleep(0.05)
        if self._process and self._process.is_alive():
            self._process.terminate()
            self._process.join(timeout=2)
        self._abort_owned_sessions(reason="broker_start_timeout")
        self._reset_client_state()
        raise VerifierBrokerError(f"verifier broker did not start: {last_error}")

    def register_contract(self, contract: DatasetContract) -> None:
        self._require_alive()
        self._call({"action": "register_contract", "contract": contract.model_dump(mode="json")})

    def register_runtime_object(self, *, kind: str, object_id: str, object_hash: str, payload: dict[str, Any]) -> int:
        self._require_alive()
        response = self._call({
            "action": "register_runtime_object",
            "kind": kind,
            "object_id": object_id,
            "object_hash": object_hash,
            "payload": payload,
        })
        return int(response["stale_results"])

    def run(self, request: CapabilityRunRequest) -> dict[str, Any]:
        self._require_alive()
        response = self._call({"action": "run", "request": request.model_dump(mode="json")})
        result = response["result"]
        receipt = response.get("receipt")
        authority_session = response.get("authority_session") or {}
        if authority_session.get("session_id"):
            self._session_id = authority_session["session_id"]
        if receipt and not verify_receipt(
            receipt,
            authoritative_public_key=authority_session.get("public_key") or self.public_key,
            expected_policy_hash=self.policy_hash,
        ):
            raise VerifierBrokerError("broker returned an invalid receipt")
        if self.export_dir:
            self._export_projection(result, receipt)
        return response

    def list_results(self, run_id: str) -> list[dict[str, Any]]:
        self._require_alive()
        return self._call({"action": "list_results", "run_id": run_id})["results"]

    def list_committed(self, run_id: str) -> list[dict[str, Any]]:
        self._require_alive()
        return self._call({"action": "list_committed", "run_id": run_id})["committed"]

    def commit_promotion(self, statement: ScientificStatement, decision: PromotionDecision) -> None:
        self._require_alive()
        self._call({
            "action": "commit_promotion",
            "statement": statement.model_dump(mode="json"),
            "decision": decision.model_dump(mode="json"),
        })

    def record_confirmation(self, confirmation: DesignConfirmation) -> None:
        self._require_alive()
        self._call({"action": "record_confirmation", "confirmation": confirmation.model_dump(mode="json")})

    def list_events(self, run_id: str, *, after: int = 0) -> list[dict[str, Any]]:
        self._require_alive()
        return self._call({"action": "list_events", "run_id": run_id, "after": after})["events"]

    def seal_run(self, run_id: str) -> dict[str, Any]:
        self._require_alive()
        return self._call({"action": "seal_run", "run_id": run_id})

    def stop(self, *, graceful: bool = True) -> None:
        if not self._process:
            return
        delivered = False
        if self._process.is_alive():
            try:
                self._call({"action": "stop" if graceful else "abort"})
                delivered = True
            except (OSError, EOFError, ConnectionError):
                pass
        self._process.join(timeout=5)
        if self._process.is_alive():
            self._process.terminate()
            self._process.join(timeout=2)
        if not delivered:
            self._abort_owned_sessions(
                reason="broker_dead_before_stop" if graceful else "broker_aborted_after_disconnect"
            )
        self._reset_client_state()

    def __enter__(self) -> "VerifierBroker":
        return self.start()

    def __exit__(self, exc_type, exc, tb) -> None:
        self.stop(graceful=exc is None)

    def _call(self, message: dict[str, Any]) -> dict[str, Any]:
        try:
            connection = Client(self._address, family=self._family, authkey=self._authkey)
            try:
                connection.send(message)
                response = connection.recv()
            finally:
                connection.close()
        except (OSError, EOFError, ConnectionError):
            if not self._process or not self._process.is_alive():
                self._abort_owned_sessions(reason="broker_died_during_request")
            raise
        if not response.get("ok"):
            raise VerifierBrokerError(str(response.get("error") or "verifier request failed"))
        return response

    def _require_alive(self) -> None:
        if not self._process or not self._process.is_alive():
            self._abort_owned_sessions(reason="broker_process_not_alive")
            raise VerifierBrokerError("verifier broker is unavailable; results remain untrusted")

    def _spawn_process(self) -> None:
        if not self._launch_instance_id:
            raise VerifierBrokerError("broker launch identity is missing")
        self._process = multiprocessing.Process(
            target=_serve,
            args=(
                self._address,
                self._family,
                self._authkey,
                str(self.authority_dir / "authority.sqlite3"),
                self.policy_hash,
                str(self.output_root),
                str(self.workspace_root) if self.workspace_root else None,
                self.run_id,
                self._launch_instance_id,
            ),
            name="pertura-verifier",
            daemon=True,
        )
        self._process.start()

    def _abort_owned_sessions(self, *, reason: str) -> tuple[str, ...]:
        database = self.authority_dir / "authority.sqlite3"
        instance_id = self._launch_instance_id or self._instance_id
        if not database.is_file() or (not self.run_id and not instance_id):
            return ()
        try:
            return AuthoritySessionStore(database).abort_open_sessions(
                run_id=self.run_id,
                broker_instance_id=instance_id,
                reason=reason,
            )
        except (OSError, RuntimeError, ValueError, sqlite3.Error):
            return ()

    def _reset_client_state(self) -> None:
        self._process = None
        self._public_key = None
        self._instance_id = None
        self._session_id = None
        self._launch_instance_id = None

    def _export_projection(self, result: dict[str, Any], receipt: dict[str, Any] | None) -> None:
        self.export_dir.mkdir(parents=True, exist_ok=True)
        for prefix, payload in (("result", result), ("receipt", receipt)):
            if payload is None:
                continue
            object_id = payload.get(f"{prefix}_id") or payload.get("result_id")
            destination = self.export_dir / f"{prefix}_{object_id}.json"
            temporary = destination.with_suffix(".tmp")
            temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            temporary.replace(destination)
            try:
                destination.chmod(0o444)
            except OSError:
                pass


def _serve(address: Any, family: str, authkey: bytes, store_path: str, policy_hash: str, output_root: str, workspace_root: str | None, initial_run_id: str | None, instance_id: str) -> None:
    from pertura_workflow.capabilities import CapabilityRegistry
    from pertura_workflow.capabilities.executors import execute_capability

    # Receipts are within-run provenance. The signing authority is deliberately
    # ephemeral: no private key is persisted into a same-user readable path.
    signer = ReceiptSigner()
    store = AuthorityStore(store_path)
    session_store = AuthoritySessionStore(store_path)
    sessions: dict[str, str] = {}

    def ensure_session(run_id: str) -> str:
        current_id = sessions.get(run_id)
        current = session_store.get_session(current_id) if current_id else None
        if current is not None and current.status == "open":
            return current.session_id
        record = session_store.start_session(
            session_id=f"authority_session_{uuid4().hex}",
            run_id=run_id,
            broker_instance_id=instance_id,
            public_key=signer.public_key_b64,
            policy_hash=policy_hash,
        )
        sessions[run_id] = record.session_id
        return record.session_id

    registry = CapabilityRegistry.load_default(include_external=False)
    try:
        listener = Listener(address, family=family, authkey=authkey)
    except PermissionError:
        # The parent detects this clean exit and retries with authenticated
        # loopback on restricted Windows accounts. Avoid emitting a child
        # traceback for an expected platform fallback.
        if family == "AF_PIPE":
            return
        raise
    running = True
    try:
        while running:
            connection = listener.accept()
            try:
                message = connection.recv()
                action = message.get("action")
                if action == "ping":
                    ping_session_id = ensure_session(initial_run_id) if initial_run_id else None
                    response = {
                        "ok": True,
                        "public_key": signer.public_key_b64,
                        "broker_instance_id": instance_id,
                        "session_id": ping_session_id,
                    }
                elif action == "register_contract":
                    contract = DatasetContract.model_validate(message["contract"])
                    store.put_contract(contract)
                    response = {"ok": True, "contract_id": contract.contract_id}
                elif action == "register_runtime_object":
                    stale = store.put_runtime_object(
                        kind=str(message["kind"]),
                        object_id=str(message["object_id"]),
                        object_hash=str(message["object_hash"]),
                        payload=dict(message.get("payload") or {}),
                    )
                    response = {"ok": True, "stale_results": stale}
                elif action == "run":
                    request = CapabilityRunRequest.model_validate(message["request"])
                    session_id = ensure_session(request.run_id)
                    spec = registry.get(request.capability_id, request.capability_version)
                    contract = store.get_contract(request.contract_id)
                    if contract is None or contract.canonical_hash != request.contract_hash:
                        raise ValueError("unknown or stale dataset contract")
                    dependency_issues = store.validate_dependencies(request.dependencies)
                    if dependency_issues:
                        raise ValueError("invalid explicit dependencies: " + "; ".join(dependency_issues))
                    declared_issues = _validate_declared_dependencies(spec, request)
                    if declared_issues:
                        raise ValueError(
                            "missing declared scientific dependencies: "
                            + "; ".join(declared_issues)
                        )
                    existing = store.get_result_for_request(request.request_id)
                    if existing is not None:
                        receipt = store.get_receipt_for_result(existing.result_id)
                        historical_session = session_store.session_for_result(existing.result_id)
                        response = {
                            "ok": True,
                            "result": existing.model_dump(mode="json"),
                            "receipt": receipt.model_dump(mode="json") if receipt else None,
                            "replayed": True,
                            "authority_session": historical_session.to_dict() if historical_session else None,
                        }
                    else:
                        with tempfile.TemporaryDirectory(prefix="pertura-verify-") as staging:
                            _write_dependency_projection(store, request, Path(staging), Path(workspace_root) if workspace_root else None)
                            result = execute_capability(spec, request, contract, staging)
                            result = _publish_outputs(result, Path(staging), Path(output_root), Path(workspace_root) if workspace_root else None)
                        if spec.trust_level != CapabilityTrust.builtin_trusted:
                            result = _with_verification_state(result, "validated_untrusted")
                            store.commit_result(result, None)
                            session_store.bind_result(result_id=result.result_id, run_id=result.run_id, session_id=session_id)
                            authority_session = session_store.get_session(session_id)
                            response = {
                                "ok": True,
                                "result": result.model_dump(mode="json"),
                                "receipt": None,
                                "authority_session": authority_session.to_dict() if authority_session else None,
                                "replayed": False,
                            }
                        else:
                            receipt = signer.sign_result(
                                request,
                                result,
                                policy_hash=policy_hash,
                                broker_instance_id=instance_id,
                            )
                            store.commit_result(result, receipt)
                            session_store.bind_result(result_id=result.result_id, run_id=result.run_id, session_id=session_id)
                            authority_session = session_store.get_session(session_id)
                            response = {
                                "ok": True,
                                "result": result.model_dump(mode="json"),
                                "receipt": receipt.model_dump(mode="json"),
                                "authority_session": authority_session.to_dict() if authority_session else None,
                                "replayed": False,
                            }
                elif action == "record_confirmation":
                    confirmation = DesignConfirmation.model_validate(message["confirmation"])
                    store.put_confirmation(confirmation)
                    response = {"ok": True, "confirmation_id": confirmation.confirmation_id}
                elif action == "list_results":
                    response = {"ok": True, "results": [item.model_dump(mode="json") for item in store.list_results(str(message["run_id"]))]}
                elif action == "list_committed":
                    projection = session_store.project_run(str(message["run_id"]), expected_policy_hash=policy_hash)
                    response = {"ok": True, "committed": list(projection.committed), "projection": projection.to_dict()}
                elif action == "commit_promotion":
                    statement = ScientificStatement.model_validate(message["statement"])
                    decision = PromotionDecision.model_validate(message["decision"])
                    if statement.statement_id != decision.statement_id or statement.run_id != decision.run_id:
                        raise ValueError("promotion decision is not bound to its statement")
                    store.put_statement(statement)
                    store.put_decision(decision)
                    response = {"ok": True, "statement_id": statement.statement_id, "decision_id": decision.decision_id}
                elif action == "list_events":
                    response = {"ok": True, "events": store.list_events(str(message["run_id"]), int(message.get("after", 0)))}
                elif action == "seal_run":
                    run_id = str(message["run_id"])
                    session_id = ensure_session(run_id)
                    sealed = _seal_authority_session(session_store, signer, session_id)
                    response = {"ok": True} | sealed.to_dict()
                elif action == "abort":
                    for session_id in sessions.values():
                        session_store.abort_session(session_id)
                    response = {"ok": True, "sessions": list(sessions.values())}
                    running = False
                elif action == "stop":
                    sealed_sessions = [_seal_authority_session(session_store, signer, session_id).to_dict() for session_id in sessions.values()]
                    response = {"ok": True, "sessions": sealed_sessions}
                    running = False
                else:
                    raise ValueError("unsupported verifier action")
            except Exception as exc:
                response = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
            connection.send(response)
            connection.close()
    finally:
        listener.close()
        if family == "AF_UNIX":
            try:
                Path(address).unlink(missing_ok=True)
            except OSError:
                pass


def _seal_authority_session(
    session_store: AuthoritySessionStore,
    signer: ReceiptSigner,
    session_id: str,
):
    current = session_store.get_session(session_id)
    if current is None:
        raise ValueError("unknown authority session")
    if current.status == "sealed":
        return current
    signing_record = session_store.signing_record(session_id)
    signature = signer.sign_bytes(canonical_json(signing_record.signing_payload()).encode("utf-8"))
    return session_store.seal_session(session_id=session_id, root_digest=signing_record.root_digest or "", signature=signature)
def _new_address(authority_dir: Path) -> tuple[Any, str]:
    token = uuid4().hex
    if os.name == "nt":
        return rf"\\.\pipe\pertura-{token}", "AF_PIPE"
    return str(authority_dir / f"verifier-{token}.sock"), "AF_UNIX"


def _new_loopback_address() -> tuple[tuple[str, int], str]:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        address = probe.getsockname()
    return (str(address[0]), int(address[1])), "AF_INET"


def _publish_outputs(result: ResultEnvelope, staging: Path, output_root: Path, workspace_root: Path | None) -> ResultEnvelope:
    if not result.output_paths:
        return result
    destination_root = output_root / result.request_id
    destination_root.mkdir(parents=True, exist_ok=True)
    published_paths: list[str] = []
    published_hashes: dict[str, str] = {}
    for name in result.output_paths:
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("capability output path must be staging-relative")
        source = (staging / relative).resolve()
        if not source.is_file() or staging.resolve() not in source.parents:
            raise ValueError(f"capability output is missing from verifier staging: {name}")
        destination = (destination_root / relative).resolve()
        if destination_root.resolve() not in destination.parents:
            raise ValueError("capability output escaped the fixed output root")
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        digest = file_sha256(destination)
        expected = result.output_hashes.get(str(name))
        if expected and expected != digest:
            raise ValueError(f"capability output hash changed during publish: {name}")
        published_hashes[str(name)] = digest
        if workspace_root and (destination == workspace_root or workspace_root in destination.parents):
            published_paths.append(destination.relative_to(workspace_root).as_posix())
        else:
            published_paths.append(str(destination))
    payload = result.model_dump(mode="json")
    payload["result_id"] = ""
    payload["canonical_hash"] = ""
    payload["output_paths"] = published_paths
    payload["output_hashes"] = published_hashes
    return ResultEnvelope.model_validate(payload)


def _with_verification_state(result: ResultEnvelope, state: str) -> ResultEnvelope:
    payload = result.model_dump(mode="json")
    payload["result_id"] = ""
    payload["canonical_hash"] = ""
    payload["metadata"] = dict(payload.get("metadata") or {}) | {"verification_state": state}
    return ResultEnvelope.model_validate(payload)


def _validate_declared_dependencies(spec: Any, request: CapabilityRunRequest) -> tuple[str, ...]:
    """Fail closed when a trusted runner lacks its declared scientific inputs."""

    if spec.trust_level != CapabilityTrust.builtin_trusted or not spec.implemented:
        return ()
    roles = {str(item.role or "") for item in request.dependencies}
    kinds = {str(item.kind or "") for item in request.dependencies}
    kinds.add("contract")
    issues: list[str] = []
    for capability_id in spec.depends_on:
        if capability_id not in roles:
            issues.append(f"missing capability dependency {capability_id}")
    runtime_only = {"environment", "prediction", "split_contract"}
    for kind in spec.dependency_kinds:
        if kind not in kinds and kind not in runtime_only:
            issues.append(f"missing dependency kind {kind}")
    return tuple(issues)


def _write_dependency_projection(
    store: AuthorityStore,
    request: CapabilityRunRequest,
    staging: Path,
    workspace_root: Path | None,
) -> None:
    results_by_id: dict[str, dict[str, Any]] = {}
    dependency_root = staging / "_dependencies"
    for dependency in request.dependencies:
        result = store.get_result(dependency.object_id)
        if result is None:
            continue
        payload = results_by_id.get(result.result_id)
        if payload is None:
            payload = result.model_dump(mode="json")
            local_paths = []
            for output in result.output_paths:
                source = Path(output)
                if not source.is_absolute() and workspace_root is not None:
                    source = workspace_root / source
                source = source.resolve()
                if not source.is_file():
                    continue
                destination = dependency_root / result.result_id / source.name
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
                local_paths.append(str(destination))
            payload["local_output_paths"] = local_paths
            payload["dependency_refs"] = []
            results_by_id[result.result_id] = payload
        payload["dependency_refs"].append(dependency.model_dump(mode="json"))
    (staging / "_dependency_results.json").write_text(
        json.dumps(
            {"results": [results_by_id[key] for key in sorted(results_by_id)]},
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
