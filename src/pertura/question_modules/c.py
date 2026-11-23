"""Question C: what each perturbation does."""
import os
import subprocess
import threading

import numpy as np
import pandas as pd
import scipy.sparse as sp
from scipy import stats

from .. import limits
from ..gene_sets import NAMED
from . import figures
from .contract import REGISTRY, Contract, Result, register
from .data import same_sample_controls, seed_of, value

MIN_CELLS = 10
Q = 0.1  # the false discovery rate at which a gene, program or state counts as changed
GENES_Q = 0.05  # the level of C.genes' design-chosen tests and of a gene finding's checks (docs/adr/0025)


def _genes_methods():
    """C.genes' methods: the design's choice (auto), every method of the genes block (steps.genes.BACKENDS, its own
    seurat3_lr path apart), and the module's own field and rank_test."""
    from ..steps.genes import BACKENDS
    first = ["auto", "wilcoxon_same_samples", "wilcoxon", "edger_ql", "seurat_lr"]
    block = first + sorted(set(BACKENDS) - set(first) - {"seurat3_lr"})
    return block, [*first, "field", "rank_test", "seurat3_lr", *block[len(first):]]


BLOCK_METHODS, GENES_METHODS = _genes_methods()


UNITS_FOR_A_TEST = 3


def q_of_testable(p):
    """q-values over the tests that were made; missing where none was."""
    p = np.asarray(p, float)
    q = np.full(len(p), np.nan)
    ok = np.isfinite(p)
    q[ok] = bh(p[ok])
    return q


def bh(p):
    """Benjamini-Hochberg q-values (missing p-values count as 1)."""
    p = np.where(np.isfinite(p), p, 1.0)
    if not len(p):
        return p
    order = np.argsort(p)
    r = p[order] * len(p) / np.arange(1, len(p) + 1)
    q = np.empty(len(p))
    q[order] = np.minimum(1, np.minimum.accumulate(r[::-1])[::-1])
    return q


def rank_test(ref, Lg):
    """Two-sided Mann-Whitney test of a group's cells against a SortedReference, per gene, with the continuity
    correction and the correction for ties at zero (the ties log-normalised counts have): z and p."""
    Lg = sp.csr_matrix(Lg)
    n, m = Lg.shape[0], ref.m
    N = n + m
    u = ref.u_stat(Lg)
    t0 = (n - np.asarray((Lg > 0).sum(0)).ravel()) + ref.zeros
    var = n * m / 12.0 * ((N + 1) - (t0 ** 3 - t0) / (N * (N - 1.0)))
    with np.errstate(divide="ignore", invalid="ignore"):
        z = (np.abs(u - n * m / 2.0) - 0.5) / np.sqrt(var)
    z = np.where(np.isfinite(z), np.maximum(z, 0), 0) * np.sign(u - n * m / 2.0)
    return z, np.minimum(1.0, 2 * stats.norm.sf(np.abs(z)))


def _mean(L, mask):
    idx = np.flatnonzero(mask)
    return np.asarray(L[idx].sum(0)).ravel() / max(len(idx), 1)


def enough_units(screen, ref, min_cells=MIN_CELLS, units=None):
    """Whether by-unit tests are possible: confirmed biological replicates, `units` of them with min_cells reference
    cells (design_fit.MIN_REPLICATES by default)."""
    from .. import policy
    units = policy.get("min_replicates") if units is None else units
    n = sum(int((ref & (screen.unit == u)).sum()) >= min_cells for u in screen.units())
    return screen.independent_units() and n >= units


# ---- any effect ----------------------------------------------------------------------------------------------------
def energy_test(P, n_t, permutations, rng):
    """The E-distance between the first n_t rows of P and the rest (twice the squared distance between their means,
    in PCA space) and its permutation null: the distance, its z against the null, and the permutation p."""
    n = len(P)
    tot = P.sum(0)

    def dist(sums):
        a = sums / n_t
        b = (tot - sums) / (n - n_t)
        return 2 * ((a - b) ** 2).sum(-1)
    d = float(dist(P[:n_t].sum(0)))
    idx = np.argsort(rng.random((permutations, n)), axis=1)[:, :n_t]
    W = np.zeros((permutations, n))
    np.put_along_axis(W, idx, 1.0, axis=1)
    null = dist(W @ P)
    z = (d - null.mean()) / max(null.std(), 1e-12)
    return d, float(z), float((1 + (null >= d).sum()) / (permutations + 1))


DISTANCE_WORKERS = int(os.environ.get("PERTURA_DISTANCE_WORKERS", "6"))
MIN_REFERENCE_DRAW = 500  # a drawn reference keeps at least this many cells


def _distance_test(job):
    """pertpy's energy-distance permutation test of one target's cells against the control cells, in a worker
    process: (distance, p). Its permutations are drawn from a seeded sequence (packages.seeded_draws), so a rerun
    gives the same p-values."""
    import anndata as ad
    import pertpy as pt
    from .packages import seeded_draws
    _, target_pcs, control_pcs, permutations, seed, *more = job
    metric = more[0] if more else "edistance"
    sub = ad.AnnData(np.zeros((len(target_pcs) + len(control_pcs), 1), np.float32),
                     obs=pd.DataFrame({"group": ["target"] * len(target_pcs) + ["control"] * len(control_pcs)},
                                      index=[f"c{i}" for i in range(len(target_pcs) + len(control_pcs))]))
    sub.obsm["X_pca"] = np.vstack([target_pcs, control_pcs])
    with seeded_draws(seed):
        res = pt.tl.DistanceTest(metric, n_perms=permutations, obsm_key="X_pca")(
            sub, groupby="group", contrast="control", show_progressbar=False)
    return float(res.loc["target", "distance"]), float(res.loc["target", "pvalue"])


@register("C.any_effect", Contract(
    serves="C.any_effect",
    applies_when="always",
    needs="log-normalised expression, labels and the control cells",
    returns="per target: the energy distance between its cells and the control cells of its own samples, in its "
            "mix (same_samples; all: every control cell), in the PCA space of log-normalised highly variable genes "
            "(scanpy), each side drawn to at most 1,000 cells as the overview's distances are, its permutation "
            "p-value (pertpy's DistanceTest, edistance, the target's and those control cells only), "
            "Benjamini-Hochberg over targets (statsmodels), the cells of "
            "each and the target's cells left out (in samples with too few control cells), and the targets ranked "
            "by the distance; the package calls. With fewer_cells, each target's cells drawn to each number given, "
            "3 draws each, and the test again: the share of the targets called on every cell called again, the "
            "figure then",
    fails_when=[("cell numbers, depth or sick cells move the whole profile",
                 "the target's UMIs per cell against the control cells', reported beside the distance"),
                ("the target's cells and the control cells come from different samples, so the distance measures "
                 "the samples", "the control cells of the target's own samples (Pertura's default), with all control "
                 "cells, which published screens pool at least as often, as the alternative")],
    tested_against="Cas12a: the place of the target with by far the most changed genes in an earlier hand "
                   "analysis; the synthetic screen's strongest target ranked first",
    choices={"control_cells": ["same_samples", "all"], "reference": ["control_guides", "other_targets"],
             "n_pcs": [50, 20]},
    inputs={"fewer_cells": "numbers of cells per target, such as [25, 50, 100, 200]: each target with more cells drawn "
                           "to each, 3 draws, and tested again, as published screens down-sample a perturbation's "
                           "cells to see how many its call needs"}))
def any_effect(screen, design, out_dir, reference="control_guides", n_pcs=50, permutations=1000, seed=0,
               control_cells="same_samples", max_reference=None, only=None, fewer_cells=None):
    from .. import blocks as pt
    from . import charts
    if only is not None or max_reference is not None:  # the callers that need the plain test (D.common_response)
        from ..steps import any_effect as step
        # each side drawn to at most CAP cells, as the overview's and the block's test: with every cell, 9 targets of
        # GSE203592 took 2 h 11 min (a 17,523-cell target against three times its cells, 1,000 permutations)
        per, calls, drawn = step.test(screen, reference, n_pcs, permutations, seed, control_cells, max_reference,
                                      only, draw=step.CAP)
        cal, recipe_as_run = None, None
    else:
        with pt.inside(screen, out_dir):
            res, cal = any_effect_recipe(reference, control_cells, n_pcs, permutations, seed)
        per, calls, drawn = res.table, res.calls, res.extra.get("drawn") or {}
        recipe_as_run = pt.recipe_as_run(any_effect_recipe, reference=reference, control_cells=control_cells,
                                         n_pcs=n_pcs, permutations=permutations, seed=seed)
    path = os.path.join(out_dir, "figure.png")
    if per.empty:
        checks = figures.bars(np.zeros(1), ["none"], path, "No target with enough cells", "cells")
        return Result("not_enough_data", per, {"package_calls": calls}, path, checks,
                      "no target with 10 cells beside 10 control cells")
    own = control_cells == "same_samples"
    ref_words = ("the control cells" if reference == "control_guides" else "the other targets' cells") + \
        (" of their own samples" if own else "")
    summary_cal = cal.extra.get("summary") if isinstance(cal, pt.Result) else None
    band = summary_cal.get("groups_95th_percentile") if summary_cal else None
    hits = per[per["q"] < ADJUSTED]
    fewer, by_draw = (fewer_cells_test(screen, per, fewer_cells, reference, n_pcs, permutations, seed, control_cells)
                      if fewer_cells else (None, None))
    if by_draw is not None and by_draw["share_called_again"].notna().any():
        checks = charts.checks(charts.strips(
            by_draw.assign(cells=[f"{n} cells" for n in by_draw["cells_per_target"]]), "share_called_again", "cells",
            path, "Share of the targets called on every cell called again",
            f"Targets called with fewer cells ({FEWER_DRAWS} draws each)", xlim=(0, 1.02)), reference=None)
    else:
        checks = charts.checks(charts.ranked_targets(
            per, "e_distance", path, "E-distance vs " + ("same-sample " if own else "")
            + ("controls" if reference == "control_guides" else "other targets"),
            f"Each target's energy distance from {ref_words}", q="q",
            control_line=None if band is None else
            (band, f"control-guide groups, 95th percentile ({summary_cal['groups']} groups)")))
    words = (f"{len(hits)} of {len(per)} targets differ from {ref_words} at adjusted p < {ADJUSTED} (pertpy's "
             f"energy-distance test, {permutations} permutations)")
    if drawn.get("at_most"):
        words += (f"; each side drawn, seeded, to at most {drawn['at_most']} cells, as the overview's distances are "
                  "(distances compare at the same number of cells)")
    if summary_cal and summary_cal.get("groups"):
        words += (f"; {summary_cal['groups']} groups of control guides through the same test: "
                  f"{len(summary_cal['targets_beyond'])} targets go past their 95th percentile of distance "
                  f"({band:.3g})")
        words += few_groups_words(summary_cal["groups"])
    diagnostics = {"targets_with_an_effect": int(len(hits)), "permutations": permutations,
                   "control_cells": control_cells, "reference": reference, "drawn": drawn, "package_calls": calls}
    if summary_cal is not None:
        diagnostics["calibration"] = summary_cal
    elif cal is not None:
        diagnostics["calibration"] = {"not_run": cal if isinstance(cal, str) else "no result"}
    if recipe_as_run:
        diagnostics["recipe_as_run"] = recipe_as_run
    tables = {"targets": per}
    if fewer is not None:
        tables.update(fewer_cells=fewer, fewer_cells_by_draw=by_draw)
        words += "; " + fewer_cells_words(by_draw, len(hits))
    return Result("detected" if len(hits) else "not_detected", per, diagnostics, path, checks, words,
                  tables=tables)


FEWER_DRAWS = 3  # draws of each number of cells, as a down-sampling is repeated


def fewer_cells_test(screen, per, numbers, reference, n_pcs, permutations, seed, control_cells):
    """Each target's cells drawn to fewer, as published screens down-sample a perturbation's cells to see how many
    its call needs: per number and draw, the energy-distance test again on the targets holding more cells, each drawn
    to that many (its reference cells drawn as in the whole test), Benjamini-Hochberg over those targets. Returns
    (per number, draw and target: q and called; per number and draw: the targets tested, those called on every cell
    among them, and the share of them called again, with the targets called only with fewer cells)."""
    from ..steps import any_effect as step
    embedding = step._embedding(screen, reference, n_pcs, seed)  # one PCA for every draw
    on_every = set(per.loc[per["q"] < ADJUSTED, "target"])
    rows, draws = [], []
    for n in sorted({int(x) for x in numbers}):
        held = per.loc[per["cells"] > n, "target"].tolist()
        for d in range(1, FEWER_DRAWS + 1) if held else ():
            t, _, _ = step.test(screen, reference, n_pcs, permutations, seed + d, control_cells, only=held, draw=n,
                                draw_reference=step.CAP, embedding=embedding)
            called = set(t.loc[t["q"] < ADJUSTED, "target"])
            rows += [{"cells_per_target": n, "draw": d, "target": x, "q": q, "called": x in called,
                      "called_on_every_cell": x in on_every} for x, q in zip(t["target"], t["q"])]
            before = on_every & set(t["target"])
            draws.append({"cells_per_target": n, "draw": d, "targets_tested": len(t),
                          "called_on_every_cell": len(before), "called_again": len(before & called),
                          "share_called_again": len(before & called) / len(before) if before else np.nan,
                          "called_only_with_fewer": len(called - on_every)})
    return (pd.DataFrame(rows, columns=["cells_per_target", "draw", "target", "q", "called", "called_on_every_cell"]),
            pd.DataFrame(draws, columns=["cells_per_target", "draw", "targets_tested", "called_on_every_cell",
                                         "called_again", "share_called_again", "called_only_with_fewer"]))


def fewer_cells_words(by_draw, hits):
    """The down-sampling in words: per number of cells, the share of the targets called on every cell called again
    over the draws."""
    if by_draw.empty:
        return "with fewer cells: no target holds more cells than the numbers given"
    parts = []
    for n, g in by_draw.groupby("cells_per_target"):
        share = g["share_called_again"].dropna()
        parts.append(f"{n} cells: " + (f"{share.mean():.0%} of {int(g['called_on_every_cell'].max())}"
                                       if len(share) else "none of them tested")
                     + (f", {int(g['called_only_with_fewer'].max())} called only with fewer"
                        if g["called_only_with_fewer"].any() else ""))
    return (f"with fewer cells ({FEWER_DRAWS} draws of each number, each target drawn to it; of the {hits} targets "
            f"called on every cell, those holding more cells, called again): " + "; ".join(parts))


def few_groups_words(groups):
    """What a control line at the groups' 95th percentile means when there are few groups: with 20 or fewer, the
    interpolated percentile lies between the two largest, so the largest group is always past it (session 4, 1g)."""
    if not groups or groups > 20:
        return ""
    return (f"; with {groups} groups that line lies between the two largest, so the largest group is always past "
            "it: one control group past the line is what the line means, not a failed calibration")


def any_effect_recipe(reference="control_guides", control_cells="same_samples", n_pcs=50, permutations=1000,
                      seed=0):
    """C.any_effect as blocks (docs/adr/0031, D2): each target's energy distance from its reference, and the same
    test on groups of control guides (their distances' 95th percentile as the line a target goes past)."""
    from .. import blocks as pt
    res = pt.any_effect(reference=reference, control_cells=control_cells, n_pcs=n_pcs, permutations=permutations,
                        seed=seed)
    try:
        cal = pt.calibrate(res)
    except ValueError as e:  # another reference, or too few control guides for groups
        cal = str(e)
    return res, cal


REGISTRY["C.any_effect"].recipe = any_effect_recipe


# ---- genes ---------------------------------------------------------------------------------------------------------
def genes_rank_test(screen, targets, reference, min_cells=MIN_CELLS, detection=0.1):
    """Within each unit, each target's cells against the unit's reference cells: a Mann-Whitney test per gene on
    log-normalised expression (genes detected in `detection` of the unit's reference cells), BH within target and
    unit. Where a sample holds several conditions, within each sample and condition (Screen.strata; its rows' unit
    names both). Rows: target, unit, gene, effect (difference of means), z, p, q."""
    from ..tools.de.wilcoxon import SortedReference
    L = screen.lognorm
    parts = []
    where = np.asarray(screen.strata)
    places = (screen.units() if not screen.crossed else
              sorted(set(where[np.isin(screen.unit, screen.units())])))
    for u in places:
        built = {}  # one sorted reference per arm of the unit (bug register, BR29)
        for t in targets:
            mt = screen.cells_of(t) & (where == u)
            if mt.sum() < min_cells:
                continue
            key = screen.arm_or_every(t)  # the other targets too are its arm's (the block's arms)
            if key not in built:
                mr_ = screen.reference(reference) & (where == u)
                if screen.reference_matches:
                    mr_ &= screen.in_arm(key)
                keep_ = np.flatnonzero(screen.detected(mr_, detection))
                Lu_ = sp.csr_matrix(L[:, keep_])
                built[key] = (mr_, keep_, Lu_, SortedReference(Lu_[np.flatnonzero(mr_)]) if mr_.sum() >= min_cells
                              else None)
            mr, keep, Lu, ref = built[key]
            if ref is None:
                continue
            ref_t = ref  # other targets as the reference: without the target's own cells
            if reference == "other_targets" and (mr & mt).any():
                ref_t = SortedReference(Lu[np.flatnonzero(mr & ~mt)])
            Lg = Lu[np.flatnonzero(mt)]
            z, p = rank_test(ref_t, Lg)
            eff = np.asarray(Lg.mean(0)).ravel() - ref_t.mean
            parts.append(pd.DataFrame({"target": t, "unit": u, "gene": screen.genes[keep], "effect": eff, "z": z,
                                       "p": p, "q": bh(p), "cells": int(mt.sum())}))
    cols = ["target", "unit", "gene", "effect", "z", "p", "q", "cells"]
    return pd.concat(parts, ignore_index=True)[cols] if parts else pd.DataFrame(columns=cols)


R_DIR_MODULES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "r")
CALIBRATION_GROUPS = 10  # negative-control groups at most (MAST is slow)


def de_workers(threads=None):
    """Processes for a step's comparisons, one thread each (R's for FindMarkers, Python's for the tests on cells):
    PERTURA_DE_WORKERS, else `threads` (a module job's, as its server counts them), else this process's threads."""
    return int(os.environ.get("PERTURA_DE_WORKERS") or threads or limits.own_threads())


DE_WORKERS = de_workers()
# every R process of this Python process takes a slot, so calls made side by side (a finding's checks) run no more R
# processes at once than one call does
R_SLOTS = threading.BoundedSemaphore(DE_WORKERS)
ADJUSTED = 0.05  # Seurat's adjusted p-value (Bonferroni), the package's own level


def parts_by_load(costs, n):
    """Each item's part of n: the largest item first, each to the part with the least load so far (the lower part on
    a tie), so the parts end close together whatever the items' order."""
    load, part = [0] * n, [0] * len(costs)
    for i in sorted(range(len(costs)), key=lambda i: (-costs[i], i)):
        k = min(range(n), key=lambda k: (load[k], k))
        part[i], load[k] = k, load[k] + costs[i]
    return np.asarray(part, dtype=int)


CALIBRATION_PREFIX = "ncg_"  # a calibration group's label: a negative-control group of control guides


