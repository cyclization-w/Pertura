"""FastAPI server for the Pertura workbench."""

from __future__ import annotations

from pathlib import Path


def analysis_spec_audit_payload(workbench, *, run_id: str = "", strict: bool = False) -> dict:
    from pertura.spec.contracts import audit_analysis_graph
    spec = _analysis_spec_for_workbench(workbench, run_id=run_id)
    if not spec:
        raise ValueError("No analysis spec")
    return audit_analysis_graph(
        spec,
        capabilities=_capability_registry_for_workbench(workbench, run_id=run_id),
        strict=strict,
    )


def analysis_spec_contract_payload(workbench, *, node_id: str = "", run_id: str = "") -> dict:
    from pertura.spec.contracts import graph_contract, node_contract
    from pertura.spec.models import spec_from_dict
    spec = spec_from_dict(_analysis_spec_for_workbench(workbench, run_id=run_id))
    if spec is None:
        raise ValueError("No analysis spec")
    registry = _capability_registry_for_workbench(workbench, run_id=run_id)
    if node_id:
        if spec.node(node_id) is None:
            raise ValueError(f"Node {node_id} not found")
        return node_contract(spec, node_id, capabilities=registry)
    return graph_contract(spec, capabilities=registry)


def runtime_node_contract_payload(workbench, *, node_id: str = "", run_id: str = "") -> dict:
    from pertura.tools.registry import execute_tool
    snap = _snapshot_for_workbench(workbench, run_id=run_id)
    if not snap:
        raise ValueError("No run snapshot")
    payload = execute_tool("get_node_contract", {"node_id": node_id} if node_id else {}, snap=snap)
    if "error" in payload:
        raise ValueError(payload["error"])
    return payload


def context_review_payload(
    workbench,
    *,
    run_id: str = "",
    purpose: str = "audit",
    max_items: int = 8,
    token_budget: int = 6000,
    runtime_state: dict | None = None,
) -> dict:
    from pertura.tools.registry import execute_tool
    snap = _snapshot_for_workbench(workbench, run_id=run_id)
    if not snap:
        raise ValueError("No run snapshot")
    payload = execute_tool(
        "get_context_review",
        {
            "purpose": purpose,
            "max_items": max_items,
            "token_budget": token_budget,
            "runtime_state": runtime_state or {},
        },
        snap=snap,
    )
    if "error" in payload:
        raise ValueError(payload["error"])
    return payload


def run_audit_payload(workbench, *, run_id: str = "") -> dict:
    from pertura.core.audit import audit_run
    snap = _snapshot_for_workbench(workbench, run_id=run_id)
    store = _store_for_workbench(workbench, run_id=run_id)
    if not snap:
        raise ValueError("No run snapshot")
    graph = store.read_graph() if store else None
    run_dir = getattr(store, "run_dir", "") if store else ""
    return audit_run(snap, graph or {}, run_dir=run_dir)


def rethinking_payload(
    workbench,
    *,
    run_id: str = "",
    node_id: str = "",
    issue: str = "",
    depth: int = 5,
) -> dict:
    from pertura.core.rethinking import plan_rethinking
    snap = _snapshot_for_workbench(workbench, run_id=run_id)
    store = _store_for_workbench(workbench, run_id=run_id)
    if not snap:
        raise ValueError("No run snapshot")
    graph = store.read_graph() if store else None
    return plan_rethinking(snap, node_id, issue=issue, depth=depth, graph=graph or {})


def harness_manifest_payload() -> dict:
    from pertura.core import build_harness_manifest
    return build_harness_manifest()


def domain_browser_payload(workbench, *, include_core_tools: bool = True) -> dict:
    return workbench.domain.describe(include_core_tools=include_core_tools)


