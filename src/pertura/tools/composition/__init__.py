"""Composition tools: does a group move cells between states? States come from the state set named in the
specification (pertura.states), fitted once without labels. Each group and each construct is tested against the
reference on its own (pairwise), so groups that share cells (research universes) never enter one model.

- propeller (unit pseudobulk:<unit>): speckle's propeller.ttest on arcsine-square-root proportions per sample (unit x group,
  samples with at least min_cells cells), design ~ 0 + group + unit (r/composition_propeller.R, adapted from the
  September pipeline); effect = log2 ratio of mean proportions.
- quasibinomial (unit pseudobulk:<unit>): binomial GLM of state counts per sample, ~ unit + group, dispersion from
  Pearson's chi-square (quasi-binomial); effect = log odds ratio of the group.
- prop_paired (unit pseudobulk:<unit>): paired t-test across units of arcsine-square-root proportions; effect =
  mean difference in proportion.
- fisher_cells (unit cells): Fisher's exact test of the group's and the reference's cells in the state against
  elsewhere; effect = difference in proportion.
Rows: feature = state name; at least 3 shared units for the pseudobulk tools.
"""
import os
import subprocess

import numpy as np
import pandas as pd
from scipy import stats

from ... import envs

R_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "r")
MIN_UNITS = 3
REF = "REF"


def _items(gs):
    return [(g, "all", pos) for g, pos in gs.groups.items()] + \
           [(gs.home[c], c, pos) for c, pos in gs.constructs.items()]


def _table(rows):
    cols = ["group", "construct", "feature", "effect", "p"]
    return pd.concat(rows, ignore_index=True)[cols] if rows else pd.DataFrame(columns=cols)

def paired_t(D):
    """Paired t-test across units of differences D (units x features): mean, p. A difference identical in every
    unit has zero variance: p = 0 if it is not zero (complete separation), 1 if it is."""
    n = len(D)
    m = D.mean(0)
    se = D.std(0, ddof=1) / np.sqrt(n)
    with np.errstate(divide="ignore", invalid="ignore"):
        t = np.where(se > 0, m / se, np.where(m != 0, np.inf, 0.0))
    return m, 2 * stats.t.sf(np.abs(t), n - 1)


def unit_counts(labels, states, pos, units, min_cells):
    """{unit: state counts (len(states),)} for units with at least min_cells of the given cells."""
    u = units[pos]
    code = pd.Categorical(labels[pos], categories=states).codes
    out = {}
    for unit in np.unique(u):
        sel = u == unit
        if sel.sum() >= min_cells:
            out[unit] = np.bincount(code[sel][code[sel] >= 0], minlength=len(states))
    return out


class _Pseudobulk:
    claim_type = "composition"
    unit_kind = "pseudobulk"
    environment = "python"
    packages = ("numpy", "scipy", "statsmodels")

    def run(self, spec, data, gs, workdir):
        labels, states = data.state_labels(spec["state_set"])
        units = data.obs[data.design.unit_col].astype(str).to_numpy()
        min_cells = int(spec["min_cells"])
        ref = unit_counts(labels, states, gs.reference, units, min_cells)
        rows = []
        for g, c, pos in _items(gs):
            gc = unit_counts(labels, states, pos, units, min_cells)
            shared = sorted(set(gc) & set(ref))
            if len(shared) < MIN_UNITS:
                continue
            eff, p = self.test(np.array([gc[u] for u in shared]), np.array([ref[u] for u in shared]))
            rows.append(pd.DataFrame({"group": g, "construct": c, "feature": states, "effect": eff, "p": p}))
        return _table(rows)


class PropPaired(_Pseudobulk):
    name = "prop_paired"
    version = "prop_paired/2"

    @staticmethod
    def test(G, R):
        """G, R: units x states counts. Paired t-test of arcsine-square-root proportions; effect = mean difference in
        proportion. (Version 1 used logit((k + 0.5) / (n + 1)), whose floor at k = 0 depends on n, so a state absent
        from both sides looked shifted whenever the group and the reference differed in size.)"""
        def asin(K):
            return np.arcsin(np.sqrt(K / np.maximum(K.sum(1, keepdims=True), 1)))
        _, p = paired_t(asin(G) - asin(R))
        eff = (G / G.sum(1, keepdims=True) - R / R.sum(1, keepdims=True)).mean(0)
        return eff, p


