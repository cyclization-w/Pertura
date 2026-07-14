from __future__ import annotations

import json
from itertools import combinations
from pathlib import Path
from typing import Any

from pertura_core import AnalysisStatus, CapabilityRunRequest, CapabilitySpec, DatasetContract, ResultEnvelope
from pertura_core.hashing import file_sha256
from pertura_workflow.capabilities.candidate_common import resource_budget


def run_gmt_import(spec: CapabilitySpec, request: CapabilityRunRequest, contract: DatasetContract, staging: Path) -> ResultEnvelope:
    params = request.parameters
    path = _resolve_input(contract, params.get("gmt_path"))
    species = str(params.get("species") or "")
    namespace = str(params.get("identifier_namespace") or "")
    if not species or not namespace:
        return _blocked(spec, request, contract, ("species and identifier_namespace must be declared",))
    universe = {str(item) for item in params.get("gene_universe") or []}
    modules: dict[str, list[str]] = {}
    duplicates: list[str] = []
    repeated_genes: dict[str, list[str]] = {}
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if not line.strip():
            continue
        fields = line.rstrip().split("\t")
        if len(fields) < 3:
            raise ValueError("GMT lines must contain name, description and at least one gene")
        name, description, *genes = fields
        if name in modules:
            duplicates.append(name)
        seen: set[str] = set()
        repeated = []
        unique = []
        for gene in genes:
            if gene in seen:
                repeated.append(gene)
            else:
                seen.add(gene)
                unique.append(gene)
        modules[name] = unique
        if repeated:
            repeated_genes[name] = sorted(set(repeated))
    blockers = []
    cautions = []
    if duplicates:
        blockers.append("GMT contains duplicate module names")
    if repeated_genes:
        cautions.append("duplicate genes within GMT modules were de-duplicated")
    coverage = {}
    if universe:
        coverage = {name: len(set(genes) & universe) / max(1, len(genes)) for name, genes in modules.items()}
        if any(value < 0.20 for value in coverage.values()):
            cautions.append("one or more modules have less than 20% coverage in the dataset gene universe")
    output = staging / "gmt_modules.json"
    payload = {
        "schema_version": "pertura-gmt-module-reference-v1",
        "species": species,
        "identifier_namespace": namespace,
        "source_name": path.name,
        "source_sha256": file_sha256(path),
        "modules": modules,
        "duplicate_module_names": sorted(set(duplicates)),
        "repeated_genes": repeated_genes,
        "coverage": coverage,
        "leakage": {"perturbation_labels_used": False, "test_split_used": False},
    }
    output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    status = AnalysisStatus.blocked if blockers else (AnalysisStatus.completed_with_caution if cautions else AnalysisStatus.completed)
    return ResultEnvelope(
        run_id=request.run_id, request_id=request.request_id, capability_id=spec.capability_id,
        capability_version=spec.version, capability_trust=spec.trust_level,
        contract_id=contract.contract_id, contract_hash=contract.canonical_hash, scope=request.scope,
        status=status, result_kind=spec.output_kind, source_class=spec.source_class,
        summary=f"Imported {len(modules)} GMT modules for {species}/{namespace}.",
        blockers=tuple(blockers), cautions=tuple(cautions), metrics={"n_modules": len(modules)},
        output_paths=(output.name,), output_hashes={output.name: file_sha256(output)},
        dependencies=request.dependencies, metadata={"species": species, "identifier_namespace": namespace},
    )


