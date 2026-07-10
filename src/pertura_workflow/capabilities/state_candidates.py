from __future__ import annotations

import json
from itertools import combinations
from pathlib import Path
from typing import Any

from pertura_core import AnalysisStatus, CapabilityRunRequest, CapabilitySpec, DatasetContract
from pertura_core.hashing import canonical_hash

from pertura_workflow.capabilities.candidate_common import (
    blocked,
    dependency_results,
    envelope,
    resolve_input,
    resource_budget,
    write_json,
)
from pertura_workflow.capabilities.modules import run_nmf_modules
from pertura_workflow.environment import doctor_environment


def run_state_reference_fit(
    spec: CapabilitySpec,
    request: CapabilityRunRequest,
    contract: DatasetContract,
    staging: Path,
):
    environment = doctor_environment("perturbseq-python-v1")
    if not environment["ok"]:
        return blocked(
            spec,
            request,
            contract,
            *environment["problems"],
            metadata={"setup_command": "pertura env setup perturbseq-python-v1"},
        )
    try:
        import anndata as ad
        import igraph as ig
        import leidenalg
        import numpy as np
        import pandas as pd
        from scipy import sparse
        from sklearn.decomposition import PCA
        from sklearn.metrics import adjusted_rand_score
        from sklearn.neighbors import NearestNeighbors
    except ModuleNotFoundError as exc:
        return blocked(
            spec,
            request,
            contract,
            f"state reference dependency is missing: {exc.name}",
            metadata={"setup_command": "pertura env setup perturbseq-python-v1"},
        )
    max_memory_gb, n_jobs = resource_budget(request.parameters)
    h5ad_path = resolve_input(contract, request.parameters.get("h5ad_path"), label="h5ad_path")
    control_column = str(request.parameters.get("control_column") or "")
    control_values = {str(item) for item in request.parameters.get("control_values") or []}
    if not control_column or not control_values:
        return blocked(spec, request, contract, "confirmed control_column and control_values are required")
    data = ad.read_h5ad(h5ad_path, backed="r")
    try:
        if control_column not in data.obs.columns:
            return blocked(spec, request, contract, f"control column is missing: {control_column}")
        control_mask = data.obs[control_column].astype(str).isin(control_values).to_numpy()
        minimum_controls = int(request.parameters.get("minimum_control_cells", 30))
        if int(control_mask.sum()) < minimum_controls:
            return blocked(
                spec,
                request,
                contract,
                f"fewer than {minimum_controls} confirmed control cells are available",
            )
        estimate_gb = int(data.n_obs) * int(data.n_vars) * 8 / 1024**3
        if estimate_gb > max_memory_gb:
            return blocked(
                spec,
                request,
                contract,
                f"dense PCA input estimate {estimate_gb:.3f} GB exceeds max_memory_gb={max_memory_gb}",
            )
        matrix = data.X[:]
        matrix = matrix.toarray() if sparse.issparse(matrix) else np.asarray(matrix)
        obs_names = np.asarray([str(item) for item in data.obs_names])
        genes = np.asarray([str(item) for item in data.var_names])
    finally:
        if getattr(data, "file", None):
            data.file.close()
    if np.any(matrix < 0):
        return blocked(spec, request, contract, "state reference requires nonnegative expression input")
    library = matrix.sum(axis=1)
    normalized = np.divide(
        matrix,
        library[:, None],
        out=np.zeros_like(matrix, dtype=float),
        where=library[:, None] > 0,
    ) * 1e4
    normalized = np.log1p(normalized)
    controls = normalized[control_mask]
    variances = controls.var(axis=0)
    n_hvg = min(2000, int((variances > 0).sum()))
    if n_hvg < 2:
        return blocked(spec, request, contract, "fewer than two variable genes are available in controls")
    hvg_index = np.argsort(variances)[-n_hvg:]
    n_pcs = min(50, controls.shape[0] - 1, n_hvg)
    pca = PCA(n_components=n_pcs, random_state=1729)
    control_pcs = pca.fit_transform(controls[:, hvg_index])
    n_neighbors = min(15, control_pcs.shape[0] - 1)
    neighbors = NearestNeighbors(n_neighbors=n_neighbors + 1, n_jobs=n_jobs).fit(control_pcs)
    graph_indices = neighbors.kneighbors(control_pcs, return_distance=False)[:, 1:]
    edges = {
        tuple(sorted((index, int(neighbor))))
        for index, row in enumerate(graph_indices)
        for neighbor in row
        if index != neighbor
    }
    graph = ig.Graph(n=control_pcs.shape[0], edges=sorted(edges), directed=False)
    candidates = []
    resolutions = [float(item) for item in request.parameters.get("resolutions") or [0.5, 1.0, 1.5]]
    seeds = [int(item) for item in request.parameters.get("seeds") or [0, 1, 2]]
    for resolution in resolutions:
        label_sets = []
        for seed in seeds:
            partition = leidenalg.find_partition(
                graph,
                leidenalg.RBConfigurationVertexPartition,
                resolution_parameter=resolution,
                seed=seed,
            )
            label_sets.append(np.asarray(partition.membership, dtype=int))
        cluster_count = len(set(label_sets[0]))
        ari_values = [
            adjusted_rand_score(left, right)
            for left, right in combinations(label_sets, 2)
        ]
        candidates.append(
            {
                "resolution": resolution,
                "cluster_count": cluster_count,
                "stability": float(sum(ari_values) / len(ari_values)) if ari_values else 1.0,
                "labels": label_sets,
            }
        )
    eligible = [item for item in candidates if 2 <= item["cluster_count"] <= 30]
    if not eligible:
        return blocked(spec, request, contract, "no Leiden resolution produced 2-30 control clusters")
    eligible.sort(key=lambda item: (-item["stability"], item["resolution"]))
    chosen = eligible[0]
    seed_scores = [
        sum(adjusted_rand_score(labels, other) for other in chosen["labels"])
        for labels in chosen["labels"]
    ]
    control_labels = chosen["labels"][int(np.argmax(seed_scores))]
    technical_ids = {}
    for label in sorted(set(control_labels)):
        centroid = control_pcs[control_labels == label].mean(axis=0)
        technical_ids[label] = (
            "state_"
            + canonical_hash([round(float(item), 8) for item in centroid]).split(":", 1)[1][:12]
        )

    model_path = staging / "state_reference_fit.npz"
    np.savez_compressed(
        model_path,
        hvg_names=genes[hvg_index],
        pca_components=pca.components_,
        pca_mean=pca.mean_,
        control_pcs=control_pcs,
        control_labels=control_labels,
        control_cell_ids=obs_names[control_mask],
        technical_state_ids=np.asarray([technical_ids[item] for item in control_labels]),
    )
    assignment_path = staging / "control_state_assignments.parquet"
    pd.DataFrame(
        {
            "cell_id": obs_names[control_mask],
            "technical_state_id": [technical_ids[item] for item in control_labels],
            "is_control": True,
            "mapping_probability": 1.0,
        }
    ).to_parquet(assignment_path, index=False)
    public_candidates = [
        {key: value for key, value in item.items() if key != "labels"}
        for item in candidates
    ]
    manifest_path = write_json(
        staging,
        "state_reference_fit.json",
        {
            "schema_version": "pertura-state-reference-fit-v1",
            "fit_population": "confirmed_controls_only",
            "normalization": {"normalize_total": 1e4, "transform": "log1p"},
            "n_controls": int(control_mask.sum()),
            "n_hvg": n_hvg,
            "n_pcs": n_pcs,
            "n_neighbors": n_neighbors,
            "resolution_candidates": public_candidates,
            "chosen_resolution": chosen["resolution"],
            "chosen_stability": chosen["stability"],
            "technical_state_ids": sorted(set(technical_ids.values())),
            "leakage": {
                "perturbation_labels_used_for_fit": False,
                "test_split_used_for_fit": False,
            },
            "resource_budget": {"max_memory_gb": max_memory_gb, "n_jobs": n_jobs},
        },
    )
    return envelope(
        spec,
        request,
        contract,
        status=AnalysisStatus.completed,
        summary=f"Fitted a control-only reference with {len(technical_ids)} technical states.",
        metrics={
            "n_controls": int(control_mask.sum()),
            "n_states": len(technical_ids),
            "stability": chosen["stability"],
            "chosen_resolution": chosen["resolution"],
        },
        outputs=(model_path, assignment_path, manifest_path),
        metadata={"fit_population": "confirmed_controls_only"},
    )


