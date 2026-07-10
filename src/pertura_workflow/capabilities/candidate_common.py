from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Iterable

from pertura_core import (
    AnalysisStatus,
    CapabilityRunRequest,
    CapabilitySpec,
    DatasetContract,
    DiagnosticStatus,
    ResultEnvelope,
)
from pertura_core.hashing import file_sha256


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


def resource_budget(parameters: dict[str, Any]) -> tuple[float, int]:
    max_memory_gb = float(parameters.get("max_memory_gb", 4.0))
    n_jobs = int(parameters.get("n_jobs", 1))
    if max_memory_gb <= 0:
        raise ValueError("max_memory_gb must be positive")
    if n_jobs < 1:
        raise ValueError("n_jobs must be at least one")
    return max_memory_gb, n_jobs


def envelope(
    spec: CapabilitySpec,
    request: CapabilityRunRequest,
    contract: DatasetContract,
    *,
    status: DiagnosticStatus | AnalysisStatus,
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
        output_hashes={path.name: file_sha256(path) for path in output_list},
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
    status = DiagnosticStatus.blocked if spec.kind == "diagnostic" else AnalysisStatus.blocked
    return envelope(
        spec,
        request,
        contract,
        status=status,
        summary=f"{spec.capability_id} was blocked.",
        blockers=reasons,
        metadata=metadata,
    )


def caution_status(spec: CapabilitySpec) -> DiagnosticStatus | AnalysisStatus:
    return DiagnosticStatus.caution if spec.kind == "diagnostic" else AnalysisStatus.completed_with_caution


def success_status(spec: CapabilitySpec) -> DiagnosticStatus | AnalysisStatus:
    return DiagnosticStatus.screen_passed if spec.kind == "diagnostic" else AnalysisStatus.completed
