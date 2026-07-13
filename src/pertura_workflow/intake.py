from __future__ import annotations

import csv
import gzip
import json
from pathlib import Path
from typing import Any

from pertura_core import DatasetContract
from pertura_core.hashing import file_sha256


_IDENTITY_CANDIDATES = {
    "control": ("control", "is_control", "control_type", "perturbation_type"),
    "guide": ("guide", "guide_id", "guide_ids", "gRNA", "sgRNA"),
    "target": ("target", "target_gene", "gene_target", "perturbation"),
    "donor": ("donor", "donor_id", "patient"),
    "replicate": ("replicate", "replicate_id", "sample", "sample_id"),
    "batch": ("batch", "batch_id", "lane"),
    "dose": ("dose", "concentration"),
    "time": ("time", "timepoint", "time_point"),
    "design_moi": ("design_moi", "moi_regime"),
    "guide_design": ("guide_design", "perturbation_design"),
}

_CONFIRMATION_ENUMS = {
    "design_moi": {"low", "high", "unknown"},
    "guide_design": {"single", "combinatorial", "mixed", "unknown"},
}


def inspect_dataset_path(path: str | Path, *, dataset_id: str | None = None) -> DatasetContract:
    source = Path(path).expanduser().resolve()
    if not source.exists():
        raise FileNotFoundError(source)
    detected = _detect_format(source)
    metadata: dict[str, Any] = {"inspection": detected, "source_exists": True}
    expression: dict[str, Any] = {}
    guide: dict[str, Any] = {}
    identity: dict[str, dict[str, Any]] = {}
    unresolved: list[str] = []

    if detected["format"] in {"csv", "tsv"}:
        table = _inspect_delimited(source, delimiter="\t" if detected["format"] == "tsv" else ",")
        metadata["table"] = table
        expression = table["matrix_candidate"]
        identity = _identity_fields(table["columns"])
    elif detected["format"] == "h5ad":
        details = _inspect_h5ad(source)
        metadata["h5ad"] = details
        expression = details.get("expression_matrix", {})
        identity = _identity_fields(details.get("obs_columns", []))
    elif detected["format"] == "mudata":
        details = _inspect_mudata(source)
        metadata["mudata"] = details
        expression = details.get("expression_matrix", {})
        guide = details.get("guide_matrix", {})
        identity = _identity_fields(details.get("obs_columns", []))
    elif detected["format"] in {"10x_mex", "cell_ranger_directory"}:
        details = _inspect_10x_directory(source)
        metadata["tenx"] = details
        expression = details.get("expression_matrix", {})
        guide = details.get("guide_matrix", {})
    elif detected["format"] == "10x_hdf5":
        details = _inspect_10x_hdf5(source)
        metadata["tenx"] = details
        expression = details.get("expression_matrix", {})
        guide = details.get("guide_matrix", {})

    for field in _IDENTITY_CANDIDATES:
        identity.setdefault(field, {"status": "unresolved", "candidates": []})
        if identity[field]["status"] == "unresolved":
            unresolved.append(field)
    if not expression.get("layer_status") == "confirmed":
        unresolved.append("expression_layer")
    if not guide:
        unresolved.append("guide_matrix")

    source_hash = _source_fingerprint(source)
    dataset = dataset_id or f"dataset_{source_hash.split(':', 1)[1][:12]}"
    return DatasetContract(
        dataset_id=dataset,
        input_format=detected["format"],
        source_paths=(str(source),),
        expression_matrix=expression,
        guide_matrix=guide,
        identity_fields=identity,
        unresolved_fields=tuple(sorted(set(unresolved))),
        metadata={**metadata, "source_fingerprint": source_hash},
    )


