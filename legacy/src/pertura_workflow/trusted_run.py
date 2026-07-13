from __future__ import annotations

from pathlib import Path
from typing import Any

from pertura_gate.evidence.execution_ledger import _append_execution_record


def record_trusted_run(
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
    """Record a Pertura-controlled runner execution in the canonical ledger.

    This is the workflow-side write path for trusted runs. The gate only reads
    the canonical ledger under the registry run root; artifact-supplied ledger
    paths are diagnostic and do not establish trust.
    """

    return _append_execution_record(
        workspace,
        execution_hash=execution_hash,
        runner_name=runner_name,
        runner_version=runner_version,
        method=method,
        input_hashes=input_hashes,
        output_hashes=output_hashes,
        parameters=parameters,
    )
