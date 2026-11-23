"""Doublet calls: scDblFinder (R) run once per dataset on the original cells, per unit, without labels; copies
(spike-in carriers) take their source cell's call. Registered in the manifest under "doublets"; the QC choices
'doublets' and 'strict_doublets' then remove called doublets (tools.qc).
Layout: <dataset dir>/doublets/scdblfinder/{calls.tsv, meta.json}.
"""
import json
import os
import subprocess
import tempfile

import numpy as np
import pandas as pd
import scipy.io
import scipy.sparse as sp

from . import envs
from .data import Data

R_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "r", "doublets_scdblfinder.R")


def fit_doublets(manifest, seed=0, register=True, log=print):
    """Calls are made once per dataset: cached analyses key on the QC choice, not on the calls, so a registered
    dataset is never refitted."""
    data = Data(manifest)
    if data.manifest.get("doublets"):
        raise RuntimeError(f"{data.name} already has doublet calls ({data.manifest['doublets']})")
    orig = data.originals()
    units = data.obs[data.design.unit_col].astype(str).to_numpy() if data.design.unit_col else \
        np.full(data.X.shape[0], "all")
    out = os.path.join(os.path.dirname(os.path.abspath(data.manifest["h5ad"])), "doublets", "scdblfinder")
    os.makedirs(out, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=out) as wd:
        scipy.io.mmwrite(os.path.join(wd, "counts.mtx"), sp.csr_matrix(data.X[orig]))
        pd.DataFrame({"cell": data.obs.index.astype(str)[orig], "unit": units[orig]}).to_csv(
            os.path.join(wd, "cells.tsv"), sep="\t", index=False)
        p = subprocess.run([envs.rscript(), R_SCRIPT, str(int(seed))], env=envs.r_env({"PERTURA_DBL_DIR": wd}),
                           capture_output=True, text=True)
        if p.returncode != 0:
            raise RuntimeError(f"scDblFinder failed ({p.returncode}): {p.stderr[-2000:]}")
        calls = pd.read_csv(os.path.join(wd, "calls.tsv"), sep="\t", dtype={"cell": str}).set_index("cell")
    doublet = np.zeros(data.X.shape[0], bool)
    score = np.zeros(data.X.shape[0])
    names = data.obs.index.astype(str)
    doublet[orig] = (calls.loc[names[orig], "class"] == "doublet").to_numpy()
    score[orig] = calls.loc[names[orig], "score"].to_numpy()
    if "copy_of" in data.obs.columns:
        cp = np.flatnonzero(data.obs["copy_of"].astype(str).to_numpy() != "")
        src = pd.Series(np.arange(len(names)), index=names)[data.obs["copy_of"].astype(str).to_numpy()[cp]].to_numpy()
        doublet[cp], score[cp] = doublet[src], score[src]
    pd.DataFrame({"cell": names, "score": score, "doublet": doublet}).to_csv(f"{out}/calls.tsv", sep="\t", index=False)
    meta = {"method": "scDblFinder, per unit, original cells; copies take their source's call", "seed": int(seed),
            "fingerprint": data.fingerprint, "doublets": int(doublet[orig].sum()), "cells": int(len(orig))}
    with open(f"{out}/meta.json", "w") as fh:
        json.dump(meta, fh, indent=1)
    if register:
        with open(manifest) as fh:
            m = json.load(fh)
        m["doublets"] = out
        with open(manifest + ".tmp", "w") as fh:
            json.dump(m, fh, indent=1)
        os.replace(manifest + ".tmp", manifest)
    log(f"scDblFinder: {meta['doublets']} doublets of {meta['cells']} original cells")
    return meta


def load_doublets(directory):
    """Boolean doublet call per cell, in dataset order."""
    return pd.read_csv(os.path.join(directory, "calls.tsv"), sep="\t")["doublet"].to_numpy(bool)