def contract_with_confirmations(
    contract: DatasetContract,
    confirmations: dict[str, Any],
) -> DatasetContract:
    identity = {key: dict(value) for key, value in contract.identity_fields.items()}
    unresolved = set(contract.unresolved_fields)
    allowed = {
        "control", "guide", "guide_target", "target", "donor", "replicate",
        "batch", "dose", "time", "state_label", "design_moi", "guide_design",
    }
    for field, value in confirmations.items():
        if field not in allowed:
            raise ValueError(f"field cannot be confirmed through the design interface: {field}")
        if field in _CONFIRMATION_ENUMS:
            normalized = str(value or "").strip().lower()
            if normalized not in _CONFIRMATION_ENUMS[field]:
                choices = ", ".join(sorted(_CONFIRMATION_ENUMS[field]))
                raise ValueError(f"invalid confirmation for {field}; expected one of: {choices}")
            value = normalized
        identity[field] = {"status": "confirmed", "value": value, "source": "user_confirmation"}
        unresolved.discard(field)
    return DatasetContract(
        dataset_id=contract.dataset_id,
        contract_version=contract.contract_version + 1,
        parent_contract_id=contract.contract_id,
        source_paths=contract.source_paths,
        input_format=contract.input_format,
        expression_matrix=contract.expression_matrix,
        guide_matrix=contract.guide_matrix,
        identity_fields=identity,
        unresolved_fields=tuple(sorted(unresolved)),
        dependencies=contract.dependencies,
        metadata=contract.metadata,
    )


def _detect_format(path: Path) -> dict[str, Any]:
    if path.is_dir():
        names = {item.name.lower() for item in path.iterdir()}
        if any(name in names for name in {"filtered_feature_bc_matrix", "raw_feature_bc_matrix"}):
            return {"format": "cell_ranger_directory", "basis": "directory layout"}
        matrix = any(name in names for name in {"matrix.mtx", "matrix.mtx.gz"})
        barcodes = any(name in names for name in {"barcodes.tsv", "barcodes.tsv.gz"})
        features = any(name in names for name in {"features.tsv", "features.tsv.gz", "genes.tsv", "genes.tsv.gz"})
        if matrix and barcodes and features:
            return {"format": "10x_mex", "basis": "required MEX members"}
        return {"format": "directory", "basis": "unrecognized directory"}
    suffix = path.suffix.lower()
    if suffix == ".h5ad":
        return {"format": "h5ad", "basis": "container extension and structure inspection"}
    if suffix in {".h5mu", ".mudata"}:
        return {"format": "mudata", "basis": "container extension and structure inspection"}
    if suffix in {".h5", ".hdf5"}:
        return {"format": "10x_hdf5", "basis": "HDF5 extension; feature types inspected"}
    if suffix == ".csv":
        return {"format": "csv", "basis": "delimiter and header inspection"}
    if suffix in {".tsv", ".txt"}:
        return {"format": "tsv", "basis": "delimiter and header inspection"}
    if suffix == ".rds":
        return {"format": "unsupported_seurat_rds", "basis": "arbitrary RDS is intentionally unsupported"}
    return {"format": "unknown", "basis": "no supported signature"}


def _inspect_delimited(path: Path, *, delimiter: str) -> dict[str, Any]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle, delimiter=delimiter)
        header = next(reader, [])
        rows = []
        for _, row in zip(range(200), reader):
            rows.append(row)
    numeric_columns: list[str] = []
    integer_nonnegative = True
    for index, column in enumerate(header):
        values = [row[index] for row in rows if index < len(row) and row[index] != ""]
        if not values:
            continue
        try:
            numbers = [float(value) for value in values]
        except ValueError:
            continue
        numeric_columns.append(column)
        integer_nonnegative = integer_nonnegative and all(value >= 0 and value.is_integer() for value in numbers)
    layer_status = "candidate" if numeric_columns else "unresolved"
    return {
        "columns": header,
        "sampled_rows": len(rows),
        "matrix_candidate": {
            "numeric_columns": numeric_columns,
            "nonnegative_integer_sample": bool(numeric_columns and integer_nonnegative),
            "layer_candidate": "raw_counts" if numeric_columns and integer_nonnegative else "expression_values",
            "layer_status": layer_status,
            "confirmation_basis": "sampled values only; explicit metadata is required",
        },
    }


