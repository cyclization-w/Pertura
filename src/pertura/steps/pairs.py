"""The pairs step: each pair of perturbations against its singles (docs/adr/0031, phase 2; E.combinations'
computation). Each label's profile is its cells' log-normalised expression minus the control cells of the same unit,
pooled over the units that hold the pair (over the genes detected in DETECTION of the control cells), so a pair is
read wherever it and both singles have min_cells cells; per pair: the observed and the additive (summed) profiles,
Norman et al. 2019's regression of the pair on its singles (fitted_weights: the two coefficients, their magnitude
and their asymmetry, R²) or the plain sum (plain_sum), the departure's size, and whether each member moves its gene
in the pair's cells. Where 2 or more units hold the pair and both
singles with min_cells cells each, an added check: the departure's agreement between those units, against control
pseudo-pairs drawn at the pair's cell numbers. Per program set, the observed and additive means.

    df, profiles, report = fit_pairs(screen, design, min_cells=20, model="fitted_weights")
"""
import itertools

import numpy as np
import pandas as pd

MODELS = ("fitted_weights", "plain_sum")
def norman_fit(S, y):
    """Norman et al. 2019's regression of a pair's profile y on its singles' (the columns of S): y = S @ c + e, the
    coefficients by least squares."""
    return np.linalg.lstsq(S, y, rcond=None)[0]


def norman_measures(w):
    """The coefficients' magnitude (the length of the coefficient vector, which Norman et al. 2019 set against the
    fitness interaction score) and, for two members of one sign, their asymmetry, |log10(c1 / c2)| (the dominance
    by which they oriented epistatic pairs)."""
    w = np.asarray(w, float)
    dom = float(abs(np.log10(w[0] / w[1]))) if len(w) == 2 and w[0] * w[1] > 0 else np.nan
    return {"magnitude": float(np.sqrt(np.sum(w ** 2))), "dominance": dom}


