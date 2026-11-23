"""Cell-level DE tool: Wilcoxon rank-sum test of each group, and of each construct, against the reference cells.

Same recipe and statistic as the construct-replication test (scanpy rank_genes_groups, method 'wilcoxon', reference
mode, no tie correction): keep genes detected in at least 1% (or 5%) of all original cells, normalise each cell to
10,000 counts on those genes (scanpy), log1p, then for each gene
    z = (R - n (n + m + 1) / 2) / sqrt(n m (n + m + 1) / 12),   p = 2 * (1 - Phi(|z|)),
where R is the group's rank sum among group and reference cells (average ranks), n and m the group and reference
sizes; log fold change = log2((expm1(mean group) + 1e-9) / (expm1(mean reference) + 1e-9)).

Version 2 computes R itself instead of calling rank_genes_groups: R = U + n (n + 1) / 2, with U counted against the
sorted reference values (zeros handled in bulk). The statistic is the same, it is about ten times faster, and groups
may share cells (research universes). Groups and constructs with fewer than 3 cells are not tested.
"""
import numpy as np
import pandas as pd
import scipy.sparse as sp
from scipy import stats

FRACTIONS = {"cells_1pct": 0.01, "cells_5pct": 0.05}


class SortedReference:
    """The reference cells' values, sorted per gene and laid end to end (gene j shifted by j * span), so one
    searchsorted call counts, for any group's values, the reference values below and equal to each of them."""

    def __init__(self, Xref):
        dense = np.asarray(Xref.todense() if sp.issparse(Xref) else Xref, dtype=np.float32)
        self.m, self.n_genes = dense.shape
        self.span = float(dense.max()) + 1.0 if dense.size else 1.0
        self.offsets = np.arange(self.n_genes, dtype=np.float64) * self.span
        self.flat = (np.sort(dense, axis=0).astype(np.float64) + self.offsets[None, :]).ravel(order="F")
        self.zeros = (dense == 0).sum(0).astype(np.float64)
        self.mean = dense.mean(0, dtype=np.float64)

    def u_stat(self, Xg):
        """The Mann-Whitney U of a group's cells (sparse CSR, cells x genes, non-negative values) against the
        reference, per gene: the pairs (group cell, reference cell) where the group's value is higher, ties counting
        one half. U is a sum over pairs, so the U of any subsets of cells follows from the U of their parts."""
        Xg = sp.csr_matrix(Xg)
        n, m = Xg.shape[0], self.m
        coo = Xg.tocoo()
        cols, vals = coo.col, coo.data.astype(np.float64) + self.offsets[coo.col]
        start = cols.astype(np.int64) * m
        below = np.searchsorted(self.flat, vals, side="left") - start
        upto = np.searchsorted(self.flat, vals, side="right") - start
        # float: with no non-zero value (a subset of cells and genes), bincount would return integers
        u = np.bincount(cols, weights=below + 0.5 * (upto - below), minlength=self.n_genes).astype(np.float64)
        nnz = np.bincount(cols, minlength=self.n_genes)
        u += 0.5 * (n - nnz) * self.zeros  # a group zero ties with every reference zero
        return u

    def test(self, Xg):
        """z, p and group means for a group's cells (sparse CSR, cells x genes, non-negative values)."""
        Xg = sp.csr_matrix(Xg)
        n, m = Xg.shape[0], self.m
        u = self.u_stat(Xg)
        r = u + n * (n + 1) / 2.0
        with np.errstate(divide="ignore", invalid="ignore"):
            z = (r - n * (n + m + 1) / 2.0) / np.sqrt(n * m * (n + m + 1) / 12.0)
        z[np.isnan(z)] = 0
        p = 2 * stats.norm.sf(np.abs(z))
        mean = np.asarray(Xg.sum(0), dtype=np.float64).ravel() / n
        return z, p, mean


class CellWilcoxon:
    name = "wilcoxon"
    claim_type = "gene_de"
    unit_kind = "cells"
    environment = "python"
    packages = ("scanpy", "anndata", "numpy", "scipy")
    version = "cell_wilcoxon/2"

    def run(self, spec, data, gs, workdir):
        import anndata as ad
        import scanpy as sc
        mask = data.detected(FRACTIONS[spec["gene_filter"]])
        genes = data.genes[mask]
        cells = np.unique(np.concatenate([gs.reference] + list(gs.groups.values())))
        a = ad.AnnData(sp.csr_matrix(data.X[cells][:, np.flatnonzero(mask)], dtype=np.float32),
                       var=pd.DataFrame(index=genes))
        sc.pp.normalize_total(a, target_sum=1e4)
        sc.pp.log1p(a)
        L = sp.csr_matrix(a.X)
        where = pd.Series(np.arange(len(cells)), index=cells)
        ref = SortedReference(L[where[gs.reference].to_numpy()])
        items = [(g, "all", pos) for g, pos in gs.groups.items()] + \
                [(gs.home[c], c, pos) for c, pos in gs.constructs.items()]
        parts = []
        for g, c, pos in items:
            z, p, mean = ref.test(L[where[pos].to_numpy()])
            lfc = np.log2((np.expm1(mean) + 1e-9) / (np.expm1(ref.mean) + 1e-9))
            parts.append(pd.DataFrame({"group": g, "construct": c, "feature": genes, "effect": lfc, "p": p}))
        cols = ["group", "construct", "feature", "effect", "p"]
        return pd.concat(parts, ignore_index=True)[cols] if parts else pd.DataFrame(columns=cols)
