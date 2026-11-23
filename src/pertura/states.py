"""Cell states: label-free clusters fitted once per dataset, without perturbation labels, and frozen before any test.

Procedure: original cells (spike-in copies excluded); counts normalised to 10,000 per cell and log1p; the n_hvg most
variable genes (scanpy, flavour 'seurat'); scaled (clipped at 10); PCA (n_pcs); kNN graph (n_neighbors); Leiden
clustering (igraph) at the given resolution and seed. States are named S01, S02, ... by size. A copy takes its source
cell's state. Markers for interpretation: each state against the rest (scanpy Wilcoxon), top 30 genes.
A state set lives in <dataset dir>/states/<set id>/ (meta.json, labels.npy, markers.tsv) and is registered in the
dataset manifest under "states". Its ID hashes the dataset fingerprint, the parameters and STATES_VERSION.
"""
import hashlib
import json
import os

import numpy as np
import pandas as pd
import scipy.sparse as sp

from .data import Data

STATES_VERSION = "states/1"


def set_id(fingerprint, resolution, n_hvg, n_pcs, n_neighbors, seed):
    raw = json.dumps([STATES_VERSION, fingerprint, float(resolution), int(n_hvg), int(n_pcs), int(n_neighbors),
                      int(seed)])
    return f"leiden_r{resolution:g}_{hashlib.sha256(raw.encode()).hexdigest()[:8]}"


def embed(X, genes, n_hvg=2000, n_pcs=30, n_neighbors=15, seed=0):
    """The log-normalised cells (all genes, for markers) and the scaled highly variable genes with their PCA and
    kNN graph (none when n_neighbors is 0), from raw counts (cells x genes)."""
    import anndata as ad
    import scanpy as sc
    a = ad.AnnData(sp.csr_matrix(X, dtype=np.float32), var=pd.DataFrame(index=np.asarray(genes).astype(str)))
    sc.pp.normalize_total(a, target_sum=1e4)
    sc.pp.log1p(a)
    from .quality import variable_genes
    variable_genes(a, n_top_genes=min(n_hvg, a.n_vars), flavor="seurat")  # receptor segments left out (PF4)
    h = a[:, a.var["highly_variable"]].copy()
    sc.pp.scale(h, max_value=10)
    sc.tl.pca(h, n_comps=min(n_pcs, h.n_vars - 1, h.n_obs - 1), random_state=seed)
    if n_neighbors:
        sc.pp.neighbors(h, n_neighbors=n_neighbors, random_state=seed)
    return a, h


def leiden_states(h, resolution, seed=0):
    """Leiden clusters of an embedding (embed's second value), named S01, S02, ... by size."""
    import scanpy as sc
    sc.tl.leiden(h, resolution=float(resolution), random_state=seed, flavor="igraph", n_iterations=2,
                 directed=False, key_added="leiden")
    raw = h.obs["leiden"].astype(str).to_numpy()
    sizes = pd.Series(raw).value_counts()
    rename = {c: f"S{i + 1:02d}" for i, c in enumerate(sizes.index)}
    return np.array([rename[c] for c in raw])


def pseudotime(h, root, seed=0):
    """Diffusion pseudotime (scanpy's tl.diffmap and tl.dpt at their defaults) on an embedding with its kNN graph
    (embed's second value), from a root group of its cells (a mask). The root cell is the group's cell nearest the
    group's mean profile, as scanpy's xroot takes the cell nearest a given profile. Cells the graph does not join to
    the root have none (NaN)."""
    import scanpy as sc
    idx = np.flatnonzero(root)
    X = h.X[idx]
    X = X.toarray() if sp.issparse(X) else np.asarray(X)
    h.uns["iroot"] = int(idx[np.argmin(((X - X.mean(0)) ** 2).sum(1))])
    sc.tl.diffmap(h, random_state=seed)
    sc.tl.dpt(h)
    t = h.obs["dpt_pseudotime"].to_numpy(float)
    return np.where(np.isfinite(t), t, np.nan)


def state_markers(a, labels, n_genes=30):
    """Each state against the rest (Wilcoxon, scanpy), its top n_genes: state, names, logfoldchanges, pvals_adj, and
    the shares of cells expressing each inside the state and outside it (pct_nz_group, pct_nz_reference), which tell a
    state's own markers from genes every cell expresses."""
    import scanpy as sc
    a.obs["state"] = pd.Categorical(labels)
    sc.tl.rank_genes_groups(a, "state", method="wilcoxon", n_genes=n_genes, pts=True)
    return pd.concat([sc.get.rank_genes_groups_df(a, s).head(n_genes).assign(state=s)
                      for s in sorted(set(labels))])[["state", "names", "logfoldchanges", "pvals_adj",
                                                       "pct_nz_group", "pct_nz_reference"]]


def fit_states(manifest, resolutions, n_hvg=2000, n_pcs=30, n_neighbors=15, seed=0, out_root=None, register=True,
               log=print):
    """Fit one state set per resolution; write and (optionally) register them. Returns {set id: meta}."""
    data = Data(manifest)
    orig = data.originals()
    a, h = embed(data.X[orig], data.genes, n_hvg, n_pcs, n_neighbors, seed)
    root = out_root or os.path.join(os.path.dirname(os.path.abspath(data.manifest["h5ad"])), "states")
    copy_of = data.obs["copy_of"].astype(str).to_numpy() if "copy_of" in data.obs.columns else None
    pos_of = pd.Series(np.arange(data.X.shape[0]), index=data.obs.index.astype(str))
    sets = {}
    for r in resolutions:
        sid = set_id(data.fingerprint, r, n_hvg, n_pcs, n_neighbors, seed)
        lab_orig = leiden_states(h, r, seed)
        labels = np.full(data.X.shape[0], "", dtype=object)
        labels[orig] = lab_orig
        if copy_of is not None:
            cp = np.flatnonzero(copy_of != "")
            labels[cp] = labels[pos_of[copy_of[cp]].to_numpy()]
        markers = state_markers(a, lab_orig)
        d = os.path.join(root, sid)
        os.makedirs(d, exist_ok=True)
        np.save(f"{d}/labels.npy", labels.astype(str))
        markers.rename(columns={"names": "gene"}).to_csv(f"{d}/markers.tsv", sep="\t", index=False)
        names = sorted(set(lab_orig))
        meta = {"set_id": sid, "version": STATES_VERSION, "dataset": data.name, "fingerprint": data.fingerprint,
                "resolution": float(r), "n_hvg": int(h.n_vars), "n_pcs": int(h.obsm["X_pca"].shape[1]),
                "n_neighbors": int(n_neighbors), "seed": int(seed), "states": names,
                "sizes": {s: int((lab_orig == s).sum()) for s in names},
                "method": "Leiden (igraph) on a kNN graph of PCA of log-normalised highly variable genes"}
        with open(f"{d}/meta.json", "w") as fh:
            json.dump(meta, fh, indent=1)
        sets[sid] = meta
        log(f"{sid}: resolution {r}, {len(names)} states, sizes {list(meta['sizes'].values())}")
    if register:
        with open(manifest) as fh:
            m = json.load(fh)
        m.setdefault("states", {}).update({s: os.path.join(root, s) for s in sets})
        with open(manifest + ".tmp", "w") as fh:
            json.dump(m, fh, indent=1)
        os.replace(manifest + ".tmp", manifest)
    return sets


def load_labels(directory):
    """(state label per cell in dataset order, state names)."""
    with open(os.path.join(directory, "meta.json")) as fh:
        meta = json.load(fh)
    return np.load(os.path.join(directory, "labels.npy"), allow_pickle=False), list(meta["states"])