def run_state_reference_map(
    spec: CapabilitySpec,
    request: CapabilityRunRequest,
    contract: DatasetContract,
    staging: Path,
):
    environment = doctor_environment("perturbseq-python-v1")
    if not environment["ok"]:
        return blocked(
            spec,
            request,
            contract,
            *environment["problems"],
            metadata={"setup_command": "pertura env setup perturbseq-python-v1"},
        )
    try:
        import anndata as ad
        import numpy as np
        import pandas as pd
        from scipy import sparse
        from sklearn.neighbors import NearestNeighbors
    except ModuleNotFoundError as exc:
        return blocked(
            spec,
            request,
            contract,
            f"state mapping dependency is missing: {exc.name}",
            metadata={"setup_command": "pertura env setup perturbseq-python-v1"},
        )
    h5ad_path = resolve_input(contract, request.parameters.get("h5ad_path"), label="h5ad_path")
    model_path = _parameter_or_dependency_path(
        contract,
        staging,
        request.parameters.get("reference_model_path"),
        suffix=".npz",
        capability_id="state.reference.fit.v1",
    )
    model = np.load(model_path, allow_pickle=False)
    data = ad.read_h5ad(h5ad_path, backed="r")
    try:
        matrix = data.X[:]
        matrix = matrix.toarray() if sparse.issparse(matrix) else np.asarray(matrix)
        obs_names = np.asarray([str(item) for item in data.obs_names])
        genes = [str(item) for item in data.var_names]
    finally:
        if getattr(data, "file", None):
            data.file.close()
    gene_index = {name: index for index, name in enumerate(genes)}
    missing = [str(name) for name in model["hvg_names"] if str(name) not in gene_index]
    if missing:
        return blocked(spec, request, contract, f"mapping input is missing {len(missing)} reference HVGs")
    selected = matrix[:, [gene_index[str(name)] for name in model["hvg_names"]]]
    library = matrix.sum(axis=1)
    normalized = np.divide(
        selected,
        library[:, None],
        out=np.zeros_like(selected, dtype=float),
        where=library[:, None] > 0,
    ) * 1e4
    normalized = np.log1p(normalized)
    all_pcs = (normalized - model["pca_mean"]) @ model["pca_components"].T
    control_pcs = model["control_pcs"]
    labels = [str(item) for item in model["technical_state_ids"]]
    n_neighbors = min(15, control_pcs.shape[0])
    neighbors = NearestNeighbors(n_neighbors=n_neighbors).fit(control_pcs)
    indices = neighbors.kneighbors(all_pcs, return_distance=False)
    threshold = float(request.parameters.get("mapping_probability_threshold", 0.60))
    assignments = []
    for cell, neighbor_indices in zip(obs_names, indices):
        votes = [labels[int(index)] for index in neighbor_indices]
        counts = {label: votes.count(label) for label in set(votes)}
        label, count = max(counts.items(), key=lambda item: (item[1], item[0]))
        probability = count / len(votes)
        assignments.append(
            {
                "cell_id": cell,
                "technical_state_id": label if probability >= threshold else "unresolved_state",
                "mapping_probability": probability,
                "candidate_human_label": None,
            }
        )
    output = staging / "state_mapping.parquet"
    pd.DataFrame(assignments).to_parquet(output, index=False)
    manifest = write_json(
        staging,
        "state_mapping.json",
        {
            "schema_version": "pertura-state-reference-map-v1",
            "mapping_probability_threshold": threshold,
            "n_cells": len(assignments),
            "unresolved_state_count": sum(
                item["technical_state_id"] == "unresolved_state"
                for item in assignments
            ),
            "reference_model_name": model_path.name,
            "reference_refit": False,
        },
    )
    unresolved = sum(item["technical_state_id"] == "unresolved_state" for item in assignments)
    caution = (
        ("one or more cells could not be mapped above the frozen-reference probability threshold",)
        if unresolved
        else ()
    )
    return envelope(
        spec,
        request,
        contract,
        status=AnalysisStatus.completed_with_caution if caution else AnalysisStatus.completed,
        summary=f"Mapped {len(assignments)} cells to the frozen control reference; {unresolved} remained unresolved.",
        cautions=caution,
        metrics={"n_cells": len(assignments), "unresolved_state_count": unresolved},
        outputs=(output, manifest),
        metadata={"reference_refit": False, "mapping_probability_threshold": threshold},
    )


