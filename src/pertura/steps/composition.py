"""The composition step: each target's share of cells in each state of a labelling against its reference
(docs/adr/0031, D1a; the backends chosen on 2026-10-21).

The plumbing is here once:
- the reference by the design (the target's arm), a target's own cells never in its reference;
- the row rule: a target with 10 cells and 10 reference cells gets a row for every state of the labelling, and a
  row needs 10 kept target cells; a backend that tests samples uses the samples holding 10 cells of each side, and a
  target with too few of them has no rows and is named among the call's not_fitted;
- one row shape for every backend (COLUMNS), with each backend's own measure after it;
- the rows called changed (`changed`): adjusted p < 0.05 by Benjamini-Hochberg over every target and state, scCODA's
  credible effects, or Milo's spatial FDR below 0.05;
- per target, state and sample, the share's difference from the reference's (by_unit);
- the package calls.

The backends, each the field's package with its defaults:
- fisher: Fisher's exact test on the cells (scipy), against the control cells of the target's own samples, in its
  mix (control_cells same_samples), or every reference cell (all);
- cmh: the Cochran-Mantel-Haenszel test over the samples holding both sides, each sample a stratum (statsmodels'
  StratifiedTable, its test_null_odds);
- propeller: speckle's propeller.ttest on the samples' transformed proportions (R), each target against its
  reference in the 3 or more samples both hold, ~ 0 + group + sample; the arcsine square root by default, since
  speckle's logit called states up in a target that had none of their cells (docs/adr/0031, 2026-10-21);
- sccoda: scCODA (pertpy's Sccoda), Bayesian, a model per target against its reference over the samples both hold;
  its credible effects at an expected FDR of 0.05; no p-value;
- milo: Milo (pertpy), the differential abundance of neighbourhoods of a kNN graph of the target's and its
  reference's cells, ~ sample + group over the samples both hold; a row per target and neighbourhood, with the
  neighbourhood's most common state; q is Milo's spatial FDR.

    per, by_unit, calls = compare(screen, targets, reference, labels, method="fisher", workdir=".")
"""
import os

import numpy as np
import pandas as pd

from .. import limits

MIN_CELLS = 10  # pertura.policy's default; the step reads the number in effect (_cells)
ADJUSTED = 0.05  # pertura.policy's default q; the step reads the q in effect (_q)
COLUMNS = ["target", "state", "share", "control_share", "mean_difference", "odds_ratio", "p", "test", "cells",
           "reference_cells", "target_samples", "cells_left_out", "q", "method", "changed"]
BY_UNIT = ["target", "state", "unit", "cells", "difference"]
BACKENDS = {}


def _cells():
    from .. import policy
    return policy.get("min_cells")


def _q():
    from .. import policy
    return policy.get("q")


def backend(name, unit, samples=0, params=None, p=True):
    """Register a backend: unit is what it tests (cells or samples), samples how many samples must hold 10 cells of
    each side for it to be fitted, params the package's settings a call may change ({name: (default, what it does)}),
    p whether it gives a p-value (scCODA gives credible effects instead)."""
    def deco(fn):
        BACKENDS[name] = {"fn": fn, "unit": unit, "samples": samples, "params": dict(params or {}), "p": p}
        return fn
    return deco


def check_params(method, params):
    """Refuse a setting the backend does not have, naming its settings."""
    if method not in BACKENDS:
        raise ValueError(f"no composition backend {method!r}; the backends are {sorted(BACKENDS)}")
    unknown = sorted(set(params or {}) - set(BACKENDS[method]["params"]))
    if unknown:
        raise ValueError(f"no setting {unknown} for {method}; its settings are {sorted(BACKENDS[method]['params'])}")


def sides(screen, target, reference):
    """A target's cells and its reference cells (its arm's; its own cells never among them)."""
    tm = screen.cells_of(target)
    return tm, screen.reference(reference, target=target) & (screen.label != target)