def _inspect_h5ad(path: Path) -> dict[str, Any]:
    try:
        import anndata as ad
    except ModuleNotFoundError:
        return {"status": "dependency_missing", "required_package": "anndata", "expression_matrix": {"layer_status": "unresolved"}}
    data = ad.read_h5ad(path, backed="r")
    try:
        layers = list(data.layers.keys())
        return {
            "status": "inspected_backed",
            "shape": list(data.shape),
            "layers": layers,
            "obs_columns": [str(item) for item in data.obs.columns],
            "var_columns": [str(item) for item in data.var.columns],
            "expression_matrix": {
                "shape": list(data.shape),
                "layers": layers,
                "layer_status": "candidate",
                "confirmation_basis": "container structure; count semantics require explicit metadata and value checks",
            },
        }
    finally:
        if getattr(data, "file", None):
            data.file.close()


def _inspect_mudata(path: Path) -> dict[str, Any]:
    try:
        import mudata
    except ModuleNotFoundError:
        return {"status": "dependency_missing", "required_package": "mudata", "expression_matrix": {"layer_status": "unresolved"}}
    data = mudata.read_h5mu(path, backed=True)
    modalities = list(data.mod.keys())
    obs_columns = [str(item) for item in data.obs.columns]
    expression_key = next((key for key in modalities if key.lower() in {"rna", "gex", "expression"}), None)
    guide_key = next((key for key in modalities if key.lower() in {"guide", "crispr", "gdo"}), None)
    return {
        "status": "inspected_backed",
        "modalities": modalities,
        "obs_columns": obs_columns,
        "expression_matrix": {"modality": expression_key, "layer_status": "candidate" if expression_key else "unresolved"},
        "guide_matrix": {"modality": guide_key, "status": "candidate"} if guide_key else {},
    }


def _inspect_10x_directory(path: Path) -> dict[str, Any]:
    roots = [path]
    for name in ("filtered_feature_bc_matrix", "raw_feature_bc_matrix"):
        candidate = path / name
        if candidate.is_dir():
            roots.append(candidate)
    detected = []
    for root in roots:
        names = {item.name.lower() for item in root.iterdir()} if root.is_dir() else set()
        if any(name in names for name in {"matrix.mtx", "matrix.mtx.gz"}):
            detected.append(str(root))
    return {
        "matrix_directories": detected,
        "expression_matrix": {"layer_candidate": "raw_counts", "layer_status": "candidate"},
        "guide_matrix": {"status": "candidate", "feature_type_confirmation_required": True},
    }


def _inspect_10x_hdf5(path: Path) -> dict[str, Any]:
    try:
        import h5py
    except ModuleNotFoundError:
        return {"status": "dependency_missing", "required_package": "h5py", "expression_matrix": {"layer_status": "unresolved"}}
    with h5py.File(path, "r") as handle:
        keys = list(handle.keys())
        shape = list(handle["matrix/shape"][()]) if "matrix/shape" in handle else None
        feature_types = []
        if "matrix/features/feature_type" in handle:
            feature_types = sorted({item.decode() if isinstance(item, bytes) else str(item) for item in handle["matrix/features/feature_type"][()]})
    return {
        "status": "inspected",
        "root_keys": keys,
        "shape": shape,
        "feature_types": feature_types,
        "expression_matrix": {"shape": shape, "layer_candidate": "raw_counts", "layer_status": "candidate"},
        "guide_matrix": {"status": "candidate", "feature_types": feature_types} if any("crispr" in item.lower() for item in feature_types) else {},
    }


def _identity_fields(columns: list[str]) -> dict[str, dict[str, Any]]:
    lower = {column.lower(): column for column in columns}
    result: dict[str, dict[str, Any]] = {}
    for field, candidates in _IDENTITY_CANDIDATES.items():
        matches = [lower[item.lower()] for item in candidates if item.lower() in lower]
        result[field] = {
            "status": "observed" if len(matches) == 1 else ("inferred" if matches else "unresolved"),
            "candidates": matches,
            "value": matches[0] if len(matches) == 1 else None,
        }
    return result


def _source_fingerprint(path: Path) -> str:
    if path.is_file():
        return file_sha256(path)
    entries = []
    for item in sorted(path.rglob("*")):
        if item.is_file():
            stat = item.stat()
            entries.append({"path": item.relative_to(path).as_posix(), "size": stat.st_size})
    from pertura_core.hashing import canonical_hash

    return canonical_hash(entries)
