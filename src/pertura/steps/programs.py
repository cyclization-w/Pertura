"""The programs step: gene programs found in the screen's cells (docs/adr/0031, D1a).

One interface for every method:

    usage, genes, about, calls = discover(screen, method, workdir, k=None, fit_on="all", seed=0)

- usage: cells by programs, each cell's value for each program (a usage summing to 1 over a cell's programs where
  the method factorises the cells; a score where it gives gene sets);
- genes: program, rank, gene, weight: each program's top genes by the method's own weight;
- about: the K and how it was chosen, the parameters, the cells the programs were found on;
- calls: the package calls.

fit_on: 'all' finds the programs on every cell; 'controls' on the control cells only, where no perturbation acts,
with every cell then read on the fixed programs (papers that define programs in non-targeting cells first, then read
the perturbed cells on them).

The methods are the field's packages, each with its defaults:
- cnmf: cNMF (Kotliar et al. 2019; the Perturb-seq papers of Schnitzler 2024 and Ota 2025, and PerturbNMF), K the
  most stable in a range unless given; fixed programs read by cNMF's own refit;
- sklearn_nmf: scikit-learn's NMF on the log-normalised counts of 2,000 highly variable genes (scanpy), K given;
- genenmf: GeneNMF's runNMF (R; Carmona lab; RcppML underneath), K given; fixed programs read by RcppML's project;
- genenmf_meta: GeneNMF's multiNMF on each sample's cells at k 4 to 9 and getMetaPrograms: the programs that recur
  across samples and k, as gene sets; each cell scored on each meta-program's genes by scanpy's score_genes.
"""
import gzip
import json
import os
import subprocess

import numpy as np
import pandas as pd
import scipy.sparse as sp

METHODS = {}
DEFAULT_K = 10  # GeneNMF's runNMF default, used where a method needs a K and none is given
MIN_FIT_CELLS = 50
CNMF_COMPONENTS = (5, 7, 9, 11, 13, 15)


def method(name, needs, k, params=None):
    """Register a method: needs is what it reads, k how its number of programs is set, params the package's
    settings a call may change: {name: (the package's default, what it does)}."""
    def deco(fn):
        METHODS[name] = {"fn": fn, "needs": needs, "k": k, "params": dict(params or {})}
        return fn
    return deco


def fit_cells(screen, fit_on):
    """The cells the programs are found on: every cell, or the control cells."""
    if fit_on == "all":
        return np.ones(len(screen.label), bool)
    if fit_on == "controls":
        m = screen.label == "control"
        if m.sum() < MIN_FIT_CELLS:
            raise ValueError(f"fit_on controls needs {MIN_FIT_CELLS} or more control cells; this screen has "
                             f"{int(m.sum())}")
        return m
    raise ValueError(f"fit_on is all or controls, not {fit_on!r}")


def discover(screen, method="cnmf", workdir=".", k=None, fit_on="all", seed=0, **params):
    """Each cell's programs by one method (see the module's docstring): (usage, genes, about, calls)."""
    if method not in METHODS:
        raise ValueError(f"no programs method {method!r}; the methods are {sorted(METHODS)}")
    unknown = sorted(set(params) - set(METHODS[method]["params"]))
    if unknown:  # a setting that would change nothing is refused, not ignored
        raise ValueError(f"no setting {unknown} for {method}; its settings are {sorted(METHODS[method]['params'])}")
    fit = fit_cells(screen, fit_on)
    os.makedirs(workdir, exist_ok=True)
    usage, genes, about, calls = METHODS[method]["fn"](screen, fit, workdir, k, seed, **params)
    about = {**about, "method": method, "fit_on": fit_on, "fitted_cells": int(fit.sum()),
             "programs": int(usage.shape[1]), "settings_changed": dict(params)}
    return usage, genes, about, calls


def _barcodes(screen):
    return np.asarray(screen.adata.obs_names).astype(str)


def _h5ad(screen, mask, path):
    import anndata as ad
    ad.AnnData(sp.csr_matrix(screen.X[np.flatnonzero(mask)], dtype=np.float32),
               obs=pd.DataFrame(index=_barcodes(screen)[mask]),
               var=pd.DataFrame(index=pd.Index(screen.genes).astype(str))).write_h5ad(path)


