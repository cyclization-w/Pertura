"""The response step: each cell's response to its perturbation (docs/adr/0031, phase 2).

The plumbing is here once:
- the cells: single-target and control cells; a target with fewer than MIN_GROUP cells in a sample is not called in
  that sample (the marker test inside the methods cannot run there), and those cells are named as not called;
- the embedding: scanpy's top 2,000 genes by dispersion and their PCA, on log-normalised values;
- the samples as the methods' split, where the cells span two or more;
- per cell its class or score, per target a summary, and the package calls.

The backends, each pertpy's port of the Satija lab's method with its defaults:
- mixscape (Papalexi et al. 2021): the perturbation signature (each cell minus its nearest control cells, within its
  sample), then each target's cells called perturbed or not perturbed by a mixture model, with the posterior;
- mixscale (Jiang, Dalgarno et al. 2025): the same signature projected on each target's direction over its top DE
  genes, standardised against the control cells: a continuous score per cell (control cells 0; a target with too
  few DE genes is not scored, its cells 1).

    cells, per, calls = respond(screen, method="mixscape")
"""
import numpy as np
import pandas as pd
import scipy.sparse as sp

MIN_GROUP = 3  # Seurat's FindMarkers minimum group size (min.cells.group), which the methods' marker step uses
MIN_CONTROLS = 20  # control cells a sample needs: the perturbation signature takes the 20 nearest (pertpy's default)
METHODS = {
    "mixscape": {"min_de_genes": (5, "DE genes a target needs to be split"),
                 "logfc_threshold": (0.25, "the DE genes' minimum log fold change"),
                 "pval_cutoff": (0.05, "the DE genes' adjusted p"),
                 "iter_num": (10, "normalmixEM iterations"),
                 "perturbation_type": ("KO", "the name of the perturbed class")},
    "mixscale": {"min_de_genes": (5, "DE genes a target needs to be scored"),
                 "max_de_genes": (100, "the top DE genes the direction uses"),
                 "logfc_threshold": (0.25, "the DE genes' minimum log fold change"),
                 "pval_cutoff": (0.05, "the DE genes' adjusted p"),
                 "fine_mode": (False, "DE genes per guide, pooled per target")},
}


def check_params(method, params):
    if method not in METHODS:
        raise ValueError(f"no response method {method!r}; the methods are {sorted(METHODS)}")
    unknown = sorted(set(params or {}) - set(METHODS[method]))
    if unknown:
        raise ValueError(f"no setting {unknown} for {method}; its settings are {sorted(METHODS[method])}")


def respond(screen, method="mixscape", n_pcs=40, seed=0, params=None):
    """Each cell's response by the method. Returns (cells, per, calls): cells (barcode, label, class or score), per
    target its summary, the package calls. Where the design matches references to guide attributes, each arm is cut
    at the entry (steps.genes.arms) and run on its own, its targets against its own control cells (pertpy is given
    the label "control": every control cell of the screen it is given; bug register, BR35)."""
    if getattr(screen, "reference_matches", None):
        from .genes import arms
        parts, without = [], []
        for key, members, part in arms(screen, screen.targets(1), "control_guides"):
            if not members:
                continue
            if not (part.label == "control").any():  # an arm with no control cells of its own is not run, said so
                without += list(members)
                continue
            parts.append(respond_one(part, method, n_pcs, seed, params))
        if parts:
            cells = pd.concat([p[0] for p in parts], ignore_index=True).drop_duplicates("barcode", keep="first")
            per = pd.concat([p[1] for p in parts], ignore_index=True)
            sort = "share_perturbed" if "share_perturbed" in per else "median_score"
            calls = [c for p in parts for c in p[2]]  # every arm's package calls
            per = per.sort_values(sort, ascending=False)
            if without:  # listed after the others, with why
                gone = pd.DataFrame({per.columns[0]: without, "not_run": "its arm has no control cells"})
                per = pd.concat([per, gone], ignore_index=True)
            return cells, per.reset_index(drop=True), calls
    return respond_one(screen, method, n_pcs, seed, params)