def run_state_annotation_candidates(
    spec: CapabilitySpec,
    request: CapabilityRunRequest,
    contract: DatasetContract,
    staging: Path,
):
    try:
        import pandas as pd
    except ModuleNotFoundError as exc:
        return blocked(spec, request, contract, f"annotation dependency is missing: {exc.name}")
    assignment_path = _parameter_or_dependency_path(
        contract,
        staging,
        request.parameters.get("assignment_path"),
        suffix=".parquet",
        capability_id="state.reference.map_knn.v1",
    )
    table = pd.read_parquet(assignment_path)
    if "technical_state_id" not in table.columns:
        return blocked(spec, request, contract, "state assignment table lacks technical_state_id")
    manual = {
        str(key): str(value)
        for key, value in dict(request.parameters.get("manual_labels") or {}).items()
    }
    marker_candidates = {
        str(key): [str(item) for item in value]
        for key, value in dict(request.parameters.get("marker_candidates") or {}).items()
    }
    records = []
    conflicts = 0
    for state_id in sorted(set(table["technical_state_id"].astype(str)) - {"unresolved_state"}):
        candidates = []
        if state_id in manual:
            candidates.append({"label": manual[state_id], "source": "user_confirmed_mapping"})
        candidates.extend(
            {"label": label, "source": "marker_or_gmt_candidate"}
            for label in marker_candidates.get(state_id, [])
        )
        unique = {(item["label"], item["source"]) for item in candidates}
        conflicts += int(len({item[0] for item in unique}) > 1)
        records.append(
            {
                "technical_state_id": state_id,
                "candidate_labels": [
                    {"label": label, "source": source}
                    for label, source in sorted(unique)
                ],
                "label_status": "candidate" if unique else "unresolved",
                "technical_id_overwritten": False,
            }
        )
    output = write_json(
        staging,
        "state_annotation_candidates.json",
        {
            "schema_version": "pertura-state-annotation-candidates-v1",
            "records": records,
            "conflict_count": conflicts,
            "human_labels_are_candidates": True,
            "llm_labels_can_overwrite_technical_id": False,
        },
    )
    unresolved = sum(not item["candidate_labels"] for item in records)
    caution = []
    if unresolved:
        caution.append(f"{unresolved} technical states have no label candidates")
    if conflicts:
        caution.append(f"{conflicts} technical states have conflicting label candidates")
    return envelope(
        spec,
        request,
        contract,
        status=AnalysisStatus.completed_with_caution if caution else AnalysisStatus.completed,
        summary=f"Generated candidate annotations for {len(records)} technical states.",
        cautions=caution,
        metrics={
            "n_states": len(records),
            "unresolved_label_count": unresolved,
            "conflict_count": conflicts,
        },
        outputs=(output,),
        metadata={"human_labels_are_candidates": True},
    )


