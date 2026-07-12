from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from pertura_core import (
    AnalysisStatus,
    CapabilityRunRequest,
    CapabilitySpec,
    DatasetContract,
    DiagnosticStatus,
    ResultEnvelope,
    VirtualStatus,
)
from pertura_core.hashing import path_sha256


def resolve_input(
    contract: DatasetContract,
    value: Any,
    *,
    label: str,
    required: bool = True,
) -> Path | None:
    if value in (None, ""):
        if required:
            raise ValueError(f"{label} is required")
        return None
    candidate = Path(str(value)).expanduser()
    roots = [Path(item).expanduser().resolve() for item in contract.source_paths]
    from pertura_workflow.capabilities.execution_context import execution_context
    roots.extend(
        Path(item).expanduser().resolve()
        for item in execution_context().get("authorized_asset_paths", ())
    )
    if not candidate.is_absolute():
        directories = [item for item in roots if item.is_dir()]
        if not directories:
            raise ValueError(f"relative {label} requires a directory DatasetContract source")
        candidate = directories[0] / candidate
    resolved = candidate.resolve()
    if not any(resolved == root or (root.is_dir() and root in resolved.parents) for root in roots):
        raise ValueError(f"{label} is not bound to the authoritative DatasetContract")
    if required and not resolved.exists():
        raise FileNotFoundError(resolved)
    return resolved


def delimiter_for(path: Path) -> str:
    return "\t" if path.suffix.lower() in {".tsv", ".txt"} else ","


def read_rows(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter=delimiter_for(path))
        if not reader.fieldnames:
            raise ValueError(f"table has no header: {path.name}")
        return list(reader.fieldnames), [
            {key: str(value or "").strip() for key, value in row.items()}
            for row in reader
        ]


def write_json(staging: Path, name: str, payload: dict[str, Any]) -> Path:
    path = staging / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def dependency_results(staging: Path) -> list[dict[str, Any]]:
    path = staging / "_dependency_results.json"
    if not path.is_file():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    return list(payload.get("results") or [])


def runtime_dependencies(staging: Path) -> list[dict[str, Any]]:
    path = staging / "_runtime_dependencies.json"
    if not path.is_file():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    return list(payload.get("dependencies") or [])


@dataclass(frozen=True)
class ResourceBudget:
    max_memory_gb: float = 4.0
    n_jobs: int = 1
    chunk_rows: int = 1024

    def __iter__(self):
        yield self.max_memory_gb
        yield self.n_jobs

    @property
    def max_bytes(self) -> int:
        return int(self.max_memory_gb * 1024**3)

    def dense_bytes(self, rows: int, columns: int, *, arrays: int = 1, itemsize: int = 8) -> int:
        return int(rows) * int(columns) * int(arrays) * int(itemsize)

    def require_dense(self, rows: int, columns: int, *, arrays: int = 1, label: str) -> None:
        required = self.dense_bytes(rows, columns, arrays=arrays)
        if required > self.max_bytes:
            raise MemoryError(
                f"{label} requires {required / 1024**3:.3f} GB, exceeding max_memory_gb={self.max_memory_gb}"
            )


def resource_budget(parameters: dict[str, Any], *, columns_hint: int = 2000) -> ResourceBudget:
    max_memory_gb = float(parameters.get("max_memory_gb", 4.0))
    n_jobs = int(parameters.get("n_jobs", 1))
    if max_memory_gb <= 0:
        raise ValueError("max_memory_gb must be positive")
    if n_jobs < 1:
        raise ValueError("n_jobs must be at least one")
    requested_chunk = parameters.get("chunk_rows")
    if requested_chunk is None:
        usable = max(1, int(max_memory_gb * 1024**3 * 0.25))
        chunk_rows = max(32, min(8192, usable // max(1, columns_hint * 8 * 4)))
    else:
        chunk_rows = int(requested_chunk)
        if chunk_rows < 1:
            raise ValueError("chunk_rows must be at least one")
    return ResourceBudget(max_memory_gb=max_memory_gb, n_jobs=n_jobs, chunk_rows=chunk_rows)


def envelope(
    spec: CapabilitySpec,
    request: CapabilityRunRequest,
    contract: DatasetContract,
    *,
    status: DiagnosticStatus | AnalysisStatus | VirtualStatus,
    summary: str,
    blockers: Iterable[str] = (),
    cautions: Iterable[str] = (),
    metrics: dict[str, Any] | None = None,
    outputs: Iterable[Path] = (),
    metadata: dict[str, Any] | None = None,
) -> ResultEnvelope:
    output_list = tuple(outputs)
    return ResultEnvelope(
        run_id=request.run_id,
        request_id=request.request_id,
        capability_id=spec.capability_id,
        capability_version=spec.version,
        capability_trust=spec.trust_level,
        contract_id=contract.contract_id,
        contract_hash=contract.canonical_hash,
        scope=request.scope,
        status=status,
        result_kind=spec.output_kind,
        source_class=spec.source_class,
        summary=summary,
        blockers=tuple(dict.fromkeys(str(item) for item in blockers)),
        cautions=tuple(dict.fromkeys(str(item) for item in cautions)),
        metrics=metrics or {},
        output_paths=tuple(path.name for path in output_list),
        output_hashes={path.name: path_sha256(path) for path in output_list},
        dependencies=request.dependencies,
        metadata={
            "validation_status": "synthetic_only",
            "candidate": True,
        }
        | (metadata or {}),
    )


def blocked(
    spec: CapabilitySpec,
    request: CapabilityRunRequest,
    contract: DatasetContract,
    *reasons: str,
    metadata: dict[str, Any] | None = None,
) -> ResultEnvelope:
    if spec.kind == "diagnostic":
        status: DiagnosticStatus | AnalysisStatus | VirtualStatus = DiagnosticStatus.blocked
    elif spec.kind == "virtual":
        status = VirtualStatus.out_of_scope
    else:
        status = AnalysisStatus.blocked
    return envelope(
        spec,
        request,
        contract,
        status=status,
        summary=f"{spec.capability_id} was blocked.",
        blockers=reasons,
        metadata=metadata,
    )


def caution_status(
    spec: CapabilitySpec,
) -> DiagnosticStatus | AnalysisStatus | VirtualStatus:
    if spec.kind == "diagnostic":
        return DiagnosticStatus.caution
    if spec.kind == "virtual":
        return VirtualStatus.limited
    return AnalysisStatus.completed_with_caution


def success_status(
    spec: CapabilitySpec,
) -> DiagnosticStatus | AnalysisStatus | VirtualStatus:
    if spec.kind == "diagnostic":
        return DiagnosticStatus.screen_passed
    if spec.kind == "virtual":
        return VirtualStatus.supported
    return AnalysisStatus.completed
