from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any


def load_resource_evidence(path: str | Path | None) -> dict[str, Any]:
    if path is None:
        raise FileNotFoundError(
            "not_configured: resource budget evidence is required"
        )
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError("resource evidence file is missing")
    payload = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("resource evidence must be a JSON object")
    if payload.get("schema_version") != "pertura-resource-evidence-v1":
        raise ValueError("resource evidence schema is unsupported")
    mode = str(payload.get("mode") or "")
    if mode not in {"scheduler", "cgroup", "rlimit"}:
        raise ValueError(
            "resource evidence mode must be scheduler, cgroup, or rlimit"
        )
    if mode == "scheduler":
        payload = _bind_scheduler_allocation(payload)
    for name in (
        "requested_memory_gb",
        "actual_memory_gb",
        "cpu_count",
        "n_jobs",
        "timeout_seconds",
    ):
        try:
            value = float(payload[name])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"resource evidence lacks numeric {name}") from exc
        if value <= 0:
            raise ValueError(f"resource evidence {name} must be positive")
    # peak RSS and elapsed time are observations.  A paper workflow loads this
    # file before it runs, so neither value may be required in the input
    # template.  The runner fills both fields after work has actually occurred.
    for name in ("peak_rss_mb", "wall_clock_seconds"):
        try:
            value = float(payload.get(name, 0.0))
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"resource evidence has invalid numeric {name}"
            ) from exc
        if value < 0:
            raise ValueError(f"resource evidence {name} cannot be negative")
        payload[name] = value
    if mode == "scheduler" and not payload.get("scheduler_job_id"):
        raise ValueError("resource evidence lacks scheduler job identity")
    if mode == "cgroup" and not payload.get("cgroup_identity"):
        raise ValueError("resource evidence lacks cgroup identity")
    if mode == "rlimit":
        # Never trust a caller to assert that a process limit was applied.  The
        # runner overwrites these fields only after setrlimit succeeds.
        payload.pop("rlimit_identity", None)
        payload.pop("rlimit_as_bytes", None)
        payload["enforcement_active"] = False
    if not isinstance(payload.get("thread_environment"), dict):
        raise ValueError("resource evidence lacks thread_environment")
    return payload


def _bind_scheduler_allocation(payload: dict[str, Any]) -> dict[str, Any]:
    """Bind scheduler evidence to the allocation exported by Slurm itself."""

    observed = dict(payload)
    slurm_job_id = str(os.environ.get("SLURM_JOB_ID") or "")
    if not slurm_job_id:
        observed["allocation_source"] = "resource_evidence_file"
        return observed
    recorded_job_id = str(observed.get("scheduler_job_id") or "")
    if recorded_job_id and recorded_job_id != slurm_job_id:
        raise ValueError("resource evidence scheduler job identity disagrees with Slurm")

    # ``SLURM_CPUS_ON_NODE`` is the scheduler allocation, not necessarily the
    # requested task parallelism.  Sherlock may allocate extra billing CPUs to
    # satisfy a large per-node memory request even when ``--cpus-per-task=1``.
    # Preserve the requested/effective task concurrency and record the larger
    # scheduler allocation separately.
    cpus = int(
        observed.get("requested_cpus_per_task")
        or observed.get("cpu_count")
        or 0
    )
    # Sherlock can expose its memory-derived billing allocation through both
    # SLURM_CPUS_ON_NODE and SLURM_CPUS_PER_TASK even when the submitted paper
    # job requested one task CPU.  The signed launcher evidence is authoritative
    # for requested task concurrency; Slurm's larger value is retained only as
    # allocation evidence.
    allocated_cpus = int(
        os.environ.get("SLURM_CPUS_ON_NODE")
        or os.environ.get("SLURM_CPUS_PER_TASK")
        or cpus
        or 0
    )
    memory_mb: float | None = None
    memory_source = ""
    if os.environ.get("SLURM_MEM_PER_NODE"):
        memory_mb = float(os.environ["SLURM_MEM_PER_NODE"])
        memory_source = "SLURM_MEM_PER_NODE"
    elif os.environ.get("SLURM_MEM_PER_CPU"):
        memory_mb = float(os.environ["SLURM_MEM_PER_CPU"]) * allocated_cpus
        memory_source = "SLURM_MEM_PER_CPU"
    if memory_mb is None or memory_mb <= 0 or cpus <= 0 or allocated_cpus <= 0:
        raise ValueError("Slurm allocation lacks positive memory or CPU evidence")

    memory_gb = memory_mb / 1024.0
    observed.update(
        {
            "scheduler_job_id": slurm_job_id,
            # The Slurm allocation is authoritative.  Do not retain stale
            # launcher-template values such as the historical 8 GB canary
            # placeholder after a 32/48 GB workflow was actually submitted.
            "requested_memory_gb": memory_gb,
            "actual_memory_gb": memory_gb,
            "cpu_count": cpus,
            "requested_cpus_per_task": cpus,
            "allocated_cpus_on_node": allocated_cpus,
            "allocation_source": "slurm_environment",
            "slurm_memory_source": memory_source,
            "slurm_memory_mb": memory_mb,
        }
    )
    return observed


