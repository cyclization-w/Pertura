"""Gene programs: consensus NMF fitted once per dataset, without perturbation labels, and frozen before any test.

The procedure follows cNMF (Kotliar et al. 2019, eLife):
1. cells: the original cells (spike-in copies excluded), at most max_cells of them drawn at random;
2. genes: the n_hvg most variable genes (scanpy, flavour 'seurat', on log-normalised counts of those cells);
3. counts normalised to 10,000 per cell on those genes, each gene scaled to unit variance without centring;
4. NMF (scikit-learn, coordinate descent, Frobenius loss) with `replicates` random starts for each K;
5. the replicates' spectra, scaled to unit length, lose outliers (mean distance to their nearest neighbours above
   density_threshold) and are clustered by k-means into K clusters; each consensus spectrum is the median of its
   cluster. Stability = silhouette of that clustering;
6. usage of every cell (copies included) is refitted on the frozen consensus spectra and scaled to sum to 1;
7. gene scores for interpretation: z-scored log-normalised expression of every gene detected in at least 1% of
   original cells, regressed on usage (as cNMF's gene_spectra_score).

A program set lives in <dataset dir>/programs/<set id>/ (meta.json, spectra.tsv, usage.npy, gene_scores.tsv.gz,
top_genes.tsv) and is registered in the dataset manifest under "programs". Its ID hashes the dataset fingerprint, the
fitting parameters and PROGRAMS_VERSION, so analyses of programs are cached against exactly one fit.
"""
import hashlib
import json
import os

import numpy as np
import pandas as pd
import scipy.sparse as sp

from .data import Data

PROGRAMS_VERSION = "programs/1"


def set_id(fingerprint, k, replicates, n_hvg, max_cells, seed, density_threshold):
    raw = json.dumps([PROGRAMS_VERSION, fingerprint, int(k), int(replicates), int(n_hvg), int(max_cells), int(seed),
                      float(density_threshold)])
    return f"cnmf_k{int(k)}_{hashlib.sha256(raw.encode()).hexdigest()[:8]}"


def _normalised_hvg(X, n_hvg, genes=None):
    """Indices of the most variable genes and the scaled matrix (cells x genes, sparse) used for factorisation; with
    the genes' names, the receptor segments left out (PF4)."""
    import anndata as ad
    import scanpy as sc
    a = ad.AnnData(sp.csr_matrix(X, dtype=np.float32))
    if genes is not None:
        a.var_names = [str(g) for g in genes]
    sc.pp.normalize_total(a, target_sum=1e4)
    logged = a.copy()
    sc.pp.log1p(logged)
    n = min(int(n_hvg), a.n_vars)
    from .quality import variable_genes
    variable_genes(logged, n_top_genes=n, flavor="seurat")
    hvg = np.flatnonzero(logged.var["highly_variable"].to_numpy())
    M = sp.csr_matrix(a.X[:, hvg])
    sd = np.sqrt(np.asarray(M.multiply(M).mean(0)).ravel() - np.asarray(M.mean(0)).ravel() ** 2)
    sd[sd == 0] = 1.0
    return hvg, sp.csr_matrix(M @ sp.diags(1.0 / sd)), sd


def _scale_like(X, hvg, sd):
    """The factorisation scale for any cells: normalised to 10,000 counts on the chosen genes, divided by their sd."""
    import anndata as ad
    import scanpy as sc
    a = ad.AnnData(sp.csr_matrix(X, dtype=np.float32))
    sc.pp.normalize_total(a, target_sum=1e4)
    return sp.csr_matrix(sp.csr_matrix(a.X[:, hvg]) @ sp.diags(1.0 / sd))


