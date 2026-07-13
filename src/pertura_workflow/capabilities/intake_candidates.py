from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from pertura_core import AnalysisStatus, CapabilityRunRequest, CapabilitySpec, DatasetContract, DiagnosticStatus

from pertura_workflow.capabilities.candidate_common import (
    blocked,
    caution_status,
    envelope,
    read_rows,
    resolve_input,
    resource_budget,
    success_status,
    write_json,
)
from pertura_workflow.capabilities.guide_counts import open_guide_count_source


_SUFFIX = re.compile(r"-\d+$")


def run_intake_materialize(
    spec: CapabilitySpec,
    request: CapabilityRunRequest,
    contract: DatasetContract,
    staging: Path,
):
    max_memory_gb, n_jobs = resource_budget(request.parameters)
    source = _source_path(contract, request.parameters.get("input_path"))
    try:
        matrix, obs, var, layer_info = _load_matrix(source, contract, request.parameters, max_memory_gb)
    except ModuleNotFoundError as exc:
        return blocked(
            spec,
            request,
            contract,
            f"scientific input dependency is missing: {exc.name}",
            metadata={"setup_command": "pertura env setup perturbseq-python-v1"},
        )
    except MemoryError as exc:
        return blocked(spec, request, contract, str(exc))

    try:
        import pandas as pd
        from scipy import sparse
    except ModuleNotFoundError as exc:
        return blocked(
            spec,
            request,
            contract,
            f"materialization dependency is missing: {exc.name}",
            metadata={"setup_command": "pertura env setup python-science-v1"},
        )

    if not sparse.issparse(matrix):
        matrix = sparse.csr_matrix(matrix)
    matrix = matrix.tocsr()
    estimated_gb = (matrix.data.nbytes + matrix.indices.nbytes + matrix.indptr.nbytes) / 1024**3
    if estimated_gb > max_memory_gb:
        return blocked(
            spec,
            request,
            contract,
            f"materialized sparse matrix estimate {estimated_gb:.3f} GB exceeds max_memory_gb={max_memory_gb}",
        )

    counts_path = staging / "counts.npz"
    sparse.save_npz(counts_path, matrix)
    obs_path = staging / "obs.parquet"
    var_path = staging / "var.parquet"
    pd.DataFrame(obs).to_parquet(obs_path, index=False)
    pd.DataFrame(var).to_parquet(var_path, index=False)

    guide_outputs: list[Path] = []
    guide_path = request.parameters.get("guide_counts_path")
    if guide_path:
        resolved_guide = resolve_input(contract, guide_path, label="guide_counts_path")
        try:
            guide_source = open_guide_count_source(
                resolved_guide,
                barcode_column=request.parameters.get("guide_barcode_column"),
                row_manifest_path=request.parameters.get("guide_row_manifest_path"),
                column_manifest_path=request.parameters.get("guide_column_manifest_path"),
                modality=request.parameters.get("guide_modality"),
                layer=request.parameters.get("guide_layer"),
                max_memory_gb=max_memory_gb,
                chunk_rows=resource_budget(request.parameters).chunk_rows,
            )
        except (MemoryError, ValueError) as exc:
            return blocked(spec, request, contract, str(exc))
        try:
            guide_matrix = guide_source.to_csr(
                chunk_rows=resource_budget(request.parameters).chunk_rows
            )
            guide_barcodes = list(guide_source.cell_ids)
            guide_names = list(guide_source.guide_ids)
        finally:
            guide_source.close()
        guide_matrix_path = staging / "guide_counts.npz"
        sparse.save_npz(guide_matrix_path, guide_matrix)
        guide_obs_path = staging / "guide_barcodes.parquet"
        guide_var_path = staging / "guides.parquet"
        pd.DataFrame({
            "raw_barcode": guide_barcodes,
            "normalized_barcode": [_normalize_barcode(item) for item in guide_barcodes],
        }).to_parquet(guide_obs_path, index=False)
        pd.DataFrame({"guide_id": guide_names}).to_parquet(guide_var_path, index=False)
        guide_outputs.extend((guide_matrix_path, guide_obs_path, guide_var_path))
    manifest = {
        "schema_version": "pertura-materialized-bundle-v1",
        "contract_id": contract.contract_id,
        "contract_hash": contract.canonical_hash,
        "source_name": source.name,
        "source_format": contract.input_format,
        "shape": [int(matrix.shape[0]), int(matrix.shape[1])],
        "nnz": int(matrix.nnz),
        "estimated_sparse_gb": estimated_gb,
        "layer": layer_info,
        "resource_budget": {"max_memory_gb": max_memory_gb, "n_jobs": n_jobs},
        "artifacts": {
            "counts": counts_path.name,
            "obs": obs_path.name,
            "var": var_path.name,
            "guide_outputs": [path.name for path in guide_outputs],
        },
    }
    manifest_path = write_json(staging, "materialization_manifest.json", manifest)
    outputs = (counts_path, obs_path, var_path, *guide_outputs, manifest_path)
    return envelope(
        spec,
        request,
        contract,
        status=AnalysisStatus.completed,
        summary=f"Materialized a sparse {matrix.shape[0]} cell × {matrix.shape[1]} feature bundle.",
        metrics={
            "n_cells": int(matrix.shape[0]),
            "n_features": int(matrix.shape[1]),
            "nnz": int(matrix.nnz),
            "estimated_sparse_gb": estimated_gb,
        },
        outputs=outputs,
        metadata={"resource_budget": manifest["resource_budget"]},
    )


