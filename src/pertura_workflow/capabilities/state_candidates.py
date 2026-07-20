from __future__ import annotations

import json
import csv
import gzip
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
    consume_dependency_output,
)
from pertura_workflow.capabilities.backed_selection import (
    BackedSelectionStats,
    iter_backed_row_selection,
    materialize_backed_selection,
)
from pertura_workflow.capabilities.dependency_inputs import retained_cells_for_request
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
        from sklearn.decomposition import IncrementalPCA
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
    budget = resource_budget(request.parameters)
    max_memory_gb, n_jobs = budget
    h5ad_path = resolve_input(contract, request.parameters.get("h5ad_path"), label="h5ad_path")
    selection_path = resolve_input(
        contract, request.parameters.get("selection_path"), label="selection_path"
    )
    control_column = str(request.parameters.get("control_column") or "")
    control_values = {str(item) for item in request.parameters.get("control_values") or []}
    if not control_column or not control_values:
        return blocked(spec, request, contract, "confirmed control_column and control_values are required")
    data = ad.read_h5ad(h5ad_path, backed="r")
    try:
        if control_column not in data.obs.columns:
            return blocked(spec, request, contract, f"control column is missing: {control_column}")
        control_mask = data.obs[control_column].astype(str).isin(control_values).to_numpy()
        selected_cells = _selected_cell_ids(selection_path)
        control_mask &= data.obs_names.astype(str).isin(selected_cells)
        retained = retained_cells_for_request(staging, request, required=True)
        if retained is not None:
            control_mask &= data.obs_names.astype(str).isin(retained)
        minimum_controls = int(request.parameters.get("minimum_control_cells", 30))
        if int(control_mask.sum()) < minimum_controls:
            return blocked(
                spec,
                request,
                contract,
                f"fewer than {minimum_controls} confirmed control cells are available",
            )
        control_indices = np.flatnonzero(control_mask)
        obs_names = np.asarray([str(item) for item in data.obs_names])
        genes = np.asarray([str(item) for item in data.var_names])
        control_dense_bytes = budget.dense_bytes(len(control_indices), int(data.n_vars))
        source_is_sparse = sparse.issparse(data.X) or "SparseDataset" in type(data.X).__name__
        if not source_is_sparse and control_dense_bytes > budget.max_bytes:
            return blocked(
                spec, request, contract,
                f"dense control slice requires {control_dense_bytes / 1024**3:.3f} GB, exceeding max_memory_gb={max_memory_gb}",
            )
        matrix, selection_stats = materialize_backed_selection(
            data.X,
            control_indices,
            chunk_rows=budget.chunk_rows,
        )
    finally:
        if getattr(data, "file", None):
            data.file.close()
    if sparse.issparse(matrix):
        matrix = matrix.tocsr().astype(float)
        if matrix.data.size and np.any(matrix.data < 0):
            return blocked(spec, request, contract, "state reference requires nonnegative expression input")
        library = np.asarray(matrix.sum(axis=1)).ravel()
        scale = np.divide(1e4, library, out=np.zeros_like(library, dtype=float), where=library > 0)
        normalized_sparse = sparse.diags(scale) @ matrix
        normalized_sparse.data = np.log1p(normalized_sparse.data)
        means = np.asarray(normalized_sparse.mean(axis=0)).ravel()
        squared_means = np.asarray(normalized_sparse.power(2).mean(axis=0)).ravel()
        variances = np.maximum(0.0, squared_means - means**2)
    else:
        matrix = np.asarray(matrix)
        if np.any(matrix < 0):
            return blocked(spec, request, contract, "state reference requires nonnegative expression input")
        budget.require_dense(matrix.shape[0], matrix.shape[1], arrays=2, label="control normalization")
        library = matrix.sum(axis=1)
        normalized_dense = np.divide(matrix, library[:, None], out=np.zeros_like(matrix, dtype=float), where=library[:, None] > 0) * 1e4
        normalized_dense = np.log1p(normalized_dense)
        variances = normalized_dense.var(axis=0)
    n_hvg = min(2000, int((variances > 0).sum()))
    if n_hvg < 2:
        return blocked(spec, request, contract, "fewer than two variable genes are available in controls")
    hvg_index = np.argsort(variances)[-n_hvg:]
    n_pcs = min(50, len(control_indices) - 1, n_hvg)
    budget.require_dense(len(control_indices), n_hvg, arrays=3, label="control HVG PCA")
    controls_hvg = normalized_sparse[:, hvg_index].toarray() if sparse.issparse(matrix) else normalized_dense[:, hvg_index]
    pca = IncrementalPCA(n_components=n_pcs, batch_size=max(n_pcs, min(budget.chunk_rows, len(control_indices))))
    control_pcs = pca.fit_transform(controls_hvg)
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
        control_cell_ids=obs_names[control_indices],
        technical_state_ids=np.asarray([technical_ids[item] for item in control_labels]),
    )
    assignment_path = staging / "control_state_assignments.parquet"
    pd.DataFrame(
        {
            "cell_id": obs_names[control_indices],
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
            "n_controls": int(len(control_indices)),
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
            "n_controls": int(len(control_indices)),
            "n_states": len(technical_ids),
            "stability": chosen["stability"],
            "chosen_resolution": chosen["resolution"],
        },
        outputs=(model_path, assignment_path, manifest_path),
        metadata={
            "fit_population": "confirmed_controls_only",
            "retained_manifest_applied": True,
            "backed_selection": {
                "block_reads": selection_stats.block_reads,
                "source_rows_read": selection_stats.source_rows_read,
                "selected_rows": selection_stats.selected_rows,
            },
        },
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
    budget = resource_budget(request.parameters)
    h5ad_path = resolve_input(contract, request.parameters.get("h5ad_path"), label="h5ad_path")
    selection_path = resolve_input(
        contract, request.parameters.get("selection_path"), label="selection_path"
    )
    model_path = _parameter_or_dependency_path(
        contract,
        staging,
        request.parameters.get("reference_model_path"),
        suffix=".npz",
        capability_id="state.reference.fit.v1",
    )
    model = np.load(model_path, allow_pickle=False)
    data = ad.read_h5ad(h5ad_path, backed="r")
    retained = retained_cells_for_request(staging, request, required=True)
    retained_set = set(retained or ()) & _selected_cell_ids(selection_path)
    retained_indices = np.asarray(
        [
            index
            for index, cell in enumerate(data.obs_names.astype(str))
            if cell in retained_set
        ],
        dtype=int,
    )
    if not len(retained_indices):
        if getattr(data, "file", None):
            data.file.close()
        return blocked(
            spec,
            request,
            contract,
            "retained-cell manifest has no overlap with state mapping input",
        )
    genes = [str(item) for item in data.var_names]
    gene_index = {name: index for index, name in enumerate(genes)}
    missing = [str(name) for name in model["hvg_names"] if str(name) not in gene_index]
    if missing:
        if getattr(data, "file", None):
            data.file.close()
        return blocked(spec, request, contract, f"mapping input is missing {len(missing)} reference HVGs")
    selected_indices = [gene_index[str(name)] for name in model["hvg_names"]]
    budget.require_dense(budget.chunk_rows, len(selected_indices), arrays=3, label="state mapping chunk")
    control_pcs = model["control_pcs"]
    labels = [str(item) for item in model["technical_state_ids"]]
    n_neighbors = min(15, control_pcs.shape[0])
    neighbors = NearestNeighbors(n_neighbors=n_neighbors, n_jobs=budget.n_jobs).fit(control_pcs)
    threshold = float(request.parameters.get("mapping_probability_threshold", 0.60))
    output = staging / "state_mapping.parquet"
    writer = None
    selection_stats = BackedSelectionStats()
    unresolved = 0
    mapped_count = 0
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
        for chunk_indices, raw in iter_backed_row_selection(
            data.X,
            retained_indices,
            chunk_rows=budget.chunk_rows,
            stats=selection_stats,
        ):
            if sparse.issparse(raw):
                raw = raw.tocsr()
                library = np.asarray(raw.sum(axis=1)).ravel()
                selected = raw[:, selected_indices].toarray()
            else:
                raw = np.asarray(raw)
                library = raw.sum(axis=1)
                selected = raw[:, selected_indices]
            normalized = np.divide(selected, library[:, None], out=np.zeros_like(selected, dtype=float), where=library[:, None] > 0) * 1e4
            normalized = np.log1p(normalized)
            pcs = (normalized - model["pca_mean"]) @ model["pca_components"].T
            indices = neighbors.kneighbors(pcs, return_distance=False)
            rows = []
            cell_ids = [str(data.obs_names[index]) for index in chunk_indices]
            for cell, neighbor_indices, cell_pcs in zip(cell_ids, indices, pcs):
                votes = [labels[int(index)] for index in neighbor_indices]
                counts = {label: votes.count(label) for label in set(votes)}
                label, count = max(counts.items(), key=lambda item: (item[1], item[0]))
                probability = count / len(votes)
                technical = label if probability >= threshold else "unresolved_state"
                unresolved += technical == "unresolved_state"
                row = {
                    "cell_id": cell,
                    "technical_state_id": technical,
                    "mapping_probability": probability,
                    "candidate_human_label": None,
                }
                row.update(
                    {
                        f"PC{index + 1}": float(value)
                        for index, value in enumerate(cell_pcs)
                    }
                )
                rows.append(row)
            table = pa.Table.from_pandas(pd.DataFrame(rows), preserve_index=False)
            writer = writer or pq.ParquetWriter(output, table.schema)
            writer.write_table(table)
            mapped_count += len(rows)
    finally:
        if writer is not None:
            writer.close()
        if getattr(data, "file", None):
            data.file.close()
    manifest = write_json(
        staging,
        "state_mapping.json",
        {
            "schema_version": "pertura-state-reference-map-v1",
            "mapping_probability_threshold": threshold,
            "n_cells": mapped_count,
            "excluded_cell_count": int(data.n_obs) - mapped_count,
            "unresolved_state_count": unresolved,
            "reference_model_name": model_path.name,
            "reference_hvg_names": [str(item) for item in model["hvg_names"]],
            "pca_columns": [
                f"PC{index + 1}" for index in range(model["pca_components"].shape[0])
            ],
            "reference_refit": False,
        },
    )
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
        summary=f"Mapped {mapped_count} cells to the frozen control reference; {unresolved} remained unresolved.",
        cautions=caution,
        metrics={
            "n_cells": mapped_count,
            "excluded_cell_count": int(data.n_obs) - mapped_count,
            "unresolved_state_count": unresolved,
        },
        outputs=(output, manifest),
        metadata={
            "reference_refit": False,
            "mapping_probability_threshold": threshold,
            "backed_selection": {
                "block_reads": selection_stats.block_reads,
                "source_rows_read": selection_stats.source_rows_read,
                "selected_rows": selection_stats.selected_rows,
            },
        },
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
    reference_model = _parameter_or_dependency_path(
        contract,
        staging,
        None,
        suffix=".npz",
        capability_id="state.reference.fit.v1",
    )
    child = request.model_copy(
        update={
            "parameters": dict(request.parameters)
            | {"reference_model_path": str(reference_model)}
        }
    )
    return run_nmf_modules(spec, child, contract, staging)


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
                consume_dependency_output(
                    result, candidate, usage="scientific_input"
                )
                return candidate
    raise ValueError(f"{capability_id} dependency does not expose a {suffix} output")


def _selected_cell_ids(path: Path) -> set[str]:
    opener = gzip.open if path.suffix.lower() == ".gz" else open
    with opener(path, "rt", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        if not reader.fieldnames:
            raise ValueError("cell selection has no header")
        key = next(
            (name for name in ("cell_id", "raw_barcode") if name in reader.fieldnames),
            None,
        )
        if key is None:
            raise ValueError("cell selection is missing cell_id")
        selected = {
            str(row.get(key) or "").strip()
            for row in reader
            if str(row.get(key) or "").strip()
        }
    if not selected:
        raise ValueError("cell selection is empty")
    return selected
