"""Pseudobulk DE tools: counts summed per unit and group, then one model per group against the reference, paired by
unit (~ unit + group, at least 3 shared units).

Methods:
- edger_ql, limma_voom, deseq2: through the September R script (r/pseudobulk_de.R, unchanged);
- glmgampoi: through r/pseudobulk_glmgampoi.R, which follows the same conventions.

Two tables per analysis: 'all' holds the pooled groups, 'constructs' every construct once (a construct shared by
several groups, as in research universes, is tested once). Version 1 used one table per construct position instead
(the k-th construct of every group), as in the construct-replication test; the pooled table is unchanged.
Table names inside R are neutral (t00001, ...), so any group or construct label works.
Aggregation rule (identical to pertura_research.legacy.build_decoys.pseudobulk):
- samples are unit x group with at least min_cells cells;
- pb_samples_10pct keeps genes detected in at least 10% of a table's samples;
- cells_1pct keeps genes detected in at least 1% of all original cells instead.
"""
import os
import subprocess

import numpy as np
import pandas as pd
import scipy.sparse as sp

from ... import envs
from ...errors import R_OUT_OF_MEMORY, MachineError, killed_from_outside


def r_failure(returncode, stderr):
    """The exception for an R run's exit, or None when it succeeded: killed from outside, or out of memory in R's own
    words (R runs Pertura's script on Pertura's tables, so its messages are not an agent's), is the machine's
    (pertura.errors); any other non-zero exit is the tool's (RuntimeError)."""
    if returncode == 0:
        return None
    text = (stderr or "").lower()
    if killed_from_outside(returncode) or any(w in text for w in R_OUT_OF_MEMORY):
        return MachineError("R was stopped by the machine (killed, or out of memory)")
    return RuntimeError(f"R failed ({returncode}): {(stderr or '')[-2000:]}")

R_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "r")
METHODS = {  # method -> (R script, tool argument, packages whose versions shape the results)
    "edger_ql": ("pseudobulk_de.R", "edger", ("edgeR", "limma", "data.table")),
    "limma_voom": ("pseudobulk_de.R", "limma", ("edgeR", "limma", "data.table")),
    "deseq2": ("pseudobulk_de.R", "deseq2", ("DESeq2", "edgeR", "data.table")),
    "glmgampoi": ("pseudobulk_glmgampoi.R", "glmgampoi", ("glmGamPoi", "edgeR", "data.table")),
}
REF = "REF"


def aggregate(X, groups, units, genes, min_cells, gene_mask=None, sample_filter=True):
    """Pseudobulk counts (genes x samples) and the sample sheet (sample, mouse = unit, group, n_cells).
    groups: name -> cell positions (groups may share cells). Units are visited in sorted order within each group."""
    rows, cols, meta = [], [], []
    for g, pos in groups.items():
        u = units[pos]
        for unit in np.unique(u):
            sel = pos[u == unit]
            if len(sel) < min_cells:
                continue
            rows.append(np.full(len(sel), len(meta)))
            cols.append(sel)
            meta.append({"sample": f"{unit}|{g}", "mouse": unit, "group": g, "n_cells": int(len(sel))})
    meta = pd.DataFrame(meta, columns=["sample", "mouse", "group", "n_cells"])
    if meta.empty:
        return pd.DataFrame(index=pd.Index([], name="gene")), meta
    r, c = np.concatenate(rows), np.concatenate(cols)
    ind = sp.csr_matrix((np.ones(len(r)), (r, c)), shape=(len(meta), X.shape[0]))
    pb = (ind @ X).T.tocsr()
    keep = np.ones(pb.shape[0], bool) if gene_mask is None else np.asarray(gene_mask, bool).copy()
    if sample_filter:
        keep &= np.asarray((pb > 0).sum(1)).ravel() >= 0.1 * len(meta)
    mat = pd.DataFrame(pb[np.flatnonzero(keep)].toarray().astype(np.int64), index=np.asarray(genes)[keep],
                       columns=meta["sample"].values)
    return mat, meta