def run_dataset_integrity(
    spec: CapabilitySpec,
    request: CapabilityRunRequest,
    contract: DatasetContract,
    staging: Path,
):
    max_memory_gb, _ = resource_budget(request.parameters)
    source = _source_path(contract, request.parameters.get("input_path"))
    blockers: list[str] = []
    cautions: list[str] = []
    try:
        matrix, obs, var, layer_info = _load_matrix(source, contract, request.parameters, max_memory_gb)
        import numpy as np
        from scipy import sparse
    except ModuleNotFoundError as exc:
        return blocked(
            spec,
            request,
            contract,
            f"dataset integrity dependency is missing: {exc.name}",
            metadata={"setup_command": "pertura env setup perturbseq-python-v1"},
        )
    except (MemoryError, ValueError) as exc:
        blockers.append(str(exc))
        matrix, obs, var, layer_info = None, [], [], {}

    raw_barcodes = [str(row.get("raw_barcode") or row.get("cell_id") or row.get("barcode") or "") for row in obs]
    normalized = [_normalize_barcode(item) for item in raw_barcodes]
    barcode_collision = len(set(normalized)) != len(set(raw_barcodes))
    if barcode_collision:
        blockers.append("barcode suffix removal would create collisions")
    features = [str(row.get("feature_id") or row.get("gene") or row.get("feature") or "") for row in var]
    duplicate_features = sorted(name for name, count in Counter(features).items() if name and count > 1)
    if duplicate_features:
        blockers.append(f"feature identifiers are not unique: {len(duplicate_features)} duplicates")

    nonnegative = integer_like = True
    missing_values = 0
    if matrix is not None:
        values = matrix.data if sparse.issparse(matrix) else np.asarray(matrix).ravel()
        missing_values = int(np.isnan(values).sum())
        nonnegative = bool(np.all(values[~np.isnan(values)] >= 0))
        integer_like = bool(np.allclose(values[~np.isnan(values)], np.round(values[~np.isnan(values)])))
        if missing_values:
            blockers.append(f"expression matrix contains {missing_values} missing values")
        if not nonnegative:
            blockers.append("expression matrix contains negative values")
        if not integer_like:
            cautions.append("selected expression layer is not integer-like and cannot support raw-count methods")

    source_claim = str(layer_info.get("source") or "")
    if source_claim not in {"raw_counts", "tenx_counts", "confirmed_metadata"}:
        cautions.append("raw-count provenance is unresolved; filename or numeric appearance is insufficient")
    if contract.unresolved_fields:
        cautions.append("DatasetContract still contains unresolved identity fields")

    payload = {
        "schema_version": "pertura-dataset-integrity-v1",
        "contract_id": contract.contract_id,
        "source_name": source.name,
        "shape": list(matrix.shape) if matrix is not None else None,
        "checks": {
            "barcode_suffix_collision": barcode_collision,
            "duplicate_feature_count": len(duplicate_features),
            "nonnegative": nonnegative,
            "integer_like": integer_like,
            "missing_value_count": missing_values,
            "layer": layer_info,
        },
        "blockers": blockers,
        "cautions": cautions,
    }
    output = write_json(staging, "dataset_integrity.json", payload)
    status = DiagnosticStatus.blocked if blockers else (
        DiagnosticStatus.caution if cautions else DiagnosticStatus.screen_passed
    )
    return envelope(
        spec,
        request,
        contract,
        status=status,
        summary=f"Dataset integrity completed with {len(blockers)} blockers and {len(cautions)} cautions.",
        blockers=blockers,
        cautions=cautions,
        metrics=payload["checks"],
        outputs=(output,),
    )


