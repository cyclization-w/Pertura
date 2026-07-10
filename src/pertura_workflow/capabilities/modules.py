from __future__ import annotations

import json
from itertools import combinations
from pathlib import Path
from typing import Any

from pertura_core import AnalysisStatus, CapabilityRunRequest, CapabilitySpec, DatasetContract, ResultEnvelope
from pertura_core.hashing import file_sha256


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
        "source_path": str(path),
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
        from sklearn.decomposition import NMF
        from sklearn.metrics import adjusted_rand_score
    except ModuleNotFoundError as exc:
        return _blocked(spec, request, contract, (f"NMF dependency is missing: {exc.name}",))
    params = request.parameters
    path = _resolve_input(contract, params.get("h5ad_path"))
    control_column = str(params.get("control_column") or "")
    control_values = {str(item) for item in params.get("control_values") or []}
    if not control_column or not control_values:
        return _blocked(spec, request, contract, ("confirmed control_column and control_values are required",))
    data = ad.read_h5ad(path)
    if control_column not in data.obs.columns:
        return _blocked(spec, request, contract, (f"control column is missing: {control_column}",))
    mask = data.obs[control_column].astype(str).isin(control_values).to_numpy()
    matrix = data.X[mask]
    matrix = matrix.toarray() if sparse.issparse(matrix) else np.asarray(matrix)
    if np.any(matrix < 0):
        return _blocked(spec, request, contract, ("NMF requires a nonnegative matrix",))
    library = matrix.sum(axis=1)
    matrix = np.divide(matrix, library[:, None], out=np.zeros_like(matrix, dtype=float), where=library[:, None] > 0) * 1e4
    matrix = np.log1p(matrix)
    genes = np.asarray([str(item) for item in data.var_names])
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
            model = NMF(n_components=rank, init="nndsvda", random_state=seed, max_iter=1000)
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
    pd.DataFrame(scores, index=data.obs_names[mask], columns=list(modules)).rename_axis("cell_id").reset_index().to_parquet(score_path, index=False)
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
        dependencies=request.dependencies, metadata={"fit_population": "confirmed_controls_only", "module_role": "reference"},
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