def consensus(spectra, k, density_threshold=0.5, neighbours=0.3, seed=0):
    """Consensus spectra (k x genes, rows sum to 1) and silhouette from replicate spectra (rows = components)."""
    from sklearn.cluster import KMeans
    from sklearn.metrics import silhouette_score
    S = spectra / np.maximum(np.linalg.norm(spectra, axis=1, keepdims=True), 1e-12)
    n_nb = max(1, int(round(neighbours * len(S) / k)))
    d = np.sqrt(np.maximum(2.0 - 2.0 * (S @ S.T), 0))  # unit-length rows: |a - b|^2 = 2 - 2 a.b
    np.fill_diagonal(d, np.inf)
    density = np.sort(d, axis=1)[:, :n_nb].mean(1)
    keep = density <= density_threshold
    if keep.sum() < k:
        keep = np.argsort(density)[:max(k, int(0.5 * len(S)))]
        keep = np.isin(np.arange(len(S)), keep)
    km = KMeans(n_clusters=k, n_init=10, random_state=seed).fit(S[keep])
    sil = float(silhouette_score(S[keep], km.labels_)) if k > 1 else np.nan
    cons = np.vstack([np.median(S[keep][km.labels_ == j], axis=0) for j in range(k)])
    cons = np.maximum(cons, 0)
    cons /= np.maximum(cons.sum(1, keepdims=True), 1e-12)
    return cons, sil, float(keep.mean())


def refit_usage(M, H):
    """Non-negative usage (cells x k) of fixed spectra H for the scaled matrix M."""
    from sklearn.decomposition import non_negative_factorization
    W, _, _ = non_negative_factorization(M, H=H.astype(np.float64), n_components=H.shape[0], init="custom",
                                         update_H=False, solver="cd", beta_loss="frobenius", max_iter=1000,
                                         tol=1e-4, W=None)
    return W


def gene_scores(X, usage, mask):
    """OLS coefficients (k x genes) of z-scored log-normalised expression on usage (no intercept: usage rows sum to
    1 and z-scores are centred), as cNMF's gene_spectra_score."""
    import anndata as ad
    import scanpy as sc
    a = ad.AnnData(sp.csr_matrix(X[:, np.flatnonzero(mask)], dtype=np.float32))
    sc.pp.normalize_total(a, target_sum=1e4)
    sc.pp.log1p(a)
    L = sp.csr_matrix(a.X, dtype=np.float64)
    mu = np.asarray(L.mean(0)).ravel()
    sd = np.sqrt(np.maximum(np.asarray(L.multiply(L).mean(0)).ravel() - mu ** 2, 1e-12))
    U = np.asarray(usage, dtype=np.float64)
    UtZ = (np.asarray((L.T @ U).T) - np.outer(U.sum(0), mu)) / sd  # U' Z without forming Z
    return np.linalg.lstsq(U.T @ U, UtZ, rcond=None)[0]


def prepare(X, orig, n_hvg=2000, max_cells=20000, seed=0, genes=None):
    """Steps 1-3 for any counts matrix (cells x genes): the fitting cells (at most max_cells of the originals, drawn
    at random), the most variable genes (receptor segments left out where the genes are named), and the scaled
    matrices of the fitting cells and of every cell."""
    rng = np.random.default_rng(seed)
    fit_cells = np.sort(rng.choice(orig, size=min(max_cells, len(orig)), replace=False))
    hvg, M_fit, sd = _normalised_hvg(X[fit_cells], n_hvg, genes)
    return fit_cells, hvg, M_fit.astype(np.float64), _scale_like(X, hvg, sd).astype(np.float64)


def factorise(M_fit, M_all, fit_cells, orig, k, replicates=20, seed=0, density_threshold=0.5):
    """Steps 4-6 for one K: consensus spectra (numbered by mean usage), every cell's usage (rows sum to 1), the
    silhouette, the share of spectra kept, the refit error and the replicates' errors."""
    from sklearn.decomposition import NMF
    spectra, errors = [], []
    for r in range(replicates):
        model = NMF(n_components=int(k), init="random", solver="cd", beta_loss="frobenius", max_iter=1000,
                    tol=1e-4, random_state=int(seed) * 1000 + r)
        model.fit(M_fit)
        spectra.append(model.components_)
        errors.append(float(model.reconstruction_err_))
    cons, sil, kept = consensus(np.vstack(spectra), int(k), density_threshold, seed=seed)
    W = refit_usage(M_all, cons)
    resid = M_all[fit_cells] - sp.csr_matrix(W[fit_cells] @ cons)
    err = float(np.sqrt((resid.multiply(resid)).sum()))
    order = np.argsort(-W[orig].mean(0))  # programs numbered by mean usage
    cons, W = cons[order], W[:, order]
    usage = W / np.maximum(W.sum(1, keepdims=True), 1e-12)
    return cons, usage, sil, kept, err, errors


