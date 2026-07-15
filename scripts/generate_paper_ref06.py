from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Iterable


SCHEMA_VERSION = "pertura-paper-ref06-v1"
DATASET_ID = "norman_k562_crispra_2019"
SEED = 1729
N_CELLS = 1200
N_TARGETS = 10
N_GUIDES_PER_TARGET = 2
N_GENES = 60
ALPHA = 0.10


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _path_sha256(path: Path) -> str:
    if path.is_file():
        return _sha256(path)
    digest = hashlib.sha256()
    for member in sorted(item for item in path.rglob("*") if item.is_file()):
        digest.update(member.relative_to(path).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(_sha256(member).encode("ascii"))
        digest.update(b"\0")
    return "sha256:" + digest.hexdigest()


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def _write_tsv(path: Path, fields: list[str], rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)


def _write_matrix(path: Path, row_label: str, row_ids: list[str], matrix: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    cell_ids = [f"cell_{index:04d}" for index in range(matrix.shape[1])]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow([row_label, *cell_ids])
        for row_id, values in zip(row_ids, matrix, strict=True):
            writer.writerow([row_id, *(int(value) for value in values)])


def _bh_adjust(pvalues: Any) -> Any:
    import numpy as np

    values = np.asarray(pvalues, dtype=float)
    order = np.argsort(values)
    ranked = values[order]
    adjusted = ranked * len(values) / np.arange(1, len(values) + 1)
    adjusted = np.minimum.accumulate(adjusted[::-1])[::-1]
    output = np.empty_like(adjusted)
    output[order] = np.minimum(adjusted, 1.0)
    return output


def _generate_fixture(output_dir: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    import numpy as np
    from scipy.stats import spearmanr, ttest_ind

    rng = np.random.default_rng(SEED)
    fixture_dir = output_dir / "sceptre_fixture"
    fixture_dir.mkdir(parents=True, exist_ok=True)
    cell_ids = [f"cell_{index:04d}" for index in range(N_CELLS)]
    targets = [f"target_{index:02d}" for index in range(N_TARGETS)]
    guides = [
        f"{target}_g{guide_index + 1}"
        for target in targets
        for guide_index in range(N_GUIDES_PER_TARGET)
    ]
    genes = [f"gene_{index:03d}" for index in range(N_GENES)]

    target_presence = np.zeros((N_TARGETS, N_CELLS), dtype=bool)
    guide_counts = np.zeros((len(guides), N_CELLS), dtype=np.int64)
    for cell_index in range(N_CELLS):
        n_targets = int(rng.integers(2, 5))
        selected = rng.choice(N_TARGETS, size=n_targets, replace=False)
        target_presence[selected, cell_index] = True
        for target_index in selected:
            guide_index = target_index * N_GUIDES_PER_TARGET + int(rng.integers(0, 2))
            guide_counts[guide_index, cell_index] = int(rng.poisson(12) + 3)
    ambient = (guide_counts == 0) & (rng.random(guide_counts.shape) < 0.02)
    guide_counts[ambient] = rng.poisson(1.0, size=int(ambient.sum())) + 1

    true_effects = np.zeros((N_TARGETS, N_GENES), dtype=float)
    for target_index in range(N_TARGETS):
        true_effects[target_index, 2 * target_index] = 0.95 + 0.03 * target_index
        true_effects[target_index, 2 * target_index + 1] = -0.75 - 0.02 * target_index
    base_rates = rng.uniform(2.5, 7.0, size=N_GENES)
    size_factors = rng.lognormal(mean=0.0, sigma=0.25, size=N_CELLS)
    batch = np.where(np.arange(N_CELLS) % 2 == 0, 0, 1)
    response_counts = np.zeros((N_GENES, N_CELLS), dtype=np.int64)
    for gene_index in range(N_GENES):
        log_effect = target_presence.T @ true_effects[:, gene_index]
        mean = base_rates[gene_index] * size_factors * np.exp(log_effect)
        response_counts[gene_index] = rng.poisson(mean)

    truth_rows: list[dict[str, Any]] = []
    pair_rows: list[dict[str, str]] = []
    null_pool = list(range(20, N_GENES))
    for target_index, target in enumerate(targets):
        gene_indices = [2 * target_index, 2 * target_index + 1]
        gene_indices.extend(
            null_pool[(target_index * 4 + offset) % len(null_pool)]
            for offset in range(8)
        )
        for gene_index in gene_indices:
            effect = float(true_effects[target_index, gene_index])
            pair_id = f"{target}::{genes[gene_index]}"
            truth_rows.append(
                {
                    "pair_id": pair_id,
                    "grna_target": target,
                    "response_id": genes[gene_index],
                    "is_positive": str(effect != 0.0).lower(),
                    "true_log_rate_ratio": f"{effect:.8f}",
                    "expected_direction": (
                        "up" if effect > 0 else "down" if effect < 0 else "null"
                    ),
                }
            )
            pair_rows.append({"grna_target": target, "response_id": genes[gene_index]})

    library_size = response_counts.sum(axis=0).astype(float)
    size = library_size / np.median(library_size)
    normalized = np.log1p(response_counts / size[None, :])
    reference_rows: list[dict[str, Any]] = []
    pvalues: list[float] = []
    for truth in truth_rows:
        target_index = targets.index(str(truth["grna_target"]))
        gene_index = genes.index(str(truth["response_id"]))
        present = target_presence[target_index]
        observed = normalized[gene_index]
        effect = float(observed[present].mean() - observed[~present].mean())
        test = ttest_ind(observed[present], observed[~present], equal_var=False)
        pvalue = float(test.pvalue) if math.isfinite(float(test.pvalue)) else 1.0
        pvalues.append(pvalue)
        reference_rows.append(
            {
                "pair_id": truth["pair_id"],
                "grna_target": truth["grna_target"],
                "response_id": truth["response_id"],
                "reference_effect": f"{effect:.10f}",
                "reference_p_value": f"{pvalue:.12g}",
            }
        )
    adjusted = _bh_adjust(pvalues)
    for row, fdr in zip(reference_rows, adjusted, strict=True):
        row["reference_fdr"] = f"{float(fdr):.12g}"
        row["reference_discovery"] = str(float(fdr) <= ALPHA).lower()

    truth_positive = np.array([row["is_positive"] == "true" for row in truth_rows])
    discoveries = np.array([row["reference_discovery"] == "true" for row in reference_rows])
    null_p = np.array(pvalues)[~truth_positive]
    true_values = np.array([float(row["true_log_rate_ratio"]) for row in truth_rows])
    observed_values = np.array([float(row["reference_effect"]) for row in reference_rows])
    correlation = float(spearmanr(true_values[truth_positive], observed_values[truth_positive]).statistic)
    metrics = {
        "type_i_error": float((null_p <= 0.05).mean()),
        "power": float(discoveries[truth_positive].mean()),
        "fdr": float((~truth_positive[discoveries]).mean()) if discoveries.any() else 0.0,
        "effect_rank_concordance": correlation,
        "positive_pair_count": int(truth_positive.sum()),
        "null_pair_count": int((~truth_positive).sum()),
        "discovery_count": int(discoveries.sum()),
    }

    response_path = fixture_dir / "response_matrix.csv"
    guide_path = fixture_dir / "guide_matrix.csv"
    guide_map_path = fixture_dir / "guide_target_map.csv"
    pairs_path = fixture_dir / "discovery_pairs.csv"
    retained_path = fixture_dir / "retained_cells.txt"
    covariates_path = fixture_dir / "covariates.csv"
    _write_matrix(response_path, "response_id", genes, response_counts)
    _write_matrix(guide_path, "grna_id", guides, guide_counts)
    with guide_map_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["grna_id", "grna_target"])
        writer.writeheader()
        for guide_index, guide in enumerate(guides):
            writer.writerow(
                {"grna_id": guide, "grna_target": targets[guide_index // 2]}
            )
    with pairs_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["grna_target", "response_id"])
        writer.writeheader()
        writer.writerows(pair_rows)
    retained_path.write_text("\n".join(cell_ids) + "\n", encoding="utf-8")
    with covariates_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["cell_id", "batch"])
        writer.writerows(zip(cell_ids, batch, strict=True))

    file_hashes = {
        path.name: _sha256(path)
        for path in (
            response_path,
            guide_path,
            guide_map_path,
            pairs_path,
            retained_path,
            covariates_path,
        )
    }
    fixture_manifest = {
        "schema_version": "pertura-sceptre-planted-fixture-v1",
        "seed": SEED,
        "dimensions": {
            "cells": N_CELLS,
            "targets": N_TARGETS,
            "guides": len(guides),
            "response_genes": N_GENES,
            "discovery_pairs": len(truth_rows),
        },
        "high_moi": {
            "minimum_targets_per_cell": 2,
            "maximum_targets_per_cell": 4,
            "mean_targets_per_cell": float(target_presence.sum(axis=0).mean()),
        },
        "files": file_hashes,
    }
    _write_json(fixture_dir / "fixture_manifest.json", fixture_manifest)
    return truth_rows, reference_rows, {"fixture": fixture_manifest, "metrics": metrics}


def _asset_tokens(value: Any) -> list[str]:
    if isinstance(value, dict):
        return [str(key).lower() for key in value] + [
            token for item in value.values() for token in _asset_tokens(item)
        ]
    if isinstance(value, list):
        return [token for item in value for token in _asset_tokens(item)]
    return [str(value).lower()]


def _norman_suitability(datasets_path: Path, ref01_root: Path) -> dict[str, Any]:
    datasets = json.loads(datasets_path.read_text(encoding="utf-8"))
    profiles_path = ref01_root / "dataset_profiles.json"
    profiles = json.loads(profiles_path.read_text(encoding="utf-8"))
    profile = profiles["datasets"][DATASET_ID]
    record = datasets["datasets"][DATASET_ID]
    obs_columns = [str(value) for value in profile.get("obs_columns", [])]
    guide_label_columns = sorted(
        value
        for value in obs_columns
        if any(token in value.lower() for token in ("guide", "grna", "sgrna"))
    )
    matrix_keys = [
        *[str(value) for value in profile.get("layers", {})],
        *[str(value) for value in profile.get("obsm_keys", [])],
    ]
    auxiliary = record.get("auxiliary_assets") or {}
    tokens = _asset_tokens(auxiliary)
    cell_by_guide_tokens = (
        "cell_by_guide",
        "cell-by-guide",
        "guide_matrix",
        "grna_matrix",
        "guide_counts",
        "grna_counts",
    )
    matrix_candidates = sorted(
        value
        for value in matrix_keys
        if any(token in value.lower() for token in ("guide", "grna", "sgrna"))
    )
    auxiliary_candidates = sorted(
        {token for token in tokens if any(name in token for name in cell_by_guide_tokens)}
    )
    has_counts = bool(matrix_candidates or auxiliary_candidates)
    return {
        "schema_version": "pertura-norman-sceptre-suitability-v1",
        "dataset_id": DATASET_ID,
        "artifact_sha256": profile["artifact_sha256"],
        "profile_sha256": _sha256(profiles_path),
        "observed_guide_label_columns": guide_label_columns,
        "candidate_cell_by_guide_matrix_keys": matrix_candidates,
        "candidate_auxiliary_assets": auxiliary_candidates,
        "cell_by_guide_counts_available": has_counts,
        "suitable_for_sceptre": has_counts,
        "expected_outcome": "not_configured" if has_counts else "blocked",
        "correct_refusal": not has_counts,
        "missing_inputs": [] if has_counts else ["cell_by_guide_counts"],
        "required_behavior": (
            "run only after a split-scoped cell-by-guide count asset is registered"
            if has_counts
            else "block before runner invocation and do not substitute another method"
        ),
        "silent_fallback_count": 0,
        "limitations": [
            "Guide identity labels are not cell-by-guide count observations.",
            "The RNA counts layer is not evidence of a guide-count modality.",
            "This audit establishes suitability and correct refusal, not real-data SCEPTRE performance.",
        ],
    }


def generate(datasets_path: Path, ref01_root: Path, output_dir: Path) -> dict[str, Any]:
    import numpy
    import scipy

    ref01_manifest_path = ref01_root / "manifest.json"
    ref01_profile_path = ref01_root / "dataset_profiles.json"
    if not datasets_path.is_file() or not ref01_manifest_path.is_file() or not ref01_profile_path.is_file():
        raise FileNotFoundError("REF-06 inputs are incomplete")
    ref01_manifest = json.loads(ref01_manifest_path.read_text(encoding="utf-8"))
    if (
        ref01_manifest.get("reference_pack_id") != "REF-01"
        or ref01_manifest.get("readiness") != "generated"
        or ref01_manifest.get("pending_jobs")
    ):
        raise ValueError("REF-01 must be frozen and complete before REF-06")

    output_dir.mkdir(parents=True, exist_ok=True)
    print("REF-06-A: generating compact high-MOI planted fixture", flush=True)
    truth_rows, reference_rows, fixture_summary = _generate_fixture(output_dir)
    truth_path = output_dir / "sceptre_synthetic_truth.tsv"
    reference_path = output_dir / "sceptre_reference_results.tsv"
    metrics_path = output_dir / "sceptre_reference_metrics.json"
    _write_tsv(
        truth_path,
        [
            "pair_id",
            "grna_target",
            "response_id",
            "is_positive",
            "true_log_rate_ratio",
            "expected_direction",
        ],
        truth_rows,
    )
    _write_tsv(
        reference_path,
        [
            "pair_id",
            "grna_target",
            "response_id",
            "reference_effect",
            "reference_p_value",
            "reference_fdr",
            "reference_discovery",
        ],
        reference_rows,
    )
    _write_json(metrics_path, fixture_summary["metrics"])

    print("REF-06-B: auditing Norman cell-by-guide input availability", flush=True)
    suitability = _norman_suitability(datasets_path, ref01_root)
    suitability_path = output_dir / "norman_sceptre_suitability.json"
    _write_json(suitability_path, suitability)
    if not suitability["correct_refusal"]:
        raise ValueError("Norman unexpectedly exposes a cell-by-guide count asset")

    fixture_dir = output_dir / "sceptre_fixture"
    outputs = {
        "sceptre_fixture": _path_sha256(fixture_dir),
        truth_path.name: _sha256(truth_path),
        reference_path.name: _sha256(reference_path),
        metrics_path.name: _sha256(metrics_path),
        suitability_path.name: _sha256(suitability_path),
    }
    metrics = fixture_summary["metrics"]
    metrics["correct_refusal"] = bool(suitability["correct_refusal"])
    metrics["silent_fallback_count"] = int(suitability["silent_fallback_count"])
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "reference_pack_id": "REF-06",
        "completed_jobs": ["REF-06-A", "REF-06-B"],
        "pending_jobs": [],
        "readiness": "generated",
        "independent_of_pertura_results": True,
        "input_files": {
            "datasets.json": _sha256(datasets_path),
            "ref01_manifest": _sha256(ref01_manifest_path),
            "ref01_dataset_profiles": _sha256(ref01_profile_path),
        },
        "generator_script_sha256": _sha256(Path(__file__).resolve()),
        "output_files": outputs,
        "counts": {
            "synthetic_cells": N_CELLS,
            "synthetic_targets": N_TARGETS,
            "synthetic_guides": N_TARGETS * N_GUIDES_PER_TARGET,
            "synthetic_response_genes": N_GENES,
            "tested_pairs": len(truth_rows),
            "positive_pairs": metrics["positive_pair_count"],
            "null_pairs": metrics["null_pair_count"],
            "norman_cell_by_guide_count_assets": 0,
        },
        "metrics": metrics,
        "parameters": {
            "seed": SEED,
            "multiple_testing_alpha": ALPHA,
            "minimum_targets_per_cell": 2,
            "maximum_targets_per_cell": 4,
        },
        "environment": {
            "numpy": numpy.__version__,
            "scipy": scipy.__version__,
        },
        "limitations": [
            "Positive performance metrics use planted high-MOI data rather than a fifth real dataset.",
            "The independent reference statistic is a Welch test on library-size-normalized log counts; SCEPTRE is evaluated against planted truth rather than expected to reproduce identical p-values.",
            "Norman contributes a correct-refusal test because cell-by-guide counts are unavailable.",
            "No real-data SCEPTRE performance claim is made.",
        ],
    }
    manifest_path = output_dir / "manifest.json"
    _write_json(manifest_path, manifest)
    print("REF-06: manifest written", flush=True)
    return {
        "schema_version": SCHEMA_VERSION,
        "reference_pack_id": "REF-06",
        "readiness": "generated",
        "completed_jobs": manifest["completed_jobs"],
        "pending_jobs": [],
        "passed": True,
        "problems": [],
        "manifest_sha256": _sha256(manifest_path),
        "metrics": metrics,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate compact planted SCEPTRE and Norman refusal references."
    )
    parser.add_argument("--datasets", type=Path, required=True)
    parser.add_argument("--ref01", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = generate(
        args.datasets.resolve(), args.ref01.resolve(), args.output.resolve()
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