@method("cnmf", needs="raw counts", k="the most stable of 5 to 15 (odd), or given",
        params={"components": (CNMF_COMPONENTS, "the K range tried (only the K given, where one is given)"),
                "n_iter": (100, "factorisations per K"),
                "density": (0.5, "the local density threshold filtering outlier spectra (2.0: none)")})
def _cnmf(screen, fit, workdir, k, seed, components=None, n_iter=100, density=0.5):
    from .. import envs
    from ..question_modules import packages
    # a K given is the only K factorised, unless a range is given too (which must hold it)
    components = tuple(components) if components else ((int(k),) if k else CNMF_COMPONENTS)
    if k and int(k) not in components:
        raise ValueError(f"cnmf: k {k} is not among the components tried {list(components)}")
    counts = os.path.join(workdir, "counts.h5ad")
    _h5ad(screen, fit, counts)
    args = [os.path.join(workdir, "cnmf"), counts, ",".join(str(c) for c in components), str(n_iter), str(seed),
            str(int(k or 0)), str(density)]
    if not fit.all():  # the fixed programs read on every cell
        every = os.path.join(workdir, "every_cell.h5ad")
        _h5ad(screen, np.ones(len(fit), bool), every)
        args.append(every)
    os.makedirs(args[0], exist_ok=True)
    runner = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools", "programs",
                          "cnmf_runner.py")
    # the factorisations in as many processes as this one has threads (cNMF's own parallel mode)
    from .. import limits
    p = subprocess.run([envs.cnmf_python(), runner, *args], capture_output=True, text=True,
                       env={**os.environ, "PERTURA_CNMF_WORKERS": str(limits.own_threads())})
    if p.returncode != 0:
        raise RuntimeError(f"cNMF failed: {p.stderr[-1500:]}")
    work = args[0]
    usage = pd.read_csv(os.path.join(work, "usage.tsv"), sep="\t", index_col=0)
    usage.index = usage.index.astype(str)
    usage = usage.reindex(_barcodes(screen)).fillna(0.0)
    top = pd.read_csv(os.path.join(work, "top_genes.tsv"), sep="\t").rename(columns={"score": "weight",
                                                                                   "rank": "gene_rank"})
    stats_k = pd.read_csv(os.path.join(work, "k_stats.tsv"), sep="\t")
    with open(os.path.join(work, "chosen.json"), encoding="utf-8") as fh:
        chosen = json.load(fh)
    call = packages.call("cnmf", "cNMF prepare, factorize, combine, consensus" +
                         (", refit_usage on every cell" if not fit.all() else ""), data=screen.X,
                         layer="raw_counts", components=list(components), n_iter=n_iter, k=chosen["k"],
                         density_threshold=density, num_highvar_genes=2000)
    about = {"k": chosen["k"], "k_given": chosen["given"], "k_rule": "the most stable K (silhouette) in the range"
             if not chosen["given"] else "given", "k_stats": stats_k.round(4).to_dict("records"),
             "density_threshold": density, "n_iter": n_iter}
    return usage, top[["program", "gene_rank", "gene", "weight"]], about, [call]


@method("sklearn_nmf", needs="log-normalised counts", k=f"given (default {DEFAULT_K})",
        params={"n_top_genes": (2000, "highly variable genes factorised (scanpy, flavor seurat)"),
                "max_iter": (500, "iterations at most"),
                "beta_loss": ("frobenius", "the divergence minimised (frobenius or kullback-leibler, with mu)"),
                "init": ("nndsvda", "the initialisation")})
