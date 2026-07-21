from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import math
import os
import subprocess
from pathlib import Path
from typing import Any, Iterable


DATASET_ID = "papalexi_thp1_eccite"
SEED = 1729
MIN_PSEUDOBULK_CELLS = 25
MIN_E_CELLS = 50
MAX_E_CELLS = 100
E_REPLICATES = ("rep2", "rep3")
N_PERMUTATIONS = 1000


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return "sha256:" + digest.hexdigest()


def _resolve_dataset_artifact(record: dict[str, Any]) -> Path:
    declared = Path(str(record["artifact_path"])).expanduser()
    artifact = declared
    if not artifact.is_file():
        cache_root = os.environ.get("PERTURA_BENCH_CACHE")
        if cache_root:
            candidate = (
                Path(cache_root)
                / "datasets"
                / DATASET_ID
                / "converted"
                / "artifact.h5ad"
            )
            if candidate.is_file():
                artifact = candidate
    if not artifact.is_file():
        raise FileNotFoundError(
            f"Papalexi artifact is unavailable at the declared path {declared} "
            "and no bound cache artifact was found"
        )
    expected = record.get("artifact_sha256")
    observed = _sha256(artifact)
    if expected and observed != expected:
        raise ValueError(
            "Papalexi artifact hash drift: "
            f"expected {expected}, observed {observed} at {artifact}"
        )
    return artifact.resolve()


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _write_tsv(
    path: Path, fieldnames: list[str], rows: Iterable[dict[str, Any]]
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=fieldnames,
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
    ids = [row["cell_id"] for row in rows]
    if len(ids) != len(set(ids)):
        raise ValueError(f"duplicate Papalexi {split} cell identity")
    return rows


def _require_disjoint_splits(
    calibration: list[dict[str, str]], evaluation: list[dict[str, str]]
) -> None:
    overlap = {row["cell_id"] for row in evaluation} & {
        row["cell_id"] for row in calibration
    }
    if overlap:
        raise ValueError(
            f"Papalexi calibration/evaluation cell leakage: {len(overlap)}"
        )


def _apply_ref02_retention(
    rows: list[dict[str, str]], ref02_root: Path
) -> tuple[list[dict[str, str]], dict[str, Any]]:
    manifest_path = ref02_root / "manifest.json"
    retained_path = ref02_root / "retained_cell_truth.tsv"
    if not manifest_path.is_file() or not retained_path.is_file():
        raise FileNotFoundError("REF-02 retained-cell truth is incomplete")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (
        manifest.get("reference_pack_id") != "REF-02"
        or manifest.get("readiness") != "generated"
        or manifest.get("pending_jobs")
    ):
        raise ValueError("REF-02 is not frozen and complete")
    expected = (manifest.get("output_files") or {}).get(
        "retained_cell_truth.tsv"
    )
    if not expected or _sha256(retained_path) != expected:
        raise ValueError("REF-02 retained-cell truth hash drift")
    with retained_path.open("r", encoding="utf-8", newline="") as handle:
        truth = [
            dict(record)
            for record in csv.DictReader(handle, delimiter="\t")
            if record.get("dataset_id") == DATASET_ID
            and record.get("split") == "evaluation"
        ]
    truth_by_cell = {str(record["cell_id"]): record for record in truth}
    if len(truth_by_cell) != len(truth):
        raise ValueError("REF-02 Papalexi evaluation truth has duplicate cells")
    selected_ids = {str(record["cell_id"]) for record in rows}
    if selected_ids != set(truth_by_cell):
        raise ValueError("REF-02 retained truth and evaluation split cell sets differ")
    retained = [
        record
        for record in rows
        if str(truth_by_cell[str(record["cell_id"])]["expected_state"])
        .strip()
        .lower()
        .startswith("retain_")
    ]
    if not retained:
        raise ValueError("REF-02 retained-cell policy selected zero evaluation cells")
    retained_controls = sum(
        str(record.get("is_control", "false")).strip().lower() == "true"
        for record in retained
    )
    if retained_controls == 0:
        raise ValueError("REF-02 retained-cell policy selected zero controls")
    states: dict[str, int] = {}
    for record in truth:
        state = str(record["expected_state"])
        states[state] = states.get(state, 0) + 1
    return retained, {
        "policy": "REF-02 expected_state starts with retain_",
        "selected_cell_count": len(rows),
        "retained_cell_count": len(retained),
        "excluded_cell_count": len(rows) - len(retained),
        "retained_control_count": retained_controls,
        "expected_state_counts": dict(sorted(states.items())),
        "manifest_path": manifest_path,
        "retained_truth_path": retained_path,
    }


def _selected_raw(source: Any, rows: list[dict[str, str]], max_memory_gb: float):
    import numpy as np
    from scipy import sparse

    selection = {str(row["cell_id"]): row for row in rows}
    source_names = source.obs_names.astype(str)
    selected = np.flatnonzero(source_names.isin(selection))
    missing = set(selection) - set(source_names[selected])
    if missing:
        raise ValueError(f"Papalexi artifact lacks {len(missing)} evaluation cells")
    estimated = len(selected) * int(source.n_vars) * 8
    if estimated > max_memory_gb * 1024**3 and not sparse.issparse(source.X):
        raise MemoryError(
            f"dense selected count matrix upper bound is {estimated / 1024**3:.3f} GB"
        )
    pieces = []
    chunk_rows = 1024
    for start in range(0, int(source.n_obs), chunk_rows):
        stop = min(start + chunk_rows, int(source.n_obs))
        in_chunk = selected[(selected >= start) & (selected < stop)]
        if not len(in_chunk):
            continue
        block = source.X[start:stop, :]
        if hasattr(block, "to_memory"):
            block = block.to_memory()
        block = block[in_chunk - start, :]
        pieces.append(block.tocsr() if sparse.issparse(block) else np.asarray(block))
    matrix = (
        sparse.vstack(pieces, format="csr")
        if pieces and sparse.issparse(pieces[0])
        else np.concatenate(pieces, axis=0)
    )
    values = matrix.data if sparse.issparse(matrix) else matrix.reshape(-1)
    if values.size and (
        not np.isfinite(values).all()
        or (values < 0).any()
        or not np.allclose(values, np.round(values), atol=1e-7)
    ):
        raise ValueError("PAPA task references require nonnegative integer raw counts")
    obs = source.obs.iloc[selected].copy()
    obs.index = source_names[selected]
    selection_rows = [selection[cell] for cell in obs.index.astype(str)]
    return matrix, obs, selection_rows


def _cell_records(obs: Any, selection_rows: list[dict[str, str]]):
    required = {"gene", "replicate"}
    if required - set(obs.columns):
        raise ValueError(f"Papalexi obs lacks {sorted(required - set(obs.columns))}")
    records = []
    for cell, target, replicate, split_row in zip(
        obs.index.astype(str),
        obs["gene"].astype(str),
        obs["replicate"].astype(str),
        selection_rows,
    ):
        is_control = str(split_row.get("is_control", "false")).lower() == "true"
        records.append(
            {
                "cell_id": cell,
                "target_uid": "NTC" if is_control else target,
                "replicate": replicate,
                "is_control": is_control,
            }
        )
    return records


def _generate_pseudobulk(
    matrix: Any,
    genes: list[str],
    records: list[dict[str, Any]],
    output: Path,
) -> dict[str, Any]:
    import numpy as np
    from scipy import sparse
    from scipy.io import mmwrite

    counts: dict[tuple[str, str], list[int]] = {}
    for index, record in enumerate(records):
        key = (str(record["target_uid"]), str(record["replicate"]))
        counts.setdefault(key, []).append(index)
    targets = sorted({target for target, _ in counts if target != "NTC"})
    eligibility = []
    eligible_targets = []
    for target in targets:
        paired = sorted(
            replicate
            for candidate, replicate in counts
            if candidate == target
            and len(counts[(target, replicate)]) >= MIN_PSEUDOBULK_CELLS
            and len(counts.get(("NTC", replicate), ())) >= MIN_PSEUDOBULK_CELLS
        )
        eligible = len(paired) >= 2
        if eligible:
            eligible_targets.append(target)
        eligibility.append(
            {
                "target_uid": target,
                "eligible": str(eligible).lower(),
                "paired_replicates": ",".join(paired),
                "paired_replicate_count": len(paired),
                "minimum_cells_per_arm": MIN_PSEUDOBULK_CELLS,
            }
        )
    if not eligible_targets:
        raise ValueError("no Papalexi targets satisfy neutral pseudobulk eligibility")
    sample_rows = []
    vectors = []
    included_controls = sorted(
        {
            replicate
            for target in eligible_targets
            for replicate in next(
                row["paired_replicates"].split(",")
                for row in eligibility
                if row["target_uid"] == target
            )
        }
    )
    groups = [("NTC", replicate) for replicate in included_controls]
    for target in eligible_targets:
        paired = next(
            row["paired_replicates"].split(",")
            for row in eligibility
            if row["target_uid"] == target
        )
        groups.extend((target, replicate) for replicate in paired)
    for target, replicate in groups:
        indices = counts[(target, replicate)]
        vector = matrix[indices, :].sum(axis=0)
        vector = np.asarray(vector).reshape(-1).astype(np.int64)
        vectors.append(vector)
        sample_rows.append(
            {
                "sample_id": f"{target}::{replicate}",
                "target_uid": target,
                "replicate": replicate,
                "condition": "control" if target == "NTC" else "target",
                "n_cells": len(indices),
                "is_control": str(target == "NTC").lower(),
            }
        )
    counts_matrix = sparse.csr_matrix(np.column_stack(vectors))
    neutral = output / "PAPA-06" / "neutral_inputs"
    neutral.mkdir(parents=True, exist_ok=True)
    matrix_path = neutral / "pseudobulk_counts.mtx"
    genes_path = neutral / "genes.tsv"
    samples_path = neutral / "sample_manifest.tsv"
    eligibility_path = neutral / "target_eligibility.tsv"
    mmwrite(matrix_path, counts_matrix)
    _write_tsv(genes_path, ["gene"], ({"gene": gene} for gene in genes))
    _write_tsv(
        samples_path,
        ["sample_id", "target_uid", "replicate", "condition", "n_cells", "is_control"],
        sample_rows,
    )
    _write_tsv(
        eligibility_path,
        ["target_uid", "eligible", "paired_replicates", "paired_replicate_count", "minimum_cells_per_arm"],
        eligibility,
    )
    return {
        "matrix": matrix_path,
        "genes": genes_path,
        "samples": samples_path,
        "eligibility": eligibility_path,
        "eligible_targets": eligible_targets,
        "sample_count": len(sample_rows),
    }


def _project_evaluation(
    matrix: Any,
    genes: list[str],
    model_path: Path,
    max_memory_gb: float,
):
    import numpy as np
    from scipy import sparse

    model = np.load(model_path, allow_pickle=False)
    hvg = [str(value) for value in model["hvg_names"]]
    gene_index = {gene: index for index, gene in enumerate(genes)}
    missing = [gene for gene in hvg if gene not in gene_index]
    if missing:
        raise ValueError(f"REF-03 model HVGs are missing: {len(missing)}")
    library = np.asarray(matrix.sum(axis=1)).reshape(-1)
    if (library <= 0).any():
        raise ValueError("evaluation cells with zero RNA library are unsupported")
    selected = matrix[:, [gene_index[gene] for gene in hvg]]
    if sparse.issparse(selected):
        selected = selected.multiply((10000.0 / library)[:, None]).toarray()
    else:
        selected = selected * (10000.0 / library)[:, None]
    if selected.nbytes * 2 > max_memory_gb * 1024**3:
        raise MemoryError("evaluation PCA projection exceeds memory budget")
    normalized = np.log1p(selected)
    components = np.asarray(model["pca_components"], dtype=float)[:15]
    mean = np.asarray(model["pca_mean"], dtype=float)
    return (normalized - mean) @ components.T


def _energy_statistics(distance: Any, masks: Any, n: int):
    import numpy as np

    membership = masks.astype(float)
    complement = 1.0 - membership
    md = membership @ distance
    cd = complement @ distance
    cross = (md * complement).sum(axis=1) / (n * n)
    within_left = (md * membership).sum(axis=1) / (n * (n - 1))
    within_right = (cd * complement).sum(axis=1) / (n * (n - 1))
    return 2.0 * cross - within_left - within_right


def _target_energy(
    pcs: Any,
    records: list[dict[str, Any]],
    target: str,
    *,
    n: int,
    rng: Any,
):
    import numpy as np
    from scipy.spatial.distance import cdist

    observed = []
    permuted = []
    for replicate in E_REPLICATES:
        target_indices = [
            index
            for index, row in enumerate(records)
            if row["target_uid"] == target and row["replicate"] == replicate
        ]
        control_indices = [
            index
            for index, row in enumerate(records)
            if row["is_control"] and row["replicate"] == replicate
        ]
        target_indices = rng.choice(target_indices, size=n, replace=False)
        control_indices = rng.choice(control_indices, size=n, replace=False)
        combined = pcs[np.concatenate([target_indices, control_indices])]
        distance = cdist(combined, combined, metric="euclidean")
        observed_mask = np.zeros((1, 2 * n), dtype=bool)
        observed_mask[0, :n] = True
        observed.append(float(_energy_statistics(distance, observed_mask, n)[0]))
        masks = np.zeros((N_PERMUTATIONS, 2 * n), dtype=bool)
        for permutation in range(N_PERMUTATIONS):
            masks[permutation, rng.permutation(2 * n)[:n]] = True
        permuted.append(_energy_statistics(distance, masks, n))
    observed_aggregate = float(np.mean(observed))
    null = np.mean(np.vstack(permuted), axis=0)
    pvalue = (1 + int((null >= observed_aggregate).sum())) / (N_PERMUTATIONS + 1)
    return observed, observed_aggregate, pvalue


def _bh(pvalues: list[float]) -> list[float]:
    order = sorted(range(len(pvalues)), key=pvalues.__getitem__)
    adjusted = [1.0] * len(pvalues)
    running = 1.0
    for rank_index in range(len(order) - 1, -1, -1):
        index = order[rank_index]
        rank = rank_index + 1
        running = min(running, pvalues[index] * len(pvalues) / rank)
        adjusted[index] = min(running, 1.0)
    return adjusted


def _generate_global_effect(
    pcs: Any,
    records: list[dict[str, Any]],
    output: Path,
) -> dict[str, Any]:
    import numpy as np

    arm_counts = {}
    for target in sorted({row["target_uid"] for row in records if not row["is_control"]}):
        arm_counts[target] = {
            replicate: sum(
                row["target_uid"] == target and row["replicate"] == replicate
                for row in records
            )
            for replicate in E_REPLICATES
        }
    control_counts = {
        replicate: sum(
            row["is_control"] and row["replicate"] == replicate for row in records
        )
        for replicate in E_REPLICATES
    }
    eligible = [
        target
        for target, counts in arm_counts.items()
        if all(counts[replicate] >= MIN_E_CELLS for replicate in E_REPLICATES)
        and all(control_counts[replicate] >= MIN_E_CELLS for replicate in E_REPLICATES)
    ]
    if not eligible:
        raise ValueError("no Papalexi targets satisfy E-distance coverage")
    common_n = min(
        MAX_E_CELLS,
        *(arm_counts[target][replicate] for target in eligible for replicate in E_REPLICATES),
        *(control_counts[replicate] for replicate in E_REPLICATES),
    )
    if common_n < MIN_E_CELLS:
        raise ValueError("global E-distance common sample size is below 50")
    rng = np.random.default_rng(SEED)
    rows = []
    pvalues = []
    for target in eligible:
        replicate_stats, aggregate, pvalue = _target_energy(
            pcs, records, target, n=common_n, rng=rng
        )
        pvalues.append(pvalue)
        rows.append(
            {
                "target_uid": target,
                "rep2_e_distance": f"{replicate_stats[0]:.12g}",
                "rep3_e_distance": f"{replicate_stats[1]:.12g}",
                "E_distance": f"{aggregate:.12g}",
                "PValue": f"{pvalue:.12g}",
                "FDR": "",
                "cells_per_arm": common_n,
                "replicates": ",".join(E_REPLICATES),
            }
        )
    for row, fdr in zip(rows, _bh(pvalues)):
        row["FDR"] = f"{fdr:.12g}"
    root = output / "PAPA-07"
    evidence = root / "global_effect_evidence.tsv"
    protocol = root / "global_effect_protocol.json"
    _write_tsv(
        evidence,
        ["target_uid", "rep2_e_distance", "rep3_e_distance", "E_distance", "PValue", "FDR", "cells_per_arm", "replicates"],
        rows,
    )
    _write_json(
        protocol,
        {
            "schema_version": "pertura-paper-global-effect-protocol-v1",
            "representation": "REF-03 frozen PCA",
            "dimensions": 15,
            "split": "evaluation",
            "replicates": list(E_REPLICATES),
            "sampling": "equal target/control arm size within replicate",
            "common_cells_per_arm": common_n,
            "minimum_cells_per_arm": MIN_E_CELLS,
            "maximum_cells_per_arm": MAX_E_CELLS,
            "permutations": N_PERMUTATIONS,
            "permutation_unit": "within_replicate_label",
            "multiple_testing": "BH across eligible targets",
            "seed": SEED,
            "claim_class_withheld_from_agent": True,
        },
    )
    return {"evidence": evidence, "protocol": protocol, "target_count": len(rows)}


def generate(
    datasets_path: Path,
    splits_path: Path,
    ref02_root: Path,
    ref03_root: Path,
    rscript: Path,
    r_runner: Path,
    output: Path,
    *,
    max_memory_gb: float,
) -> dict[str, Any]:
    import anndata as ad

    datasets = json.loads(datasets_path.read_text(encoding="utf-8"))
    artifact = _resolve_dataset_artifact(datasets["datasets"][DATASET_ID])
    selection = _selection_rows(splits_path, "evaluation")
    calibration = _selection_rows(splits_path, "calibration")
    _require_disjoint_splits(calibration, selection)
    selection, retention = _apply_ref02_retention(selection, ref02_root)
    source = ad.read_h5ad(artifact, backed="r")
    try:
        matrix, obs, selected_rows = _selected_raw(source, selection, max_memory_gb)
        genes = [str(value) for value in source.var_names]
    finally:
        if getattr(source, "file", None) is not None:
            source.file.close()
    records = _cell_records(obs, selected_rows)
    p06 = _generate_pseudobulk(matrix, genes, records, output)
    reference_root = output / "PAPA-06" / "reference"
    reference_root.mkdir(parents=True, exist_ok=True)
    command = [
        str(rscript), "--vanilla", str(r_runner),
        str(p06["matrix"]), str(p06["genes"]), str(p06["samples"]),
        str(p06["eligibility"]), str(reference_root),
    ]
    environment = dict(os.environ)
    environment.update(
        {"OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1", "MKL_NUM_THREADS": "1"}
    )
    completed = subprocess.run(command, text=True, env=environment, check=False)
    if completed.returncode:
        raise RuntimeError(f"PAPA-06 edgeR reference failed: {completed.returncode}")
    trans_de_reference = reference_root / "trans_de_reference.tsv"
    design_reference = reference_root / "design_matrices.tsv"
    design_manifest = reference_root / "reference_design_manifest.json"
    if not trans_de_reference.is_file() or not design_reference.is_file() or not design_manifest.is_file():
        raise FileNotFoundError("PAPA-06 R reference outputs are incomplete")
    design_provenance = json.loads(design_manifest.read_text(encoding="utf-8"))
    expected_protocol = {
        "design": "~ replicate + condition",
        "baseline": "NTC",
        "minimum_paired_replicates": 2,
        "gene_filter": "edgeR::filterByExpr(y, design)",
        "normalization": "edgeR::calcNormFactors",
        "fit": "edgeR quasi-likelihood with robust=TRUE",
    }
    for field, expected_value in expected_protocol.items():
        if design_provenance.get(field) != expected_value:
            raise RuntimeError(f"PAPA-06 reference protocol drift: {field}")
    reference_versions = design_provenance.get("versions") or {}
    if any(not reference_versions.get(name) for name in ("R", "edgeR", "Matrix")):
        raise RuntimeError("PAPA-06 reference environment provenance is incomplete")

    model_path = ref03_root / "control_state_reference" / "model.npz"
    if not model_path.is_file():
        raise FileNotFoundError(model_path)
    pcs = _project_evaluation(matrix, genes, model_path, max_memory_gb)
    p07 = _generate_global_effect(pcs, records, output)
    files = [
        p06["matrix"], p06["genes"], p06["samples"], p06["eligibility"],
        trans_de_reference, design_reference, design_manifest, p07["evidence"], p07["protocol"],
    ]
    manifest = {
        "schema_version": "pertura-paper-task-reference-pack-v1",
        "task_reference_sets": ["TASKREF-PAPA-TRANS-DE", "TASKREF-PAPA-E-DISTANCE"],
        "dataset_id": DATASET_ID,
        "split": "evaluation",
        "independent_of_pertura_results": True,
        "input_files": {
            "datasets.json": _sha256(datasets_path), "splits.json": _sha256(splits_path),
            "papalexi_artifact": _sha256(artifact),
            "ref02_manifest": _sha256(retention["manifest_path"]),
            "ref02_retained_cell_truth": _sha256(retention["retained_truth_path"]),
            "ref03_model": _sha256(model_path),
            "generator_script": _sha256(Path(__file__).resolve()),
            "r_runner": _sha256(r_runner),
        },
        "environment": {
            "profile": "edger-v1",
            "versions": reference_versions,
        },
        "output_files": {
            path.relative_to(output).as_posix(): _sha256(path) for path in files
        },
        "counts": {
            "evaluation_selected_cells": retention["selected_cell_count"],
            "evaluation_retained_cells": len(records),
            "evaluation_excluded_cells": retention["excluded_cell_count"],
            "retained_controls": retention["retained_control_count"],
            "genes": len(genes),
            "trans_de_eligible_targets": len(p06["eligible_targets"]),
            "pseudobulk_samples": p06["sample_count"],
            "global_effect_eligible_targets": p07["target_count"],
        },
        "parameters": {
            "retained_cell_policy": retention["policy"],
            "retained_expected_state_counts": retention["expected_state_counts"],
            "trans_de": {
                **expected_protocol,
                "robust": True,
                "minimum_cells_per_arm": MIN_PSEUDOBULK_CELLS,
                "full_gene_untested_encoding": {
                    "tested": False,
                    "logFC": 0,
                    "PValue": 1,
                    "FDR": 1,
                },
            },
            "global_effect": {"dimensions": 15, "replicates": list(E_REPLICATES), "maximum_cells_per_arm": MAX_E_CELLS, "minimum_cells_per_arm": MIN_E_CELLS, "permutations": N_PERMUTATIONS, "seed": SEED, "multiple_testing": "BH"},
        },
        "readiness": "generated", "pending_jobs": [], "problems": [], "passed": True,
    }
    _write_json(output / "manifest.json", manifest)
    return manifest


def validate_task_reference_pack(root: Path) -> dict[str, Any]:
    root = Path(root).resolve()
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    problems: list[str] = []
    if manifest.get("schema_version") != "pertura-paper-task-reference-pack-v1":
        problems.append("unsupported task-reference pack schema")
    if manifest.get("readiness") != "generated" or manifest.get("pending_jobs"):
        problems.append("task-reference pack is not complete")
    if manifest.get("passed") is not True or manifest.get("problems"):
        problems.append("task-reference pack did not pass generation checks")
    counts = manifest.get("counts") or {}
    selected = counts.get("evaluation_selected_cells")
    retained = counts.get("evaluation_retained_cells")
    excluded = counts.get("evaluation_excluded_cells")
    controls = counts.get("retained_controls")
    if (
        not isinstance(selected, int)
        or not isinstance(retained, int)
        or not isinstance(excluded, int)
        or retained <= 0
        or excluded < 0
        or selected != retained + excluded
        or not isinstance(controls, int)
        or controls <= 0
    ):
        problems.append("retained-cell accounting is invalid")
    if (manifest.get("parameters") or {}).get("retained_cell_policy") != (
        "REF-02 expected_state starts with retain_"
    ):
        problems.append("retained-cell policy drift")
    for name in ("ref02_manifest", "ref02_retained_cell_truth"):
        value = str((manifest.get("input_files") or {}).get(name) or "")
        if not value.startswith("sha256:") or len(value) != 71:
            problems.append(f"retained-cell input hash is missing: {name}")
    for relative, expected in (manifest.get("output_files") or {}).items():
        path = root / relative
        if not path.is_file() or _sha256(path) != expected:
            problems.append(f"output hash drift: {relative}")
    if "TASKREF-PAPA-TRANS-DE" in (manifest.get("task_reference_sets") or []):
        input_files = manifest.get("input_files") or {}
        expected_generator_hashes = {
            "generator_script": _sha256(Path(__file__).resolve()),
            "r_runner": _sha256(
                Path(__file__).with_name("generate_paper_task_trans_de.R")
            ),
        }
        for name, expected_hash in expected_generator_hashes.items():
            if input_files.get(name) != expected_hash:
                problems.append(f"PAPA-06 generator hash drift: {name}")
        trans_de_protocol = (manifest.get("parameters") or {}).get("trans_de") or {}
        expected_trans_de_protocol = {
            "design": "~ replicate + condition",
            "baseline": "NTC",
            "minimum_paired_replicates": 2,
            "gene_filter": "edgeR::filterByExpr(y, design)",
            "normalization": "edgeR::calcNormFactors",
            "fit": "edgeR quasi-likelihood with robust=TRUE",
            "robust": True,
        }
        for field, expected_value in expected_trans_de_protocol.items():
            if trans_de_protocol.get(field) != expected_value:
                problems.append(f"PAPA-06 reference protocol drift: {field}")
        reference_versions = ((manifest.get("environment") or {}).get("versions") or {})
        if any(not reference_versions.get(name) for name in ("R", "edgeR", "Matrix")):
            problems.append("PAPA-06 reference environment provenance is incomplete")
        design_manifest_path = root / "PAPA-06/reference/reference_design_manifest.json"
        if design_manifest_path.is_file():
            design_provenance = json.loads(
                design_manifest_path.read_text(encoding="utf-8")
            )
            if (design_provenance.get("versions") or {}) != reference_versions:
                problems.append("PAPA-06 reference environment provenance disagrees")
        else:
            problems.append("PAPA-06 reference design manifest is missing")
    protocol_path = root / "PAPA-07" / "global_effect_protocol.json"
    evidence_path = root / "PAPA-07" / "global_effect_evidence.tsv"
    if protocol_path.is_file():
        protocol = json.loads(protocol_path.read_text(encoding="utf-8"))
        expected_protocol = {
            "dimensions": 15,
            "replicates": list(E_REPLICATES),
            "permutations": N_PERMUTATIONS,
            "permutation_unit": "within_replicate_label",
            "multiple_testing": "BH across eligible targets",
            "seed": SEED,
            "claim_class_withheld_from_agent": True,
        }
        for field, expected in expected_protocol.items():
            if protocol.get(field) != expected:
                problems.append(f"global-effect protocol drift: {field}")
    else:
        problems.append("global-effect protocol is missing")
    if evidence_path.is_file():
        with evidence_path.open("r", encoding="utf-8", newline="") as handle:
            evidence = list(csv.DictReader(handle, delimiter="\t"))
        if not evidence:
            problems.append("global-effect evidence is empty")
        if evidence and "claim_class" in evidence[0]:
            problems.append("agent-visible global-effect evidence leaks claim class")
        arm_sizes = {row.get("cells_per_arm") for row in evidence}
        if len(arm_sizes) != 1:
            problems.append("global-effect evidence does not use one common arm size")
        if any(row.get("replicates") != ",".join(E_REPLICATES) for row in evidence):
            problems.append("global-effect evidence replicate set drift")
    else:
        problems.append("global-effect evidence is missing")
    return {
        "schema_version": "pertura-paper-task-reference-pack-validation-v1",
        "passed": not problems,
        "problems": problems,
        "manifest_sha256": _sha256(manifest_path) if manifest_path.is_file() else None,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--datasets", type=Path, required=True)
    parser.add_argument("--splits", type=Path, required=True)
    parser.add_argument("--ref02-root", type=Path, required=True)
    parser.add_argument("--ref03-root", type=Path, required=True)
    parser.add_argument("--rscript", type=Path, required=True)
    parser.add_argument(
        "--r-runner", type=Path,
        default=Path(__file__).with_name("generate_paper_task_trans_de.R"),
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-memory-gb", type=float, default=8.0)
    args = parser.parse_args(argv)
    manifest = generate(
        args.datasets.resolve(), args.splits.resolve(), args.ref02_root.resolve(),
        args.ref03_root.resolve(),
        args.rscript.resolve(), args.r_runner.resolve(), args.output.resolve(),
        max_memory_gb=args.max_memory_gb,
    )
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