def shared_samples(screen, tm, rm, min_cells=None):
    min_cells = _cells() if min_cells is None else min_cells
    """The samples holding min_cells or more of both sides' cells."""
    from .. import design_fit as fit
    return fit.shared_units(screen, tm, rm, min_cells)


def pooled_shares(lab, tm, rm, state):
    """A state's share of the target's cells and of the reference cells."""
    a, b = int((lab[tm] == state).sum()), int((lab[rm] == state).sum())
    return a / max(int(tm.sum()), 1), b / max(int(rm.sum()), 1)


def by_unit_rows(screen, targets, reference, lab, names):
    """Per target, state and sample with cells on both sides, the share's difference from the reference's."""
    rows = []
    for t in targets:
        mt, mr = sides(screen, t, reference)
        if mt.sum() < _cells() or mr.sum() < _cells():
            continue
        if screen.crossed:  # its reference drawn within its samples' conditions, in its mix
            from ..question_modules.data import seed_of
            mt, mr, _ = screen.matched(mt, mr, _cells(), seed_of(t))
        for u in screen.units():
            mtu, mru = mt & (screen.unit == u), mr & (screen.unit == u)
            if mtu.sum() and mru.sum():
                for s in names:
                    rows.append({"target": t, "state": s, "unit": u, "cells": int(mtu.sum()),
                                 "difference": float((lab[mtu] == s).mean() - (lab[mru] == s).mean())})
    return pd.DataFrame(rows, columns=BY_UNIT)


def compare(screen, targets, reference, lab, method="fisher", workdir=".", control_cells="same_samples", seed=0,
            params=None):
    """Each target's share of cells in each state (lab: each cell's state, '' or 'nan' for a cell without one)
    against its reference, by one backend. Returns (per, by_unit, calls): per in COLUMNS and the backend's own
    columns, changed by the backend's rule."""
    from statsmodels.stats.multitest import multipletests
    params = dict(params or {})
    check_params(method, params)
    lab = np.asarray(lab).astype(str)
    names = sorted(set(lab) - {"", "nan"})
    b = BACKENDS[method]
    kept, not_fitted = [], {}
    for t in targets:
        mt, mr = sides(screen, t, reference)
        if mt.sum() < _cells() or mr.sum() < _cells():
            continue
        if b["samples"]:
            have = shared_samples(screen, mt, mr)
            if len(have) < b["samples"]:
                not_fitted[t] = (f"{len(have)} samples hold {_cells()} cells of the target and of its reference; "
                                 f"{method} needs {b['samples']}")
                continue
        kept.append(t)
    os.makedirs(workdir, exist_ok=True)
    per, calls = b["fn"](screen, kept, reference, lab, names, workdir=workdir, control_cells=control_cells,
                         seed=seed, **params)
    per = per.copy()
    per["method"] = method
    if b["p"] and method != "milo":
        per["q"] = multipletests(per["p"].to_numpy(), method="fdr_bh")[1] if len(per) else []
    if method != "sccoda":
        per["changed"] = per["q"] < _q() if len(per) else []
    extra = [c for c in per.columns if c not in COLUMNS]
    per = per.reindex(columns=COLUMNS + extra)
    for c in calls:
        c["not_fitted"] = sorted(not_fitted)
        if not_fitted:
            c["not_fitted_why"] = not_fitted
    return per.reset_index(drop=True), by_unit_rows(screen, targets, reference, lab, names), calls


