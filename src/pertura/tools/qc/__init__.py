"""Cell QC tools. Every filter looks only at cells, never at groups.

- provided: the cells shipped with the data.
- strict: drop the lowest 5% of library sizes and the highest 5% of mitochondrial fractions. The cut-offs come from
  the original cells (metrics column 'original', if present), so spike-in copies do not move them.
- doublets: drop the cells scDblFinder calls doublets (pertura.doublets; available once fitted for the dataset).
- strict_doublets: both.
"""
import numpy as np
import pandas as pd

QC = ("provided", "strict")
DOUBLET_QC = ("doublets", "strict_doublets")


def cell_metrics(X, gene_names):
    """Per-cell library size and mitochondrial fraction (genes named mt-* or MT-*), from raw counts."""
    lib = np.asarray(X.sum(1)).ravel().astype(float)
    mt = np.array([str(g).lower().startswith("mt-") for g in gene_names])
    mito = np.asarray(X[:, np.flatnonzero(mt)].sum(1)).ravel() if mt.any() else np.zeros(X.shape[0])
    return pd.DataFrame({"library_size": lib, "mito_fraction": mito / np.maximum(lib, 1.0)})


def qc_keep(choice, metrics):
    if choice == "provided":
        return np.ones(len(metrics), bool)
    if choice in DOUBLET_QC:
        if "doublet" not in metrics.columns:
            raise ValueError("no doublet calls for this dataset (pertura.doublets.fit_doublets)")
        keep = ~metrics["doublet"].to_numpy(bool)
        return keep & qc_keep("strict", metrics) if choice == "strict_doublets" else np.array(keep, dtype=bool)
    if choice == "strict":
        basis = metrics[metrics["original"]] if "original" in metrics.columns else metrics
        low = np.quantile(basis["library_size"], 0.05)
        high = np.quantile(basis["mito_fraction"], 0.95)
        # a writable copy: with pandas copy-on-write, to_numpy() can return a read-only view
        return np.array((metrics["library_size"] >= low) & (metrics["mito_fraction"] <= high), dtype=bool)
    raise ValueError(f"unknown qc choice {choice!r}")
