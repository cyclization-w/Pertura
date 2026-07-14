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
    mixed_source = bool(manifest.conversion)
    artifact_root = (
        cache_path / "datasets" / manifest.dataset_id / "source"
        if mixed_source
        else cache_path
    )
    artifact_root.mkdir(parents=True, exist_ok=True)
    destination = artifact_root / str(manifest.file["name"])
    temporary = destination.with_suffix(destination.suffix + ".part")
    expected_size = int(manifest.file["size_bytes"])
    md5 = hashlib.md5()  # noqa: S324 - verifies an upstream-published checksum only
    sha256 = hashlib.sha256()
    downloaded = False
    if destination.is_file():
        with destination.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                md5.update(chunk)
                sha256.update(chunk)
        if destination.stat().st_size != expected_size:
            raise ValueError("cached benchmark source size mismatch")
    else:
        request = urllib.request.Request(str(manifest.file["download_url"]), headers={"User-Agent": "pertura-benchmark-fetch"})
        with opener(request, timeout=300) as response, temporary.open("wb") as handle:
            while chunk := response.read(1024 * 1024):
                handle.write(chunk)
                md5.update(chunk)
                sha256.update(chunk)
        downloaded = True
        if temporary.stat().st_size != expected_size:
            temporary.unlink(missing_ok=True)
            raise ValueError("benchmark download size mismatch")
    if md5.hexdigest() != manifest.file["supplied_md5"]:
        temporary.unlink(missing_ok=True)
        raise ValueError("benchmark download checksum mismatch")
    if downloaded:
        temporary.replace(destination)
    lock = BenchmarkArtifactLock(
        dataset_id=manifest.dataset_id,
        source_manifest_hash=manifest.canonical_hash,
        artifact_sha256="sha256:" + sha256.hexdigest(),
        size_bytes=destination.stat().st_size,
        upstream_checksum="md5:" + md5.hexdigest(),
        license_status=manifest.license_status,
    )
    lock_path = (
        artifact_root / "artifact.lock.json"
        if mixed_source
        else cache_path / f"{manifest.dataset_id}.lock.json"
    )
    if lock_path.exists():
        existing = BenchmarkArtifactLock.model_validate_json(lock_path.read_text(encoding="utf-8"))
        if existing.canonical_hash != lock.canonical_hash:
            raise ValueError("benchmark lock already exists with different content")
    lock_path.write_text(json.dumps(lock.model_dump(mode="json"), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    local_path = (
        artifact_root / "artifact.local.json"
        if mixed_source
        else cache_path / f"{manifest.dataset_id}.local.json"
    )
    local_path.write_text(
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
    upstream_lock_hash: str | None = None,
) -> BenchmarkArtifactLock:
    output = Path(output_path).resolve()
    script = Path(conversion_script).resolve()
    if not output.is_file() or output.stat().st_size <= 0:
        raise ValueError("benchmark conversion output is missing or empty")
    if not script.is_file():
        raise ValueError("benchmark conversion script is missing")
    if manifest.file and not upstream_lock_hash:
        raise ValueError(
            "benchmark conversion with a downloadable source requires an upstream lock hash"
        )
    return BenchmarkArtifactLock(
        dataset_id=manifest.dataset_id,
        source_manifest_hash=manifest.canonical_hash,
        artifact_sha256=file_sha256(output),
        size_bytes=output.stat().st_size,
        upstream_lock_hash=upstream_lock_hash,
        conversion_script_hash=file_sha256(script),
        parameters={"output_format": output.suffix.lower().lstrip(".")},
        package_versions=package_versions or {},
        license_status=manifest.license_status,
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
    cache_root = Path(cache).expanduser().resolve()
    source_path: Path | None = None
    upstream_lock_hash: str | None = None
    if manifest.file:
        source_root = cache_root / "datasets" / manifest.dataset_id / "source"
        source_lock_path = source_root / "artifact.lock.json"
        source_local_path = source_root / "artifact.local.json"
        if not source_lock_path.is_file() or not source_local_path.is_file():
            raise FileNotFoundError(
                "benchmark conversion source lock chain is missing; run fetch first"
            )
        source_lock = BenchmarkArtifactLock.model_validate_json(
            source_lock_path.read_text(encoding="utf-8")
        )
        if source_lock.dataset_id != manifest.dataset_id:
            raise ValueError("benchmark conversion source lock dataset mismatch")
        if source_lock.source_manifest_hash != manifest.canonical_hash:
            raise ValueError("benchmark conversion source manifest hash drift")
        source_sidecar = json.loads(source_local_path.read_text(encoding="utf-8"))
        if source_sidecar.get("lock_id") != source_lock.lock_id:
            raise ValueError("benchmark conversion source sidecar lock identity mismatch")
        source_path = Path(str(source_sidecar.get("artifact_path") or "")).resolve()
        if not source_path.is_file():
            raise FileNotFoundError("benchmark conversion source artifact is missing")
        if source_path.stat().st_size != source_lock.size_bytes:
            raise ValueError("benchmark conversion source size mismatch")
        if file_sha256(source_path) != source_lock.artifact_sha256:
            raise ValueError("benchmark conversion source checksum mismatch")
        upstream_lock_hash = source_lock.canonical_hash

    artifact_root = (
        cache_root / "datasets" / manifest.dataset_id / "converted"
        if manifest.file
        else cache_root
    )
    destination = (
        artifact_root / "artifact.h5ad"
        if manifest.file
        else artifact_root / f"{manifest.dataset_id}.h5ad"
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    command = [rscript, str(script), str(destination)]
    if source_path is not None:
        command.append(str(source_path))
    completed = subprocess.run(
        command,
        text=True, encoding="utf-8", errors="replace", capture_output=True, timeout=7200, check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError("benchmark conversion failed: " + completed.stderr[-4000:])
    sidecar = destination.with_suffix(destination.suffix + ".manifest.json")
    raw = json.loads(sidecar.read_text(encoding="utf-8")) if sidecar.is_file() else {}
    if not isinstance(raw, dict):
        raise RuntimeError("benchmark conversion sidecar must contain a JSON object")
    packages = raw.get("packages", {})
    if not isinstance(packages, dict) or not all(
        isinstance(name, str) and isinstance(version, str)
        for name, version in packages.items()
    ):
        raise RuntimeError(
            "benchmark conversion sidecar field 'packages' must be a string-to-string JSON object"
        )
    lock = finalize_conversion(
        manifest,
        destination,
        script,
        package_versions=packages,
        upstream_lock_hash=upstream_lock_hash,
    )
    lock_path = (
        artifact_root / "artifact.lock.json"
        if manifest.file
        else destination.parent / f"{manifest.dataset_id}.lock.json"
    )
    lock_path.write_text(json.dumps(lock.model_dump(mode="json"), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    local_path = (
        artifact_root / "artifact.local.json"
        if manifest.file
        else destination.parent / f"{manifest.dataset_id}.local.json"
    )
    local_path.write_text(
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
    rng = np.random.default_rng(spec.seed)
    if spec.schema_version.endswith("v1"):
        if spec.label_column not in data.obs.columns:
            raise ValueError(f"missing label column: {spec.label_column}")
        selected: list[int] = []
        observed = data.obs[spec.label_column].astype(str).to_numpy()
        for label in sorted(spec.labels):
            indices = np.flatnonzero(observed == label)
            if len(indices) > spec.max_cells_per_label:
                indices = np.sort(
                    rng.choice(indices, spec.max_cells_per_label, replace=False)
                )
            selected.extend(indices.tolist())
        summary = {"selection_version": "v1", "labels": list(spec.labels)}
    else:
        selected, summary = _select_v2_cells(data.obs, spec, rng)
    if not selected:
        raise ValueError("benchmark subset selection retained no cells")
    selected_indices = sorted(selected)
    selected_ids = [str(data.obs_names[index]) for index in selected_indices]
    if len(selected_ids) != len(set(selected_ids)):
        raise ValueError("benchmark subset contains duplicate cell identities")
    subset = _chunked_anndata_subset(data, selected_indices, spec)
    destination.parent.mkdir(parents=True, exist_ok=True)
    subset.write_h5ad(destination)
    selection_manifest = destination.parent / "selection.ids.json"
    selection_manifest.write_text(
        json.dumps(selected_ids, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    script = Path(__file__).resolve()
    return BenchmarkSubsetLock(
        schema_version=(
            "pertura-benchmark-subset-lock-v1"
            if spec.schema_version.endswith("v1")
            else "pertura-benchmark-subset-lock-v2"
        ),
        dataset_id=spec.dataset_id,
        subset_spec_hash=spec.canonical_hash,
        source_lock_hash=spec.source_lock_hash,
        output_sha256=file_sha256(destination),
        n_cells=subset.n_obs,
        n_genes=subset.n_vars,
        subset_script_hash=file_sha256(script),
        selected_ids_sha256=canonical_hash(selected_ids),
        selection_manifest_sha256=file_sha256(selection_manifest),
        selected_groups_sha256=(
            canonical_hash(sorted(spec.selected_groups))
            if spec.selected_groups
            else None
        ),
        selected_control_units_sha256=(
            canonical_hash(sorted(spec.selected_control_units))
            if spec.selected_control_units
            else None
        ),
        selection_summary=summary,
    )


def _filter_mask(obs, filter_spec: dict[str, Any]):
    import numpy as np

    column = str(filter_spec["column"])
    if column not in obs.columns:
        raise ValueError(f"missing subset filter column: {column}")
    values = obs[column].astype(str)
    operator = str(filter_spec["op"])
    if operator == "eq":
        return (values == str(filter_spec["value"])).to_numpy()
    if operator == "not_eq":
        return (values != str(filter_spec["value"])).to_numpy()
    choices = {str(item) for item in filter_spec.get("values") or ()}
    mask = values.isin(choices).to_numpy()
    return np.logical_not(mask) if operator == "not_in" else mask


def _select_v2_cells(obs, spec: BenchmarkSubsetSpec, rng) -> tuple[list[int], dict[str, Any]]:
    import numpy as np

    required = {
        str(spec.unit_id_column),
        str(spec.group_column),
        *spec.strata_columns,
    }
    missing = sorted(required - set(obs.columns))
    if missing:
        raise ValueError("missing v2 subset columns: " + ", ".join(missing))
    mask = np.ones(len(obs), dtype=bool)
    for filter_spec in spec.include_filters:
        mask &= _filter_mask(obs, filter_spec)
    for filter_spec in spec.exclude_filters:
        mask &= ~_filter_mask(obs, filter_spec)

    groups = obs[str(spec.group_column)].astype(str).to_numpy()
    units = obs[str(spec.unit_id_column)].astype(str).to_numpy()
    case_mask = np.isin(groups, list(spec.selected_groups))
    control_mask = np.zeros(len(obs), dtype=bool)
    if spec.control_selector is not None:
        control_mask = _filter_mask(obs, spec.control_selector)
        if not spec.selected_control_units:
            raise ValueError(
                "v2 control selection requires explicit selected_control_units"
            )
        control_mask &= np.isin(units, list(spec.selected_control_units))
    mask &= case_mask | control_mask
    candidate = np.flatnonzero(mask)
    if not len(candidate):
        raise ValueError("v2 subset filters retained no cells")

    # Sample independently within role and declared strata.  This preserves
    # replicate/donor structure without ever loading the expression matrix.
    buckets: dict[tuple[str, ...], list[int]] = {}
    for index in candidate.tolist():
        role = "control" if control_mask[index] else "case"
        key = (role,) + tuple(
            str(obs.iloc[index][column]) for column in spec.strata_columns
        )
        buckets.setdefault(key, []).append(index)
    selected: list[int] = []
    counts: dict[str, int] = {}
    for key in sorted(buckets):
        indices = np.asarray(buckets[key], dtype=int)
        if len(indices) > spec.max_cells_per_stratum:
            indices = np.sort(
                rng.choice(indices, spec.max_cells_per_stratum, replace=False)
            )
        selected.extend(indices.tolist())
        counts["|".join(key)] = int(len(indices))

    selected_units = sorted({units[index] for index in selected})
    arms = sorted({groups[index] for index in selected if not control_mask[index]})
    units_by_arm = {
        arm: sorted(
            {
                units[index]
                for index in selected
                if not control_mask[index] and groups[index] == arm
            }
        )
        for arm in arms
    }
    undersized = [
        arm
        for arm, arm_units in units_by_arm.items()
        if len(arm_units) < spec.minimum_units_per_arm
    ]
    selected_control_units = sorted(
        {units[index] for index in selected if control_mask[index]}
    )
    if undersized:
        raise ValueError(
            "subset arms have fewer independent units than minimum_units_per_arm: "
            + ", ".join(undersized)
        )
    if spec.control_selector is not None and len(selected_control_units) < spec.minimum_units_per_arm:
        raise ValueError(
            "subset controls have fewer independent units than minimum_units_per_arm"
        )
    return selected, {
        "selection_version": "v2",
        "split_id": spec.split_id,
        "split": spec.split,
        "selected_groups": arms,
        "selected_unit_count": len(selected_units),
        "selected_control_units": selected_control_units,
        "selected_control_unit_count": len(selected_control_units),
        "units_by_arm": units_by_arm,
        "stratum_counts": counts,
    }


def _chunked_anndata_subset(data, selected_indices: list[int], spec: BenchmarkSubsetSpec):
    import anndata as ad
    import numpy as np
    from scipy import sparse

    chunk_rows = int(spec.selection.get("chunk_rows", 1024))
    if chunk_rows < 1:
        raise ValueError("subset chunk_rows must be positive")

    def subset_matrix(matrix):
        blocks = []
        for start in range(0, len(selected_indices), chunk_rows):
            rows = selected_indices[start : start + chunk_rows]
            block = matrix[rows, :]
            if hasattr(block, "to_memory"):
                block = block.to_memory()
            if sparse.issparse(block):
                blocks.append(block.tocsr())
            else:
                blocks.append(sparse.csr_matrix(np.asarray(block)))
        return sparse.vstack(blocks, format="csr")

    subset = ad.AnnData(
        X=subset_matrix(data.X),
        obs=data.obs.iloc[selected_indices].copy(),
        var=data.var.copy(),
    )
    for name in data.layers.keys():
        subset.layers[name] = subset_matrix(data.layers[name])
    return subset


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
