from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from pertura_runtime.claude.workspace import ClaudeRunWorkspace
from pertura_runtime.dashboard import create_dashboard_app
from pertura_runtime.product import PerturaProductRuntime


def test_design_confirmation_rejects_effect_smuggling_atomically(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("PERTURA_AUTHORITY_ROOT", str(tmp_path / "authority"))
    source = tmp_path / "expression.csv"
    source.write_text("cell_id,ind,G1\nc1,d1,1\nc2,d2,2\n", encoding="utf-8")
    workspace = ClaudeRunWorkspace.create(
        root=tmp_path / "runs", input_source=source, run_id="confirmation-smuggling"
    )
    runtime = PerturaProductRuntime(workspace)
    try:
        contract = runtime.inspect_dataset()
        latest_contract = workspace.artifacts_dir / "dataset_contract.latest.json"
        contract_before = latest_contract.read_bytes()
        events_before = runtime.read_authority_events()
        projection_before = runtime.read_authority_projection()

        with pytest.raises(
            ValueError,
            match="field cannot be confirmed through the design interface",
        ):
            runtime.confirm_design(
                contract["contract_id"],
                {
                    "donor": "ind",
                    "measured_effect": {
                        "gene": "G1",
                        "significant": True,
                        "claim_strength": "measured",
                    },
                },
            )

        assert latest_contract.read_bytes() == contract_before
        assert runtime.read_authority_events() == events_before
        assert runtime.read_authority_projection() == projection_before
        assert not projection_before["committed"]
    finally:
        runtime.close()


def test_dashboard_is_read_only_except_design_confirmation_and_propagates_stale(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("PERTURA_AUTHORITY_ROOT", str(tmp_path / "authority"))
    source = tmp_path / "expression.csv"
    source.write_text("cell_id,replicate,G1\nc1,r1,1\nc2,r2,2\n", encoding="utf-8")
    workspace = ClaudeRunWorkspace.create(
        root=tmp_path / "runs", input_source=source, run_id="dashboard"
    )
    runtime = PerturaProductRuntime(workspace)
    try:
        contract = runtime.inspect_dataset()
        runtime.run_diagnostic(
            "diagnostic.contract_integrity.v1",
            contract_id=contract["contract_id"],
            dependencies=[
                {
                    "kind": "contract",
                    "object_id": contract["contract_id"],
                    "object_hash": contract["contract_hash"],
                }
            ],
        )
        app = create_dashboard_app(runtime)
        routes = {
            (route.path, tuple(sorted(getattr(route, "methods", []) or [])))
            for route in app.routes
        }
        assert not any(
            path.endswith("/run") and "POST" in methods for path, methods in routes
        )
        assert not any("retry" in path or "cancel" in path for path, _ in routes)

        client = TestClient(app)
        before = client.get("/api/run")
        assert before.status_code == 200
        assert before.json()["permissions"]["can_run"] is False

        response = client.post(
            "/runs/dashboard/confirmations",
            json={
                "contract_id": contract["contract_id"],
                "field": "control",
                "value": "NTC",
                "rationale": "Confirmed from the screen design sheet.",
            },
        )
        assert response.status_code == 200
        assert response.json()["version"] == 2

        after = client.get("/api/run").json()
        assert after["contract"]["identity_fields"]["control"]["status"] == "confirmed"
        assert after["results"][0]["stale"] is True

        rejected = client.post(
            "/runs/dashboard/confirmations",
            json={
                "contract_id": contract["contract_id"],
                "field": "measured_effect",
                "value": 3.2,
                "rationale": "not allowed",
            },
        )
        assert rejected.status_code == 422
    finally:
        runtime.close()


def test_dashboard_get_and_sse_do_not_start_a_broker(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("PERTURA_AUTHORITY_ROOT", str(tmp_path / "authority"))
    source = tmp_path / "expression.csv"
    source.write_text("cell_id,G1\nc1,1\n", encoding="utf-8")
    workspace = ClaudeRunWorkspace.create(
        root=tmp_path / "runs", input_source=source, run_id="dashboard-readonly"
    )
    writer = PerturaProductRuntime(workspace)
    contract = writer.inspect_dataset()
    writer.run_diagnostic(
        "diagnostic.contract_integrity.v1", contract_id=contract["contract_id"]
    )
    writer.close()

    reader = PerturaProductRuntime(workspace)
    assert reader.started is False
    app = create_dashboard_app(reader)
    response = TestClient(app).get("/api/run")
    assert response.status_code == 200
    assert response.json()["results"]
    assert reader.started is False

    route = next(
        route for route in app.routes if getattr(route, "path", None) == "/api/events"
    )

    async def first_event():
        stream = await route.endpoint(after=0)
        chunk = await stream.body_iterator.__anext__()
        await stream.body_iterator.aclose()
        return chunk

    assert asyncio.run(first_event())
    assert reader.started is False
