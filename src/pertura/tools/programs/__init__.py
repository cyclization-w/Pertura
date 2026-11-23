"""Program tools: tests of per-cell program usage (pertura.programs) for each group and each construct against the
reference. Usage comes from the program set named in the specification, fitted once without labels.

- usage_paired (unit pseudobulk:<unit>): mean usage per unit for the group and for the reference, over units with at
  least min_cells cells in both; effect = mean over those units of (group - reference); p from a paired t-test across
  them (at least 3 units).
- usage_wilcoxon (unit cells): Wilcoxon rank-sum test of per-cell usage, group against reference (the statistic of
  the gene-level Wilcoxon tool); effect = difference in mean usage.

Rows: feature = program name; effect in usage units (a cell's usage sums to 1 over programs).
"""
import numpy as np
import pandas as pd
import scipy.sparse as sp
from scipy import stats

from ..composition import paired_t
from ..de.wilcoxon import SortedReference

MIN_UNITS = 3


def unit_means(U, pos, units, min_cells):
    """{unit: mean usage vector} over the given cells, for units with at least min_cells of them."""
    u = units[pos]
    out = {}
    for unit in np.unique(u):
        sel = pos[u == unit]
        if len(sel) >= min_cells:
            out[unit] = U[sel].mean(0)
    return out


def _items(gs):
    return [(g, "all", pos) for g, pos in gs.groups.items()] + \
           [(gs.home[c], c, pos) for c, pos in gs.constructs.items()]


def _table(rows):
    cols = ["group", "construct", "feature", "effect", "p"]
    return pd.concat(rows, ignore_index=True)[cols] if rows else pd.DataFrame(columns=cols)


class UsagePaired:
    name = "usage_paired"
    claim_type = "programs"
    unit_kind = "pseudobulk"
    environment = "python"
    packages = ("numpy", "scipy", "scikit-learn")
    version = "usage_paired/2"

    def run(self, spec, data, gs, workdir):
        U, names = data.program_usage(spec["program_set"])
        units = data.obs[data.design.unit_col].astype(str).to_numpy()
        min_cells = int(spec["min_cells"])
        ref = unit_means(U, gs.reference, units, min_cells)
        rows = []
        for g, c, pos in _items(gs):
            gm = unit_means(U, pos, units, min_cells)
            shared = sorted(set(gm) & set(ref))
            if len(shared) < MIN_UNITS:
                continue
            eff, p = paired_t(np.array([gm[u] - ref[u] for u in shared], dtype=np.float64))
            rows.append(pd.DataFrame({"group": g, "construct": c, "feature": names, "effect": eff, "p": p}))
        return _table(rows)


class UsageWilcoxon:
    name = "usage_wilcoxon"
    claim_type = "programs"
    unit_kind = "cells"
    environment = "python"
    packages = ("numpy", "scipy", "scikit-learn")
    version = "usage_wilcoxon/1"

    def run(self, spec, data, gs, workdir):
        U, names = data.program_usage(spec["program_set"])
        ref = SortedReference(U[gs.reference])
        rows = []
        for g, c, pos in _items(gs):
            z, p, mean = ref.test(sp.csr_matrix(U[pos]))
            rows.append(pd.DataFrame({"group": g, "construct": c, "feature": names, "effect": mean - ref.mean,
                                      "p": p}))
        return _table(rows)
