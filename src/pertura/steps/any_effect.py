"""The any-effect step (docs/adr/0031, D1a): whether each target's cells differ from its reference at all, by
pertpy's energy-distance permutation test (DistanceTest, edistance) in the PCA space of log-normalised highly variable
genes (scanpy), against the control cells of the target's own samples in its mix (same_samples) or every control cell
(all); Benjamini-Hochberg over the targets tested. The PCA is fitted on every single-target and reference cell, so a
call on some targets (only) gives each the numbers a call on all gives it; each target's permutations are seeded by its
place among the screen's targets.

    per, calls, drawn = test(screen, reference="control_guides", only=None, draw=CAP)

With draw CAP (pt.any_effect's default) each side of a test is drawn to at most CAP cells, as the overview's
distances are, so that a screen of many cells is tested in minutes.

distances() is the screen overview's (A.screen_overview): the same distance without its permutation test, each target
read against random groups of control guides, the target and the groups drawn to one number of cells, since the energy
distance of a small group reads larger from its few cells alone.

    per, calls = distances(screen)
"""
import numpy as np

from .. import limits, policy
import pandas as pd
import scipy.sparse as sp


METRICS = ("edistance", "mmd", "mean_pairwise", "euclidean", "pearson_distance", "cosine_distance",
           "spearman_distance", "mahalanobis")  # pertpy's Distance metrics that need no further package


def _draw(mask, n, rng):
    """The mask cut to n of its cells, drawn without replacement; the mask as it is where it holds n or fewer."""
    idx = np.flatnonzero(mask)
    if len(idx) <= n:
        return mask
    out = np.zeros_like(mask)
    out[rng.choice(idx, n, replace=False)] = True
    return out


def test(screen, reference="control_guides", n_pcs=50, permutations=1000, seed=0, control_cells="same_samples",
         max_reference=None, only=None, metric="edistance", draw=None, draw_reference=None, embedding=None):
    """Per target: cells, reference_cells, cells_left_out, samples, cells_drawn, reference_cells_drawn, e_distance, p,
    q, umis_ratio, and the metric and its distance (pertpy's DistanceTest with that metric; e_distance holds the
    energy distance, the default); the calls; and the draw, {at_most}. draw: None, every cell; a number, each side
    (the target's cells, its reference cells) drawn, seeded by the target's place, to at most that many, as the
    overview's distances are (CAP, pt.any_effect's default), which keeps a large screen's test to minutes. The energy
    distance of a side with fewer cells reads larger, so two targets' distances compare at the same number of cells
    (the overview reads each against control-guide groups of its own number). draw_reference: the reference side's
    own number, where the target's cells are drawn to fewer (a down-sampling); embedding: _embedding's result, for
    many calls on one screen."""
    if metric not in METRICS:
        raise ValueError(f"metric is one of {list(METRICS)}, not {metric!r}")
    from statsmodels.stats.multitest import multipletests
    from ..question_modules import c, packages
    cap = None if draw in (None, "none") else int(draw)
    a, idx, pca_call = embedding or _embedding(screen, reference, n_pcs, seed)
    calls = [pca_call,
             packages.call("pertpy", "tl.DistanceTest", contrast="each target's cells against "
                           + ("the control cells" if reference == "control_guides" else "the other targets' cells")
                           + ((" of its own samples and conditions, in its mix" if screen.crossed else
                               " of its own samples, in its mix") if control_cells == "same_samples" else ""),
                           metric=metric, obsm_key="X_pca", n_perms=permutations,
                           permutation_seed=seed, cells_drawn_at_most=cap if cap else "every cell"),
             packages.call("statsmodels", "stats.multitest.multipletests", method="fdr_bh")]
    rows, jobs = [], []
    lab = screen.label[idx]
    # each target's draws seeded by its place among the screen's targets: a call on some of them gives the numbers
    # the whole call gives them
    wanted = set(only) if only is not None else None
    for place, tname in enumerate(screen.targets(policy.get("min_cells"))):
        if wanted is not None and tname not in wanted:
            continue
        tm = lab == tname
        rm = screen.reference(reference, target=tname)[idx] & ~tm
        left = 0
        if control_cells == "same_samples":
            tm, rm, left = c.same_sample_controls(screen.strata[idx], tm, rm, policy.get("min_cells"), seed)
        if tm.sum() < policy.get("min_cells") or rm.sum() < policy.get("min_cells"):
            continue
        if max_reference:  # a reference of every other target's cells: drawn (seeded) to a multiple of the target's
            k = int(max(c.MIN_REFERENCE_DRAW, max_reference * tm.sum()))
            if rm.sum() > k:
                pick = np.random.default_rng(seed * 100003 + place).choice(np.flatnonzero(rm), k, replace=False)
                rm = np.zeros_like(rm)
                rm[pick] = True
        row = {"target": tname, "cells": int(tm.sum()), "reference_cells": int(rm.sum()),
               "cells_left_out": int(left), "samples": len(set(screen.unit[idx][tm])),
               "umis_ratio": float(np.median(screen.libsize[idx][tm]) /
                                   max(np.median(screen.libsize[idx][rm]), 1))}
        if cap:  # each side drawn, seeded by the target's place, to at most cap cells
            rng = np.random.default_rng((seed, place))
            tm, rm = _draw(tm, cap, rng), _draw(rm, int(draw_reference or cap), rng)
        row.update(cells_drawn=int(tm.sum()), reference_cells_drawn=int(rm.sum()))
        pcs = a.obsm["X_pca"]
        jobs.append((tname, pcs[tm], pcs[rm], permutations, seed * 100003 + place, metric))
        rows.append(row)
    # the targets over DISTANCE_WORKERS processes: each test is pertpy's own, and the targets are independent
    from concurrent.futures import ProcessPoolExecutor
    if jobs:
        n = max(1, min(c.DISTANCE_WORKERS, len(jobs)))
        with ProcessPoolExecutor(n, initializer=limits.set_threads, initargs=(limits.worker_threads(n),)) as ex:
            results = list(ex.map(c._distance_test, jobs))
        for row, (d, p) in zip(rows, results):
            row.update(e_distance=d if metric == "edistance" else np.nan, p=p, metric=metric, distance=d)
    per = pd.DataFrame(rows, columns=["target", "cells", "reference_cells", "cells_left_out", "samples",
                                      "cells_drawn", "reference_cells_drawn", "e_distance", "p", "umis_ratio",
                                      "metric", "distance"])
    if len(per):
        per = per.sort_values("distance", ascending=False).reset_index(drop=True)
        per["q"] = multipletests(per["p"].to_numpy(), method="fdr_bh")[1]
        per["distance_rank"] = np.arange(1, len(per) + 1)  # not "rank", a DataFrame method (item 18.2)
    return per, calls, {"at_most": cap}


