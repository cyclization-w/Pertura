"""Cell cycle (docs/adr/0031, D1a; ADR 0022's routine step, scored or regressed in 15 of 78 published screens): each
cell's S and G2/M scores and its phase, by the two forms of one method (Tirosh et al. 2016):
- scanpy: tl.score_genes_cell_cycle (the default);
- seurat: Seurat's CellCycleScoring (R).
Both read Seurat's updated gene lists (cc.genes.updated.2019: 43 S and 54 G2/M genes, cell_cycle_genes_tirosh2016.tsv);
for mouse, the human symbols in title case, as the field converts them.

    cells, calls = phases(screen, method="scanpy")   # DataFrame by barcode: S_score, G2M_score, phase
"""
import json
import os
import subprocess

import numpy as np
import pandas as pd

GENES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cell_cycle_genes_tirosh2016.tsv")
METHODS = ("scanpy", "seurat")
MIN_GENES = 10  # of each phase's genes in the screen, for a score


def gene_lists(organism):
    """The S and the G2/M genes for an organism (human or mouse)."""
    t = pd.read_csv(GENES, sep="\t")
    if organism == "mouse":
        t["gene"] = t["gene"].str.capitalize()
    elif organism != "human":
        raise ValueError(f"the cell-cycle genes are for human or mouse, not {organism!r} (experiment.organism)")
    return list(t.loc[t["phase"] == "S", "gene"]), list(t.loc[t["phase"] == "G2M", "gene"])


def phases(screen, method="scanpy", seed=0, workdir="."):
    """Each cell's S and G2/M scores and phase (G1, S or G2M): (DataFrame by barcode, package calls)."""
    from ..question_modules.data import value
    if method not in METHODS:
        raise ValueError(f"no cell-cycle method {method!r}; the methods are {list(METHODS)}")
    s_genes, g2m_genes = gene_lists(value(screen.design, "experiment.organism"))
    have = set(map(str, screen.genes))
    s_in, g2m_in = [g for g in s_genes if g in have], [g for g in g2m_genes if g in have]
    if len(s_in) < MIN_GENES or len(g2m_in) < MIN_GENES:
        raise ValueError(f"too few cell-cycle genes in the screen ({len(s_in)} S, {len(g2m_in)} G2/M; {MIN_GENES} of "
                         "each needed)")
    barcodes = np.asarray(screen.adata.obs_names).astype(str)
    genes_used = {"S": len(s_in), "G2M": len(g2m_in), "S_listed": len(s_genes), "G2M_listed": len(g2m_genes)}
    if method == "scanpy":
        import anndata as ad
        import scanpy as sc
        from ..question_modules import packages
        a = ad.AnnData(screen.lognorm, var=pd.DataFrame(index=pd.Index(screen.genes).astype(str)))
        a.var_names_make_unique()
        sc.tl.score_genes_cell_cycle(a, s_genes=s_in, g2m_genes=g2m_in, random_state=seed)
        out = pd.DataFrame({"S_score": a.obs["S_score"].to_numpy(), "G2M_score": a.obs["G2M_score"].to_numpy(),
                            "phase": a.obs["phase"].astype(str).to_numpy()}, index=barcodes)
        call = packages.call("scanpy", "tl.score_genes_cell_cycle", data=a.X, layer="lognorm",
                             genes="Seurat's cc.genes.updated.2019 (Tirosh et al. 2016)", random_state=seed,
                             **genes_used)
        return out, [call]
    return _seurat(screen, s_in, g2m_in, barcodes, genes_used, workdir)


def _seurat(screen, s_in, g2m_in, barcodes, genes_used, workdir):
    from .. import envs
    os.makedirs(workdir, exist_ok=True)
    envs.counts_for_r(os.path.join(workdir, "counts.mtx.gz"), screen.X)
    with open(os.path.join(workdir, "genes.tsv"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(map(str, screen.genes)) + "\n")
    pd.DataFrame({"cell": barcodes}).to_csv(os.path.join(workdir, "cells.tsv"), sep="\t", index=False)
    with open(os.path.join(workdir, "params.json"), "w", encoding="utf-8") as fh:
        json.dump({"s_genes": s_in, "g2m_genes": g2m_in}, fh)
    r = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "r", "cell_cycle_seurat.R")
    p = subprocess.run([envs.rscript(), r, workdir], env=envs.r_env({"OMP_NUM_THREADS": "1"}), capture_output=True,
                       text=True)
    if p.returncode != 0:
        raise RuntimeError(f"Seurat CellCycleScoring failed: {p.stderr[-1500:]}")
    out = pd.read_csv(os.path.join(workdir, "phases.tsv"), sep="\t", index_col=0)
    out.index = out.index.astype(str)
    out = out.reindex(barcodes)
    with open(os.path.join(workdir, "versions.txt"), encoding="utf-8") as fh:
        versions = dict(line.split() for line in fh if line.strip())
    call = {"package": "Seurat", "version": versions.get("Seurat"), "function": "NormalizeData, CellCycleScoring",
            "layer": "raw_counts", "params": {"genes": "cc.genes.updated.2019 (Tirosh et al. 2016)", **genes_used}}
    return out, [call]
