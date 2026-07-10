from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from pertura_core import AnalysisStatus, DatasetContract, ResultEnvelope, ScopeKey
from pertura_runtime.claude.workspace import ClaudeRunWorkspace
from pertura_runtime.product import PerturaProductRuntime
from pertura_runtime.verifier import AuthoritySessionStore, AuthorityStore, VerifierBroker


def _source(path: Path) -> Path:
    path.write_text(
        "cell_id,replicate,guide,target,G1\n"
        "c1,r1,g1,KLF1,2\n"
        "c2,r2,NTC,NTC,0\n",
        encoding="utf-8",
    )
    return path


def test_historical_session_is_verified_without_starting_a_new_broker(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("PERTURA_AUTHORITY_ROOT", str(tmp_path / "authority"))
    source = _source(tmp_path / "expression.csv")
    workspace = ClaudeRunWorkspace.create(
        root=tmp_path / "runs", input_source=source, run_id="historical"
    )

    writer = PerturaProductRuntime(workspace)
    contract = writer.inspect_dataset()
    result = writer.run_diagnostic(
        "diagnostic.contract_integrity.v1", contract_id=contract["contract_id"]
    )
    assert result["receipt_id"]
    writer.close(graceful=True)

    reader = PerturaProductRuntime(workspace)
    assert reader.started is False
    report = reader.finalize_report("historical")
    assert reader.started is False
    assert report["result_count"] == 1
    assert report["root_digest"].startswith("sha256:")

    payload = json.loads(
        (workspace.reports_dir / "capability_report.json").read_text(encoding="utf-8")
    )
    assert payload["verification_state_by_result"][result["result_id"]] == "trusted_receipt"
    assert any(item["verified"] for item in payload["authority_projection"]["sessions"])
    assert not list((tmp_path / "authority").rglob("signing.key"))
    reader.close()


def test_non_graceful_stop_aborts_session_and_downgrades_receipt(tmp_path: Path) -> None:
    contract = DatasetContract(dataset_id="aborted", input_format="csv")
    from pertura_core import CapabilityRunRequest

    request = CapabilityRunRequest(
        run_id="aborted-run",
        capability_id="diagnostic.contract_integrity.v1",
        capability_version="1.0.0",
        contract_id=contract.contract_id,
        contract_hash=contract.canonical_hash,
        scope=ScopeKey(dataset_id=contract.dataset_id),
    )
    broker = VerifierBroker(
        authority_dir=tmp_path / "authority",
        policy_hash="sha256:policy",
        run_id="aborted-run",
    ).start()
    broker.register_contract(contract)
    response = broker.run(request)
    assert response["receipt"] is not None
    broker.stop(graceful=False)

    projection = AuthoritySessionStore(
        tmp_path / "authority" / "authority.sqlite3", read_only=True
    ).project_run("aborted-run", expected_policy_hash="sha256:policy")
    assert projection.sessions[0]["status"] == "aborted"
    assert projection.committed[0]["verification_state"] == "session_aborted_untrusted"


def test_pre_session_result_is_visible_only_as_legacy_unverified(tmp_path: Path) -> None:
    database = tmp_path / "authority.sqlite3"
    store = AuthorityStore(database)
    result = ResultEnvelope(
        run_id="legacy-run",
        request_id="legacy-request",
        capability_id="legacy.import.v1",
        capability_version="1.0.0",
        capability_trust="exploratory",
        contract_id="legacy-contract",
        contract_hash="sha256:legacy",
        scope=ScopeKey(dataset_id="legacy"),
        status=AnalysisStatus.completed_with_caution,
        result_kind="legacy_unverified",
        source_class="observed_metadata",
        summary="Historical alpha result.",
    )
    store.commit_result(result)

    # Opening the new writer performs the additive SQLite migration but does
    # not assign an authority session or fabricate a receipt for the old row.
    AuthoritySessionStore(database)
    projection = AuthoritySessionStore(database, read_only=True).project_run("legacy-run")
    assert projection.legacy_unverified_result_ids == (result.result_id,)
    assert projection.committed[0]["verification_state"] == "legacy_unverified"
    assert projection.committed[0]["receipt"] is None


def test_tampered_session_seal_is_rejected(tmp_path: Path) -> None:
    contract = DatasetContract(dataset_id="tamper", input_format="csv")
    from pertura_core import CapabilityRunRequest

    request = CapabilityRunRequest(
        run_id="tamper-run",
        capability_id="diagnostic.contract_integrity.v1",
        capability_version="1.0.0",
        contract_id=contract.contract_id,
        contract_hash=contract.canonical_hash,
        scope=ScopeKey(dataset_id=contract.dataset_id),
    )
    authority = tmp_path / "authority"
    with VerifierBroker(
        authority_dir=authority, policy_hash="sha256:policy", run_id="tamper-run"
    ) as broker:
        broker.register_contract(contract)
        broker.run(request)

    database = authority / "authority.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE authority_sessions SET root_digest = 'sha256:tampered' WHERE run_id = ?",
            ("tamper-run",),
        )
        connection.commit()

    projection = AuthoritySessionStore(database, read_only=True).project_run(
        "tamper-run", expected_policy_hash="sha256:policy"
    )
    assert projection.invalid_session_ids
    assert projection.committed[0]["verification_state"] == "invalid_authority_session"


