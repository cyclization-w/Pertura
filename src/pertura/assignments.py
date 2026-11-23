"""Guide-assignment fits: crispat methods (Braunger et al. 2024) run once per dataset on its guide counts, without
labels, and registered in the manifest under "assignments". Each becomes an assignment choice in the grammar,
crispat:<method>[:<threshold>], next to the provided calls and UMI re-calling.

crispat runs on construct-level counts: the UMIs of a construct's sgRNAs (columns <construct><suffix><k>) are summed
first, so with dual-guide vectors a ratio rule means the vector's share of the cell's guide UMIs (per sgRNA, each of
the two sgRNAs holds about half and a 0.7 rule calls almost nothing). A cell's call is its assigned construct, or none
if crispat assigns several or none. crispat's batch is the dataset's unit (lanes differ in guide capture). It runs in its own interpreter (envs 'py', tools/assignment/crispat_runner.py),
one thread. Fits are made once per dataset: cached analyses key on the choice name, not on the calls.
Layout: <dataset dir>/assignments/<name>/{calls.tsv, meta.json}, name = crispat_<method>[_<threshold>].
"""
import json
import os
import subprocess
import tempfile
import time

import numpy as np
import pandas as pd

from . import envs
from .data import Data

RUNNER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tools", "assignment", "crispat_runner.py")


def choice_name(method, threshold=None):
    return f"crispat:{method}" + (f":{threshold:g}" if threshold is not None else "")


def dir_name(choice):
    return choice.replace(":", "_")


def fit_assignment(manifest, method, threshold=None, register=True, log=print):
    import anndata as ad
    data = Data(manifest)
    choice = choice_name(method, threshold)
    if choice in data.manifest.get("assignments", {}):
        raise RuntimeError(f"{data.name} already has {choice}")
    G, names = data.guide_counts()
    owner = np.array([str(n).rsplit(data.design.sgrna_suffix, 1)[0] for n in names])
    constructs = np.array(sorted(set(owner)))
    col = pd.Series(np.arange(len(constructs)), index=constructs)[owner].to_numpy()
    import scipy.sparse as sp
    to_con = sp.csr_matrix((np.ones(len(owner)), (np.arange(len(owner)), col)), shape=(len(owner), len(constructs)))
    C = sp.csr_matrix(G) @ to_con
    units = data.obs[data.design.unit_col].astype(str).to_numpy() if data.design.unit_col else \
        np.full(data.X.shape[0], "all")
    codes = pd.Categorical(units).codes.astype(int)
    out = os.path.join(os.path.dirname(os.path.abspath(data.manifest["h5ad"])), "assignments", dir_name(choice))
    os.makedirs(out, exist_ok=True)
    t0 = time.time()
    with tempfile.TemporaryDirectory(dir=out) as wd:
        cells = data.obs.index.astype(str)
        ad.AnnData(sp.csr_matrix(C, dtype=np.float32), obs=pd.DataFrame({"batch": codes}, index=cells),
                   var=pd.DataFrame(index=[str(c) for c in constructs])).write_h5ad(os.path.join(wd, "guides.h5ad"))
        cmd = [envs.ENVIRONMENTS["py"]["executable"], RUNNER, os.path.join(wd, "guides.h5ad"), os.path.join(wd, "run"),
               method] + ([str(threshold)] if threshold is not None else [])
        env = {**os.environ, "OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1", "MKL_NUM_THREADS": "1"}
        p = subprocess.run(cmd, env=env, capture_output=True, text=True)
        if p.returncode != 0:
            raise RuntimeError(f"crispat {choice} failed ({p.returncode}): {p.stderr[-2000:]}")
        a = pd.read_csv(os.path.join(wd, "run", "assignments.csv"), dtype={"cell": str, "gRNA": str})
        with open(os.path.join(wd, "run", "version.txt")) as fh:
            version = fh.read().strip()
    per_cell = a["gRNA"].groupby(a["cell"]).agg(lambda s: s.iloc[0] if s.nunique() == 1 else "")
    calls = per_cell.reindex(data.obs.index.astype(str)).fillna("")
    pd.DataFrame({"cell": calls.index, "construct": calls.values}).to_csv(f"{out}/calls.tsv", sep="\t", index=False)
    meta = {"choice": choice, "method": method, "threshold": threshold, "crispat_version": version,
            "fingerprint": data.fingerprint, "cells_called": int((calls != "").sum()), "cells": int(len(calls)),
            "ambiguous": int((per_cell == "").sum()), "seconds": round(time.time() - t0, 1)}
    with open(f"{out}/meta.json", "w") as fh:
        json.dump(meta, fh, indent=1)
    if register:
        with open(manifest) as fh:
            m = json.load(fh)
        m.setdefault("assignments", {})[choice] = out
        with open(manifest + ".tmp", "w") as fh:
            json.dump(m, fh, indent=1)
        os.replace(manifest + ".tmp", manifest)
    log(f"{choice}: {meta['cells_called']} of {meta['cells']} cells called ({meta['ambiguous']} ambiguous), "
        f"{meta['seconds']} s")
    return meta


def load_calls(directory):
    """Construct call per cell ('' for none), in dataset order."""
    return pd.read_csv(os.path.join(directory, "calls.tsv"), sep="\t", dtype=str,
                       keep_default_na=False)["construct"].to_numpy()