def enforce_runtime_resource_budget(evidence: dict[str, Any]) -> dict[str, Any]:
    """Apply the portable paper-runner memory limit when requested.

    Scheduler and cgroup modes are enforced outside this process.  ``rlimit``
    is the fallback for servers without a user scheduler or writable cgroup.
    It is deliberately applied by the runner rather than trusted from JSON.
    """

    observed = dict(evidence)
    if observed.get("mode") != "rlimit":
        return observed
    requested = float(observed["requested_memory_gb"])
    actual = float(observed["actual_memory_gb"])
    if actual < requested:
        raise ValueError("actual rlimit memory is below the requested budget")
    limit_bytes = int(actual * 1024**3)
    enforced_bytes = _apply_rlimit_as(limit_bytes)
    if enforced_bytes <= 0 or enforced_bytes > limit_bytes:
        raise RuntimeError(
            "RLIMIT_AS did not enforce the requested memory ceiling"
        )
    observed.update(
        {
            "enforcement_active": True,
            "rlimit_identity": f"pid:{os.getpid()}:RLIMIT_AS",
            "rlimit_as_bytes": enforced_bytes,
        }
    )
    return observed


def observe_runtime_resources(
    evidence: dict[str, Any], *, started_monotonic: float
) -> dict[str, Any]:
    """Add observations collected by the running benchmark process."""

    observed = dict(evidence)
    observed["wall_clock_seconds"] = max(
        float(observed.get("wall_clock_seconds", 0.0)),
        time.monotonic() - started_monotonic,
    )
    peak_rss_mb, source = _peak_rss_mb()
    observed["peak_rss_mb"] = max(
        float(observed.get("peak_rss_mb", 0.0)), peak_rss_mb
    )
    observed["observation_source"] = source
    return observed


def _apply_rlimit_as(limit_bytes: int) -> int:
    if os.name != "posix":
        raise RuntimeError("rlimit resource enforcement requires a POSIX host")
    import resource

    soft, hard = resource.getrlimit(resource.RLIMIT_AS)
    if hard != resource.RLIM_INFINITY and hard < limit_bytes:
        raise RuntimeError(
            "existing RLIMIT_AS hard limit is below the benchmark budget"
        )
    resource.setrlimit(resource.RLIMIT_AS, (limit_bytes, hard))
    enforced, _ = resource.getrlimit(resource.RLIMIT_AS)
    return int(enforced)


def _peak_rss_mb() -> tuple[float, str]:
    if os.name != "posix":
        return 0.0, "unavailable:non_posix"
    import resource

    self_peak = float(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    child_peak = float(resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss)
    # Linux reports KiB; macOS reports bytes.  Formal paper runs are Linux, but
    # retaining the conversion makes this helper unambiguous on both systems.
    divisor = 1024.0 * 1024.0 if sys.platform == "darwin" else 1024.0
    return max(self_peak, child_peak) / divisor, "getrusage:self_or_child_max"


def validate_resource_request(
    evidence: dict[str, Any],
    *,
    memory_gb: float,
    n_jobs: int,
    observed_peak_rss_mb: float | None = None,
) -> None:
    if float(evidence["requested_memory_gb"]) != float(memory_gb):
        raise ValueError("resource and capability memory requests disagree")
    if int(evidence["n_jobs"]) != int(n_jobs):
        raise ValueError("resource and capability n_jobs requests disagree")
    if float(evidence["actual_memory_gb"]) < float(memory_gb):
        raise ValueError("actual resource memory is below the requested budget")
    if observed_peak_rss_mb is not None and observed_peak_rss_mb > (
        float(evidence["actual_memory_gb"]) * 1024.0
    ):
        raise ValueError("observed peak RSS exceeds resource memory evidence")
