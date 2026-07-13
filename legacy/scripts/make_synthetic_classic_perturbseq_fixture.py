from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

RAW_KLF1 = "KLF1_NegCtrl0__KLF1_NegCtrl0"
RAW_CONTROL = "NegCtrl0_NegCtrl0__NegCtrl0_NegCtrl0"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create a small synthetic guide-based Perturb-seq fixture for Pertura smoke tests.")
    parser.add_argument("--out", type=Path, default=Path("fixtures/synthetic_classic_perturbseq"), help="Output fixture directory.")
    parser.add_argument("--n-control", type=int, default=72)
    parser.add_argument("--n-klf1", type=int, default=72)
    parser.add_argument("--n-background-genes", type=int, default=30)
    args = parser.parse_args(argv)

    try:
        import anndata as ad
        import pandas as pd
        from scipy import sparse
    except ModuleNotFoundError as exc:
        raise SystemExit(
            "This fixture generator requires omics dependencies. Install with `python -m pip install -e .[omics]` "
            "or run inside the pertura environment that contains anndata, pandas, and scipy."
        ) from exc

    out_dir = args.out
    out_dir.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(20260707)

    signal_genes = ["KLF1", "GATA1", "HBB", "ALAS2", "GYPA", "SPI1", "LYZ", "MPO", "ELANE"]
    background_genes = [f"BG{i:02d}" for i in range(args.n_background_genes)]
    genes = signal_genes + background_genes
    gene_to_idx = {gene: idx for idx, gene in enumerate(genes)}

    n_cells = args.n_control + args.n_klf1
    counts = rng.poisson(1.0, size=(n_cells, len(genes))).astype(float)
    labels = np.array([RAW_CONTROL] * args.n_control + [RAW_KLF1] * args.n_klf1)
    rng.shuffle(labels)

    for cell_idx, label in enumerate(labels):
        if label == RAW_CONTROL:
            for gene in ["KLF1", "GATA1", "HBB", "ALAS2", "GYPA"]:
                counts[cell_idx, gene_to_idx[gene]] += rng.poisson(5.0)
        else:
            counts[cell_idx, gene_to_idx["KLF1"]] += rng.poisson(1.0)
            counts[cell_idx, gene_to_idx["GATA1"]] += rng.poisson(3.0)
            for gene in ["HBB", "ALAS2", "GYPA"]:
                counts[cell_idx, gene_to_idx[gene]] += rng.poisson(2.0)
            for gene in ["SPI1", "LYZ"]:
                counts[cell_idx, gene_to_idx[gene]] += rng.poisson(2.0)

    obs = pd.DataFrame(index=[f"cell_{i:03d}" for i in range(n_cells)])
    obs["guide_identity"] = labels
    obs["perturbation_label"] = np.where(labels == RAW_KLF1, "KLF1", "negative_control")
    obs["target_gene"] = np.where(labels == RAW_KLF1, "KLF1", "negative_control")
    obs["control_pool"] = "NegCtrl0"
    obs["batch"] = ["batch1" if i < n_cells / 2 else "batch2" for i in range(n_cells)]
    obs["synthetic_state"] = "erythroid_like"

    var = pd.DataFrame(index=genes)
    adata = ad.AnnData(X=sparse.csr_matrix(counts), obs=obs, var=var)
    adata.uns["pertura_fixture"] = {
        "fixture_name": "synthetic_classic_perturbseq",
        "raw_klf1_label": RAW_KLF1,
        "raw_control_label": RAW_CONTROL,
        "guide_to_target_map": {"KLF1": "KLF1", "NegCtrl0": "negative_control"},
        "expected_signal": "KLF1 and erythroid marker genes are lower in KLF1 perturbation cells than in negative controls.",
    }

    h5ad_path = out_dir / "synthetic_classic_perturbseq.h5ad"
    adata.write_h5ad(h5ad_path)

    manifest = {
        "fixture_name": "synthetic_classic_perturbseq",
        "h5ad": h5ad_path.name,
        "n_cells": int(n_cells),
        "n_genes": len(genes),
        "guide_identity_column": "guide_identity",
        "raw_labels": [RAW_KLF1, RAW_CONTROL],
        "raw_klf1_label": RAW_KLF1,
        "raw_control_label": RAW_CONTROL,
        "guide_to_target_map": {"KLF1": "KLF1", "NegCtrl0": "negative_control"},
        "recommended_contrast": {
            "left": RAW_KLF1,
            "baseline": RAW_CONTROL,
            "estimand": "single_target_marginal",
        },
        "expected_signal": "KLF1 and erythroid marker genes are lower in KLF1 perturbation cells than in negative controls.",
        "purpose": "Pertura natural classic Perturb-seq smoke fixture for cell_state_reference -> measured_de -> claim_report.",
    }
    (out_dir / "fixture_manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {h5ad_path}")
    print(f"wrote {out_dir / 'fixture_manifest.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