def run_design_balance(
    spec: CapabilitySpec,
    request: CapabilityRunRequest,
    contract: DatasetContract,
    staging: Path,
):
    metadata_path = resolve_input(
        contract,
        request.parameters.get("metadata_path"),
        label="metadata_path",
    )
    fields, rows = read_rows(metadata_path)
    condition = str(request.parameters.get("condition_column") or "condition")
    replicate = str(request.parameters.get("replicate_column") or "replicate")
    donor = str(request.parameters.get("donor_column") or "donor")
    state = str(request.parameters.get("state_column") or "state")
    batch = str(request.parameters.get("batch_column") or "batch")
    paired = bool(request.parameters.get("paired", False))
    required = [condition, replicate]
    missing = [name for name in required if name not in fields]
    if missing:
        return blocked(spec, request, contract, "metadata is missing required design columns: " + ", ".join(missing))

    counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    units_by_condition: dict[str, set[str]] = defaultdict(set)
    batches_by_condition: dict[str, set[str]] = defaultdict(set)
    cross: dict[str, int] = defaultdict(int)
    for row in rows:
        arm = row.get(condition, "")
        unit = row.get(replicate, "")
        if not arm or not unit:
            continue
        counts[arm][unit] += 1
        units_by_condition[arm].add(unit)
        if batch in fields and row.get(batch):
            batches_by_condition[arm].add(row[batch])
        key = "|".join([arm, unit, row.get(donor, ""), row.get(state, ""), row.get(batch, "")])
        cross[key] += 1

    blockers: list[str] = []
    cautions: list[str] = []
    if len(units_by_condition) < 2:
        blockers.append("fewer than two contrast conditions are represented")
    minimum_units = min((len(values) for values in units_by_condition.values()), default=0)
    if minimum_units < 2:
        blockers.append("fewer than two independent units are available in at least one condition")
    shared_units = set.intersection(*units_by_condition.values()) if len(units_by_condition) >= 2 else set()
    if paired and not shared_units:
        blockers.append("paired design has no replicate/donor overlap across conditions")
    if not paired and shared_units:
        cautions.append("replicate IDs overlap across arms; confirm whether the design is paired")
    shared_batches = set.intersection(*batches_by_condition.values()) if len(batches_by_condition) >= 2 else set()
    if batches_by_condition and not shared_batches:
        blockers.append("condition is completely confounded with batch")
    if minimum_units == 2 and not blockers:
        cautions.append("two units per arm permit execution but not strict measured association")

    design_payload = {
        "schema_version": "pertura-design-balance-v1",
        "columns": {
            "condition": condition,
            "replicate": replicate,
            "donor": donor if donor in fields else None,
            "state": state if state in fields else None,
            "batch": batch if batch in fields else None,
        },
        "paired": paired,
        "units_by_condition": {key: sorted(values) for key, values in units_by_condition.items()},
        "cell_counts": {key: dict(values) for key, values in counts.items()},
        "shared_units": sorted(shared_units),
        "shared_batches": sorted(shared_batches),
        "cross_counts": dict(sorted(cross.items())),
        "contrast_estimable": not blockers,
        "blockers": blockers,
        "cautions": cautions,
    }
    output = write_json(staging, "design_balance.json", design_payload)
    status = DiagnosticStatus.blocked if blockers else (
        DiagnosticStatus.caution if cautions else DiagnosticStatus.screen_passed
    )
    return envelope(
        spec,
        request,
        contract,
        status=status,
        summary=f"Design balance found {minimum_units} minimum units per condition.",
        blockers=blockers,
        cautions=cautions,
        metrics={
            "n_conditions": len(units_by_condition),
            "minimum_units_per_condition": minimum_units,
            "shared_unit_count": len(shared_units),
            "contrast_estimable": not blockers,
        },
        outputs=(output,),
    )


def _source_path(contract: DatasetContract, value: Any) -> Path:
    if value not in (None, ""):
        resolved = resolve_input(contract, value, label="input_path")
        assert resolved is not None
        return resolved
    if len(contract.source_paths) != 1:
        raise ValueError("input_path is required when DatasetContract has multiple sources")
    source = Path(contract.source_paths[0]).expanduser().resolve()
    if not source.exists():
        raise FileNotFoundError(source)
    return source