def respond_one(screen, method="mixscape", n_pcs=40, seed=0, params=None):
    """respond on one arm, or on a screen that matches no attribute."""
    import anndata as ad
    import pertpy as pt
    import scanpy as sc
    from ..question_modules import packages
    params = dict(params or {})
    check_params(method, params)
    keep = screen.single() | (screen.label == "control")
    every = np.flatnonzero(keep)
    # each sample, and each of its conditions where it holds several (Screen.strata): a cell's signature is read against
    # control cells of its own sample and condition
    within = np.asarray(screen.strata).astype(str)
    pair = pd.Series([f"{lab}\t{u}" for lab, u in zip(screen.label[every], within[every])])
    few = ((screen.label[every] != "control") & (pair.map(pair.value_counts()).to_numpy() < MIN_GROUP))
    # a sample with fewer control cells than the signature's neighbours: its cells cannot be set against their own
    # sample's controls, so they are not called (the package would search no control cell there)
    sample_of = pd.Series(within[every])
    controls_in = sample_of[screen.label[every] == "control"].value_counts()
    few |= sample_of.map(controls_in).fillna(0).to_numpy() < MIN_CONTROLS
    idx = every[~few]
    L = screen.lognorm[idx]
    split = len(set(within[idx])) > 1
    calls = [packages.call("scanpy", "pp.highly_variable_genes, pp.pca", data=L, layer="lognorm",
                           n_top_genes=min(2000, L.shape[1]), n_comps=n_pcs, random_state=seed)]
    a = ad.AnnData(sp.csr_matrix(L), obs=pd.DataFrame({"pert": screen.label[idx], "sample": within[idx],
                                                       "guide_id": np.asarray(screen.top_guide)[idx].astype(str)},
                                                      index=[f"c{i}" for i in idx]),
                   var=pd.DataFrame(index=pd.Index(screen.genes).astype(str)))
    a.var_names_make_unique()
    from ..quality import variable_genes
    variable_genes(a, n_top_genes=min(2000, a.n_vars))  # receptor segments left out (PF4)
    sc.pp.pca(a, n_comps=max(2, min(n_pcs, a.n_obs - 1, int(a.var["highly_variable"].sum()) - 1)),
              mask_var="highly_variable", random_state=seed)
    names = np.asarray(screen.adata.obs_names).astype(str)
    by = "sample" if split else None
    if method == "mixscape":
        ms = pt.tl.Mixscape()
        ms.perturbation_signature(a, "pert", "control", split_by=by)
        settings = {"perturbation_type": "KO", **params}
        ms.mixscape(a, "pert", "control", split_by=by, random_state=seed, **settings)
        calls.append(packages.call("pertpy", "tl.Mixscape.perturbation_signature, tl.Mixscape.mixscape",
                                   contrast="each target's cells against the control cells",
                                   covariates=["sample"] if split else [], split_by=by, **settings))
        glob = a.obs["mixscape_class_global"].astype(str).to_numpy()
        cls = np.where(glob == settings["perturbation_type"], "perturbed",
                       np.where(glob == "NP", "not perturbed", "control"))
        cells = pd.concat([pd.DataFrame({"barcode": names[idx], "label": screen.label[idx], "class": cls}),
                           pd.DataFrame({"barcode": names[every[few]], "label": screen.label[every[few]],
                                         "class": "not called"})], ignore_index=True)
        per = (cells[cells["label"] != "control"].groupby("label")["class"]
               .agg(cells="size", perturbed=lambda c: int((c == "perturbed").sum()),
                    not_called=lambda c: int((c == "not called").sum())).reset_index()
               .rename(columns={"label": "target"}))
        per["share_perturbed"] = per["perturbed"] / (per["cells"] - per["not_called"]).clip(lower=1)
        return cells, per.sort_values("share_perturbed", ascending=False).reset_index(drop=True), calls
    ms = pt.tl.Mixscale()
    ms.perturbation_signature(a, "pert", "control", split_by=by)
    ms.mixscale(a, "pert", "control", split_by=by, random_state=seed, **params)
    calls.append(packages.call("pertpy", "tl.Mixscale.perturbation_signature, tl.Mixscale.mixscale",
                               contrast="each target's cells against the control cells",
                               covariates=["sample"] if split else [], split_by=by, **params))
    score = a.obs["mixscale_score"].to_numpy(dtype=float)
    de = a.uns.get("mixscale_de_genes", {}) or {}
    cells = pd.concat([pd.DataFrame({"barcode": names[idx], "label": screen.label[idx], "score": score}),
                       pd.DataFrame({"barcode": names[every[few]], "label": screen.label[every[few]],
                                     "score": np.nan})], ignore_index=True)
    t = cells[cells["label"] != "control"]
    per = t.groupby("label")["score"].agg(cells="size", median_score="median", mean_score="mean").reset_index()
    per = per.rename(columns={"label": "target"})
    per["not_called"] = per["target"].map(t.assign(n=t["score"].isna()).groupby("label")["n"].sum()).astype(int)
    per["de_genes"] = per["target"].map(lambda x: 0 if de.get(x) is None else int(np.size(de.get(x))))
    per["scored"] = per["de_genes"] >= int(params.get("min_de_genes", 5))
    return cells, per.sort_values("median_score", ascending=False).reset_index(drop=True), calls