def workbench_view_payload(
    workbench,
    *,
    run_id: str = "",
    max_items: int = 8,
    token_budget: int = 6000,
    jobs: list[dict] | None = None,
) -> dict:
    """Return the stable compact UI contract for the workbench shell.

    This endpoint is intentionally a projection over existing runtime views. It
    gives a GUI the current decision surface without exposing full event logs,
    notebooks, or the complete graph by default.
    """
    from pertura.models import _model_dump

    snap = _snapshot_for_workbench(workbench, run_id=run_id)
    store = _store_for_workbench(workbench, run_id=run_id)
    status = _status_for_snapshot(snap) if snap else dict(getattr(workbench, "status", {}) or {})
    graph = store.read_graph() if store else (workbench.graph or {"nodes": [], "edges": []})

    active_node_id = (snap.active_node_id if snap else "") or ""
    node_contract = _safe_payload(
        lambda: runtime_node_contract_payload(workbench, node_id=active_node_id, run_id=run_id),
        default={},
    )
    context_review = _safe_payload(
        lambda: context_review_payload(
            workbench,
            run_id=run_id,
            purpose="ui",
            max_items=max_items,
            token_budget=token_budget,
        ),
        default={},
    )
    run_audit = _safe_payload(
        lambda: run_audit_payload(workbench, run_id=run_id),
        default={},
    )
    rethinking = _safe_payload(
        lambda: rethinking_payload(
            workbench,
            run_id=run_id,
            node_id=active_node_id,
            issue="workbench_view",
            depth=4,
        ),
        default={},
    )

    open_interrupts = [
        _model_dump(item) for item in ((snap.interrupts if snap else []) or [])
        if item.status == "open"
    ][:max_items]
    open_triggers = [
        _model_dump(item) for item in ((snap.triggers if snap else []) or [])
        if item.status == "open"
    ][:max_items]
    open_findings = [
        _model_dump(item) for item in ((snap.findings if snap else []) or [])
        if item.severity in {"warning", "blocking"}
    ][-max_items:]
    recent_attempts = [_attempt_card(item, snap=snap) for item in ((snap.attempts if snap else []) or [])[-max_items:]]
    artifact_summary = [_artifact_card(item) for item in ((snap.artifacts if snap else []) or [])[-max_items:]]

    domain_payload = domain_browser_payload(workbench, include_core_tools=False)
    return {
        "view_type": "workbench_view",
        "schema_version": "v1",
        "run_id": status.get("run_id", run_id),
        "status": status,
        "active": {
            "node_id": active_node_id,
            "branch_id": snap.active_branch if snap else "",
            "attempt_id": snap.active_attempt if snap else "",
        },
        "budget": _model_dump(snap.budget) if snap else {},
        "analysis": {
            "graph_summary": {
                "nodes": len((graph or {}).get("nodes", [])),
                "edges": len((graph or {}).get("edges", [])),
            },
            "active_node_contract": node_contract,
            "domain": domain_payload.get("domain", {}),
            "nodes": [
                {
                    "node_id": item.get("node_id", ""),
                    "title": item.get("title", ""),
                    "purpose": item.get("purpose", ""),
                    "allowed_capabilities": item.get("allowed_capabilities", []),
                    "recommended_actions": item.get("recommended_actions", []),
                    "expected_outputs": item.get("expected_outputs", []),
                    "next_nodes": item.get("next_nodes", []),
                    "strict_edges": item.get("strict_edges", False),
                    "hard_conditions": sum(
                        1
                        for group in (item.get("conditions", {}) or {}).values()
                        for condition in group
                        if condition.get("hard")
                    ),
                    "rubric_only_conditions": sum(
                        1
                        for group in (item.get("conditions", {}) or {}).values()
                        for condition in group
                        if condition.get("evaluator_id") == "rubric_only"
                    ),
                }
                for item in domain_payload.get("nodes", [])
            ],
            "capabilities_by_node": domain_payload.get("capabilities_by_node", {}),
        },
        "agent_context": context_review,
        "review": {
            "open_interrupts": open_interrupts,
            "open_triggers": open_triggers,
            "open_findings": open_findings,
            "run_audit_summary": _audit_summary_card(run_audit),
            "rethinking": _rethinking_card(rethinking),
        },
        "activity": {
            "recent_attempts": recent_attempts,
            "jobs": (jobs or [])[:max_items],
        },
        "artifacts": {
            "recent": artifact_summary,
            "total": len(snap.artifacts) if snap else 0,
        },
        "report": _report_summary_for_snapshot(snap),
        "links": {
            "graph": "/api/graph",
            "domain": "/api/domain",
            "node_contract": "/api/node-contract",
            "context_review": "/api/context-review",
            "run_audit": "/api/run-audit",
            "rethink": "/api/rethink",
            "artifacts": "/api/artifacts",
            "jobs": "/api/jobs",
            "interrupts": "/api/interrupts",
        },
    }