MIN_PAIRED_UNITS = 3  # pseudobulk_de.R's MIN_PAIRED_MICE: a group is fitted where it shares 3 units with REF


def untestable_groups(groups, reference, units, min_cells):
    """{group: reason} for the groups pseudobulk_de.R cannot fit by its own rule: a sample is a unit with min_cells
    cells of the group (as aggregate counts them), and a group needs MIN_PAIRED_UNITS units with samples of both the
    group and the reference. groups: name -> cell positions; reference: the reference's positions."""
    def sampled(pos):
        u, n = np.unique(units[pos], return_counts=True)
        return set(u[n >= min_cells])
    ref = sampled(np.asarray(reference))
    out = {}
    for g, pos in groups.items():
        shared = len(sampled(np.asarray(pos)) & ref)
        if shared < MIN_PAIRED_UNITS:
            out[g] = (f"untestable by pseudobulk's own rule: {shared} unit(s) with {min_cells}+ cells of both the "
                      f"target and the reference, and the test needs {MIN_PAIRED_UNITS}")
    return out


def write_table(mat, meta, stem):
    mat.to_csv(f"{stem}.counts.tsv.gz", sep="\t", compression="gzip")
    meta.to_csv(f"{stem}.samples.tsv", sep="\t", index=False)


class PseudobulkR:
    claim_type = "gene_de"
    unit_kind = "pseudobulk"
    environment = "r45"

    def __init__(self, name):
        self.name = name
        self.script, self.tool, self.packages = METHODS[name]
        self.version = "pseudobulk_r/2"

    def untestable(self, spec, data, gs):
        """The groups this tool's own rule cannot test, with the reason (untestable_groups), on the pooled level."""
        units = data.obs[data.design.unit_col].astype(str).to_numpy()
        return untestable_groups(gs.groups, gs.reference, units, int(spec["min_cells"]))

    def run(self, spec, data, gs, workdir):
        design = data.design
        units = data.obs[design.unit_col].astype(str).to_numpy()
        gene_mask = data.detected(0.01) if spec["gene_filter"] == "cells_1pct" else None
        sample_filter = spec["gene_filter"] == "pb_samples_10pct"
        base = os.path.join(workdir, "pseudobulk")
        os.makedirs(os.path.join(base, "pb"), exist_ok=True)
        tables = {"all": [(g, "all", pos) for g, pos in gs.groups.items()],
                  "constructs": [(gs.home[c], c, pos) for c, pos in gs.constructs.items()]}
        labels = {}  # (table, neutral name) -> (group, construct)
        for level, items in tables.items():
            names = {}
            for k, (g, c, pos) in enumerate(items):
                names[f"t{k + 1:05d}"] = pos
                labels[(level, f"t{k + 1:05d}")] = (g, c)
            mat, meta = aggregate(data.X, {REF: gs.reference, **names}, units, data.genes,
                                  int(spec["min_cells"]), gene_mask, sample_filter)
            write_table(mat, meta, os.path.join(base, "pb", f"{level}__r0"))
        p = subprocess.run([envs.rscript(), os.path.join(R_DIR, self.script), self.tool, "0"],
                           env=envs.r_env({"PERTURA_EXPR_DIR": base}), capture_output=True, text=True)
        failure = r_failure(p.returncode, p.stderr)
        if failure is not None:
            raise failure
        parts = []
        for level in tables:
            f = os.path.join(base, "r_results", f"{level}__r0__{self.tool}.tsv.gz")
            if not os.path.exists(f) or os.path.getsize(f) == 0:
                continue
            try:
                t = pd.read_csv(f, sep="\t", dtype={"group": str, "gene": str})
            except pd.errors.EmptyDataError:
                continue
            gc = [labels[(level, n)] for n in t["group"]]
            parts.append(pd.DataFrame({"group": [x[0] for x in gc], "construct": [x[1] for x in gc],
                                       "feature": t["gene"].values, "effect": t["logFC"].values, "p": t["p"].values}))
        cols = ["group", "construct", "feature", "effect", "p"]
        return pd.concat(parts, ignore_index=True)[cols] if parts else pd.DataFrame(columns=cols)