def _sklearn_nmf(screen, fit, workdir, k, seed, n_top_genes=2000, max_iter=500, beta_loss="frobenius",
                 init="nndsvda"):
    import anndata as ad
    import scanpy as sc
    from sklearn.decomposition import NMF
    from ..question_modules import packages
    given, k = k is not None, int(k or DEFAULT_K)
    L = screen.lognorm
    a = ad.AnnData(L[np.flatnonzero(fit)], var=pd.DataFrame(index=pd.Index(screen.genes).astype(str)))
    a.var_names_make_unique()
    from ..quality import variable_genes
    variable_genes(a, n_top_genes=min(n_top_genes, a.n_vars), flavor="seurat")  # receptor segments left out (PF4)
    hvg = np.flatnonzero(a.var["highly_variable"].to_numpy())
    solver = "mu" if beta_loss != "frobenius" else "cd"
    model = NMF(n_components=k, init=init, random_state=seed, max_iter=max_iter, beta_loss=beta_loss, solver=solver)
    model.fit(L[np.flatnonzero(fit)][:, hvg])
    W = model.transform(L[:, hvg])
    names = [f"P{j + 1:02d}" for j in range(k)]
    usage = pd.DataFrame(W / np.maximum(W.sum(1, keepdims=True), np.finfo(float).eps), index=_barcodes(screen),
                         columns=names)
    rows = []
    for j, n in enumerate(names):
        order = np.argsort(-model.components_[j])[:50]
        rows += [{"program": n, "gene_rank": r + 1, "gene": str(screen.genes[hvg[i]]),
                  "weight": float(model.components_[j, i])} for r, i in enumerate(order)]
    call = packages.call("scikit-learn", "decomposition.NMF", data=L, layer="lognorm", n_components=k,
                         init=init, random_state=seed, max_iter=max_iter, beta_loss=beta_loss, solver=solver,
                         genes=f"{len(hvg)} highly variable genes (scanpy, flavor seurat)")
    about = {"k": k, "k_given": given, "k_rule": "given" if given else f"the default, {DEFAULT_K}",
             "reconstruction_error": float(model.reconstruction_err_), "iterations": int(model.n_iter_)}
    return usage, pd.DataFrame(rows), about, [call]


def _genenmf_folder(screen, fit, workdir, params):
    """The counts of every cell (genes by cells), the cells with their sample and whether they are fitted, and the
    parameters, for genenmf_programs.R."""
    import scipy.io
    os.makedirs(workdir, exist_ok=True)
    with gzip.open(os.path.join(workdir, "counts.mtx.gz"), "wb") as fh:
        scipy.io.mmwrite(fh, sp.csr_matrix(screen.X).T.tocoo())
    with open(os.path.join(workdir, "genes.tsv"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(map(str, screen.genes)) + "\n")
    pd.DataFrame({"cell": _barcodes(screen), "sample": np.asarray(screen.unit).astype(str),
                  "fit": fit.astype(int).astype(str)}).to_csv(os.path.join(workdir, "cells.tsv"), sep="\t",
                                                              index=False)
    with open(os.path.join(workdir, "params.json"), "w", encoding="utf-8") as fh:
        json.dump(params, fh)


def _genenmf_run(workdir):
    from .. import envs
    r = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "r", "genenmf_programs.R")
    p = subprocess.run([envs.rscript(), r, workdir], env=envs.r_env({"OMP_NUM_THREADS": "1"}),
                       capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"GeneNMF failed: {p.stderr[-1500:]}")
    with open(os.path.join(workdir, "versions.txt"), encoding="utf-8") as fh:
        return dict(line.split() for line in fh if line.strip())


GENENMF_GENES = {"nfeatures": (2000, "variable genes factorised (GeneNMF's filter on Seurat's)"),
                 "min_exp": (0.01, "a variable gene's mean normalised expression, at least"),
                 "max_exp": (3.0, "a variable gene's mean normalised expression, at most")}


@method("genenmf", needs="raw counts (log-normalised by Seurat)", k=f"given (default {DEFAULT_K}, GeneNMF's)",
        params=GENENMF_GENES)