def calibration_groups(screen, seed=0):
    """Negative-control groups for the calibration check: the control guides with cells, shuffled, in groups the
    size of a target's guides (the median over targets), at most CALIBRATION_GROUPS, leaving at least one group's
    worth of control guides as their reference. Where the design matches references to guide attributes (ADR 0026),
    each group is drawn within one arm, as a target's guides share their arm, and each arm leaves its own reference;
    the arms share the groups by their control guides. Returns {group: [guides]} (empty where there are too few)."""
    ctrl = screen.label == "control"
    guides = sorted(set(screen.top_guide[ctrl]) - {""})
    tg = screen.guides[screen.guides["kind"].astype(str) == "targeting"].groupby("target")["guide"].nunique()
    k = max(1, int(np.median(tg))) if len(tg) else 1
    rng = np.random.default_rng(seed)
    if not getattr(screen, "reference_matches", None):
        n = min(CALIBRATION_GROUPS, len(guides) // k - 1)
        if n < 1:
            return {}
        order = list(rng.permutation(guides))
        return {f"{CALIBRATION_PREFIX}{i + 1}": sorted(order[i * k:(i + 1) * k]) for i in range(n)}
    maps = [screen.attribute(a) for a in screen.reference_matches]
    by_arm = {}
    from .data import key_of
    for g in guides:
        by_arm.setdefault(key_of([m.get(g, "") for m in maps]), []).append(g)
    groups = {}
    for arm, gs in sorted(by_arm.items()):
        n = min(max(1, round(CALIBRATION_GROUPS * len(gs) / len(guides))), len(gs) // k - 1)
        order = list(rng.permutation(gs))
        for i in range(max(0, n)):
            groups[f"{CALIBRATION_PREFIX}{len(groups) + 1}"] = sorted(order[i * k:(i + 1) * k])
    return groups


def calibrate_method(screen, run, reference, level, seed=0, method=None):
    """The calibration check of a method run by its own code (edgeR-QL, the rank test), as genes_field runs it for
    FindMarkers: the method's test on groups of control guides in place of the targets, the rest of the control
    cells as their reference. run(screen, groups) gives the method's table (target, gene, p, q), or the table and
    its package calls. Returns (the calibration, {"calibration_groups": each group's discoveries at q < level})."""
    groups = calibration_groups(screen, seed) if reference == "control_guides" else {}
    lab = screen.label.copy()
    for g, guides in groups.items():  # a group of too few cells stays in the reference, as pt.calibrate leaves it
        m = (screen.label == "control") & np.isin(screen.top_guide, guides)
        if m.sum() >= MIN_CELLS:
            lab = np.where(m, g, lab)
    groups = {g: v for g, v in groups.items() if (lab == g).sum() >= MIN_CELLS}
    if not groups:
        return {"not_run": "the reference is not the control cells" if reference != "control_guides"
                else "too few control guides for a group and its reference"}, {}
    out = run(screen.with_labels(lab), list(groups))
    t, calls = out if isinstance(out, tuple) else (out, [])
    per_group = [{"group": g, "guides": ";".join(groups[g]), "cells": int((lab == g).sum()),
                  "genes_tested": int((t["target"] == g).sum()),
                  "discoveries": int(((t["target"] == g) & (t["q"] < level)).sum())} for g in groups]
    # a group the test could not fit (edgeR-QL without 3 samples holding it; a package call that failed) is not a
    # group with no discovery; it is left out of the counts and listed as not fitted. A genes backend says which
    # (Seurat's tests drop genes by their own filters, so a group without rows there has no discovery).
    if method is not None:
        from ..steps import genes as genes_step
        ok = set(genes_step.fitted(method, t, list(groups), calls))
        fitted = [x for x in per_group if x["group"] in ok]
    else:
        fitted = [x for x in per_group if x["genes_tested"] > 0]
    if not fitted:
        return {"not_run": f"the test could fit none of the {len(per_group)} control-guide groups (pseudobulk needs "
                           "3 samples holding a group and its reference)"}, {"calibration_groups": pd.DataFrame(per_group)}
    pv = t["p"].to_numpy()
    calibration = {"groups": len(fitted), "group_size": len(next(iter(groups.values()))),
                   "groups_not_fitted": len(per_group) - len(fitted),
                   "discoveries": int(sum(x["discoveries"] for x in fitted)), "p_values": int(len(pv)),
                   "share_p_below_0.05": round(float((pv < 0.05).mean()), 4) if len(pv) else None, "not_run": None}
    return calibration, {"calibration_groups": pd.DataFrame(fitted)}


def positive_controls(screen, design, table, level):
    """Each CRISPRi or CRISPRa target's own gene, changed in the expected direction at adjusted p < level (a power
    check); a knockout's own gene is listed but not counted, since a knocked-out gene's transcript can stay."""
    from .b import expected_sign, modality_of
    positives = []
    for tname in sorted(set(table["target"])):
        own = screen.own_gene_of(tname)
        sign = expected_sign(design, screen.label_guide(tname)) if own is not None else 0
        if own is None or sign == 0:
            continue
        row = table[(table["target"] == tname) & (table["gene"] == own)]
        found = bool(len(row) and row["q"].iat[0] < level and np.sign(row["effect"].iat[0]) == sign)
        knockout = modality_of(design, screen.label_guide(tname)) == "crispr_ko"
        positives.append({"target": tname, "gene": own, "expected": int(sign), "found": found,
                          "power_check": not knockout})
    return positives


def positive_control_counts(positives):
    counted = [x for x in positives if x["power_check"]]
    return {"positive_controls": len(counted), "positive_controls_found": int(sum(x["found"] for x in counted)),
            "knockout_target_genes": len(positives) - len(counted),
            "knockout_target_genes_found": int(sum(x["found"] for x in positives if not x["power_check"]))}


# Seurat FindMarkers' min.pct (its default to v4): against hundreds of reference cells at zero, a rank test calls a
# gene seen in one or two of a target's cells, so a gene is tested only where one side detects it this often
MIN_PCT = 0.1
TESTED = f"genes detected in at least {MIN_PCT:.0%} of the target's cells or of the reference's (Seurat's min.pct)"


def _wilcoxon_part(X, labels, genes, targets, ref_label, method="wilcoxon", settings=None):
    """One part of genes_wilcoxon: scanpy's rank_genes_groups on these cells (the part's targets and the reference),
    each target against the reference, by the method (wilcoxon, tie-corrected, or t-test). A gene detected in fewer
    than MIN_PCT of the target's cells and of the reference's is not tested; q is Benjamini-Hochberg over the genes
    tested. A function at the module's top level, so a process can run it."""
    import anndata as ad
    import scanpy as sc
    a = ad.AnnData(X, obs=pd.DataFrame({"g": pd.Categorical(labels)}),
                   var=pd.DataFrame(index=pd.Index(genes).astype(str)))
    a.obs_names = [f"c{i}" for i in range(X.shape[0])]
    a.var_names_make_unique()
    sc.tl.rank_genes_groups(a, "g", groups=list(targets), reference=ref_label, method=method,
                            **({"tie_correct": True} if method == "wilcoxon" else {}), **(settings or {}))
    labels, at = np.asarray(labels).astype(str), pd.Series(np.arange(a.n_vars), index=a.var_names)

    def detected(m):  # each gene's number of cells above zero among the cells m
        return np.asarray((X[m] > 0).sum(axis=0), float).ravel()
    ref = labels == str(ref_label) if ref_label != "rest" else np.ones(len(labels), bool)
    ref_n, ref_seen = int(ref.sum()), detected(ref)
    parts = []
    for x in targets:
        m = labels == str(x)
        seen = detected(m)
        rest_n, rest_seen = (ref_n, ref_seen) if ref_label != "rest" else (ref_n - int(m.sum()), ref_seen - seen)
        tested = (seen / max(int(m.sum()), 1) >= MIN_PCT) | (rest_seen / max(rest_n, 1) >= MIN_PCT)
        d = sc.get.rank_genes_groups_df(a, group=x)
        d = d[tested[at[d["names"].astype(str)].to_numpy()]].reset_index(drop=True)
        parts.append(d.assign(pvals_adj=bh(d["pvals"].to_numpy(float)), target=x))
    return pd.concat(parts, ignore_index=True)


def genes_wilcoxon(screen, targets, reference, workers=None, method="wilcoxon", settings=None):
    """scanpy's Wilcoxon rank-sum test (tl.rank_genes_groups, tie-corrected) of each target's cells against the
    reference cells on log-normalised expression, the genes detected in at least MIN_PCT of the cells on one side,
    Benjamini-Hochberg within each target over them: the test C.genes
    leads with where the design has too few biological replicates (docs/adr/0025), and pt.genes' own. Rows: target,
    unit ('all'), gene, effect (scanpy's log2 fold change), p, q, test. Each target's test uses only its cells and the
    reference's, so the targets are split into parts run by `workers` processes (DE_WORKERS by default), with the
    same numbers as one call."""
    from . import packages
    cols = ["target", "unit", "gene", "effect", "p", "q", "test"]
    targets = list(dict.fromkeys(targets))
    if not len(targets):
        return pd.DataFrame(columns=cols), None
    # the log-normalised values are made here from the raw counts, so log-normalised input would be normalised twice
    packages.check_layer(screen.X, "raw_counts", "scanpy tl.rank_genes_groups (log-normalised from the raw counts)")
    ref_label = {"control_guides": "control", "no_guide": "none"}.get(reference, "rest")
    ref = screen.reference(reference)
    n = max(1, min(DE_WORKERS if workers is None else workers, len(targets)))
    if ref_label == "rest":  # against the other targets' cells scanpy ranks every cell once: parts would only copy them
        n = 1
    chunks = [targets[k::n] for k in range(n)]

    def part(chunk):
        idx = np.flatnonzero(np.isin(screen.label, chunk) | ref)
        return screen.lognorm[idx], screen.label[idx], screen.genes, chunk, ref_label, method, settings or {}

    from .. import progress
    progress.expect(len(targets))
    if n == 1:
        found = [_wilcoxon_part(*part(targets))]
        progress.advance(len(targets))
    else:
        import multiprocessing as mp
        from concurrent.futures import ProcessPoolExecutor, as_completed
        try:
            with ProcessPoolExecutor(n, mp_context=mp.get_context("spawn"), initializer=limits.set_threads,
                                     initargs=(limits.worker_threads(n),)) as ex:
                running = {ex.submit(_wilcoxon_part, *part(c)): k for k, c in enumerate(chunks)}
                found = [None] * len(chunks)
                for f in as_completed(running):  # each part counted as it ends, its tables kept in order
                    found[running[f]] = f.result()
                    progress.advance(len(chunks[running[f]]))
        except PermissionError:  # where starting a process is refused (the agent's confined notebook): one process
            n, found = 1, [_wilcoxon_part(*part(targets))]
    table = pd.concat(found, ignore_index=True).rename(columns={
        "names": "gene", "logfoldchanges": "effect", "pvals": "p", "pvals_adj": "q"})
    order = {x: k for k, x in enumerate(targets)}  # the targets in the order given, each with scanpy's own order
    table = table.iloc[np.argsort(table["target"].map(order).to_numpy(), kind="stable")]
    table = table.assign(unit="all", test="wilcoxon" if method == "wilcoxon" else "ttest")[cols].reset_index(drop=True)
    call = packages.call("scanpy", "tl.rank_genes_groups", data=screen.lognorm, layer="lognorm", method=method,
                         reference=ref_label, **({"tie_correct": True} if method == "wilcoxon" else {}),
                         # the named alternative takes every reference cell: said where that pools a sample's conditions
                         **({"pooled": "every reference cell, pooled over the conditions inside the samples"}
                            if screen.crossed else {}),
                         **(settings or {}), tested=TESTED,
                         correction="Benjamini-Hochberg within each target, over the genes tested", processes=n)
    return table, call


def _each_part(make, keys, n):
    """_wilcoxon_part on each key's part, n processes at a time, sharing this process's threads. make(key) builds the
    part (None: no test), only when a process is about to take it, so that a screen of many targets never holds every
    part at once. Each key counts in the job's progress as its part ends. Returns the tables in the keys' order."""
    from .. import progress
    if n <= 1:
        out = []
        for p in map(make, keys):
            if p is not None:
                out.append(_wilcoxon_part(*p))
            progress.advance()
        return out
    import multiprocessing as mp
    from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
    keys, out = list(keys), {}
    with ProcessPoolExecutor(n, mp_context=mp.get_context("spawn"), initializer=limits.set_threads,
                             initargs=(limits.worker_threads(n),)) as ex:
        running, k = {}, 0
        while k < len(keys) or running:
            while k < len(keys) and len(running) < 2 * n:
                p = make(keys[k])
                if p is not None:
                    running[ex.submit(_wilcoxon_part, *p)] = keys[k]
                else:  # not fitted: done
                    progress.advance()
                k += 1
            if running:
                done, _ = wait(running, return_when=FIRST_COMPLETED)
                for f in done:
                    out[running.pop(f)] = f.result()
                    progress.advance()
    return [out[x] for x in keys if x in out]


def genes_wilcoxon_same_samples(screen, targets, reference, workers=None, settings=None):
    """scanpy's Wilcoxon rank-sum test (tie-corrected) of each target's cells against the reference cells of its own
    samples, in its mix (data.same_sample_controls: from each sample holding the target's cells, that sample's
    reference cells in proportion to the target's cells there, seeded), so that a sample's difference from the
    others is not read as the target's genes. The target's cells in a sample with fewer than MIN_CELLS reference
    cells are left out; a target left with fewer than MIN_CELLS cells is not fitted. Where every cell is in one
    sample, every reference cell is used, as genes_wilcoxon does. Rows as genes_wilcoxon's (test
    wilcoxon_same_samples); the call gives each target's cells kept, its matched reference cells and its cells left
    out."""
    from . import packages
    cols = ["target", "unit", "gene", "effect", "p", "q", "test"]
    targets = list(dict.fromkeys(targets))
    if not len(targets):
        return pd.DataFrame(columns=cols), None
    packages.check_layer(screen.X, "raw_counts", "scanpy tl.rank_genes_groups (log-normalised from the raw counts)")
    strata = np.asarray(screen.strata).astype(str)  # its samples, and its conditions where a sample holds several
    others = screen.single() if reference == "other_targets" else None
    matched, not_fitted = {}, []

    def part(t):  # one target's test: its kept cells, and its matched reference labelled 'control'; None: not fitted
        tm = screen.cells_of(t)
        rm = (others if others is not None else screen.reference(reference, target=t)) & ~tm
        kept, ref, left = same_sample_controls(strata, tm, rm, MIN_CELLS, 0)
        matched[t] = {"cells": int(kept.sum()), "reference_cells": int(ref.sum()), "cells_left_out": int(left)}
        if kept.sum() < MIN_CELLS or ref.sum() < MIN_CELLS:
            not_fitted.append(t)
            return None
        idx = np.flatnonzero(kept | ref)
        return (screen.lognorm[idx], np.where(kept[idx], t, "control"), screen.genes, [t], "control", "wilcoxon",
                settings or {})

    n = max(1, min(DE_WORKERS if workers is None else workers, len(targets)))
    from .. import progress
    progress.expect(len(targets))
    try:
        found = _each_part(part, targets, n)
    except PermissionError:  # where starting a process is refused (the agent's confined notebook): one process
        matched.clear()
        not_fitted.clear()
        n, found = 1, _each_part(part, targets, 1)
    table = (pd.concat(found, ignore_index=True) if found else pd.DataFrame(
        columns=["names", "logfoldchanges", "pvals", "pvals_adj", "target"])).rename(columns={
            "names": "gene", "logfoldchanges": "effect", "pvals": "p", "pvals_adj": "q"})
    table = table.assign(unit="all", test="wilcoxon_same_samples")[cols].reset_index(drop=True)
    words = {"control_guides": "control", "no_guide": "no-guide"}.get(reference, "other targets'")
    own, each = (("own samples and conditions", "each sample and condition's") if screen.crossed
                 else ("own samples", "each sample's"))
    call = packages.call("scanpy", "tl.rank_genes_groups", data=screen.lognorm, layer="lognorm", method="wilcoxon",
                         reference=f"the {words} cells of the target's {own}, in its mix ({each} in "
                                   f"proportion to the target's cells there, seeded; a sample with fewer than "
                                   f"{MIN_CELLS} of them leaves the target's cells there out)",
                         tie_correct=True, **(settings or {}), tested=TESTED,
                         correction="Benjamini-Hochberg within each target, over the genes tested", processes=n)
    call["matched"] = matched
    if not_fitted:
        call["not_fitted"] = not_fitted
    return table, call


def genes_by_leads(screen, leads, reference, workdir, genes=None):
    """Each target by its lead test ({target: {method, reason}}): the genes step's one plumbing (pertura.steps.genes,
    docs/adr/0031), so a module, a notebook block and a finding's checks compare each target with the same reference:
    its own arm's where the design matches references to attributes, without its own cells. genes: only these (a
    finding's), q over them. Returns (table, calls)."""
    from ..steps import genes as step
    return step.compare(screen, leads, reference, workdir, genes=genes)


def _unmatched(screen):
    """A screen cut to one arm, its reference matching already done (pertura.steps.genes.unmatched)."""
    from ..steps import genes
    return genes.unmatched(screen)


TEST_NAMES = {"wilcoxon": "Wilcoxon on cells",
              "wilcoxon_same_samples": "Wilcoxon on cells against the control cells of the same samples",
              "edger_ql": "edgeR-QL pseudobulk by sample",
              "seurat_lr": "Seurat's LR with the sample as a covariate", "ttest": "the t-test on cells (scanpy)",
              "limma_voom": "limma-voom pseudobulk by sample", "deseq2": "DESeq2 pseudobulk by sample",
              "glmgampoi": "glmGamPoi pseudobulk by sample", "pydeseq2": "DESeq2 pseudobulk by sample (pyDESeq2)",
              "mast": "MAST on cells with the sample as a covariate",
              "sceptre": "SCEPTRE against the non-targeting cells"}


def leads_of(screen, targets, reference, method="auto"):
    """The lead test of each target: the design's (docs/adr/0025, section 2) for method auto, else the one asked."""
    from ..design_fit import gene_lead
    if method == "auto":
        return {x: gene_lead(screen, x, reference) for x in targets}
    name = TEST_NAMES.get(method, method)
    return {x: {"method": method, "reason": f"{name}, as asked"} for x in targets}


def lead_words(leads):
    """The leads as a methods sentence: each test with its reason and how many targets it covers."""
    by = {}
    for ld in leads.values():
        by.setdefault(ld["reason"], 0)
        by[ld["reason"]] += 1
    return "; ".join(f"{n} target{'s' if n != 1 else ''} by {why}" for why, n in by.items())


def findmarkers_one(screen, tm, rm, gene):
    """Seurat's FindMarkers of one gene, the cells tm against rm (MAST with the sample where they span two or more
    samples, else Wilcoxon), through pertura/r/findmarkers_de.R: (avg_log2FC, p_val_adj over every gene)."""
    import tempfile
    from .. import envs
    idx = np.flatnonzero(tm | rm)
    test = "MAST" if len(set(screen.unit[idx])) > 1 else "wilcox"
    with tempfile.TemporaryDirectory() as base:
        envs.counts_for_r(os.path.join(base, "counts.mtx.gz"), screen.X[idx])
        with open(os.path.join(base, "genes.tsv"), "w", encoding="utf-8") as fh:
            fh.write("\n".join(screen.genes) + "\n")
        pd.DataFrame({"cell": [f"c{i}" for i in idx], "group": np.where(tm[idx], "a", "b"),
                      "sample": screen.unit[idx]}).to_csv(os.path.join(base, "cells.tsv"), sep="\t", index=False)
        # with one feature Seurat's MAST result loses its dimensions, so two other detected genes come along (the
        # gene's row is the one kept; Seurat's adjusted p-value is over every gene either way)
        det = np.flatnonzero(np.asarray((screen.X[idx] > 0).sum(0)).ravel() > 0)
        others = [screen.genes[j] for j in det if screen.genes[j] != gene][:2]
        pd.DataFrame([{"name": "one", "ident1": "a", "ident2": "b", "test": test,
                       "features": ";".join([gene] + others)}]).to_csv(
            os.path.join(base, "comparisons.tsv"), sep="\t", index=False)
        with R_SLOTS:
            p = subprocess.run([envs.rscript(), os.path.join(R_DIR_MODULES, "findmarkers_de.R"), base],
                               env=envs.r_env({"OMP_NUM_THREADS": "1"}), capture_output=True, text=True)
        f = os.path.join(base, "findmarkers.tsv.gz")
        if p.returncode != 0 or not os.path.exists(f):
            return np.nan, np.nan
        r = pd.read_csv(f, sep="\t", dtype={"gene": str})
    r = r[r["gene"] == gene]
    return (float(r["avg_log2FC"].iat[0]), float(r["p_val_adj"].iat[0])) if len(r) else (np.nan, np.nan)


class _counted:
    """While R parts run, their finished comparisons (a line each in its progress file) counted into the job's
    progress every few seconds, and at the end."""

    def __init__(self, marks, every=5.0):
        import threading
        self.marks, self.every, self.seen = marks, every, 0
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._watch, daemon=True)

    def _count(self):
        from .. import progress
        n = 0
        for m in self.marks:
            try:
                with open(m, encoding="utf-8") as fh:
                    n += sum(1 for line in fh if line.strip())
            except OSError:
                pass
        if n > self.seen:
            progress.advance(n - self.seen)
            self.seen = n

    def _watch(self):
        while not self.stop.wait(self.every):
            self._count()

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.stop.set()
        self.thread.join()
        self._count()


def genes_field(screen, design, targets, reference, workdir, calibrate=True, max_cells=None, test="MAST",
                logfc_threshold=0.1, min_pct=0.01, cell_cycle=False, features=None):
    """C.genes' primary: Seurat's FindMarkers on raw counts (pertura/r/findmarkers_de.R), each target against the
    control cells, MAST (or LR, as test names) with the sample as a latent variable where the design has samples or
    lanes, the two sides compared within the samples both hold; MAST on one sample is Wilcoxon. Changed at Seurat's
    adjusted p < ADJUSTED. With calibrate, the calibration check (docs/adr/0022, section 15, point 1, as corrected;
    reported only): each negative-control group of control guides against the other control cells through the same
    test, and each CRISPRi or CRISPRa target's target gene as a positive control. A knockout target's gene is listed
    but not read as a power check: a knocked-out gene's transcript can stay (nonsense-mediated decay escaped,
    in-frame repair, as published screens say). max_cells: Seurat's own cap on each
    side's cells (max.cells.per.ident), none by default. A comparison FindMarkers could not run is named in the call's
    not_fitted, and a control group among them is left out of the calibration's counts. features: only these genes
    tested (Seurat's filters off for them), each cell still normalised on every gene. Returns (table, per_unit,
    calls, calibration, tables)."""
    from .. import envs
    from . import packages
    if test not in ("MAST", "LR"):
        raise ValueError(f"genes_field's test is MAST or LR, not {test!r}")
    several = len(screen.units()) > 1
    crossed = screen.crossed  # a sample holds several conditions: the condition is a latent variable too
    test = "wilcox" if test == "MAST" and not several and not crossed else test
    latent = (["sample"] if several else []) + (["condition"] if crossed else [])
    rec = packages.call("Seurat", "FindMarkers", data=screen.X, layer="raw_counts",
                        formula=(f"test.use = {test}, latent.vars = {' + '.join(latent)}" if latent and test != "wilcox"
                                 else f"test.use = {test}"),
                        contrast="each target against the control cells" + (
                            (", within the samples and conditions both hold" if crossed else
                             ", within the samples both hold") if latent and test != "wilcox" else ""),
                        covariates=(latent if test != "wilcox" else []) +
                                   (["S_score", "G2M_score"] if cell_cycle and test != "wilcox" else []),
                        normalisation="NormalizeData (LogNormalize, 10,000)", changed=f"p_val_adj < {ADJUSTED}",
                        logfc_threshold=logfc_threshold, min_pct=min_pct,
                        max_cells_per_ident=int(max_cells) if max_cells else "every cell",
                        defaults=("Seurat's" if (logfc_threshold, min_pct) == (0.1, 0.01) and not max_cells else
                                  "changed from Seurat's (logfc.threshold 0.1, min.pct 0.01, every cell)"))
    rec["MAST"] = packages.version("MAST") if test == "MAST" else None
    groups = calibration_groups(screen) if calibrate and reference == "control_guides" else {}
    in_group = {g: name for name, gs in groups.items() for g in gs}
    ref = screen.reference(reference)
    # the control cells outside the calibration groups named by their arm, so a target's reference brings in no other
    # arm's cells when R selects them by name (BR29)
    keys = screen.match_key_of_cells if screen.reference_matches else np.full(len(screen.label), "", dtype=object)
    served = [tuple(sorted(s)) for s in screen.arms_of_cells]  # a cell of two arms' control guides serves both
    arm_no = {k: i for i, k in enumerate(sorted(set(served)))}
    rest = [f"ctrl_rest_{arm_no[k]}" if screen.reference_matches else "ctrl_rest" for k in served]
    label = np.where(ref & (screen.label == "control"),
                     [in_group.get(g, r_) for g, r_ in zip(screen.top_guide, rest)], screen.label)
    if reference == "other_targets":
        label = screen.label.copy()
    use = np.zeros(len(label), bool)
    comparisons = []
    for tname in targets:
        tm = screen.cells_of(tname)
        rm = screen.own_reference(tname, reference) & ~tm  # its arm's control cells, or its arm's other targets
        if tm.sum() < MIN_CELLS or rm.sum() < MIN_CELLS:
            continue
        use |= tm | rm
        ident2 = sorted(set(label[rm]))
        comparisons.append({"name": tname, "ident1": tname, "ident2": ";".join(ident2), "test": test,
                            "features": ";".join(features or [])})
    for name in groups:
        arms_ = set(keys[label == name])  # a group is drawn within one arm: its cells' arm
        in_arm = ref & screen.in_arm(next(iter(arms_))) if screen.reference_matches and len(arms_) == 1 else ref
        others = sorted(set(label[in_arm]) - {name})
        if (label == name).sum() >= MIN_CELLS:
            comparisons.append({"name": name, "ident1": name, "ident2": ";".join(others), "test": test,
                                "features": ";".join(features or [])})
            use |= ref
    base = os.path.join(workdir, "findmarkers")
    os.makedirs(base, exist_ok=True)
    idx = np.flatnonzero(use)
    envs.counts_for_r(os.path.join(base, "counts.mtx.gz"), screen.X[idx])
    with open(os.path.join(base, "genes.tsv"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(screen.genes) + "\n")
    cells_table = pd.DataFrame({"cell": [f"c{i}" for i in idx], "group": label[idx], "sample": screen.unit[idx]})
    if crossed:  # the conditions inside the samples, as a latent variable (pertura/r/findmarkers_de.R)
        cells_table["condition"] = screen.condition[idx]
    if cell_cycle:  # the cells' cell-cycle scores, as latent variables beside the sample
        from ..steps import cell_cycle as cc
        phases, _ = cc.phases(screen)
        cells_table["S"] = phases["S_score"].to_numpy()[idx]
        cells_table["G2M"] = phases["G2M_score"].to_numpy()[idx]
    cells_table.to_csv(os.path.join(base, "cells.tsv"), sep="\t", index=False)
    # the comparisons in parts, each its own single-threaded R process (DE_WORKERS at a time, within the server's
    # thread limit): MAST is slow, and the comparisons are independent
    cmp = pd.DataFrame(comparisons, columns=["name", "ident1", "ident2", "test", "features"])
    n = max(1, min(DE_WORKERS, len(cmp)))
    env = envs.r_env({"OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1",
                      "PERTURA_LOGFC_THRESHOLD": str(float(logfc_threshold)), "PERTURA_MIN_PCT": str(float(min_pct)),
                      **({"PERTURA_MAX_CELLS_PER_IDENT": str(int(max_cells))} if max_cells else {})})
    # a comparison's time grows with the cells on its two sides: the parts are filled by those cells
    sizes = pd.Series(label[idx]).value_counts()
    cells_in = [int(sizes.get(r.ident1, 0)) + sum(int(sizes.get(x, 0)) for x in str(r.ident2).split(";") if x)
                for r in cmp.itertuples()]
    part_of = parts_by_load(cells_in, n)
    for k in range(n):
        cmp[part_of == k].to_csv(os.path.join(base, f"comparisons_{k}.tsv"), sep="\t", index=False)

    def part(k):
        with R_SLOTS:
            pr = subprocess.Popen([envs.rscript(), os.path.join(R_DIR_MODULES, "findmarkers_de.R"), base, str(k)],
                                  env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
            return pr.communicate()[1], pr.returncode

    from concurrent.futures import ThreadPoolExecutor
    from .. import progress
    marks = [os.path.join(base, f"progress_{k}.txt") for k in range(n)]
    for m in marks:  # each part's line per finished comparison, counted afresh
        open(m, "w").close()
    progress.expect(len(cmp))
    with _counted(marks):
        with ThreadPoolExecutor(n) as ex:
            errors = list(ex.map(part, range(n)))
    failed = [e for e, code in errors if code != 0]
    if failed:
        raise RuntimeError(f"Seurat FindMarkers failed: {failed[0][-1500:]}")
    res = pd.concat([pd.read_csv(os.path.join(base, f"findmarkers_{k}.tsv.gz"), sep="\t",
                                 dtype={"name": str, "gene": str}) for k in range(n)], ignore_index=True)
    order = {name: i for i, name in enumerate(cmp["name"].astype(str))}  # the rows in the comparisons' order
    res = res.iloc[np.argsort(res["name"].map(order).to_numpy(), kind="stable")].reset_index(drop=True)
    status = pd.concat([pd.read_csv(os.path.join(base, f"findmarkers_status_{k}.tsv"), sep="\t",
                                    dtype={"name": str, "status": str}, keep_default_na=False) for k in range(n)],
                       ignore_index=True)
    failed = {r["name"]: r["status"] for _, r in status.iterrows() if r["status"] != "ran"}
    rec["not_fitted"] = sorted(t for t in failed if t not in groups)
    if failed:
        rec["not_fitted_why"] = {t: failed[t][:200] for t in sorted(failed)}
    tg = res[~res["name"].isin(groups)]
    table = pd.DataFrame({"target": tg["name"], "unit": "all", "gene": tg["gene"], "effect": tg["avg_log2FC"],
                          "p": tg["p_val"], "q": tg["p_val_adj"], "pct.1": tg["pct.1"], "pct.2": tg["pct.2"],
                          "test": tg["test"], "latent": tg["latent"].fillna("")})
    cal = res[res["name"].isin(groups)]
    # a group FindMarkers could not run is not a group with no discovery: left out of the counts, and named
    per_group = [{"group": g, "guides": ";".join(groups[g]), "cells": int((label == g).sum()),
                  "genes_tested": int((cal["name"] == g).sum()),
                  "discoveries": int(((cal["name"] == g) & (cal["p_val_adj"] < ADJUSTED)).sum())}
                 for g in groups if g not in failed and (label == g).sum() >= MIN_CELLS]
    positives = positive_controls(screen, design, table, ADJUSTED)
    pv = cal["p_val"].to_numpy()
    calibration = {
        "groups": len(per_group), "group_size": len(next(iter(groups.values()))) if groups else None,
        "groups_not_fitted": len(groups) - len(per_group),
        "discoveries": int(sum(x["discoveries"] for x in per_group)),
        "p_values": int(len(pv)), "share_p_below_0.05": round(float((pv < 0.05).mean()), 4) if len(pv) else None,
        **positive_control_counts(positives),
        "not_run": None if groups else ("the reference is not the control cells" if reference != "control_guides"
                                        else "too few control guides for a group and its reference")}
    tables = {"calibration_groups": pd.DataFrame(per_group),
              "calibration_pvalues": cal[["name", "gene", "p_val", "avg_log2FC"]].rename(columns={"name": "group"}),
              "positive_controls": pd.DataFrame(positives)}
    return table, table[["target", "unit", "gene", "effect"]], [rec], calibration, tables


PSEUDOBULK_TOOLS = {  # tool: (the R script, its argument, the package, the functions, the test's words)
    "edger": ("pseudobulk_de.R", "edger", "edgeR", "glmQLFit, glmQLFTest", "edgeR-QL"),
    "limma": ("pseudobulk_de.R", "limma", "limma", "voom, lmFit, eBayes", "limma-voom"),
    "deseq2": ("pseudobulk_de.R", "deseq2", "DESeq2", "DESeq, results (Wald)", "DESeq2"),
    "glmgampoi": ("pseudobulk_glmgampoi.R", "glmgampoi", "glmGamPoi", "glm_gp, test_de", "glmGamPoi"),
}


def genes_edger(screen, targets, reference, workdir, min_cells=MIN_CELLS, tool="edger"):
    """Pseudobulk by unit with edgeR's quasi-likelihood F-test (pertura's R script, ~ unit + group, a target fitted
    where it shares 3 units with the reference; genes in 10% of the samples, then edgeR's filterByExpr), BH within
    target; tool limma (voom), deseq2 (R's DESeq2, Wald) or glmgampoi on the same pseudobulk and design. Rows:
    target, unit ('all'), gene, effect (log2 fold change), p, q; and the per-unit log2 fold changes of counts per
    million (target, unit, gene, effect)."""
    script, argument, package, functions, _ = PSEUDOBULK_TOOLS[tool]
    from .. import envs
    from ..tools.de.pseudobulk import R_DIR, REF, aggregate, r_failure, write_table
    from . import packages
    packages.check_layer(screen.X, "raw_counts", f"{package} {functions} (pseudobulk of raw counts)")
    names = {f"t{k + 1:05d}": t for k, t in enumerate(targets)}
    groups = {n: np.flatnonzero(screen.cells_of(t)) for n, t in names.items()}
    if screen.crossed:  # each target against its own control cells, drawn within its samples' conditions (REF_<t>)
        refs = {}
        for n, t in names.items():
            tm = screen.cells_of(t)
            kept, ref, _ = screen.matched(tm, screen.own_reference(t, reference) & ~tm, min_cells, seed_of(t))
            groups[n], refs[f"{REF}_{n}"] = np.flatnonzero(kept), np.flatnonzero(ref)
        mat, meta = aggregate(screen.X, {**refs, **groups}, screen.unit, screen.genes, min_cells, None, True)
    else:
        mat, meta = aggregate(screen.X, {REF: np.flatnonzero(screen.reference(reference)), **groups}, screen.unit,
                              screen.genes, min_cells, None, True)
    from . import packages
    # the cells' values, before the pseudobulk sums: aggregate casts its sums to integers, so log-normalised input
    # would pass as counts after it
    rec = packages.call(package, functions, data=screen.X, layer="raw_counts",
                        formula="~ unit + group", contrast=(
                            "each target against its control cells drawn from its own samples and conditions, in "
                            "its mix, in the samples they share" if screen.crossed else
                            "each target against the control cells, in the samples they share"),
                        covariates=["sample or lane"], filter="genes in 10% of samples, then "
                        "filterByExpr", min_cells_per_sample=min_cells)
    base = os.path.join(workdir, "pseudobulk")
    os.makedirs(os.path.join(base, "pb"), exist_ok=True)
    write_table(mat, meta, os.path.join(base, "pb", "all__r0"))
    from .. import policy
    least = policy.floor("min_replicates", "method")
    if least < policy.get("min_replicates"):
        policy.note(f"{tool} ran wherever {least} or more replicates hold both sides; Pertura's default for a test "
                    f"over replicates needs {policy.get('min_replicates')}")
    with R_SLOTS:
        p = subprocess.run([envs.rscript(), os.path.join(R_DIR, script), argument, "0"],
                           env=envs.r_env({"PERTURA_EXPR_DIR": base, "PERTURA_MIN_REPLICATES": str(least)}),
                           capture_output=True, text=True)
    failure = r_failure(p.returncode, p.stderr)
    if failure is not None:
        raise failure
    f = os.path.join(base, "r_results", f"all__r0__{argument}.tsv.gz")
    cols = ["target", "unit", "gene", "effect", "p", "q"]
    if not os.path.exists(f) or os.path.getsize(f) == 0:
        return pd.DataFrame(columns=cols), pd.DataFrame(columns=["target", "unit", "gene", "effect"]), rec
    t = pd.read_csv(f, sep="\t", dtype={"group": str, "gene": str})
    t["target"] = t["group"].map(names)
    # per target by its own rows (the R scripts write each target's rows together; a transform does not rely on it)
    t["q"] = t.groupby("group", sort=False)["p"].transform(lambda p: bh(p.to_numpy())) if len(t) else []
    out = pd.DataFrame({"target": t["target"], "unit": "all", "gene": t["gene"], "effect": t["logFC"], "p": t["p"],
                        "q": t["q"]})
    cpm = mat / mat.sum(0).clip(lower=1) * 1e6
    per_unit = []
    for n, tname in names.items():
        own = f"{REF}_{n}" if screen.crossed else REF  # its own drawn reference, or the shared one
        for _, row in meta[meta["group"] == n].iterrows():
            ref = meta[(meta["group"] == own) & (meta["mouse"] == row["mouse"])]
            if ref.empty:
                continue
            lfc = np.log2((cpm[row["sample"]] + 1) / (cpm[ref["sample"].iat[0]] + 1))
            per_unit.append(pd.DataFrame({"target": tname, "unit": row["mouse"], "gene": mat.index,
                                          "effect": lfc.values}))
    pu = pd.concat(per_unit, ignore_index=True) if per_unit else pd.DataFrame(columns=["target", "unit", "gene",
                                                                                      "effect"])
    return out[cols], pu, rec


def genes_sceptre(screen, targets, reference, workdir, n_nonzero_trt_thresh=7, n_nonzero_cntrl_thresh=7,
                  side="both", resampling_approximation="skew_normal"):
    """SCEPTRE (pertura/r/sceptre_de.R): each target's cells against the non-targeting control cells, on the raw
    counts and the screen's guide calls (each cell's strongest guide), every gene a trans pair, its pair filters and
    resampling at their defaults, the sample as a covariate where the cells span two or more. q is Benjamini-Hochberg
    within the target over the genes SCEPTRE tested; SCEPTRE's own family (every pair) as q_package."""
    from .. import envs
    from . import packages
    if reference != "control_guides":
        raise ValueError("sceptre tests each target against the non-targeting cells (reference control_guides)")
    packages.check_layer(screen.X, "raw_counts", "sceptre run_discovery_analysis (raw counts)")
    ref = screen.reference(reference)
    use = ref | np.isin(screen.label, list(targets))
    idx = np.flatnonzero(use)
    base = os.path.join(workdir, "sceptre")
    os.makedirs(base, exist_ok=True)
    envs.counts_for_r(os.path.join(base, "counts.mtx.gz"), screen.X[idx])
    names = pd.Index(screen.genes).astype(str)
    unique = pd.Series(names).where(~names.duplicated(), names + "__" + pd.Series(range(len(names))).astype(str))
    with open(os.path.join(base, "genes.tsv"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(unique) + "\n")
    top = np.asarray(screen.top_guide)[idx].astype(str)
    target = np.where(ref[idx], "non-targeting", screen.label[idx])
    cells = pd.DataFrame({"cell": [f"c{i}" for i in idx], "guide": np.where(top == "", "unknown_" + target, top),
                          "target": target, "sample": screen.unit[idx]})
    if screen.crossed:  # the conditions inside the samples, a covariate beside the sample (pertura/r/sceptre_de.R)
        cells["condition"] = screen.condition[idx]
    cells.to_csv(os.path.join(base, "cells.tsv"), sep="\t", index=False)
    several = len(set(screen.unit[idx])) > 1
    rec = packages.call("sceptre", "import_data, set_analysis_parameters, assign_grnas, run_qc, "
                        "run_discovery_analysis", data=screen.X, layer="raw_counts",
                        formula="~ log(response_n_umis) + log(response_n_nonzero)" + (" + sample" if several else "")
                        + (" + condition" if screen.crossed else ""),
                        contrast="each target's cells against the non-targeting cells", moi="low",
                        n_nonzero_trt_thresh=n_nonzero_trt_thresh, n_nonzero_cntrl_thresh=n_nonzero_cntrl_thresh,
                        side=side, resampling_approximation=resampling_approximation,
                        cell_qc="off (the screen's cells)")
    with R_SLOTS:
        p = subprocess.run([envs.rscript(), os.path.join(R_DIR_MODULES, "sceptre_de.R"), str(n_nonzero_trt_thresh),
                            str(n_nonzero_cntrl_thresh), side, resampling_approximation],
                           env=envs.r_env({"PERTURA_SCEPTRE_DIR": base,
                                                                                 "OMP_NUM_THREADS": "1"}),
                           capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"SCEPTRE failed: {p.stderr[-1500:]}")
    r = pd.read_csv(os.path.join(base, "sceptre.tsv.gz"), sep="\t", dtype={"response_id": str, "grna_target": str})
    r = r[np.isfinite(r["p_value"].to_numpy(dtype=float))]
    back = dict(zip(unique, names))
    out = pd.DataFrame({"target": r["grna_target"], "unit": "all", "gene": r["response_id"].map(back),
                        "effect": r["log_2_fold_change"], "p": r["p_value"]})
    # SCEPTRE's rows interleave the targets: q per target by its own rows, then the rows by target
    out["q"] = out.groupby("target", sort=False)["p"].transform(lambda p: bh(p.to_numpy())) if len(out) else []
    order = out.groupby("target", sort=False).ngroup()
    out = out.iloc[np.argsort(order.to_numpy(), kind="stable")].reset_index(drop=True)
    out["q_package"] = bh(out["p"].to_numpy()) if len(out) else []
    rec["not_fitted"] = sorted(set(targets) - set(out["target"]))
    return out.assign(test="sceptre"), rec


def unit_agreement(changed, per_unit):
    """Per target and unit: the mean over the target's changed genes of the unit's effect, signed by the pooled
    direction (positive: the unit agrees)."""
    rows = []
    for t, c in changed.groupby("target"):
        sign = c.groupby("gene")["effect"].mean().apply(np.sign)
        s = per_unit[(per_unit["target"] == t) & per_unit["gene"].isin(sign.index)]
        for u, su in s.groupby("unit"):
            rows.append({"target": t, "unit": u, "agreement": float((su["effect"].to_numpy()
                                                                     * sign[su["gene"]].to_numpy()).mean()),
                         "changed_genes": int(len(sign))})
    return pd.DataFrame(rows, columns=["target", "unit", "agreement", "changed_genes"])


def genes_recipe(method="auto", reference="control_guides", params=None, targets=None):
    """C.genes as blocks (docs/adr/0031, D2): each target's genes by the design's test for it (or the method asked),
    the same tests on groups of control guides, and each CRISPRi or CRISPRa target's own gene."""
    from .. import blocks as pt
    res = pt.genes(targets=targets, method=method, reference=reference, params=params)
    try:
        cal = pt.calibrate(res)
    except ValueError as e:  # another reference, or too few control guides for groups
        cal = str(e)
    own = res.own_genes()
    return res, cal, own


def _calibration_of(summary, reference):
    """A pt.calibrate summary of one test as C.genes reports it: (the calibration, its groups table)."""
    if summary is None or summary.get("not_run"):
        why = (summary or {}).get("not_run") or ("the reference is not the control cells"
                                                 if reference != "control_guides" else
                                                 "too few control guides for a group and its reference")
        return {"not_run": why}, {}
    groups = pd.DataFrame(summary["groups"])
    return ({"groups": len(groups), "group_size": len(str(groups["guides"].iat[0]).split(";")),
             "groups_not_fitted": len(summary["groups_not_fitted"]), "discoveries": int(groups["discoveries"].sum()),
             "p_values": summary["p_values"], "share_p_below_0.05": summary["share_p_below_0.05"], "not_run": None},
            {"calibration_groups": groups})


@register("C.genes", Contract(
    serves="C.genes",
    applies_when="always",
    needs="raw counts, labels, samples, the control cells; for edgeR-QL, 3 or more samples or lanes that hold the "
          "target and the control cells",
    returns="per target: the genes changed by the test the design chooses for it (auto, docs/adr/0025: edgeR-QL "
            "pseudobulk by replicate where 3 or more biological replicates hold the target's and the control cells, "
            "else scanpy's Wilcoxon on cells against the control cells of the target's own samples, in its mix; "
            "Benjamini-Hochberg q < 0.05), with the test and its reason; up and down (the figure); "
            "changed_genes.tsv: those genes with their effect, p and q; the calibration check (groups of control "
            "guides tested as targets, through each test used: their discoveries) and the positive controls (each "
            "target's target gene); the package calls. Method field: Seurat FindMarkers, MAST with the sample as a "
            "latent variable (adjusted p < 0.05)",
    fails_when=[("cells as the observations can give excess false positives (Squair et al., Nat Commun 2021; Barry "
                 "et al., Genome Biology 2024)",
                 "pseudobulk by replicate leads where the design has 3 or more biological replicates (Pertura's "
                 "choice; Squair et al. 2021 test over replicates); "
                 "elsewhere a finding's checks run pseudobulk by sample where 3 samples hold both sides, and the "
                 "calibration check"),
                ("a target whose cells sit in other samples than the control cells' reads the samples' differences "
                 "as its genes (Barry et al., Genome Biology 2024)",
                 "the test on cells compares each target with the control cells of its own samples, in its mix "
                 "(wilcoxon_same_samples, the default there); the calibration check gives each test's control-guide "
                 "groups"),
                ("many targets change the same genes, a response most perturbations share",
                 "the genes most targets change (res.shared()), then D.common_response: each target against the "
                 "other targets' cells"),
                ("large fold changes of genes the control cells barely express rest on low counts",
                 "the detection rate of the changed genes in the target's and the control cells; a detection filter "
                 "fixed before the test, the same for every group")],
    tested_against="the rank test: CRISPRai's enhancer against promoter activation of one gene, against an "
                   "earlier hand analysis's counts; edgeR-QL: the earlier pipeline's pseudobulk table (the same "
                   "R script and aggregation) on the same groups; the design's lead: the synthetic screen's "
                   "planted genes found by each lead",
    choices={"method": GENES_METHODS, "reference": ["control_guides", "other_targets"]},
    inputs={"groups": "seurat3_lr: {name: [labels]}, the cells tested together, pair labels included (default: "
                      "each target)",
            "reference_labels": "seurat3_lr: the labels of the reference cells, as a paper states them (default: the "
                                "design's, the reference cells of the samples each group is in, with the sample as a "
                                "latent variable)",
            "covariate_by_unit": "seurat3_lr: {unit: value}, a latent variable such as the capture day, as a paper "
                                 "states it (default: the design's, as for reference_labels)",
            "thresholds": "seurat3_lr: {min_pct, logfc_threshold, p_adj, abs_logfc}, as a paper states them",
            "params": "{setting: value}: the tests' package settings changed from their defaults, such as Seurat's "
                      "logfc_threshold (the genes block's settings, pt.help('genes'))",
            "targets": "the targets to test (default: every target with 10 cells), for a question about some of "
                       "them; the calibration is the same for any of them and is computed once per build",
            "genes": "named genes read across the targets (a list), as a heatmap with the first ones' boxes per "
                     "target; or targets_own: each target's own target gene in the other targets' cells, as a network",
            "de": "with genes: an earlier C.genes run read in place of a new test (its ID, or <run>:changed_genes; "
                  "the run's own test of every gene is read)"},
    scope={**{k: ("method", ["seurat3_lr"]) for k in ("groups", "reference_labels", "covariate_by_unit",
                                                     "thresholds")},
           "params": ("method", BLOCK_METHODS),
           "targets": ("method", [*BLOCK_METHODS, "field", "rank_test"]),
           "genes": ("method", [*BLOCK_METHODS, "field", "rank_test"])}))
def genes(screen, design, out_dir, method="auto", reference="control_guides", groups=None, reference_labels=None,
          covariate_by_unit=None, thresholds=None, params=None, targets=None, genes=None, de=None):
    from .. import policy
    if de is not None:  # an earlier run read for named genes: no new test
        if not genes:
            raise ValueError("C.genes reads de for named genes: give genes (a list, or targets_own)")
        table = pd.read_csv(de, sep="\t") if isinstance(de, str) else de
        if targets:
            table = table[table["target"].astype(str).isin(set(map(str, targets)))]
        return named_genes_result(screen, table, genes, policy.get("q"), out_dir, "the earlier run given as de")
    wanted = [str(g) for g in ([genes] if isinstance(genes, str) else genes or []) if g != "targets_own"]
    held = set(map(str, screen.adata.var_names))
    if wanted and not held & set(wanted):  # said before the test, not after it (GSE190604: 15 minutes, then none)
        path = os.path.join(out_dir, "figure.png")
        checks = figures.bars(np.zeros(1), ["none"], path, "None of the named genes is in the screen", "genes")
        return Result("not_enough_data", pd.DataFrame(), {"genes_not_in_the_screen": wanted[:50]}, path, checks,
                      f"none of the named genes ({', '.join(wanted[:8])}) is among the screen's {len(held):,} genes, "
                      f"named like {', '.join(map(str, screen.adata.var_names[:3]))}; nothing was tested")
    if method == "seurat3_lr":
        return genes_seurat3_lr(screen, out_dir, groups, reference_labels, reference, covariate_by_unit, thresholds)
    every = screen.targets(policy.get("min_cells"))
    if targets:
        named, unknown = policy.named_targets(screen.targets, [str(t) for t in targets])
        if unknown:
            raise ValueError(f"no target {sorted(unknown)} with {policy.COMPUTABLE['min_cells']} cells, the least a "
                             f"test can be computed on; the targets are {every[:40]}")
        every = sorted(set(every) | set(named))
    targets = [t for t in every if not targets or t in set(map(str, targets))]
    calibration, extra, leads, how, p95_by = None, {}, None, None, {}
    recipe_as_run = None
    if method in BLOCK_METHODS:  # the genes block's methods: the module is their recipe (one door's rules)
        from .. import blocks as pt
        asked = targets if len(targets) < len(every) else None
        with pt.inside(screen, out_dir):
            res, cal, own = genes_recipe(method, reference, params, asked)
        recipe_as_run = pt.recipe_as_run(genes_recipe, method=method, reference=reference, params=params,
                                         **({"targets": asked} if asked else {}))
        leads, table, calls = res.extra["leads"], res.table, res.calls
        summary_of = cal.extra.get("summary", {}) if isinstance(cal, pt.Result) else None
        each = (summary_of.get("by_method") or {}) if summary_of else {}
        if summary_of and not each:
            each = {sorted({ld["method"] for ld in leads.values()})[0]: summary_of} if leads else {}
        used = sorted({ld["method"] for ld in leads.values()})
        main = max(used, key=lambda m: sum(ld["method"] == m for ld in leads.values())) if used else None
        by_method = {m: _calibration_of(each.get(m), reference) for m in used}
        calibration, extra = by_method.get(main, ({"not_run": "no target to test"}, {}))
        calibration = dict(calibration)  # a copy: the main method's calibration also sits in by_method
        if len(by_method) > 1:
            calibration["by_method"] = {m: dict(c) for m, (c, _) in by_method.items()}
        p95_by = {m: float(np.percentile(e["calibration_groups"]["discoveries"], 95))
                  for m, (c, e) in by_method.items() if not c.get("not_run") and "calibration_groups" in e}
        positives = own.to_dict("records")
        if not calibration.get("not_run"):
            calibration.update(positive_control_counts(positives))
        extra = {**extra, "positive_controls": own}
        level, used_word, how = policy.get("q"), method, lead_words(leads)
    elif method == "field":
        table, _, calls, calibration, extra = genes_field(screen, design, targets, reference, out_dir)
        level, used_word = ADJUSTED, "field"
    else:
        table = genes_rank_test(screen, targets, reference)
        calls = [{"package": "pertura", "function": "a rank test within each sample" + (
                      " and condition" if screen.crossed else "") + " (its own code, until the module is converted)",
                  "layer": "raw_counts"}]
        calibration, extra = calibrate_method(screen, lambda s, g: genes_rank_test(s, g, reference), reference, Q)
        level, used_word = (policy.get("q") if "q" in policy.changed() else Q), "rank_test"
    least_effect = policy.get("min_effect")
    changed = table[(table["q"] < level) & ((table["effect"].abs() >= least_effect) if least_effect is not None
                                            else True)]
    summary = []
    for x in targets:
        c = changed[changed["target"] == x]
        tested = table[table["target"] == x]
        row = {"target": x, "tested": bool(len(tested)), "genes_changed": int(c["gene"].nunique()),
               "up": int(c[c["effect"] > 0]["gene"].nunique()), "down": int(c[c["effect"] < 0]["gene"].nunique()),
               "units": int(tested["unit"].nunique()) if used_word == "rank_test" else
               len(set(screen.unit[screen.cells_of(x)]))}
        if leads is not None:
            row["test"] = leads[x]["method"]
        summary.append(row)
    summary = pd.DataFrame(summary)
    path = os.path.join(out_dir, "figure.png")  # the per-unit agreement figure is out (docs/adr/0022, section 4)
    ran = bool(calibration) and not calibration.get("not_run") and "calibration_groups" in extra
    control_counts = [int(x) for x in extra["calibration_groups"]["discoveries"]] if ran else None
    checks = _genes_figures(table, summary, changed, path, level, control_counts=control_counts)
    if control_counts:  # each target's changed genes against the control-guide groups' of its own test
        p95 = float(np.percentile(control_counts, 95))
        if leads is not None and "test" in summary:
            line = summary["test"].map(p95_by)
            summary["beyond_control_groups"] = line.notna() & (summary["genes_changed"] > line.fillna(np.inf))
        else:
            summary["beyond_control_groups"] = summary["genes_changed"] > p95
        calibration["targets_beyond_control_groups_95th"] = int(summary["beyond_control_groups"].sum())
        calibration["control_groups_95th_percentile"] = p95
    if leads is not None and "test" in summary and calibration and calibration.get("by_method"):
        for m, each in calibration["by_method"].items():  # each test's targets against its own groups
            mine = summary[summary["test"] == m]
            each["targets"] = int(len(mine))
            if m in p95_by and "beyond_control_groups" in summary:
                each["control_groups_95th_percentile"] = p95_by[m]
                each["targets_beyond_control_groups_95th"] = int(mine["beyond_control_groups"].sum())
    hits = summary[summary["genes_changed"] > 0]["target"].tolist() if len(summary) else []
    if leads is not None:
        words = f"q < {GENES_Q} ({how})"
    elif used_word == "field":
        words = f"adjusted p < {ADJUSTED} (Seurat FindMarkers, {calls[0]['formula']})"
    else:
        words = f"q < {Q} (rank_test)"
    tops = top_genes(changed, summary)
    diagnostics = {"method": used_word, "reference": reference, "targets_with_changed_genes": len(hits),
                   "multiple_testing": {"tests": int(len(table))},
                   "package_calls": calls, "calibration": calibration,
                   "replicates": how or "cells within each sample or lane are the observations",
                   "top_genes": tops}
    if recipe_as_run:  # the block calls this run made, to paste into run_code and change one step (docs/adr/0031)
        diagnostics["recipe_as_run"] = recipe_as_run
    if method == "edger_ql":
        diagnostics["replicates"] = ("pseudobulk by biological replicate" if screen.independent_units() else
                                     "pseudobulk by sample or lane (technical replicates)")
    if leads is not None:
        diagnostics["leads"] = {x: {"method": ld["method"], "reason": ld["reason"]} for x, ld in leads.items()}
    words = (f"{len(hits)} of {len(summary)} targets change genes at " + words
             + (_calibration_words(calibration) if calibration else "") + top_genes_words(tops))
    biology = genes_biology(changed, summary)
    if genes:  # the named genes lead the reply: their figure and lines, the test's own beside them
        path = os.path.join(out_dir, "figure_named.png")
        named, checks, lines, missing, boxes = named_genes_view(screen, table, genes, level, path)
        extra = {**extra, "named_genes": named}
        diagnostics.update({"named_genes": genes, "not_tested": missing, "boxes": boxes})
        biology = lines + biology[:max(0, BIOLOGY_LINES - len(lines))]
    return Result("detected" if hits else ("not_detected" if len(table) else "not_enough_data"), summary,
                  diagnostics, path, checks, words, tables={"changed_genes": changed, **extra}, biology=biology)


def top_genes(changed, summary, targets=5, genes=5):
    """For the targets that change the most genes, their top changed genes up and down: ranked by the test's p, then
    by the size of the change, among the genes the test took and called changed. Ranking by fold change alone puts
    genes near zero on both sides first (the first-look rehearsal). {target: {"up": [...], "down": [...]}}."""
    if summary is None or not len(summary) or changed is None or not len(changed):
        return {}
    out = {}
    lead = summary[summary["genes_changed"] > 0].sort_values("genes_changed", ascending=False)["target"]
    for x in lead.head(targets):
        c = changed[changed["target"] == x].assign(size=lambda t: t["effect"].abs())
        c = c.sort_values(["p", "size"], ascending=[True, False]).drop_duplicates("gene")
        out[str(x)] = {"up": c[c["effect"] > 0]["gene"].astype(str).head(genes).tolist(),
                       "down": c[c["effect"] < 0]["gene"].astype(str).head(genes).tolist()}
    return out


BIOLOGY_LINES = 25  # a result's biology in the reply, at most these lines; its tables hold the rest
NAMED_BOXES, BOXED_TARGETS = 3, 12  # the first named genes drawn as boxes, each with the targets moving it most


def named_genes_view(screen, table, genes, level, path):
    """Named genes across the targets, from a gene test's table (target, gene, effect, q): each target's effect on
    each gene, a heatmap at path; or, with genes "targets_own", each target's own target gene in the other targets'
    cells, a network of the changes at q < level (a target's target gene in its own cells is its knockdown,
    B.efficacy). The first
    named genes' expression per target is drawn as boxes beside it. Returns (the table, the figure's checks, the
    biology lines, the genes the test did not hold, the boxes' files)."""
    from . import charts
    t = table.assign(target=table["target"].astype(str), gene=table["gene"].astype(str))
    own = isinstance(genes, str) and genes == "targets_own"
    if own:
        whose = {screen.own_gene_of(x): x for x in sorted(set(t["target"])) if screen.own_gene_of(x)}
        named = t[t["gene"].isin(whose)].assign(of=lambda d: d["gene"].map(whose))
        named, wanted = named[named["of"] != named["target"]], list(whose)
    else:
        wanted = [str(g) for g in ([genes] if isinstance(genes, str) else genes)]
        named = t[t["gene"].isin(wanted)]
    named = named.assign(changed=named["q"] < level).reset_index(drop=True)
    missing = sorted(set(wanted) - set(named["gene"]))
    hit = named[named["changed"]]
    if own and len(hit):
        edges = hit[["target", "of", "effect"]].rename(columns={"target": "source", "of": "target"})
        checks = charts.checks(charts.network(edges, path, "Targets acting on each other's target genes"),
                               reference=None, zero=True)
    elif len(named):
        checks = _target_by_column_heatmap(named, "gene", "effect", path, "Named genes in each target",
                                           "log2 fold change vs control cells")
    else:
        checks = figures.bars(np.zeros(1), ["none"], path, "None of the named genes was tested", "genes")
    lines = []
    if own:  # each perturbed target, the most changes first: the other targets' genes it moves
        for x, h in sorted(hit.groupby("target"), key=lambda kv: -len(kv[1]))[:BIOLOGY_LINES]:
            ways = [f"{w} " + ", ".join(f"{r.gene} ({r.effect:+.2f})" for r in d.itertuples())
                    for w, d in (("raises", h[h["effect"] > 0]), ("lowers", h[h["effect"] < 0])) if len(d)]
            lines.append(f"{x}: " + "; ".join(ways))
    else:  # each named gene: the targets that move it
        for g in [g for g in wanted if g in set(hit["gene"])][:BIOLOGY_LINES]:
            h = hit[hit["gene"] == g].sort_values("effect")
            ways = [f"{w} in " + ", ".join(f"{r.target} ({r.effect:+.2f})" for r in d.itertuples())
                    for w, d in (("lower", h[h["effect"] < 0]), ("higher", h[h["effect"] > 0].iloc[::-1])) if len(d)]
            lines.append(f"{g}: " + "; ".join(ways))
        quiet = [g for g in wanted if g not in set(hit["gene"]) and g not in missing]
        if quiet and len(lines) < BIOLOGY_LINES:
            lines.append(f"no target changes {', '.join(quiet[:20])} at q < {level}")
    boxes = []
    folder = os.path.dirname(path)
    libsize = np.maximum(np.asarray(screen.libsize, dtype=float), 1)
    for g in [g for g in wanted if screen.gene(g) is not None and g in set(named["gene"])][:NAMED_BOXES]:
        moved = named[named["gene"] == g].assign(size=lambda d: d["effect"].abs())
        moved = moved.sort_values(["changed", "size"], ascending=False).head(BOXED_TARGETS)
        values = np.log1p(screen.gene(g) * 1e4 / libsize)
        groups = {"control": screen.reference("control_guides")}
        groups.update({x: screen.cells_of(x) for x in moved["target"]})
        tidy = pd.concat([pd.DataFrame({"group": k, "value": values[m]}) for k, m in groups.items()],
                         ignore_index=True)
        box = os.path.join(folder, f"figure_boxes_{g}.png")
        charts.strips(tidy, "value", "group", box, f"{g}, log normalised expression", title=f"{g} in each target",
                      order=list(groups)[::-1], points_at_most=300)  # drawn from the bottom: the control on top
        boxes.append(os.path.basename(box))
    return named, checks, lines, missing, boxes


def named_genes_result(screen, table, genes, level, out_dir, read_from):
    """C.genes reading named genes from an earlier run (de) in place of a new test."""
    named, checks, lines, missing, boxes = named_genes_view(screen, table, genes, level,
                                                            os.path.join(out_dir, "figure.png"))
    hit = named[named["changed"]] if len(named) else named
    what = "the targets' target genes" if isinstance(genes, str) and genes == "targets_own" else "named genes"
    return Result("detected" if len(hit) else ("not_detected" if len(named) else "not_enough_data"), named,
                  {"named_genes": genes, "not_tested": missing, "boxes": boxes, "read_from": read_from},
                  os.path.join(out_dir, "figure.png"), checks,
                  f"{int(hit['gene'].nunique()) if len(hit) else 0} {what} changed in "
                  f"{int(hit['target'].nunique()) if len(hit) else 0} targets at q < {level}, read from {read_from}"
                  + (f"; not tested there: {', '.join(missing[:10])}" if missing else ""), biology=lines)


def genes_biology(changed, summary, most=BIOLOGY_LINES):
    """Each target past its control-guide line (each that changes genes, where no line was drawn), the most genes
    first: its changed genes, up and down, and the top ones of each, as top_genes ranks them."""
    if summary is None or not len(summary) or "genes_changed" not in summary:
        return []
    lined = "beyond_control_groups" in summary
    past = summary[(summary["genes_changed"] > 0) & (summary["beyond_control_groups"].astype(bool) if lined else True)]
    past = past.sort_values("genes_changed", ascending=False)
    tops = top_genes(changed, past, targets=most)
    lines = []
    for r in past.head(most).itertuples():
        g = tops.get(str(r.target)) or {"up": [], "down": []}
        lines.append(f"{r.target}: {r.genes_changed} genes, {r.up} up and {r.down} down"
                     + "".join(f"; {side} {', '.join(g[side])}" for side in ("up", "down") if g[side]))
    if len(past) > most:
        lines.append(f"{len(past) - most} more targets {'past the line' if lined else 'change genes'}: the table "
                     "changed_genes holds them")
    return lines


def states_biology(per, most=BIOLOGY_LINES):
    """Each target x state pair changed, the largest difference of shares first: the target's share of the state
    against the control cells', and the pair's q."""
    hits = per[per["changed"].astype(bool)] if "changed" in per and len(per) else per.iloc[:0]
    if "mean_difference" in hits:
        hits = hits.reindex(hits["mean_difference"].abs().sort_values(ascending=False).index)
    lines = []
    for r in hits.head(most).to_dict("records"):
        share, ref, q = (r.get(k, np.nan) for k in ("share", "control_share", "q"))
        said = (f"{share:.0%} of its cells against {ref:.0%} of the control cells"
                if np.isfinite(share) and np.isfinite(ref) else f"changed ({r.get('method')})")
        lines.append(f"{r['target']} in {r.get('state_name', r['state'])}: {said}"
                     + (f" (q {q:.2g})" if np.isfinite(q) else ""))
    if len(hits) > most:
        lines.append(f"{len(hits) - most} more target x state pairs changed: the result table holds them")
    return lines


def programs_biology(hits, most=BIOLOGY_LINES):
    """Each target x program pair changed, the largest effect first: its score's difference from the control
    cells', and the pair's q."""
    if not len(hits):
        return []
    hits = hits.reindex(hits["effect"].abs().sort_values(ascending=False).index)
    lines = [f"{r['target']} in {r['score']}: {r['effect']:+.3g} against the control cells (q {r['q']:.2g})"
             for r in hits.head(most).to_dict("records")]
    if len(hits) > most:
        lines.append(f"{len(hits) - most} more target x program pairs changed: the result table holds them")
    return lines


def top_genes_words(tops, shown=3):
    """The strongest targets' top genes, as a sentence's end."""
    parts = []
    for x, g in list(tops.items())[:shown]:
        bits = ([f"up {', '.join(g['up'])}"] if g["up"] else []) + ([f"down {', '.join(g['down'])}"] if g["down"] else [])
        if bits:
            parts.append(f"{x} ({'; '.join(bits)})")
    return f"; the strongest targets' top genes: {'; '.join(parts)}" if parts else ""


REGISTRY["C.genes"].recipe = genes_recipe  # its methods auto, wilcoxon, edger_ql and seurat_lr run as this recipe

def _calibration_words(c):
    if c.get("not_run"):
        return f"; the calibration check was not run ({c['not_run']})"
    each = c.get("by_method") or {}
    if len(each) > 1:  # each test through its own groups, so that one test's calibration does not stand for another's
        parts = []
        for m, x in each.items():
            name = TEST_NAMES.get(m, m)
            if x.get("not_run"):
                parts.append(f"{name}: not run ({x['not_run']})")
                continue
            part = f"{name}: {x['groups']} groups gave {x['discoveries']} discoveries"
            if x.get("targets_beyond_control_groups_95th") is not None:
                part += (f", and {x['targets_beyond_control_groups_95th']} of its {x['targets']} targets change "
                         f"more genes than those groups' 95th percentile ({x['control_groups_95th_percentile']:.0f})")
            parts.append(part)
        words = f"; groups of {c['group_size']} control guides tested as targets, through each test: " + "; ".join(
            parts)
    else:
        words = (f"; {c['groups']} groups of {c['group_size']} control guides tested as targets gave "
                 f"{c['discoveries']} discoveries")
        if c.get("targets_beyond_control_groups_95th") is not None:
            words += (f"; {c['targets_beyond_control_groups_95th']} targets change more genes than the control "
                      f"groups' 95th percentile ({c['control_groups_95th_percentile']:.0f})")
    if c.get("positive_controls"):
        words += f"; {c['positive_controls_found']} of {c['positive_controls']} CRISPRi or CRISPRa target genes found"
    if c.get("knockout_target_genes"):
        words += (f"; knockout targets' target genes ({c['knockout_target_genes_found']} of "
                  f"{c['knockout_target_genes']} lower) are not read as a power check, since a knocked-out gene's "
                  "transcript can stay")
    return words


def name_states(a, clusters, state_markers):
    """Each cluster named the state whose marker genes it expresses most: the markers' expression z-scored over the
    cells, averaged per state, then per cluster. Returns the cells' named states and {cluster: state}."""
    X = a.X
    names = {}
    scores = {}
    genes = {g: i for i, g in enumerate(np.asarray(a.var_names).astype(str))}
    for state, markers in state_markers.items():
        idx = [genes[g] for g in markers if g in genes]
        if not idx:
            continue
        m = X[:, idx].toarray() if sp.issparse(X) else np.asarray(X[:, idx])
        z = (m - m.mean(0)) / np.maximum(m.std(0), 1e-9)
        scores[state] = z.mean(1)
    if not scores:
        raise ValueError("no marker gene of any state is in the screen")
    table = pd.DataFrame(scores)
    table["cluster"] = clusters
    means = table.groupby("cluster").mean()
    names = means.idxmax(axis=1).to_dict()
    return np.array([names[c] for c in clusters]), names


def composition_figures(cells, by_unit, chosen, ref, groups, states, out_dir, first=None, n_maps=5, min_units=3):
    """The states' charts from published screens: each group's state composition beside the control row (the mean over
    units, the first state's spread over units as an error bar), and where the control cells and the groups that move
    the first state most lie on the embedding. Groups seen in fewer than min_units units are left out of both. Returns
    {name: path} and the layout checks' issues of both (figures.check_figure), each named by its figure."""
    from matplotlib.patches import Patch
    enough = set(chosen.loc[chosen["units"] >= min_units, "group"])
    chosen = chosen[chosen["group"].isin(enough)]
    first = first if first in states else states[0]
    order = [first] + [s for s in states if s != first]
    colours = dict(zip(order, [figures.OKABE_ITO[3], figures.OKABE_ITO[0], figures.OKABE_ITO[4],
                               figures.OKABE_ITO[2], figures.OKABE_ITO[1], figures.OKABE_ITO[5]]))
    shown = set(chosen["group"])
    u = by_unit[by_unit["group"].isin(shown)]
    target_of = dict(zip(u["group"], u["target"]))
    mean = u.pivot_table(index="group", columns="state", values="share", aggfunc="mean", dropna=False)
    sem = u.pivot_table(index="group", columns="state", values="share", aggfunc="sem", dropna=False)
    refs = u.drop_duplicates(["unit", "state"])
    mean.loc["control"] = refs.groupby("state")["reference share"].mean()
    sem.loc["control"] = refs.groupby("state")["reference share"].sem()
    rows = mean.drop(index="control").sort_values(first, ascending=False).index.tolist()
    shares = mean.reindex(index=["control"] + rows, columns=order).fillna(0.0).rename(
        index=lambda g: target_of.get(g, g))
    spread = sem.reindex(index=["control"] + rows, columns=order).fillna(0.0).rename(
        index=lambda g: target_of.get(g, g))
    plt = figures._plt()
    out = {}
    fig, ax = plt.subplots(figsize=(figures.SINGLE, max(figures.HEIGHT, (len(rows) + 2) * 3.6 * figures.MM)))
    figures.composition_bars(ax, shares, order, colours, spread=spread)
    ax.set_xlabel("Share of cells (mean over units)")
    ax.legend(handles=[Patch(color=colours[st], label=st) for st in order], loc="lower left",
              bbox_to_anchor=(0.0, 1.0), ncol=min(len(order), 4), handletextpad=0.3, columnspacing=0.8,
              borderaxespad=0.2)
    out["composition"] = os.path.join(out_dir, "figure_composition.png")
    issues = [f"figure_composition: {i}" for i in figures._save(plt, fig, out["composition"], "Each group's states")]
    change = chosen[chosen["state"] == first].set_index("group")["mean_difference"].abs().sort_values(ascending=False)
    picked = {"control": ref}
    for g in change.index[:n_maps]:
        picked[target_of.get(g, g)] = groups[g][1]
    ncol = 3
    nrow = int(np.ceil(len(picked) / ncol))
    fig, axes = plt.subplots(nrow, ncol, figsize=(figures.SINGLE, nrow * 28 * figures.MM), squeeze=False)
    xy = cells[["umap1", "umap2"]].to_numpy()
    figures.density_maps(list(axes.ravel()), xy, picked)
    for ax in axes.ravel()[len(picked):]:
        ax.axis("off")
    out["density"] = os.path.join(out_dir, "figure_density.png")
    issues += [f"figure_density: {i}" for i in figures._save(plt, fig, out["density"], "Where the cells lie")]
    return out, issues


def composition_by_groups(screen, out_dir, resolution, reference, seed, group_by, per_target, population_units,
                          exclude_cells, state_markers, vectors, best_state, reference_labels, maps=False):
    """A paper's composition analysis: the cells of the given units and not left out, clustered (named by markers
    where given); per group (each target, or each guide vector) and unit, its share of cells in each state minus the
    reference cells' share in that unit; per group and state, the mean over units. With per_target best or other,
    each target keeps the vector with the larger (or the smaller) drop in best_state."""
    from .. import states
    keep = ~cells_left_out(screen, exclude_cells)
    if population_units:
        keep &= np.isin(screen.unit, list(population_units))
    idx = np.flatnonzero(keep)
    a, h = states.embed(screen.X[idx], screen.genes, seed=seed)
    clusters = states.leiden_states(h, resolution, seed)
    named = {}
    lab = clusters
    if state_markers:
        lab, named = name_states(a, clusters, state_markers)
    labels, units = screen.label[idx], screen.unit[idx]
    top = np.asarray(screen.top_guide)[idx]
    ref = np.isin(labels, reference_labels) if reference_labels else screen.reference(reference)[idx]
    outside = set(reference_labels or []) | {"none", "ambiguous", "control"}
    groups = {}
    if group_by == "vector":
        for vector, guides in (vectors or {}).items():
            m = np.isin(top, list(guides)) & ~ref & ~np.isin(labels, list(outside))
            if m.sum():
                groups[vector] = (pd.Series(labels[m]).mode().iloc[0], m)
    else:
        for t in sorted(set(labels) - outside):
            groups[t] = (t, labels == t)
    names = sorted(set(lab))

    def ref_of(target):  # a group's own arm's reference cells
        if reference_labels or not screen.reference_matches:
            return ref
        try:
            return screen.own_reference(target, reference)[idx]
        except ValueError:
            return ref
    rows = []
    for g, (target, m) in groups.items():
        own = ref_of(target)
        for u in sorted(set(units)):
            mt, mr = m & (units == u), own & (units == u)
            if screen.crossed:  # the reference drawn within the unit's conditions, in the group's mix
                mt, mr, _ = same_sample_controls(screen.strata[idx], mt, mr & ~mt, MIN_CELLS, seed_of(g))
            if mt.sum() < MIN_CELLS or mr.sum() < MIN_CELLS:
                continue
            for st in names:
                rows.append({"group": g, "target": target, "unit": u, "state": st, "cells": int(mt.sum()),
                             "share": float((lab[mt] == st).mean()), "reference share": float((lab[mr] == st).mean()),
                             "difference": float((lab[mt] == st).mean() - (lab[mr] == st).mean())})
    by_unit = pd.DataFrame(rows, columns=["group", "target", "unit", "state", "cells", "share", "reference share",
                                          "difference"])
    per = by_unit.groupby(["group", "target", "state"], sort=False).agg(
        units=("unit", "nunique"), mean_difference=("difference", "mean")).reset_index()
    chosen = per
    if group_by == "vector" and per_target in ("best", "other") and best_state and len(per):
        drop = per[per["state"] == best_state].set_index("group")["mean_difference"]
        pick = {}
        for target, g in per[per["state"] == best_state].groupby("target"):
            order = g.sort_values("mean_difference")["group"].tolist()  # the larger drop first
            pick[target] = order[0] if per_target == "best" or len(order) == 1 else order[-1]
        chosen = per[per["group"].isin(set(pick.values()))]
        chosen = chosen.assign(shown=True)
        per = per.assign(shown=per["group"].isin(set(pick.values())))
        del drop
    path = os.path.join(out_dir, "figure.png")
    if chosen.empty:
        checks = figures.bars(np.zeros(1), ["none"], path, "No group with enough cells", "cells")
        return Result("not_enough_data", chosen, {"states": {st: int((lab == st).sum()) for st in names}}, path,
                      checks, "no group with 10 cells beside 10 reference cells in a unit",
                      tables={"by_unit": by_unit, "groups": per})
    shown_unit = by_unit[by_unit["group"].isin(set(chosen["group"]))].rename(columns={"state": "score"})
    shown_unit = shown_unit.assign(target=shown_unit["group"])  # a vector's own row where vectors are the groups
    checks = _heatmap_figure(shown_unit[["target", "score", "unit", "difference"]],
                             chosen.assign(target=chosen["group"]).rename(columns={"state": "score"})[
                                 ["target", "score", "mean_difference"]],
                             path, f"Share of each group's cells in each state, minus the reference's (resolution "
                             f"{resolution})", "Share in state, group − reference")
    cells = pd.DataFrame({"barcode": barcodes(screen)[idx], "cluster": clusters, "state": lab,
                          "label": labels, "unit": units, "top_guide": top})
    drawn = {}
    if maps:  # the embedding kept, and the charts published screens use for states and composition
        import scanpy as sc
        sc.tl.umap(h, random_state=seed)
        cells["umap1"], cells["umap2"] = h.obsm["X_umap"][:, 0], h.obsm["X_umap"][:, 1]
        drawn, issues = composition_figures(cells, by_unit, chosen, ref, groups, names, out_dir, first=best_state)
        checks = {**checks, "layout_issues": list(checks.get("layout_issues") or []) + issues}
    return Result("detected", chosen.reset_index(drop=True),
                  {"states": {st: int((lab == st).sum()) for st in names}, "clusters named": named,
                   "cells": int(len(idx)), "resolution": resolution, "group_by": group_by, "per_target": per_target,
                   "reference cells": int(ref.sum()), "figures": drawn},
                  path, checks, f"{len(idx)} cells in {len(names)} states; {chosen['group'].nunique()} groups",
                  tables={"by_unit": by_unit, "groups": per, "cell_states": cells})


# ---- a paper's choices: Seurat 3's logistic-regression test -------------------------------------------------------
THRESHOLDS = {"min_pct": 0.1, "logfc_threshold": 0.25, "p_adj": 0.05, "abs_logfc": 0.5}  # FindMarkers' defaults


def barcodes(screen):
    return np.asarray(screen.adata.obs_names).astype(str)


def cells_left_out(screen, exclude):
    """A mask of the cells to leave out: from a mask, a list of barcodes, a table with a barcode column, or the path of
    such a table (a filter step's set-aside cells)."""
    if exclude is None:
        return np.zeros(len(screen.label), bool)
    if isinstance(exclude, str):
        exclude = pd.read_csv(exclude, sep="\t")
    if isinstance(exclude, pd.DataFrame):
        exclude = exclude["barcode"].astype(str).to_numpy()
    exclude = np.asarray(exclude)
    if exclude.dtype == bool and len(exclude) == len(screen.label):
        return exclude
    return np.isin(barcodes(screen), exclude.astype(str))


def quality_mask(screen, min_genes=None, max_mito=None, min_ribo=None):
    """The cells meeting a paper's quality thresholds: at least min_genes genes detected, at most max_mito of their
    counts from mitochondrial genes (names starting mt- or MT-), at least min_ribo from ribosomal protein genes (Rps,
    Rpl)."""
    from ..quality import mito_ribo
    keep = np.ones(len(screen.label), bool)
    lib = np.maximum(screen.libsize, 1)
    mito, ribo = (np.flatnonzero(m) for m in mito_ribo(screen.genes))
    if min_genes is not None:
        keep &= np.asarray((screen.X > 0).sum(1)).ravel() >= min_genes
    if max_mito is not None:
        keep &= np.asarray(screen.X[:, mito].sum(1)).ravel() / lib <= max_mito
    if min_ribo is not None:
        keep &= np.asarray(screen.X[:, ribo].sum(1)).ravel() / lib >= min_ribo
    return keep


def covariate_by_unit(screen, by_unit):
    """A covariate's value per cell, from its value per unit; None without one. The cells whose unit could not be told
    (left out of every test) take none."""
    if not by_unit:
        return None
    missing = sorted(set(screen.units()) - set(by_unit))
    if missing:
        raise ValueError(f"the covariate has no value for units {', '.join(missing)}")
    return np.array([float(by_unit[u]) if u in by_unit else np.nan for u in screen.unit])


def seurat_logfc(Lg, Lr):
    """Seurat 3's avg_logFC: log of the mean of expm1 plus 1, group minus reference (natural log)."""
    def mean_fxn(L):
        E = L.copy()
        E.data = np.expm1(E.data)
        return np.log(np.asarray(E.mean(0)).ravel() + 1)
    return mean_fxn(Lg) - mean_fxn(Lr)


def logistic_lr(y, C, Z, iterations=30):
    """Per column of Z, the likelihood-ratio test of a logistic regression of y on C and that column against y on C
    alone (C holds the intercept and covariates). Newton steps for all columns at once. Returns the p-values."""
    n, k = C.shape
    G = Z.shape[1]

    def loglik(p):
        p = np.clip(p, 1e-12, 1 - 1e-12)
        return (y[:, None] * np.log(p) + (1 - y[:, None]) * np.log(1 - p)).sum(0)

    b0 = np.zeros(k)  # the null model, shared by every column
    for _ in range(iterations):
        p = 1 / (1 + np.exp(-(C @ b0)))
        w = np.maximum(p * (1 - p), 1e-10)
        step = np.linalg.solve(C.T @ (C * w[:, None]) + 1e-9 * np.eye(k), C.T @ (y - p))
        b0 += step
        if np.max(np.abs(step)) < 1e-8:
            break
    ll0 = float(loglik((1 / (1 + np.exp(-(C @ b0))))[:, None])[0])
    beta = np.zeros((G, k + 1))
    beta[:, :k] = b0
    for _ in range(iterations):
        eta = C @ beta[:, :k].T + Z * beta[:, k]
        p = 1 / (1 + np.exp(-eta))
        w = np.maximum(p * (1 - p), 1e-10)
        r = y[:, None] - p
        H = np.empty((G, k + 1, k + 1))
        U = np.empty((G, k + 1))
        for a in range(k + 1):
            xa = C[:, a][:, None] if a < k else Z
            U[:, a] = (xa * r).sum(0)
            for b in range(a, k + 1):
                xb = C[:, b][:, None] if b < k else Z
                H[:, a, b] = H[:, b, a] = (w * xa * xb).sum(0)
        H += 1e-9 * np.eye(k + 1)
        step = np.linalg.solve(H, U[:, :, None])[:, :, 0]
        beta += np.clip(step, -10, 10)
        if np.max(np.abs(step)) < 1e-8:
            break
    eta = C @ beta[:, :k].T + Z * beta[:, k]
    ll1 = loglik(1 / (1 + np.exp(-eta)))
    return stats.chi2.sf(np.maximum(2 * (ll1 - ll0), 0), 1)


def seurat3_lr_de(screen, group, ref, covariate=None, min_pct=0.1, logfc_threshold=0.25):
    """FindMarkers(test.use = "LR", latent.vars = covariate) of the group's cells against the reference's: a table
    of every gene with pct.1, pct.2, avg_logFC, p_val and p_val_adj (Bonferroni over every gene of the matrix)."""
    L = screen.lognorm
    gi, ri = np.flatnonzero(group), np.flatnonzero(ref)
    Lg, Lr = L[gi], L[ri]
    pct1 = np.round(np.asarray((Lg > 0).mean(0)).ravel(), 3)
    pct2 = np.round(np.asarray((Lr > 0).mean(0)).ravel(), 3)
    lfc = seurat_logfc(Lg, Lr)
    tested = (np.maximum(pct1, pct2) > min_pct) & (np.abs(lfc) > logfc_threshold)
    p = np.full(len(screen.genes), np.nan)
    if tested.any():
        cells = np.r_[gi, ri]
        y = np.r_[np.ones(len(gi)), np.zeros(len(ri))]
        cols = [np.ones(len(cells))]
        if covariate is not None:  # one value per cell, or one column per level of a sample covariate
            cov = np.asarray(covariate, float)[cells]
            cov = cov[:, None] if cov.ndim == 1 else cov
            cols += [cov[:, j] for j in range(cov.shape[1]) if np.unique(cov[:, j]).size > 1]
        C = np.column_stack(cols)
        idx = np.flatnonzero(tested)
        for start in range(0, len(idx), 2000):  # in blocks, to bound memory
            block = idx[start:start + 2000]
            Z = L[cells][:, block].toarray().astype(np.float64)
            p[block] = logistic_lr(y, C, Z)
    return pd.DataFrame({"gene": screen.genes, "pct.1": pct1, "pct.2": pct2, "avg_logFC": lfc, "tested": tested,
                         "p_val": p, "p_val_adj": np.minimum(1.0, p * len(screen.genes))})


def sample_columns(unit, held):
    """The sample as a latent variable: one 0/1 column per sample of `held` but the first (cells x columns)."""
    unit = np.asarray(unit).astype(str)
    return np.column_stack([(unit == u).astype(float) for u in held[1:]]) if len(held) > 1 else None


def with_conditions(screen, columns, cells):
    """A model's latent columns with the conditions beside them where a sample holds several (Screen.crossed): one
    0/1 column per condition of these cells but the first; the columns as given otherwise."""
    if not screen.crossed:
        return columns
    k = sample_columns(screen.condition, sorted(set(screen.condition[cells])))
    return k if columns is None else columns if k is None else np.column_stack([columns, k])


def genes_seurat3_lr(screen, out_dir, groups=None, reference_labels=None, reference="control_guides",
                     by_unit=None, thresholds=None, exclude_cells=None):
    """C.genes with Seurat 3's test, as a paper ran it: each named group of labels (its cells not left out) against
    the reference cells, with the covariate; a gene is DE at p_val_adj < p_adj and |avg_logFC| > abs_logfc
    (avg_logFC is Seurat 3's, in natural log). A paper's run names its reference (reference_labels) or its covariate
    (covariate_by_unit). Where neither is named and the cells span several samples, the design's default holds, as
    for C.genes' own test: each group against the reference cells of the samples it is in, with the sample as a
    latent variable (session 4: pairs in some captures and singles in others were otherwise read against every
    capture's control cells)."""
    th = {**THRESHOLDS, **(thresholds or {})}
    left_out = cells_left_out(screen, exclude_cells)
    ref = np.isin(screen.label, reference_labels) if reference_labels else screen.reference(reference)
    design_default = not reference_labels and not by_unit and (len(screen.units()) > 1 or screen.crossed)
    cov = None if design_default else covariate_by_unit(screen, by_unit)
    groups = groups or {t: [t] for t in screen.targets(MIN_CELLS)}
    tables, summary, untested = [], [], {}
    for name, labels in groups.items():
        cells = np.isin(screen.label, labels) & ~left_out
        r, c = ref, cov
        if not reference_labels and reference in ("control_guides", "other_targets") and screen.reference_matches:
            # its own arm's control cells; a group spanning arms (a pair of a CRISPRi and a CRISPRa guide) every
            # control cell (the user's decision on BR34)
            members = [m for lab in labels for m in screen.members(lab)]
            arms_ = {screen.arm_of(m) for m in members if (screen.label == m).any() or m in labels}
            r = screen.reference(reference, target=members[0]) if len(arms_) == 1 else screen.reference(reference)
        r = r & ~cells  # other targets as the reference: never the group's own cells
        if design_default:
            held = sorted(set(screen.unit[cells]))
            r = r & np.isin(screen.unit, held)  # the group's own reference, in the samples it is in (BR05)
            if screen.crossed:  # and in the conditions it is in, each a latent variable too
                r = r & np.isin(screen.condition, sorted(set(screen.condition[cells])))
            c = with_conditions(screen, sample_columns(screen.unit, held), cells)
        if r.sum() < MIN_CELLS:  # nothing to compare it with (its arm holds no other target): said, not tested
            untested[name] = f"{int(r.sum())} reference cells"
            continue
        t = seurat3_lr_de(screen, cells, r, c, th["min_pct"], th["logfc_threshold"])
        t["DE"] = (t["p_val_adj"] < th["p_adj"]) & (t["avg_logFC"].abs() > th["abs_logfc"])
        tables.append(t.assign(group=name))
        summary.append({"group": name, "cells": int(cells.sum()), "reference cells": int(r.sum()),
                        "DE genes": int(t["DE"].sum()),
                        "up": int((t["DE"] & (t["avg_logFC"] > 0)).sum()),
                        "down": int((t["DE"] & (t["avg_logFC"] < 0)).sum())})
    summary = pd.DataFrame(summary)
    path = os.path.join(out_dir, "figure.png")
    checks = figures.bars(summary["DE genes"].to_numpy() if len(summary) else np.zeros(1),
                          summary["group"].tolist() or ["none"], path, "DE genes per group (Seurat 3 LR test)",
                          "DE genes vs reference")
    n = int(summary["DE genes"].sum()) if len(summary) else 0
    if design_default:
        used = ({"reference": "the reference cells of the samples and conditions each group is in (the design's "
                              "default)", "covariate": "the sample and the condition, as latent variables"}
                if screen.crossed else
                {"reference": "the reference cells of the samples each group is in (the design's default)",
                 "covariate": "the sample, as a latent variable"})
    else:
        used = {"reference": f"the labels {list(reference_labels)}" if reference_labels else
                f"every reference cell ({reference}), whatever its sample",
                "covariate": "the given value per unit, as a latent variable" if by_unit else "none"}
    return Result("detected" if n else "not_detected", summary,
                  {"method": "seurat3_lr", "reference_cells": int(ref.sum()), "thresholds": th,
                   **({"not tested": untested} if untested else {}),
                   "cells left out": int(left_out.sum()), **used,
                   "replicates": "cells" + (", with the covariate as a latent variable" if used["covariate"] != "none"
                                            else ", with no covariate"),
                   "log_base": "e (Seurat 3's avg_logFC is the natural log)"},
                  path, checks, f"{n} DE genes over {len(summary)} groups (Seurat 3 LR test; the reference: "
                                f"{used['reference']}; the covariate: {used['covariate']}; avg_logFC in natural log)",
                  tables={"de": pd.concat(tables) if tables else pd.DataFrame()})


# ---- programs ----------------------------------------------------------------------------------------------------
def set_scores(screen, sets, min_genes=5, detection=0.01):
    """Per cell, each set's mean log-normalised expression over its genes detected in `detection` of the cells:
    {set: scores}, and the genes used."""
    det = screen.detected(np.ones(len(screen.label), bool), detection)
    L = screen.lognorm
    scores, used = {}, {}
    for name, genes in sets.items():
        idx = [screen.gene_index[g] for g in genes if g in screen.gene_index and det[screen.gene_index[g]]]
        if len(idx) >= min_genes:
            scores[name] = np.asarray(L[:, idx].mean(1)).ravel()
            used[name] = [screen.genes[i] for i in idx]
    return scores, used


def nmf_programs(screen, k=10, replicates=10, seed=0):
    """The screen's own programs (consensus NMF, pertura.programs): {program: usage per cell}, and each one's
    20 top genes by its spectrum."""
    from .. import programs
    orig = np.arange(len(screen.label))
    fit_cells, hvg, M_fit, M_all = programs.prepare(screen.X, orig, seed=seed, genes=screen.genes)
    cons, usage, sil, kept, err, _ = programs.factorise(M_fit, M_all, fit_cells, orig, k, replicates, seed)
    names = [f"P{j + 1:02d}" for j in range(k)]
    top = {n: [screen.genes[hvg][i] for i in np.argsort(-cons[j])[:20]] for j, n in enumerate(names)}
    return {n: usage[:, j] for j, n in enumerate(names)}, top, sil


CNMF_COMPONENTS = (5, 7, 9, 11, 13, 15)  # the K range tried; the K is the most stable of these unless one is given


def cnmf_programs(screen, workdir, components=CNMF_COMPONENTS, n_iter=100, k=None, seed=0, density=0.5):
    """The screen's own programs by cNMF (Kotliar et al. 2019, the package Schnitzler et al. 2024 and Ota et al.
    2025 use), on every cell's raw counts: the programs step's cnmf method (pertura.steps.programs). Returns
    {program: usage per cell}, each program's 20 top genes by cNMF's gene spectra score, and the K-selection
    statistics with the K chosen (the one given, else the most stable in the range)."""
    from ..steps import programs as step
    usage, genes, about, _ = step.discover(screen, "cnmf", workdir, k=k, seed=seed, components=components,
                                           n_iter=n_iter, density=density)
    tops = {g: list(s.sort_values("gene_rank")["gene"].head(20)) for g, s in genes.groupby("program")}
    return ({c: usage[c].to_numpy() for c in usage.columns}, tops,
            {"k": about["k"], "k_given": about["k_given"], "k_stats": about["k_stats"], "density_threshold": density,
             "n_iter": n_iter})


def scanpy_scores(screen, sets, min_genes=5, detection=0.01, ctrl_size=50, n_bins=25, random_state=0,
                  by_columns=True):
    """Per cell, each set's score by scanpy's tl.score_genes (the Python form of Seurat's module score: the mean
    log-normalised expression of the set's genes detected in `detection` of the cells, minus that of control genes
    drawn from the same expression bins), with scanpy's defaults: {set: scores}, the genes used, and the call.
    by_columns: the matrix scored stored by columns (a module's sets); False keeps it stored by rows, where one set
    is scored (a finding's checks) and the copy by columns costs more than it saves."""
    import anndata as ad
    import scanpy as sc
    from . import packages
    L = screen.lognorm
    call = packages.call("scanpy", "tl.score_genes", data=L, layer="lognorm", ctrl_size=ctrl_size, n_bins=n_bins,
                         random_state=random_state, genes=f"detected in {detection:.0%} of cells, {min_genes} or more")
    det = screen.detected(np.ones(len(screen.label), bool), detection)
    # stored by columns: score_genes takes the columns of each set's genes and of its control genes on every call,
    # and a matrix stored by rows is read whole for each (on a 250k-cell screen: 50 sets in 472 s by rows, 123 s by
    # columns; one set in 35 s by rows, 134 s by columns, most of it the copy)
    a = ad.AnnData(sp.csc_matrix(L) if by_columns else sp.csr_matrix(L),
                   var=pd.DataFrame(index=pd.Index(screen.genes).astype(str)))
    a.var_names_make_unique()
    scores, used = {}, {}
    for name, genes in sets.items():
        keep = [g for g in genes if g in screen.gene_index and det[screen.gene_index[g]]]
        if len(keep) >= min_genes:
            sc.tl.score_genes(a, keep, ctrl_size=ctrl_size, n_bins=n_bins, random_state=random_state,
                              score_name="_score", use_raw=False)
            scores[name] = a.obs["_score"].to_numpy().copy()
            used[name] = keep
    return scores, used, call


def seurat_scores(screen, sets, min_genes=5, detection=0.01, nbin=24, ctrl=100, seed=1):
    """Per cell, each set's score by Seurat's AddModuleScore (pertura/r/module_score_seurat.R; NormalizeData, then
    nbin 24, ctrl 100, seed 1, its defaults), on the genes scanpy_scores uses (detected in `detection` of the cells,
    min_genes or more): {set: scores}, the genes used, and the call."""
    import tempfile
    from .. import envs
    from . import packages
    packages.check_layer(screen.X, "raw_counts", "Seurat AddModuleScore (NormalizeData of raw counts)")
    det = screen.detected(np.ones(len(screen.label), bool), detection)
    used = {}
    for name, genes in sets.items():
        keep = [g for g in genes if g in screen.gene_index and det[screen.gene_index[g]]]
        if len(keep) >= min_genes:
            used[name] = keep
    call = packages.call("Seurat", "NormalizeData, AddModuleScore", data=screen.X, layer="raw_counts", nbin=nbin,
                         ctrl=ctrl, seed=seed, genes=f"detected in {detection:.0%} of cells, {min_genes} or more")
    if not used:
        return {}, used, call
    # Seurat turns underscores in feature names into dashes: the names it is given are already its own
    names = pd.Index(screen.genes).astype(str).str.replace("_", "-")
    unique = pd.Series(names).where(~names.duplicated(), names + "--" + pd.Series(range(len(names))).astype(str))
    own = dict(zip(pd.Index(screen.genes).astype(str), unique))
    with tempfile.TemporaryDirectory() as base:
        envs.counts_for_r(os.path.join(base, "counts.mtx.gz"), screen.X)
        with open(os.path.join(base, "genes.tsv"), "w", encoding="utf-8") as fh:
            fh.write("".join(f"{g}\n" for g in unique))
        with open(os.path.join(base, "cells.txt"), "w", encoding="utf-8") as fh:
            fh.write("".join(f"c{i}\n" for i in range(len(screen.label))))
        pd.DataFrame([(k, own[g]) for k, gs in used.items() for g in gs], columns=["set", "gene"]).to_csv(
            os.path.join(base, "sets.tsv"), sep="\t", index=False)
        with R_SLOTS:
            p = subprocess.run([envs.rscript(), os.path.join(R_DIR_MODULES, "module_score_seurat.R"), str(nbin),
                                str(ctrl), str(seed)], env=envs.r_env({"PERTURA_SCORES_DIR": base,
                                                                       "OMP_NUM_THREADS": "1"}),
                               capture_output=True, text=True)
        if p.returncode != 0:
            raise RuntimeError(f"Seurat AddModuleScore failed: {p.stderr[-1500:]}")
        r = pd.read_csv(os.path.join(base, "scores.tsv.gz"), sep="\t").set_index("cell")
    r = r.reindex([f"c{i}" for i in range(len(screen.label))])
    return {k: r[k].to_numpy(dtype=float) for k in used if k in r.columns}, used, call


def score_test(unit, score, mt, mr, method, condition=None, strata=None):
    """One target's score against its reference cells: the linear model with the sample (statsmodels' OLS,
    score ~ target + sample) or the Wilcoxon rank-sum test (scipy). condition: each cell's condition, where a sample
    holds several (Screen.crossed); the linear model takes it as a covariate too, and the Wilcoxon test the control
    cells drawn within each sample and condition (same_sample_controls; strata: Screen.strata, built once by the
    screen, else here). Returns (effect, p, test)."""
    if condition is not None and method != "linear_model":
        if strata is None:
            strata = np.char.add(np.char.add(np.asarray(unit).astype(str), "|"), np.asarray(condition).astype(str))
        mt, mr, _ = same_sample_controls(strata, mt, mr, MIN_CELLS, 0)
        if not mt.any() or not mr.any():
            return np.nan, np.nan, "Wilcoxon rank-sum: no sample and condition with enough control cells"
    a, b = score[mt], score[mr]
    if method == "linear_model":
        import statsmodels.api as sm
        unit = np.asarray(unit)
        samples = sorted(set(unit[mt | mr]))
        y = np.r_[np.ones(mt.sum()), np.zeros(mr.sum())]
        u = np.r_[unit[mt], unit[mr]]
        cols = [np.ones(len(y)), y] + [(u == s).astype(float) for s in samples[1:]]
        if condition is not None:  # the conditions inside the samples, as a term of their own
            condition = np.asarray(condition).astype(str)
            v = np.r_[condition[mt], condition[mr]]
            cols += [(v == x).astype(float) for x in sorted(set(v))[1:]]
        fit = sm.OLS(np.r_[a, b], np.column_stack(cols)).fit()
        terms = ["sample"] * (len(samples) > 1) + ["condition"] * (condition is not None)
        return float(fit.params[1]), float(fit.pvalues[1]), (
            f"OLS, {' and '.join(terms)} as covariates" if len(terms) > 1 else
            f"OLS, {terms[0]} as a covariate" if terms else "OLS")
    r = stats.ranksums(a, b)
    return float(a.mean() - b.mean()), float(r.pvalue), (
        "Wilcoxon rank-sum, against control cells drawn within its samples and conditions" if condition is not None
        else "Wilcoxon rank-sum")


def compare_scores_field(screen, scores, reference, min_cells=MIN_CELLS, test="auto"):
    """Per target and score, the target's cells against the control cells, by test: statsmodels' linear model
    score ~ target + sample (linear_model) or scipy's Wilcoxon rank-sum test (wilcoxon); auto, the design's default
    (design_fit.score_lead): the linear model where two or more samples or lanes hold both sides, else Wilcoxon.
    Benjamini-Hochberg over every target and score (statsmodels' multipletests); the n of each. Returns (the per-sample
    differences, the tests, the calls)."""
    from statsmodels.stats.multitest import multipletests
    from ..design_fit import score_lead
    from . import packages
    rows, per = [], []
    for t in screen.targets(min_cells):
        mt, mr = screen.cells_of(t), screen.reference(reference, target=t) & (screen.label != t)
        if mt.sum() < min_cells or mr.sum() < min_cells:
            continue
        cells = mt | mr
        samples = sorted(set(screen.unit[cells]))
        method = score_lead(screen, t, reference)["method"] if test == "auto" else test
        for name, s in scores.items():
            a, b = s[mt], s[mr]
            for u in samples:
                au, bu = s[mt & (screen.unit == u)], s[mr & (screen.unit == u)]
                if len(au) and len(bu):
                    rows.append({"target": t, "score": name, "unit": u, "difference": float(au.mean() - bu.mean()),
                                 "cells": int(len(au))})
            effect, pval, how = score_test(screen.unit, s, mt, mr, method,
                                           screen.condition if screen.crossed else None, screen.strata)
            per.append({"target": t, "score": name, "effect": effect, "mean_difference": float(a.mean() - b.mean()),
                        "p": pval, "test": how, "cells": int(mt.sum()), "reference_cells": int(mr.sum()),
                        "samples": len(samples), "target_samples": len(set(screen.unit[mt]))})
    per = pd.DataFrame(per, columns=["target", "score", "effect", "mean_difference", "p", "test", "cells",
                                     "reference_cells", "samples", "target_samples"])
    per["q"] = multipletests(per["p"].to_numpy(), method="fdr_bh")[1] if len(per) else []
    crossed = screen.crossed  # a sample holds several conditions: the model takes them as a term
    calls = [packages.call("statsmodels", "OLS", formula="score ~ target + sample + condition" if crossed else
                           "score ~ target + sample", contrast="the target's cells against the control cells",
                           covariates=["sample", "condition"] if crossed else ["sample"]),
             packages.call("scipy", "stats.ranksums", contrast="where the cells are in one sample" + (
                 ", against control cells drawn within its samples and conditions" if crossed else "")),
             packages.call("statsmodels", "stats.multitest.multipletests", method="fdr_bh")]
    return pd.DataFrame(rows, columns=["target", "score", "unit", "difference", "cells"]), per, calls


def composition_same_samples(unit, lab, tm, rm, state, min_cells=MIN_CELLS, seed=0, strata=None):
    """One target's share of cells in a state against the control cells of its own samples, in its mix
    (data.same_sample_controls): Fisher's exact test on the cells (scipy), the field's test, with the matching of
    control cells the field uses for its comparisons. The target's cells in a sample with fewer than min_cells
    control cells are left out. strata (Screen.strata): the groups the control cells are drawn within, where a
    sample holds several conditions; the samples are still counted by unit."""
    unit = np.asarray(unit).astype(str)
    kept, matched, left = same_sample_controls(unit if strata is None else strata, tm, rm, min_cells, seed)
    out = {"cells": int(kept.sum()), "reference_cells": int(matched.sum()),
           "target_samples": len(set(unit[kept])), "cells_left_out": int(left)}
    if not kept.sum() or not matched.sum():
        return {**out, "share": np.nan, "control_share": np.nan, "mean_difference": np.nan, "odds_ratio": np.nan,
                "p": np.nan, "test": "no sample with enough control cells"}
    a1, b1 = int((lab[kept] == state).sum()), int((lab[matched] == state).sum())
    odds, p = stats.fisher_exact([[a1, kept.sum() - a1], [b1, matched.sum() - b1]])
    share, control_share = a1 / kept.sum(), b1 / matched.sum()
    return {**out, "share": float(share), "control_share": float(control_share),
            "mean_difference": float(share - control_share), "odds_ratio": float(odds), "p": float(p),
            "test": "Fisher's exact test on cells, against same-sample control cells"}


def composition_rows(screen, targets, reference, lab, control_cells="same_samples", seed=0):
    """Each target's share of cells in each state of a labelling (lab: each cell's state; '' or 'nan' for a cell
    without one) against its reference: per target and state, Fisher's exact test on the cells against the control
    cells of its own samples, in its mix (same_samples), or against every reference cell (all); Benjamini-Hochberg
    over every target and state. A target needs 10 cells and 10 reference cells, a row 10 kept target cells. Per
    target, state and sample, the share's difference from the reference's (samples with cells on both sides).
    Returns (per, by_unit). The composition step's fisher backend (pertura.steps.composition)."""
    from ..steps import composition
    per, by_unit, _ = composition.compare(screen, targets, reference, lab, "fisher", control_cells=control_cells,
                                          seed=seed)
    return per, by_unit


def _target_by_column_heatmap(per, column, value, path, title, label, top=40):
    """A clustered heatmap of targets by `column` (programs, states), the value on a diverging scale that stops at
    the 98th percentile (a few extreme cells would wash out the rest), with significance marks from q
    (charts.heatmap)."""
    from . import charts
    keep = per.groupby("target")[value].apply(lambda v: v.abs().max()).sort_values(ascending=False).head(top).index
    sub = per[per["target"].isin(keep)]
    cols = sub.groupby(column)[value].apply(lambda v: v.abs().max()).sort_values(ascending=False).head(top).index
    sub = sub[sub[column].isin(cols)]  # a collection's sets: the ones that move most, as many as the targets shown
    M = sub.pivot(index="target", columns=column, values=value)
    Q = sub.pivot(index="target", columns=column, values="q").reindex_like(M)
    marks = np.vectorize(lambda v: charts.stars(v) if np.isfinite(v) else "")(Q.to_numpy(float))
    return charts.checks(charts.heatmap(M.to_numpy(float), list(M.index), list(M.columns), path, title, label,
                                        marks=marks, cap=98), zero=True)


def _genes_figures(table, summary, changed, path, level, per_target=3, top_genes=40, control_counts=None):
    """C.genes' charts: a clustered heatmap of targets by their top changed genes (log2 fold change, with
    significance marks) as the figure, a volcano of the target with the most changed genes, and the targets ranked
    by their changed genes."""
    from . import charts
    out = os.path.dirname(path)
    issues = []
    if len(summary):  # with the control-guide groups' 95th percentile as a line (Schnitzler et al. 2024)
        line = None
        if control_counts:
            line = (float(np.percentile(control_counts, 95)),
                    f"control-guide groups, 95th percentile ({len(control_counts)} groups)")
        issues += [f"figure_ranked: {i}" for i in charts.ranked_targets(
            summary.rename(columns={"genes_changed": "n"}), "n", os.path.join(out, "figure_ranked.png"),
            "Genes changed vs control cells", f"Genes changed per target (adjusted p < {level})",
            control_line=line)]
    if changed.empty:
        return charts.checks(charts.ranked_targets(summary.assign(n=0) if len(summary) else
                                                   pd.DataFrame({"target": ["none"], "n": [0]}), "n", path,
                                                   "Genes changed vs control cells", "No target changes genes"))
    lead = changed.groupby("target")["gene"].nunique().idxmax()
    issues += [f"figure_volcano: {i}" for i in charts.volcano(
        table[table["target"] == lead], os.path.join(out, "figure_volcano.png"),
        f"{lead}: genes against the control cells", threshold=level)]
    # each target's most significant genes (then the largest changes), so that genes switched on from near zero,
    # with very large fold changes, do not crowd out the rest
    genes = list(dict.fromkeys(changed.assign(a=-changed["effect"].abs()).sort_values(["q", "a"])
                               .groupby("target").head(per_target)["gene"]))[:top_genes]
    sub = table[table["gene"].isin(genes)]
    if "unit" in sub:  # a test within each unit (rank_test): the median effect over units, marked where all pass
        sub = sub.groupby(["target", "gene"], as_index=False).agg(effect=("effect", "median"), q=("q", "max"))
    from ..steps.effects_across_targets import one_row_per_key
    one_row_per_key(sub, ("target", "gene"), "C.genes' figure")
    M = sub.pivot(index="target", columns="gene", values="effect").reindex(columns=genes)
    Q = sub.pivot(index="target", columns="gene", values="q").reindex_like(M)
    marks = np.vectorize(lambda v: charts.stars(v) if np.isfinite(v) else "")(Q.to_numpy(float))
    return charts.checks(charts.heatmap(M.fillna(0).to_numpy(float), list(M.index), list(M.columns), path,
                                        "Each target's top changed genes", "log2 FC vs control cells", marks=marks,
                                        cap=98) + issues, zero=True)


def marker_table(a, lab, state_name, markers, per_state=3):
    """The marker dot plot's table (state, gene, fraction, mean): each state's top markers, each gene measured in
    every state, as scanpy's and Seurat's dot plots draw them."""
    genes = []
    for s in sorted(set(lab)):
        for g in markers[markers["state"] == s]["gene"].astype(str).head(per_state):
            if g in a.var_names and g not in genes:
                genes.append(g)
    names, rows = list(a.var_names), []
    for g in genes:
        j = names.index(g)
        col = np.asarray(a.X[:, j].todense()).ravel() if hasattr(a.X, "todense") else np.asarray(a.X[:, j]).ravel()
        for s in sorted(set(lab)):
            m = lab == s
            rows.append({"state": state_name[s], "gene": g, "fraction": float((col[m] > 0).mean()),
                         "mean_expression": float(col[m].mean())})
    return pd.DataFrame(rows, columns=["state", "gene", "fraction", "mean_expression"])


def _states_figures(screen, xy, lab, per, state_name, markers, ref_all, path, seed, control_cells="all",
                    n_maps=3, top=12):
    """C.states' charts by default: the cells on the states' UMAP (xy) by named state (the figure), the markers' dot
    plot (log-normalised expression), the composition bars of the targets that shift most against the control cells
    their test used (each target's own control cells with same_samples, else one control row), and density
    difference maps."""
    import anndata as ad
    from . import charts
    out = os.path.dirname(path)
    issues = charts.embedding_states(xy, lab, state_name, path, "The cells by state, on a UMAP")
    a = ad.AnnData(screen.lognorm, var=pd.DataFrame(index=np.asarray(screen.genes).astype(str)))
    dots = marker_table(a, lab, state_name, markers)
    if len(dots):
        issues += [f"figure_markers: {i}" for i in charts.marker_dots(
            dots, os.path.join(out, "figure_markers.png"), "Each state's markers",
            states_order=[state_name[s] for s in sorted(set(lab))])]
    shift = per.groupby("target")["mean_difference"].apply(lambda v: v.abs().max()).sort_values(ascending=False)
    states = sorted(set(lab))
    own = control_cells == "same_samples"
    by_arm = bool(getattr(screen, "reference_matches", None))

    def ref_of(t):  # a target's own arm's reference cells (bug register, BR37)
        if not by_arm:
            return ref_all
        return ref_all & screen.in_arm(screen.arm_or_every(t))  # a combination of two arms: every arm's
    if own:  # each target's shares beside its own control cells' (per: the shares its test compared)
        shares = pd.DataFrame({t: per[per["target"] == t].set_index("state")["share"] for t in shift.index[:top]}).T
        matched = {t: per[per["target"] == t].set_index("state")["control_share"] for t in shift.index[:top]}
    elif by_arm:  # each target beside its own arm's control cells
        shares = pd.DataFrame({t: pd.Series({s: float((lab[screen.cells_of(t)] == s).mean()) for s in states})
                               for t in shift.index[:top]}).T
        matched = {t: pd.Series({s: float((lab[ref_of(t)] == s).mean()) for s in states}) for t in shift.index[:top]}
    else:
        shares = pd.DataFrame([{s: float((lab[ref_all] == s).mean()) for s in states}], index=["control"])
        for t in shift.index[:top]:
            m = screen.cells_of(t)
            shares.loc[t] = [float((lab[m] == s).mean()) for s in states]
        matched = None
    own_words = " of its own samples and conditions" if screen.crossed else " of its own samples"
    issues += [f"figure_composition: {i}" for i in charts.composition(
        shares.reindex(columns=states).fillna(0.0), states, os.path.join(out, "figure_composition.png"),
        "Each target's cells by state, against the control cells" + (own_words if own else
                                                                      " of its own arm" if by_arm else ""),
        names=state_name, matched=matched)]
    for k, t in enumerate(shift.index[:n_maps]):
        mt, mr = screen.cells_of(t), ref_of(t) & (screen.label != t)
        if own:
            mt, mr, _ = screen.matched(mt, mr, MIN_CELLS, seed)
        issues += [f"figure_density_{k + 1}: {i}" for i in charts.density_difference(
            xy, mt, mr, os.path.join(out, f"figure_density_{k + 1}.png"),
            f"{t}: density of cells minus the control cells'" + (own_words if own else ""),
            f"{t} − control cells")]
    return charts.checks(issues, reference=None), dots


def _heatmap_figure(df, per, path, title, label, top=20):
    M = per.pivot(index="target", columns="score", values="mean_difference")
    pairs = per.reindex(per["mean_difference"].abs().sort_values(ascending=False).index).head(top)
    keys = set(zip(pairs["target"], pairs["score"]))
    d = df[np.array([k in keys for k in zip(df["target"], df["score"])], dtype=bool)].copy()
    d["pair"] = d["target"] + " : " + d["score"].map(figures.short_name)
    return figures.heatmap_and_dots(M.to_numpy(), list(M.index), [figures.short_name(c) for c in M.columns], d,
                                    "pair", "difference", "unit", path, title, label)


from . import PROGRAM_METHODS  # noqa: E402  (the queue a run waits in reads them too: queue_of)
COLLECTIONS = tuple(NAMED)  # the MSigDB collections by their names (pertura.gene_sets)


def programs_recipe(gene_sets="hallmark", reference="control_guides", gene_lists=None, k=None, fit_on="all",
                    params=None, test="auto", select=None, cell_values=None, contrast=None, proteins=None):
    """C.programs as blocks (docs/adr/0031, D2): the programs found in the cells (or gene sets given, or an MSigDB
    collection's sets, or per-cell values as they are), each target's score on each against its reference, and the
    same test on groups of control guides (the control guides' distribution of each score)."""
    from .. import blocks as pt
    prog = None
    if gene_sets in PROGRAM_METHODS:
        prog = pt.programs(method=gene_sets, fit_on=fit_on, k=k, params=params)
        res = pt.scores(programs=prog, reference=reference, test=test)
    elif gene_sets == "given":
        res = pt.scores(sets=gene_lists, reference=reference, min_genes=3, test=test)
    elif gene_sets == "cell_values":
        res = pt.scores(cell_values=cell_values, reference=reference, test=test)
    elif gene_sets == "contrast":
        res = pt.scores(contrast=contrast, reference=reference, test=test)
    elif gene_sets == "proteins":
        res = pt.scores(proteins=proteins or "all", reference=reference, test=test)
    else:
        res = pt.scores(sets=gene_sets, select=select, reference=reference, test=test)
    try:
        cal = pt.calibrate(res)
    except ValueError as e:  # another reference, or too few control guides for groups
        cal = str(e)
    return prog, res, cal


def _group_pairs_words(cal, what):
    """A score or composition result's calibration in words: the group x program (state, phase) pairs that the same
    test calls changed in groups of control guides, as C.genes counts the groups' discoveries."""
    s = cal.extra.get("summary") if hasattr(cal, "extra") else None
    if not s or "group_pairs" not in s:
        return "; the calibration check was not run" + (f" ({cal})" if isinstance(cal, str) else "")
    rule = changed_words(cal.table) if "changed" in cal.table.columns else f"at adjusted p < {ADJUSTED}"
    return (f"; the same test on {s['groups']} groups of control guides: {s['group_pairs_changed']} of "
            f"{s['group_pairs']} group x {what} pairs changed {rule}")


COMPOSITION_WORDS = {"fisher": "Fisher's exact test on cells", "cmh": "the Cochran-Mantel-Haenszel test over the "
                     "samples", "propeller": "propeller (speckle) on the samples' proportions",
                     "sccoda": "scCODA (pertpy), credible effects", "milo": "Milo (pertpy), neighbourhoods"}


def composition_words(per, control_cells="same_samples"):
    """The composition tests a table holds, in words (each with its targets where there are several)."""
    if "method" not in per.columns or not len(per):
        return COMPOSITION_WORDS["fisher"]
    by = per.groupby("method")["target"].nunique()
    words = []
    for m, n in by.items():
        w = COMPOSITION_WORDS.get(m, m)
        if m == "fisher" and control_cells == "same_samples":
            w += ", against the control cells of each target's own samples"
        words.append(w + (f" ({n} targets)" if len(by) > 1 else ""))
    return "; ".join(words)


def changed_words(per):
    """How a composition table calls a row changed: adjusted p < 0.05, or scCODA's credible effects."""
    methods = set(per["method"]) if "method" in per.columns and len(per) else {"fisher"}
    if methods == {"sccoda"}:
        return "as credible effects (scCODA, expected FDR 0.05)"
    if "sccoda" in methods:
        return f"at adjusted p < {ADJUSTED}, or as scCODA's credible effects"
    return f"at adjusted p < {ADJUSTED}"


@register("C.programs", Contract(
    serves="C.programs",
    applies_when="always",
    needs="log-normalised expression, labels, samples, the control cells; the organism (experiment.organism: "
          "human or mouse) for an MSigDB collection (hallmark, immunologic, cell_types and the others the gene_sets "
          "tool names), its sets chosen with select where it holds more than 200; or gene lists given by the agent, "
          "written down before looking at any result; or a contrast between two levels of a cell column in the control "
          "cells (contrast); or the antibody counts (proteins); or per-cell values (cell_values); for programs found "
          "in the "
          "cells, raw counts (and samples for genenmf_meta)",
    returns="per cell, each set's score (scanpy's tl.score_genes, the Python form of Seurat's module score), each "
            "program's usage (pertura.steps.programs: cNMF, scikit-learn's NMF, GeneNMF, which no reviewed screen "
            "uses) or each value given; per target and program: "
            "the difference from the control cells, by test (auto: statsmodels' linear model with the sample as a "
            "covariate where there are samples or lanes, else the Wilcoxon rank-sum test; or either named), "
            "Benjamini-Hochberg "
            "over targets and sets, with the cells and samples of each; the same test on groups of control guides "
            "(calibration: each program's 95th percentile of their effect sizes, and the pairs past it); per "
            "target, program and sample: the difference (by_unit.tsv); the package calls",
    fails_when=[("a program shift shared by every target (a common response)",
                 "the same program against the other targets' cells (reference other_targets; D.common_response)"),
                ("a program found on the data it is tested on looks stronger than it is",
                 "the calibration on control-guide groups; fixed gene sets, or fit_on controls (the programs found "
                 "in the control cells only, which no reviewed screen does), as Pertura's options"),
                ("a program of one sample or capture found as biology", "programs that follow the batch or lane set "
                 "aside, as a published screen did; genenmf_meta: programs recurring across samples (GeneNMF, which "
                 "no reviewed screen uses)")],
    tested_against="CAR-T, with an earlier hand analysis's fixed cytotoxic list: the sign of the targets' "
                   "difference from the control cells in each mouse, against that analysis; each method: the "
                   "synthetic screen's planted program",
    choices={"gene_sets": [*COLLECTIONS, "given", "contrast", "proteins", "cell_values", *PROGRAM_METHODS,
                           "consensus_nmf"],
             "reference": ["control_guides", "other_targets"], "fit_on": ["all", "controls"],
             "test": ["auto", "linear_model", "wilcoxon"]},
    inputs={"gene_lists": "{name: [genes]} for gene_sets 'given', written down before looking at any result",
            "select": "a collection's sets to score: their names, or a word their names hold (the gene_sets tool "
                      "with match lists them)",
            "cell_values": "for gene_sets 'cell_values': a numeric cell column, or a table's ID with a barcode column "
                           "and numeric columns, each tested as it is",
            "contrast": "for gene_sets 'contrast': {column, levels: [a, b], top}, the control cells' genes up and "
                        "down between two levels of a cell column (the whole screen's), scored on every target",
            "proteins": "for gene_sets 'proteins': the proteins to test (their names; default every one the screen "
                        "object holds), the antibody counts CLR-normalised per cell as Seurat's multimodal vignette "
                        "does",
            "components": "cnmf: the K range tried (5 to 15, odd)",
            "k": "cnmf: the K to use (default: the most stable in the range); sklearn_nmf, genenmf: the number of "
                 "programs (default 10); genenmf_meta: the number of meta-programs (default 10)",
            "n_iter": "cnmf: factorisations per K (cNMF's default, 100)",
            "params": "{setting: value}: the method's package settings changed from their defaults (the programs "
                      "block's, pt.help('programs'))"},
    scope={"gene_lists": ("gene_sets", ["given"]), "select": ("gene_sets", list(COLLECTIONS)),
           "cell_values": ("gene_sets", ["cell_values"]), "contrast": ("gene_sets", ["contrast"]),
           "proteins": ("gene_sets", ["proteins"]),
           **{k: ("gene_sets", ["cnmf"]) for k in ("components", "n_iter")},
           **{k: ("gene_sets", list(PROGRAM_METHODS)) for k in ("k", "fit_on", "params")}}))
def programs(screen, design, out_dir, gene_sets="hallmark", reference="control_guides", gene_lists=None,
             components=None, k=None, n_iter=None, fit_on="all", params=None, test="auto", select=None,
             cell_values=None, contrast=None, proteins=None):
    path = os.path.join(out_dir, "figure.png")
    for needed, given, name in (("given", gene_lists, "gene_lists"), ("cell_values", cell_values, "cell_values"),
                                ("contrast", contrast, "contrast")):
        if gene_sets == needed and not given:
            checks = figures.bars(np.zeros(1), ["none"], path, f"No {name} given", "cells")
            return Result("not_enough_data", pd.DataFrame(), {}, path, checks, f"gene_sets '{needed}' needs {name}")
    if gene_sets == "proteins" and screen.adata.obsm.get("antibody_counts") is None:
        checks = figures.bars(np.zeros(1), ["none"], path, "No antibody counts", "cells")
        return Result("not_enough_data", pd.DataFrame(), {}, path, checks,
                      "the screen object holds no antibody counts: its deposit has no protein features")
    if gene_sets in COLLECTIONS and value(design, "experiment.organism") not in ("human", "mouse"):
        checks = figures.bars(np.zeros(1), ["none"], path, "No gene sets for this organism", "cells")
        return Result("not_enough_data", pd.DataFrame(), {"organism": value(design, "experiment.organism")}, path,
                      checks, f"the {gene_sets} sets need experiment.organism, human or mouse")
    recipe_as_run = None
    if gene_sets == "consensus_nmf":  # Pertura's own code, kept until its place is decided
        scores, top, sil = nmf_programs(screen)
        about = {"top_genes": top, "silhouette": sil}
        score_call = {"package": "pertura", "function": "consensus NMF usages (its own code, until converted)"}
        df, per, test_calls = compare_scores_field(screen, scores, reference, test=test)
        calls, words_cal = [score_call] + test_calls, ""
    else:
        from .. import blocks as pt
        settings = {**(params or {}), **{x: v for x, v in (("components", components), ("n_iter", n_iter))
                                          if v is not None}} or None
        with pt.inside(screen, out_dir):
            prog, res, cal = programs_recipe(gene_sets, reference, gene_lists, k, fit_on, settings, test, select,
                                             cell_values, contrast, proteins)
        recipe_as_run = pt.recipe_as_run(programs_recipe, gene_sets=gene_sets, reference=reference,
                                         gene_lists=gene_lists, k=k, fit_on=fit_on, params=settings, test=test,
                                         select=select, cell_values=cell_values, contrast=contrast,
                                         proteins=proteins)
        per, df = res.table, res.extra.get("by_unit", pd.DataFrame())
        calls = (prog.calls if prog is not None else []) + res.calls
        if prog is not None:
            about = {"top_genes": {p: list(g.sort_values("gene_rank")["gene"].head(20))
                                   for p, g in prog.table.groupby("program")}, **prog.extra.get("about", {})}
        elif gene_sets in ("cell_values", "proteins"):
            about = {gene_sets: cell_values if gene_sets == "cell_values" else proteins or "all"}
        else:
            used = res.extra.get("genes_used", {})
            about = {"genes_used": used if gene_sets in ("given", "contrast") else
                     {n: len(v) for n, v in used.items()}}
        summary_cal = cal.extra.get("summary") if isinstance(cal, pt.Result) else None
        about["calibration"] = summary_cal if summary_cal else {"not_run": cal if isinstance(cal, str) else
                                                                "no result"}
        words_cal = _group_pairs_words(cal, "program")
    if per.empty:
        checks = figures.bars(np.zeros(1), ["none"], path, "No target with enough cells", "cells")
        return Result("not_enough_data", per, about, path, checks, "no target with 10 cells beside 10 reference cells")
    checks = _target_by_column_heatmap(per, "score", "effect", path,
                                       f"Program scores against the control cells ({gene_sets})",
                                       "Score vs control cells")
    hits = per[per["q"] < ADJUSTED]
    what = {"cnmf": "cNMF usages", "sklearn_nmf": "scikit-learn NMF usages", "genenmf": "GeneNMF usages",
            "genenmf_meta": "GeneNMF meta-program scores",
            "cell_values": "the per-cell values given",
            "proteins": "proteins, CLR per cell (Seurat's multimodal vignette)"}.get(gene_sets, "scanpy scores")
    diagnostics = {**about, "gene_sets": gene_sets, "reference": reference, "pairs_changed": int(len(hits)),
                   "package_calls": calls}
    if recipe_as_run:
        diagnostics["recipe_as_run"] = recipe_as_run
    return Result("detected" if len(hits) else "not_detected", per, diagnostics, path, checks,
                  f"{len(hits)} target x program pairs changed at adjusted p < {ADJUSTED} "
                  f"({what}; {per['test'].iat[0] if len(per) else 'no test'})" + words_cal,
                  tables={"by_unit": df}, biology=programs_biology(hits))


REGISTRY["C.programs"].recipe = programs_recipe  # every method but consensus_nmf runs as this recipe


# ---- states and composition ----------------------------------------------------------------------------------------
@register("C.states_and_composition", Contract(
    serves="C.states_and_composition",
    applies_when="always",
    needs="counts, labels, units and the reference cells",
    returns="cell states fitted without perturbation labels (scanpy: Leiden on the PCA of log-normalised highly "
            "variable genes), each named by its top markers (scanpy's Wilcoxon); per target and state: its share of "
            "cells against the control cells of its own samples, in its mix (same_samples), or against every "
            "control cell (all), by the design's test (auto: propeller on the replicates' proportions where 3 or "
            "more biological replicates hold both sides, else Fisher's exact test on the cells, scipy; the "
            "Cochran-Mantel-Haenszel test, which no reviewed screen uses, propeller by sample and scCODA as "
            "choices), Benjamini-Hochberg (statsmodels), with the cells and samples of each; per target, state and "
            "sample: the difference (by_unit.tsv); the package calls. With trajectory_root, each cell's diffusion "
            "pseudotime from it on the same graph (scanpy's tl.diffmap and tl.dpt), and per target its cells along "
            "it against the control cells of its own samples (the medians' shift, Mann-Whitney on the cells, "
            "Benjamini-Hochberg), beside the control guides' band; descriptive, the figure then",
    fails_when=[("one of a target's guides can carry its shift in a state",
                 "the shares with each guide alone, and the state's markers checked for doublets"),
                ("a change of a gene's mean is a change of states", "C.genes read beside these shares"),
                ("the target's cells and the control cells come from different samples, so the shares measure the "
                 "samples", "the control cells of the target's own samples (Pertura's default), with all control "
                 "cells, which published screens take more often, as the alternative")],
    tested_against="CAR-T: the targets ordered by their share of cells in the CD8 T-cell states, against an "
                   "earlier hand analysis's order",
    choices={"control_cells": ["same_samples", "all"], "resolution": [1.0, 0.5],
             "reference": ["control_guides", "other_targets"], "group_by": ["target", "vector"],
             "per_target": ["each", "best", "other"], "test": ["auto", "fisher", "cmh", "propeller", "sccoda"]},
    inputs={"naming_sets": "the sets each state's naming evidence is read on: an MSigDB collection by name "
                           "(cell_types, MSigDB's cell types, by default; immunesigdb for immune cells' states), "
                           "{name: [genes]}, or none",
            "state_markers": "{state: [genes]}: each cluster named the state whose markers it expresses most",
            "vectors": "group_by vector: {vector: [guides]}, each cell's vector by its strongest guide",
            "best_state": "per_target best or other: the state in which the vectors are compared (the larger "
                          "drop is the better)",
            "reference_labels": "the labels of the reference cells (default: the reference choice)",
            "quality": "{min_genes, max_mito, min_ribo}: a paper's thresholds on genes detected and on the shares "
                       "of counts from mitochondrial and ribosomal protein genes",
            "drop_labels": "labels left out before clustering (cells without a guide, or with several targets')",
            "maps": "draw the composition bars beside the control row and the density maps on a UMAP, and keep the "
                    "UMAP in cell_states (the paper's composition analysis)",
            "trajectory_root": "a state (S01, ...) or {column: level}, such as a progenitor state or the first time "
                               "point: the cells a diffusion pseudotime starts from, and each target's cells along it"},
    scope={"vectors": ("group_by", ["vector"]), "best_state": ("per_target", ["best", "other"])}))
def states_and_composition(screen, design, out_dir, resolution=1.0, reference="control_guides", seed=0,
                           group_by="target", per_target="each",
                           state_markers=None, vectors=None, best_state=None, reference_labels=None, quality=None,
                           drop_labels=None, maps=False, control_cells="same_samples", test="auto",
                           naming_sets="cell_types", trajectory_root=None):
    if state_markers or group_by != "target" or reference_labels or quality or drop_labels:
        if trajectory_root is not None:
            raise ValueError("trajectory_root runs on the states the module fits itself, without state_markers, "
                             "vectors, reference_labels, quality or drop_labels")
        left_out = np.zeros(len(screen.label), bool)
        if quality:
            left_out |= ~quality_mask(screen, **quality)
        if drop_labels:
            left_out |= np.isin(screen.label, list(drop_labels))
        return composition_by_groups(screen, out_dir, resolution, reference, seed, group_by, per_target,
                                     None, left_out, state_markers, vectors, best_state,
                                     reference_labels, maps)
    from .. import blocks as pt
    with pt.inside(screen, out_dir):
        st, comp, cal = states_recipe(resolution, reference, control_cells, seed, test, trajectory_root)
    recipe_as_run = pt.recipe_as_run(states_recipe, resolution=resolution, reference=reference,
                                     control_cells=control_cells, seed=seed, test=test,
                                     **({"root": trajectory_root} if trajectory_root is not None else {}))
    barcodes = np.asarray(screen.adata.obs_names).astype(str)
    each = st.extra["states"]
    each.index = each.index.astype(str)
    each = each.reindex(barcodes)
    lab = each["state"].fillna("").astype(str).to_numpy()
    xy = each[["umap_1", "umap_2"]].to_numpy(dtype=float)
    markers = st.extra["markers"].rename(columns={"names": "gene"})
    fitted = ~np.isin(lab, ["", "nan"])  # the cells the quality rule left out have no state
    names = sorted(set(lab[fitted]))
    # the states keep their IDs: the agent names them from the markers table (each gene's shares of cells inside and
    # outside the state) and what it knows; the dot plot draws each state's top markers, mitochondrial and ribosomal
    # protein genes left out, as marker lists usually are
    from ..quality import UNNAMED
    namers = markers[~markers["gene"].astype(str).str.match(UNNAMED)]
    state_name = {s: s for s in names}
    naming, naming_about = state_naming_evidence(screen, lab, design, naming_sets, seed)
    fits = {s: list(g["set"].head(2)) for s, g in naming.groupby("state")} if len(naming) else {}
    receptors = list(st.extra.get("receptor_segments_left_out") or [])
    per = comp.table.copy()
    per.insert(2, "state_name", per["state"].map(state_name))
    df = comp.extra.get("by_unit", pd.DataFrame(columns=["target", "state", "unit", "cells", "difference"]))
    calls = st.calls[:1] + comp.calls
    path = os.path.join(out_dir, "figure.png")
    # each cell's state with its name (its ID until the agent names it), the table notebooks draw states from
    cells = pd.DataFrame({"barcode": barcodes, "state": lab, "state_name": [state_name.get(s, "") for s in lab]})
    if per.empty:
        checks = figures.bars(np.zeros(1), ["none"], path, "No target with enough cells", "cells")
        return Result("not_enough_data", per, {}, path, checks, "no target with 10 cells beside 10 reference cells",
                      tables={"cell_states": cells, "markers": markers})
    view = screen if fitted.all() else screen.subset(cells=fitted)  # the figures draw the cells with a state
    ref_all = view.reference(reference)  # the figure's control shares: every reference cell
    # with a trajectory its figure leads, and the states' figure is kept beside it
    checks, dots = _states_figures(view, xy[fitted], lab[fitted], per, state_name, namers, ref_all,
                                   path if trajectory_root is None else os.path.join(out_dir, "states.png"), seed,
                                   control_cells)
    cells["umap_1"], cells["umap_2"] = xy[:, 0], xy[:, 1]  # kept, so later figures draw on the same map
    along, along_words, along_lines = pd.DataFrame(), "", []
    if trajectory_root is not None:
        cells["pseudotime"] = each["pseudotime"].to_numpy(float)  # a cell column for later scores and tests
        along, along_words, along_lines, checks = trajectory_reading(screen, cells["pseudotime"].to_numpy(float), xy,
                                                                     trajectory_root, reference, seed, path)
    hits = per[per["changed"].astype(bool)]
    how = composition_words(per, control_cells)
    summary_cal = cal.extra.get("summary") if isinstance(cal, pt.Result) else None
    said = (f"; {len(receptors)} T- or B-cell receptor segments left out of the variable genes, as T-cell clustering "
            "leaves them ({}). In T cells a state whose markers hold receptor V genes can be one clone's cells "
            "(the knowledge base's PF4)".format(", ".join(receptors[:6]) + (" ..." if len(receptors) > 6 else ""))
            if receptors else "")
    # each state's top markers in the summary itself: the states were left unnamed while the markers sat in a table
    # (GSE267740 on a15df12, GSE203592), and what is in view is what gets read
    shares = {"pct_nz_group", "pct_nz_reference"} <= set(namers.columns)
    tops = "; ".join(f"{s}: " + ", ".join(
        f"{r.gene} {round(100 * r.pct_nz_group)}/{round(100 * r.pct_nz_reference)}" if shares else str(r.gene)
        for r in namers[namers["state"] == s].head(4).itertuples())
        + (f" (fits {', '.join(fits[s])})" if s in fits else "") for s in names)
    return Result("detected" if len(hits) else "not_detected", per,
                  {"states": {s: int((lab == s).sum()) for s in names}, "state_names": state_name,
                   "resolution": resolution, "reference": reference, "control_cells": control_cells,
                   "test": test, "leads": comp.extra.get("leads"),
                   "pairs_changed": int(len(hits)), "package_calls": calls,
                   "receptor_segments_left_out": receptors,
                   "calibration": summary_cal or {"not_run": cal if isinstance(cal, str) else "no result"},
                   "recipe_as_run": recipe_as_run, "naming": naming_about},
                  path, checks, f"{len(names)} states, numbered: name each from its markers, here the test's top "
                  "four" + (" with the % of cells expressing each inside/outside the state" if shares else "") +
                  f" (mitochondrial and ribosomal protein genes left out; the markers table has "
                  f"{len(markers) // max(len(names), 1)} per state, those among them)"
                  + (f", and the two {naming_about['sets']} sets each state fits best (naming_evidence holds three, "
                     "decoupler's ranking as its tutorial annotates clusters)" if fits else "") + f": {tops}. "
                  f"{len(hits)} target x state pairs changed "
                  f"{changed_words(per)} ({how})" + _group_pairs_words(cal, "state") + said + along_words,
                  tables={"by_unit": df, "cell_states": cells, "markers": markers, "marker_dots": dots,
                          "naming_evidence": naming, **({"trajectory": along} if len(along) else {})},
                  biology=along_lines + states_biology(per))


TRAJECTORY_SHOWN = 6  # the targets whose densities along the pseudotime are drawn


def trajectory_shifts(screen, pseudotime, reference="control_guides", seed=0):
    """Each target's cells along a pseudotime against the control cells of its own samples, in its mix: the medians,
    the shift (the target's median less its control cells'), Mann-Whitney's two-sided p on the cells (scipy),
    Benjamini-Hochberg over the targets; each control guide's cells against the other control cells of its samples
    the same way, whose absolute shifts' 95th percentile is the band. Returns (the table, the band, the guides)."""
    from scipy import stats
    from statsmodels.stats.multitest import multipletests
    from .. import policy
    t = np.asarray(pseudotime, float)
    ok = np.isfinite(t)
    least = policy.get("min_cells")

    def one(tm, rm):
        tm, rm, _ = screen.matched(tm & ok, rm & ok, least, seed)
        if tm.sum() < least or rm.sum() < least:
            return None
        a, b = t[tm], t[rm]
        return {"cells": int(tm.sum()), "control_cells": int(rm.sum()), "median": float(np.median(a)),
                "control_median": float(np.median(b)), "shift": float(np.median(a) - np.median(b)),
                "p": float(stats.mannwhitneyu(a, b).pvalue)}
    rows = []
    for target in screen.targets(least):
        tm = screen.label == target
        r = one(tm, screen.reference(reference, target=target) & ~tm)
        if r:
            rows.append({"target": target, **r})
    table = pd.DataFrame(rows, columns=["target", "cells", "control_cells", "median", "control_median", "shift", "p"])
    control, guide = screen.label == "control", screen.top_guide
    shifts = [r["shift"] for g in sorted(set(guide[control]) - {""})
              if (r := one(control & (guide == g), control & (guide != g)))]
    band = float(np.percentile(np.abs(shifts), 95)) if shifts else None
    if len(table):
        table["q"] = multipletests(table["p"].to_numpy(), method="fdr_bh")[1]
        table["past_band"] = table["shift"].abs() > band if band is not None else None
        table = table.reindex(table["shift"].abs().sort_values(ascending=False).index).reset_index(drop=True)
    return table, band, len(shifts)


def trajectory_reading(screen, pseudotime, xy, root, reference, seed, path):
    """The trajectory's reading: each target's shift along the pseudotime (trajectory_shifts), its lines and words,
    and the figure, as published screens draw cells along a pseudotime: a, the cells coloured by it; b, the densities
    of the targets that moved most against the control cells'; c, every target's shift beside the control guides'
    band. Returns (the table, the words, the lines, the figure's checks)."""
    from .. import policy
    from . import charts, figure_series
    table, band, guides = trajectory_shifts(screen, pseudotime, reference, seed)
    named = (f"{next(iter(root))} {next(iter(root.values()))}" if isinstance(root, dict) else f"state {root}")
    lost = int((~np.isfinite(pseudotime) & ~np.isnan(xy[:, 0])).sum())
    fit = ~np.isnan(xy[:, 0])
    cells_by_time = (0.9, lambda p: charts.embedding_values(xy[fit], pseudotime[fit], None, "Pseudotime", fig=p),
                     f"Cells by diffusion pseudotime from {named}")
    title = f"Cells along a pseudotime from {named}"
    if table.empty:
        issues, _ = figure_series.compose([(55, [cells_by_time])], path, title)
        return (table, f"; no target holds {policy.get('min_cells')} cells along the pseudotime from {named}", [],
                charts.checks(issues, reference=False))
    moved = table[table["q"] < ADJUSTED]
    if band is not None:
        moved = moved[moved["past_band"].astype(bool)]
    shown = list((moved if len(moved) else table)["target"].head(TRAJECTORY_SHOWN))
    rows = [(55, [cells_by_time,
                  (1.1, lambda p: charts.pseudotime_densities(pseudotime, screen.label, None, "", order=shown, fig=p),
                   "Cells along the pseudotime, the control cells grey")]),
            (60, [(1, lambda p: charts.ranked_targets(  # the largest shifts either way (the table's order)
                table.head(80), "shift", None, "Median pseudotime, target - control", "", q="q",
                control_band=None if band is None else (-band, band), fig=p),
                "Each target's shift against the control cells of its samples" +
                ("" if band is None else f"; grey, the {guides} control guides' band"))])]
    issues, _ = figure_series.compose(rows, path, title)
    later = ", ".join(f"{r.target} ({r.shift:+.2f})" for r in moved.itertuples() if r.shift > 0)
    earlier = ", ".join(f"{r.target} ({r.shift:+.2f})" for r in moved.itertuples() if r.shift < 0)
    words = (f"; along a diffusion pseudotime from {named} (scanpy's tl.diffmap and tl.dpt, from the root's cell "
             f"nearest its mean" + (f"; {lost} cells not joined to it" if lost else "") + f"), {len(moved)} of "
             f"{len(table)} targets' cells sit elsewhere than their control cells' (Mann-Whitney on the cells, "
             f"q < {ADJUSTED}" + ("" if band is None else f", past the {guides} control guides' band, {band:.3f}")
             + ")" + (f": later {later}" if later else "") + (f"; earlier {earlier}" if earlier else ""))
    lines = [f"{r.target}: {'later' if r.shift > 0 else 'earlier'} along the pseudotime from {named} than its "
             f"control cells (median {r.median:.2f} against {r.control_median:.2f}, q = {r.q:.2g})"
             for r in moved.itertuples()]
    return table, words, lines, charts.checks(issues, reference=band is not None, zero=True)


NAMING_CELLS, NAMING_TOP = 2000, 3  # each state's cells read for its naming evidence, and the sets it gives


def state_naming_evidence(screen, lab, design, naming="cell_types", seed=0):
    """Each state's naming evidence, as decoupler's tutorial annotates clusters: each cell's score on a collection's
    sets (MSigDB's cell types by default) by decoupler's univariate linear model (tmin 3), then each state's sets
    ranked against the other states' cells (rankby_group, a t-test with the variance overestimated), the top three
    with a positive statistic, on at most NAMING_CELLS cells of each state. It sits beside the markers; the agent names
    the state. Returns (the table: state, set, stat, meanchange, padj; what was read)."""
    organism = value(design, "experiment.organism")
    if naming in (None, "none"):
        return pd.DataFrame(), {"not_run": "naming_sets none"}
    given = naming if isinstance(naming, dict) else None
    if given is None and organism not in ("human", "mouse"):
        return pd.DataFrame(), {"not_run": f"the {naming} sets need experiment.organism, human or mouse"}
    import anndata as ad
    import decoupler as dc
    from ..steps import enrichment
    from . import packages
    net = enrichment.network(None if given else naming, organism, given)
    rng = np.random.default_rng(seed)
    states = sorted(set(lab) - {"", "nan"})
    rows = np.sort(np.concatenate([rng.permutation(np.flatnonzero(lab == s))[:NAMING_CELLS] for s in states]))
    a = ad.AnnData(screen.lognorm[rows], obs=pd.DataFrame({"state": pd.Categorical(lab[rows])},
                                                         index=[f"c{i}" for i in rows]),
                   var=pd.DataFrame(index=pd.Index(screen.genes).astype(str)))
    a.var_names_make_unique()
    try:
        dc.mt.ulm(data=a, net=net, tmin=3)
    except (ValueError, AssertionError) as e:  # no set keeps 3 of its genes (decoupler asserts it): the states stand
        return pd.DataFrame(), {"not_run": f"no {naming if not given else 'given'} set has 3 of its genes among the "
                                           f"screen's genes ({e})"}
    ranked = dc.tl.rankby_group(adata=dc.pp.get_obsm(a, key="score_ulm"), groupby="state", reference="rest",
                                method="t-test_overestim_var")
    ranked = ranked[ranked["stat"] > 0].sort_values(["group", "stat"], ascending=[True, False])
    table = ranked.groupby("group").head(NAMING_TOP).rename(columns={"group": "state", "name": "set"})
    about = {"sets": "given lists" if given else naming, "cells_per_state": NAMING_CELLS,
             "package_calls": [packages.call("decoupler", "mt.ulm", contrast="each cell's log-normalised counts",
                                             net="given lists" if given else naming, tmin=3),
                               packages.call("decoupler", "tl.rankby_group", contrast="each state against the "
                                             "other states' cells", method="t-test_overestim_var")]}
    return table[["state", "set", "stat", "meanchange", "padj"]].reset_index(drop=True), about


def states_recipe(resolution=1.0, reference="control_guides", control_cells="same_samples", seed=0, test="auto",
                  root=None):
    """C.states_and_composition as blocks (docs/adr/0031, D2): the cells' states (Leiden on the PCA of log-normalised
    highly variable genes, with each state's markers and a UMAP; with root, each cell's diffusion pseudotime from it),
    each target's share of each state against its reference by the test (auto: the design's lead per target), and the
    same test on groups of control guides."""
    from .. import blocks as pt
    st = pt.states(reference=reference, resolution=resolution, seed=seed, root=root)
    comp = pt.composition(of=st, column="state", reference=reference, control_cells=control_cells, seed=seed,
                          method=test)
    try:
        cal = pt.calibrate(comp)
    except ValueError as e:  # another reference, or too few control guides for groups
        cal = str(e)
    return st, comp, cal


REGISTRY["C.states_and_composition"].recipe = states_recipe  # its default path; the paper path is its own code


# ---- between or within states --------------------------------------------------------------------------------------
@register("C.within_states", Contract(
    serves="C.within_states", within="each state",
    applies_when="a target whose share of a state shifted (C.states_and_composition)",
    needs="each cell's state (C.states_and_composition's cell_states table), raw counts, samples, the control cells",
    returns="per target and state: the genes changed within the state, the target's cells in it against the control "
            "cells in it, by the test the design chooses for the pair (as C.genes: edgeR-QL pseudobulk by replicate "
            "where 3 or more biological replicates hold both, else scanpy's Wilcoxon on cells; q < 0.05), beside the "
            "shift in the target's share of the state; so a shift between states is told from a change within one, "
            "as Xia et al. 2023 and Belk et al. 2022 test it",
    fails_when=[("few of a target's cells in a state give no power to see a change within it",
                 "the cells per target and state, beside each count"),
                ("a state's own markers differ little between target and control cells, so a change within the "
                 "state can be a finer state", "the changed genes against the state markers")],
    tested_against="synthetic (calibration screen, two states drawn at random): the target lowering its gene and g0 "
                   "changes both within a state; the inert target changes none",
    choices={"min_cells": [10, 20]},
    inputs={"cell_states": "each cell's state: C.states_and_composition's cell_states table, or its TSV path "
                           "(default: the build's latest states run's)",
            "pairs": "[[target, state], ...] to test; by default the composition table's pairs at adjusted p < 0.05",
            "composition": "C.states_and_composition's result table, or its TSV path (for the default pairs and "
                           "each pair's shift; default: the run that gives cell_states)"}))
def within_states(screen, design, out_dir, cell_states=None, pairs=None, composition=None, min_cells=MIN_CELLS):
    from . import charts
    if cell_states is None:
        raise ValueError("C.within_states needs cell_states: give a C.states_and_composition run's ID, and its table "
                         "of each cell's state and its composition are both taken from it")
    cs = pd.read_csv(cell_states, sep="\t") if isinstance(cell_states, str) else cell_states
    comp = pd.read_csv(composition, sep="\t") if isinstance(composition, str) else composition
    state = (pd.Series(cs["state"].astype(str).to_numpy(), index=cs["barcode"].astype(str))
             .reindex(np.asarray(screen.adata.obs_names).astype(str)).fillna("").to_numpy())
    if pairs is None:
        if comp is None:
            raise ValueError("C.within_states needs pairs, or the composition table to take them from")
        pairs = comp[comp["q"] < ADJUSTED][["target", "state"]].astype(str).values.tolist()
    shift = ({(str(r.target), str(r.state)): float(r.mean_difference) for r in comp.itertuples()}
             if comp is not None else {})
    rows, changed, calls, all_leads = [], [], [], {}
    for st in sorted({s for _, s in pairs}):
        ts = [t for t, s in pairs if s == st]
        name = {t: f"{t} in {st}" for t in ts}
        lab = np.full(len(screen.label), "outside", dtype=object)
        lab[(state == st) & (screen.label == "control")] = "control"
        for t in ts:
            lab[(state == st) & (screen.label == t)] = name[t]
        s2 = screen.with_labels(lab)
        # each target's control cells in the state are those its test uses: its arm's (bug register, BR02)
        ctrl = {t: int(s2.reference("control_guides", target=name[t]).sum()) if (lab == name[t]).any() else 0
                for t in ts}
        run = [t for t in ts if (lab == name[t]).sum() >= min_cells and ctrl[t] >= min_cells]
        table, leads = pd.DataFrame(columns=["target", "gene", "effect", "q", "test"]), {}
        if run:
            leads = leads_of(s2, [name[t] for t in run], "control_guides")
            table, more = genes_by_leads(s2, leads, "control_guides", os.path.join(out_dir, f"state_{st}"))
            calls += more
            all_leads.update(leads)
        for t in ts:
            tt = table[table["target"] == name[t]]
            hit = tt[tt["q"] < GENES_Q]
            rows.append({"target": t, "state": st, "pair": name[t], "cells": int((lab == name[t]).sum()),
                         "control_cells": ctrl[t], "tested": t in run,
                         "test": leads[name[t]]["method"] if name[t] in leads else "",
                         "share_difference": shift.get((t, st), np.nan), "genes_changed_within": int(len(hit)),
                         "up": int((hit["effect"] > 0).sum()), "down": int((hit["effect"] < 0).sum())})
            changed.append(hit.assign(target=t, state=st))
    df = pd.DataFrame(rows, columns=["target", "state", "pair", "cells", "control_cells", "tested", "test",
                                     "share_difference", "genes_changed_within", "up", "down"])
    path = os.path.join(out_dir, "figure.png")
    shown = df[df["tested"].astype(bool)].rename(columns={"target": "name", "pair": "target"})
    if shown.empty:
        checks = figures.bars(np.zeros(1), ["none"], path, "No target with enough cells in a state", "cells")
        return Result("not_enough_data", df, {"pairs": len(df)}, path, checks,
                      f"no target with {min_cells} cells in a shifted state beside {min_cells} control cells there"
                      if pairs else f"no target-state pair to test: the composition shifts no target's share of a "
                                    f"state at q < {ADJUSTED}; pairs names the pairs to test")
    checks = charts.checks(charts.two_scores(shown.fillna({"share_difference": 0.0}), "share_difference",
                                             "genes_changed_within", path, "Between or within states",
                                             "Share of the state, target minus control cells",
                                             "Genes changed within the state"), zero=None)
    n_within = int((shown["genes_changed_within"] > 0).sum())
    return Result("detected" if n_within else "not_detected", df,
                  {"pairs": len(df), "tested": int(len(shown)), "with_changes_within": n_within,
                   "package_calls": calls}, path, checks,
                  f"{n_within} of {len(shown)} target-state pairs change genes within the state at q < {GENES_Q}, against "
                  f"the control cells in the same state ({lead_words(all_leads)})",
                  tables={"changed_within": pd.concat(changed, ignore_index=True) if changed else pd.DataFrame()})


# ---- cell cycle ------------------------------------------------------------------------------------------------------
def cell_cycle_recipe(method="scanpy", reference="control_guides", test="auto"):
    """C.cell_cycle as blocks (docs/adr/0031, D2): each cell's cell-cycle phase, each target's phase shares against
    its reference by the test (auto: the design's lead per target), and the same test on groups of control
    guides."""
    from .. import blocks as pt
    cc = pt.cell_cycle(method=method)
    comp = pt.composition(of=cc, column="phase", reference=reference, method=test)
    try:
        cal = pt.calibrate(comp)
    except ValueError as e:  # another reference, or too few control guides for groups
        cal = str(e)
    return cc, comp, cal


@register("C.cell_cycle", Contract(
    serves="C.cell_cycle",
    applies_when="human or mouse (experiment.organism)",
    needs="log-normalised expression (or raw counts for Seurat's), labels, samples, the control cells; the organism",
    returns="each cell's S and G2/M scores and phase (G1, S, G2M: scanpy's score_genes_cell_cycle, or Seurat's "
            "CellCycleScoring, with Seurat's cc.genes.updated.2019 from Tirosh et al. 2016); per target and phase: "
            "its share of cells against the control cells by the design's test (auto: propeller on the replicates' "
            "proportions where 3 or more biological replicates hold both sides, else Fisher's exact test on the "
            "cells against the control cells of its own samples), Benjamini-Hochberg over targets and phases; the "
            "same test on groups of control guides (calibration: "
            "each phase's 95th percentile of their share differences)",
    fails_when=[("a target that stops or speeds cycling moves every proliferation-linked gene and program",
                 "the gene test with the cell-cycle scores as latent variables (C.genes params cell_cycle)"),
                ("cell-cycle genes respond to stress or differentiation too", "the phase shares read with the "
                 "cells' states")],
    tested_against="the synthetic screen: a target whose cells have the S genes raised has more S cells than the "
                   "controls",
    choices={"method": ["scanpy", "seurat"], "reference": ["control_guides", "other_targets"],
             "test": ["auto", "fisher", "cmh", "propeller", "sccoda"]}))
def cell_cycle(screen, design, out_dir, method="scanpy", reference="control_guides", test="auto"):
    from .. import blocks as pt
    path = os.path.join(out_dir, "figure.png")
    if value(design, "experiment.organism") not in ("human", "mouse"):
        checks = figures.bars(np.zeros(1), ["none"], path, "No cell-cycle genes for this organism", "cells")
        return Result("not_enough_data", pd.DataFrame(), {"organism": value(design, "experiment.organism")}, path,
                      checks, "the cell-cycle genes need experiment.organism, human or mouse")
    with pt.inside(screen, out_dir):
        cc, comp, cal = cell_cycle_recipe(method, reference, test)
    per = comp.table
    if per.empty:
        checks = figures.bars(np.zeros(1), ["none"], path, "No target with enough cells", "cells")
        return Result("not_enough_data", per, {}, path, checks, "no target with 10 cells beside 10 reference cells")
    checks = _target_by_column_heatmap(per.dropna(subset=["mean_difference"]), "state", "mean_difference", path,
                                       "Cell-cycle phase shares against the control cells", "Share vs control cells")
    hits = per[per["changed"].astype(bool)]
    summary_cal = cal.extra.get("summary") if isinstance(cal, pt.Result) else None
    words = (f"{len(hits)} target x phase shares differ from the control cells' {changed_words(per)} "
             f"({'scanpy score_genes_cell_cycle' if method == 'scanpy' else 'Seurat CellCycleScoring'}; "
             f"{composition_words(per, 'same_samples')})")
    words += _group_pairs_words(cal, "phase")
    shares = cc.table
    return Result("detected" if len(hits) else "not_detected", per,
                  {"method": method, "reference": reference, "pairs_changed": int(len(hits)),
                   "calibration": summary_cal or {"not_run": cal if isinstance(cal, str) else "no result"},
                   "package_calls": cc.calls + comp.calls,
                   "test": test, "leads": comp.extra.get("leads"),
                   "recipe_as_run": pt.recipe_as_run(cell_cycle_recipe, method=method, reference=reference,
                                                     test=test)},
                  path, checks, words, tables={"phase_shares": shares})


REGISTRY["C.cell_cycle"].recipe = cell_cycle_recipe
