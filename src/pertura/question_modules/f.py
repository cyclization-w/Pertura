"""Question F: whether the screen's findings hold up (here: whether a target's guides agree)."""
import os

import numpy as np

from .. import policy
import pandas as pd

from . import figures
from .contract import REGISTRY, Contract, Result, register

from ..steps.profiles import WELL_EXPRESSED  # UMIs per control cell: the genes Replogle et al. 2022 correlate over
from ..steps.similarity import control_kind_of, masked_genes  # noqa: F401 (the step's, used here and by e.py)

MIN_CELLS = 10


def well_expressed(screen, mask, at_least=WELL_EXPRESSED):
    """Genes with at least `at_least` UMIs per cell, on average, in the masked cells."""
    idx = np.flatnonzero(mask)
    return np.asarray(screen.X[idx].sum(0)).ravel() >= at_least * max(len(idx), 1)


def guide_pairs(screen, design=None, min_cells=None):
    """Every two guides' profile correlation (Pearson), each cell against its unit's control cells and pooled over
    the units of one kind of capture (the similarity step's default), with the pair's kind:
    two guides of one target, guides of two targets, or two control guides of one kind (the reference: one control
    group and one mode, as a target's guides share a vector and a mode). The similarity step's guide level
    (pertura.steps.similarity), in this function's columns."""
    min_cells = policy.get("min_cells") if min_cells is None else min_cells
    from ..steps import similarity
    table, _, _ = similarity.pairs(screen, "guide", min_cells=min_cells, design=design)
    return table.rename(columns={"group": "guide"})[["guide", "other", "target", "other_target", "kind",
                                                    "correlation", "correlation_halves", "units"]]


@register("F.guides_agree", Contract(
    serves="F.guides_agree",
    applies_when="2 or more guides per target",
    needs="counts, labels, each cell's strongest guide, units and the control cells",
    returns="every two guides' profiles (each cell against its own sample's control cells, pooled over the samples "
            "of one kind of capture, over the well-expressed genes, the targeted genes left out) correlated, as pairs of one target, of two targets, and of two control guides "
            "of one kind (one control group and mode: the reference); per "
            "target: its guide pairs' median correlation, the share of control-guide pairs as high and the share of "
            "pairs of two targets' guides as high, the pairs of its own arm; the pairs of one target's guides "
            "against the pairs of two targets' guides of one arm (Mann-Whitney); the pairs' distributions as the "
            "figure",
    fails_when=[("guides of one target sit in different samples, so their profiles differ by sample",
                 "each cell is set against its own sample's control cells before a guide's cells are pooled, and "
                 "profiles are compared within one kind of capture"),
                ("a guide's change of its target gene makes the guides of one target alike",
                 "the targeted genes are left out of the profiles"),
                ("guides with few cells have noisy profiles that agree with nothing",
                 "the control-guide pairs, of guides with as few cells, are the reference"),
                ("profiles set against the same control cells share that mean's noise, the more the more cells a guide has",
                 "the reference reads each pair with its two sides set against disjoint halves of the control cells, averaged over 16 random splits; the correlation against all of them is the one reported")],
    tested_against="the synthetic screen: the guides of a target with a planted effect correlate above every pair "
                   "of control guides and every pair of two targets' guides, and those of a target with none do not",
    choices={"min_cells": [10, 30]}))