def test_cli_commands_finalize_a_run_created_by_other_processes(
    monkeypatch, tmp_path: Path
) -> None:
    source = tmp_path / "expression.csv"
    source.write_text(
        "cell_id,G1,G2\n"
        "c1,2,0\n"
        "c2,0,3\n",
        encoding="utf-8",
    )
    authority = tmp_path / "authority"
    monkeypatch.setenv("PERTURA_AUTHORITY_ROOT", str(authority))
    environment = os.environ.copy()
    source_root = str(Path(__file__).resolve().parents[2] / "src")
    environment["PYTHONPATH"] = source_root + os.pathsep + environment.get("PYTHONPATH", "")

    inspect_output = tmp_path / "inspect.json"
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pertura_runtime.product_cli",
            "inspect",
            str(source),
            "--out",
            str(inspect_output),
        ],
        check=True,
        env=environment,
    )
    contract_id = json.loads(inspect_output.read_text(encoding="utf-8"))["contract_id"]

    diagnostic_output = tmp_path / "diagnostic.json"
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pertura_runtime.product_cli",
            "diagnostic",
            "diagnostic.contract_integrity.v1",
            str(source),
            "--contract-id",
            contract_id,
            "--out",
            str(diagnostic_output),
        ],
        check=True,
        env=environment,
    )
    analysis_output = tmp_path / "analysis.json"
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pertura_runtime.product_cli",
            "analyze",
            "intake",
            str(source),
            "--capability-id",
            "intake.materialize.v1",
            "--contract-id",
            contract_id,
            "--out",
            str(analysis_output),
        ],
        check=True,
        env=environment,
    )

    finalize_output = tmp_path / "finalize.json"
    subprocess.run(
        [sys.executable, "-m", "pertura_runtime.product_cli", "finalize", "current", "--workspace", str(source), "--out", str(finalize_output)],
        check=True,
        env=environment,
    )
    finalized = json.loads(finalize_output.read_text(encoding="utf-8"))
    assert finalized["result_count"] == 2
    assert finalized["root_digest"].startswith("sha256:")


def test_dead_broker_process_is_reconciled_to_aborted(tmp_path: Path) -> None:
    from pertura_core import CapabilityRunRequest

    contract = DatasetContract(dataset_id="dead", input_format="csv")
    request = CapabilityRunRequest(
        run_id="dead-run",
        capability_id="diagnostic.contract_integrity.v1",
        capability_version="1.0.0",
        contract_id=contract.contract_id,
        contract_hash=contract.canonical_hash,
        scope=ScopeKey(dataset_id=contract.dataset_id),
    )
    authority = tmp_path / "authority"
    broker = VerifierBroker(
        authority_dir=authority, policy_hash="sha256:policy", run_id="dead-run"
    ).start()
    broker.register_contract(contract)
    result = broker.run(request)
    assert result["receipt"] is not None
    assert broker._process is not None
    broker._process.terminate()
    broker._process.join(timeout=5)

    with pytest.raises(Exception, match="unavailable"):
        broker.list_results("dead-run")
    projection = AuthoritySessionStore(
        authority / "authority.sqlite3", read_only=True
    ).project_run("dead-run", expected_policy_hash="sha256:policy")
    assert projection.sessions[-1]["status"] == "aborted"
    assert projection.committed[0]["verification_state"] == "session_aborted_untrusted"
    broker.stop(graceful=False)