def _embedding(screen, reference, n_pcs, seed):
    """The PCA of log-normalised highly variable genes over every single-target and reference cell (scanpy's
    tutorial settings): (the AnnData with obsm X_pca, the cells' places in the screen, the package call)."""
    import anndata as ad
    import scanpy as sc
    from ..question_modules import packages
    idx = np.flatnonzero(screen.single() | screen.reference(reference))
    L = screen.lognorm[idx]
    n_top = min(2000, L.shape[1])
    call = packages.call("scanpy", "pp.highly_variable_genes, pp.pca", data=L, layer="lognorm", n_comps=n_pcs,
                         n_top_genes=n_top, random_state=seed)
    a = ad.AnnData(sp.csr_matrix(L), obs=pd.DataFrame({"label": screen.label[idx]}, index=[f"c{i}" for i in idx]),
                   var=pd.DataFrame(index=pd.Index(screen.genes).astype(str)))
    a.var_names_make_unique()
    from ..quality import variable_genes
    variable_genes(a, n_top_genes=n_top)  # scanpy's tutorial setting, the top 2,000; receptor segments left out (PF4)
    sc.pp.pca(a, n_comps=max(2, min(n_pcs, a.n_obs - 1, int(a.var["highly_variable"].sum()) - 1)),
              mask_var="highly_variable", random_state=seed)
    return a, idx, call


CAP = 1000  # each side's cells drawn, for the overview's distances and pt.any_effect's test, so a screen is read in
# minutes
LINE_GROUPS = 20  # random groups of control guides, each of a target's number of guides, read for a target's line
MIN_LINE_GROUPS = 3  # groups that hold the cells a target is drawn to, for its line


def _edistance(job):
    """pertpy's energy distance between two sets of PCA coordinates, in a worker process."""
    import pertpy as pt
    _, X, Y = job
    return float(pt.tl.Distance("edistance", obsm_key="X_pca")(X, Y))


def _guides_per_target(screen):
    """The median number of guides of a target: the size of a group of control guides read as one."""
    tg = screen.guides[screen.guides["kind"].astype(str) == "targeting"].groupby("target")["guide"].nunique()
    return max(1, int(np.median(tg))) if len(tg) else 1


