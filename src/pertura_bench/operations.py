from __future__ import annotations

import csv
import hashlib
import json
import shutil
import subprocess
import tomllib
import urllib.request
from pathlib import Path
from typing import Any, BinaryIO, Callable

from pertura_core.hashing import canonical_hash, file_sha256
from pertura_bench.models import (
    BenchmarkArtifactLock,
    BenchmarkSourceManifest,
    BenchmarkSplitManifest,
    BenchmarkSubsetLock,
    BenchmarkSubsetSpec,
)


def require_repo_root(repo_root: str | Path) -> Path:
    """Return the authoritative inner Pertura repository or fail with a hint."""

    root = Path(repo_root).expanduser().resolve()
    pyproject = root / "pyproject.toml"
    nested = root / "pertura" / "pyproject.toml"
    if not pyproject.is_file():
        hint = (
            f"; detected nested Pertura checkout at {nested.parent}"
            if nested.is_file()
            else ""
        )
        raise ValueError(
            f"--repo must point to the inner Pertura repository containing pyproject.toml{hint}"
        )
    try:
        project = tomllib.loads(pyproject.read_text(encoding="utf-8")).get("project") or {}
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ValueError(f"cannot read repository pyproject.toml: {exc}") from exc
    if project.get("name") != "pertura":
        hint = (
            f"; detected nested Pertura checkout at {nested.parent}"
            if nested.is_file()
            else ""
        )
        raise ValueError(
            "--repo pyproject.toml does not declare project.name = 'pertura'"
            + hint
        )
    if not (root / ".git").exists():
        raise ValueError("--repo is not the authoritative Pertura Git worktree")
    return root


def load_source_manifest(path: str | Path) -> BenchmarkSourceManifest:
    return BenchmarkSourceManifest.model_validate_json(Path(path).read_text(encoding="utf-8"))


def source_manifests(repo_root: str | Path) -> dict[str, tuple[Path, BenchmarkSourceManifest]]:
    root = require_repo_root(repo_root)
    loaded = {}
    for path in sorted((root / "benchmarks" / "manifests").glob("*.json")):
        manifest = load_source_manifest(path)
        if manifest.dataset_id in loaded:
            raise ValueError(f"duplicate benchmark dataset_id: {manifest.dataset_id}")
        loaded[manifest.dataset_id] = (path, manifest)
    return loaded


def validate_repository(repo_root: str | Path) -> dict[str, Any]:
    manifests = source_manifests(repo_root)
    return {
        "schema_version": "pertura-benchmark-validation-v1",
        "valid": len(manifests) == 4,
        "dataset_count": len(manifests),
        "datasets": sorted(manifests),
        "problems": [] if len(manifests) == 4 else ["exactly four v0.2 source manifests are required"],
    }