def _open_store_for_run(run_id: str):
    from pertura.core import Store
    d = Path("runs") / run_id
    return Store(d) if (d / "events.db").exists() else None


def _store_for_workbench(workbench, *, run_id: str = ""):
    rid = run_id or getattr(workbench, "_run_id", "")
    if rid and rid == getattr(workbench, "_run_id", "") and getattr(workbench, "_store", None):
        return workbench._store
    return _open_store_for_run(rid) if rid else None


def _snapshot_for_workbench(workbench, *, run_id: str = ""):
    store = _store_for_workbench(workbench, run_id=run_id)
    return store.read_snapshot() if store else None


def _analysis_spec_for_workbench(workbench, *, run_id: str = ""):
    snap = _snapshot_for_workbench(workbench, run_id=run_id)
    if snap and snap.analysis_spec:
        return snap.analysis_spec
    return workbench.domain.analysis_graph or {}


def _capability_registry_for_workbench(workbench, *, run_id: str = ""):
    from pertura.capabilities import CapabilityRegistry
    snap = _snapshot_for_workbench(workbench, run_id=run_id)
    if snap and snap.capabilities:
        return CapabilityRegistry(snap.capabilities)
    return CapabilityRegistry(getattr(workbench.domain, "capabilities", []) or [])


def _safe_payload(fn, *, default: dict) -> dict:
    try:
        payload = fn()
        return payload if isinstance(payload, dict) else default
    except Exception as exc:
        return {"error": str(exc)}


def _status_for_snapshot(snap) -> dict:
    if not snap:
        return {"state": "no_snapshot"}
    return {
        "run_id": snap.run_id,
        "phase": snap.phase,
        "workspace": snap.workspace,
        "goal": snap.goal,
        "attempts": len(snap.attempts),
        "observations": len(snap.observations),
        "artifacts": len(snap.artifacts),
        "conclusions": len(snap.conclusions),
        "triggers_open": len([item for item in snap.triggers if item.status == "open"]),
        "interrupts_open": len([item for item in snap.interrupts if item.status == "open"]),
        "branches": len(snap.branches),
    }


def _attempt_card(attempt, *, snap) -> dict:
    outcomes = [item for item in (snap.outcomes if snap else []) if item.attempt_id == attempt.attempt_id]
    observations = [item for item in (snap.observations if snap else []) if item.attempt_id == attempt.attempt_id]
    artifacts = [item for item in (snap.artifacts if snap else []) if item.attempt_id == attempt.attempt_id]
    last_outcome = outcomes[-1] if outcomes else None
    return {
        "attempt_id": attempt.attempt_id,
        "title": attempt.title,
        "status": attempt.status,
        "analysis_node_id": attempt.analysis_node_id,
        "branch_id": attempt.branch_id,
        "capability_ids": list(attempt.capability_ids),
        "outcome_status": last_outcome.status if last_outcome else "",
        "outcome_summary": last_outcome.summary if last_outcome else "",
        "observations": len(observations),
        "artifacts": len(artifacts),
        "created_at": str(attempt.created_at),
    }


def _artifact_card(artifact) -> dict:
    return {
        "artifact_id": artifact.artifact_id,
        "attempt_id": artifact.attempt_id,
        "kind": artifact.kind,
        "summary": artifact.summary,
        "path": artifact.path,
        "metadata": artifact.metadata,
        "preview_url": f"/api/artifacts/{artifact.artifact_id}/preview",
    }


def _audit_summary_card(audit: dict) -> dict:
    if not audit or "error" in audit:
        return audit or {}
    errors = audit.get("errors", []) or []
    warnings = audit.get("warnings", []) or []
    next_actions = audit.get("next_actions", []) or []
    return {
        "audit_type": audit.get("audit_type", "run_audit"),
        "ok": not errors,
        "errors": len(errors),
        "warnings": len(warnings),
        "next_actions": next_actions[:5],
    }


