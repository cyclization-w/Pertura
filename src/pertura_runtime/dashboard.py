from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from pertura_runtime.product import PerturaProductRuntime


class DashboardConfirmation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    contract_id: str
    field: str
    value: Any
    rationale: str


def create_dashboard_app(runtime: PerturaProductRuntime):
    try:
        from fastapi import FastAPI, HTTPException
        from fastapi.responses import FileResponse, StreamingResponse
        from fastapi.staticfiles import StaticFiles
    except ModuleNotFoundError as exc:  # pragma: no cover - optional dependency
        raise RuntimeError("Dashboard dependencies are missing. Install with `pip install 'pertura[dashboard]'`.") from exc

    app = FastAPI(title="Pertura dashboard", version="0.2.0a3", docs_url=None, redoc_url=None)

    @app.get("/api/run")
    async def run_projection() -> dict[str, Any]:
        contract_path = runtime.workspace.artifacts_dir / "dataset_contract.latest.json"
        contract = json.loads(contract_path.read_text(encoding="utf-8")) if contract_path.exists() else None
        results = runtime.broker.list_results(runtime.workspace.root.name)
        report_path = runtime.workspace.reports_dir / "capability_report.md"
        return {
            "run_id": runtime.workspace.root.name,
            "contract": contract,
            "results": results,
            "phases": _phase_projection(results),
            "target_failure_queue": [item for item in results if item["result_kind"] == "target_reliability" and item["status"] != "screen_passed"],
            "report": report_path.read_text(encoding="utf-8") if report_path.exists() else None,
            "permissions": {"can_run": False, "can_retry": False, "can_cancel": False, "can_confirm_design": True},
        }

    @app.get("/api/events")
    async def events(after: int = 0):
        async def stream():
            cursor = after
            while True:
                items = runtime.broker.list_events(runtime.workspace.root.name, after=cursor)
                for item in items:
                    cursor = max(cursor, int(item["sequence"]))
                    yield f"id: {cursor}\nevent: {item['event_type']}\ndata: {json.dumps(item, ensure_ascii=False)}\n\n"
                yield ": keepalive\n\n"
                await asyncio.sleep(1.0)

        return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.post("/runs/{run_id}/confirmations")
    async def confirmation(run_id: str, body: DashboardConfirmation) -> dict[str, Any]:
        if run_id != runtime.workspace.root.name:
            raise HTTPException(status_code=404, detail="run not found")
        allowed = {"control", "guide_target", "replicate", "state_label", "donor", "batch"}
        if body.field not in allowed:
            raise HTTPException(status_code=422, detail="only design/identity fields can be confirmed")
        if not body.rationale.strip():
            raise HTTPException(status_code=422, detail="confirmation rationale is required")
        try:
            return runtime.confirm_design(body.contract_id, {body.field: body.value})
        except (ValueError, FileNotFoundError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    static = Path(__file__).resolve().parent / "dashboard_static"
    if static.is_dir():
        assets = static / "assets"
        if assets.is_dir():
            app.mount("/assets", StaticFiles(directory=assets), name="assets")

        @app.get("/{path:path}")
        async def frontend(path: str):
            candidate = (static / path).resolve()
            if path and candidate.is_file() and static in candidate.parents:
                return FileResponse(candidate)
            return FileResponse(static / "index.html")

    return app


def run_dashboard(runtime: PerturaProductRuntime, *, port: int = 8765) -> None:
    try:
        import uvicorn
    except ModuleNotFoundError as exc:  # pragma: no cover - optional dependency
        raise RuntimeError("Dashboard dependencies are missing. Install with `pip install 'pertura[dashboard]'`.") from exc
    app = create_dashboard_app(runtime)
    try:
        uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")
    finally:
        runtime.close()


def _phase_projection(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    phases = [
        (1, "Data intake"),
        (2, "Guide assignment & screen QC"),
        (3, "State & module reference"),
        (4, "Target reliability"),
        (5, "Effect estimation & calibration"),
        (6, "Biological interpretation"),
        (7, "Virtual evaluation & next design"),
    ]
    mapping = {
        "contract_integrity": 1,
        "guide_assignment_qc": 2,
        "state_reference": 3,
        "module_reference": 3,
        "target_reliability": 4,
        "differential_expression": 5,
        "virtual_evaluation": 7,
        "materialized_dataset": 1,
        "dataset_integrity": 1,
        "design_balance": 1,
        "guide_integrity": 2,
        "guide_assignment": 2,
        "guide_ambient": 2,
        "moi_doublet": 2,
        "retained_cell_manifest": 2,
        "state_reference_fit": 3,
        "state_mapping": 3,
        "state_annotation_candidates": 3,
        "reference_modules": 3,
        "mixscape_responder": 4,
        "target_guide_efficacy": 4,
        "conditional_association": 5,
        "effect_sensitivity": 5,
        "module_global_effect": 5,
        "method_null_calibration": 5,
    }
    by_phase: dict[int, list[dict[str, Any]]] = {}
    for result in results:
        by_phase.setdefault(mapping.get(result.get("result_kind"), 1), []).append(result)
    return [
        {
            "phase": phase,
            "title": title,
            "status": _phase_status(by_phase.get(phase, [])),
            "result_ids": [item["result_id"] for item in by_phase.get(phase, [])],
        }
        for phase, title in phases
    ]


def _phase_status(results: list[dict[str, Any]]) -> str:
    if not results:
        return "pending"
    statuses = {item["status"] for item in results}
    if statuses & {"blocked", "failed", "unresolved", "out_of_scope"}:
        return "blocked"
    if statuses & {"caution", "completed_with_caution", "limited"}:
        return "caution"
    return "completed"