class QuasiBinomial(_Pseudobulk):
    name = "quasibinomial"
    version = "quasibinomial/1"

    @staticmethod
    def test(G, R):
        import statsmodels.api as sm
        n_u = len(G)
        X = np.column_stack([np.ones(2 * n_u), np.r_[np.zeros(n_u), np.ones(n_u)],
                             np.vstack([np.eye(n_u)[:, 1:]] * 2)])  # intercept, group, unit effects
        tot = np.r_[R.sum(1), G.sum(1)]
        eff, p = np.zeros(G.shape[1]), np.ones(G.shape[1])
        for j in range(G.shape[1]):
            k = np.r_[R[:, j], G[:, j]]
            if k.sum() == 0 or k.sum() == tot.sum():
                continue
            try:
                fit = sm.GLM(np.column_stack([k, tot - k]), X, family=sm.families.Binomial()).fit(scale="X2")
                eff[j], p[j] = fit.params[1], fit.pvalues[1]
            except Exception:  # perfect separation and similar: no estimate
                eff[j], p[j] = 0.0, 1.0
        return eff, p


class Propeller:
    name = "propeller"
    claim_type = "composition"
    unit_kind = "pseudobulk"
    environment = "r45"
    packages = ("speckle", "limma", "data.table")
    version = "propeller/2"

    def run(self, spec, data, gs, workdir):
        labels, states = data.state_labels(spec["state_set"])
        units = data.obs[data.design.unit_col].astype(str).to_numpy()
        base = os.path.join(workdir, "composition")
        os.makedirs(os.path.join(base, "counts"), exist_ok=True)
        names, rows = {}, []
        items = _items(gs)
        known = set(states)
        for k, (g, c, pos) in enumerate([("REF", "REF", gs.reference)] + items):
            name = REF if k == 0 else f"t{k:05d}"
            names[name] = (g, c)
            lab = labels[pos]
            ok = np.isin(lab, list(known))
            df = pd.DataFrame({"unit": units[pos][ok], "group": name, "state": lab[ok]})
            rows.append(df.groupby(["unit", "group", "state"]).size().rename("count").reset_index())
        pd.concat(rows).to_csv(os.path.join(base, "counts", "all__r0.csv"), index=False)
        p = subprocess.run([envs.rscript(), os.path.join(R_DIR, "composition_propeller.R"), str(int(spec["min_cells"]))],
                           env=envs.r_env({"PERTURA_COMP_DIR": base}), capture_output=True, text=True)
        if p.returncode != 0:
            raise RuntimeError(f"R failed ({p.returncode}): {p.stderr[-2000:]}")
        f = os.path.join(base, "r_results", "all__r0__propeller.tsv")
        if not os.path.exists(f) or os.path.getsize(f) == 0:
            return _table([])
        try:
            t = pd.read_csv(f, sep="\t", dtype={"group": str, "state": str})
        except pd.errors.EmptyDataError:
            return _table([])
        gc = [names[n] for n in t["group"]]
        return pd.DataFrame({"group": [x[0] for x in gc], "construct": [x[1] for x in gc], "feature": t["state"].values,
                             "effect": t["effect"].values, "p": t["p"].values})


class FisherCells:
    name = "fisher_cells"
    claim_type = "composition"
    unit_kind = "cells"
    environment = "python"
    packages = ("numpy", "scipy")
    version = "fisher_cells/1"

    def run(self, spec, data, gs, workdir):
        labels, states = data.state_labels(spec["state_set"])
        code = pd.Categorical(labels, categories=states).codes
        rc = np.bincount(code[gs.reference][code[gs.reference] >= 0], minlength=len(states))
        nr = rc.sum()
        rows = []
        for g, c, pos in _items(gs):
            gc = np.bincount(code[pos][code[pos] >= 0], minlength=len(states))
            ng = gc.sum()
            if ng == 0 or nr == 0:
                continue
            p = np.array([stats.fisher_exact([[gc[j], ng - gc[j]], [rc[j], nr - rc[j]]])[1] for j in range(len(states))])
            rows.append(pd.DataFrame({"group": g, "construct": c, "feature": states, "effect": gc / ng - rc / nr, "p": p}))
        return _table(rows)
