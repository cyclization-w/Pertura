from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
from itertools import combinations
from pathlib import Path
from typing import Any, Iterable


SCHEMA_VERSION = "pertura-paper-ref03-v1"
DATASET_ID = "papalexi_thp1_eccite"
SEED = 1729
RESOLUTIONS = (0.5, 1.0, 1.5)
SEEDS = (1729, 1730, 1731)
MARKER_SETS = {
    "interferon_response": ("IFIT1", "IFIT2", "IFIT3", "ISG15", "MX1"),
    "inflammatory_response": ("NFKBIA", "TNF", "IL1B", "CXCL8"),
    "cell_cycle_s": ("PCNA", "MCM5", "MCM6", "TYMS"),
    "cell_cycle_g2m": ("MKI67", "TOP2A", "CDK1", "CCNB1"),
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return "sha256:" + digest.hexdigest()


def _path_sha256(path: Path) -> str:
    if path.is_file():
        return _sha256(path)
    digest = hashlib.sha256()
    for item in sorted(value for value in path.rglob("*") if value.is_file()):
        digest.update(item.relative_to(path).as_posix().encode("utf-8") + b"\0")
        with item.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
    return "sha256:" + digest.hexdigest()


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _write_tsv(path: Path, fields: list[str], rows: Iterable[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=fields,
            delimiter="\t",
            lineterminator="\n",
            extrasaction="ignore",
        )
        writer.writeheader()
        writer.writerows(rows)


def _selection_rows(splits_path: Path, split: str) -> list[dict[str, str]]:
    payload = json.loads(splits_path.read_text(encoding="utf-8"))
    record = payload["datasets"][DATASET_ID][split]
    path = splits_path.resolve().parent.parent / record["cell_selection_path"]
    if not path.is_file() or _sha256(path) != record["cell_selection_file_sha256"]:
        raise ValueError(f"Papalexi {split} selection hash drift")
    with gzip.open(path, "rt", encoding="utf-8", newline="") as handle:
        rows = [dict(row) for row in csv.DictReader(handle, delimiter="\t")]
    if len(rows) != len({row["cell_id"] for row in rows}):
        raise ValueError(f"duplicate Papalexi {split} cell identity")
    return rows


def _dense(matrix: Any, *, rows: int, columns: int, max_memory_gb: float, label: str) -> Any:
    import numpy as np
    from scipy import sparse

    estimated = rows * columns * 8 * 3
    if estimated > max_memory_gb * 1024**3:
        raise MemoryError(
            f"{label} requires about {estimated / 1024**3:.3f} GB, "
            f"exceeding max_memory_gb={max_memory_gb}"
        )
    return matrix.toarray() if sparse.issparse(matrix) else np.asarray(matrix)


def _normalized_subset(source: Any, cell_ids: list[str]) -> Any:
    import numpy as np
    import scanpy as sc
    from scipy import sparse

    index = source.obs_names.astype(str).get_indexer(cell_ids)
    missing = [cell_ids[position] for position, value in enumerate(index) if value < 0]
    if missing:
        raise ValueError(f"Papalexi artifact is missing {len(missing)} selected cells")
    index = np.sort(index)
    subset = source[index, :].to_memory()
    if sparse.issparse(subset.X):
        values = subset.X.data
    else:
        values = np.asarray(subset.X).reshape(-1)
    if values.size and (not np.isfinite(values).all() or (values < 0).any()):
        raise ValueError("state reference requires finite nonnegative expression")
    sc.pp.normalize_total(subset, target_sum=1e4)
    sc.pp.log1p(subset)
    return subset


def _stable_state_id(centroid: Any) -> str:
    payload = json.dumps(
        [round(float(value), 8) for value in centroid],
        separators=(",", ":"),
    ).encode("utf-8")
    return "state_" + hashlib.sha256(payload).hexdigest()[:12]


def _vote_assignments(
    points: Any,
    control_pcs: Any,
    control_state_ids: Any,
    *,
    n_neighbors: int,
    probability_threshold: float,
    distance_threshold: float,
) -> list[dict[str, Any]]:
    import numpy as np
    from sklearn.neighbors import NearestNeighbors

    model = NearestNeighbors(n_neighbors=n_neighbors, n_jobs=1).fit(control_pcs)
    distances, indices = model.kneighbors(points, return_distance=True)
    rows: list[dict[str, Any]] = []
    for local_distances, local_indices in zip(distances, indices):
        votes = [str(control_state_ids[index]) for index in local_indices]
        counts = {label: votes.count(label) for label in set(votes)}
        label, count = max(counts.items(), key=lambda item: (item[1], item[0]))
        probability = count / len(votes)
        median_distance = float(np.median(local_distances))
        rejected = probability < probability_threshold or median_distance > distance_threshold
        rows.append(
            {
                "technical_state_id": "unresolved_state" if rejected else label,
                "nearest_state_id": label,
                "mapping_probability": probability,
                "median_neighbor_distance": median_distance,
                "distance_threshold": distance_threshold,
                "rejected": rejected,
            }
        )
    return rows


def _candidate_rows(control: Any, labels: Any, state_ids: dict[int, str]) -> list[dict[str, Any]]:
    import numpy as np
    from scipy import sparse

    genes = [str(value) for value in control.var_names]
    gene_index = {gene: index for index, gene in enumerate(genes)}
    clusters = sorted(state_ids)
    raw_scores = np.zeros((len(clusters), len(MARKER_SETS)), dtype=float)
    evidence: dict[str, list[str]] = {}
    for marker_index, (marker_name, markers) in enumerate(MARKER_SETS.items()):
        present = [marker for marker in markers if marker in gene_index]
        evidence[marker_name] = present
        if not present:
            continue
        columns = [gene_index[marker] for marker in present]
        for cluster_index, cluster in enumerate(clusters):
            block = control.X[labels == cluster, :][:, columns]
            raw_scores[cluster_index, marker_index] = float(
                block.mean() if sparse.issparse(block) else np.asarray(block).mean()
            )
    means = raw_scores.mean(axis=0)
    standard = raw_scores.std(axis=0)
    zscores = np.divide(
        raw_scores - means,
        standard,
        out=np.zeros_like(raw_scores),
        where=standard > 0,
    )
    marker_names = list(MARKER_SETS)
    rows: list[dict[str, Any]] = []
    for cluster_index, cluster in enumerate(clusters):
        order = np.argsort(zscores[cluster_index])[::-1]
        top = int(order[0])
        second = int(order[1]) if len(order) > 1 else top
        top_score = float(zscores[cluster_index, top])
        margin = top_score - float(zscores[cluster_index, second])
        if top_score < 0.5 or len(evidence[marker_names[top]]) < 2:
            status = "unsupported"
            candidates = ""
        elif margin <= 0.25:
            status = "ambiguous"
            candidates = f"{marker_names[top]};{marker_names[second]}"
        else:
            status = "candidate"
            candidates = marker_names[top]
        rows.append(
            {
                "record_id": state_ids[cluster],
                "record_source": "real_control_marker_evidence",
                "technical_state_id": state_ids[cluster],
                "candidate_labels": candidates,
                "label_status": status,
                "evidence_genes": ";".join(evidence[marker_names[top]]),
                "top_marker_score": f"{top_score:.8g}",
                "strong_claim_allowed": "false",
                "technical_id_overwritten": "false",
            }
        )
    rows.extend(
        [
            {
                "record_id": "planted_ambiguous_state",
                "record_source": "planted_protocol_boundary",
                "technical_state_id": "planted_ambiguous_state",
                "candidate_labels": "interferon_response;inflammatory_response",
                "label_status": "ambiguous",
                "evidence_genes": "IFIT1;NFKBIA",
                "top_marker_score": "1.0",
                "strong_claim_allowed": "false",
                "technical_id_overwritten": "false",
            },
            {
                "record_id": "planted_unsupported_state",
                "record_source": "planted_protocol_boundary",
                "technical_state_id": "planted_unsupported_state",
                "candidate_labels": "",
                "label_status": "unsupported",
                "evidence_genes": "",
                "top_marker_score": "0.0",
                "strong_claim_allowed": "false",
                "technical_id_overwritten": "false",
            },
        ]
    )
    return rows


def generate(
    datasets_path: Path,
    splits_path: Path,
    output_dir: Path,
    *,
    max_memory_gb: float,
) -> dict[str, Any]:
    import anndata as ad
    import igraph as ig
    import leidenalg
    import numpy as np
    import scanpy as sc
    import sklearn
    from sklearn.decomposition import PCA
    from sklearn.metrics import adjusted_rand_score
    from sklearn.neighbors import NearestNeighbors

    datasets = json.loads(datasets_path.read_text(encoding="utf-8"))
    record = datasets["datasets"][DATASET_ID]
    artifact = Path(record["artifact_path"]).resolve()
    if not artifact.is_file():
        raise FileNotFoundError(artifact)
    if record.get("artifact_sha256") and _sha256(artifact) != record["artifact_sha256"]:
        raise ValueError("Papalexi artifact hash drift")
    calibration = _selection_rows(splits_path, "calibration")
    evaluation = _selection_rows(splits_path, "evaluation")
    calibration_ids = {row["cell_id"] for row in calibration}
    evaluation_ids = {row["cell_id"] for row in evaluation}
    if calibration_ids & evaluation_ids:
        raise ValueError("Papalexi calibration/evaluation overlap")
    control_ids = [
        row["cell_id"]
        for row in calibration
        if str(row["is_control"]).lower() == "true"
    ]
    if len(control_ids) < 30:
        raise ValueError("REF-03 requires at least 30 calibration control cells")

    output_dir.mkdir(parents=True, exist_ok=True)
    reference_dir = output_dir / "control_state_reference"
    reference_dir.mkdir(parents=True, exist_ok=True)
    print(f"REF-03-A: loading {len(control_ids)} calibration control cells", flush=True)
    source = ad.read_h5ad(artifact, backed="r")
    try:
        control = _normalized_subset(source, control_ids)
        evaluation_data = _normalized_subset(
            source, [row["cell_id"] for row in evaluation]
        )
    finally:
        if getattr(source, "file", None) is not None:
            source.file.close()
    control_ids = [str(value) for value in control.obs_names]

    sc.pp.highly_variable_genes(
        control,
        flavor="seurat",
        n_top_genes=min(2000, int(control.n_vars)),
        subset=False,
        inplace=True,
    )
    hvg_names = np.asarray(control.var_names[control.var["highly_variable"]], dtype=str)
    if len(hvg_names) < 2:
        raise ValueError("REF-03 found fewer than two variable control genes")
    control_matrix = _dense(
        control[:, hvg_names].X,
        rows=control.n_obs,
        columns=len(hvg_names),
        max_memory_gb=max_memory_gb,
        label="control HVG PCA",
    ).astype(float, copy=False)
    n_pcs = min(30, control_matrix.shape[0] - 1, control_matrix.shape[1])
    pca = PCA(n_components=n_pcs, svd_solver="full", random_state=SEED)
    control_pcs = pca.fit_transform(control_matrix)
    n_neighbors = min(15, len(control_pcs) - 1)
    neighbor_model = NearestNeighbors(n_neighbors=n_neighbors + 1, n_jobs=1).fit(control_pcs)
    control_distances, graph_indices = neighbor_model.kneighbors(control_pcs)
    graph_indices = graph_indices[:, 1:]
    control_distance = np.median(control_distances[:, 1:], axis=1)
    distance_threshold = float(np.quantile(control_distance, 0.99))
    edges = {
        tuple(sorted((index, int(neighbor))))
        for index, neighbors in enumerate(graph_indices)
        for neighbor in neighbors
        if index != neighbor
    }
    graph = ig.Graph(n=len(control_pcs), edges=sorted(edges), directed=False)

    candidates: list[dict[str, Any]] = []
    stability_rows: list[dict[str, Any]] = []
    for resolution in RESOLUTIONS:
        label_sets = []
        for seed in SEEDS:
            partition = leidenalg.find_partition(
                graph,
                leidenalg.RBConfigurationVertexPartition,
                resolution_parameter=resolution,
                seed=seed,
            )
            label_sets.append(np.asarray(partition.membership, dtype=int))
        pairwise = []
        for (left_seed, left), (right_seed, right) in combinations(
            zip(SEEDS, label_sets), 2
        ):
            ari = float(adjusted_rand_score(left, right))
            pairwise.append(ari)
            stability_rows.append(
                {
                    "resolution": resolution,
                    "left_seed": left_seed,
                    "right_seed": right_seed,
                    "cluster_count": len(set(left)),
                    "ari": f"{ari:.12g}",
                }
            )
        candidates.append(
            {
                "resolution": resolution,
                "cluster_count": len(set(label_sets[0])),
                "mean_pairwise_ari": float(np.mean(pairwise)) if pairwise else 1.0,
                "labels": label_sets,
            }
        )
    eligible = [item for item in candidates if 2 <= item["cluster_count"] <= 30]
    if not eligible:
        raise ValueError("REF-03 found no 2-30 cluster Leiden solution")
    chosen = sorted(
        eligible, key=lambda item: (-item["mean_pairwise_ari"], item["resolution"])
    )[0]
    seed_scores = [
        sum(adjusted_rand_score(labels, other) for other in chosen["labels"])
        for labels in chosen["labels"]
    ]
    labels = chosen["labels"][int(np.argmax(seed_scores))]
    state_ids = {
        cluster: _stable_state_id(control_pcs[labels == cluster].mean(axis=0))
        for cluster in sorted(set(labels))
    }
    control_state_ids = np.asarray([state_ids[int(label)] for label in labels])

    model_path = reference_dir / "model.npz"
    np.savez_compressed(
        model_path,
        hvg_names=hvg_names,
        pca_components=pca.components_,
        pca_mean=pca.mean_,
        control_pcs=control_pcs,
        control_state_ids=control_state_ids,
        control_cell_ids=np.asarray(control_ids),
        distance_threshold=np.asarray([distance_threshold]),
    )
    assignments_path = reference_dir / "control_assignments.tsv"
    _write_tsv(
        assignments_path,
        ["cell_id", "technical_state_id", "is_control", "mapping_probability"],
        (
            {
                "cell_id": cell_id,
                "technical_state_id": state_id,
                "is_control": "true",
                "mapping_probability": "1.0",
            }
            for cell_id, state_id in zip(control_ids, control_state_ids)
        ),
    )
    reference_manifest_path = reference_dir / "reference_manifest.json"
    _write_json(
        reference_manifest_path,
        {
            "schema_version": SCHEMA_VERSION,
            "reference_pack_id": "REF-03",
            "generator_job_id": "REF-03-A",
            "dataset_id": DATASET_ID,
            "fit_population": "calibration_controls_only",
            "calibration_cell_count": len(calibration),
            "control_cell_count": len(control_ids),
            "evaluation_cell_count_used_for_fit": 0,
            "n_hvg": len(hvg_names),
            "n_pcs": n_pcs,
            "n_neighbors": n_neighbors,
            "resolutions": list(RESOLUTIONS),
            "seeds": list(SEEDS),
            "chosen_resolution": chosen["resolution"],
            "chosen_mean_pairwise_ari": chosen["mean_pairwise_ari"],
            "state_ids": sorted(state_ids.values()),
            "distance_rejection_quantile": 0.99,
            "distance_threshold": distance_threshold,
            "normalization": {"target_sum": 10000, "transform": "log1p"},
        },
    )
    stability_path = output_dir / "state_stability.tsv"
    _write_tsv(
        stability_path,
        ["resolution", "left_seed", "right_seed", "cluster_count", "ari"],
        stability_rows,
    )
    print(
        f"REF-03-A: fitted {len(state_ids)} states at resolution {chosen['resolution']}",
        flush=True,
    )

    print(f"REF-03-B: mapping {evaluation_data.n_obs} evaluation cells", flush=True)
    evaluation_matrix = _dense(
        evaluation_data[:, hvg_names].X,
        rows=evaluation_data.n_obs,
        columns=len(hvg_names),
        max_memory_gb=max_memory_gb,
        label="evaluation HVG projection",
    ).astype(float, copy=False)
    evaluation_pcs = pca.transform(evaluation_matrix)
    mapped = _vote_assignments(
        evaluation_pcs,
        control_pcs,
        control_state_ids,
        n_neighbors=n_neighbors,
        probability_threshold=0.60,
        distance_threshold=distance_threshold,
    )
    mapping_path = output_dir / "state_mapping_reference.tsv"
    _write_tsv(
        mapping_path,
        [
            "cell_id", "technical_state_id", "nearest_state_id",
            "mapping_probability", "median_neighbor_distance",
            "distance_threshold", "rejected",
        ],
        (
            {"cell_id": cell_id, **record}
            for cell_id, record in zip(evaluation_data.obs_names.astype(str), mapped)
        ),
    )

    planted_points = []
    planted_truth = []
    center = control_pcs.mean(axis=0)
    span = np.maximum(np.ptp(control_pcs, axis=0), 1.0)
    for axis in range(min(6, n_pcs)):
        for direction in (-1.0, 1.0):
            point = center.copy()
            point[axis] += direction * span[axis] * 8.0
            planted_points.append(point)
            planted_truth.append((f"outlier-pc{axis + 1}-{'neg' if direction < 0 else 'pos'}", True, "out_of_reference"))
    planted_mapped = _vote_assignments(
        np.asarray(planted_points),
        control_pcs,
        control_state_ids,
        n_neighbors=n_neighbors,
        probability_threshold=0.60,
        distance_threshold=distance_threshold,
    )
    if not all(record["rejected"] for record in planted_mapped):
        raise ValueError("REF-03 planted out-of-reference states were not all rejected")
    rejection_path = output_dir / "mapping_rejection_truth.tsv"
    _write_tsv(
        rejection_path,
        [
            "fixture_id", "expected_rejected", "expected_state_id",
            "reference_rejected", "reference_nearest_state_id",
            "mapping_probability", "median_neighbor_distance",
            "distance_threshold",
        ],
        (
            {
                "fixture_id": fixture_id,
                "expected_rejected": str(expected_rejected).lower(),
                "expected_state_id": expected_state,
                "reference_rejected": str(record["rejected"]).lower(),
                "reference_nearest_state_id": record["nearest_state_id"],
                "mapping_probability": record["mapping_probability"],
                "median_neighbor_distance": record["median_neighbor_distance"],
                "distance_threshold": record["distance_threshold"],
            }
            for (fixture_id, expected_rejected, expected_state), record in zip(
                planted_truth, planted_mapped
            )
        ),
    )

    print("REF-03-C: generating marker-evidence candidate boundaries", flush=True)
    candidate_path = output_dir / "candidate_annotation_truth.tsv"
    candidate_rows = _candidate_rows(control, labels, state_ids)
    _write_tsv(
        candidate_path,
        [
            "record_id", "record_source", "technical_state_id",
            "candidate_labels", "label_status", "evidence_genes",
            "top_marker_score", "strong_claim_allowed",
            "technical_id_overwritten",
        ],
        candidate_rows,
    )

    output_paths = (
        reference_dir,
        stability_path,
        mapping_path,
        rejection_path,
        candidate_path,
    )
    outputs = {path.name: _path_sha256(path) for path in output_paths}
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "reference_pack_id": "REF-03",
        "completed_jobs": ["REF-03-A", "REF-03-B", "REF-03-C"],
        "pending_jobs": [],
        "readiness": "generated",
        "independent_of_pertura_results": True,
        "input_files": {
            "datasets.json": _sha256(datasets_path),
            "splits.json": _sha256(splits_path),
            "papalexi_artifact": _sha256(artifact),
        },
        "generator_script_sha256": _sha256(Path(__file__).resolve()),
        "output_files": outputs,
        "parameters": {
            "seed": SEED,
            "seeds": list(SEEDS),
            "resolutions": list(RESOLUTIONS),
            "n_top_genes": min(2000, int(control.n_vars)),
            "n_pcs": n_pcs,
            "n_neighbors": n_neighbors,
            "mapping_probability_threshold": 0.60,
            "distance_rejection_quantile": 0.99,
            "max_memory_gb": max_memory_gb,
            "marker_sets": {key: list(value) for key, value in MARKER_SETS.items()},
        },
        "counts": {
            "calibration_controls": len(control_ids),
            "technical_states": len(state_ids),
            "evaluation_mappings": len(mapped),
            "rejected_evaluation_cells": sum(record["rejected"] for record in mapped),
            "planted_rejection_cases": len(planted_truth),
            "candidate_annotation_rows": len(candidate_rows),
            "strong_claim_count": 0,
        },
        "environment": {
            "anndata": ad.__version__,
            "scanpy": sc.__version__,
            "scikit_learn": sklearn.__version__,
            "igraph": ig.__version__,
            "leidenalg": getattr(leidenalg, "__version__", "not_exposed"),
        },
        "limitations": [
            "Real-data state labels are technical clusters; marker-derived labels remain candidates.",
            "Distance rejection is an independent reference criterion and may expose missing open-set behavior.",
            "Norman is represented in the protocol boundary but is not used to fit the Papalexi reference.",
        ],
    }
    manifest_path = output_dir / "manifest.json"
    _write_json(manifest_path, manifest)
    print("REF-03: manifest written", flush=True)
    return {
        "reference_pack_id": "REF-03",
        "readiness": "generated",
        "completed_jobs": manifest["completed_jobs"],
        "pending_jobs": [],
        "dataset_count": 2,
        "counts": manifest["counts"],
        "manifest_sha256": _sha256(manifest_path),
        "problems": [],
        "passed": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate independent REF-03 control-state and mapping references."
    )
    parser.add_argument("--datasets", type=Path, required=True)
    parser.add_argument("--splits", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-memory-gb", type=float, default=4.0)
    args = parser.parse_args()
    if args.max_memory_gb <= 0:
        parser.error("--max-memory-gb must be positive")
    result = generate(
        args.datasets.resolve(),
        args.splits.resolve(),
        args.output.resolve(),
        max_memory_gb=args.max_memory_gb,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
