from __future__ import annotations

import argparse
import csv
import hashlib
import json
from itertools import combinations
from pathlib import Path
from typing import Any, Iterable


SCHEMA_VERSION = "pertura-paper-ref07-v1"
SEED = 1729
DATASET_IDS = (
    "replogle_k562_essential_2022",
    "papalexi_thp1_eccite",
    "norman_k562_crispra_2019",
)
N_PERTURBATIONS = 12
N_EFFECT_GENES = 48
N_MODULES = 4
N_CELLS = 360
N_CONTROL_CELLS = 240
N_NMF_GENES = 80
NMF_RANKS = (3, 4, 5)
NMF_SEEDS = (1729, 1730, 1731)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
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


def _effect_and_gmt_references(output: Path) -> dict[str, Any]:
    import numpy as np

    rng = np.random.default_rng(SEED)
    perturbations = [f"perturbation_{index:02d}" for index in range(N_PERTURBATIONS)]
    genes = [f"effect_gene_{index:03d}" for index in range(N_EFFECT_GENES)]
    modules = {
        f"module_{module + 1:02d}": genes[module * 12 : (module + 1) * 12]
        for module in range(N_MODULES)
    }
    matrix = rng.normal(0.0, 0.04, size=(N_PERTURBATIONS, N_EFFECT_GENES))
    for perturbation_index in range(N_PERTURBATIONS):
        primary = perturbation_index % N_MODULES
        secondary = (primary + 1) % N_MODULES
        matrix[perturbation_index, primary * 12 : (primary + 1) * 12] += (
            1.0 + 0.08 * (perturbation_index // N_MODULES)
        )
        matrix[perturbation_index, secondary * 12 : (secondary + 1) * 12] -= 0.35
    observed = rng.random(matrix.shape) >= 0.18
    for perturbation_index in range(N_PERTURBATIONS):
        primary = perturbation_index % N_MODULES
        observed[perturbation_index, primary * 12 : primary * 12 + 6] = True

    effect_rows = []
    for row_index, perturbation in enumerate(perturbations):
        for column_index, gene in enumerate(genes):
            is_observed = bool(observed[row_index, column_index])
            effect_rows.append(
                {
                    "perturbation_id": perturbation,
                    "feature_id": gene,
                    "effect": f"{matrix[row_index, column_index]:.10f}" if is_observed else "",
                    "observed": str(is_observed).lower(),
                }
            )
    effect_path = output / "effect_matrix_reference.tsv"
    _write_tsv(
        effect_path,
        ["perturbation_id", "feature_id", "effect", "observed"],
        effect_rows,
    )

    inputs = output / "effect_inputs"
    inputs.mkdir(parents=True, exist_ok=True)
    input_hashes = {}
    for batch in range(2):
        selected = set(perturbations[batch * 6 : (batch + 1) * 6])
        path = inputs / f"target_effects_{batch + 1:02d}.csv"
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(
                handle, fieldnames=["perturbation_id", "gene", "log2FC"]
            )
            writer.writeheader()
            for row in effect_rows:
                if row["perturbation_id"] in selected and row["observed"] == "true":
                    writer.writerow(
                        {
                            "perturbation_id": row["perturbation_id"],
                            "gene": row["feature_id"],
                            "log2FC": row["effect"],
                        }
                    )
        input_hashes[path.name] = _sha256(path)

    gmt_root = output / "gmt_fixtures"
    gmt_root.mkdir(parents=True, exist_ok=True)
    valid_gmt = gmt_root / "valid_modules.gmt"
    valid_gmt.write_text(
        "\n".join(
            f"{name}\tplanted module\t" + "\t".join(members)
            for name, members in modules.items()
        )
        + "\n",
        encoding="utf-8",
    )
    malformed = {
        "duplicate_module.gmt": (
            "module_01\tfirst\teffect_gene_000\teffect_gene_001\n"
            "module_01\tsecond\teffect_gene_002\teffect_gene_003\n"
        ),
        "empty_module.gmt": "module_empty\tdescription only\n",
        "repeated_gene.gmt": (
            "module_repeat\trepeated member\teffect_gene_000\teffect_gene_000\t"
            "effect_gene_001\n"
        ),
    }
    for name, text in malformed.items():
        (gmt_root / name).write_text(text, encoding="utf-8")

    module_rows = []
    for row_index, perturbation in enumerate(perturbations):
        for module_name, members in modules.items():
            indices = [genes.index(gene) for gene in members]
            indices = [index for index in indices if observed[row_index, index]]
            values = matrix[row_index, indices]
            module_rows.append(
                {
                    "perturbation_id": perturbation,
                    "module": module_name,
                    "n_observed_genes": len(indices),
                    "mean_signed_effect": f"{float(values.mean()):.10f}",
                    "direction_consistency": f"{float(max((values >= 0).mean(), (values <= 0).mean())):.10f}",
                    "weighting": "uniform_observed_genes",
                }
            )
    module_path = output / "module_effect_reference.tsv"
    _write_tsv(
        module_path,
        [
            "perturbation_id",
            "module",
            "n_observed_genes",
            "mean_signed_effect",
            "direction_consistency",
            "weighting",
        ],
        module_rows,
    )
    gmt_reference = {
        "schema_version": "pertura-paper-gmt-reference-v1",
        "species": "Homo sapiens",
        "identifier_namespace": "synthetic_symbol",
        "tested_gene_universe": genes,
        "valid_modules": modules,
        "valid_module_count": len(modules),
        "expected_coverage": {name: 1.0 for name in modules},
        "fixtures": {
            "valid_modules.gmt": "completed",
            "duplicate_module.gmt": "blocked_duplicate_module_name",
            "empty_module.gmt": "blocked_missing_gene",
            "repeated_gene.gmt": "completed_with_caution_deduplicated_gene",
        },
        "file_hashes": {
            path.name: _sha256(path)
            for path in sorted(gmt_root.iterdir())
            if path.is_file()
        },
    }
    gmt_reference_path = output / "gmt_reference.json"
    _write_json(gmt_reference_path, gmt_reference)
    return {
        "effect_path": effect_path,
        "module_path": module_path,
        "gmt_reference_path": gmt_reference_path,
        "input_hashes": input_hashes,
        "observed_fraction": float(observed.mean()),
        "missing_fraction": float(1.0 - observed.mean()),
        "effect_shape": [N_PERTURBATIONS, N_EFFECT_GENES],
        "module_count": len(modules),
    }


def _component_metrics(components: Any, truth: Any) -> tuple[float, float]:
    import numpy as np
    from scipy.optimize import linear_sum_assignment

    normalized_components = components / np.maximum(
        np.linalg.norm(components, axis=1, keepdims=True), 1e-12
    )
    normalized_truth = truth / np.maximum(
        np.linalg.norm(truth, axis=1, keepdims=True), 1e-12
    )
    similarity = normalized_components @ normalized_truth.T
    rows, columns = linear_sum_assignment(-similarity)
    component_matching = float(similarity[rows, columns].mean())
    jaccards = []
    truth_size = int((truth[0] > 0).sum())
    for row, column in zip(rows, columns, strict=True):
        predicted = set(np.argsort(components[row])[::-1][:truth_size].tolist())
        expected = set(np.flatnonzero(truth[column] > 0).tolist())
        jaccards.append(len(predicted & expected) / len(predicted | expected))
    return component_matching, float(np.mean(jaccards))


def _nmf_references(output: Path) -> dict[str, Any]:
    import anndata as ad
    import numpy as np
    import pandas as pd
    from scipy import sparse
    from sklearn.decomposition import NMF
    from sklearn.metrics import adjusted_rand_score

    rng = np.random.default_rng(SEED)
    cells = [f"nmf_cell_{index:04d}" for index in range(N_CELLS)]
    genes = [f"nmf_gene_{index:03d}" for index in range(N_NMF_GENES)]
    truth = np.zeros((N_MODULES, N_NMF_GENES), dtype=float)
    for module in range(N_MODULES):
        start = module * 15
        truth[module, start : start + 15] = np.linspace(2.4, 1.2, 15)
    scores = rng.gamma(shape=1.8, scale=1.0, size=(N_CELLS, N_MODULES))
    scores[N_CONTROL_CELLS:, 0] += 3.0
    scores[N_CONTROL_CELLS:, 2] += 2.0
    mean = 0.15 + scores @ truth
    counts = rng.poisson(mean).astype(np.int32)
    counts = sparse.csr_matrix(counts)
    obs = pd.DataFrame(
        {
            "control_status": [
                "control" if index < N_CONTROL_CELLS else "perturbed"
                for index in range(N_CELLS)
            ],
            "split_scope": [
                "calibration" if index < N_CONTROL_CELLS else "evaluation"
                for index in range(N_CELLS)
            ],
            "retained": True,
        },
        index=cells,
    )
    var = pd.DataFrame(index=genes)
    fixture_path = output / "planted_module_fixture.h5ad"
    ad.AnnData(X=counts, obs=obs, var=var).write_h5ad(
        fixture_path, compression="gzip"
    )

    truth_rows = []
    for module in range(N_MODULES):
        for gene_index, gene in enumerate(genes):
            loading = float(truth[module, gene_index])
            truth_rows.append(
                {
                    "program": f"program_{module + 1:02d}",
                    "gene": gene,
                    "planted_loading": f"{loading:.10f}",
                    "is_member": str(loading > 0).lower(),
                }
            )
    truth_path = output / "nmf_truth.tsv"
    _write_tsv(
        truth_path,
        ["program", "gene", "planted_loading", "is_member"],
        truth_rows,
    )

    control_rows = []
    for index, cell in enumerate(cells):
        expected = index < N_CONTROL_CELLS
        control_rows.append(
            {
                "cell_id": cell,
                "control_status": "control" if expected else "perturbed",
                "split_scope": "calibration" if expected else "evaluation",
                "expected_in_control_fit": str(expected).lower(),
                "exclusion_reason": "" if expected else "perturbed_or_evaluation_cell",
            }
        )
    control_path = output / "control_nmf_truth.tsv"
    _write_tsv(
        control_path,
        [
            "cell_id",
            "control_status",
            "split_scope",
            "expected_in_control_fit",
            "exclusion_reason",
        ],
        control_rows,
    )

    control_matrix = counts[:N_CONTROL_CELLS].astype(float)
    library = np.asarray(control_matrix.sum(axis=1)).ravel()
    scale = np.divide(
        1e4,
        library,
        out=np.zeros_like(library, dtype=float),
        where=library > 0,
    )
    normalized = sparse.diags(scale) @ control_matrix
    normalized.data = np.log1p(normalized.data)
    stability_rows = []
    rank_assignments: dict[int, list[Any]] = {}
    rank_fits: dict[int, list[dict[str, Any]]] = {}
    for rank in NMF_RANKS:
        assignments = []
        fits = []
        for seed in NMF_SEEDS:
            model = NMF(
                n_components=rank,
                init="nndsvda",
                random_state=seed,
                max_iter=1000,
                solver="cd",
            )
            model.fit_transform(normalized)
            assignment = model.components_.argmax(axis=0)
            assignments.append(assignment)
            matching, recovery = _component_metrics(model.components_, truth)
            fits.append(
                {
                    "rank": rank,
                    "seed": seed,
                    "reconstruction_error": float(model.reconstruction_err_),
                    "component_matching": matching,
                    "module_recovery": recovery,
                }
            )
        rank_assignments[rank] = assignments
        rank_fits[rank] = fits
        comparisons = [
            adjusted_rand_score(left, right)
            for left, right in combinations(assignments, 2)
        ]
        stability = float(np.mean(comparisons))
        for fit in fits:
            stability_rows.append(
                {
                    **fit,
                    "cross_seed_stability": stability,
                    "fit_population": "confirmed_calibration_controls_only",
                }
            )
    stability_path = output / "nmf_stability_reference.tsv"
    _write_tsv(
        stability_path,
        [
            "rank",
            "seed",
            "reconstruction_error",
            "component_matching",
            "module_recovery",
            "cross_seed_stability",
            "fit_population",
        ],
        stability_rows,
    )
    chosen = max(
        (row for row in stability_rows if row["rank"] == N_MODULES),
        key=lambda row: (row["module_recovery"], row["component_matching"]),
    )
    return {
        "fixture_path": fixture_path,
        "truth_path": truth_path,
        "stability_path": stability_path,
        "control_path": control_path,
        "counts_nnz": int(counts.nnz),
        "chosen_rank": N_MODULES,
        "module_recovery": float(chosen["module_recovery"]),
        "component_matching": float(chosen["component_matching"]),
        "cross_seed_stability": float(chosen["cross_seed_stability"]),
        "control_identity_match": 1.0,
        "leakage_count": 0,
    }


def generate(datasets_path: Path, ref01_root: Path, output: Path) -> dict[str, Any]:
    import anndata
    import numpy
    import scipy
    import sklearn

    ref01_manifest_path = ref01_root / "manifest.json"
    profiles_path = ref01_root / "dataset_profiles.json"
    if not datasets_path.is_file() or not ref01_manifest_path.is_file() or not profiles_path.is_file():
        raise FileNotFoundError("REF-07 inputs are incomplete")
    ref01_manifest = json.loads(ref01_manifest_path.read_text(encoding="utf-8"))
    if (
        ref01_manifest.get("reference_pack_id") != "REF-01"
        or ref01_manifest.get("readiness") != "generated"
        or ref01_manifest.get("pending_jobs")
    ):
        raise ValueError("REF-01 must be frozen and complete before REF-07")
    profiles = json.loads(profiles_path.read_text(encoding="utf-8"))["datasets"]
    missing_profiles = sorted(set(DATASET_IDS) - set(profiles))
    if missing_profiles:
        raise ValueError("REF-01 profiles are missing: " + ", ".join(missing_profiles))

    output.mkdir(parents=True, exist_ok=True)
    print("REF-07-A/B: generating compact effect and GMT references", flush=True)
    effects = _effect_and_gmt_references(output)
    print("REF-07-C: generating planted sparse NMF references", flush=True)
    nmf = _nmf_references(output)
    fixture_manifest = {
        "schema_version": "pertura-paper-ref07-fixture-v1",
        "seed": SEED,
        "effect_shape": effects["effect_shape"],
        "effect_observed_fraction": effects["observed_fraction"],
        "effect_missing_fraction": effects["missing_fraction"],
        "module_count": effects["module_count"],
        "nmf_shape": [N_CELLS, N_NMF_GENES],
        "nmf_nonzero_count": nmf["counts_nnz"],
        "control_cell_count": N_CONTROL_CELLS,
        "excluded_perturbed_or_evaluation_cells": N_CELLS - N_CONTROL_CELLS,
        "ranks": list(NMF_RANKS),
        "seeds": list(NMF_SEEDS),
        "effect_input_hashes": effects["input_hashes"],
    }
    fixture_manifest_path = output / "fixture_manifest.json"
    _write_json(fixture_manifest_path, fixture_manifest)

    output_paths = {
        "effect_matrix_reference.tsv": effects["effect_path"],
        "module_effect_reference.tsv": effects["module_path"],
        "gmt_reference.json": effects["gmt_reference_path"],
        "planted_module_fixture.h5ad": nmf["fixture_path"],
        "nmf_truth.tsv": nmf["truth_path"],
        "nmf_stability_reference.tsv": nmf["stability_path"],
        "control_nmf_truth.tsv": nmf["control_path"],
        "fixture_manifest.json": fixture_manifest_path,
    }
    metrics = {
        "matrix_value_mae": 0.0,
        "missingness_match": 1.0,
        "module_effect_mae": 0.0,
        "gene_set_membership_match": 1.0,
        "module_recovery": nmf["module_recovery"],
        "component_matching": nmf["component_matching"],
        "cross_seed_stability": nmf["cross_seed_stability"],
        "control_identity_match": nmf["control_identity_match"],
        "leakage_count": nmf["leakage_count"],
    }
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "reference_pack_id": "REF-07",
        "completed_jobs": ["REF-07-A", "REF-07-B", "REF-07-C"],
        "pending_jobs": [],
        "readiness": "generated",
        "independent_of_pertura_results": True,
        "input_files": {
            "datasets.json": _sha256(datasets_path),
            "ref01_manifest": _sha256(ref01_manifest_path),
            "ref01_dataset_profiles": _sha256(profiles_path),
        },
        "dataset_boundaries": {
            dataset_id: {
                "shape": profiles[dataset_id]["shape"],
                "use": "applicability profile only; real expression matrix was not loaded",
            }
            for dataset_id in DATASET_IDS
        },
        "generator_script_sha256": _sha256(Path(__file__).resolve()),
        "output_files": {name: _sha256(path) for name, path in output_paths.items()},
        "counts": {
            "perturbations": N_PERTURBATIONS,
            "effect_genes": N_EFFECT_GENES,
            "gmt_modules": N_MODULES,
            "nmf_cells": N_CELLS,
            "nmf_control_cells": N_CONTROL_CELLS,
            "nmf_genes": N_NMF_GENES,
            "nmf_rank_seed_fits": len(NMF_RANKS) * len(NMF_SEEDS),
        },
        "metrics": metrics,
        "parameters": {
            "seed": SEED,
            "nmf_ranks": list(NMF_RANKS),
            "nmf_seeds": list(NMF_SEEDS),
            "fit_population": "confirmed_calibration_controls_only",
        },
        "environment": {
            "anndata": anndata.__version__,
            "numpy": numpy.__version__,
            "scipy": scipy.__version__,
            "scikit_learn": sklearn.__version__,
        },
        "limitations": [
            "NMF recovery uses a compact planted sparse fixture rather than claiming a unique biological module truth.",
            "Real dataset profiles define applicability boundaries but their expression matrices are not read by REF-07.",
            "Effect and module MAE values are reference self-consistency targets; observed capability errors are computed later by the artifact evaluator.",
            "Learned modules remain candidate or derived structures and are not promoted to measured biological facts.",
        ],
    }
    manifest_path = output / "manifest.json"
    _write_json(manifest_path, manifest)
    print("REF-07: manifest written", flush=True)
    return {
        "schema_version": SCHEMA_VERSION,
        "reference_pack_id": "REF-07",
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
        description="Generate compact effect-matrix, GMT, and planted NMF references."
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
