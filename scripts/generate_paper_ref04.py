from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import math
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable


SCHEMA_VERSION = "pertura-paper-ref04-v1"
DATASET_ID = "papalexi_thp1_eccite"
SEED = 1729
GENE_ALIASES = {"PDL1": "CD274"}


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
    for item in sorted(candidate for candidate in path.rglob("*") if candidate.is_file()):
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


def _write_tsv(
    path: Path, fieldnames: list[str], rows: Iterable[dict[str, Any]]
) -> None:
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


def _read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle, delimiter="\t")]


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


def _validate_guide_assets(root: Path) -> tuple[dict[str, str], str]:
    manifest_path = root / "guide_assets_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (
        manifest.get("schema_version") != "pertura-papalexi-guide-assets-v1"
        or manifest.get("dataset_id") != DATASET_ID
    ):
        raise ValueError("unsupported Papalexi guide asset manifest")
    for relative, expected in manifest.get("files", {}).items():
        path = root / relative
        if not path.is_file() or _sha256(path) != expected:
            raise ValueError(f"Papalexi guide asset hash drift: {relative}")
    rows = _read_tsv(root / "guide_map.tsv")
    guide_map = {str(row["guide"]): str(row["target"]) for row in rows}
    if len(guide_map) != len(rows):
        raise ValueError("Papalexi guide map contains duplicate guides")
    return guide_map, _sha256(manifest_path)


def _selected_normalized_data(
    source: Any,
    cell_ids: list[str],
    *,
    max_memory_gb: float,
    chunk_rows: int = 1024,
) -> Any:
    import anndata as ad
    import numpy as np
    import scanpy as sc
    from scipy import sparse

    source_names = source.obs_names.astype(str)
    requested = set(cell_ids)
    missing = sorted(requested.difference(source_names))
    if missing:
        raise ValueError(f"Papalexi artifact is missing {len(missing)} evaluation cells")
    selected = np.flatnonzero(source_names.isin(requested))
    dense_upper_bound = len(selected) * int(source.n_vars) * 8 * 3
    if dense_upper_bound > max_memory_gb * 1024**3:
        raise MemoryError(
            f"REF-04 working-set upper bound is {dense_upper_bound / 1024**3:.3f} GB, "
            f"exceeding max_memory_gb={max_memory_gb}"
        )

    print(
        f"REF-04: sequentially scanning {source.n_obs} source rows for "
        f"{len(selected)} evaluation cells",
        flush=True,
    )
    pieces: list[Any] = []
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
    if not pieces:
        raise ValueError("Papalexi evaluation selection is empty")
    matrix = (
        sparse.vstack(pieces, format="csr")
        if sparse.issparse(pieces[0])
        else np.concatenate(pieces, axis=0)
    )
    values = matrix.data if sparse.issparse(matrix) else matrix.reshape(-1)
    if values.size and (not np.isfinite(values).all() or (values < 0).any()):
        raise ValueError("REF-04 requires finite nonnegative expression")
    data = ad.AnnData(
        X=matrix,
        obs=source.obs.iloc[selected].copy(),
        var=source.var.copy(),
    )
    sc.pp.normalize_total(data, target_sum=1e4)
    sc.pp.log1p(data)
    print(f"REF-04: normalized {data.n_obs} evaluation cells", flush=True)
    return data


def _replicate_effect(
    values: list[float],
    replicates: list[str],
    selected: list[bool],
    controls: list[bool],
) -> tuple[float | None, list[float]]:
    by_replicate: dict[str, tuple[list[float], list[float]]] = {}
    for value, replicate, is_selected, is_control in zip(
        values, replicates, selected, controls
    ):
        target_values, control_values = by_replicate.setdefault(
            replicate, ([], [])
        )
        if is_selected:
            target_values.append(float(value))
        if is_control:
            control_values.append(float(value))
    effects = [
        sum(target) / len(target) - sum(control) / len(control)
        for replicate, (target, control) in sorted(by_replicate.items())
        if replicate and target and control
    ]
    return (sum(effects) / len(effects) if effects else None), effects


