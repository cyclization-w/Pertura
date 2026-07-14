from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_resource_evidence(path: str | Path | None) -> dict[str, Any]:
    if path is None:
        raise FileNotFoundError(
            "not_configured: scheduler/cgroup resource evidence is required"
        )
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError("resource evidence file is missing")
    payload = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("resource evidence must be a JSON object")
    if payload.get("schema_version") != "pertura-resource-evidence-v1":
        raise ValueError("resource evidence schema is unsupported")
    if str(payload.get("mode") or "") not in {"scheduler", "cgroup"}:
        raise ValueError("resource evidence mode must be scheduler or cgroup")
    for name in (
        "requested_memory_gb",
        "actual_memory_gb",
        "peak_rss_mb",
        "cpu_count",
        "n_jobs",
        "timeout_seconds",
        "wall_clock_seconds",
    ):
        try:
            value = float(payload[name])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"resource evidence lacks numeric {name}") from exc
        if value <= 0:
            raise ValueError(f"resource evidence {name} must be positive")
    if not payload.get("scheduler_job_id") and not payload.get("cgroup_identity"):
        raise ValueError("resource evidence lacks scheduler/cgroup identity")
    if not isinstance(payload.get("thread_environment"), dict):
        raise ValueError("resource evidence lacks thread_environment")
    return payload


def validate_resource_request(
    evidence: dict[str, Any],
    *,
    memory_gb: float,
    n_jobs: int,
    observed_peak_rss_mb: float | None = None,
) -> None:
    if float(evidence["requested_memory_gb"]) != float(memory_gb):
        raise ValueError("scheduler and capability memory requests disagree")
    if int(evidence["n_jobs"]) != int(n_jobs):
        raise ValueError("scheduler and capability n_jobs requests disagree")
    if float(evidence["actual_memory_gb"]) < float(memory_gb):
        raise ValueError("actual scheduler memory is below the requested budget")
    if observed_peak_rss_mb is not None and observed_peak_rss_mb > (
        float(evidence["actual_memory_gb"]) * 1024.0
    ):
        raise ValueError("observed peak RSS exceeds scheduler memory evidence")
