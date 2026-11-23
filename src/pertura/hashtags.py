"""Each cell's hashtag, as the field calls it: Seurat's HTODemux with its defaults (the hashtag counts CLR-normalised,
as Seurat's hashing vignette does), or MULTIseqDemux, one Cell Ranger run (a sample) at a time. The measurement
compares the two on the deposit; the agent calls each cell's hashtag with the one it chooses and writes it into the
cell tables (write_into_cell_tables), where the design names the column as the cells' unit (docs/adr/0041, step 4).

A hashtag marks one sample of cells stained before they were pooled: an animal, a donor, one condition's cells, or
a donor's cells in one condition. A method is named as
    {"method": "htodemux" | "multiseqdemux" | "top_ratio", "settings": {<the R function's own arguments>}}
with, where it is written into the cell tables, whether one hashtag marks the same sample in every Cell Ranger run
("one unit") or each run's hashtags are samples of their own ("one unit per sample").
"top_ratio" (a cell's top hashtag with min_counts and ratio times the second's) runs only where the design names it
with both numbers: it is no package's method.
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

METHODS = ("htodemux", "multiseqdemux", "top_ratio")
DEFAULT_METHOD = "htodemux"
ONE_UNIT, PER_SAMPLE = "one unit", "one unit per sample"
ACROSS = (ONE_UNIT, PER_SAMPLE)
# each R function's arguments a design may set (Seurat 5's HTODemux and MULTIseqDemux)
SETTINGS = {"htodemux": ("positive.quantile", "init", "nstarts", "kfunc", "nsamples", "seed"),
            "multiseqdemux": ("quantile", "autoThresh", "maxiter", "qrange")}
FUNCTION = {"htodemux": "HTODemux", "multiseqdemux": "MULTIseqDemux"}
R_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "r", "hashtags_seurat.R")


def spec_of(value):
    """A hashtag method ({method, settings}) with its defaults filled in."""
    v = dict(value) if isinstance(value, dict) else {}
    v.setdefault("method", DEFAULT_METHOD)
    v.setdefault("settings", {})
    return v


def validate(value):
    """Problems with a hashtag method as named ({method, settings, across_samples}; empty when it can be applied)."""
    if value is None:
        return []
    if not isinstance(value, dict):
        return [f"hashtag calls: {value!r} is not a mapping; give {{method, settings, across_samples}}"]
    v = spec_of(value)
    problems = []
    if v["method"] not in METHODS:
        problems.append(f"hashtag calls: method {v['method']!r} is not one of {', '.join(METHODS)}")
    elif v["method"] == "top_ratio":
        if not all(isinstance(v.get(k), (int, float)) for k in ("min_counts", "ratio")):
            problems.append("hashtag calls: top_ratio is no package's method; it needs both numbers named "
                            "(min_counts, ratio)")
    else:
        if not isinstance(v["settings"], dict):
            problems.append("hashtag calls: settings is a mapping of the R function's arguments")
        else:
            unknown = sorted(set(v["settings"]) - set(SETTINGS[v["method"]]))
            if unknown:
                problems.append(f"hashtag calls: {FUNCTION[v['method']]} takes no {', '.join(unknown)} (it takes "
                                f"{', '.join(SETTINGS[v['method']])})")
    if v.get("across_samples") not in (None, *ACROSS):
        problems.append(f"hashtag calls: across_samples is {ONE_UNIT!r} (one hashtag marks the same sample in "
                        f"every Cell Ranger run: an animal, a donor or one condition's cells) or {PER_SAMPLE!r} (each "
                        "run's hashtags are samples of their own)")
    return problems


def describe(value):
    """A hashtag method in plain words."""
    v = spec_of(value)
    if v["method"] == "top_ratio":
        how = f"a cell's top hashtag with {v.get('min_counts')} counts and {v.get('ratio')} times the second's"
    else:
        how = f"Seurat's {FUNCTION.get(v['method'], v['method'])}" + (
            " with " + ", ".join(f"{k} {x}" for k, x in v["settings"].items()) if v["settings"] else
            " at its defaults")
    across = {ONE_UNIT: "; one hashtag marks the same sample in every capture",
              PER_SAMPLE: "; each capture's hashtags are samples of their own"}.get(v.get("across_samples"), "")
    return how + across


def package_call(value):
    """The package call behind the hashtag calls (question_modules.packages), or None for top_ratio."""
    v = spec_of(value)
    if v["method"] not in FUNCTION:
        return None
    from .question_modules import packages
    return packages.call("Seurat", FUNCTION[v["method"]], layer="raw_counts",
                         formula="hashtag counts, CLR-normalised (NormalizeData), one Cell Ranger run at a time",
                         contrast="Singlet, Doublet or Negative", **v["settings"])


def _top_ratio(H, min_counts, ratio):
    H = H.toarray() if sp.issparse(H) else np.asarray(H)
    if H.shape[1] == 0:
        return np.full(len(H), -1), np.full(len(H), "none", dtype=object)
    top = H.max(1)
    second = np.sort(H, 1)[:, -2] if H.shape[1] > 1 else np.zeros(len(H))
    ok = (top >= min_counts) & (top >= ratio * second)
    call = np.where(ok, "one", np.where(top >= min_counts, "several", "none")).astype(object)
    return np.where(ok, H.argmax(1), -1), call


def demultiplex(H, hashtags, groups=None, value=None, workdir=None):
    """H: cells x hashtags counts (columns in the order of `hashtags`); groups: per cell its Cell Ranger run.
    Returns (hashtag per cell, '' where none is called; call per cell: 'one', 'several' or 'none'; info: the method,
    the counts of each call and the groups the method could not run on, with why). Raises RuntimeError where R or
    Seurat fails as a whole."""
    v = spec_of(value)
    if v["method"] == "top_ratio":
        col, call = _top_ratio(H, v["min_counts"], v["ratio"])
        return _named(col, call, hashtags, v, {})
    return demultiplex_each(H, hashtags, groups, [v["method"]], v["settings"], workdir)[v["method"]]


def demultiplex_each(H, hashtags, groups=None, methods=("htodemux", "multiseqdemux"), settings=None, workdir=None):
    """The field's demultiplexers at once, in one R run that loads Seurat once (the measurement compares them):
    {method: (hashtag per cell, call per cell, info)} as demultiplex returns them."""
    n = H.shape[0]
    groups = np.asarray(["all"] * n if groups is None else groups, dtype=object).astype(str)
    methods = [m for m in methods if m in FUNCTION]
    if workdir:  # a folder the caller names may not exist yet (a deposit of h5 matrices parses nothing first)
        os.makedirs(workdir, exist_ok=True)
    out = {}
    with tempfile.TemporaryDirectory(dir=workdir) as wd:
        scipy.io.mmwrite(os.path.join(wd, "counts.mtx"), sp.csr_matrix(H, dtype=np.float64))
        pd.DataFrame({"cell": [f"c{i}" for i in range(n)], "group": groups}).to_csv(
            os.path.join(wd, "cells.tsv"), sep="	", index=False)
        with open(os.path.join(wd, "settings.json"), "w") as fh:
            json.dump(settings or {}, fh)
        p = subprocess.run([envs.rscript(), R_SCRIPT, ",".join(methods)], env=envs.r_env({"PERTURA_HTO_DIR": wd}),
                           capture_output=True, text=True)
        if p.returncode != 0:
            raise RuntimeError(f"Seurat's {', '.join(FUNCTION[m] for m in methods)} failed ({p.returncode}): "
                               f"{p.stderr[-1500:]}")
        for m in methods:
            calls = pd.read_csv(os.path.join(wd, f"calls_{m}.tsv"), sep="	", dtype={"cell": str})
            failed = pd.read_csv(os.path.join(wd, f"groups_{m}.tsv"), sep="	", dtype=str)
            errors = dict(zip(failed["group"], failed["error"])) if len(failed) else {}
            col = calls["hashtag"].fillna(0).astype(int).to_numpy() - 1
            out[m] = _named(col, calls["call"].astype(str).to_numpy(dtype=object), hashtags,
                            {"method": m, "settings": settings or {}}, errors)
    return out


def _named(col, call, hashtags, v, errors):
    hashtags = [str(h) for h in hashtags]
    tag = np.array([hashtags[c] if k == "one" and c >= 0 else "" for c, k in zip(col, call)], dtype=object)
    info = {"method": v["method"], "settings": v.get("settings") or {},
            "calls": {k: int((call == k).sum()) for k in ("one", "several", "none")}, "groups_not_called": errors}
    return tag, call, info


def write_into_cell_tables(cell_tables, hashtags, across_samples, value=None, column="hashtag", made_by="",
                           workdir=None):
    """Each cell's hashtag written into the project's cell tables as a column (pertura.cell_table.annotate), for the
    design to name as the cells' unit: called by the method `value` names ({method, settings}; Seurat's HTODemux at
    its defaults when None), one sample (one Cell Ranger run) at a time. across_samples: ONE_UNIT where one hashtag
    marks the same animal, donor or condition's cells in every sample (the value is the hashtag), PER_SAMPLE where each
    sample's hashtags are samples of their own (the value is '<sample>:<hashtag>'). A cell without one clear hashtag
    gets no value. Returns annotate's answer with the method in words and each sample's calls (one, several, none)."""
    import anndata as ad
    from . import cell_table as ct
    problems = validate({**spec_of(value), "across_samples": across_samples})
    if across_samples is None:
        problems.append(f"across_samples: {ONE_UNIT!r} or {PER_SAMPLE!r}; the measurement's "
                        "units.hashtag_shares_by_sample gives each hashtag's share of each sample's cells")
    if problems:
        return {"ok": False, "problems": problems, "column": column}
    values, calls = {}, {}
    for s in ct.samples(cell_tables):
        a = ad.read_h5ad(s["matrix"])
        names = a.var["name"].astype(str).to_numpy()
        present = [str(h) for h in hashtags if (names == str(h)).any()]
        if not present:
            problems.append(f"{s['name']}: none of the hashtags is among its features")
            continue
        H = sp.csr_matrix(a.X)[:, [int(np.flatnonzero(names == h)[0]) for h in present]]
        tag, _, info = demultiplex(H, present, groups=[s["name"]] * a.n_obs, value=value, workdir=workdir)
        if info["groups_not_called"]:  # said, never a quiet loss of a sample's cells
            problems.append(f"{s['name']}: {describe(value)} could not call its hashtags: "
                            f"{next(iter(info['groups_not_called'].values())).strip()}")
            continue
        values[s["name"]] = pd.Series([t if across_samples == ONE_UNIT or not t else f"{s['name']}:{t}" for t in tag],
                                      index=a.obs_names.astype(str))
        calls[s["name"]] = info["calls"]
    how = describe({**spec_of(value), "across_samples": across_samples})
    out = ct.annotate(cell_tables, column, values, made_by, note=f"each cell's hashtag by {how}; calls one, several, "
                      "none: " + "; ".join(f"{n} {c['one']}, {c['several']}, {c['none']}" for n, c in calls.items())
                      ) if values else {"ok": False, "problems": [], "column": column, "samples": {}}
    return {**out, "ok": out["ok"] and not problems, "problems": problems + out["problems"], "method": how,
            "calls": calls}


def shares_by_sample(tag, sample):
    """Each hashtag's share of a sample's cells with one hashtag: {sample: {hashtag: share}}."""
    t = pd.Series(np.asarray(tag, dtype=str))
    s = pd.Series(np.asarray(sample, dtype=str))
    keep = t != ""
    if not keep.any():
        return {}
    ct = pd.crosstab(s[keep], t[keep], normalize="index").round(3)
    return {str(r): {str(c): float(x) for c, x in row.items()} for r, row in ct.iterrows()}
