"""cNMF (Kotliar et al. 2019) on one counts file, run with cNMF's own interpreter (envs.cnmf_python): prepare,
factorize, combine, the K-selection statistics, and the consensus at the chosen K (the K given, else the most stable
one in the range). Writes, in the work folder: usage.tsv (cells by programs, each cell's usages summing to 1),
top_genes.tsv (program, rank, gene, score: cNMF's gene spectra scores), k_stats.tsv and chosen.json.

With a second counts file, the programs found on the first file's cells are fixed and every cell of the second file
gets its usage by cNMF's own refit (refit_usage, as its consensus step refits usages: TPM of the high-variance genes,
each scaled by the fitted cells' standard deviation): programs found in the control cells, for instance, read in
every cell.

    python cnmf_runner.py <work> <counts.h5ad> <components, comma-separated> <n_iter> <seed> <k or 0> <density>
        [<counts.h5ad of the cells to read>]

The factorisations run in PERTURA_CNMF_WORKERS processes (1 by default), as cNMF's own parallel mode runs them (each
worker takes its share of the runs, each run with its own seed), each worker with one thread.
"""
import json
import os
import subprocess
import sys

import numpy as np
import pandas as pd
import scanpy as sc
import scipy.sparse as sp
from cnmf import cNMF
from cnmf.cnmf import load_df_from_npz


def project(c, chosen, tag, counts):
    """Every cell of the counts file's usage of the fixed programs, by cNMF's refit."""
    cells = sc.read_h5ad(counts)
    sc.pp.normalize_total(cells, target_sum=1e6)
    with open(c.paths["nmf_genes_list"], encoding="utf-8") as fh:
        hvgs = [g for g in fh.read().split("\n") if g]
    std = load_df_from_npz(c.paths["tpm_stats"]).loc[hvgs, "__std"].to_numpy()
    X = cells[:, hvgs].X
    X = X.multiply(1.0 / std).tocsr() if sp.issparse(X) else X / std
    spectra = load_df_from_npz(c.paths["gene_spectra_tpm"] % (chosen, tag)).loc[:, hvgs]
    spectra = spectra.div(std, axis=1)
    X = X.astype(np.float64)
    usage = c.refit_usage(X, spectra.to_numpy().astype(np.float64))
    return pd.DataFrame(np.asarray(usage), index=cells.obs_names, columns=spectra.index)


def factorize(work, workers):
    """cNMF's factorisations, in `workers` processes of one thread each."""
    if workers <= 1:
        cNMF(output_dir=work, name="cnmf").factorize(worker_i=0, total_workers=1)
        return
    env = {**os.environ, **{v: "1" for v in os.environ if v.endswith("_NUM_THREADS")},
           **{v: "1" for v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS")}}
    logs = [os.path.join(work, f"factorize_{i}.log") for i in range(workers)]  # a file each: no pipe fills up
    procs = []
    for i, log in enumerate(logs):
        with open(log, "w", encoding="utf-8") as fh:
            procs.append(subprocess.Popen([sys.executable, os.path.abspath(__file__), "factorize", work, str(i),
                                           str(workers)], env=env, stdout=fh, stderr=subprocess.STDOUT))
    failed = [log for p, log in zip(procs, logs) if p.wait() != 0]
    if failed:
        with open(failed[0], encoding="utf-8", errors="replace") as fh:
            raise RuntimeError(f"a cNMF worker failed: {fh.read()[-1500:]}")


def main():
    if sys.argv[1] == "factorize":  # one worker's share of the factorisations
        work, i, n = sys.argv[2:5]
        cNMF(output_dir=work, name="cnmf").factorize(worker_i=int(i), total_workers=int(n))
        return
    work, counts, comps, n_iter, seed, k, density = sys.argv[1:8]
    read = sys.argv[8] if len(sys.argv) > 8 else None
    components = [int(x) for x in comps.split(",")]
    c = cNMF(output_dir=work, name="cnmf")
    c.prepare(counts_fn=counts, components=components, n_iter=int(n_iter), seed=int(seed), num_highvar_genes=2000)
    factorize(work, int(os.environ.get("PERTURA_CNMF_WORKERS") or 1))
    c.combine()
    c.k_selection_plot(close_fig=True)
    stats = load_df_from_npz(c.paths["k_selection_stats"])
    chosen = int(k) if int(k) else int(stats.loc[stats["silhouette"].idxmax(), "k"])
    dt = float(density)
    c.consensus(k=chosen, density_threshold=dt, show_clustering=False, close_clustergram_fig=True)
    tag = str(dt).replace(".", "_")
    usage = project(c, chosen, tag, read) if read else load_df_from_npz(c.paths["consensus_usages"] % (chosen, tag))
    usage = usage.div(usage.sum(1).replace(0, 1), axis=0)
    usage.columns = [f"P{int(j):02d}" for j in usage.columns]
    usage.to_csv(os.path.join(work, "usage.tsv"), sep="\t")
    score = load_df_from_npz(c.paths["gene_spectra_score"] % (chosen, tag))
    score.index = [f"P{int(j):02d}" for j in score.index]
    rows = []
    for prog, s in score.iterrows():
        for rank, (gene, v) in enumerate(s.sort_values(ascending=False).head(50).items(), 1):
            rows.append({"program": prog, "rank": rank, "gene": gene, "score": float(v)})
    pd.DataFrame(rows).to_csv(os.path.join(work, "top_genes.tsv"), sep="\t", index=False)
    stats.to_csv(os.path.join(work, "k_stats.tsv"), sep="\t", index=False)
    with open(os.path.join(work, "chosen.json"), "w", encoding="utf-8") as fh:
        json.dump({"k": chosen, "given": bool(int(k)), "density_threshold": dt, "components": components,
                   "n_iter": int(n_iter), "seed": int(seed), "read_on_other_cells": bool(read)}, fh)


if __name__ == "__main__":
    main()