def guides_agree(screen, design, out_dir, min_cells=None):
    min_cells = policy.get("min_cells") if min_cells is None else min_cells
    from .. import blocks as pt
    from . import charts
    with pt.inside(screen, out_dir):
        sim = guides_agree_recipe(min_cells)
    pairs = sim.table.rename(columns={"group": "guide"})[["guide", "other", "target", "other_target", "kind",
                                                          "correlation", "correlation_halves", "units"]]
    path = os.path.join(out_dir, "figure.png")
    same = pairs[pairs["kind"] == "same target"]
    if same.empty:
        checks = figures.bars(np.zeros(1), ["none"], path, "No target with two guides of enough cells", "cells")
        return Result("not_enough_data", pairs, {}, path, checks,
                      f"no target with two guides of {min_cells} cells, pooled over the samples of one kind "
                      "of capture")
    # the reference reads each pair against halves of the control cells, as pairs set against the same control
    # cells share that mean's noise
    ctrl = pairs[pairs["kind"] == "control guides"]["correlation_halves"].dropna().to_numpy()
    per = (same.groupby("target").agg(median_correlation=("correlation", "median"),
                                      median_halves=("correlation_halves", "median"),
                                      guide_pairs=("correlation", "size")).reset_index())
    per["control_pairs_as_high"] = [float(np.mean(ctrl >= m)) if len(ctrl) and np.isfinite(m) else np.nan
                                    for m in per["median_halves"]]
    # pairs of two targets' guides are set against the control cells as one target's are, so read as they are; each
    # target against the pairs of its own arm, whose guides are set against the same control cells as its own
    two = pairs[(pairs["kind"] == "different targets") & pairs["correlation"].notna()]
    arm = {t: _arm(screen, t) for t in set(two["target"]) | set(two["other_target"]) | set(per["target"])}
    # a boolean array: an empty list selects no columns (one target alone has no pair of two targets' guides)
    two = two[np.array([arm[a] == arm[b] for a, b in zip(two["target"], two["other_target"])], dtype=bool)]
    by_arm = {a: g["correlation"].to_numpy() for a, g in two.groupby(two["target"].map(arm))}
    per["different_pairs_as_high"] = [float(np.mean(by_arm[arm[t]] >= m)) if arm[t] in by_arm and np.isfinite(m)
                                      else np.nan for t, m in zip(per["target"], per["median_correlation"])]
    other = two["correlation"].to_numpy()
    per = per.sort_values("median_correlation", ascending=False).reset_index(drop=True)
    medians = pairs.groupby("kind")["correlation"].median().round(3).to_dict()
    checks = charts.checks(charts.pair_correlations(pairs, path, "Correlation of two guides' profiles, by kind of "
                                                                  "pair"), reference=None)
    beyond = int((per["control_pairs_as_high"] <= 0.05).sum()) if len(ctrl) else None
    words = (f"median correlation of guide pairs: {medians}; " +
             (f"{beyond} of {len(per)} targets' guides correlate more than 95% of the {len(ctrl)} control-guide "
              f"pairs, each side against halves of the control cells" if len(ctrl) else
              "no control-guide pairs for a reference"))
    same_r = same["correlation"].dropna().to_numpy()
    against = None
    if len(same_r) and len(other):
        from scipy import stats
        against = {"same_target_pairs": int(len(same_r)), "different_targets_pairs": int(len(other)),
                   "same_target_median": float(np.median(same_r)), "different_targets_median": float(np.median(other)),
                   "p": float(stats.mannwhitneyu(same_r, other).pvalue),
                   "targets_beyond": int((per["different_pairs_as_high"] <= 0.05).sum())}
        words += (f"; pairs of one target's guides against pairs of two targets' guides: median "
                  f"{against['same_target_median']:.2f} ({len(same_r)} pairs) against "
                  f"{against['different_targets_median']:.2f} ({len(other)}), Mann-Whitney p = {against['p']:.2g}; "
                  f"{against['targets_beyond']} of {len(per)} targets' guides correlate more than 95% of the pairs "
                  "of two targets' guides")
    return Result("measured", per, {"median_by_kind": medians, "control_pairs": int(len(ctrl)),
                                    "targets_beyond_control_pairs": beyond, "against_two_targets": against,
                                    "min_cells": min_cells,
                                    "package_calls": sim.calls,
                                    "recipe_as_run": pt.recipe_as_run(guides_agree_recipe, min_cells=min_cells)},
                  path, checks, words, tables={"guide_pairs": pairs})


