"""The similarity step: every two targets' (or guides') profiles correlated, read against pairs of control guides
(docs/adr/0031, phase 2's notes; E.similar_targets, F.guides_agree, Replogle et al. 2022).

The plumbing is here once:
- a group's profile in a sample: the mean log-normalised expression of its cells (a target's labelled cells, or the
  single-target and control cells whose strongest guide a guide is) minus the sample's control cells', over the
  genes well expressed in those control cells (WELL_EXPRESSED UMIs per cell), the targeted genes left out;
- pooled (the default): within each kind of capture, each cell against the control cells of its own sample and
  arm, pooled over the kind's samples, so a group needs min_cells cells in all for a profile, as published screens
  pool a perturbation's cells after normalising each batch to its own control cells (Replogle et al. 2022); by_sample:
  a group needs min_cells cells in a sample for a profile there, two groups are correlated within each sample and the
  correlation averaged over the samples holding both;
- the reference: every two control guides of one kind (one control group and one mode, as a target's guides share a
  vector and a mode), each against the sample's control cells without the two, averaged over the samples holding
  both; each pair's share of control-guide pairs as high. Profiles set against the same control cells share the noise
  of that mean, more the more cells a group has beside them, so the share is read on each pair's correlation against
  halves: each side against one of two disjoint halves of the control cells, the two ways round; the correlation
  against all the control cells is the one reported and drawn;
- the metric: Pearson (the field's), Spearman, or cosine;
- with more than one kind of capture (Screen.capture_kind: the delivery, the batch, the design's other grouping
  factor), each kind's mean and its own control-guide reference beside the mean over samples, and where the kinds
  disagree (kinds_disagree). A correlation is always taken within one sample, so no profile mixes kinds.

    table, matrix, control = pairs(screen, level="target", min_cells=30)
"""
import itertools

import numpy as np
import pandas as pd

from ..most_common import most_common_value
from .profiles import DETECTION, WELL_EXPRESSED  # 0.25 UMIs per control cell (Replogle et al. 2022); 10% detection

METRICS = ("pearson", "spearman", "cosine")
LEVELS = ("target", "guide")
PROFILES = ("pooled", "by_sample")
SPLITS = 16  # the random splits of the control cells into halves the reference averages over
BASE_CELLS = 10  # a sample's control cells (of an arm) needed to set its cells against them, in pooled profiles
COLUMNS = ["group", "other", "target", "other_target", "kind", "correlation", "units", "correlation_halves",
           "control_pairs_as_high", "control_pairs_as_low"]


def similarity(x, y, metric="pearson"):
    """Two profiles' similarity by the metric (NaN where a profile is flat)."""
    from scipy.stats import rankdata
    x, y = np.asarray(x, float), np.asarray(y, float)
    if metric == "spearman":
        x, y = rankdata(x), rankdata(y)
    if metric == "cosine":
        d = np.linalg.norm(x) * np.linalg.norm(y)
        return float(x @ y / d) if d > 0 else np.nan
    with np.errstate(invalid="ignore", divide="ignore"):
        return float(np.corrcoef(x, y)[0, 1])


def masked_genes(screen):
    """The targeted genes: left out, so a guide's change of its own gene does not make a target's guides alike
    (Replogle et al. 2022; Zhu et al. 2026)."""
    own = {screen.own_gene_of(t) for t in screen.targets(1)} - {None}
    return np.isin(screen.genes, list(own))


def gene_mask(screen, control_cells, genes="well_expressed"):
    """The genes a sample's profiles are taken over, chosen on its control cells."""
    idx = np.flatnonzero(control_cells)
    if genes == "well_expressed":
        return np.asarray(screen.X[idx].sum(0)).ravel() >= WELL_EXPRESSED * max(len(idx), 1)
    if genes == "detected":
        return screen.detected(control_cells, DETECTION)
    if genes == "all":
        return np.ones(len(screen.genes), bool)
    raise ValueError(f"genes is well_expressed, detected or all, not {genes!r}")