def fit_pairs(screen, design, min_cells=20, model="fitted_weights", gene_sets="hallmark", gene_lists=None):
    """Each pair against its singles. Returns (df, kept profiles, report)."""
    from ..question_modules.e import DETECTION, _members_act, _program_sets, _units, pseudo_pair_null
    model = "plain_sum" if model == "profiles" else model
    units = _units(screen)
    L = screen.lognorm
    in_units = np.isin(screen.unit, units)
    # each pair's reference (bug register, BR34): its own arm's control cells where its members share one, else every
    # control cell; its singles and their sum are read against the same cells, so they add on one scale
    labs = sorted(set(screen.label[in_units]) - {"control"})
    pair_labels = sorted({lab for lab in labs if "|" in lab or "+" in lab} - {"two sets"})
    ref_of = {c: screen.own_reference(c) for c in pair_labels}
    ctrl = np.logical_or.reduce([ref_of[c] for c in pair_labels]) & in_units if pair_labels else (
        (screen.label == "control") & in_units)
    keep = np.flatnonzero(screen.detected(ctrl, DETECTION))
    genes = screen.genes[keep]
    Lk = L[:, keep]
    sums, bases = {}, {}
    for u in units:
        for lab in labs:
            m = (screen.label == lab) & (screen.unit == u)
            if m.sum():
                sums[(u, lab)] = (np.asarray(Lk[np.flatnonzero(m)].sum(0)).ravel(), int(m.sum()))

    def base(u, c):
        key = (u, screen.arm_or_every(c) if screen.reference_matches else "")
        if key not in bases:
            mr = ref_of[c] & (screen.unit == u)
            bases[key] = np.asarray(Lk[np.flatnonzero(mr)].mean(0)).ravel() if mr.sum() >= min_cells else None
        return bases[key]
    pooled, per_unit, units_of = {}, {}, {}
    for c in pair_labels:
        units_of[c] = [u for u in units if (u, c) in sums and base(u, c) is not None]
        for lab in [c] + c.replace("+", "|").split("|"):
            total, n = 0.0, 0
            for u in units_of[c]:
                if (u, lab) in sums:
                    s_, k = sums[(u, lab)]
                    total, n = total + (s_ - k * base(u, c)), n + k
                    if k >= min_cells:
                        per_unit[(u, c, lab)] = (s_ / k - base(u, c), k)
            if n >= min_cells:
                pooled[(c, lab)] = (total / n, n)
    pairs = sorted(c for c in pair_labels if (c, c) in pooled)
    sets = _program_sets(screen, design, gene_sets, gene_lists, genes)
    rows, testable, kept = [], [], []
    for c in pairs:
        parts = c.replace("+", "|").split("|")
        if not all((c, p) in pooled for p in parts):
            continue
        testable.append(c)
        obs = pooled[(c, c)][0]
        singles_ = [pooled[(c, p)][0] for p in parts]
        add = np.sum(singles_, 0)
        top = np.argsort(-np.abs(obs))[:300]
        # Norman et al. 2019: the pair's profile as a weighted sum of its singles'; on the genes the singles move
        # (each single's top 300, Pertura's choice: chosen on the singles, not on the pair, so the fit is not drawn
        # to the pair)
        S = np.column_stack(singles_)
        sel = np.unique(np.concatenate([np.argsort(-np.abs(s_))[:300] for s_ in singles_]))
        w = norman_fit(S[sel], obs[sel])
        fit = S @ w
        expected = fit if model == "fitted_weights" else add
        kept.append(pd.DataFrame({"pair": c, "gene": genes, "observed": obs, "additive": add, "fitted": fit}))
        slope, _ = np.polyfit(add[top], obs[top], 1)
        # the units that hold the pair and both singles with min_cells cells each: the agreement check's
        per = [(u, per_unit[(u, c, c)][0], sum(per_unit[(u, c, p)][0] for p in parts)) for u in units_of[c]
               if (u, c, c) in per_unit and all((u, c, p) in per_unit for p in parts)]
        per_expected = ([np.column_stack([per_unit[(u, c, m)][0] for m in parts]) @ w for u, _, _ in per]
                        if model == "fitted_weights" else [p[2] for p in per])
        agree = len(per) >= 2

        # two different measures, named apart (a reader took them for one, development session 4): the weighted
        # sum's fit, 1 - SSE/SST over the singles' top genes; and the plain sum's agreement in shape, a squared
        # correlation over the pair's top 300 genes, which ignores scale (a sum that doubles a shared response can
        # correlate well and still overshoot)
        row = {"pair": c, "units": len(units_of[c]), "units_for_agreement": len(per), "cells": pooled[(c, c)][1],
               **{f"cells_{m}": pooled[(c, m)][1] for m in parts}, "model": model,
               "reference": (f"the control cells of {screen.arm_words(c)}" if screen.reference_matches
                             else "the control cells"),
               **{f"weight_{m}": float(v) for m, v in zip(parts, w)},
               **norman_measures(w),
               "fitted_R2": float(1 - np.sum((obs - fit)[sel] ** 2) / max(np.sum((obs[sel] - obs[sel].mean()) ** 2),
                                                                       1e-12)),
               "additive_correlation_sq": float(np.corrcoef(add[top], obs[top])[0, 1] ** 2), "slope": float(slope),
               "interaction_size": float(np.linalg.norm((obs - expected)[top]) / np.linalg.norm(obs[top])),
               "observed_replicate_r": float(np.mean([np.corrcoef(p[1][top], q[1][top])[0, 1]
                                                      for p, q in itertools.combinations(per, 2)])) if agree else np.nan,
               "interaction_replicate_r": float(np.mean([
                   np.corrcoef((p[1] - e1)[top], (q[1] - e2)[top])[0, 1]
                   for (p, e1), (q, e2) in itertools.combinations(list(zip(per, per_expected)), 2)]))
               if agree else np.nan}
        row.update(_members_act(screen, design, c, parts, units_of[c], reference=ref_of[c]))
        if agree:
            sizes = {u: [per_unit[(u, c, c)][1]] + [per_unit[(u, c, m)][1] for m in parts] for u, _, _ in per}
            nulls = pseudo_pair_null(screen, Lk, sizes, model, min_cells, seed=len(rows), reference=ref_of[c])
        else:
            nulls = np.asarray([])
        row["control_pseudo_pairs"] = int(len(nulls))
        row["control_pseudo_pairs_as_high"] = (float(np.mean(nulls >= row["interaction_replicate_r"]))
                                               if len(nulls) else np.nan)
        for name, idx in sets.items():
            row[f"{name}: observed"], row[f"{name}: additive"] = float(obs[idx].mean()), float(add[idx].mean())
        rows.append(row)
    df = pd.DataFrame(rows)
    report = {"pairs": len(pairs), "testable": len(testable), "lacking_a_single": len(pairs) - len(testable),
              "with_the_agreement_check": int((df["units_for_agreement"] >= 2).sum()) if len(df) else 0,
              "model": model, "members_not_acting": int((~df["members_act"].astype(bool)).sum()) if len(df) else 0}
    return df, kept, report
