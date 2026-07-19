from __future__ import annotations

import csv
import json
from itertools import combinations
from pathlib import Path
from typing import Any

from pertura_core import AnalysisStatus, CapabilityRunRequest, CapabilitySpec, DatasetContract, ResultEnvelope
from pertura_core.hashing import canonical_hash, file_sha256
from pertura_workflow.capabilities.execution_context import authoritative_input_roots


def run_state_reference(
    spec: CapabilitySpec,
    request: CapabilityRunRequest,
    contract: DatasetContract,
    staging: Path,
) -> ResultEnvelope:
    try:
        import anndata as ad
        import igraph as ig
        import leidenalg
        import numpy as np
        from scipy import sparse
        from sklearn.decomposition import PCA
        from sklearn.metrics import adjusted_rand_score
        from sklearn.neighbors import NearestNeighbors
    except ModuleNotFoundError as exc:
        return _blocked(spec, request, contract, (f"state_reference_v1 dependency is missing: {exc.name}",), {"setup_command": "pertura env setup python-science-v1"})

    params = request.parameters
    h5ad_path = _resolve_input(contract, params.get("h5ad_path"))
    control_column = str(params.get("control_column") or "")
    control_values = {str(item) for item in params.get("control_values") or []}
    if not control_column or not control_values:
        return _blocked(spec, request, contract, ("confirmed control_column and control_values are required",), {})
    data = ad.read_h5ad(h5ad_path, backed="r")
    try:
        if control_column not in data.obs.columns:
            return _blocked(spec, request, contract, (f"control column is missing: {control_column}",), {})
        control_mask = data.obs[control_column].astype(str).isin(control_values).to_numpy()
        if control_mask.sum() < 30:
            return _blocked(spec, request, contract, ("fewer than 30 confirmed control cells are available",), {"n_control_cells": int(control_mask.sum())})
        matrix = data.X[:]
        matrix = matrix.toarray() if sparse.issparse(matrix) else np.asarray(matrix)
        obs_names = [str(item) for item in data.obs_names]
        genes = np.asarray([str(item) for item in data.var_names])
    finally:
        if getattr(data, "file", None):
            data.file.close()
    if np.any(matrix < 0):
        return _blocked(spec, request, contract, ("state_reference_v1 requires nonnegative expression input",), {})
    library = matrix.sum(axis=1)
    normalized = np.divide(matrix, library[:, None], out=np.zeros_like(matrix, dtype=float), where=library[:, None] > 0) * 1e4
    normalized = np.log1p(normalized)
    control_matrix = normalized[control_mask]
    variances = control_matrix.var(axis=0)
    n_hvg = min(2000, int((variances > 0).sum()))
    if n_hvg < 2:
        return _blocked(spec, request, contract, ("fewer than two variable genes are available in controls",), {})
    hvg_index = np.argsort(variances)[-n_hvg:]
    n_pcs = min(50, control_matrix.shape[0] - 1, n_hvg)
    pca = PCA(n_components=n_pcs, random_state=0)
    control_pcs = pca.fit_transform(control_matrix[:, hvg_index])
    all_pcs = pca.transform(normalized[:, hvg_index])
    n_neighbors = min(15, control_pcs.shape[0] - 1)
    neighbors = NearestNeighbors(n_neighbors=n_neighbors + 1).fit(control_pcs)
    graph_indices = neighbors.kneighbors(control_pcs, return_distance=False)[:, 1:]
    edges = {tuple(sorted((index, int(neighbor)))) for index, row in enumerate(graph_indices) for neighbor in row if index != neighbor}
    graph = ig.Graph(n=control_pcs.shape[0], edges=sorted(edges), directed=False)

    candidates: list[dict[str, Any]] = []
    resolutions = [float(item) for item in params.get("resolutions") or [0.5, 1.0, 1.5]]
    seeds = [int(item) for item in params.get("seeds") or [0, 1, 2]]
    for resolution in resolutions:
        labels = []
        for seed in seeds:
            partition = leidenalg.find_partition(
                graph,
                leidenalg.RBConfigurationVertexPartition,
                resolution_parameter=resolution,
                seed=seed,
            )
            labels.append(np.asarray(partition.membership, dtype=int))
        cluster_count = len(set(labels[0]))
        ari_values = [adjusted_rand_score(left, right) for left, right in combinations(labels, 2)]
        stability = float(sum(ari_values) / len(ari_values)) if ari_values else 1.0
        candidates.append({"resolution": resolution, "cluster_count": cluster_count, "stability": stability, "labels": labels})
    eligible = [item for item in candidates if 2 <= item["cluster_count"] <= 30]
    if not eligible:
        return _blocked(spec, request, contract, ("no Leiden resolution produced 2-30 control clusters",), {"candidates": [{key: value for key, value in item.items() if key != "labels"} for item in candidates]})
    eligible.sort(key=lambda item: (-item["stability"], item["resolution"]))
    chosen = eligible[0]
    labels_by_seed = chosen["labels"]
    seed_scores = [sum(adjusted_rand_score(labels, other) for other in labels_by_seed) for labels in labels_by_seed]
    control_labels = labels_by_seed[int(np.argmax(seed_scores))]
    cluster_ids = {}
    for cluster in sorted(set(control_labels)):
        centroid = control_pcs[control_labels == cluster].mean(axis=0)
        cluster_ids[cluster] = "state_" + canonical_hash([round(float(item), 8) for item in centroid]).split(":", 1)[1][:12]

    model = NearestNeighbors(n_neighbors=min(15, control_pcs.shape[0])).fit(control_pcs)
    indices = model.kneighbors(all_pcs, return_distance=False)
    control_indices = np.flatnonzero(control_mask)
    assignments = []
    for cell_index, neighbor_indices in enumerate(indices):
        if control_mask[cell_index]:
            local_index = int(np.where(control_indices == cell_index)[0][0])
            label = int(control_labels[local_index])
            probability = 1.0
        else:
            votes = [int(control_labels[item]) for item in neighbor_indices]
            counts = {label: votes.count(label) for label in set(votes)}
            label, count = max(counts.items(), key=lambda item: (item[1], -item[0]))
            probability = count / len(votes)
        assignments.append({
            "cell_id": obs_names[cell_index],
            "is_control": bool(control_mask[cell_index]),
            "technical_state_id": cluster_ids[label] if probability >= 0.60 else "unresolved_state",
            "mapping_probability": probability,
            "candidate_human_label": None,
        })

    assignment_path = staging / "state_assignments.csv"
    metrics_path = staging / "state_reference.json"
    model_path = staging / "state_reference.npz"
    with assignment_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(assignments[0]))
        writer.writeheader()
        writer.writerows(assignments)
    np.savez_compressed(
        model_path,
        hvg_indices=hvg_index,
        hvg_names=genes[hvg_index],
        pca_components=pca.components_,
        pca_mean=pca.mean_,
        control_pcs=control_pcs,
        control_labels=control_labels,
        control_cell_ids=np.asarray(obs_names)[control_mask],
    )
    public_candidates = [{key: value for key, value in item.items() if key != "labels"} for item in candidates]
    metrics_payload = {
        "schema_version": "pertura-state-reference-v1",
        "fit_population": "confirmed_controls_only",
        "n_controls": int(control_mask.sum()),
        "n_hvg": n_hvg,
        "n_pcs": n_pcs,
        "n_neighbors": n_neighbors,
        "resolution_candidates": public_candidates,
        "chosen_resolution": chosen["resolution"],
        "chosen_stability": chosen["stability"],
        "technical_state_ids": list(cluster_ids.values()),
        "unresolved_state_count": sum(item["technical_state_id"] == "unresolved_state" for item in assignments),
        "human_labels_are_candidates": True,
        "leakage": {"perturbation_labels_used_for_fit": False, "test_split_used_for_fit": False},
    }
    metrics_path.write_text(json.dumps(metrics_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    outputs = (assignment_path.name, metrics_path.name, model_path.name)
    return ResultEnvelope(
        run_id=request.run_id, request_id=request.request_id, capability_id=spec.capability_id,
        capability_version=spec.version, capability_trust=spec.trust_level,
        contract_id=contract.contract_id, contract_hash=contract.canonical_hash, scope=request.scope,
        status=AnalysisStatus.completed, result_kind=spec.output_kind, source_class=spec.source_class,
        summary=f"Control-derived state reference fitted with {len(cluster_ids)} technical states.",
        metrics={"n_controls": int(control_mask.sum()), "n_states": len(cluster_ids), "stability": chosen["stability"], "unresolved_state_count": metrics_payload["unresolved_state_count"]},
        output_paths=outputs, output_hashes={name: file_sha256(staging / name) for name in outputs},
        dependencies=request.dependencies, metadata={"fit_population": "confirmed_controls_only", "mapping_probability_threshold": 0.60},
    )


def _blocked(spec: CapabilitySpec, request: CapabilityRunRequest, contract: DatasetContract, blockers: tuple[str, ...], metrics: dict[str, Any]) -> ResultEnvelope:
    return ResultEnvelope(
        run_id=request.run_id, request_id=request.request_id, capability_id=spec.capability_id,
        capability_version=spec.version, capability_trust=spec.trust_level,
        contract_id=contract.contract_id, contract_hash=contract.canonical_hash, scope=request.scope,
        status=AnalysisStatus.blocked, result_kind=spec.output_kind, source_class=spec.source_class,
        summary="State reference fitting was blocked.", blockers=blockers, metrics=metrics,
        dependencies=request.dependencies,
    )


def _resolve_input(contract: DatasetContract, value: Any) -> Path:
    if value in (None, ""):
        raise ValueError("state reference capability requires h5ad_path")
    candidate = Path(str(value)).expanduser()
    roots = authoritative_input_roots(contract)
    if not candidate.is_absolute():
        directories = [item for item in roots if item.is_dir()]
        if not directories:
            raise ValueError("relative h5ad_path requires a directory DatasetContract source")
        candidate = directories[0] / candidate
    resolved = candidate.resolve()
    if not any(resolved == root or (root.is_dir() and root in resolved.parents) for root in roots):
        raise ValueError("state reference input is not bound to DatasetContract")
    return resolved
