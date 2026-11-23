"""A synthetic in vivo screen for the trying loop's tests (ADR 0016 §10, phase 3): what the gates, practice decoys,
spikes and triggers need, which tests/synthetic_counts.py (6 controls, lanes) does not have.

Layout:
- 6 mice, independent units;
- 32 control constructs of two kinds (28 non-targeting, 4 safe-targeting), 120 cells each, spread over the mice;
  NTC_v07 is hot: it raises genes g20-g29 threefold, in one mouse, like a clonal artefact;
- 8 targets with 2 constructs each (180 cells per construct), clonal: 40% of a construct's cells sit in its home
  mouse (half of them in one mouse, counting chance), so the other mice still hold about 18 each (pseudobulk needs 3 shared units with 10 cells);
  - T01 and T02 raise g0-g9 threefold through both constructs, and knock their own gene down;
  - T03 raises g10-g19 through construct v2 only (a single-construct artefact);
  - T04-T08 are null, with their own gene knocked down;
- 80 genes: g0-g69, the targets' own genes (T01 ... T08 as gene names), and two mitochondrial genes.
"""
import json
import os

import numpy as np
import pandas as pd
import scipy.sparse as sp

MICE = [f"m{i}" for i in range(1, 7)]
TARGETS = [f"T0{i}" for i in range(1, 9)]
NT = [f"NTC_v{k:02d}" for k in range(1, 29)]
SAFE = [f"SAFE_v{k:02d}" for k in range(1, 5)]
HOT = "NTC_v07"
CELLS = 120
TARGET_CELLS = 180
CLONAL = 0.4
DESIGN = {"perturbation_col": "perturbation_id", "construct_col": "vector_id", "control_label": "control",
          "modality": "crispr_ko", "unit_col": "mouse", "unit_independent": True, "batch_col": "",
          "control_kind_col": "control_kind", "control_kinds": ["non_targeting", "safe_targeting"],
          "constructs_per_target": 2, "context": "in_vivo"}


def make_obs_and_counts(seed=0, clonal=CLONAL):
    rng = np.random.default_rng(seed)
    rows = []
    for c in NT + SAFE:
        kind = "non_targeting" if c in NT else "safe_targeting"
        mice = rng.choice(MICE, CELLS) if c != HOT else np.array([MICE[0]] * CELLS)
        rows += [("control", c, m, kind) for m in mice]
    for t in TARGETS:
        for v in (1, 2):
            home = MICE[(TARGETS.index(t) + v) % len(MICE)]
            mice = np.where(rng.random(TARGET_CELLS) < clonal, home, rng.choice(MICE, TARGET_CELLS))
            rows += [(t, f"{t}_v{v}", m, "") for m in mice]
    obs = pd.DataFrame(rows, columns=["perturbation_id", "vector_id", "mouse", "control_kind"])
    obs.index = [f"cell{i:05d}" for i in range(len(obs))]
    genes = [f"g{j}" for j in range(70)] + TARGETS + ["mt-Co1", "mt-Nd1"]
    base = rng.gamma(2.0, 2.0, len(genes))
    mouse_shift = {m: rng.normal(0, 0.15, len(genes)) for m in MICE}  # mice differ a little, as real animals do
    lam = np.vstack([base * np.exp(mouse_shift[m]) for m in obs["mouse"]])
    pert, vec = obs["perturbation_id"].to_numpy(), obs["vector_id"].to_numpy()
    up = np.isin(pert, ["T01", "T02"])
    lam[np.ix_(up, np.arange(10))] *= 3
    lam[np.ix_(vec == "T03_v2", np.arange(10, 20))] *= 3
    lam[np.ix_(vec == HOT, np.arange(20, 30))] *= 3
    for t in TARGETS:  # each target knocks down its own gene
        lam[pert == t, genes.index(t)] *= 0.3
    return obs, sp.csr_matrix(rng.poisson(lam).astype(np.int32)), genes


def write_dataset(root, seed=0, clonal=CLONAL):
    """screen.h5ad, design.json and manifest.json under root; returns the manifest path."""
    import anndata as ad
    from pertura.data import make_manifest
    os.makedirs(root, exist_ok=True)
    obs, X, genes = make_obs_and_counts(seed, clonal)
    ad.AnnData(X, obs=obs, var=pd.DataFrame(index=genes)).write_h5ad(f"{root}/screen.h5ad")
    with open(f"{root}/design.json", "w") as fh:
        json.dump({**DESIGN, "guide_counts": ""}, fh)
    make_manifest("synthetic_loop", f"{root}/screen.h5ad", f"{root}/design.json", out=f"{root}/manifest.json")
    return f"{root}/manifest.json"


def build_view(root, seed=0, clonal=CLONAL):
    """The dataset, its hidden-check universe (16 reference constructs, the rest a pool for decoys and spikes) and
    its exploration view. Returns (universe key, exploration key)."""
    from pertura_research.retired.exploration import build
    from pertura_research.retired.universe import build_universe
    src = write_dataset(f"{root}/source", seed=seed, clonal=clonal)
    ukey = build_universe(src, f"{root}/universe", seed=3, n_reference=16, min_reference_cells=200,
                          min_reference_per_unit=5, size_window=(0, 10000), n_spikes=2, spike_genes=10,
                          spike_log2fc=(-1.5, 1.5), spike_min_mean=0.5)
    return ukey, build(f"{root}/universe", f"{root}/exploration")