def run_control_nmf(
    spec: CapabilitySpec,
    request: CapabilityRunRequest,
    contract: DatasetContract,
    staging: Path,
):
    if request.parameters.get("test_split_used") or request.parameters.get("perturbation_labels_used"):
        return blocked(
            spec,
            request,
            contract,
            "reference module learning cannot use perturbation labels or evaluation/test cells",
            metadata={"leakage_detected": True},
        )
    environment = doctor_environment("python-science-v1")
    if not environment["ok"]:
        return blocked(
            spec,
            request,
            contract,
            *environment["problems"],
            metadata={"setup_command": "pertura env setup python-science-v1"},
        )
    return run_nmf_modules(spec, request, contract, staging)


def _parameter_or_dependency_path(
    contract: DatasetContract,
    staging: Path,
    value: Any,
    *,
    suffix: str,
    capability_id: str,
) -> Path:
    if value not in (None, ""):
        resolved = resolve_input(contract, value, label=f"{capability_id} input")
        assert resolved is not None
        return resolved
    for result in dependency_results(staging):
        if result.get("capability_id") != capability_id:
            continue
        for path in result.get("local_output_paths") or []:
            candidate = Path(path)
            if candidate.suffix.lower() == suffix and candidate.is_file():
                return candidate
    raise ValueError(f"{capability_id} dependency does not expose a {suffix} output")