def _genenmf(screen, fit, workdir, k, seed, nfeatures=2000, min_exp=0.01, max_exp=3.0):
    k = int(k or DEFAULT_K)
    _genenmf_folder(screen, fit, workdir, {"mode": "run", "k": k, "nfeatures": nfeatures, "min_exp": min_exp,
                                           "max_exp": max_exp, "seed": seed})
    versions = _genenmf_run(workdir)
    usage = pd.read_csv(os.path.join(workdir, "usage.tsv"), sep="\t", index_col=0)
    usage.index = usage.index.astype(str)
    usage = usage.reindex(_barcodes(screen)).fillna(0.0)
    genes = pd.read_csv(os.path.join(workdir, "programs.tsv"), sep="\t", dtype={"gene": str}).rename(
        columns={"rank": "gene_rank"})  # the R script's rank: not a DataFrame method's name (item 18.2)
    call = {"package": "GeneNMF", "version": versions.get("GeneNMF"), "function": "runNMF" +
            (", RcppML project on every cell" if not fit.all() else ", RcppML project"), "layer": "lognorm",
            "params": {"k": k, "nfeatures": nfeatures, "min.exp": min_exp, "max.exp": max_exp, "seed": seed,
                       "RcppML": versions.get("RcppML"), "Seurat": versions.get("Seurat")}}
    return usage, genes, {"k": k, "k_rule": f"given (default {DEFAULT_K}, GeneNMF's)"}, [call]


@method("genenmf_meta", needs="raw counts (log-normalised by Seurat), samples",
        k="programs at k 4 to 9 in each sample, merged into 10 meta-programs (GeneNMF's defaults)",
        params={**GENENMF_GENES, "k_min": (4, "each sample's smallest k"), "k_max": (9, "each sample's largest k"),
                "specificity_weight": (5, "getMetaPrograms: how much a gene's specificity to a program counts"),
                "weight_explained": (0.5, "getMetaPrograms: the share of a program's weight its genes explain"),
                "min_confidence": (0.5, "getMetaPrograms: a gene's share of a meta-program's programs, at least"),
                "min_cells": (10, "a sample's fitted cells, at least, to be factorised")})
def _genenmf_meta(screen, fit, workdir, k, seed, k_min=4, k_max=9, n_mp=10, nfeatures=2000, min_exp=0.01,
                  max_exp=3.0, specificity_weight=5, weight_explained=0.5, min_confidence=0.5, min_cells=10):
    from ..question_modules import c
    n_mp = int(k or n_mp)
    _genenmf_folder(screen, fit, workdir, {"mode": "meta", "k_min": k_min, "k_max": k_max, "n_mp": n_mp,
                                           "nfeatures": nfeatures, "min_exp": min_exp, "max_exp": max_exp,
                                           "specificity_weight": specificity_weight,
                                           "weight_explained": weight_explained, "min_confidence": min_confidence,
                                           "min_cells": min_cells, "seed": seed})
    versions = _genenmf_run(workdir)
    genes = pd.read_csv(os.path.join(workdir, "programs.tsv"), sep="\t", dtype={"gene": str}).rename(
        columns={"rank": "gene_rank"})  # the R script's rank: not a DataFrame method's name (item 18.2)
    metrics = pd.read_csv(os.path.join(workdir, "metrics.tsv"), sep="\t")
    sets = {p: list(g.sort_values("gene_rank")["gene"]) for p, g in genes.groupby("program")}
    scores, used, score_call = c.scanpy_scores(screen, sets, min_genes=3)
    usage = pd.DataFrame(scores, index=_barcodes(screen))
    call = {"package": "GeneNMF", "version": versions.get("GeneNMF"), "function": "multiNMF, getMetaPrograms",
            "layer": "lognorm", "params": {"k": f"{k_min}:{k_max}", "nMP": n_mp, "nfeatures": nfeatures,
                                           "min.exp": min_exp, "max.exp": max_exp,
                                           "specificity.weight": specificity_weight,
                                           "weight.explained": weight_explained, "min.confidence": min_confidence,
                                           "split.by": "sample", "seed": seed}}
    about = {"k": n_mp, "k_rule": f"{n_mp} meta-programs from k {k_min} to {k_max} in each sample",
             "metrics": metrics.round(4).to_dict("records"), "genes_scored": {p: len(g) for p, g in used.items()},
             "samples": int(len(set(np.asarray(screen.unit)[fit])))}
    return usage, genes, about, [call, score_call]