def compare_by_leads(screen, leads, reference, lab, workdir=".", control_cells="same_samples", seed=0,
                     params=None):
    """Each target by its own backend ({target: {method, reason}}, the design's leads): one table, Benjamini-Hochberg
    over every row with a p-value from a test on target x state, and the package calls."""
    from statsmodels.stats.multitest import multipletests
    leads = {t: {**ld, "method": BACKEND_OF.get(ld["method"], ld["method"])} for t, ld in leads.items()}
    used = sorted({ld["method"] for ld in leads.values()})
    allowed = {p for m in used for p in BACKENDS[m]["params"]}
    unknown = sorted(set(params or {}) - allowed)
    if unknown:
        raise ValueError(f"no setting {unknown} for {used}; their settings are "
                         f"{ {m: sorted(BACKENDS[m]['params']) for m in used} }")
    parts, units, calls = [], [], []
    for method in BACKENDS:  # the backends' own order
        ts = [t for t, ld in leads.items() if ld["method"] == method]
        if not ts:
            continue
        own = {k: v for k, v in (params or {}).items() if k in BACKENDS[method]["params"]}
        per, by_unit, c = compare(screen, ts, reference, lab, method, os.path.join(workdir, method), control_cells,
                                  seed, own)
        parts.append(per)
        units.append(by_unit)
        calls += c
    per = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=COLUMNS)
    tested = per["method"].map(lambda m: BACKENDS[m]["p"] and m != "milo").to_numpy(bool) if len(per) else []
    if len(per) and tested.any():
        per.loc[tested, "q"] = multipletests(per.loc[tested, "p"].to_numpy(float), method="fdr_bh")[1]
        per.loc[tested, "changed"] = per.loc[tested, "q"] < _q()
    by_unit = pd.concat(units, ignore_index=True) if units else pd.DataFrame(columns=BY_UNIT)
    return per, by_unit, calls


BACKEND_OF = {"fisher_same_samples": "fisher"}  # the design's name of a lead, as a backend


def _row(t, state, lab, tm, rm, screen, **more):
    share, control_share = pooled_shares(lab, tm, rm, state)
    return {"target": t, "state": state, "share": share, "control_share": control_share,
            "mean_difference": share - control_share, "cells": int(tm.sum()), "reference_cells": int(rm.sum()),
            "target_samples": len(set(screen.unit[tm])), **more}


# ---- the backends --------------------------------------------------------------------------------------------------
@backend("fisher", unit="cells")
def _fisher(screen, targets, reference, lab, names, workdir=".", control_cells="same_samples", seed=0):
    """Fisher's exact test on the cells, as C.states_and_composition has always tested."""
    from scipy import stats
    from ..question_modules import packages
    from ..question_modules.c import composition_same_samples
    rows = []
    for t in targets:
        mt, mr = sides(screen, t, reference)
        for s in names:
            if control_cells == "same_samples":
                r = composition_same_samples(screen.unit, lab, mt, mr, s, seed=seed, strata=screen.strata)
            else:
                a1, b1 = int((lab[mt] == s).sum()), int((lab[mr] == s).sum())
                odds, p = stats.fisher_exact([[a1, mt.sum() - a1], [b1, mr.sum() - b1]])
                r = {"share": a1 / mt.sum(), "control_share": b1 / mr.sum(),
                     "mean_difference": float(a1 / mt.sum() - b1 / mr.sum()), "odds_ratio": float(odds),
                     "p": float(p), "test": "Fisher's exact test on cells" + (
                         ", against every control cell, pooled over the conditions inside the samples"
                         if screen.crossed else ""), "cells": int(mt.sum()),
                     "reference_cells": int(mr.sum()), "target_samples": len(set(screen.unit[mt])),
                     "cells_left_out": 0}
            if r["cells"] >= _cells():
                rows.append({"target": t, "state": s, **r})
    own = control_cells == "same_samples"
    calls = [packages.call("scipy", "stats.fisher_exact", contrast="each target's share of cells in a state against "
                           "the control cells'" + ((" of its own samples and conditions, in its mix" if screen.crossed
                                                    else " of its own samples, in its mix") if own else
                                                   (", every one, pooled over the conditions inside the samples"
                                                    if screen.crossed else ""))),
             packages.call("statsmodels", "stats.multitest.multipletests", method="fdr_bh")]
    return pd.DataFrame(rows, columns=[c for c in COLUMNS if c not in ("q", "method", "changed")]), calls