def _guide_sets(guides, k, count, rng):
    """count distinct sets of k of the guides, drawn by rng; every set where there are no more than count; none where
    no guide would be left beside a set."""
    from itertools import combinations
    from math import comb
    if len(guides) <= k:
        return []
    if comb(len(guides), k) <= count:
        return [list(s) for s in combinations(guides, k)]
    seen = set()
    while len(seen) < count:
        seen.add(tuple(sorted(rng.choice(guides, k, replace=False))))
    return [list(s) for s in sorted(seen)]


def distances(screen, reference="control_guides", n_pcs=50, seed=0, control_cells="same_samples", cap=CAP,
              groups=LINE_GROUPS):
    """Per target: cells, cells_drawn, reference_cells, e_distance (pertpy's energy distance from the control cells of
    its own samples, in its mix, as test() computes it, without the permutation test), effect_line (the 95th
    percentile of the same distance for groups of control guides, each of `groups` random sets of a target's number of
    guides among its arm's control guides, against the rest of its arm's control cells), line_groups (the groups read;
    no line with fewer than MIN_LINE_GROUPS) and umis_ratio; and the calls. The target and its groups are drawn,
    seeded, to one number of cells (cells_drawn): the smallest of its cells, cap, and what MIN_LINE_GROUPS of the
    groups hold, since the energy distance of a group reads larger from its few cells alone."""
    from concurrent.futures import ProcessPoolExecutor
    from ..question_modules import c, packages
    a, idx, pca_call = _embedding(screen, reference, n_pcs, seed)
    pcs = a.obsm["X_pca"]
    lab = screen.label[idx]
    ctrl = lab == "control"
    guide = np.asarray(screen.top_guide)[idx]
    k = _guides_per_target(screen)
    least = policy.get("min_cells")
    rows, jobs = [], []
    for place, t in enumerate(screen.targets(least)):
        tm = lab == t
        arm = screen.reference(reference, target=t)[idx]
        rm = arm & ~tm
        if control_cells == "same_samples":
            tm, rm, _ = c.same_sample_controls(screen.strata[idx], tm, rm, least, seed)
        if tm.sum() < least or rm.sum() < least:
            continue
        line = []
        for gs in _guide_sets(sorted(set(guide[arm & ctrl]) - {""}), k, groups, np.random.default_rng((seed, place))):
            gm = arm & ctrl & np.isin(guide, gs)
            rest = arm & ctrl & ~gm
            if gm.sum() >= least and rest.sum() >= least:
                line.append((gm, rest))
        held = sorted((int(gm.sum()) for gm, _ in line), reverse=True)
        n = int(min(tm.sum(), cap, held[MIN_LINE_GROUPS - 1] if len(held) >= MIN_LINE_GROUPS else cap))
        m = int(min(rm.sum(), cap))
        rng = np.random.default_rng(seed * 100003 + place)
        jobs.append(((t, "target"), pcs[rng.choice(np.flatnonzero(tm), n, replace=False)],
                     pcs[rng.choice(np.flatnonzero(rm), m, replace=False)]))
        used = 0
        for gm, rest in line:
            if gm.sum() < n:
                continue
            used += 1
            jobs.append(((t, "line"), pcs[rng.choice(np.flatnonzero(gm), n, replace=False)],
                         pcs[rng.choice(np.flatnonzero(rest), int(min(m, rest.sum())), replace=False)]))
        rows.append({"target": t, "cells": int(tm.sum()), "cells_drawn": n, "reference_cells": int(rm.sum()),
                     "line_groups": used,
                     "umis_ratio": float(np.median(screen.libsize[idx][tm]) /
                                         max(np.median(screen.libsize[idx][rm]), 1))})
    found = {}
    if jobs:
        n = max(1, min(c.DISTANCE_WORKERS, len(jobs)))
        with ProcessPoolExecutor(n, initializer=limits.set_threads, initargs=(limits.worker_threads(n),)) as ex:
            for (key, _, _), d in zip(jobs, ex.map(_edistance, jobs)):
                found.setdefault(key, []).append(d)
    for row in rows:
        row["e_distance"] = found.get((row["target"], "target"), [np.nan])[0]
        line = found.get((row["target"], "line"), [])
        row["effect_line"] = (float(np.percentile(line, 95)) if row["line_groups"] >= MIN_LINE_GROUPS
                              else np.nan)
    per = pd.DataFrame(rows, columns=["target", "cells", "cells_drawn", "reference_cells", "e_distance",
                                      "effect_line", "line_groups", "umis_ratio"])
    calls = [pca_call, packages.call("pertpy", "tl.Distance", metric="edistance", obsm_key="X_pca",
                                     contrast="each target's cells, and random groups of control guides of a "
                                              "target's number of guides drawn to the same cells, against control "
                                              "cells", cells_drawn_at_most=cap, control_guide_groups=groups)]
    return per, calls