def run_nmf_modules(spec: CapabilitySpec, request: CapabilityRunRequest, contract: DatasetContract, staging: Path) -> ResultEnvelope:
    try:
        import anndata as ad
        import numpy as np
        import pandas as pd
        from scipy import sparse
        from sklearn.decomposition import MiniBatchNMF
        from sklearn.metrics import adjusted_rand_score
    except ModuleNotFoundError as exc:
        return _blocked(spec, request, contract, (f"NMF dependency is missing: {exc.name}",))
    params = request.parameters
    path = _resolve_input(contract, params.get("h5ad_path"))
    control_column = str(params.get("control_column") or "")
    control_values = {str(item) for item in params.get("control_values") or []}
    if not control_column or not control_values:
        return _blocked(spec, request, contract, ("confirmed control_column and control_values are required",))
    budget = resource_budget(params)
    from pertura_workflow.capabilities.dependency_inputs import retained_cells_for_request

    retained = retained_cells_for_request(staging, request, required=False)
    data = ad.read_h5ad(path, backed="r")
    if control_column not in data.obs.columns:
        if getattr(data, "file", None):
            data.file.close()
        return _blocked(spec, request, contract, (f"control column is missing: {control_column}",))
    mask = data.obs[control_column].astype(str).isin(control_values).to_numpy()
    if retained is not None:
        mask &= data.obs_names.astype(str).isin(retained)
    control_indices = np.flatnonzero(mask)
    genes = np.asarray([str(item) for item in data.var_names])
    obs_names = np.asarray([str(item) for item in data.obs_names])
    dense_bytes = budget.dense_bytes(len(control_indices), int(data.n_vars))
    source_is_sparse = sparse.issparse(data.X) or "SparseDataset" in type(data.X).__name__
    if not source_is_sparse and dense_bytes > budget.max_bytes:
        if getattr(data, "file", None):
            data.file.close()
        return _blocked(spec, request, contract, (f"dense control slice requires {dense_bytes / 1024**3:.3f} GB, exceeding max_memory_gb={budget.max_memory_gb}",))
    matrix = data.X[control_indices, :]
    matrix = matrix.to_memory() if hasattr(matrix, "to_memory") else matrix
    if getattr(data, "file", None):
        data.file.close()
    if sparse.issparse(matrix):
        matrix = matrix.tocsr().astype(float)
        if matrix.data.size and np.any(matrix.data < 0):
            return _blocked(spec, request, contract, ("NMF requires a nonnegative matrix",))
        library = np.asarray(matrix.sum(axis=1)).ravel()
        scale = np.divide(1e4, library, out=np.zeros_like(library, dtype=float), where=library > 0)
        matrix = sparse.diags(scale) @ matrix
        matrix.data = np.log1p(matrix.data)
        mean = np.asarray(matrix.mean(axis=0)).ravel()
        variance = np.maximum(0.0, np.asarray(matrix.power(2).mean(axis=0)).ravel() - mean**2)
    else:
        matrix = np.asarray(matrix, dtype=float)
        if np.any(matrix < 0):
            return _blocked(spec, request, contract, ("NMF requires a nonnegative matrix",))
        budget.require_dense(matrix.shape[0], matrix.shape[1], arrays=2, label="control NMF normalization")
        library = matrix.sum(axis=1)
        matrix = np.divide(matrix, library[:, None], out=np.zeros_like(matrix, dtype=float), where=library[:, None] > 0) * 1e4
        matrix = np.log1p(matrix)
        variance = matrix.var(axis=0)
    reference_hvg: list[str] = []
    reference_model_path = params.get("reference_model_path")
    if reference_model_path:
        reference_model = np.load(Path(str(reference_model_path)), allow_pickle=False)
        reference_hvg = [str(item) for item in reference_model["hvg_names"]]
    n_hvg = min(2000, int((variance > 0).sum()))
    if n_hvg < 2:
        return _blocked(spec, request, contract, ("fewer than two variable control genes are available",))
    if reference_hvg:
        gene_index = {str(gene): index for index, gene in enumerate(genes)}
        hvg_index = np.asarray(
            [gene_index[gene] for gene in reference_hvg if gene in gene_index],
            dtype=int,
        )
        if len(hvg_index) < 2:
            return _blocked(
                spec,
                request,
                contract,
                ("state reference exposes fewer than two matching HVGs",),
            )
    else:
        hvg_index = np.argsort(variance)[-n_hvg:]
    matrix = matrix[:, hvg_index]
    genes = genes[hvg_index]
    ranks = [int(item) for item in params.get("ranks") or [5, 10, 15, 20]]
    seeds = [int(item) for item in params.get("seeds") or [0, 1, 2, 3, 4]]
    ranks = [rank for rank in ranks if 2 <= rank < min(matrix.shape)]
    if not ranks:
        return _blocked(spec, request, contract, ("no requested NMF rank fits the control matrix dimensions",))
    candidates = []
    fitted = {}
    for rank in ranks:
        assignments = []
        errors = []
        models = []
        for seed in seeds:
            model = MiniBatchNMF(
                n_components=rank,
                init="nndsvda",
                random_state=seed,
                max_iter=1000,
                batch_size=max(rank, min(budget.chunk_rows, matrix.shape[0])),
            )
            scores = model.fit_transform(matrix)
            assignments.append(model.components_.argmax(axis=0))
            errors.append(float(model.reconstruction_err_))
            models.append((model, scores))
        stability_values = [adjusted_rand_score(left, right) for left, right in combinations(assignments, 2)]
        stability = float(sum(stability_values) / len(stability_values)) if stability_values else 1.0
        best_index = int(np.argmin(errors))
        candidates.append({"rank": rank, "stability": stability, "reconstruction_error": errors[best_index]})
        fitted[rank] = models[best_index]
    eligible = [item for item in candidates if item["stability"] >= 0.80]
    cautions = []
    if eligible:
        chosen = min(eligible, key=lambda item: (item["reconstruction_error"], item["rank"]))
    else:
        chosen = max(candidates, key=lambda item: (item["stability"], -item["reconstruction_error"]))
        cautions.append("no NMF rank reached consensus stability >=0.80")
    model, scores = fitted[chosen["rank"]]
    modules = {}
    for component, loadings in enumerate(model.components_):
        top = np.argsort(loadings)[::-1][: min(50, len(genes))]
        modules[f"reference_nmf_{component + 1:02d}"] = [{"gene": str(genes[index]), "loading": float(loadings[index])} for index in top]
    module_path = staging / "nmf_modules.json"
    score_path = staging / "nmf_control_scores.parquet"
    module_payload = {
        "schema_version": "pertura-nmf-module-reference-v1",
        "fit_population": "confirmed_controls_only",
        "candidate_ranks": candidates,
        "chosen_rank": chosen["rank"],
        "chosen_stability": chosen["stability"],
        "modules": modules,
        "leakage": {"perturbation_labels_used": False, "test_split_used": False},
    }
    module_path.write_text(json.dumps(module_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    pd.DataFrame(scores, index=obs_names[control_indices], columns=list(modules)).rename_axis("cell_id").reset_index().to_parquet(score_path, index=False)
    outputs = (module_path.name, score_path.name)
    status = AnalysisStatus.completed_with_caution if cautions else AnalysisStatus.completed
    return ResultEnvelope(
        run_id=request.run_id, request_id=request.request_id, capability_id=spec.capability_id,
        capability_version=spec.version, capability_trust=spec.trust_level,
        contract_id=contract.contract_id, contract_hash=contract.canonical_hash, scope=request.scope,
        status=status, result_kind=spec.output_kind, source_class=spec.source_class,
        summary=f"Learned {chosen['rank']} control-derived NMF modules.", cautions=tuple(cautions),
        metrics={"chosen_rank": chosen["rank"], "stability": chosen["stability"], "n_control_cells": int(mask.sum())},
        output_paths=outputs, output_hashes={name: file_sha256(staging / name) for name in outputs},
        dependencies=request.dependencies, metadata={"fit_population": "confirmed_controls_only", "module_role": "reference", "retained_manifest_applied": retained is not None, "state_reference_hvg_applied": bool(reference_hvg)},
    )


def _blocked(spec: CapabilitySpec, request: CapabilityRunRequest, contract: DatasetContract, blockers: tuple[str, ...]) -> ResultEnvelope:
    return ResultEnvelope(
        run_id=request.run_id, request_id=request.request_id, capability_id=spec.capability_id,
        capability_version=spec.version, capability_trust=spec.trust_level,
        contract_id=contract.contract_id, contract_hash=contract.canonical_hash, scope=request.scope,
        status=AnalysisStatus.blocked, result_kind=spec.output_kind, source_class=spec.source_class,
        summary=f"{spec.capability_id} was blocked.", blockers=blockers, dependencies=request.dependencies,
    )


def _resolve_input(contract: DatasetContract, value: Any) -> Path:
    if value in (None, ""):
        raise ValueError("module capability is missing a required input path")
    candidate = Path(str(value)).expanduser()
    roots = [Path(item).expanduser().resolve() for item in contract.source_paths]
    if not candidate.is_absolute():
        directories = [item for item in roots if item.is_dir()]
        if not directories:
            raise ValueError("relative module input requires a directory DatasetContract source")
        candidate = directories[0] / candidate
    resolved = candidate.resolve()
    if not any(resolved == root or (root.is_dir() and root in resolved.parents) for root in roots):
        raise ValueError("module input is not bound to DatasetContract")
    return resolved
