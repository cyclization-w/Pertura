"""Attempt execution: kernel run, hooks, manifest parsing, circuit breaker."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from uuid import uuid4

from pertura.models import (
    Attempt, Outcome, Artifact, Observation, Interrupt, Finding,
    RuntimeJob, _model_dump,
)


def _emit_execution_output(wb, attempt_id: str, stream: str, text: str, *, max_chars: int = 2000) -> None:
    if not text:
        return
    for line in text.splitlines(True) or [text]:
        chunk = line[-max_chars:]
        try:
            wb._emit("execution_output", {
                "attempt_id": attempt_id,
                "stream": stream,
                "text": chunk,
                "truncated": len(line) > max_chars,
            })
        except Exception:
            pass


def _execute_attempt(wb, attempt: Attempt) -> str:
    """Execute notebook code in kernel/sandbox, parse manifest, run hooks."""
    from pertura.hooks import pre_execute

    if wb._cancel_requested():
        return wb._pause_for_cancel(attempt.attempt_id)

    domain_context = wb.domain.runtime_context()
    code = _build_notebook_code(
        attempt, wb._store.read_snapshot().workspace,
        str(wb._store.run_dir / "artifacts"),
        domain_context.get("audit_preamble", ""),
    )
    cell_code = _build_cell_code(attempt)
    execution_code = cell_code if wb.sandbox == "kernel" else code

    # Safety check
    workspace = wb._store.read_snapshot().workspace
    artifacts_dir = wb._store.run_dir / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    for evt_type, payload, _actor in pre_execute(execution_code, workspace, str(artifacts_dir)):
        if payload.get("severity") == "blocking":
            wb._emit(evt_type, payload)
            wb._emit("outcome_recorded", {"outcome": _model_dump(Outcome(
                outcome_id=f"out_{uuid4().hex[:12]}", attempt_id=attempt.attempt_id,
                status="error",
                summary=f"Safety violation: {payload.get('violations', ['unknown'])[0]}",
                metrics={"returncode": 1, "safety_blocked": True},
            ))})
            return "blocked"
        if evt_type:
            wb._emit(evt_type, payload)

    # Execute
    if attempt.parameters.get("execution_kind") == "job":
        result = _run_attempt_job(wb, attempt, code, workspace, str(artifacts_dir))
    else:
        result = _run_attempt_code(wb, attempt, execution_code, workspace, str(artifacts_dir))

    # Parse manifest
    manifest_path = artifacts_dir / f"{attempt.attempt_id}_manifest.json"
    obs_count = 0
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            for art in manifest.get("artifacts", []):
                wb._emit("artifact_registered", {"artifact": _model_dump(Artifact(
                    artifact_id=f"art_{uuid4().hex[:12]}",
                    attempt_id=attempt.attempt_id, path=art.get("path", ""),
                    kind=art.get("kind", ""), summary=art.get("summary", ""),
                    metadata=art.get("metadata", {}),
                ))})
            for obs in manifest.get("observations", []):
                wb._emit("observation_registered", {"observation": _model_dump(Observation(
                    observation_id=f"obs_{uuid4().hex[:12]}",
                    type=obs.get("type", "custom"),
                    target=obs.get("target", ""), metric=obs.get("metric", ""),
                    value=obs.get("value"), contrast=obs.get("contrast", ""),
                    method=obs.get("method", ""),
                    parameters=obs.get("parameters", {}),
                    uncertainty=obs.get("uncertainty", {}),
                    attempt_id=attempt.attempt_id,
                    branch_id=attempt.branch_id,
                    artifact_id=obs.get("artifact_id", ""),
                    variable_key=obs.get("variable_key", ""),
                    input_ids=obs.get("input_ids", []),
                    design_fields_used=obs.get("design_fields_used", attempt.design_fields_used),
                    parameter_hash=obs.get("parameter_hash", ""),
                    method_version=obs.get("method_version", ""),
                ))})
                obs_count += 1
        except Exception as exc:
            wb._emit("finding_recorded", {"finding": _model_dump(Finding(
                finding_id=f"fnd_{uuid4().hex[:12]}",
                attempt_id=attempt.attempt_id,
                finding_type="manifest_error",
                severity="warning",
                suggested_action="continue",
                summary=f"Manifest parse error: {exc}",
            ))})

    from pertura.hooks import post_capability_contract
    snap_mid = wb._store.read_snapshot()
    for evt_type, payload, _actor in post_capability_contract(attempt, snap_mid, result):
        wb._emit(evt_type, payload)

    # Notebook recording
    _append_notebook(wb._store.run_dir, attempt, execution_code, result)

    # Record outcome after manifest parsing so behaviors can review observations.
    outcome = Outcome(
        outcome_id=f"out_{uuid4().hex[:12]}", attempt_id=attempt.attempt_id,
        status="success" if result.get("returncode") == 0 else "error",
        summary=f"returncode={result.get('returncode')}, stdout={len(result.get('stdout', '') or '')} chars",
        metrics={
            "returncode": result.get("returncode"),
            "timed_out": result.get("timed_out", False),
            "timed_out_at": result.get("timed_out_at", ""),
            "soft_timeout_hit": result.get("soft_timeout_hit", False),
            "execution_time": result.get("execution_time"),
            "observations_registered": obs_count,
            "stdout_chars": len(result.get("stdout", "") or ""),
            "stderr": (result.get("stderr", "") or "")[-500:],
            "kernel_state": _compact_kernel_state(result.get("kernel_state", {})),
        },
    )
    wb._emit("outcome_recorded", {"outcome": _model_dump(outcome)})
    if result.get("soft_timeout_hit"):
        wb._emit("attempt_soft_timeout", {
            "attempt_id": attempt.attempt_id,
            "execution_time": result.get("execution_time"),
            "hard_timeout_reached": result.get("timed_out_at") in {"hard", "heartbeat"},
            "timed_out_at": result.get("timed_out_at", ""),
            "partial_stdout": (result.get("stdout", "") or "")[-2000:],
            "partial_stderr": (result.get("stderr", "") or "")[-2000:],
        })

    if wb._cancel_requested():
        wb._emit("run_paused", {"reason": "job_cancel_requested"})
        return "cancelled"

    # Circuit breaker: 5 consecutive failed-or-empty cells
    snap_now = wb._store.read_snapshot()
    recent = [a for a in snap_now.attempts[-8:]
              if a.status in ("succeeded", "failed")]
    dead_streak = 0
    for a in reversed(recent):
        outcome = next((o for o in snap_now.outcomes
                       if o.attempt_id == a.attempt_id), None)
        has_obs = any(o.attempt_id == a.attempt_id for o in snap_now.observations)
        has_output = (outcome and
                      outcome.status == "success" and
                      (outcome.metrics or {}).get("stdout_chars", 0) > 100)
        if not has_obs and not has_output:
            dead_streak += 1
        else:
            break
    if dead_streak >= 5:
        wb._emit("interrupt_opened", {"interrupt": _model_dump(Interrupt(
            interrupt_id=f"irq_{uuid4().hex[:12]}", source="circuit_breaker",
            question=f"{dead_streak} consecutive cells produced no observations "
                     "and no meaningful output. LLM may be stuck. "
                     "Type 'continue' or give new instructions.",
        ))})
        return "waiting_for_human"

    # Tool loop: assess + choose the next state-changing tool.
    from pertura.agent.tool_loop import run_tool_loop
    from pertura.agent.gated_dispatch import gated_dispatch

    decision, next_code = "ask_user", ""
    assessment = {}
    decision_dict = {}
    if _has_key(wb.provider):
        try:
            snap_after = wb._store.read_snapshot()
            decision, next_code, assessment, decision_dict = run_tool_loop(
                result=result, obs_count=obs_count,
                snap=snap_after, attempt=attempt,
                provider=wb.provider, emit=wb._emit,
                coding_guidelines=domain_context.get("coding_guidelines", ""),
                protocol=domain_context.get("protocol", ""),
                tools=domain_context.get("tools", ""),
            )
        except Exception:
            import traceback
            traceback.print_exc(file=sys.stderr)

    wb._emit("review_decision_recorded", {"review_id": f"rev_{attempt.attempt_id}",
        "attempt_id": attempt.attempt_id, "decision": decision,
        "assessment_status": assessment.get("status", ""),
        "assessment_summary": assessment.get("summary", ""),
        "reason": decision_dict.get("reason", ""),
        "evidence_ids": decision_dict.get("evidence_ids", []),
        "branch_id": decision_dict.get("branch_id", ""),
        "parent_attempt_id": decision_dict.get("parent_attempt_id", ""),
    })

    return gated_dispatch(wb, decision, next_code, assessment, decision_dict,
                          snap_now, parent_attempt=attempt)


def _has_key(provider: str) -> bool:
    from pertura.core.fixtures import fixture_mode
    if fixture_mode() in {"replay", "strict"}:
        return True
    from pertura.planner import _api_key, _anthropic_key
    if provider == "anthropic":
        return bool(_anthropic_key())
    return bool(_api_key())


def _compact_kernel_state(state: dict | None, *, limit: int = 50) -> dict:
    if not isinstance(state, dict):
        return {}
    variables = state.get("variables", {})
    imports = state.get("imports", [])
    if not isinstance(variables, dict):
        variables = {}
    if not isinstance(imports, list):
        imports = []
    variables = dict(sorted(variables.items(), key=lambda item: str(item[0])))
    imports = sorted(str(item) for item in imports)
    return {
        "variables": dict(list(variables.items())[:limit]),
        "imports": imports[:limit],
    }


def _run_attempt_code(wb, attempt: Attempt, code: str, workspace: str, artifacts_dir: str) -> dict:
    default_soft = float(os.getenv("PETURA_CELL_SOFT_TIMEOUT_SECONDS", os.getenv("PETURA_CELL_TIMEOUT_SECONDS", "120")))
    expected_runtime = attempt.parameters.get("expected_runtime_seconds") if attempt.parameters else None
    soft_timeout = float(expected_runtime or default_soft)
    hard_timeout = float(os.getenv("PETURA_CELL_HARD_TIMEOUT_SECONDS", str(max(soft_timeout * 3, 900))))
    heartbeat_timeout = float(os.getenv("PETURA_CELL_HEARTBEAT_TIMEOUT_SECONDS", "240"))
    if wb.sandbox == "kernel":
        try:
            from pertura.kernel.session import KernelSession
            if wb._kernel is None:
                wb._kernel = KernelSession(workspace, artifacts_dir)
            return wb._kernel.execute(
                attempt.attempt_id,
                code,
                soft_timeout=soft_timeout,
                hard_timeout=hard_timeout,
                heartbeat_timeout=heartbeat_timeout,
                on_output=lambda stream, text: _emit_execution_output(
                    wb, attempt.attempt_id, stream, text
                ),
            )
        except Exception as exc:
            return {
                "returncode": 1,
                "stdout": "",
                "stderr": f"Kernel execution failed: {exc}",
                "timed_out": False,
            }

    from pertura.kernel.sandbox import run_code as _run_sandbox
    return _run_sandbox(
        code,
        workspace,
        artifacts_dir,
        backend=wb.sandbox,
        docker_image=wb.docker_image,
        soft_timeout=soft_timeout,
        hard_timeout=hard_timeout,
        heartbeat_timeout=heartbeat_timeout,
    )


def _run_attempt_job(wb, attempt: Attempt, code: str, workspace: str, artifacts_dir: str) -> dict:
    from datetime import datetime, timezone
    from pertura.kernel.sandbox import run_code as _run_sandbox

    params = attempt.parameters or {}
    resources = dict(params.get("resources", {}) or {})
    backend = str(params.get("job_backend") or resources.get("backend") or "subprocess")
    if backend not in {"subprocess", "docker"}:
        backend = "subprocess"

    run_dir = wb._store.run_dir
    scripts_dir = run_dir / "scripts"
    jobs_dir = run_dir / "jobs"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    jobs_dir.mkdir(parents=True, exist_ok=True)

    job_id = f"job_{uuid4().hex[:12]}"
    script_path = scripts_dir / f"{attempt.attempt_id}_{job_id}.py"
    log_path = jobs_dir / f"{job_id}.log"
    manifest_path = Path(artifacts_dir) / f"{attempt.attempt_id}_manifest.json"
    script_path.write_text(code, encoding="utf-8")
    started_at = datetime.now(timezone.utc).isoformat()

    wb._emit("job_submitted", {"job": _model_dump(RuntimeJob(
        job_id=job_id,
        attempt_id=attempt.attempt_id,
        capability_id=(attempt.capability_ids[0] if attempt.capability_ids else ""),
        backend=backend,
        resources=resources,
        status="running",
        script_path=str(script_path),
        log_path=str(log_path),
        manifest_path=str(manifest_path),
        started_at=started_at,
    ))})

    timeout_minutes = resources.get("timeout_minutes")
    hard_timeout = float(timeout_minutes) * 60 if timeout_minutes else float(
        os.getenv("PETURA_JOB_HARD_TIMEOUT_SECONDS", "3600")
    )
    soft_timeout = float(resources.get("soft_timeout_seconds") or os.getenv(
        "PETURA_JOB_SOFT_TIMEOUT_SECONDS", str(min(hard_timeout, 600))
    ))
    heartbeat_timeout = float(resources.get("heartbeat_timeout_seconds") or os.getenv(
        "PETURA_JOB_HEARTBEAT_TIMEOUT_SECONDS", "600"
    ))
    docker_image = str(resources.get("docker_image") or wb.docker_image or "")

    result = _run_sandbox(
        code,
        workspace,
        artifacts_dir,
        backend=backend,
        docker_image=docker_image,
        soft_timeout=soft_timeout,
        hard_timeout=hard_timeout,
        heartbeat_timeout=heartbeat_timeout,
        docker_options=resources,
    )
    log_path.write_text(
        "STDOUT\n" + (result.get("partial_stdout") or result.get("stdout") or "") +
        "\n\nSTDERR\n" + (result.get("partial_stderr") or result.get("stderr") or ""),
        encoding="utf-8",
    )
    finished_at = datetime.now(timezone.utc).isoformat()
    status = "succeeded" if result.get("returncode") == 0 else "failed"
    wb._emit("job_completed", {
        "job_id": job_id,
        "status": status,
        "finished_at": finished_at,
        "log_path": str(log_path),
        "manifest_path": str(manifest_path),
        "result": {
            "returncode": result.get("returncode"),
            "timed_out": result.get("timed_out", False),
            "timed_out_at": result.get("timed_out_at", ""),
            "execution_time": result.get("execution_time"),
        },
    })
    return result


# ── Notebook execution helpers ─────────────────────────────────────────

def _build_notebook_code(attempt: Attempt, workspace: str, artifacts_dir: str,
                         audit_preamble: str) -> str:
    preamble = audit_preamble or _DEFAULT_PREAMBLE
    preamble = (
        preamble
        .replace('"{workspace}"', repr(str(workspace)))
        .replace('"{artifacts_dir}"', repr(str(artifacts_dir)))
        .replace("{workspace}", str(workspace))
        .replace("{artifacts_dir}", str(artifacts_dir))
    )
    preamble = preamble.replace('"attempt_id": ""',
                               f'"attempt_id": "{attempt.attempt_id}"')
    cells = [preamble]
    for c in attempt.notebook_cells:
        cells.append(c.get("source", "") if isinstance(c, dict) else c.source)
    return "\n\n# %% CELL\n\n".join(cells)


def _build_cell_code(attempt: Attempt) -> str:
    cells = []
    for c in attempt.notebook_cells:
        cells.append(c.get("source", "") if isinstance(c, dict) else c.source)
    return "\n\n# %% CELL\n\n".join(cells)


_DEFAULT_PREAMBLE = '''
import atexit, json, sys
from pathlib import Path

workspace = Path("{workspace}")
artifacts_dir = Path("{artifacts_dir}")
artifacts_dir.mkdir(parents=True, exist_ok=True)

_manifest = {"manifest_id": "manifest", "attempt_id": "", "artifacts": [], "observations": []}

def register_artifact(path, kind, summary="", metadata=None):
    _manifest["artifacts"].append({"path": str(path), "kind": str(kind), "summary": str(summary), "metadata": dict(metadata or {})})

def register_observation(type, target="", metric="", value=None, contrast="", method="", parameters=None, uncertainty=None, artifact_id="", variable_key="", input_ids=None, design_fields_used=None, parameter_hash="", method_version=""):
    _manifest["observations"].append({"type": str(type), "target": str(target), "metric": str(metric), "value": value, "contrast": str(contrast), "method": str(method), "parameters": dict(parameters or {}), "uncertainty": dict(uncertainty or {}), "artifact_id": str(artifact_id), "variable_key": str(variable_key), "input_ids": list(input_ids or []), "design_fields_used": list(design_fields_used or []), "parameter_hash": str(parameter_hash), "method_version": str(method_version)})

def _flush():
    path = artifacts_dir / (_manifest.get("attempt_id", "unknown") + "_manifest.json")
    path.write_text(json.dumps(_manifest, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print("MANIFEST: " + str(path), file=sys.stderr)

atexit.register(_flush)
print("Audit contract ready.", file=sys.stderr)
'''


def _append_notebook(run_dir: Path, attempt: Attempt, code: str, result: dict):
    try:
        import nbformat as nbf
        from nbformat.v4 import new_code_cell, new_markdown_cell, new_output

        nb_dir = run_dir / "notebooks"
        nb_dir.mkdir(parents=True, exist_ok=True)
        nb_path = nb_dir / "execution.ipynb"

        if nb_path.exists():
            nb = nbf.read(nb_path, as_version=4)
        else:
            nb = nbf.v4.new_notebook(metadata={
                "kernelspec": {"display_name": "Python 3", "language": "python",
                              "name": "python3"},
                "language_info": {"name": "python", "pygments_lexer": "ipython3"},
            })

        nb.cells.append(new_markdown_cell(
            f"## Cell {len([c for c in nb.cells if c.cell_type == 'markdown']) + 1}"
            f" — {attempt.stage or 'analysis'}\n"
            f"**{attempt.title or 'Step'}** — {attempt.objective or ''}"
        ))

        cell = new_code_cell(code)
        if result.get("stdout"):
            cell.outputs.append(new_output("stream", name="stdout",
                text=result["stdout"][-3000:] or ""))
        if result.get("stderr"):
            cell.outputs.append(new_output("stream", name="stderr",
                text=result["stderr"][-3000:] or ""))
        if result.get("returncode", 0) != 0:
            cell.outputs.append(new_output("error", ename="RuntimeError",
                evalue=f"Return code: {result.get('returncode')}",
                traceback=[(result.get('stderr', '') or 'Unknown error').split('\n')[0]]))
        nb.cells.append(cell)

        nbf.write(nb, nb_path)
    except ImportError:
        print("[workbench] nbformat not installed — skipping .ipynb output.",
              file=sys.stderr)