def control_kind_of(screen, design=None):
    """Each control guide's kind for pairing: the control group the design names it in (perturbation.controls), else
    its cells' control group, and the guide's mode. The cells' group is the guide itself when the target map does not
    list the controls, so the design's group is read first (development session 3: no control pair on CRISPRai)."""
    from ..question_modules.b import modality_of
    from ..question_modules.data import value
    d = design or screen.design
    named = {}
    for c in value(d, "perturbation.controls") or []:
        if isinstance(c, dict):
            for g in c.get("guides") or []:
                named[str(g)] = str(c.get("name") or c.get("kind") or "control")
    ctrl = screen.label == "control"
    group = pd.Series(screen.control_group[ctrl]).groupby(pd.Series(screen.top_guide[ctrl])).agg(
        most_common_value).to_dict()
    return {g: (named.get(str(g), grp), modality_of(d, g)) for g, grp in group.items() if g}


def _guide_arm(screen, g):
    """A guide's arm key: the values of its reference-matching attributes."""
    from ..question_modules.data import key_of
    return key_of([screen.attribute(a).get(g, "") for a in screen.reference_matches])


def pairs(screen, level="target", targets=None, metric="pearson", min_cells=30, mask_targets=True,
          genes="well_expressed", design=None, profile="pooled"):
    """Every two groups' profile similarity, with the control-guide pairs of one kind as the reference: pooled
    profiles compared once per kind of capture, or (by_sample) compared within each sample and averaged over the
    samples holding both. Each pair has two numbers: its correlation (each profile against all the control cells),
    and its correlation against halves (each profile against one of two disjoint halves of the control cells, the
    two ways round), which the reference reads, since profiles set against the same control cells share that mean's
    noise. Returns (table in COLUMNS, matrix of the groups' mean similarity, the control pairs' correlations against
    halves)."""
    if level not in LEVELS:
        raise ValueError(f"level is target or guide, not {level!r}")
    if profile not in PROFILES:
        raise ValueError(f"profile is pooled or by_sample, not {profile!r}")
    if metric not in METRICS:
        raise ValueError(f"metric is one of {list(METRICS)}, not {metric!r}")
    L = screen.lognorm
    masked = masked_genes(screen) if mask_targets else np.zeros(len(screen.genes), bool)
    kind_of = control_kind_of(screen, design)
    targets = list(targets) if targets is not None else screen.targets(min_cells)
    carriers = screen.single() | (screen.label == "control")
    # a guide's target: the label of its single-target (or control) cells, the cells its profile is taken over; a
    # guide whose cells are mostly in pairs keeps its own target, not the pair's label
    label_of = pd.Series(screen.label[carriers]).groupby(pd.Series(screen.top_guide[carriers])).agg(
        most_common_value).to_dict()
    splits = halves(len(screen.label))
    sums, in_kind, sums_h, in_kind_h = {}, {}, {}, {}
    capture_of = screen.kind_of_units()

    def add(a, b, r, k, h=None):
        for got, total, kinds in ((r, sums, in_kind), (h, sums_h, in_kind_h)):
            if got is not None and np.isfinite(got):
                for s in (total.setdefault((a, b), [0.0, 0]), kinds.setdefault((a, b), {}).setdefault(k, [0.0, 0])):
                    s[0] += got
                    s[1] += 1
    every = np.ones(len(screen.label), bool)
    in_arm = (lambda a: screen.in_arm(a)) if screen.reference_matches else (lambda a: every)
    all_arms = sorted({k for s in screen.arms_of_cells for k in s}) if screen.reference_matches else [""]
    arm_of_guide = (lambda g: _guide_arm(screen, g)) if screen.reference_matches else (lambda g: "")
    arm_of_label = (lambda t: screen.arm_or_every(t)) if screen.reference_matches else (lambda t: "")
    units_of_pair = {}  # pooled: the samples holding both groups' cells
    # by sample: each sample's profiles, or each sample and condition's where a sample holds several (Screen.strata)
    by_place = np.asarray(screen.strata)
    unit_of_place = dict(zip(by_place, np.asarray(screen.unit)))
    kept_units = set(screen.units())
    places = (list(screen.units()) if not screen.crossed else
              sorted(x for x in set(by_place) if unit_of_place[x] in kept_units))
    for u in (places if profile == "by_sample" else []):
        k = capture_of.get(unit_of_place.get(u, u), "")
        mr = (screen.label == "control") & (by_place == u)
        if mr.sum() < min_cells:
            continue
        here = carriers & (by_place == u)
        # the arms profiled here: the targets' (or the guides') own; each subtracts its own arm's control cells, and
        # the genes are those every such arm's control cells hold (bug register, BR06, BR04)
        present = ({arm_of_label(t) for t in targets if ((screen.label == t) & (by_place == u)).sum() >= min_cells}
                   if level == "target" else {arm_of_guide(g) for g in set(screen.top_guide[here]) - {""}})
        masks = [mr & in_arm(a) for a in sorted(present)]
        masks = [m for m in masks if m.sum() >= min_cells] or [mr]
        keep_genes = np.logical_and.reduce([gene_mask(screen, m, genes) for m in masks]) & ~masked
        Lu = L[:, np.flatnonzero(keep_genes)]
        if Lu.shape[1] < 2:
            continue

        def summed(mask):
            return np.asarray(Lu[np.flatnonzero(mask)].sum(0)).ravel(), int(mask.sum())
        ctrl_by_arm = {a: summed(mr & in_arm(a)) for a in all_arms + ["every arm"]}
        ctrl_by_arm_h = {a: [[summed(mr & in_arm(a) & (sp == h)) for h in (0, 1)] for sp in splits]
                         for a in all_arms + ["every arm"]}
        sum_of, n_of, sum_of_h = {}, {}, {}
        for g in sorted(set(screen.top_guide[here]) - {""}):  # the control guides, the reference at either level
            mg = here & (screen.top_guide == g)
            if mg.sum() >= min_cells and (level == "guide" or label_of.get(g) == "control"):
                if level == "guide" and label_of.get(g) not in set(targets) | {"control"}:
                    continue
                sum_of[g], n_of[g] = summed(mg)
                sum_of_h[g] = [[summed(mg & (sp == h)) for h in (0, 1)] for sp in splits]
        if level == "target":
            groups = []
            for t in targets:
                mt = (screen.label == t) & (by_place == u)
                if mt.sum() >= min_cells:
                    sum_of[t], n_of[t] = summed(mt)
                    groups.append(t)
        else:
            groups = [g for g in sum_of if label_of.get(g) != "control"]
        controls = [g for g in sum_of if g not in groups and label_of.get(g) == "control"]

        def arm_x(x):
            return arm_of_label(x) if level == "target" and x in targets else arm_of_guide(x)

        def base_of(x):
            s_, n_ = ctrl_by_arm.get(arm_x(x), (None, 0))
            return s_ / n_ if n_ >= min_cells else None

        def bases_of(x):  # each split's two halves' means, where the whole has min_cells cells
            if base_of(x) is None:
                return None
            out = [[s_ / n_ for s_, n_ in hs] for hs in ctrl_by_arm_h[arm_x(x)] if all(n_ for _, n_ in hs)]
            return out or None
        for a, b in itertools.combinations(groups, 2):  # each against its own arm's control cells of the sample
            ba, bb = base_of(a), base_of(b)
            if ba is not None and bb is not None:
                pa, pb = sum_of[a] / n_of[a], sum_of[b] / n_of[b]
                ha, hb = bases_of(a), bases_of(b)
                add(a, b, similarity(pa - ba, pb - bb, metric), k,
                    crossed(pa, pb, ha, hb, metric) if ha and hb else None)
        for a, b in itertools.combinations(controls, 2):  # two of one kind, against the rest of their own arm
            arm = arm_of_guide(a)
            if kind_of.get(a) != kind_of.get(b) or arm != arm_of_guide(b):
                continue
            s_, n_ = ctrl_by_arm.get(arm, (None, 0))
            n = n_ - n_of[a] - n_of[b]
            if n < min_cells:
                continue
            rest = (s_ - sum_of[a] - sum_of[b]) / n
            pa, pb = sum_of[a] / n_of[a], sum_of[b] / n_of[b]
            rest_h = []
            for j in range(len(splits)):
                two = []
                for h in (0, 1):
                    s_h, n_h = ctrl_by_arm_h[arm][j][h]
                    n_h -= sum_of_h[a][j][h][1] + sum_of_h[b][j][h][1]
                    two.append((s_h - sum_of_h[a][j][h][0] - sum_of_h[b][j][h][0]) / n_h if n_h > 0 else None)
                if all(x is not None for x in two):
                    rest_h.append(two)
            add(a, b, similarity(pa - rest, pb - rest, metric), k,
                crossed(pa, pb, rest_h, rest_h, metric) if rest_h else None)
    if profile == "pooled":
        _pool(screen, L, level, targets, metric, min_cells, genes, masked, kind_of, carriers, label_of, capture_of,
              in_arm, all_arms, arm_of_guide, arm_of_label, add, units_of_pair, splits)

    def arm_of(x):
        return arm_of_label(x) if level == "target" and x in targets else arm_of_guide(x)
    rows, row_arms = [], []
    for (a, b), (total, n) in sums.items():
        la = a if level == "target" and a in targets else label_of.get(a, "")
        lb = b if level == "target" and b in targets else label_of.get(b, "")
        kind = ("control guides" if la == lb == "control" else "same target" if la == lb else "different targets")
        th, nh = sums_h.get((a, b), (np.nan, 1))
        rows.append({"group": a, "other": b, "target": la, "other_target": lb, "kind": kind,
                     "correlation": total / n, "units": units_of_pair.get((a, b), n) if profile == "pooled" else n,
                     "correlation_halves": th / nh})
        row_arms.append({arm_of(a), arm_of(b)})
    table = pd.DataFrame(rows, columns=COLUMNS[:-2])
    is_control = (table["kind"] == "control guides").to_numpy()
    control = table.loc[is_control, "correlation_halves"].to_numpy(dtype=float)
    control = control[np.isfinite(control)]
    # a pair is read against the control-guide pairs of its own arms (a control pair lies within one arm; BR06), each
    # side against halves of the control cells
    control_of = {}
    for r, arms_, c in zip(table["correlation_halves"], row_arms, is_control):
        if c and np.isfinite(r):
            control_of.setdefault(next(iter(arms_)), []).append(r)

    def control_in(arms_):  # a combination of two arms ('every arm'): every arm's control pairs
        arms_ = set(control_of) if "every arm" in arms_ else arms_
        return np.array([x for a in sorted(arms_) for x in control_of.get(a, [])], dtype=float)
    for side, at_least in (("high", True), ("low", False)):  # alike, and opposite: the share of control pairs as far
        table[f"control_pairs_as_{side}"] = [
            float(np.mean(control_in(arms_) >= c if at_least else control_in(arms_) <= c))
            if len(control_in(arms_)) and not ctl and np.isfinite(c) else np.nan
            for c, arms_, ctl in zip(table["correlation_halves"], row_arms, is_control)]
    kinds = sorted({k for v in in_kind.values() for k in v})
    if len(kinds) > 1:  # each kind of capture's mean and reference beside the mean over samples, and where they differ
        from .profiles import kinds_disagree
        mean_in = [{k: s / n for k, (s, n) in in_kind[key].items()} for key in sums]
        halves_in = [{k: s / n for k, (s, n) in in_kind_h.get(key, {}).items()} for key in sums]
        reference_in = {k: [(m[k], arms_) for m, c, arms_ in zip(halves_in, is_control, row_arms) if c and k in m]
                        for k in kinds}

        def reference_of(k, arms_):
            return np.array([x for x, a_ in reference_in[k] if a_ & arms_ or "every arm" in arms_], dtype=float)
        high_in = [{k: float(np.mean(reference_of(k, arms_) >= r)) for k, r in m.items()
                    if len(reference_of(k, arms_)) and not c} for m, c, arms_ in zip(halves_in, is_control, row_arms)]
        for k in kinds:
            table[f"correlation: {k}"] = [m.get(k, np.nan) for m in mean_in]
            table[f"control_pairs_as_high: {k}"] = [h.get(k, np.nan) for h in high_in]
        table["kinds_disagree"] = ["" if c else kinds_disagree(m, {k: x <= 0.05 for k, x in h.items()})
                                   for m, h, c in zip(mean_in, high_in, is_control)]
    shown = table[table["kind"] != "control guides"]
    names = sorted(set(shown["group"]) | set(shown["other"]))
    matrix = pd.DataFrame(np.nan, index=names, columns=names)
    for a, b, r in zip(shown["group"], shown["other"], shown["correlation"]):
        matrix.at[a, b] = matrix.at[b, a] = r
    return table, matrix, control