@backend("cmh", unit="cells, stratified by sample", samples=2)
def _cmh(screen, targets, reference, lab, names, workdir=".", control_cells="same_samples", seed=0):
    """The Cochran-Mantel-Haenszel test of a state's share, each sample holding both sides a stratum."""
    from statsmodels.stats.contingency_tables import StratifiedTable
    from ..question_modules import packages
    rows = []
    for t in targets:
        mt, mr = sides(screen, t, reference)
        units = shared_samples(screen, mt, mr)
        tm, rm = mt & np.isin(screen.unit, units), mr & np.isin(screen.unit, units)
        # each sample a stratum; each sample and condition where a sample holds several (Screen.strata)
        strata = units if not screen.crossed else [x for x in sorted(set(screen.strata[tm]))
                                                    if (rm & (screen.strata == x)).any()]
        for s in names:
            tables = []
            for u in strata:
                a_u, r_u = tm & (screen.strata == u), rm & (screen.strata == u)
                a, b = int((lab[a_u] == s).sum()), int((lab[r_u] == s).sum())
                tables.append(np.array([[a, a_u.sum() - a], [b, r_u.sum() - b]], dtype=float))
            if sum(x[0, 0] + x[1, 0] for x in tables) == 0:  # the state is on neither side: nothing to test
                p, odds = 1.0, np.nan
            else:
                st = StratifiedTable(tables)
                with np.errstate(divide="ignore", invalid="ignore"):  # a state on one side only: an infinite ratio
                    p, odds = float(st.test_null_odds().pvalue), float(st.oddsratio_pooled)
            rows.append(_row(t, s, lab, tm, rm, screen, odds_ratio=odds, p=p,
                             test="Cochran-Mantel-Haenszel over the samples" + (
                                 " and conditions" if screen.crossed else ""),
                             cells_left_out=int(mt.sum() - tm.sum())))
    calls = [packages.call("statsmodels", "stats.contingency_tables.StratifiedTable.test_null_odds",
                           contrast="each target's share of cells in a state against its reference's, each sample "
                                    + ("and condition " if screen.crossed else "") + "holding both a stratum",
                           correction=False),
             packages.call("statsmodels", "stats.multitest.multipletests", method="fdr_bh")]
    return pd.DataFrame(rows), calls


@backend("propeller", unit="samples", samples=3,
         params={"transform": ("asin", "the transform of the samples' proportions: asin, or speckle's default "
                                       "logit (its 0.5 pseudo-count lifts a small sample's floor; docs/adr/0031, "
                                       "the measurement of 2026-10-21)"),
                 "robust": (True, "limma's robust empirical Bayes"),
                 "trend": (False, "limma's mean-variance trend")})
