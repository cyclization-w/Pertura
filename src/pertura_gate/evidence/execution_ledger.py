from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

LEDGER_RELATIVE_PATH = Path("artifacts") / "execution_ledger.jsonl"
TRUSTED_RUN_WRITER_ID = "pertura_trusted_run"


def file_sha256(path: str | Path) -> str:
    target = Path(path)
    digest = hashlib.sha256()
    with target.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def canonical_execution_hash(payload: dict[str, Any]) -> str:
    canonical = json.dumps(_canonicalize(payload), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _append_execution_record(
    workspace: str | Path,
    *,
    execution_hash: str,
    runner_name: str,
    runner_version: str,
    method: str,
    input_hashes: dict[str, str] | None = None,
    output_hashes: dict[str, str] | None = None,
    parameters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not execution_hash:
        raise ValueError("execution_hash is required")
    root = Path(workspace).resolve()
    ledger_path = root / LEDGER_RELATIVE_PATH
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "schema_version": "pertura-execution-ledger-v1",
        "execution_hash": execution_hash,
        "runner_name": runner_name,
        "runner_version": runner_version,
        "method": method,
        "writer_id": TRUSTED_RUN_WRITER_ID,
        "input_hashes": dict(input_hashes or {}),
        "output_hashes": dict(output_hashes or {}),
        "parameters": _canonicalize(dict(parameters or {})),
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    with ledger_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True, ensure_ascii=True) + "\n")
    return {
        **record,
        "execution_ledger_path": str(ledger_path),
        "execution_ledger_relative_path": str(LEDGER_RELATIVE_PATH).replace("\\", "/"),
    }


def ledger_contains_execution_hash(
    path: str | Path,
    execution_hash: str,
    *,
    method: str | None = None,
    source_sha256: str | None = None,
) -> bool:
    if not execution_hash:
        return False
    ledger_path = Path(path)
    if not ledger_path.exists():
        return False
    expected_method = _normalize(method) if method else None
    for line in ledger_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if record.get("execution_hash") != execution_hash:
            continue
        if record.get("writer_id") != TRUSTED_RUN_WRITER_ID:
            continue
        if expected_method and _normalize(record.get("method")) != expected_method:
            continue
        if source_sha256:
            output_hashes = record.get("output_hashes") or {}
            if not isinstance(output_hashes, dict) or source_sha256 not in set(output_hashes.values()):
                continue
        return True
    return False


def artifact_execution_is_in_ledger(artifact, run_root: str | Path, *, method: str | None = None) -> bool:
    execution_hash = getattr(artifact, "execution_hash", None)
    if not execution_hash:
        return False
    ledger_path = Path(run_root).resolve() / LEDGER_RELATIVE_PATH
    return ledger_contains_execution_hash(
        ledger_path,
        execution_hash,
        method=method,
        source_sha256=getattr(artifact, "source_sha256", None),
    )


def _normalize(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "_")


def _canonicalize(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _canonicalize(value[key]) for key in sorted(value)}
    if isinstance(value, (list, tuple)):
        return [_canonicalize(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    return value