def test_broker_start_timeout_aborts_session_created_before_lost_ping(
    monkeypatch, tmp_path: Path
) -> None:
    authority = tmp_path / "authority"
    broker = VerifierBroker(
        authority_dir=authority, policy_hash="sha256:policy", run_id="timeout-run"
    )

    class FakeProcess:
        def __init__(self) -> None:
            self.alive = True

        def is_alive(self) -> bool:
            return self.alive

        def terminate(self) -> None:
            self.alive = False

        def join(self, timeout=None) -> None:
            return None

    def spawn_with_open_session() -> None:
        database = authority / "authority.sqlite3"
        AuthorityStore(database)
        AuthoritySessionStore(database).start_session(
            session_id="authority_session_timeout",
            run_id="timeout-run",
            broker_instance_id=broker._launch_instance_id or "missing",
            public_key="not-used-for-aborted-session",
            policy_hash="sha256:policy",
        )
        broker._process = FakeProcess()

    def lose_response(message):
        raise OSError("simulated lost ping response")

    monkeypatch.setattr(broker, "_spawn_process", spawn_with_open_session)
    monkeypatch.setattr(broker, "_call", lose_response)
    with pytest.raises(Exception, match="did not start"):
        broker.start(timeout=0.05)

    projection = AuthoritySessionStore(
        authority / "authority.sqlite3", read_only=True
    ).project_run("timeout-run", expected_policy_hash="sha256:policy")
    assert projection.sessions
    assert {item["status"] for item in projection.sessions} == {"aborted"}

def test_sealed_broker_opens_a_new_session_for_later_results(tmp_path: Path) -> None:
    from pertura_core import CapabilityRunRequest

    contract = DatasetContract(dataset_id="continued", input_format="csv")
    authority = tmp_path / "authority"
    with VerifierBroker(
        authority_dir=authority, policy_hash="sha256:policy", run_id="continued-run"
    ) as broker:
        broker.register_contract(contract)
        first = broker.run(CapabilityRunRequest(
            run_id="continued-run",
            capability_id="diagnostic.contract_integrity.v1",
            capability_version="1.0.0",
            contract_id=contract.contract_id,
            contract_hash=contract.canonical_hash,
            scope=ScopeKey(dataset_id=contract.dataset_id),
        ))
        first_session = first["authority_session"]["session_id"]
        broker.seal_run("continued-run")
        second = broker.run(CapabilityRunRequest(
            run_id="continued-run",
            capability_id="diagnostic.contract_integrity.v1",
            capability_version="1.0.0",
            contract_id=contract.contract_id,
            contract_hash=contract.canonical_hash,
            scope=ScopeKey(dataset_id=contract.dataset_id),
        ))
        second_session = second["authority_session"]["session_id"]
        assert second_session != first_session
        assert second["receipt"] is not None
        broker.seal_run("continued-run")

    projection = AuthoritySessionStore(
        authority / "authority.sqlite3", read_only=True
    ).project_run("continued-run", expected_policy_hash="sha256:policy")
    verified = [item for item in projection.sessions if item["verified"]]
    assert len(verified) == 2
    assert {item["verification_state"] for item in projection.committed} == {"trusted_receipt"}


def test_finalize_after_broker_crash_is_not_completed(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("PERTURA_AUTHORITY_ROOT", str(tmp_path / "authority"))
    source = _source(tmp_path / "expression.csv")
    workspace = ClaudeRunWorkspace.create(
        root=tmp_path / "runs", input_source=source, run_id="crashed-finalize"
    )
    runtime = PerturaProductRuntime(workspace)
    contract = runtime.inspect_dataset()
    runtime.run_diagnostic(
        "diagnostic.contract_integrity.v1", contract_id=contract["contract_id"]
    )
    assert runtime._broker._process is not None
    runtime._broker._process.terminate()
    runtime._broker._process.join(timeout=5)

    report = runtime.finalize_report()
    assert report["status"] == "untrusted_no_verified_results"
    assert report["root_digest"] is None
    assert runtime.started is False


def test_finalizer_keeps_retired_legacy_row_without_registry_lookup(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("PERTURA_AUTHORITY_ROOT", str(tmp_path / "authority"))
    workspace = ClaudeRunWorkspace.create(root=tmp_path / "runs", run_id="retired")
    runtime = PerturaProductRuntime(workspace)
    database = runtime.authority_dir / "authority.sqlite3"
    store = AuthorityStore(database)
    result = ResultEnvelope(
        run_id="retired",
        request_id="retired-request",
        capability_id="retired.capability.v0",
        capability_version="0.0.1",
        capability_trust="exploratory",
        contract_id="legacy-contract",
        contract_hash="sha256:legacy",
        scope=ScopeKey(dataset_id="legacy"),
        status=AnalysisStatus.completed_with_caution,
        result_kind="legacy_unverified",
        source_class="observed_metadata",
        summary="Retired historical result.",
    )
    store.commit_result(result)

    report = runtime.finalize_report()
    assert report["result_count"] == 1
    assert report["status"] == "untrusted_no_verified_results"
    rendered = json.loads(
        (workspace.reports_dir / "capability_report.json").read_text(encoding="utf-8")
    )
    assert rendered["unverified_results"][0]["capability_id"] == "retired.capability.v0"

\n