def halves(n, splits=None, seed=0):
    """Each cell's half (0 or 1) in each of `splits` (SPLITS) fixed random splits: (splits, n). One split's two halves
    differ by chance, by an amount every pair compared across them shares; over many splits that part averages out."""
    return np.random.default_rng(seed).integers(0, 2, (splits or SPLITS, n))


def crossed(pa, pb, ha, hb, metric):
    """Two profiles' similarity with each set against a different half of the control cells, the two ways round
    (pa against half 0 with pb against half 1, and the reverse), averaged over the splits: no control cell is in
    both baselines. ha, hb: per split, the two halves' means."""
    r = [similarity(pa - x[0], pb - y[1], metric) for x, y in zip(ha, hb)]
    r += [similarity(pa - x[1], pb - y[0], metric) for x, y in zip(ha, hb)]
    r = [x for x in r if np.isfinite(x)]
    return float(np.mean(r)) if r else np.nan


def _pool(screen, L, level, targets, metric, min_cells, genes, masked, kind_of, carriers, label_of, capture_of,
          in_arm, all_arms, arm_of_guide, arm_of_label, add, units_of_pair, splits):
    """The pooled profiles: within each kind of capture, each group's cells against the control cells of their own
    sample and arm (a sample with BASE_CELLS of them), pooled over the kind's samples; a group with min_cells cells
    in all has a profile, over the genes well expressed in the kind's control cells. Two groups are compared once
    per kind; two control guides of one kind, each against the rest of its arm's control cells in each sample. Each
    comparison is made again with the two sides set against disjoint halves of those control cells, in each
    split."""
    units = list(screen.units())
    unit = np.asarray(screen.unit)
    # the cells are summed by sample, or by sample and condition where a sample holds several (Screen.strata): a
    # group's deviation is then from the control cells of its own condition, weighted by its cells there
    stratum = np.asarray(screen.strata)
    unit_of = dict(zip(stratum, unit))
    control = screen.label == "control"
    base_min = min(min_cells, BASE_CELLS)
    arm_mask = {a: in_arm(a) for a in all_arms + ["every arm"]}
    for k in sorted({capture_of.get(u, "") for u in units}):
        us = [u for u in units if capture_of.get(u, "") == k]
        in_k = np.isin(unit, us)
        mr_k = control & in_k
        if mr_k.sum() < min_cells:
            continue
        here_k = carriers & in_k
        present = ({arm_of_label(t) for t in targets if ((screen.label == t) & in_k).sum() >= min_cells}
                   if level == "target" else {arm_of_guide(g) for g in set(screen.top_guide[here_k]) - {""}})
        masks = [mr_k & arm_mask.get(a, in_arm(a)) for a in sorted(present)]
        masks = [m for m in masks if m.sum() >= min_cells] or [mr_k]
        keep_genes = np.logical_and.reduce([gene_mask(screen, m, genes) for m in masks]) & ~masked
        Lk = L[:, np.flatnonzero(keep_genes)]
        if Lk.shape[1] < 2:
            continue
        # the groups' cells in this kind: a target's labelled cells, or the single-target cells whose strongest guide
        # a guide is; the control guides' cells apart, the reference
        if level == "target":
            member = in_k & np.isin(screen.label, list(targets))
            group_of = np.asarray(screen.label)
            arm_x = arm_of_label
        else:
            wanted = {g for g in set(screen.top_guide[here_k]) - {""}
                      if label_of.get(g) in set(targets) and label_of.get(g) != "control"}
            member = here_k & np.isin(screen.top_guide, list(wanted))
            group_of = np.asarray(screen.top_guide)
            arm_x = arm_of_guide
        ctrl_guides = sorted(g for g in set(screen.top_guide[here_k]) - {""} if label_of.get(g) == "control")

        def sums_by(mask, keys=None):
            """{(sample, key) or sample: (the cells' summed expression, their number)} of the cells in mask."""
            idx = np.flatnonzero(mask)
            by = stratum[idx] if keys is None else [stratum[idx], keys[idx]]
            return {key: (np.asarray(Lk[idx[np.asarray(rows)]].sum(0)).ravel(), len(rows))
                    for key, rows in pd.Series(np.arange(len(idx))).groupby(by).groups.items()}
        groups_sum = sums_by(member, group_of)
        top = np.asarray(screen.top_guide)
        cg_mask = here_k & np.isin(screen.top_guide, ctrl_guides)
        cg_sum = sums_by(cg_mask, top)
        cg_sum_h = [[sums_by(cg_mask & (sp == h), top) for h in (0, 1)] for sp in splits]
        ctrl_sum, ctrl_sum_h = {}, [[{}, {}] for _ in splits]
        for a, m_a in arm_mask.items():
            for u, v in sums_by(mr_k & m_a).items():
                ctrl_sum[(u, a)] = v
            for j, sp in enumerate(splits):
                for h in (0, 1):
                    for u, v in sums_by(mr_k & m_a & (sp == h)).items():
                        ctrl_sum_h[j][h][(u, a)] = v
        dev, dev_h, cells_of, where = {}, {}, {}, {}
        for (u, x), (s_x, n_x) in groups_sum.items():
            s_c, n_c = ctrl_sum.get((u, arm_x(x)), (None, 0))
            if n_c >= base_min:
                dev[x] = dev.get(x, 0.0) + (s_x - n_x * s_c / n_c)
                cells_of[x] = cells_of.get(x, 0) + n_x
                where.setdefault(x, set()).add(unit_of[u])
                d_h = dev_h.setdefault(x, [[0.0, 0.0] for _ in splits])
                for j in range(len(splits)):
                    for h in (0, 1):
                        s_h, n_h = ctrl_sum_h[j][h].get((u, arm_x(x)), (s_c, n_c))
                        d_h[j][h] = d_h[j][h] + (s_x - n_x * s_h / max(n_h, 1))
        groups = sorted(x for x in dev if cells_of[x] >= min_cells)
        prof = {x: dev[x] / cells_of[x] for x in groups}
        prof_h = {x: [[d / cells_of[x] for d in two] for two in dev_h[x]] for x in groups}
        for a, b in itertools.combinations(groups, 2):
            r_h = [similarity(pa[0], pb[1], metric) for pa, pb in zip(prof_h[a], prof_h[b])]
            r_h += [similarity(pa[1], pb[0], metric) for pa, pb in zip(prof_h[a], prof_h[b])]
            r_h = [x for x in r_h if np.isfinite(x)]
            add(a, b, similarity(prof[a], prof[b], metric), k, float(np.mean(r_h)) if r_h else None)
            units_of_pair[(a, b)] = units_of_pair.get((a, b), 0) + len(where[a] & where[b])
        for a, b in itertools.combinations(ctrl_guides, 2):  # two of one kind, against the rest of their own arm
            arm = arm_of_guide(a)
            if kind_of.get(a) != kind_of.get(b) or arm != arm_of_guide(b):
                continue
            da, db, na, nb, both = 0.0, 0.0, 0, 0, set()
            da_h, db_h = [[0.0, 0.0] for _ in splits], [[0.0, 0.0] for _ in splits]
            for u in (us if not screen.crossed else sorted(set(stratum[in_k]))):
                s_c, n_c = ctrl_sum.get((u, arm), (0.0, 0))
                sa, ka = cg_sum.get((u, a), (0.0, 0))
                sb, kb = cg_sum.get((u, b), (0.0, 0))
                n = n_c - ka - kb
                if (ka or kb) and n >= base_min:
                    rest = (s_c - sa - sb) / n
                    da, na = da + (sa - ka * rest), na + ka
                    db, nb = db + (sb - kb * rest), nb + kb
                    if ka and kb:  # the samples holding both
                        both.add(unit_of[u])
                    for j in range(len(splits)):
                        for h in (0, 1):
                            s_h, n_h = ctrl_sum_h[j][h].get((u, arm), (0.0, 0))
                            sa_h, ka_h = cg_sum_h[j][h].get((u, a), (0.0, 0))
                            sb_h, kb_h = cg_sum_h[j][h].get((u, b), (0.0, 0))
                            m = n_h - ka_h - kb_h
                            rest_h = (s_h - sa_h - sb_h) / m if m > 0 else rest
                            da_h[j][h] = da_h[j][h] + (sa - ka * rest_h)
                            db_h[j][h] = db_h[j][h] + (sb - kb * rest_h)
            if na >= min_cells and nb >= min_cells:
                r_h = [similarity(x[0] / na, y[1] / nb, metric) for x, y in zip(da_h, db_h)]
                r_h += [similarity(x[1] / na, y[0] / nb, metric) for x, y in zip(da_h, db_h)]
                r_h = [x for x in r_h if np.isfinite(x)]
                add(a, b, similarity(da / na, db / nb, metric), k, float(np.mean(r_h)) if r_h else None)
                units_of_pair[(a, b)] = units_of_pair.get((a, b), 0) + len(both)