def fit_programs(manifest, ks, replicates=20, n_hvg=2000, max_cells=20000, seed=0, density_threshold=0.5,
                 out_root=None, register=True, log=print):
    """Fit one program set per K; write and (optionally) register them. Returns {set id: meta}."""
    data = Data(manifest)
    orig = data.originals()
    fit_cells, hvg, M_fit, M_all = prepare(data.X, orig, n_hvg, max_cells, seed)
    mask = data.detected(0.01)
    root = out_root or os.path.join(os.path.dirname(os.path.abspath(data.manifest["h5ad"])), "programs")
    sets = {}
    for k in ks:
        sid = set_id(data.fingerprint, k, replicates, n_hvg, max_cells, seed, density_threshold)
        cons, usage, sil, kept, err, errors = factorise(M_fit, M_all, fit_cells, orig, k, replicates, seed,
                                                        density_threshold)
        names = [f"K{int(k)}_{j + 1:02d}" for j in range(int(k))]
        scores = gene_scores(data.X[orig], usage[orig], mask)
        d = os.path.join(root, sid)
        os.makedirs(d, exist_ok=True)
        pd.DataFrame(cons, index=names, columns=data.genes[hvg]).to_csv(f"{d}/spectra.tsv", sep="\t")
        np.save(f"{d}/usage.npy", usage.astype(np.float32))
        gs = pd.DataFrame(scores, index=names, columns=data.genes[mask])
        gs.to_csv(f"{d}/gene_scores.tsv.gz", sep="\t", compression="gzip")
        top = [(p, i + 1, g, float(v)) for p in names for i, (g, v) in enumerate(gs.loc[p].nlargest(50).items())]
        pd.DataFrame(top, columns=["program", "rank", "gene", "score"]).to_csv(f"{d}/top_genes.tsv", sep="\t",
                                                                                index=False)
        meta = {"set_id": sid, "version": PROGRAMS_VERSION, "dataset": data.name, "fingerprint": data.fingerprint,
                "k": int(k), "replicates": int(replicates), "n_hvg": int(len(hvg)), "max_cells": int(max_cells),
                "fit_cells": int(len(fit_cells)), "seed": int(seed), "density_threshold": float(density_threshold),
                "silhouette": sil, "spectra_kept": kept, "refit_error": err,
                "replicate_error_median": float(np.median(errors)), "programs": names,
                "mean_usage": usage[orig].mean(0).round(4).tolist(),
                "method": "consensus NMF (scikit-learn coordinate descent, Frobenius loss), cNMF-style"}
        with open(f"{d}/meta.json", "w") as fh:
            json.dump(meta, fh, indent=1)
        sets[sid] = meta
        log(f"{sid}: K {k}, silhouette {sil:.3f}, spectra kept {kept:.2f}, refit error {err:.1f}")
    if register:
        register_sets(manifest, {s: os.path.join(root, s) for s in sets})
    return sets


def register_sets(manifest_path, sets):
    """Add program sets ({set id: directory}) to a dataset manifest."""
    with open(manifest_path) as fh:
        m = json.load(fh)
    m.setdefault("programs", {}).update(sets)
    tmp = manifest_path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(m, fh, indent=1)
    os.replace(tmp, manifest_path)


def load_usage(directory):
    """(usage matrix cells x programs in dataset cell order, program names)."""
    with open(os.path.join(directory, "meta.json")) as fh:
        meta = json.load(fh)
    return np.load(os.path.join(directory, "usage.npy")), list(meta["programs"])