def _propeller(screen, targets, reference, lab, names, workdir=".", control_cells="same_samples", seed=0,
               transform="asin", robust=True, trend=False):
    """speckle's propeller.ttest, each target against its reference in the samples both hold
    (pertura/r/propeller_by_target.R)."""
    import subprocess
    from .. import envs
    from ..question_modules import packages
    from ..question_modules.c import R_SLOTS
    if transform not in ("logit", "asin"):
        raise ValueError(f"propeller's transform is logit or asin, not {transform!r}")
    base = os.path.join(workdir, "propeller")
    os.makedirs(os.path.join(base, "cells"), exist_ok=True)
    names_of, kept = {}, {}
    for k, t in enumerate(targets):
        mt, mr = sides(screen, t, reference)
        units = shared_samples(screen, mt, mr)
        tm, rm = mt & np.isin(screen.unit, units), mr & np.isin(screen.unit, units)
        if screen.crossed:  # its reference drawn within its samples' conditions, in its mix
            from ..question_modules.data import seed_of
            tm, rm, _ = screen.matched(tm, rm, _cells(), seed_of(t))
        key = f"t{k + 1:05d}"
        names_of[key], kept[t] = t, (tm, rm, int(mt.sum() - tm.sum()))
        both = tm | rm
        pd.DataFrame({"unit": screen.unit[both].astype(str), "group": np.where(tm[both], "G", "REF"),
                      "state": lab[both]}).to_csv(os.path.join(base, "cells", f"{key}.tsv.gz"), sep="\t",
                                                  index=False)
    with open(os.path.join(base, "states.txt"), "w", encoding="utf-8") as fh:  # every state gets a row
        fh.write("".join(f"{s}\n" for s in names))
    if targets:
        with R_SLOTS:
            p = subprocess.run([envs.rscript(), os.path.join(R_DIR, "propeller_by_target.R"), transform,
                                "TRUE" if robust else "FALSE", "TRUE" if trend else "FALSE"],
                               env=envs.r_env({"PERTURA_COMP_DIR": base, "OMP_NUM_THREADS": "1"}),
                               capture_output=True, text=True)
        if p.returncode != 0:
            raise RuntimeError(f"speckle's propeller failed: {p.stderr[-1500:]}")
    rows = []
    for key, t in names_of.items():
        tm, rm, left = kept[t]
        r = pd.read_csv(os.path.join(base, "results", f"{key}.tsv"), sep="\t", dtype={"state": str})
        for _, x in r.iterrows():
            rows.append(_row(t, x["state"], lab, tm, rm, screen, odds_ratio=np.nan, p=float(x["p"]),
                             test="propeller (speckle), the samples' proportions", cells_left_out=left,
                             prop_ratio=float(x["prop_ratio"]), t_statistic=float(x["t"]),
                             proportion_transform=transform))
    calls = [packages.call("speckle", "getTransformedProps, propeller.ttest", formula="~ 0 + group + sample",
                           contrast="each target against its reference, in the samples both hold", transform=transform,
                           robust=robust, trend=trend, min_samples=3, min_cells_per_sample=_cells()),
             packages.call("statsmodels", "stats.multitest.multipletests", method="fdr_bh")]
    return pd.DataFrame(rows), calls


def _sccoda_one(job):
    """One scCODA model in its own process (JAX on one thread): the target's and its reference's cells, each sample
    a unit and a side. Returns rows of state, final_parameter, inclusion_probability, log2_fold_change, credible."""
    os.environ["JAX_PLATFORMS"] = "cpu"
    os.environ["XLA_FLAGS"] = "--xla_cpu_multi_thread_eigen=false intra_op_parallelism_threads=1"
    os.environ["OMP_NUM_THREADS"] = "1"
    import contextlib
    import io
    import warnings
    warnings.filterwarnings("ignore")
    import anndata as ad
    import numpyro
    import pertpy as pt
    # with numpyro 0.22, pertpy 1.3.0's scCODA fails numpyro's argument checks on every input: its 0.5 pseudo-count
    # makes a sample's total non-integer, and its initial values are refused; the model itself is unchanged
    numpyro.enable_validation(False)
    states, units, side, seed, settings = job
    obs = pd.DataFrame({"state": states, "group": side,
                        "sample": [f"{u}|{g}" for u, g in zip(units, side)]})
    obs.index = [f"c{i}" for i in range(len(obs))]
    a = ad.AnnData(np.zeros((len(obs), 1), np.float32), obs=obs)
    model = pt.tl.Sccoda()
    mdata = model.load(a, type="cell_level", generate_sample_level=True, cell_type_identifier="state",
                       sample_identifier="sample", covariate_obs=["group"])
    mdata = model.prepare(mdata, modality_key="coda", formula="C(group, Treatment('control'))",
                          reference_cell_type=settings["reference_cell_type"])
    with contextlib.redirect_stderr(io.StringIO()):  # the sampler's progress bar
        model.run_nuts(mdata, modality_key="coda", num_samples=settings["num_samples"],
                       num_warmup=settings["num_warmup"], rng_key=int(seed))
    model.set_fdr(mdata, est_fdr=settings["est_fdr"], modality_key="coda")
    eff = model.get_effect_df(mdata, modality_key="coda").reset_index()
    eff.columns = [str(c) for c in eff.columns]
    cell_type = "Cell Type" if "Cell Type" in eff.columns else eff.columns[1]
    reference_state = str(mdata["coda"].var_names[int(mdata["coda"].uns["scCODA_params"]["reference_index"])])
    return [{"state": str(x[cell_type]), "final_parameter": float(x["Final Parameter"]),
             "inclusion_probability": float(x["Inclusion probability"]),
             "log2_fold_change": float(x["log2-fold change"]), "credible": bool(x["Final Parameter"] != 0),
             "reference_state": reference_state} for _, x in eff.iterrows()]


