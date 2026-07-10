from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from pertura_runtime.claude.workspace import ClaudeRunWorkspace
from pertura_runtime.dashboard import create_dashboard_app
from pertura_runtime.product import PerturaProductRuntime


def test_dashboard_is_read_only_except_design_confirmation_and_propagates_stale(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("PERTURA_AUTHORITY_ROOT", str(tmp_path / "authority"))
    source = tmp_path / "expression.csv"
    source.write_text("cell_id,replicate,G1\nc1,r1,1\nc2,r2,2\n", encoding="utf-8")
    workspace = ClaudeRunWorkspace.create(root=tmp_path / "runs", input_source=source, run_id="dashboard")
    runtime = PerturaProductRuntime(workspace)
    try:
        contract = runtime.inspect_dataset()
        runtime.run_diagnostic(
            "diagnostic.contract_integrity.v1",
            contract_id=contract["contract_id"],
            dependencies=[{
                "kind": "contract",
                "object_id": contract["contract_id"],
                "object_hash": contract["contract_hash"],
            }],
        )
        app = create_dashboard_app(runtime)
        routes = {(route.path, tuple(sorted(getattr(route, "methods", []) or []))) for route in app.routes}
        assert not any(path.endswith("/run") and "POST" in methods for path, methods in routes)
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
            json={"contract_id": contract["contract_id"], "field": "measured_effect", "value": 3.2, "rationale": "not allowed"},
        )
        assert rejected.status_code == 422
    finally:
        runtime.close()