def _rethinking_card(payload: dict) -> dict:
    if not payload or "error" in payload:
        return payload or {}
    return {
        "view_type": payload.get("view_type", ""),
        "status": payload.get("status", ""),
        "summary": payload.get("summary", ""),
        "suspected_roots": (payload.get("suspected_roots", []) or [])[:5],
        "recommended_actions": (payload.get("recommended_actions", []) or [])[:5],
    }


def _report_summary_for_snapshot(snap) -> dict:
    if not snap:
        return {"available": False}
    return {
        "available": bool(snap.conclusions or snap.observations),
        "conclusions": [
            {
                "conclusion_id": item.conclusion_id,
                "text": item.text,
                "grade": item.grade,
                "support_count": len(item.support_ids),
                "limitation_count": len(item.limitation_ids),
            }
            for item in snap.conclusions[-8:]
        ],
        "observation_count": len(snap.observations),
        "artifact_count": len(snap.artifacts),
    }


def create_app(workbench):
    from fastapi import FastAPI, HTTPException
    from fastapi.responses import HTMLResponse
    from pydantic import BaseModel

    from pertura.core import Store, build_impact_view, build_trace_view
    from pertura.jobs import JobRunner
    from pertura.models import _model_dump
    from pertura.tools.registry import inspect_artifact_summary

    app = FastAPI(title="Pertura Workbench", version="1.0.0")
    runner = JobRunner()

    class RunRequest(BaseModel):
        workspace: str
        goal: str = ""
        steps: int = 5

    class AnswerRequest(BaseModel):
        answer: str

    class AnalysisSpecRequest(BaseModel):
        analysis_spec: dict
        reason: str = "api_update"

    class AnalysisSpecCompileRequest(BaseModel):
        analysis_spec: dict
        provider: str = "deterministic"
        apply: bool = False
        reason: str = "api_compile"
        domain_context: str = ""

    class DesignUpdateRequest(BaseModel):
        design: dict
        reason: str = "api_update"
        source: str = "api_confirmed"
        confidence: str = "high"

    def _store_for_run(run_id: str = ""):
        return _store_for_workbench(workbench, run_id=run_id)

    def _snapshot_for_run(run_id: str = ""):
        return _snapshot_for_workbench(workbench, run_id=run_id)

    def _graph_for_run(run_id: str = ""):
        store = _store_for_run(run_id)
        if store:
            graph = store.read_graph()
            if graph:
                return graph
        return workbench.graph or {"nodes": [], "edges": []}

    def _analysis_spec_for_run(run_id: str = ""):
        return _analysis_spec_for_workbench(workbench, run_id=run_id)

    def _capability_registry_for_run(run_id: str = ""):
        return _capability_registry_for_workbench(workbench, run_id=run_id)

    def _run_with_cancel(payload: dict, cancel_event):
        previous = getattr(workbench, "_cancel_event", None)
        workbench.set_cancel_event(cancel_event)
        try:
            return workbench.run(
                payload.get("workspace", ""),
                goal=payload.get("goal", ""),
                steps=int(payload.get("steps", 5)),
            )
        finally:
            workbench.set_cancel_event(previous)

    def _step_with_cancel(payload: dict, cancel_event):
        previous = getattr(workbench, "_cancel_event", None)
        workbench.set_cancel_event(cancel_event)
        try:
            return {"actions": workbench.step(int(payload.get("steps", 1)))}
        finally:
            workbench.set_cancel_event(previous)

    runner.register_handler("run", _run_with_cancel)
    runner.register_handler("step", _step_with_cancel)

    @app.get("/", response_class=HTMLResponse)
    def gui():
        tpl = Path(__file__).parent / "_gui.html"
        return tpl.read_text(encoding="utf-8").replace("{domain_name}", workbench.domain.name)

    @app.get("/api/status")
    def status():
        return workbench.status

    @app.get("/api/runtime-status")
    def runtime_status(recent: int = 20):
        payload = workbench.runtime_status(recent=recent)
        payload["jobs"] = runner.list_jobs()[:recent]
        return payload

    @app.get("/api/workbench-view")
    def workbench_view(run_id: str = "", max_items: int = 8, token_budget: int = 6000):
        return workbench_view_payload(
            workbench,
            run_id=run_id,
            max_items=max_items,
            token_budget=token_budget,
            jobs=runner.list_jobs()[:max_items],
        )

    @app.get("/api/graph")
    def graph(run_id: str = ""):
        return _graph_for_run(run_id)

    @app.get("/api/analysis-spec")
    def analysis_spec(run_id: str = ""):
        return _analysis_spec_for_run(run_id)

    @app.get("/api/domain")
    def domain_browser(include_core_tools: bool = True):
        return domain_browser_payload(workbench, include_core_tools=include_core_tools)

    @app.get("/api/domain/capabilities")
    def domain_capabilities(node_id: str = ""):
        payload = domain_browser_payload(workbench, include_core_tools=False)
        caps = payload.get("capabilities", [])
        if node_id:
            allowed = set(payload.get("capabilities_by_node", {}).get(node_id, []))
            caps = [item for item in caps if item.get("id") in allowed]
        return {
            "domain": payload.get("domain", {}),
            "node_id": node_id,
            "capabilities": caps,
        }

    @app.get("/api/analysis-spec/audit")
    def analysis_spec_audit(run_id: str = "", strict: bool = False):
        try:
            return analysis_spec_audit_payload(workbench, run_id=run_id, strict=strict)
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.get("/api/analysis-spec/contract")
    def analysis_spec_contract(node_id: str = "", run_id: str = ""):
        try:
            return analysis_spec_contract_payload(workbench, node_id=node_id, run_id=run_id)
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.get("/api/node-contract")
    def runtime_node_contract(node_id: str = "", run_id: str = ""):
        try:
            return runtime_node_contract_payload(workbench, node_id=node_id, run_id=run_id)
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.get("/api/context-review")
    def context_review(
        run_id: str = "",
        purpose: str = "audit",
        max_items: int = 8,
        token_budget: int = 6000,
    ):
        try:
            return context_review_payload(
                workbench,
                run_id=run_id,
                purpose=purpose,
                max_items=max_items,
                token_budget=token_budget,
            )
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.get("/api/run-audit")
    def run_audit(run_id: str = ""):
        try:
            return run_audit_payload(workbench, run_id=run_id)
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.get("/api/rethink")
    def rethink_latest(run_id: str = "", issue: str = "", depth: int = 5):
        try:
            return rethinking_payload(
                workbench,
                run_id=run_id,
                issue=issue,
                depth=depth,
            )
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.get("/api/rethink/{node_id}")
    def rethink_node(node_id: str, run_id: str = "", issue: str = "", depth: int = 5):
        try:
            return rethinking_payload(
                workbench,
                run_id=run_id,
                node_id=node_id,
                issue=issue,
                depth=depth,
            )
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.get("/api/harness-manifest")
    def harness_manifest():
        return harness_manifest_payload()

    @app.post("/api/analysis-spec")
    def update_analysis_spec(req: AnalysisSpecRequest):
        workbench.load_analysis_spec(req.analysis_spec, reason=req.reason)
        return workbench.status

    @app.post("/api/analysis-spec/compile")
    def compile_analysis_spec(req: AnalysisSpecCompileRequest):
        from pertura.spec.compiler import compile_conditions
        report = compile_conditions(
            req.analysis_spec,
            provider=req.provider,
            domain_context=req.domain_context,
        )
        payload = report.to_dict()
        if req.apply:
            workbench.load_analysis_spec(payload["spec"], reason=req.reason)
        return payload

    @app.post("/api/design")
    def update_design(req: DesignUpdateRequest):
        workbench.update_design(req.design, reason=req.reason,
                                source=req.source, confidence=req.confidence)
        return workbench.status

    @app.get("/api/trace/{node_id}")
    def trace_node(node_id: str, depth: int = 4, run_id: str = ""):
        graph = _graph_for_run(run_id)
        if not any(n.get("node_id") == node_id for n in graph.get("nodes", [])):
            raise HTTPException(404, f"Node {node_id} not found")
        return build_trace_view(graph, node_id, depth=depth)

    @app.get("/api/impact/{node_id}")
    def impact_node(node_id: str, depth: int = 4, run_id: str = ""):
        graph = _graph_for_run(run_id)
        if not any(n.get("node_id") == node_id for n in graph.get("nodes", [])):
            raise HTTPException(404, f"Node {node_id} not found")
        return build_impact_view(graph, node_id, depth=depth)

    @app.get("/api/report")
    def report():
        return workbench.report_preview()

    @app.post("/api/report/generate")
    def generate_report():
        return workbench.report()

    @app.post("/api/run")
    def run(req: RunRequest):
        result = workbench.run(req.workspace, goal=req.goal, steps=req.steps)
        return {**result, **workbench.status}

    @app.post("/api/step")
    def step():
        actions = workbench.step(1)
        return {"actions": actions, **workbench.status}

    @app.get("/api/runs")
    def list_runs():
        runs_dir = Path("runs")
        if not runs_dir.exists():
            return {"runs": []}
        runs = []
        for d in sorted(runs_dir.iterdir(), reverse=True):
            db = d / "events.db"
            if not db.exists():
                continue
            try:
                snap = Store(d).read_snapshot()
                runs.append({
                    "run_id": snap.run_id if snap else d.name,
                    "phase": snap.phase if snap else "unknown",
                    "workspace": snap.workspace if snap else "",
                    "goal": snap.goal if snap else "",
                    "attempts": len(snap.attempts) if snap else 0,
                    "observations": len(snap.observations) if snap else 0,
                })
            except Exception:
                runs.append({"run_id": d.name, "phase": "error"})
        return {"runs": runs}

    @app.get("/api/artifacts")
    def list_artifacts(run_id: str = ""):
        snap = _snapshot_for_run(run_id)
        if not snap:
            return {"artifacts": []}
        return {"artifacts": [_model_dump(a) for a in snap.artifacts]}

    @app.get("/api/artifacts/{artifact_id}/preview")
    def preview_artifact(artifact_id: str, run_id: str = ""):
        snap = _snapshot_for_run(run_id)
        if not snap:
            raise HTTPException(404, "No data")
        preview = inspect_artifact_summary(artifact_id=artifact_id, snap=snap)
        if "error" in preview:
            raise HTTPException(404, f"Artifact {artifact_id} not found")
        return {"artifact_id": artifact_id, **preview}

    @app.post("/api/jobs/run")
    def start_run_job(req: RunRequest):
        job = runner.submit(
            job_type="run",
            payload=_model_dump(req),
            run_id=getattr(workbench, "_run_id", ""),
        )
        return {"job_id": job.job_id, "status": "queued"}

    @app.post("/api/jobs/step")
    def start_step_job():
        job = runner.submit(
            job_type="step",
            payload={"steps": 1},
            run_id=getattr(workbench, "_run_id", ""),
        )
        return {"job_id": job.job_id, "status": "queued"}

    @app.get("/api/jobs")
    def list_jobs():
        return {"jobs": runner.list_jobs()}

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str):
        job = runner.get(job_id)
        if not job:
            raise HTTPException(404, f"Job {job_id} not found")
        return job

    @app.post("/api/jobs/{job_id}/cancel")
    def cancel_job(job_id: str):
        return {"cancelled": runner.cancel(job_id)}

    @app.post("/api/jobs/{job_id}/retry")
    def retry_job(job_id: str):
        job = runner.retry(job_id)
        if not job:
            raise HTTPException(409, f"Job {job_id} is not retryable")
        return job.to_dict()

    @app.post("/api/answer/{interrupt_id}")
    def answer(interrupt_id: str, req: AnswerRequest):
        workbench.answer(interrupt_id, req.answer)
        return workbench.status

    @app.get("/api/interrupts")
    def interrupts():
        snap = _snapshot_for_run()
        return {"interrupts": [
            _model_dump(i) for i in (snap.interrupts if snap else [])
            if i.status == "open"
        ]}

    return app