@backend("sccoda", unit="samples", samples=3, p=False,
         params={"num_samples": (10000, "NUTS samples"), "num_warmup": (1000, "NUTS warm-up samples"),
                 "est_fdr": (0.05, "the expected FDR of the credible effects"),
                 "reference_cell_type": ("automatic", "the reference state, or automatic (present in 95% of "
                                                      "samples, the least dispersed)")})
def _sccoda(screen, targets, reference, lab, names, workdir=".", control_cells="same_samples", seed=0,
            num_samples=10000, num_warmup=1000, est_fdr=0.05, reference_cell_type="automatic"):
    """scCODA, one model per target against its reference, in processes of one thread each."""
    import multiprocessing
    from concurrent.futures import ProcessPoolExecutor
    from ..question_modules import packages
    from ..question_modules.c import DE_WORKERS
    settings = {"num_samples": int(num_samples), "num_warmup": int(num_warmup), "est_fdr": float(est_fdr),
                "reference_cell_type": reference_cell_type}
    jobs, kept = [], {}
    for t in targets:
        mt, mr = sides(screen, t, reference)
        units = shared_samples(screen, mt, mr)
        tm, rm = mt & np.isin(screen.unit, units), mr & np.isin(screen.unit, units)
        if screen.crossed:  # its reference drawn within its samples' conditions, in its mix
            from ..question_modules.data import seed_of
            tm, rm, _ = screen.matched(tm, rm, _cells(), seed_of(t))
        both = (tm | rm) & ~np.isin(lab, ["", "nan"])
        kept[t] = (tm, rm, int(mt.sum() - tm.sum()))
        jobs.append((lab[both], screen.unit[both].astype(str), np.where(tm[both], "target", "control"), seed,
                     settings))
    results = []
    if jobs:
        n = max(1, min(DE_WORKERS, len(jobs)))
        with ProcessPoolExecutor(n, mp_context=multiprocessing.get_context("spawn"), initializer=limits.set_threads,
                                 initargs=(limits.worker_threads(n),)) as ex:
            results = list(ex.map(_sccoda_one, jobs))
    rows = []
    for t, res in zip(targets, results):
        tm, rm, left = kept[t]
        for x in res:
            rows.append(_row(t, x["state"], lab, tm, rm, screen, odds_ratio=np.nan, p=np.nan, q=np.nan,
                             test="scCODA (pertpy), credible effects", cells_left_out=left, changed=x["credible"],
                             log2_fold_change=x["log2_fold_change"],
                             inclusion_probability=x["inclusion_probability"],
                             reference_state=x["reference_state"]))
    calls = [packages.call("pertpy", "tl.Sccoda: load, prepare, run_nuts, set_fdr, get_effect_df",
                           formula="C(group, Treatment('control'))",
                           contrast="each target against its reference, in the samples both hold; effects relative "
                                    "to the reference state", numpyro_validation=False, **settings)]
    return pd.DataFrame(rows), calls


@backend("milo", unit="samples, by neighbourhood", samples=3,
         params={"n_neighbors": (15, "the kNN graph's neighbours (scanpy's default, as pertpy's examples)"),
                 "prop": (0.1, "the share of cells sampled as neighbourhood indices"),
                 "solver": ("pydeseq2", "the GLM's solver")})
