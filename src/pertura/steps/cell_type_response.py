"""Which cell types respond to a perturbation (docs/adr/0031, phase 2, batch C): pertpy's Augur (Skinnider et al.
2021), for screens of several cell types. Within each cell type, a random forest tells the target's cells from the
control cells over subsamples of 20 cells a side (50 subsamples, 3 folds, half the variable genes); its mean AUC
ranks the cell types by how far the perturbation moves them. A related measure, not a test: an AUC of 0.5 is no
separation, and the cell types are read against each other.

    per, calls = respond(screen, column="cell_type", targets=["T1"])
"""
import numpy as np
import pandas as pd

MIN_CELLS = 20  # Augur's subsample size: a cell type needs this many cells on each side


def respond(screen, column, targets=None, reference="control_guides", n_subsamples=50, subsample_size=20, seed=0):
    """Per target and cell type of `column`: Augur's mean AUC and the cells on each side. Returns (per, calls)."""
    import warnings
    import anndata as ad
    import pertpy as pt
    from ..question_modules import packages
    warnings.filterwarnings("ignore")
    if column not in screen.adata.obs:
        raise ValueError(f"no cell column {column!r}; the columns are {sorted(map(str, screen.adata.obs.columns))}")
    kind = screen.adata.obs[column].astype(str).to_numpy()
    rows = []
    for t in (targets if targets is not None else screen.targets(MIN_CELLS)):
        tm = screen.cells_of(t)
        rm = screen.reference(reference, target=t) & (screen.label != t)
        if screen.crossed:  # the control cells drawn within each sample, condition and cell type, in its mix
            from ..question_modules.data import same_sample_controls, seed_of
            within = np.char.add(np.char.add(np.asarray(screen.strata).astype(str), "|"), kind.astype(str))
            tm, rm, _ = same_sample_controls(within, tm, rm, subsample_size, seed_of(t))
        types = [k for k in sorted(set(kind[tm])) if (tm & (kind == k)).sum() >= subsample_size
                 and (rm & (kind == k)).sum() >= subsample_size]
        if not types:
            continue
        idx = np.flatnonzero((tm | rm) & np.isin(kind, types))
        a = ad.AnnData(screen.lognorm[idx].astype(np.float32),
                       # categorical columns: scikit-learn's cross-validation cannot index pandas' Arrow strings
                       obs=pd.DataFrame({"label": pd.Categorical(np.where(tm[idx], "target", "control")),
                                         "cell_type": pd.Categorical(kind[idx])}, index=[f"c{i}" for i in idx]),
                       var=pd.DataFrame(index=pd.Index(screen.genes).astype(str)))
        a.var_names_make_unique()
        ag = pt.tl.Augur("random_forest_classifier")
        loaded = ag.load(a, label_col="label", cell_type_col="cell_type")
        _, res = ag.predict(loaded, n_subsamples=n_subsamples, subsample_size=subsample_size, n_threads=1,
                            random_state=seed)
        auc = res["summary_metrics"].loc["mean_augur_score"]
        for k in types:
            rows.append({"target": t, "cell_type": k, "auc": float(auc.get(k, np.nan)),
                         "cells": int((tm & (kind == k)).sum()), "reference_cells": int((rm & (kind == k)).sum())})
    per = pd.DataFrame(rows, columns=["target", "cell_type", "auc", "cells", "reference_cells"])
    if len(per):
        per["rank_in_target"] = per.groupby("target")["auc"].rank(ascending=False, method="min").astype(int)
    calls = [packages.call("pertpy", "tl.Augur (random_forest_classifier): load, predict", layer="lognorm",
                           contrast="each target's cells against the control cells, within each cell type",
                           n_subsamples=n_subsamples, subsample_size=subsample_size, folds=3, random_state=seed)]
    return per, calls