def fetch_benchmark(
    manifest: BenchmarkSourceManifest,
    cache: str | Path,
    *,
    opener: Callable[..., Any] = urllib.request.urlopen,
) -> tuple[BenchmarkArtifactLock, Path]:
    if not manifest.file:
        raise ValueError("benchmark source is conversion-only and cannot be fetched directly")
    cache_path = Path(cache).expanduser().resolve()
    cache_path.mkdir(parents=True, exist_ok=True)
    destination = cache_path / str(manifest.file["name"])
    temporary = destination.with_suffix(destination.suffix + ".part")
    expected_size = int(manifest.file["size_bytes"])
    md5 = hashlib.md5()  # noqa: S324 - verifies an upstream-published checksum only
    sha256 = hashlib.sha256()
    request = urllib.request.Request(str(manifest.file["download_url"]), headers={"User-Agent": "pertura-benchmark-fetch"})
    with opener(request, timeout=300) as response, temporary.open("wb") as handle:
        while chunk := response.read(1024 * 1024):
            handle.write(chunk)
            md5.update(chunk)
            sha256.update(chunk)
    if temporary.stat().st_size != expected_size:
        temporary.unlink(missing_ok=True)
        raise ValueError("benchmark download size mismatch")
    if md5.hexdigest() != manifest.file["supplied_md5"]:
        temporary.unlink(missing_ok=True)
        raise ValueError("benchmark download checksum mismatch")
    temporary.replace(destination)
    lock = BenchmarkArtifactLock(
        dataset_id=manifest.dataset_id,
        source_manifest_hash=manifest.canonical_hash,
        artifact_sha256="sha256:" + sha256.hexdigest(),
        size_bytes=destination.stat().st_size,
        upstream_checksum="md5:" + md5.hexdigest(),
        license_status="reviewed",
    )
    lock_path = cache_path / f"{manifest.dataset_id}.lock.json"
    if lock_path.exists():
        existing = BenchmarkArtifactLock.model_validate_json(lock_path.read_text(encoding="utf-8"))
        if existing.canonical_hash != lock.canonical_hash:
            raise ValueError("benchmark lock already exists with different content")
    lock_path.write_text(json.dumps(lock.model_dump(mode="json"), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (cache_path / f"{manifest.dataset_id}.local.json").write_text(
        json.dumps({"artifact_path": str(destination), "lock_id": lock.lock_id}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return lock, destination


def finalize_conversion(
    manifest: BenchmarkSourceManifest,
    output_path: str | Path,
    conversion_script: str | Path,
    *,
    package_versions: dict[str, str] | None = None,
) -> BenchmarkArtifactLock:
    output = Path(output_path).resolve()
    script = Path(conversion_script).resolve()
    if not output.is_file() or output.stat().st_size <= 0:
        raise ValueError("benchmark conversion output is missing or empty")
    if not script.is_file():
        raise ValueError("benchmark conversion script is missing")
    return BenchmarkArtifactLock(
        dataset_id=manifest.dataset_id,
        source_manifest_hash=manifest.canonical_hash,
        artifact_sha256=file_sha256(output),
        size_bytes=output.stat().st_size,
        conversion_script_hash=file_sha256(script),
        parameters={"output_format": output.suffix.lower().lstrip(".")},
        package_versions=package_versions or {},
        license_status="reviewed",
    )


def run_conversion(
    manifest: BenchmarkSourceManifest,
    *,
    repo_root: str | Path,
    cache: str | Path,
    rscript: str = "Rscript",
) -> tuple[BenchmarkArtifactLock, Path]:
    if not manifest.conversion:
        raise ValueError("benchmark manifest has no versioned conversion script")
    root = Path(repo_root).resolve()
    script = (root / manifest.conversion).resolve()
    if root not in script.parents:
        raise ValueError("benchmark conversion script escapes the repository")
    destination = Path(cache).resolve() / f"{manifest.dataset_id}.h5ad"
    destination.parent.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(
        [rscript, str(script), str(destination)],
        text=True, encoding="utf-8", errors="replace", capture_output=True, timeout=7200, check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError("benchmark conversion failed: " + completed.stderr[-4000:])
    sidecar = destination.with_suffix(destination.suffix + ".manifest.json")
    raw = json.loads(sidecar.read_text(encoding="utf-8")) if sidecar.is_file() else {}
    lock = finalize_conversion(manifest, destination, script, package_versions=dict(raw.get("packages") or {}))
    lock_path = destination.parent / f"{manifest.dataset_id}.lock.json"
    lock_path.write_text(json.dumps(lock.model_dump(mode="json"), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (destination.parent / f"{manifest.dataset_id}.local.json").write_text(
        json.dumps({"artifact_path": str(destination), "lock_id": lock.lock_id}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return lock, destination


def stable_target_split(target_ids: list[str], modality: str, *, seed: int = 1729) -> BenchmarkSplitManifest:
    unique = sorted(set(target_ids))
    if len(unique) < 2:
        raise ValueError("at least two targets are required for a benchmark split")
    ordered = sorted(unique, key=lambda value: hashlib.sha256(f"{seed}:{modality}:{value}".encode("utf-8")).hexdigest())
    cut = min(len(ordered) - 1, max(1, int(len(ordered) * 0.60)))
    return BenchmarkSplitManifest(
        modality=modality,
        seed=seed,
        calibration_ids=tuple(sorted(ordered[:cut])),
        evaluation_ids=tuple(sorted(ordered[cut:])),
    )


def subset_h5ad(input_path: str | Path, output_path: str | Path, spec: BenchmarkSubsetSpec) -> BenchmarkSubsetLock:
    import anndata as ad
    import numpy as np

    source = Path(input_path).resolve()
    destination = Path(output_path).resolve()
    data = ad.read_h5ad(source, backed="r")
    if spec.label_column not in data.obs.columns:
        raise ValueError(f"missing label column: {spec.label_column}")
    rng = np.random.default_rng(spec.seed)
    selected: list[int] = []
    observed = data.obs[spec.label_column].astype(str).to_numpy()
    for label in sorted(spec.labels):
        indices = np.flatnonzero(observed == label)
        if len(indices) > spec.max_cells_per_label:
            indices = np.sort(rng.choice(indices, spec.max_cells_per_label, replace=False))
        selected.extend(indices.tolist())
    if not selected:
        raise ValueError("benchmark subset selection retained no cells")
    subset = data[sorted(selected)].to_memory()
    destination.parent.mkdir(parents=True, exist_ok=True)
    subset.write_h5ad(destination)
    script = Path(__file__).resolve()
    return BenchmarkSubsetLock(
        dataset_id=spec.dataset_id,
        subset_spec_hash=spec.canonical_hash,
        source_lock_hash=spec.source_lock_hash,
        output_sha256=file_sha256(destination),
        n_cells=subset.n_obs,
        n_genes=subset.n_vars,
        subset_script_hash=file_sha256(script),
    )


def write_annotation_packet(modality: str, output_dir: str | Path) -> dict[str, str]:
    destination = Path(output_dir).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    csv_path = destination / f"{modality}_expert_annotation.csv"
    fields = [
        "dataset_id", "target_id", "expected_direction", "reviewer_1_verdict",
        "reviewer_1_reasons", "reviewer_2_verdict", "reviewer_2_reasons",
        "adjudicator_id", "adjudicated_verdict", "adjudicated_reasons",
    ]
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        csv.writer(handle).writerow(fields)
    instructions = destination / f"{modality}_expert_annotation.json"
    instructions.write_text(json.dumps({
        "schema_version": "pertura-expert-annotation-packet-v1",
        "modality": modality,
        "minimum_total_targets": 50,
        "minimum_evaluation_targets": 20,
        "review_process": "two independent reviewers followed by explicit adjudication",
        "csv_sha256": file_sha256(csv_path),
    }, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"csv": str(csv_path), "instructions": str(instructions)}