def _load_matrix(
    source: Path,
    contract: DatasetContract,
    parameters: dict[str, Any],
    max_memory_gb: float,
):
    suffix = source.suffix.lower()
    if suffix in {".csv", ".tsv", ".txt"}:
        import numpy as np

        fields, rows = read_rows(source)
        cell_column = str(parameters.get("cell_column") or fields[0])
        if cell_column not in fields:
            raise ValueError(f"cell column is missing: {cell_column}")
        features = [name for name in fields if name != cell_column]
        values = []
        for row in rows:
            values.append([_nonnegative_number(row.get(name), integer=False) for name in features])
        estimate = len(rows) * len(features) * 8 / 1024**3
        if estimate > max_memory_gb:
            raise MemoryError(
                f"dense input estimate {estimate:.3f} GB exceeds max_memory_gb={max_memory_gb}"
            )
        matrix = np.asarray(values, dtype=float)
        obs = [
            {
                "raw_barcode": row[cell_column],
                "normalized_barcode": _normalize_barcode(row[cell_column]),
            }
            for row in rows
        ]
        var = [{"feature_id": name} for name in features]
        source_class = (
            "confirmed_metadata"
            if bool(contract.expression_matrix.get("raw_counts_confirmed"))
            else "numeric_candidate"
        )
        return matrix, obs, var, {"name": "X", "source": source_class}
    if suffix == ".h5ad":
        import anndata as ad
        import numpy as np

        data = ad.read_h5ad(source, backed="r")
        layer = str(parameters.get("layer") or "X")
        matrix = data.X if layer == "X" else data.layers[layer]
        estimate = int(data.n_obs) * int(data.n_vars) * 8 / 1024**3
        if not hasattr(matrix, "to_memory") and estimate > max_memory_gb:
            raise MemoryError(
                f"dense H5AD layer estimate {estimate:.3f} GB exceeds max_memory_gb={max_memory_gb}"
            )
        matrix = matrix.to_memory() if hasattr(matrix, "to_memory") else np.asarray(matrix)
        obs = [
            {"raw_barcode": str(name), "normalized_barcode": _normalize_barcode(str(name))}
            for name in data.obs_names
        ]
        var = [{"feature_id": str(name)} for name in data.var_names]
        layer_metadata = dict(data.uns.get("pertura_layers") or {})
        raw_confirmed = bool(layer_metadata.get(layer, {}).get("raw_counts_confirmed"))
        return matrix, obs, var, {
            "name": layer,
            "source": "confirmed_metadata" if raw_confirmed else "h5ad_layer_candidate",
        }
    if suffix in {".h5mu", ".mudata"}:
        import mudata

        data = mudata.read_h5mu(source, backed="r")
        modality = str(parameters.get("modality") or "rna")
        if modality not in data.mod:
            raise ValueError(f"MuData modality is missing: {modality}")
        adata = data.mod[modality]
        matrix = adata.X.to_memory() if hasattr(adata.X, "to_memory") else adata.X
        obs = [
            {"raw_barcode": str(name), "normalized_barcode": _normalize_barcode(str(name))}
            for name in adata.obs_names
        ]
        var = [{"feature_id": str(name)} for name in adata.var_names]
        return matrix, obs, var, {"name": f"{modality}.X", "source": "mudata_layer_candidate"}
    if source.is_dir():
        from scipy.io import mmread

        matrix_path = next(
            (source / name for name in ("matrix.mtx.gz", "matrix.mtx") if (source / name).exists()),
            None,
        )
        if matrix_path is None:
            raise ValueError("10x directory does not contain matrix.mtx or matrix.mtx.gz")
        matrix = mmread(matrix_path).tocsr().transpose().tocsr()
        barcode_path = next(
            (source / name for name in ("barcodes.tsv.gz", "barcodes.tsv") if (source / name).exists()),
            None,
        )
        feature_path = next(
            (source / name for name in ("features.tsv.gz", "features.tsv", "genes.tsv") if (source / name).exists()),
            None,
        )
        if barcode_path is None or feature_path is None:
            raise ValueError("10x MEX directory lacks barcode or feature metadata")
        import gzip

        opener = gzip.open if barcode_path.suffix == ".gz" else open
        with opener(barcode_path, "rt", encoding="utf-8") as handle:
            barcodes = [line.rstrip().split("\t")[0] for line in handle if line.strip()]
        opener = gzip.open if feature_path.suffix == ".gz" else open
        with opener(feature_path, "rt", encoding="utf-8") as handle:
            features = [line.rstrip().split("\t")[0] for line in handle if line.strip()]
        obs = [
            {"raw_barcode": name, "normalized_barcode": _normalize_barcode(name)}
            for name in barcodes
        ]
        var = [{"feature_id": name} for name in features]
        return matrix, obs, var, {"name": "X", "source": "tenx_counts"}
    if suffix in {".h5", ".hdf5"}:
        import scanpy as sc

        data = sc.read_10x_h5(source)
        obs = [
            {"raw_barcode": str(name), "normalized_barcode": _normalize_barcode(str(name))}
            for name in data.obs_names
        ]
        var = [{"feature_id": str(name)} for name in data.var_names]
        return data.X, obs, var, {"name": "X", "source": "tenx_counts"}
    raise ValueError(f"unsupported materialization format: {source.name}")


def _nonnegative_number(value: Any, *, integer: bool) -> float | int:
    number = float(value or 0)
    if number < 0:
        raise ValueError("count/expression matrices must be nonnegative")
    if integer and not number.is_integer():
        raise ValueError("count matrices must contain integer values")
    return int(number) if integer else number


def _normalize_barcode(value: str) -> str:
    return _SUFFIX.sub("", str(value).strip().upper())