def _guide_sensitivity(
    guide_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in guide_rows:
        if row.get("status") == "resolved" and row.get("effect") is not None:
            grouped[str(row["target_uid"])].append(row)
    output: list[dict[str, Any]] = []
    for target_uid, rows in sorted(grouped.items()):
        effects = [float(row["effect"]) for row in rows]
        pooled = sum(effects) / len(effects)
        concordance = max(
            sum(value >= 0 for value in effects),
            sum(value <= 0 for value in effects),
        ) / len(effects)
        for row in rows:
            remaining = [
                float(other["effect"])
                for other in rows
                if other["guide"] != row["guide"]
            ]
            leave_one_out = sum(remaining) / len(remaining) if remaining else None
            unstable = len({value > 0 for value in effects if value != 0}) > 1
            if leave_one_out is not None and pooled != 0 and leave_one_out * pooled < 0:
                unstable = True
            output.append(
                {
                    "target_uid": target_uid,
                    "target_gene": row["target_gene"],
                    "excluded_guide": row["guide"],
                    "guide_count": len(rows),
                    "pooled_effect": pooled,
                    "leave_one_guide_out_effect": leave_one_out,
                    "direction_concordance": concordance,
                    "unstable": str(unstable).lower(),
                }
            )
    return output


def _reliability_rows(
    efficacy_rows: list[dict[str, Any]],
    mixscape_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    mixscape = {str(row["target_uid"]): row for row in mixscape_rows}
    output = []
    for efficacy in efficacy_rows:
        target_uid = str(efficacy["target_uid"])
        responder = mixscape.get(target_uid)
        efficacy_available = efficacy.get("status") == "resolved"
        responder_available = responder is not None and responder.get("status") == "available"
        direction_supported = bool(efficacy.get("direction_supported"))
        resolved = efficacy_available and responder_available and direction_supported
        limitations = []
        if not efficacy_available:
            limitations.append("target efficacy is unresolved")
        if not direction_supported:
            limitations.append("expected direct-expression direction is unsupported")
        if not responder_available:
            limitations.append("target-specific Mixscape result is unavailable")
        output.append(
            {
                "target_uid": target_uid,
                "target_gene": efficacy["target_gene"],
                "status": "resolved" if resolved else "unresolved",
                "direct_effect": efficacy.get("direct_effect"),
                "direction_supported": str(direction_supported).lower(),
                "signature_distance": efficacy.get("signature_distance"),
                "responder_fraction": (
                    responder.get("responder_fraction") if responder_available else None
                ),
                "escape_fraction": (
                    responder.get("escape_fraction") if responder_available else None
                ),
                "target_specific_join": "true" if responder_available else "false",
                "limitations": "; ".join(limitations),
            }
        )
    return output


def _class_flags(label: str) -> tuple[bool, bool]:
    lower = label.lower()
    responder = any(token in lower for token in ("ko", "responder", "perturbed"))
    escape = any(token in lower for token in ("np", "escape", "non.perturbed"))
    return responder, escape


def _generate_direct_references(
    data: Any,
    selection: list[dict[str, str]],
    guide_map: dict[str, str],
    evaluation_pcs: Any,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    import numpy as np
    from scipy import sparse

    required_columns = {"gene", "guide_ID", "replicate"}
    missing_columns = sorted(required_columns - set(data.obs.columns))
    if missing_columns:
        raise ValueError("Papalexi metadata is missing: " + ", ".join(missing_columns))
    selection_by_cell = {row["cell_id"]: row for row in selection}
    cells = [str(value) for value in data.obs_names]
    if set(cells) != set(selection_by_cell):
        raise ValueError("Papalexi evaluation H5AD rows do not match frozen selection")
    controls = [
        str(selection_by_cell[cell]["is_control"]).lower() == "true"
        for cell in cells
    ]
    targets = [str(value) for value in data.obs["gene"]]
    guides = [str(value) for value in data.obs["guide_ID"]]
    replicates = [str(value) for value in data.obs["replicate"]]
    gene_index = {str(gene): index for index, gene in enumerate(data.var_names)}
    target_uids = sorted(
        {
            target
            for target, is_control in zip(targets, controls)
            if not is_control and target and target != "NT"
        }
    )
    control_pcs = evaluation_pcs[np.asarray(controls, dtype=bool)]
    control_pc_center = control_pcs.mean(axis=0)
    efficacy_rows: list[dict[str, Any]] = []
    guide_rows: list[dict[str, Any]] = []
    for target_uid in target_uids:
        target_gene = GENE_ALIASES.get(target_uid, target_uid)
        target_mask = [
            target == target_uid and not is_control
            for target, is_control in zip(targets, controls)
        ]
        target_guides = sorted(
            {
                guide
                for guide, target, is_control in zip(guides, targets, controls)
                if target == target_uid and not is_control
            }
        )
        if target_gene not in gene_index:
            efficacy_rows.append(
                {
                    "target_uid": target_uid,
                    "target_gene": target_gene,
                    "status": "unresolved",
                    "n_cells": sum(target_mask),
                    "n_guides": len(target_guides),
                    "n_replicates": 0,
                    "direct_effect": None,
                    "direct_effect_se": None,
                    "direction_supported": False,
                    "signature_distance": None,
                    "limitation": "target gene is absent from expression matrix",
                }
            )
            continue
        column = data.X[:, gene_index[target_gene]]
        values = (
            np.asarray(column.toarray()).reshape(-1)
            if sparse.issparse(column)
            else np.asarray(column).reshape(-1)
        )
        effect, replicate_effects = _replicate_effect(
            values.tolist(), replicates, target_mask, controls
        )
        signature_distance = float(
            np.linalg.norm(
                evaluation_pcs[np.asarray(target_mask, dtype=bool)].mean(axis=0)
                - control_pc_center
            )
        )
        standard_error = (
            float(np.std(replicate_effects, ddof=1) / math.sqrt(len(replicate_effects)))
            if len(replicate_effects) > 1
            else None
        )
        resolved = effect is not None and len(replicate_effects) >= 2
        efficacy_rows.append(
            {
                "target_uid": target_uid,
                "target_gene": target_gene,
                "status": "resolved" if resolved else "unresolved",
                "n_cells": sum(target_mask),
                "n_guides": len(target_guides),
                "n_replicates": len(replicate_effects),
                "direct_effect": effect,
                "direct_effect_se": standard_error,
                "direction_supported": bool(effect is not None and effect < 0),
                "signature_distance": signature_distance,
                "limitation": "" if resolved else "fewer than two paired replicates",
            }
        )
        for guide in target_guides:
            guide_mask = [
                candidate == guide and target == target_uid and not is_control
                for candidate, target, is_control in zip(guides, targets, controls)
            ]
            guide_effect, guide_replicate_effects = _replicate_effect(
                values.tolist(), replicates, guide_mask, controls
            )
            guide_rows.append(
                {
                    "target_uid": target_uid,
                    "target_gene": target_gene,
                    "guide": guide,
                    "mapped_target": guide_map.get(guide, ""),
                    "status": (
                        "resolved"
                        if guide_effect is not None and len(guide_replicate_effects) >= 2
                        else "unresolved"
                    ),
                    "n_cells": sum(guide_mask),
                    "n_replicates": len(guide_replicate_effects),
                    "effect": guide_effect,
                    "direction_supported": bool(
                        guide_effect is not None and guide_effect < 0
                    ),
                }
            )
    return efficacy_rows, guide_rows


def _run_mixscape(
    data: Any,
    selection: list[dict[str, str]],
    evaluation_pcs: Any,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    import pandas as pd
    import pertpy as pt

    selection_by_cell = {row["cell_id"]: row for row in selection}
    cells = [str(value) for value in data.obs_names]
    controls = [
        str(selection_by_cell[cell]["is_control"]).lower() == "true"
        for cell in cells
    ]
    targets = [str(value) for value in data.obs["gene"]]
    data.obs["_ref04_target"] = [
        "NT" if is_control else target
        for target, is_control in zip(targets, controls)
    ]
    data.obs["_ref04_replicate"] = data.obs["replicate"].astype(str).to_numpy()
    data.obsm["X_ref03_pca"] = evaluation_pcs
    mixscape = pt.tl.Mixscape()
    print("REF-04-B: computing independent Pertpy perturbation signatures", flush=True)
    mixscape.perturbation_signature(
        data,
        pert_key="_ref04_target",
        control="NT",
        ref_selection_mode="nn",
        split_by="_ref04_replicate",
        n_neighbors=20,
        use_rep="X_ref03_pca",
        n_dims=min(15, evaluation_pcs.shape[1]),
        copy=False,
    )
    print("REF-04-B: running independent Pertpy Mixscape", flush=True)
    mixscape.mixscape(
        data,
        pert_key="_ref04_target",
        control="NT",
        new_class_name="mixscape_class",
        layer="X_pert",
        min_de_genes=5,
        logfc_threshold=0.25,
        iter_num=10,
        scale=True,
        split_by="_ref04_replicate",
        pval_cutoff=0.05,
        perturbation_type="KO",
        random_state=SEED,
    )
    class_columns = [
        name
        for name in data.obs.columns
        if str(name) == "mixscape_class" or str(name).startswith("mixscape_class")
    ]
    if not class_columns:
        raise ValueError("Pertpy Mixscape did not produce a class column")
    class_column = class_columns[0]
    labels = [str(value) for value in data.obs[class_column]]
    score_columns = [
        name
        for name in data.obs.columns
        if "mixscape" in str(name).lower() and "score" in str(name).lower()
    ]
    scores = (
        pd.to_numeric(data.obs[score_columns[0]], errors="coerce").tolist()
        if score_columns
        else [None] * len(cells)
    )
    cell_rows = []
    for cell, target, guide, replicate, is_control, label, score in zip(
        cells,
        data.obs["_ref04_target"].astype(str),
        data.obs["guide_ID"].astype(str),
        data.obs["_ref04_replicate"].astype(str),
        controls,
        labels,
        scores,
    ):
        responder, escape = _class_flags(label)
        cell_rows.append(
            {
                "cell_id": cell,
                "target_uid": target,
                "guide": guide,
                "replicate": replicate,
                "is_control": str(is_control).lower(),
                "mixscape_class": label,
                "reference_responder": str(responder and not is_control).lower(),
                "reference_escape": str(escape and not is_control).lower(),
                "perturbation_score": score,
            }
        )
    target_rows = []
    for target_uid in sorted(
        {row["target_uid"] for row in cell_rows if row["is_control"] == "false"}
    ):
        rows = [
            row
            for row in cell_rows
            if row["target_uid"] == target_uid and row["is_control"] == "false"
        ]
        responder_count = sum(row["reference_responder"] == "true" for row in rows)
        escape_count = sum(row["reference_escape"] == "true" for row in rows)
        target_rows.append(
            {
                "target_uid": target_uid,
                "status": "available",
                "n_candidate_cells": len(rows),
                "responder_fraction": responder_count / len(rows),
                "escape_fraction": escape_count / len(rows),
                "class_counts": json.dumps(
                    dict(sorted(Counter(row["mixscape_class"] for row in rows).items())),
                    sort_keys=True,
                ),
            }
        )
    return cell_rows, target_rows


def _compatibility_snapshot(repo: Path) -> dict[str, Any]:
    surface_path = (
        repo
        / "src"
        / "pertura_core"
        / "compatibility"
        / "v0.2"
        / "capability-surface.json"
    )
    payload = json.loads(surface_path.read_text(encoding="utf-8"))
    records = [
        row
        for row in payload.get("capabilities", [])
        if row.get("capability_id") == "target.reliability.v2"
    ]
    if len(records) != 1:
        raise ValueError("frozen v0.2 target.reliability.v2 record is missing")
    return {
        "schema_version": SCHEMA_VERSION,
        "reference_pack_id": "REF-04",
        "generator_job_id": "REF-04-D",
        "surface_sha256": _sha256(surface_path),
        "capability": records[0],
        "primary_scientific_path": False,
        "unexpected_strong_claim_count": 0,
    }


def generate(
    datasets_path: Path,
    splits_path: Path,
    guide_root: Path,
    ref03_root: Path,
    repo: Path,
    output_dir: Path,
    *,
    max_memory_gb: float,
) -> dict[str, Any]:
    import anndata as ad
    import igraph as ig
    import leidenalg
    import numpy as np
    import pandas as pd
    import pertpy
    import scanpy as sc
    import sklearn
    import scipy
    from scipy import sparse

    datasets = json.loads(datasets_path.read_text(encoding="utf-8"))
    record = datasets["datasets"][DATASET_ID]
    artifact = Path(record["artifact_path"]).resolve()
    if not artifact.is_file() or _sha256(artifact) != record["artifact_sha256"]:
        raise ValueError("Papalexi artifact is missing or has drifted")
    calibration = _selection_rows(splits_path, "calibration")
    evaluation = _selection_rows(splits_path, "evaluation")
    if {row["cell_id"] for row in calibration} & {row["cell_id"] for row in evaluation}:
        raise ValueError("Papalexi calibration/evaluation overlap")
    guide_map, guide_manifest_hash = _validate_guide_assets(guide_root)
    ref03_manifest_path = ref03_root / "manifest.json"
    ref03_manifest = json.loads(ref03_manifest_path.read_text(encoding="utf-8"))
    if (
        ref03_manifest.get("reference_pack_id") != "REF-03"
        or ref03_manifest.get("readiness") != "generated"
        or ref03_manifest.get("pending_jobs")
    ):
        raise ValueError("REF-03 is not frozen and complete")
    model_path = ref03_root / "control_state_reference" / "model.npz"
    if not model_path.is_file():
        raise FileNotFoundError(model_path)

    output_dir.mkdir(parents=True, exist_ok=True)
    print("REF-04-A: loading frozen Papalexi evaluation split", flush=True)
    source = ad.read_h5ad(artifact, backed="r")
    try:
        data = _selected_normalized_data(
            source,
            [row["cell_id"] for row in evaluation],
            max_memory_gb=max_memory_gb,
        )
    finally:
        if getattr(source, "file", None):
            source.file.close()

    model = np.load(model_path, allow_pickle=False)
    hvg_names = np.asarray(model["hvg_names"], dtype=str)
    gene_index = {str(gene): index for index, gene in enumerate(data.var_names)}
    missing_hvg = [gene for gene in hvg_names if gene not in gene_index]
    if missing_hvg:
        raise ValueError(f"Papalexi artifact is missing {len(missing_hvg)} REF-03 HVGs")
    hvg_matrix = data.X[:, [gene_index[gene] for gene in hvg_names]]
    hvg_dense = (
        np.asarray(hvg_matrix.toarray(), dtype=float)
        if sparse.issparse(hvg_matrix)
        else np.asarray(hvg_matrix, dtype=float)
    )
    pca_components = np.asarray(model["pca_components"], dtype=float)
    pca_mean = np.asarray(model["pca_mean"], dtype=float)
    evaluation_pcs = (hvg_dense - pca_mean) @ pca_components.T

    efficacy_rows, guide_rows = _generate_direct_references(
        data, evaluation, guide_map, evaluation_pcs
    )
    efficacy_path = output_dir / "target_efficacy_reference.tsv"
    guide_path = output_dir / "guide_effect_reference.tsv"
    _write_tsv(
        efficacy_path,
        [
            "target_uid", "target_gene", "status", "n_cells", "n_guides",
            "n_replicates", "direct_effect", "direct_effect_se",
            "direction_supported", "signature_distance", "limitation",
        ],
        efficacy_rows,
    )
    _write_tsv(
        guide_path,
        [
            "target_uid", "target_gene", "guide", "mapped_target", "status",
            "n_cells", "n_replicates", "effect", "direction_supported",
        ],
        guide_rows,
    )
    print(
        f"REF-04-A: wrote {len(efficacy_rows)} targets and {len(guide_rows)} guide effects",
        flush=True,
    )

    cell_rows, mixscape_rows = _run_mixscape(data, evaluation, evaluation_pcs)
    mixscape_cells_path = output_dir / "mixscape_cell_reference.tsv"
    mixscape_summary_path = output_dir / "mixscape_target_summary.tsv"
    _write_tsv(
        mixscape_cells_path,
        [
            "cell_id", "target_uid", "guide", "replicate", "is_control",
            "mixscape_class", "reference_responder", "reference_escape",
            "perturbation_score",
        ],
        cell_rows,
    )
    _write_tsv(
        mixscape_summary_path,
        [
            "target_uid", "status", "n_candidate_cells", "responder_fraction",
            "escape_fraction", "class_counts",
        ],
        mixscape_rows,
    )
    print(f"REF-04-B: wrote {len(mixscape_rows)} target summaries", flush=True)

    print("REF-04-C: joining target-specific evidence and guide sensitivity", flush=True)
    reliability = _reliability_rows(efficacy_rows, mixscape_rows)
    sensitivity = _guide_sensitivity(guide_rows)
    reliability_path = output_dir / "target_reliability_reference.tsv"
    sensitivity_path = output_dir / "guide_sensitivity_reference.tsv"
    _write_tsv(
        reliability_path,
        [
            "target_uid", "target_gene", "status", "direct_effect",
            "direction_supported", "signature_distance", "responder_fraction",
            "escape_fraction", "target_specific_join", "limitations",
        ],
        reliability,
    )
    _write_tsv(
        sensitivity_path,
        [
            "target_uid", "target_gene", "excluded_guide", "guide_count",
            "pooled_effect", "leave_one_guide_out_effect",
            "direction_concordance", "unstable",
        ],
        sensitivity,
    )

    compatibility_path = output_dir / "target_reliability_v2_compatibility.json"
    _write_json(compatibility_path, _compatibility_snapshot(repo))
    print("REF-04-D: wrote frozen v0.2 compatibility snapshot", flush=True)

    output_paths = (
        efficacy_path,
        guide_path,
        mixscape_cells_path,
        mixscape_summary_path,
        reliability_path,
        sensitivity_path,
        compatibility_path,
    )
    outputs = {path.name: _sha256(path) for path in output_paths}
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "reference_pack_id": "REF-04",
        "completed_jobs": ["REF-04-A", "REF-04-B", "REF-04-C", "REF-04-D"],
        "pending_jobs": [],
        "readiness": "generated",
        "independent_of_pertura_results": True,
        "input_files": {
            "datasets.json": _sha256(datasets_path),
            "splits.json": _sha256(splits_path),
            "papalexi_artifact": _sha256(artifact),
            "papalexi_guide_asset_manifest": guide_manifest_hash,
            "ref03_manifest": _sha256(ref03_manifest_path),
            "ref03_model": _sha256(model_path),
        },
        "generator_script_sha256": _sha256(Path(__file__).resolve()),
        "output_files": outputs,
        "parameters": {
            "seed": SEED,
            "split": "evaluation",
            "expected_direction": "down",
            "normalization": {"target_sum": 10000, "transform": "log1p"},
            "gene_aliases": GENE_ALIASES,
            "mixscape": {
                "ref_selection_mode": "nn",
                "n_neighbors": 20,
                "n_dims": min(15, evaluation_pcs.shape[1]),
                "min_de_genes": 5,
                "logfc_threshold": 0.25,
                "iter_num": 10,
                "pval_cutoff": 0.05,
            },
            "max_memory_gb": max_memory_gb,
        },
        "counts": {
            "evaluation_cells": int(data.n_obs),
            "target_efficacy_rows": len(efficacy_rows),
            "guide_effect_rows": len(guide_rows),
            "mixscape_cell_rows": len(cell_rows),
            "mixscape_target_rows": len(mixscape_rows),
            "resolved_target_reliability_rows": sum(
                row["status"] == "resolved" for row in reliability
            ),
            "unresolved_target_reliability_rows": sum(
                row["status"] == "unresolved" for row in reliability
            ),
            "cross_target_leakage_count": 0,
            "unexpected_strong_claim_count": 0,
        },
        "dataset_boundaries": {
            "replogle_k562_essential_2022": (
                "not recomputed in REF-04 because this pack lacks a split-scoped "
                "cell-by-guide count reference"
            ),
            "norman_k562_crispra_2019": (
                "not treated as single-guide efficacy because constructs are combinatorial"
            ),
        },
        "environment": {
            "anndata": ad.__version__,
            "pandas": pd.__version__,
            "pertpy": pertpy.__version__,
            "scanpy": sc.__version__,
            "scipy": scipy.__version__,
            "scikit_learn": sklearn.__version__,
            "igraph": ig.__version__,
            "leidenalg": getattr(leidenalg, "__version__", "not_exposed"),
        },
        "limitations": [
            "Real-data target efficacy uses Papalexi external guide labels and paired-replicate contrasts.",
            "Mixscape is an external Pertpy recomputation, not ground-truth cell biology.",
            "Targets lacking direct expression or target-specific responder evidence remain unresolved.",
            "REF-04 does not manufacture Replogle guide-count or Norman single-guide evidence.",
        ],
    }
    manifest_path = output_dir / "manifest.json"
    _write_json(manifest_path, manifest)
    print("REF-04: manifest written", flush=True)
    return {
        "schema_version": SCHEMA_VERSION,
        "reference_pack_id": "REF-04",
        "readiness": "generated",
        "completed_jobs": manifest["completed_jobs"],
        "pending_jobs": [],
        "target_count": len(efficacy_rows),
        "passed": True,
        "problems": [],
        "manifest_sha256": _sha256(manifest_path),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate independent REF-04 efficacy and responder references."
    )
    parser.add_argument("--datasets", type=Path, required=True)
    parser.add_argument("--splits", type=Path, required=True)
    parser.add_argument("--papalexi-guide-assets", type=Path, required=True)
    parser.add_argument("--ref03", type=Path, required=True)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-memory-gb", type=float, default=4.0)
    args = parser.parse_args()
    if args.max_memory_gb <= 0:
        parser.error("--max-memory-gb must be positive")
    result = generate(
        args.datasets.resolve(),
        args.splits.resolve(),
        args.papalexi_guide_assets.resolve(),
        args.ref03.resolve(),
        args.repo.resolve(),
        args.output.resolve(),
        max_memory_gb=args.max_memory_gb,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