def _arm(screen, label):
    """A target's arm (its mode's control cells, where the design matches them); one whose guides differ in a
    matched attribute has no arm and is read against no pairs."""
    try:
        return str(screen.arm_or_every(label))
    except ValueError:
        return f"no one arm: {label}"


def guides_agree_recipe(min_cells=None):
    """F.guides_agree as a block (docs/adr/0031, D2): every two guides' profiles correlated, with pairs of control
    guides of one kind as the reference."""
    min_cells = policy.get("min_cells") if min_cells is None else min_cells
    from .. import blocks as pt
    return pt.similarity(level="guide", min_cells=min_cells)


REGISTRY["F.guides_agree"].recipe = guides_agree_recipe


def replicates_recipe(genes="well_expressed"):
    """F.replicates_agree as blocks (docs/adr/0031, D2): the replicate agreement of the screen's target profiles."""
    from .. import blocks as pt
    agreement = pt.replicate_agreement(genes=genes)
    return agreement


@register("F.replicates_agree", Contract(
    serves="F.replicates_agree",
    applies_when="2 or more samples or lanes",
    needs="labels and samples (mice, donors, lanes or captures), with control cells in each",
    returns="per pair of samples: the correlation (Pearson) of every target's profile against its control cells, over "
            "well-expressed genes (Replogle et al. 2022), with the targets and genes each pair shares and the median "
            "of the targets' own correlations (pairs.tsv); per target and pair: its correlation; whether the samples "
            "are biological replicates or technical ones (lanes, wells or captures of one pool)",
    fails_when=[("targets without an effect pull the screen's r down, and one strong target can carry it",
                 "each target's r beside the screen's; read with C.genes' targets that change genes"),
                ("samples of different libraries share few targets", "the targets each pair shares are stated")],
    tested_against="the synthetic screen: an effect planted in three samples gives a high r, noise alone none",
    choices={"genes": ["well_expressed", "detected", "all"]}))
def replicates_agree(screen, design, out_dir, genes="well_expressed"):
    from .. import blocks as pt
    from . import charts
    with pt.inside(screen, out_dir):
        agreement = replicates_recipe(genes)
    pairs, per_target = agreement.table, agreement.extra.get("per_target", pd.DataFrame())
    path = os.path.join(out_dir, "figure.png")
    kind = "biological replicates" if screen.independent_units() else "technical samples (lanes, wells or captures)"
    if pairs.empty:
        checks = figures.bars(np.zeros(1), ["none"], path, "No two samples share targets", "pairs")
        return Result("not_enough_data", per_target, {"samples": kind}, path, checks,
                      "no two samples share 2 or more targets with 30 cells each beside their control cells")
    units = sorted(set(pairs["unit_a"]) | set(pairs["unit_b"]))
    M = pd.DataFrame(np.eye(len(units)), index=units, columns=units)
    for _, r in pairs.iterrows():
        M.loc[r["unit_a"], r["unit_b"]] = M.loc[r["unit_b"], r["unit_a"]] = r["r"]
    checks = charts.checks(charts.heatmap(M.to_numpy(), units, units, path, "Target profiles, sample against sample",
                                          "Correlation of profiles", cluster=False), reference=None)
    med = float(pairs["r"].median())
    words = (f"the targets' profiles agree between {kind} at a median r of {med:.2f} over {len(pairs)} pairs of "
             f"samples (range {pairs['r'].min():.2f} to {pairs['r'].max():.2f}; each target's own median r "
             f"{float(per_target['r'].median()):.2f})")
    return Result("measured", per_target, {"samples": kind, "median_r": med, "pairs": int(len(pairs)), "genes": genes,
                                           "recipe_as_run": pt.recipe_as_run(replicates_recipe, genes=genes),
                                           "package_calls": agreement.calls},
                  path, checks, words, tables={"pairs": pairs})


REGISTRY["F.replicates_agree"].recipe = replicates_recipe