def _milo(screen, targets, reference, lab, names, workdir=".", control_cells="same_samples", seed=0,
          n_neighbors=15, prop=0.1, solver="pydeseq2"):
    """Milo on each target's and its reference's cells: their kNN graph on the screen's PCA, neighbourhoods, counts
    per sample, ~ sample + group."""
    import warnings
    import anndata as ad
    import pertpy as pt
    import scanpy as sc
    from ..question_modules import packages
    warnings.filterwarnings("ignore")
    rows = []
    for t in targets:
        mt, mr = sides(screen, t, reference)
        units = shared_samples(screen, mt, mr)
        tm, rm = mt & np.isin(screen.unit, units), mr & np.isin(screen.unit, units)
        if screen.crossed:  # its reference drawn within its samples' conditions, in its mix
            from ..question_modules.data import seed_of
            tm, rm, _ = screen.matched(tm, rm, _cells(), seed_of(t))
        both = tm | rm
        idx = np.flatnonzero(both)
        a = ad.AnnData(screen.lognorm[idx].astype(np.float32),
                       obs=pd.DataFrame({"state": lab[idx], "unit": screen.unit[idx].astype(str),
                                         "group": pd.Categorical(np.where(tm[idx], "target", "control"),
                                                                 categories=["control", "target"])},
                                        index=[f"c{i}" for i in idx]))
        a.obs["sample"] = a.obs["unit"] + "|" + a.obs["group"].astype(str)
        from ..quality import variable_genes
        variable_genes(a, n_top_genes=min(2000, a.n_vars))  # receptor segments left out (PF4)
        sc.pp.pca(a, n_comps=min(30, a.n_obs - 1, a.n_vars - 1), mask_var="highly_variable", random_state=seed)
        sc.pp.neighbors(a, n_neighbors=n_neighbors, random_state=seed)
        milo = pt.tl.Milo()
        mdata = milo.load(a)
        milo.make_nhoods(mdata["rna"], prop=prop, seed=seed)
        mdata = milo.count_nhoods(mdata, sample_col="sample")
        milo.da_nhoods(mdata, design="~unit+group", solver=solver)
        milo.annotate_nhoods(mdata, anno_col="state")
        var = mdata["milo"].var
        nh = mdata["rna"].obsm["nhoods"]
        in_t = np.asarray(nh[np.flatnonzero(tm[idx])].sum(0)).ravel()
        in_r = np.asarray(nh[np.flatnonzero(rm[idx])].sum(0)).ravel()
        for j, (name, x) in enumerate(var.iterrows()):
            if not np.isfinite(x.get("PValue", np.nan)):
                continue
            share, control_share = in_t[j] / max(int(tm.sum()), 1), in_r[j] / max(int(rm.sum()), 1)
            rows.append({"target": t, "state": str(x["nhood_annotation"]), "share": share,
                         "control_share": control_share, "mean_difference": share - control_share,
                         "odds_ratio": np.nan, "p": float(x["PValue"]), "q": float(x["SpatialFDR"]),
                         "test": "Milo (pertpy), neighbourhoods", "cells": int(tm.sum()),
                         "reference_cells": int(rm.sum()), "target_samples": len(units),
                         "cells_left_out": int(mt.sum() - tm.sum()), "neighbourhood": str(name),
                         "neighbourhood_cells": int(x.get("Nhood_size", in_t[j] + in_r[j])),
                         "state_fraction": float(x["nhood_annotation_frac"]), "logFC": float(x["logFC"])})
    calls = [packages.call("scanpy", "pp.highly_variable_genes, pp.pca, pp.neighbors", layer="lognorm",
                           n_top_genes=2000, n_comps=30, n_neighbors=n_neighbors, random_state=seed),
             packages.call("pertpy", "tl.Milo: make_nhoods, count_nhoods, da_nhoods, annotate_nhoods",
                           formula="~unit+group", contrast="each target against its reference, in the samples both "
                           "hold", prop=prop, solver=solver, q="Milo's spatial FDR")]
    return pd.DataFrame(rows), calls


R_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "r")
