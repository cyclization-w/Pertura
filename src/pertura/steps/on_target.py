"""The target gene's change (docs/adr/0031, D1a): a target's own gene in some of its cells (a guide's, a label's, a
pair's) against the reference cells, by one of the formulas the modules use, written once:
- pooled_cpm (the default; B.efficacy, B.dose, a finding's guides): the log2 fold change of pooled counts per
  million, log2((CPM_cells + 1) / (CPM_reference + 1)), where CPM is the gene's counts over the cells' total counts;
- mean_cp10k (E.opposite_modes, E.combinations): log2((mean CP10K_cells + 0.1) / (mean CP10K_reference + 0.1)), the
  mean of each cell's counts per 10,000, as Replogle et al. 2022 read knockdown (the ratio of mean normalised
  expression);
- detection (B.escape): the share of the cells detecting the gene minus the reference's share.

The cells and the reference are masks the caller chooses by the design (the target's arm, the same units).

    value = change(screen, gene, cells, reference_cells, method="pooled_cpm")
"""
import numpy as np

METHODS = ("pooled_cpm", "mean_cp10k", "detection")
PSEUDO_CPM = 1.0       # pooled_cpm's pseudocount, in counts per million
PSEUDO_CP10K = 0.1     # mean_cp10k's pseudocount, in counts per 10,000


def change(screen, gene, cells, reference, method="pooled_cpm"):
    """The gene's change in the cells against the reference by one formula; NaN where the gene is not in the matrix
    or a side has no cell."""
    if method not in METHODS:
        raise ValueError(f"no on-target formula {method!r}; the formulas are {list(METHODS)}")
    x = screen.gene(gene) if gene else None
    cells, reference = np.asarray(cells, bool), np.asarray(reference, bool)
    if x is None or not cells.any() or not reference.any():
        return np.nan
    if method == "detection":
        return float((x[cells] > 0).mean() - (x[reference] > 0).mean())
    if method == "pooled_cpm":
        def cpm(m):
            return x[m].sum() / max(screen.libsize[m].sum(), 1) * 1e6
        return float(np.log2((cpm(cells) + PSEUDO_CPM) / (cpm(reference) + PSEUDO_CPM)))
    cp10k = x / np.maximum(screen.libsize, 1) * 1e4
    return float(np.log2((cp10k[cells].mean() + PSEUDO_CP10K) / (cp10k[reference].mean() + PSEUDO_CP10K)))
