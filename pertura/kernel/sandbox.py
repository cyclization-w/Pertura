"""Code execution sandbox: subprocess and Docker isolation."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path


def run_code(
    code: str,
    workspace: str,
    artifacts_dir: str,
    *,
    backend: str = "subprocess",
    docker_image: str = "",
    soft_timeout: float | None = None,
    hard_timeout: float | None = None,
    heartbeat_timeout: float | None = None,
    docker_options: dict | None = None,
) -> dict:
    """Execute Python code in the selected sandbox."""
    if backend == "docker":
        return _run_docker(
            code,
            workspace,
            artifacts_dir,
            docker_image,
            soft_timeout=soft_timeout,
            hard_timeout=hard_timeout,
            heartbeat_timeout=heartbeat_timeout,
            docker_options=docker_options or {},
        )
    return _run_subprocess(
        code,
        workspace,
        artifacts_dir,
        soft_timeout=soft_timeout,
        hard_timeout=hard_timeout,
        heartbeat_timeout=heartbeat_timeout,
    )


def _timeout_values(
    *,
    soft_timeout: float | None,
    hard_timeout: float | None,
    heartbeat_timeout: float | None,
) -> tuple[float, float, float]:
    soft = float(soft_timeout if soft_timeout is not None else os.getenv("PETURA_CELL_SOFT_TIMEOUT_SECONDS", "120"))
    hard = float(hard_timeout if hard_timeout is not None else os.getenv("PETURA_CELL_HARD_TIMEOUT_SECONDS", str(max(soft * 3, 900))))
    heartbeat = float(heartbeat_timeout if heartbeat_timeout is not None else os.getenv("PETURA_CELL_HEARTBEAT_TIMEOUT_SECONDS", "240"))
    return soft, hard, heartbeat


def _run_subprocess(
    code: str,
    workspace: str,
    artifacts_dir: str,
    *,
    soft_timeout: float | None = None,
    hard_timeout: float | None = None,
    heartbeat_timeout: float | None = None,
) -> dict:
    soft, hard, heartbeat = _timeout_values(
        soft_timeout=soft_timeout,
        hard_timeout=hard_timeout,
        heartbeat_timeout=heartbeat_timeout,
    )
    del heartbeat  # subprocess backend cannot heartbeat without a streaming runner yet.
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False, encoding="utf-8") as handle:
        handle.write(code)
        script_path = handle.name
    script = Path(script_path)
    start = time.time()
    try:
        result = subprocess.run(
            [sys.executable, str(script)],
            capture_output=True,
            text=True,
            timeout=hard,
            cwd=workspace,
            env={**os.environ, "WORKSPACE": workspace, "ARTIFACTS_DIR": artifacts_dir},
        )
        elapsed = round(time.time() - start, 3)
        stdout = result.stdout or ""
        stderr = result.stderr or ""
        return {
            "returncode": result.returncode,
            "stdout": stdout[-3000:],
            "stderr": stderr[-3000:],
            "partial_stdout": stdout[-4000:],
            "partial_stderr": stderr[-4000:],
            "timed_out": False,
            "timed_out_at": "",
            "soft_timeout_hit": elapsed > soft,
            "execution_time": elapsed,
        }
    except subprocess.TimeoutExpired as exc:
        stdout = _decode_timeout_output(exc.stdout)
        stderr = _decode_timeout_output(exc.stderr)
        return {
            "returncode": 124,
            "stdout": stdout[-3000:],
            "stderr": (stderr or f"Timed out after hard timeout ({hard}s)")[-3000:],
            "partial_stdout": stdout[-4000:],
            "partial_stderr": stderr[-4000:],
            "timed_out": True,
            "timed_out_at": "hard",
            "soft_timeout_hit": True,
            "execution_time": round(time.time() - start, 3),
        }
    finally:
        try:
            script.unlink()
        except OSError:
            pass


def _run_docker(
    code: str,
    workspace: str,
    artifacts_dir: str,
    image: str,
    *,
    soft_timeout: float | None = None,
    hard_timeout: float | None = None,
    heartbeat_timeout: float | None = None,
    docker_options: dict | None = None,
) -> dict:
    soft, hard, heartbeat = _timeout_values(
        soft_timeout=soft_timeout,
        hard_timeout=hard_timeout,
        heartbeat_timeout=heartbeat_timeout,
    )
    del heartbeat
    image = image or os.getenv("PETURA_DOCKER_IMAGE") or os.getenv("BLACKBOARD_DOCKER_IMAGE", "python:3.11-slim")
    options = dict(docker_options or {})
    memory = str(options.get("memory") or (
        f"{options.get('memory_gb')}g" if options.get("memory_gb") else os.getenv("PETURA_DOCKER_MEMORY", "2g")
    ))
    cpus = str(options.get("cpus") or os.getenv("PETURA_DOCKER_CPUS", "2"))
    pids_limit = str(options.get("pids_limit") or os.getenv("PETURA_DOCKER_PIDS_LIMIT", "256"))
    network = str(options.get("network") or os.getenv("PETURA_DOCKER_NETWORK", "none"))
    sandbox_dir = Path(artifacts_dir) / ".sandbox"
    sandbox_dir.mkdir(parents=True, exist_ok=True)

    script = sandbox_dir / "run.py"
    adapted = code.replace(workspace, "/workspace").replace(artifacts_dir, "/artifacts")
    script.write_text(adapted, encoding="utf-8")

    cmd = [
        "docker", "run", "--rm",
        "--network", network,
        "--memory", memory,
        "--cpus", cpus,
        "--pids-limit", pids_limit,
        "--security-opt", "no-new-privileges",
        "-v", f"{workspace}:/workspace:ro",
        "-v", f"{artifacts_dir}:/artifacts",
        "-w", "/workspace",
        image,
        "python", "/artifacts/.sandbox/run.py",
    ]
    start = time.time()
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=hard)
        elapsed = round(time.time() - start, 3)
        stdout = result.stdout or ""
        stderr = result.stderr or ""
        return {
            "returncode": result.returncode,
            "stdout": stdout[-3000:],
            "stderr": stderr[-3000:],
            "partial_stdout": stdout[-4000:],
            "partial_stderr": stderr[-4000:],
            "timed_out": False,
            "timed_out_at": "",
            "soft_timeout_hit": elapsed > soft,
            "execution_time": elapsed,
        }
    except subprocess.TimeoutExpired as exc:
        stdout = _decode_timeout_output(exc.stdout)
        stderr = _decode_timeout_output(exc.stderr)
        return {
            "returncode": 124,
            "stdout": stdout[-3000:],
            "stderr": (stderr or f"Docker execution timed out after hard timeout ({hard}s)")[-3000:],
            "partial_stdout": stdout[-4000:],
            "partial_stderr": stderr[-4000:],
            "timed_out": True,
            "timed_out_at": "hard",
            "soft_timeout_hit": True,
            "execution_time": round(time.time() - start, 3),
        }
    except FileNotFoundError:
        return _sandbox_error("Docker not found. Install Docker or use backend='subprocess'.")
    except OSError as exc:
        return _sandbox_error(str(exc))


def _decode_timeout_output(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _sandbox_error(message: str) -> dict:
    return {
        "returncode": 127,
        "stdout": "",
        "stderr": message,
        "partial_stdout": "",
        "partial_stderr": message,
        "timed_out": False,
        "timed_out_at": "",
        "soft_timeout_hit": False,
        "execution_time": 0,
    }
