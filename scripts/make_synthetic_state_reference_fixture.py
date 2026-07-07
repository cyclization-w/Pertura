from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np


MARKER_GENES = {
    "state_a": ["GATA1", "KLF1", "HBB", "ALAS2"],
    "state_b": ["SPI1", "LYZ", "MPO", "ELANE"],
    "state_c": ["IRF8", "CTSS", "LST1", "TYROBP"],
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create a small synthetic AnnData fixture for Pertura cell_state_reference smoke tests.")
    parser.add_argument("--out", type=Path, default=Path("fixtures/synthetic_state_reference"), help="Output fixture directory.")
    parser.add_argument("--n-cells", type=int, default=90)
    parser.add_argument("--n-background-genes", type=int, default=24)
    args = parser.parse_args(argv)

    try:
        import anndata as ad
        import pandas as pd
        from scipy import sparse
    except ModuleNotFoundError as exc:
        raise SystemExit(
            "This fixture generator requires the omics dependencies. Install with `python -m pip install -e .[omics]` "
            "or run it inside the pertura environment that contains anndata/pandas/scipy."
        ) from exc

    out_dir = args.out
    out_dir.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(20260706)

    marker_names = [gene for genes in MARKER_GENES.values() for gene in genes]
    background = [f"BG{i:02d}" for i in range(args.n_background_genes)]
    genes = marker_names + background
    states = np.array(["state_a", "state_b", "state_c"] * (args.n_cells // 3))
    if states.size < args.n_cells:
        states = np.concatenate([states, np.array(["state_a"] * (args.n_cells - states.size))])
    rng.shuffle(states)

    counts = rng.poisson(1.0, size=(args.n_cells, len(genes))).astype(float)
    gene_to_idx = {gene: idx for idx, gene in enumerate(genes)}
    for cell_idx, state in enumerate(states):
        for gene in MARKER_GENES[state]:
            counts[cell_idx, gene_to_idx[gene]] += rng.poisson(5.0)

    obs = pd.DataFrame(
        {
            "synthetic_state": states,
            "batch": ["batch1" if i < args.n_cells / 2 else "batch2" for i in range(args.n_cells)],
        },
        index=[f"cell_{i:03d}" for i in range(args.n_cells)],
    )
    var = pd.DataFrame(index=genes)
    adata = ad.AnnData(X=sparse.csr_matrix(counts), obs=obs, var=var)
    adata.uns["pertura_fixture"] = {
        "fixture_name": "synthetic_state_reference",
        "expected_state_column": "synthetic_state",
        "marker_genes": MARKER_GENES,
    }

    h5ad_path = out_dir / "synthetic_state_reference.h5ad"
    adata.write_h5ad(h5ad_path)

    manifest = {
        "fixture_name": "synthetic_state_reference",
        "h5ad": str(h5ad_path.name),
        "n_cells": int(args.n_cells),
        "n_genes": len(genes),
        "state_column": "synthetic_state",
        "states": sorted(set(states.tolist())),
        "marker_genes": MARKER_GENES,
        "purpose": "Pertura cell_state_reference stage capability smoke fixture",
    }
    (out_dir / "fixture_manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    print(f"wrote {h5ad_path}")
    print(f"wrote {out_dir / 'fixture_manifest.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())