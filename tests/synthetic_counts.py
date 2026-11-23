"""Synthetic screen with counts for the execution-layer tests.

Layout: 4 lanes, 6 control constructs (60 cells each), and 6 groups with 2 constructs each (30 cells per construct).
- P01 raises genes g0-g9 fourfold through both constructs.
- P02 raises them through construct v2 only, a single-construct artefact like a clonal expansion.
- The other groups are null.
Guide counts give each cell 4-14 UMIs on each of its own construct's two sgRNAs, plus sparse ambient UMIs. In 5% of
cells another construct is as strong, so re-calling drops them.
"""
import json
import os

import numpy as np
import pandas as pd
import scipy.sparse as sp

GROUPS = [f"P0{i}" for i in range(1, 7)]
LANES = ["L1", "L2", "L3", "L4"]
EFFECT_GENES = [f"g{j}" for j in range(10)]


def make_obs_and_counts(seed=0, n_genes=60, cells_per_construct=30, control_constructs=6):
    rng = np.random.default_rng(seed)
    rows = []
    for k in range(1, control_constructs + 1):
        rows += [("control", f"NTC_v{k}")] * (2 * cells_per_construct)
    for g in GROUPS:
        for v in (1, 2):
            rows += [(g, f"{g}_v{v}")] * cells_per_construct
    obs = pd.DataFrame(rows, columns=["perturbation_id", "vector_id"])
    obs["lane"] = rng.choice(LANES, len(obs))
    obs["batch"] = np.where(obs["lane"].isin(["L1", "L2"]), "b1", "b2")
    obs.index = [f"cell{i:05d}" for i in range(len(obs))]
    genes = [f"g{j}" for j in range(n_genes - 2)] + ["mt-Co1", "mt-Nd1"]
    lam = np.tile(rng.gamma(2.0, 2.0, n_genes), (len(obs), 1))
    up = ((obs["perturbation_id"] == "P01") | (obs["vector_id"] == "P02_v2")).to_numpy()
    lam[np.ix_(up, np.arange(10))] *= 4
    return obs, sp.csr_matrix(rng.poisson(lam).astype(np.int32)), genes


def make_guide_counts(obs, seed=1, ambiguous=0.05):
    rng = np.random.default_rng(seed)
    constructs = sorted(obs["vector_id"].unique())
    names = [f"{c}_s{k}" for c in constructs for k in (1, 2)]
    col = {n: i for i, n in enumerate(names)}
    G = np.zeros((len(obs), len(names)), np.int32)
    own = obs["vector_id"].to_numpy()
    for i, c in enumerate(own):
        G[i, col[f"{c}_s1"]] = rng.integers(4, 15)
        G[i, col[f"{c}_s2"]] = rng.integers(4, 15)
    amb = np.flatnonzero(rng.random(len(obs)) < ambiguous)
    for i in amb:
        other = rng.choice([c for c in constructs if c != own[i]])
        G[i, col[f"{other}_s1"]] = G[i].sum()
    G += rng.binomial(1, 0.02, G.shape).astype(np.int32)
    return sp.csr_matrix(G), names, set(obs.index[amb])


DESIGN = {"perturbation_col": "perturbation_id", "construct_col": "vector_id", "control_label": "control",
          "modality": "crispr_ko", "unit_col": "lane", "unit_independent": False, "batch_col": "batch",
          "constructs_per_target": 2}


def write_dataset(root, seed=0, n_genes=60):
    """screen.h5ad, guide_counts.h5ad, design.json and manifest.json under root; returns the manifest path."""
    import anndata as ad
    from pertura.data import make_manifest
    os.makedirs(root, exist_ok=True)
    obs, X, genes = make_obs_and_counts(seed, n_genes=n_genes)
    G, names, _ = make_guide_counts(obs)
    ad.AnnData(X, obs=obs, var=pd.DataFrame(index=genes)).write_h5ad(f"{root}/screen.h5ad")
    ad.AnnData(G, obs=pd.DataFrame(index=obs.index), var=pd.DataFrame(index=names)).write_h5ad(f"{root}/guide_counts.h5ad")
    with open(f"{root}/design.json", "w") as fh:
        json.dump({**DESIGN, "guide_counts": f"{root}/guide_counts.h5ad"}, fh)
    make_manifest("synthetic", f"{root}/screen.h5ad", f"{root}/design.json", f"{root}/guide_counts.h5ad",
                  out=f"{root}/manifest.json")
    return f"{root}/manifest.json"
