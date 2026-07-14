from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from pertura_core.hashing import canonical_hash, file_sha256
from pertura_bench.models import BenchmarkSubsetLock


MANIFEST_NAME = "reference-generation-manifest.json"


def generate_reference(
    *,
    dataset_id: str,
    split: str,
    subset_lock: Path,
    generator_script: Path,
    environment_lock: Path,
    parameters: Path,
    output: Path,
) -> dict[str, Any]:
    sources = (subset_lock, generator_script, environment_lock, parameters)
    if any(not path.is_file() for path in sources):
        missing = [str(path) for path in sources if not path.is_file()]
        raise FileNotFoundError("reference inputs are missing: " + ", ".join(missing))
    subset = BenchmarkSubsetLock.model_validate_json(
        subset_lock.read_text(encoding="utf-8")
    )
    if subset.schema_version != "pertura-benchmark-subset-lock-v2":
        raise ValueError("formal references require a subset v2 lock")
    if subset.dataset_id != dataset_id:
        raise ValueError("reference dataset does not match the subset lock")
    if str(subset.selection_summary.get("split") or "") != split:
        raise ValueError("reference split does not match the subset lock")
    destination = output.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    if any(destination.iterdir()):
        raise ValueError("reference output directory must be empty")
    command = (
        [sys.executable, str(generator_script.resolve())]
        if generator_script.suffix.lower() == ".py"
        else ["Rscript", "--vanilla", str(generator_script.resolve())]
    )
    command.append(str(parameters.resolve()))
    completed = subprocess.run(
        command,
        cwd=destination,
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            "reference generator failed: " + completed.stderr[-4000:]
        )
    files = _artifact_hashes(destination)
    if not files:
        raise RuntimeError("reference generator produced no artifacts")
    payload = {
        "schema_version": "pertura-reference-generation-v1",
        "dataset_id": dataset_id,
        "split": split,
        "subset_lock_hash": file_sha256(subset_lock),
        "generator_script_hash": file_sha256(generator_script),
        "environment_lock_hash": file_sha256(environment_lock),
        "parameter_hash": file_sha256(parameters),
        "artifacts": files,
        "provenance_hash": canonical_hash(
            {
                "dataset_id": dataset_id,
                "split": split,
                "subset_lock": file_sha256(subset_lock),
                "generator": file_sha256(generator_script),
                "environment": file_sha256(environment_lock),
                "parameters": file_sha256(parameters),
                "artifacts": files,
            }
        ),
    }
    (destination / MANIFEST_NAME).write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return payload


def validate_reference(root: Path) -> dict[str, Any]:
    directory = root.resolve()
    manifest_path = directory / MANIFEST_NAME
    if not manifest_path.is_file():
        return {"ok": False, "problems": ["reference manifest is missing"]}
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    problems: list[str] = []
    if payload.get("schema_version") != "pertura-reference-generation-v1":
        problems.append("reference manifest schema is unsupported")
    expected = dict(payload.get("artifacts") or {})
    observed = _artifact_hashes(directory)
    if expected != observed:
        problems.append("reference artifact set/hash drift")
    return {
        "ok": not problems,
        "problems": problems,
        "manifest_hash": file_sha256(manifest_path),
        "provenance_hash": payload.get("provenance_hash"),
    }


def freeze_reference(root: Path, output: Path) -> dict[str, Any]:
    verdict = validate_reference(root)
    if not verdict["ok"]:
        raise ValueError("cannot freeze invalid reference: " + "; ".join(verdict["problems"]))
    manifest_path = root.resolve() / MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload = {
        "schema_version": "pertura-frozen-reference-v1",
        "dataset_id": manifest["dataset_id"],
        "split": manifest["split"],
        "generation_manifest_sha256": file_sha256(manifest_path),
        "provenance_hash": manifest["provenance_hash"],
        "artifacts": manifest["artifacts"],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return payload | {"output": str(output.resolve())}


def _artifact_hashes(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): file_sha256(path)
        for path in sorted(root.rglob("*"))
        if path.is_file() and path.name != MANIFEST_NAME
    }
