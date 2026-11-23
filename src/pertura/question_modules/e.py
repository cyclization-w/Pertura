"""Question E: how perturbations relate: similar targets, opposite modes of one gene, combinations, and the
perturbation's dependence on a condition."""
import itertools
import os

import numpy as np

from .. import candidates, policy
import pandas as pd
import scipy.sparse as sp
from scipy import stats

from ..most_common import most_common_value
from . import figures
from .c import BIOLOGY_LINES, Q, bh, scanpy_scores
from .contract import REGISTRY, Contract, Result, register
from .data import value

PROFILE_CELLS = 30
DETECTION = 0.1
MAP_ROWS, MAP_COLS = 40, 30


_short = figures.short_name


def _units(screen):
    return screen.units()


def unit_profiles(screen, units, min_cells=None, labels=None, min_reference=None):
    """Per unit: the genes detected in DETECTION of its control cells, and each label's profile there (its cells'
    mean log-normalised expression minus its reference cells': the control cells, its arm's where the design matches
    references), for labels with min_cells cells in the unit: the profiles step (steps.profiles)."""
    min_cells = policy.get("min_cells_profile") if min_cells is None else min_cells
    from ..steps import profiles
    out = {}
    for u in units:
        here = screen.unit == u
        names = labels if labels is not None else sorted(set(screen.label[here]) - {"control", "none", "multi"})
        by = profiles.profiles(screen, names, [u], genes="detected", min_cells=min_cells, min_reference=min_reference)
        out[u] = {lab: row for lab, row in by[u].iterrows()} if u in by else {}
    return out


def label_gene_and_mode(screen, design):
    """Each label's gene and mode, from its cells' most common guide: the guide's gene (the guide table's, where the
    design gives it, else its target) and its mode (perturbation.guides)."""
    from .b import expected_sign
    target_of = dict(zip(screen.guides["guide"].astype(str), screen.guides["target"].astype(str)))
    gene_of = {g: v for g, v in screen.attribute("gene").items() if v}
    element_of = screen.attribute("element")
    out = {}
    for lab in screen.targets(1):
        g = pd.Series(screen.top_guide[screen.label == lab])
        g = g[g != ""]
        if len(g):
            top = most_common_value(g)
            gene = gene_of.get(top, target_of.get(top, lab))
            if element_of.get(top):  # a promoter and an enhancer of one gene stay apart
                gene = f"{gene} ({element_of[top]})"
            out[lab] = (gene, expected_sign(design, top))
    return out


# ---- similar targets -----------------------------------------------------------------------------------------------
@register("E.similar_targets", Contract(
    serves="E.similar_targets",
    applies_when="3 or more targets",
    needs="counts, labels, units, the control cells",
    returns="the correlation between every two targets' profiles over well-expressed genes, the targeted genes "
            "left out, each cell against its own unit's control cells: pooled over the units of one kind of capture "
            "(the default, as published screens correlate mean profiles normalised to each batch's control cells), "
            "or (profile by_sample) correlated within each unit and averaged over the units holding both; the same "
            "for every two control guides of one kind as the reference; per pair, the share of "
            "control-guide pairs as high; per target, its most similar targets; with more than one kind of capture "
            "(the delivery, the batch, the design's other grouping factor), each kind's correlation and reference "
            "beside them, and where the kinds disagree; the pairs with opposite effects (correlated as low as at most "
            "5% of the control-guide pairs); with de, the genes each pair past a line changes both (the same way for "
            "pairs alike, opposite ways for opposite pairs); the pairs within one GO cellular component (a complex) "
            "against the other pairs, as published screens check their groups against known complexes",
    fails_when=[("a response many targets share makes them look alike",
                 "D.common_response: which targets still differ from the other targets' cells"),
                ("a target's change of its target gene makes it look like targets that change that gene",
                 "the targeted genes are left out (Replogle et al. 2022)"),
                ("targets of few cells have noisy profiles that correlate by chance",
                 "a profile needs min_cells cells (pooled over the units, or in one unit by_sample); pairs of "
                 "control guides as the reference"),
                ("profiles set against the same control cells share that mean's noise, the more the more cells a target has",
                 "the reference reads each pair with its two sides set against disjoint halves of the control cells, averaged over 16 random splits; the correlation against all of them is the one reported")],
    tested_against="the synthetic screen: target B's most similar targets found, and target pairs read against the "
                   "pairs of control guides (tests/test_question_modules.py)",
    choices={"min_cells": [30, 10], "profile": ["pooled", "by_sample"]},
    inputs={"de": "a C.genes run (its ID, or <run>:changed_genes; the run's own test of every gene is read): the "
                  "genes each pair past a line changes both"}))
def similar_targets(screen, design, out_dir, min_cells=None, profile="pooled", de=None):
    """The targets' profiles correlated (the similarity step), ordered by average-linkage clustering, each pair read
    against the control-guide pairs."""
    min_cells = policy.get("min_cells_profile") if min_cells is None else min_cells
    from .. import blocks as pt
    from . import charts
    from scipy.cluster.hierarchy import leaves_list, linkage
    with pt.inside(screen, out_dir):
        sim = similar_targets_recipe(min_cells, profile)
    path = os.path.join(out_dir, "figure.png")
    t = sim.table[sim.table["kind"] != "control guides"]
    where = "in all, pooled over the units" if profile == "pooled" else "in a unit"
    if t.empty or len(sim.extra["matrix"]) < 3:
        checks = figures.bars(np.zeros(1), ["none"], path, "Fewer than 3 targets with enough cells", "cells")
        return Result("not_enough_data", pd.DataFrame(), {}, path, checks,
                      f"fewer than 3 targets with {min_cells} cells {where} beside their control cells")
    C = sim.extra["matrix"]
    filled = C.fillna(0.0).to_numpy(copy=True)
    np.fill_diagonal(filled, 1.0)
    order = C.index[leaves_list(linkage(filled, "average"))] if len(C) > 2 else C.index
    C = C.loc[order, order]
    by_kind = [c for c in t.columns if c.startswith(("correlation: ", "control_pairs_as_high: "))]
    by_kind += ["kinds_disagree"] if "kinds_disagree" in t else []  # more than one kind of capture
    table = (t[["group", "other", "correlation", "correlation_halves", "control_pairs_as_high",
                "control_pairs_as_low", *by_kind]]
             .rename(columns={"group": "target"}).sort_values("correlation", ascending=False).reset_index(drop=True))
    ref = np.asarray(sim.extra["control_pairs"], dtype=float)
    # the sets of targets alike, each with the targets opposite it, named in the lines and boxed in the figure
    sets = (candidates.apart(candidates.sets_alike(table, reference=float(np.quantile(ref, 0.95)),
                                                    low=float(np.quantile(ref, 0.05)))["sets"]) if len(ref) else [])
    nearest = {x: table[(table["target"] == x) | (table["other"] == x)].head(3).apply(
        lambda r: r["other"] if r["target"] == x else r["target"], axis=1).tolist() for x in C.index}
    side = pd.DataFrame({"Cells": [int((screen.label == x).sum()) for x in C.index]}, index=C.index)
    checks = charts.checks(charts.target_panel(C, side, path, "Targets' profiles correlated",
                                               label="Correlation of two targets' profiles",
                                               order=candidates.leaf_order(table) if sets else None,
                                               boxes=[s["targets"] for s in sets]), zero=True)
    about = {"most_similar": nearest, "units": int(t["units"].max()), "profile": profile, "package_calls": sim.calls,
             "recipe_as_run": pt.recipe_as_run(similar_targets_recipe, min_cells=min_cells, profile=profile)}
    kinds = ""
    if "kinds_disagree" in table:
        disagree = table.loc[table["kinds_disagree"] != "", ["target", "other"]].agg(" / ".join, axis=1).tolist()
        about["kinds_disagree"] = disagree
        kinds = (f"; the kinds of capture disagree for {len(disagree)} pairs" if disagree
                 else "; the kinds of capture agree")
    if not len(ref):
        return Result("measured", table, {**about, "control_pairs": 0}, path, checks,
                      f"{len(table)} target pairs; no pairs of control guides with {min_cells} cells {where} for a "
                      f"reference{kinds}")
    beyond = int((table["control_pairs_as_high"] <= 0.05).sum())
    opposite = int(((table["control_pairs_as_low"] <= 0.05) & (table["correlation"] < 0)).sum())
    tables, shared = {}, {}
    if de is not None:
        tables["shared_genes"] = shared_genes(table, pd.read_csv(de, sep="\t") if isinstance(de, str) else de)
        shared = {(r.target, r.other): r.genes for r in tables["shared_genes"].itertuples()}
    complexes, said = within_complexes(screen, design, table)
    if len(complexes):
        tables["complexes"] = complexes
    return Result("detected" if beyond or opposite else "not_detected", table,
                  {**about, "control_pairs": int(len(ref)),
                   "control_pairs_95th_percentile": float(np.quantile(ref, 0.95)),
                   "control_pairs_5th_percentile": float(np.quantile(ref, 0.05))}, path, checks,
                  f"{beyond} target pairs correlate more than 95% of the {len(ref)} pairs of control guides "
                  f"(above {np.quantile(ref, 0.95):.2f}, each side against halves of the control cells), and "
                  f"{opposite} less than 95% of them (below {np.quantile(ref, 0.05):.2f}, opposite effects){kinds}"
                  + (f"; {len(sets)} sets of targets alike, boxed" if sets else "") + said,
                  biology=similar_biology(table, shared, sets=sets), tables=tables)


def _set_line(s):
    """A set of targets alike (candidates.sets_alike) in one line: its targets, how alike, the targets opposite it."""
    opposite = ", ".join(f"{o['target']} {o['mean_correlation']:.2f}" for o in s.get("opposite") or [])
    return (f"Alike: {', '.join(s['targets'])} (mean r {s['mean_correlation']:.2f}; "
            f"{s['pairs_beyond_the_control_pairs']} of {s['pairs']} pairs past the control pairs)"
            + (f"; opposite them: {opposite}" if opposite else "; none opposite them past the control pairs"))


def similar_biology(table, shared=None, most=BIOLOGY_LINES, sets=()):
    """The sets of targets alike first, each with the targets opposite it (sets: candidates.sets_alike's, as
    candidates.apart keeps them); then the target pairs past the control-guide pairs' lines, alike (the most
    correlated first) and then opposite (the least first), each with its correlation and, where a genes run was
    given, the genes it changes both."""
    head = [_set_line(s) for s in sets][:most]
    most -= len(head)
    shared = shared or {}
    alike = table[table["control_pairs_as_high"] <= 0.05].sort_values("correlation", ascending=False)
    opposite = table.iloc[:0]
    if "control_pairs_as_low" in table:
        opposite = table[(table["control_pairs_as_low"] <= 0.05) & (table["correlation"] < 0)]
        opposite = opposite.sort_values("correlation")

    def line(r, way):
        genes = shared.get((r["target"], r["other"]))
        return (f"{r['target']} and {r['other']}: r {r['correlation']:.2f}{way}"
                + (f"; both change {genes}" if genes else ""))
    # the opposite pairs keep up to half the lines beside the alike ones (docs/adr/0045, D5): with many pairs alike,
    # GSE342764's 1,155, the opposite ones had none
    lines = [line(r, "") for r in alike.head(most - min(len(opposite), most // 2)).to_dict("records")]
    lines += [line(r, ", opposite") for r in opposite.head(max(0, most - len(lines))).to_dict("records")]
    left = len(alike) + len(opposite) - len(lines)
    if left > 0:
        lines.append(f"{left} more pairs past the lines: the result table holds them")
    return head + lines


def shared_genes(table, de, level=0.05, shown=8):
    """For each pair past a line, the genes both targets change at q < level: the same way for pairs alike, opposite
    ways for opposite pairs, so why two targets look alike is read in their changed genes. de: target, gene, effect,
    q."""
    hit = de[de["q"] < level]
    sign = {str(t): g.set_index(g["gene"].astype(str))["effect"].apply(np.sign) for t, g in hit.groupby("target")}
    rows = []
    for r in table.to_dict("records"):
        alike = r["control_pairs_as_high"] <= 0.05
        opposite = r.get("control_pairs_as_low", 1) <= 0.05 and r["correlation"] < 0
        a, b = sign.get(str(r["target"])), sign.get(str(r["other"]))
        if not (alike or opposite) or a is None or b is None:
            continue
        keep = [g for g in a.index.intersection(b.index) if (a[g] == b[g]) == bool(alike)]
        more = f" (+{len(keep) - shown})" if len(keep) > shown else ""
        rows.append({"target": r["target"], "other": r["other"], "way": "alike" if alike else "opposite",
                     "n": len(keep), "genes": ", ".join(keep[:shown]) + more})
    return pd.DataFrame(rows, columns=["target", "other", "way", "n", "genes"])


def within_complexes(screen, design, table):
    """The pairs whose two target genes share a GO cellular component (a known complex, as MSigDB holds them) against
    the other pairs: their correlations (Mann-Whitney U, one-sided) and the share past the line, as published screens
    check their groups against known complexes. Returns (the components holding two targets or more, the words)."""
    from .. import gene_sets
    organism = value(design, "experiment.organism")
    if organism not in ("human", "mouse"):
        return pd.DataFrame(), ""
    try:
        cc = gene_sets.msigdb("go_cc", organism)
    except (ValueError, OSError):  # a folder without the GO cellular components: no complexes read
        return pd.DataFrame(), ""
    gene_of = {t: screen.own_gene_of(t) for t in set(table["target"]) | set(table["other"])}
    members = {}
    for name, genes in cc.items():
        inside = sorted(t for t, g in gene_of.items() if g and g in set(genes))
        if 2 <= len(inside) <= 30:  # a component holding most targets is a compartment, not a complex
            members[name] = inside
    if not members:
        return pd.DataFrame(), ""
    together = {frozenset(p) for m in members.values() for p in itertools.combinations(m, 2)}
    within = table.apply(lambda r: frozenset((r["target"], r["other"])) in together, axis=1)
    rows = [{"complex": n, "targets": ", ".join(m), "pairs": len(m) * (len(m) - 1) // 2} for n, m in members.items()]
    a, b = table.loc[within, "correlation"].dropna(), table.loc[~within, "correlation"].dropna()
    if not len(a) or not len(b):
        return pd.DataFrame(rows), ""
    p = float(stats.mannwhitneyu(a, b, alternative="greater").pvalue)
    past_in = float((table.loc[within, "control_pairs_as_high"] <= 0.05).mean())
    past_out = float((table.loc[~within, "control_pairs_as_high"] <= 0.05).mean())
    return pd.DataFrame(rows), (f"; the {len(a)} pairs within one GO cellular component correlate at a median r of "
                                f"{a.median():.2f} against {b.median():.2f} for the other {len(b)} pairs (Mann-Whitney "
                                f"U, one-sided p {p:.2g}; {100 * past_in:.0f}% against {100 * past_out:.0f}% past the "
                                "line; prior knowledge)")


def similar_targets_recipe(min_cells=None, profile="pooled"):
    """E.similar_targets as a block (docs/adr/0031, D2): every two targets' profiles correlated, with pairs of
    control guides of one kind as the reference."""
    min_cells = policy.get("min_cells_profile") if min_cells is None else min_cells
    from .. import blocks as pt
    return pt.similarity(level="target", min_cells=min_cells, profile=profile)


REGISTRY["E.similar_targets"].recipe = similar_targets_recipe


# ---- opposite modes ------------------------------------------------------------------------------------------------
@register("E.opposite_modes", Contract(
    serves="E.opposite_modes",
    applies_when="CRISPRa and CRISPRi of the same genes in one library",
    needs="labels, each guide's mode (perturbation.guide_modes), units, the control cells",
    returns="per activation label and inhibition label of the same gene (a promoter and an enhancer label of one gene "
            "stay apart): the correlation of the two profiles, each against its unit's control cells and pooled "
            "only within one kind of capture (the delivery, the batch and the design's other grouping factor), "
            "weighted by cells, the targeted genes left out, over all genes and over the 200 genes changing most; "
            "with more than one kind, the cells-weighted mean of the kinds' correlations, each kind's beside it "
            "and where they disagree (kinds_disagree); against it, the same correlation for activation and "
            "inhibition of different genes (the null, built alike), and per pair the share of those as low",
    fails_when=[("activation and inhibition in different batches differ by batch, not by mode",
                 "profiles pooled only within one kind of capture, each kind's value beside the pooled one; the "
                 "null of different genes built alike"),
                ("a mode that does not work gives no profile to oppose", "B.efficacy for both modes")],
    tested_against="the synthetic mixed library: activation and inhibition of one gene planted with opposite effects "
                   "lie below every pair of different genes; a gene with no effect does not "
                   "(tests/test_common_response_and_field_measures.py)",
    choices={"top": [200, 500]},
    inputs={}))
def opposite_modes(screen, design, out_dir, top=200):
    from ..steps import profiles as steps
    gm = label_gene_and_mode(screen, design)
    labels = [lab for lab, (_, mode) in gm.items() if mode != 0]
    from ..steps.similarity import BASE_CELLS
    # each unit's cells against its own unit's control cells (BASE_CELLS of them), whatever the label's cells there;
    # a label's cells are counted where they are pooled (Pertura's min_cells_profile)
    prof = unit_profiles(screen, _units(screen), min_cells=1, labels=labels, min_reference=BASE_CELLS)
    cells = {(u, lab): int(((screen.label == lab) & (screen.unit == u)).sum()) for u, p in prof.items() for lab in p}
    # pooled within one kind of capture, weighted by cells; across kinds only where no kind holds both labels
    pooled, n_of = steps.pooled_within_kinds(prof, cells, screen.kind_of_units())
    everywhere, n_all = steps.pooled_within_kinds(prof, cells, {u: steps.ACROSS for u in prof})
    least = policy.get("min_cells_profile")
    pooled = {k: {lab: v for lab, v in p.items() if n_of[(k, lab)] >= least} for k, p in pooled.items()}
    everywhere = {k: {lab: v for lab, v in p.items() if n_all[(k, lab)] >= least} for k, p in everywhere.items()}
    prof = {u: {lab: v for lab, v in p.items() if any(lab in q for q in pooled.values()) or
                any(lab in q for q in everywhere.values())} for u, p in prof.items()}
    kinds = sorted(pooled)
    several = len(kinds) > 1
    act = sorted({lab for p in prof.values() for lab in p if gm[lab][1] > 0})
    inh = sorted({lab for p in prof.values() for lab in p if gm[lab][1] < 0})

    from .f import masked_genes
    own = set(screen.genes[masked_genes(screen)])  # a gene's own rise and fall would make every pair look opposite

    def correlated(x, y):
        common = x.index.intersection(y.index)
        common = common[~common.isin(own)]  # the targeted genes left out (Replogle et al. 2022)
        x, y = x[common], y[common]
        t = (x.abs() + y.abs()).sort_values(ascending=False).index[:top]
        return float(np.corrcoef(x, y)[0, 1]), float(np.corrcoef(x[t], y[t])[0, 1]), x, y, t, len(common)

    def compared(a, i):
        """{kind: (r over all genes, r over the top genes, x, y, the top genes, genes, cells)} for the kinds holding
        both labels; where none does, one comparison across kinds."""
        per = {k: (*correlated(pooled[k][a], pooled[k][i]), n_of[(k, a)] + n_of[(k, i)])
               for k in kinds if a in pooled[k] and i in pooled[k]}
        if not per:
            per[steps.ACROSS] = (*correlated(everywhere[steps.ACROSS][a], everywhere[steps.ACROSS][i]),
                                 n_all[(steps.ACROSS, a)] + n_all[(steps.ACROSS, i)])
        return per

    def combined(per, j):
        """The cells-weighted mean over the kinds of one value (j 0: over all genes; 1: over the top genes)."""
        x = np.array([v[j] for v in per.values()], dtype=float)
        w = np.array([v[6] for v in per.values()], dtype=float)
        ok = np.isfinite(x)
        return float(x[ok] @ w[ok] / w[ok].sum()) if ok.any() else np.nan

    def as_low(null_values, r):
        return float(np.mean(null_values <= r)) if len(null_values) and np.isfinite(r) else np.nan

    # the null: activation and inhibition of different genes, profiled and correlated the same way, kind by kind
    null, null_in = [], {}
    for a, i in itertools.product(act, inh):
        if gm[a][0] != gm[i][0]:
            per = compared(a, i)
            null.append(combined(per, 1))
            for k, v in per.items():
                null_in.setdefault(k, []).append(v[1])
    null = np.asarray(null, dtype=float)
    null = null[np.isfinite(null)]
    null_in = {k: np.asarray([x for x in v if np.isfinite(x)]) for k, v in null_in.items()}
    rows, kept = [], []
    units_used = _units(screen)
    for a, i in itertools.product(act, inh):
        if gm[a][0] != gm[i][0]:
            continue
        gate = _members_act(screen, design, f"{a} / {i}", [a, i], units_used, in_pair=False)
        per = compared(a, i)
        r_top = combined(per, 1)
        main = max(per, key=lambda k: per[k][6])  # the kind with the most cells: its profiles for the figure
        _, _, x, y, t, n_genes, _ = per[main]
        row = {"gene": gm[a][0], "activation": a, "inhibition": i, "pair": f"{a} / {i}",
               "correlation_all": combined(per, 0), "correlation_top": r_top, "genes": n_genes,
               "different_gene_pairs_as_low": as_low(null, r_top),
               "activation_change": gate[f"single_change_{a}"], "inhibition_change": gate[f"single_change_{i}"],
               "both_directions_act": gate["members_act"]}
        if several:  # each kind's value beside the pooled one, and where the kinds disagree
            low = {k: as_low(null_in.get(k, np.array([])), v[1]) for k, v in per.items()}
            for k in kinds + ([steps.ACROSS] if steps.ACROSS in per else []):
                row[f"correlation_top: {k}"] = per[k][1] if k in per else np.nan
                row[f"different_gene_pairs_as_low: {k}"] = low.get(k, np.nan)
            row["kinds"] = ", ".join(per)
            row["kinds_disagree"] = steps.kinds_disagree({k: v[1] for k, v in per.items()},
                                                         {k: x <= 0.05 for k, x in low.items() if np.isfinite(x)})
        rows.append(row)
        kept.append(pd.DataFrame({"pair": f"{a} / {i}", "kind": main, "gene": list(t),
                                  "activation": x[t].to_numpy(), "inhibition": y[t].to_numpy()}))
    columns = ["gene", "activation", "inhibition", "pair", "correlation_all", "correlation_top", "genes",
               "different_gene_pairs_as_low", "activation_change", "inhibition_change", "both_directions_act"]
    df = pd.DataFrame(rows)
    df = (df[columns + [c for c in df.columns if c not in columns]] if len(df)
          else pd.DataFrame(columns=columns)).sort_values("correlation_top")
    path = os.path.join(out_dir, "figure.png")
    if df.empty:
        checks = figures.bars(np.zeros(1), ["none"], path, "No gene with both modes", "genes")
        return Result("not_enough_data", df, {}, path, checks,
                      "no gene with both an activation and an inhibition label")
    from . import charts
    muted = set(df.loc[~df["both_directions_act"].astype(bool), "pair"])  # a direction that does not move its gene
    checks = charts.checks(charts.ranked_targets(df.rename(columns={"pair": "target"}), "correlation_top", path,
                                                 "CRISPRa vs CRISPRi profile, r", "Activation against inhibition of "
                                                 "the same gene", muted=(muted, "Mode without effect")),
                           zero=True)
    acting = df[df["both_directions_act"].astype(bool)]
    c = acting["correlation_top"]
    readable = (f"; where both directions move the gene ({len(c)}), from {c.min():.2f} to {c.max():.2f}"
                if len(c) else "; no gene is moved by both directions")
    diag = {"top": top, "both_directions_act": int(len(c)), "different_gene_pairs": int(len(null))}
    if several:
        disagree = df.loc[df["kinds_disagree"] != "", "pair"].tolist()
        across = df.loc[df["kinds"] == steps.ACROSS, "pair"].tolist()
        diag.update({"kinds_of_capture": kinds, "kinds_disagree": disagree, "across_kinds": across})
        readable += (f"; each profile pooled within one of {len(kinds)} kinds of capture ({'; '.join(kinds)}), "
                     "weighted by cells"
                     + (f"; the kinds disagree for {len(disagree)} ({', '.join(disagree[:5])})" if disagree else "")
                     + (f"; {len(across)} compared across kinds, as no kind holds both" if across else ""))
    tables = {"profiles": pd.concat(kept, ignore_index=True)}
    if not len(null):  # no pair of different genes to read against: the numbers, without a status
        return Result("measured", df, diag, path, checks,
                      f"{len(df)} activation and inhibition pairs of one gene; the correlation of their profiles (top "
                      f"{top} genes){readable}; no pair of different genes for a reference", tables=tables)
    below = acting[acting["different_gene_pairs_as_low"] <= 0.05]
    return Result("detected" if len(below) else "not_detected", df, {**diag, "below_the_null": below["pair"].tolist()},
                  path, checks, f"{len(below)} of {len(df)} activation and inhibition pairs of one gene correlate "
                                f"lower than 95% of the {len(null)} pairs of different genes (top {top} genes)"
                                f"{readable}", tables=tables)


# ---- combinations --------------------------------------------------------------------------------------------------
@register("E.combinations", Contract(
    serves="E.combinations",
    applies_when="pairs or sets",
    needs="labels of pairs ('a|b') or sets ('a+b') and of their singles, the control cells; replicate units for "
          "the added agreement check",
    returns="of the pairs (min_cells cells, pooled over the units that hold them), the testable ones (both singles "
            "with min_cells cells too) and, per pair: its cells and its singles'; whether each member changes its "
            "target gene in the pair's cells (B.efficacy's 30% rule); the pair's profile, each cell against the "
            "control cells of its own unit, as a weighted sum of its singles' (Norman et al. 2019's regression: the "
            "two coefficients by least squares, their magnitude and asymmetry (dominance), R², and what is left, "
            "the interaction) or against their plain sum: fitted_R2 is the weighted sum's 1 - SSE/SST over the "
            "singles' top genes, and additive_correlation_sq the plain sum's squared "
            "correlation with the pair over the pair's top 300 genes, a measure of shape that ignores scale, so the "
            "two do not compare; the departure's size over the pair's top 300 genes. Where 2 or more units hold the "
            "pair and both singles with min_cells cells each, an added check: the departure's agreement between "
            "those units, against the same agreement for pseudo-pairs of control cells drawn at the pair's and its "
            "singles' cell numbers in each unit (calibration on control guides, as published screens make simulated "
            "targets from them), and per pair the share of those as high; per program its observed and additive "
            "score",
    fails_when=[("a pair without its singles in the screen cannot be compared with them",
                 "the testable-pairs report: the pair and both singles with min_cells cells"),
                ("a member that does not act in the pair's cells makes the pair a single", "members_act, per pair"),
                ("a departure can be noise, more so with fewer cells",
                 "the cells of the pair and its singles stated; where 2 or more units hold them, the departure's "
                 "agreement between units against control pseudo-pairs at their cell numbers")],
    tested_against="CRISPRai's pair captures: each pair's departure from the weighted sum of its singles and "
                   "its reproduction between captures, against the doubles an earlier hand analysis tested; the "
                   "synthetic screen: a planted interaction beyond its control pseudo-pairs, an additive pair not; "
                   "regulatory_modes: the modes of the paper's reproduction of one pair",
    choices={"min_cells": [20, 30], "gene_sets": ["hallmark", "given"],
             "model": ["fitted_weights", "plain_sum", "regulatory_modes"]},
    inputs={"gene_lists": "{name: [genes]} for gene_sets 'given'",
            "de": "regulatory_modes: the DE table of a genes run (C.genes) with method seurat3_lr and groups holding "
                  "the pair and each single (gene, group, avg_logFC, DE), as <run>:de, a table or a TSV path",
            "pair": "regulatory_modes: the pair's group", "singles": "regulatory_modes: its two singles' groups",
            "reference": "regulatory_modes: pairs to read the pair against, in the same de table, as "
                         "[{pair, singles: [a, b]}]: the pairs with a member that does not change its target (they "
                         "should look like their other single) or other pairs taken as negative controls. Their "
                         "additive shares give the reference band",
            "synergy": "regulatory_modes: how far above the sum of the singles a gene must rise (0.1)",
            "buffering": "regulatory_modes: how far below it a gene must fall (0.7)"},
    scope={"gene_lists": ("gene_sets", ["given"]),
           **{k: ("model", ["regulatory_modes"]) for k in ("de", "pair", "singles", "synergy", "buffering",
                                                            "reference")}}))
def combinations(screen, design, out_dir, min_cells=20, gene_sets="hallmark",
                 gene_lists=None, model="fitted_weights", de=None, pair=None, singles=None, synergy=0.1,
                 buffering=0.7, reference=None):
    if model == "regulatory_modes":
        return regulatory_modes_of(de, pair, singles, synergy, buffering, out_dir, reference=reference)
    from .. import blocks as pt
    model = "plain_sum" if model == "profiles" else model
    with pt.inside(screen, out_dir):
        res = combinations_recipe(min_cells, model, gene_sets, gene_lists)
    df, report = res.table, dict(res.extra["report"])
    kept = [res.extra["profiles"]] if len(res.extra["profiles"]) else []
    report["recipe_as_run"] = pt.recipe_as_run(combinations_recipe, min_cells=min_cells, model=model,
                                               gene_sets=gene_sets, gene_lists=gene_lists)
    report["package_calls"] = res.calls
    path = os.path.join(out_dir, "figure.png")
    if df.empty:
        checks = figures.bars(np.zeros(1), ["none"], path, "No testable pair", "pairs")
        return Result("not_enough_data", df, report, path, checks,
                      f"{report['testable']} of {report['pairs']} pairs have both singles with {min_cells} cells")
    from . import charts
    muted = set(df.loc[~df["members_act"].astype(bool), "pair"])
    r = df["interaction_replicate_r"].dropna()
    shown = (("interaction_replicate_r", "Departure's agreement between units, r",
              "How well each pair's departure reproduces" + (
                  f" ({len(r)} of {len(df)} pairs held by 2 or more units)" if len(r) < len(df) else "")) if len(r)
             else ("interaction_size", "Departure, share of the pair's profile",
                   "How far each pair departs from its fit"))
    drawn = df[df[shown[0]].notna()]  # a pair without the agreement check is not drawn as an empty bar
    checks = charts.checks(charts.ranked_targets(drawn.rename(columns={"pair": "target"}), shown[0], path, shown[1],
                                                 shown[2], muted=(muted, "Single without effect")), zero=True)
    what = "the weighted sum of its singles" if model == "fitted_weights" else "the plain sum of its singles"
    d = df["interaction_size"]
    words = (f"{report['testable']} of {report['pairs']} pairs testable, {report['members_not_acting']} with a member "
             f"that does not change its target gene; the departure from {what} is median {d.median():.2f} of the "
             f"pair's profile (from {d.min():.2f} to {d.max():.2f}), on {int(df['cells'].sum())} pair cells")
    if len(r):
        words += (f"; where 2 or more units hold the pair and its singles ({len(r)} pairs), the departure correlates "
                  f"between units at median r {r.median():.2f} (from {r.min():.2f} to {r.max():.2f})")
    calibrated = df[df["control_pseudo_pairs"] > 0]
    if calibrated.empty:  # no agreement check: the fit's numbers, without a status
        why = ("too few control cells for pseudo-pairs" if len(r) else
               "no pair held with both singles by 2 or more units, so the departure's agreement is not checked")
        return Result("measured", df, report, path, checks, f"{words}; {why}",
                      tables={"profiles": pd.concat(kept, ignore_index=True)})
    beyond = calibrated[(calibrated["control_pseudo_pairs_as_high"] <= 0.05) & calibrated["members_act"].astype(bool)]
    report["beyond_the_control_pseudo_pairs"] = beyond["pair"].tolist()
    return Result("detected" if len(beyond) else "not_detected", df, report, path, checks,
                  words + f"; {len(beyond)} of the {len(calibrated)} pairs checked, both members acting, reproduce "
                          "beyond 95% of control pseudo-pairs at their cell numbers",
                  tables={"profiles": pd.concat(kept, ignore_index=True)})



def combinations_recipe(min_cells=20, model="fitted_weights", gene_sets="hallmark", gene_lists=None):
    """E.combinations as a block (docs/adr/0031, D2): each pair against its singles, with control pseudo-pairs at
    its cell numbers as the reference."""
    from .. import blocks as pt
    return pt.pair_fit(model=model, min_cells=min_cells, gene_sets=gene_sets, gene_lists=gene_lists)


REGISTRY["E.combinations"].recipe = combinations_recipe  # its fitted_weights and plain_sum models


NULL_DRAWS = 50  # pseudo-pairs per pair


def pseudo_pair_null(screen, Lk, sizes, model, min_cells, draws=NULL_DRAWS, seed=0, reference=None):
    """The between-unit agreement of a pair's departure when nothing interacts: in each unit, control cells drawn
    without replacement as a pseudo-pair and pseudo-singles at the pair's and its singles' cell numbers, the rest as
    their reference; fitted and correlated as the pair is. sizes: {unit: [pair cells, single cells ...]}. Units
    whose control cells cannot hold the draws and min_cells of reference are left out; fewer than 2 left, no null.
    reference: the pair's own reference cells (its arm's control cells, or every control cell for a pair of two arms;
    bug register, BR36), every control cell where not given."""
    from ..steps.pairs import norman_fit
    rng = np.random.default_rng(seed)
    pool = reference if reference is not None else screen.label == "control"
    ctrl = {u: np.flatnonzero(pool & (screen.unit == u)) for u in sizes}
    usable = [u for u, n in sizes.items() if len(ctrl[u]) >= sum(n) + min_cells]
    if len(usable) < 2:
        return np.asarray([])
    out = []
    for _ in range(draws):
        per = []
        for u in usable:
            cells = rng.permutation(ctrl[u])
            cut = np.cumsum([0] + sizes[u])
            groups = [cells[a:b] for a, b in zip(cut[:-1], cut[1:])]
            base = np.asarray(Lk[cells[cut[-1]:]].mean(0)).ravel()
            pair, *singles = [np.asarray(Lk[g].mean(0)).ravel() - base for g in groups]
            per.append((pair, singles))
        # pooled as the pair is: each unit's profile weighted by its cells
        cells = {u: sizes[u] for u in usable}
        obs = sum(cells[u][0] * p for u, (p, _) in zip(usable, per)) / sum(cells[u][0] for u in usable)
        singles_ = [sum(cells[u][k + 1] * s[k] for u, (_, s) in zip(usable, per)) / sum(cells[u][k + 1] for u in usable)
                    for k in range(len(per[0][1]))]
        S = np.column_stack(singles_)
        sel = np.unique(np.concatenate([np.argsort(-np.abs(s_))[:300] for s_ in singles_]))
        w = norman_fit(S[sel], obs[sel]) if model == "fitted_weights" else np.ones(S.shape[1])
        top = np.argsort(-np.abs(obs))[:300]
        res = [(p - np.column_stack(s) @ w)[top] for p, s in per]
        rs = [np.corrcoef(a, b)[0, 1] for a, b in itertools.combinations(res, 2)]
        if np.all(np.isfinite(rs)):
            out.append(float(np.mean(rs)))
    return np.asarray(out)


def _members_act(screen, design, pair, parts, units, in_pair=True, reference=None):
    """Each member's target gene in the pair's cells against the control cells of the same units (log2 of mean counts
    per 10,000), beside the member's single; a member acts when its gene moves by B.efficacy's 30% the way its
    guide should (CRISPRi down, CRISPRa up). A member whose gene is not in the matrix is not measurable. Read in a
    pair, the pair and its singles share the pair's reference; read alone (in_pair False, `pair` only a name), each
    member is read against its own."""
    from ..steps import on_target
    from .b import EFFICACY_LOG2FC, expected_sign
    in_units = np.isin(screen.unit, units)
    ctrl = in_units & (reference if reference is not None else screen.own_reference(pair)) if in_pair else None  # BR36
    out, acts = {}, []
    for m in parts:
        gene = screen.own_gene_of(m)
        sign = expected_sign(design, screen.label_guide(m))
        if gene is None or sign == 0:
            out[f"own_gene_{m}"], out[f"own_change_{m}"], out[f"single_change_{m}"] = gene, np.nan, np.nan
            acts.append(False)
            continue
        ref = ctrl if in_pair else in_units & (reference if reference is not None else screen.own_reference(m))
        alone = on_target.change(screen, gene, in_units & (screen.label == m), ref, "mean_cp10k")
        change = on_target.change(screen, gene, in_units & (screen.label == pair), ctrl, "mean_cp10k") if in_pair \
            else alone
        out[f"own_gene_{m}"], out[f"own_change_{m}"], out[f"single_change_{m}"] = gene, change, alone
        acts.append(bool(np.sign(change) == sign and abs(change) >= EFFICACY_LOG2FC))
    out["members_act"] = all(acts)
    return out


def regulatory_modes(obs, exp, synergy=0.1, buffering=0.7):
    """Each gene's mode under the additive model (expected = the sum of the singles' logFC): synergy where the
    difference exceeds `synergy` and the magnitude grows or the sign flips, buffering where the magnitude falls short
    by more than `buffering`, additive otherwise."""
    obs, exp = np.asarray(obs, float), np.asarray(exp, float)
    d = obs - exp
    syn = ((np.sign(obs) != np.sign(exp)) | (np.abs(obs) - np.abs(exp) > synergy)) & (np.abs(d) > synergy)
    buf = ~syn & (np.abs(exp) - np.abs(obs) > buffering)
    return np.where(syn, "synergy", np.where(buf, "buffering", "additive"))


def _pair_additive_share(de_sets, lfc, pair, singles, synergy, buffering):
    """A pair's additive share: the share of its DE genes whose observed logFC follows the sum of its singles'."""
    a, b = singles
    genes = sorted(de_sets[pair])
    if not genes:
        return None, 0
    modes = regulatory_modes(lfc[pair].reindex(genes), (lfc[a] + lfc[b]).reindex(genes), synergy, buffering)
    return round(float((modes == "additive").mean()), 3), len(genes)


def regulatory_modes_of(de, pair, singles, synergy, buffering, out_dir, band=None, reference=None):
    """The modes of each perturbation's DE genes (each single and the pair), the pair's observed logFC against the sum
    of its singles'; and the pair's DE genes in neither single. reference: other pairs in the same table, each with its
    singles, read the same way: their additive shares are the band the pair is read against."""
    if isinstance(de, str):
        de = pd.read_csv(de, sep="\t")
    if de is None or not pair or len(singles or []) != 2:
        raise ValueError("regulatory_modes needs the DE table of C.genes run with method seurat3_lr and groups holding "
                         "the pair and each single, the pair and its two singles")
    tables = {g: t for g, t in de.groupby("group", sort=False)}
    named = [pair, *singles] + [x for r in reference or [] for x in [r.get("pair"), *(r.get("singles") or [])]]
    absent = [g for g in named if g not in tables]
    if absent:
        raise ValueError(f"groups {sorted(set(map(str, absent)))} are not in the de table; its groups are "
                         f"{sorted(tables)[:12]}. C.genes with method seurat3_lr tests each group named in its groups")
    a, b = singles
    de_sets = {k: set(t.loc[t["DE"].astype(bool), "gene"]) for k, t in tables.items()}
    pair_only = de_sets[pair] - de_sets[a] - de_sets[b]
    lfc = {k: t.set_index("gene")["avg_logFC"] for k, t in tables.items()}
    expected = lfc[a] + lfc[b]
    rows, modes_rows = [], []
    for name in (a, b, pair):
        genes = sorted(de_sets[name])
        modes = regulatory_modes(lfc[pair].reindex(genes), expected.reindex(genes), synergy, buffering) \
            if genes else np.array([])
        n = len(genes)
        counts = {k: int((modes == k).sum()) for k in ("additive", "synergy", "buffering")}
        rows.append({"perturbation": name, "DE genes": n,
                     **{f"{k} share": round(v / n, 3) if n else None for k, v in counts.items()},
                     "synergy/buffering": round(counts["synergy"] / counts["buffering"], 2)
                     if counts["buffering"] else None})
        modes_rows += [{"perturbation": name, "gene": g, "observed": float(lfc[pair][g]),
                        "expected": float(expected[g]), "regulatory_mode": md} for g, md in zip(genes, modes)]
    table = pd.DataFrame(rows)
    modes_df = pd.DataFrame(modes_rows, columns=["perturbation", "gene", "observed", "expected", "regulatory_mode"])
    ref_rows = []
    for r in reference or []:
        share, n = _pair_additive_share(de_sets, lfc, r["pair"], r["singles"], synergy, buffering)
        ref_rows.append({"pair": r["pair"], "singles": " + ".join(r["singles"]), "DE genes": n,
                         "additive share": share})
    ref = pd.DataFrame(ref_rows, columns=["pair", "singles", "DE genes", "additive share"])
    shares = [float(x) for x in ref["additive share"] if x is not None and pd.notna(x)]
    if band is None and shares:
        band = (min(shares), max(shares))
    path = os.path.join(out_dir, "figure.png")
    checks = modes_figure(modes_df, table, pair, singles, band, path)
    own = next(r["additive share"] for r in rows if r["perturbation"] == pair)
    summary = (f"{len(pair_only)} DE genes of {pair} alone; additive shares " +
               ", ".join(f"{r['perturbation']} {r['additive share']}" for r in rows))
    if shares:
        summary += (f". Against the reference: the pair's additive share {own}, the {len(shares)} reference pairs' "
                    f"{band[0]}-{band[1]} (read the same way, in the same table)")
    else:
        summary += (". No reference pairs were given: the pairs with a member that does not change its target, run "
                    "the same way (reference), show how far a pair departs without an interaction")
    diag = {"model": "regulatory_modes", "pair_specific_de": len(pair_only), "synergy": synergy,
            "buffering": buffering, "log_base": "e (Seurat 3's avg_logFC is the natural log)"}
    if shares:
        diag.update(reference_band=[band[0], band[1]], reference_pairs=len(shares))
    return Result("detected" if len(modes_df) else "not_enough_data", table, diag, path, checks, summary,
                  tables={"modes": modes_df, **({"reference": ref} if len(ref) else {})})


def modes_figure(modes_df, table, pair, singles, band, path):
    """a, the pair's observed against expected logFC on its DE genes, by mode; b, each perturbation's share of DE
    genes in each mode (a reference range of additive shares as a grey band, where one is given)."""
    plt = figures._plt()
    fig, (ax, bx) = plt.subplots(1, 2, figsize=(figures.DOUBLE, figures.HEIGHT))
    colours = dict(zip(("additive", "synergy", "buffering"), ("0.6", figures.OKABE_ITO[3], figures.OKABE_ITO[0])))
    p = modes_df[modes_df["perturbation"] == pair]
    for mode, c in colours.items():
        sel = p[p["regulatory_mode"] == mode]
        ax.scatter(sel["expected"], sel["observed"], s=6, color=c, linewidths=0, label=mode, zorder=3)
    lim = float(np.nanmax(np.abs(p[["observed", "expected"]].to_numpy()))) if len(p) else 1.0
    ax.plot([-lim, lim], [-lim, lim], color="0.45", lw=0.5, ls="--", zorder=1)
    ax.set_xlabel(f"Expected logFC, natural log ({' + '.join(singles)})")
    ax.set_ylabel(f"Observed logFC, natural log ({pair})")
    ax.legend(loc="upper left", handletextpad=0.2, borderaxespad=0.2)
    names = list(singles) + [pair]
    if band:
        bx.axhspan(band[0] * 100, band[1] * 100, color="0.9", zorder=0)
    shares = table.set_index("perturbation")
    bottom = np.zeros(len(names))
    for mode, c in colours.items():
        v = np.array([100 * (shares.loc[n, f"{mode} share"] if pd.notna(shares.loc[n, f"{mode} share"]) else 0)
                      for n in names])
        bx.bar(range(len(names)), v, bottom=bottom, color=c, width=0.6, zorder=2)
        bottom += v
    bx.set_xticks(range(len(names)))
    bx.set_xticklabels(names, rotation=45, ha="right")
    bx.set_ylabel("Share of the DE genes (%)")
    bx.set_ylim(0, 100)
    for a_, letter in ((ax, "a"), (bx, "b")):
        a_.text(-0.02, 1.02, letter, transform=a_.transAxes, fontsize=8, fontweight="bold", ha="right", va="bottom")
    issues = figures._save(plt, fig, path, f"The additive model of {pair}")
    return {"reference_shown": True, "zero_centred": None, "direction_labelled": True, "layout_issues": issues}


def _program_sets(screen, design, gene_sets, gene_lists, genes):
    """Index arrays into `genes` of each program: the given lists, or the hallmark sets of the organism."""
    if gene_sets == "given":
        lists = gene_lists or {}
    else:
        from .. import gene_sets as gs
        organism = value(design, "experiment.organism")
        lists = gs.msigdb("hallmark", organism) if organism in ("human", "mouse") else {}
    pos = {g: i for i, g in enumerate(genes)}
    out = {}
    for name, gl in lists.items():
        idx = [pos[g] for g in gl if g in pos]
        if idx:
            out[name] = np.array(idx)
    return out


# ---- condition x perturbation --------------------------------------------------------------------------------------
def _interaction_on_cells(Y, target, second, sample):
    """Least squares on cells, every score at once: score ~ each sample in each condition its own baseline + target +
    target in the second condition. The last coefficient is the change of the target's effect; returns it and its
    two-sided p per score, and the residual degrees of freedom. Where the design cannot tell the change from the
    samples' baselines (no sample and condition holding both the target's and control cells to anchor it), the
    change is not estimable: it and its p are missing."""
    group = np.array([f"{s}\t{c}" for s, c in zip(sample, second)])
    groups = list(dict.fromkeys(group))
    cols = [np.ones(len(target))] + [(group == g).astype(float) for g in groups[1:]]
    cols += [target.astype(float), (target & second).astype(float)]
    X = np.column_stack(cols)
    k = Y.shape[1] if Y.ndim > 1 else 1
    rank = np.linalg.matrix_rank(X)
    if rank == np.linalg.matrix_rank(X[:, :-1]):
        return np.full(k, np.nan), np.full(k, np.nan), len(X) - rank
    XtX = np.linalg.pinv(X.T @ X)
    B = XtX @ X.T @ Y
    dof = len(X) - rank
    resid = Y - X @ B
    s2 = (resid ** 2).sum(axis=0) / max(dof, 1)
    se = np.sqrt(s2 * XtX[-1, -1])
    with np.errstate(divide="ignore", invalid="ignore"):
        p = np.where(se > 0, 2 * stats.t.sf(np.abs(B[-1] / se), max(dof, 1)), np.nan)
    return B[-1], p, dof


def _units_hold_each_condition(screen, t, cond, use, min_cells=10):
    """Whether the design's test of target t's change is over units: the design names biological replicates, and in
    each of the two conditions at least UNITS_FOR_A_TEST units hold min_cells of its cells and of its control cells
    (C.genes' rule)."""
    from .c import UNITS_FOR_A_TEST
    if not screen.independent_units():
        return False
    mt, mr = screen.cells_of(t), screen.reference("control_guides", target=t)
    for c in use:
        held = sum(int((mt & (cond == c) & (screen.unit == u)).sum()) >= min_cells and
                   int((mr & (cond == c) & (screen.unit == u)).sum()) >= min_cells for u in screen.units())
        if held < UNITS_FOR_A_TEST:
            return False
    return True


def pair_needed(names):
    """Why two of more than two conditions are named: the conditions, and the pairs that differ in one factor."""
    split = {n: str(n).split("|") for n in names}
    pairs = [[a, b] for i, a in enumerate(sorted(names)) for b in sorted(names)[i + 1:]
             if len(split[a]) == len(split[b]) > 1 and sum(x != y for x, y in zip(split[a], split[b])) == 1]
    return (f"{len(names)} conditions ({', '.join(sorted(map(str, names)))}): give pair, the two to compare"
            + (f"; those that differ in one factor: {pairs}" if pairs else ""))


@register("E.condition_x_perturbation", Contract(
    serves="E.condition_x_perturbation", within="each condition",
    applies_when="2 or more conditions",
    needs="each cell's condition (experiment.conditions, by sample or unit), units, the control cells; the organism "
          "for the hallmark sets, or gene lists",
    returns="per target and program: the change of the target's effect on the program's score from one condition to "
            "the other, each effect against the target's own control cells; by default the design's test, as "
            "C.genes chooses it: over units where the design names biological replicates and 3 or more of them hold "
            "10 of the target's cells and 10 control cells in each condition (each unit's difference, paired where a "
            "unit holds both conditions, Welch otherwise), else one test on cells (least squares: each sample in "
            "each condition its own baseline, the target and the target in the second condition), with the cells "
            "and samples of each condition; a change the design cannot estimate is left out and counted; q-values; "
            "per target, program and unit, the score's difference from the unit's control cells, for the figure",
    fails_when=[("a condition's own shift moves every target alike", "each effect is against the control cells "
                 "of its own sample in the same condition"),
                ("cells as the observations take a difference between samples for an effect",
                 "the units are the replicates where 3 or more hold each condition; on cells, the samples of each "
                 "condition are stated, and a target with one sample in a condition is said to be read against a "
                 "difference between samples"),
                ("a layout where the change cannot be told from the samples' baselines gives a number anyway",
                 "the change is tested only where the design can estimate it; the others are counted")],
    tested_against="the synthetic screen of wells in two conditions, 50 seeds: the planted interaction at p < 0.05 "
                   "in 50 by the design's test (on cells there, the wells not named as replicates) and in 50 over "
                   "units, the null in 2 by each (the set's mean log-normalised expression as the score); a "
                   "condition's shift that differs between two samples, read with unbalanced cells, gives fewer "
                   "than 10% at p < 0.05, and a layout that cannot tell the change from the samples' baselines "
                   "gives no number (tests/test_condition_interaction_on_cells.py)",
    choices={"what": ["scores", "genes", "controls", "shares"], "gene_sets": ["hallmark", "given"],
             "test": ["auto", "cells", "units"]},
    inputs={"gene_lists": "{name: [genes]} for gene_sets 'given'",
            "pair": "the two conditions to compare, [first, second] (needed where there are more than two)",
            "genes": "what genes: the named genes whose effect's change is tested as a score's is (at most 200)",
            "cell_states": "what shares: each cell's state, a C.states_and_composition run's cell_states table "
                           "(<run>:cell_states)"},
    scope={"gene_lists": ("gene_sets", ["given"]), "genes": ("what", ["genes"]),
           "cell_states": ("what", ["shares"])}))
def condition_x_perturbation(screen, design, out_dir, gene_sets="hallmark", gene_lists=None, pair=None,
                             test="auto", what="scores", genes=None, cell_states=None):
    """what: scores (the sets' scores), genes (the named genes' expression), controls (the control cells' own
    response), shares (each target's share of each state in each condition)."""
    path = os.path.join(out_dir, "figure.png")
    cond = screen.condition
    names = [c for c in pd.Series(cond[cond != ""]).value_counts().index]
    if not pair and len(names) > 2:
        raise ValueError(pair_needed(names))
    from ..design_gate import condition_names
    listed = [c for c in condition_names(value(design, "experiment.conditions")) if c in names]
    use = list(pair) if pair else listed if sorted(listed) == sorted(names) else sorted(names)  # the design's order
    if len(use) < 2 or any(c not in names for c in use):
        checks = figures.bars(np.zeros(1), ["none"], path, "Fewer than two conditions", "cells")
        return Result("not_enough_data", pd.DataFrame(), {"conditions": names}, path, checks,
                      "the cells carry fewer than two of the confirmed conditions")
    # then the reference (docs/adr/0045, D4): control cells of one kind, so a change in the mix of kinds between the
    # conditions is not read as a change; each target keeps its own arm's control cells (Screen.reference)
    screen, reference = _controls_of_one_kind(screen)
    cond = screen.condition
    if what == "controls":
        return controls_response(screen, design, out_dir, use, test, reference)
    if what == "shares":
        return shares_by_condition(screen, out_dir, use, cell_states, reference)
    # the per-cell values whose effect's change is tested: the named genes' log-normalised expression, or the sets
    # scored as C.programs scores them (scanpy's score_genes, the Python form of Seurat's module score)
    if what == "genes":
        libsize = np.maximum(np.asarray(screen.libsize, dtype=float), 1)
        scores = {g: np.log1p(screen.gene(g) * 1e4 / libsize) for g in list(dict.fromkeys(genes or []))[:200]
                  if screen.gene(g) is not None}
    elif gene_sets == "given":
        scores, _, _ = scanpy_scores(screen, gene_lists or {}, min_genes=3)
    else:
        from .. import gene_sets as gs
        organism = value(design, "experiment.organism")
        scores = (scanpy_scores(screen, gs.msigdb("hallmark", organism))[0] if organism in ("human", "mouse")
                  else {})
    rows = []
    for t in screen.targets(10):
        for u in screen.units():
            for c in use:
                m = (screen.unit == u) & (cond == c)
                mt, mr = m & screen.cells_of(t), m & screen.reference("control_guides", target=t)
                if mt.sum() < 10 or mr.sum() < 10:
                    continue
                for name, s in scores.items():
                    rows.append({"target": t, "score": name, "unit": u, "condition": c,
                                 "difference": float(s[mt].mean() - s[mr].mean())})
    df = pd.DataFrame(rows, columns=["target", "score", "unit", "condition", "difference"])
    per, not_estimable, one_sample = [], [], []
    names_ = list(scores)
    by_units = {t: test == "units" or (test == "auto" and _units_hold_each_condition(screen, t, cond, use))
                for t in screen.targets(10)}
    in_pair = np.isin(cond, use)
    unit_of = np.asarray(screen.unit)
    for t in [t for t, u in by_units.items() if not u] if names_ else []:
        mt, mr = in_pair & screen.cells_of(t), in_pair & screen.reference("control_guides", target=t)
        n = {c: (int((mt & (cond == c)).sum()), int((mr & (cond == c)).sum())) for c in use}
        if any(a < 10 or b < 10 for a, b in n.values()):
            continue
        m = mt | mr
        Y = np.column_stack([np.asarray(scores[k])[m] for k in names_])
        inter, p, _ = _interaction_on_cells(Y, mt[m], cond[m] == use[1], unit_of[m])
        if not np.isfinite(inter).any():
            not_estimable.append(t)
            continue
        samples = {c: int(len(set(unit_of[mt & (cond == c)]))) for c in use}
        how_t = "cells, each sample in each condition its own baseline"
        if min(samples.values()) < 2:
            one_sample.append(t)
            how_t += "; one sample in a condition, so the change is also a difference between samples"
        for k, name in enumerate(names_):
            row = {"target": t, "score": name, "interaction": float(inter[k]), "p": float(p[k]), "test": how_t}
            for c in use:
                row[f"cells {c}"], row[f"control cells {c}"] = n[c]
                row[f"samples {c}"] = samples[c]
            per.append(row)
    paired = {}  # (target, score): the units its paired test used, which the figure and the text read too
    for (t, name), g in df.groupby(["target", "score"]):
        if not by_units.get(t):
            continue
        a, b = g[g["condition"] == use[0]], g[g["condition"] == use[1]]
        both = sorted(set(a["unit"]) & set(b["unit"]))
        if len(both) >= 2:  # units that hold both conditions: paired by unit, those in one condition left out
            d = b.set_index("unit").loc[both, "difference"] - a.set_index("unit").loc[both, "difference"]
            p, how, inter = float(stats.ttest_1samp(d, 0).pvalue), "paired over units", float(d.mean())
            paired[(t, name)] = set(both)
            units = {f"units {use[0]}": len(both), f"units {use[1]}": len(both), "units paired": len(both),
                     "units in one condition only": len(a) + len(b) - 2 * len(both)}
        elif len(a) >= 2 and len(b) >= 2:
            p = float(stats.ttest_ind(b["difference"], a["difference"], equal_var=False).pvalue)
            how, inter = "Welch over units", float(b["difference"].mean() - a["difference"].mean())
            units = {f"units {use[0]}": len(a), f"units {use[1]}": len(b)}
        else:
            continue
        per.append({"target": t, "score": name, "interaction": inter, "p": p, "test": how, **units})
    # the figure's points and the text's means on the units each paired test used: with units in one condition only,
    # the means over every unit read "rest +1.02, stim -0.478" beside a change of +1.01 (GPT's review of 251c436)
    tested = df[np.array([(t, s) not in paired or u in paired[(t, s)]
                          for t, s, u in zip(df["target"], df["score"], df["unit"])], dtype=bool)]
    per = pd.DataFrame(per)
    if per.empty:
        checks = figures.bars(np.zeros(1), ["none"], path, "No target testable in both conditions", "cells")
        if not names_:
            why = ("none of the named genes is a gene of the screen" if what == "genes" else
                   "nothing was scored: no given list of 3 or more genes in the screen" if gene_sets == "given" else
                   "nothing was scored: the hallmark sets are for human or mouse")
        elif not_estimable:
            why = (f"the change of {len(not_estimable)} target{'s' if len(not_estimable) > 1 else ''}' effects "
                   "cannot be told from the samples' baselines (no sample in a condition holds both its cells and "
                   "control cells), and no other target has 10 of its cells and 10 control cells in each condition")
        else:
            why = ("no target with 10 of its cells and 10 control cells in each condition" if test != "units" else
                   "no target with 2 units of each condition holding 10 of its cells and 10 control cells")
        return Result("not_enough_data", per, {"conditions": use, "not_estimable": not_estimable}, path, checks,
                      why)
    per["q"] = bh(per["p"].to_numpy())
    hits = per[per["q"] < Q]
    # the map: the targets and the programs with the most changes at q < Q, then the largest change
    ranks = per.assign(hit=per["q"] < Q, size=per["interaction"].abs())
    map_rows, map_cols = [ranks.groupby(k).agg(hits=("hit", "sum"), size=("size", "max"))
                          .sort_values(["hits", "size"], ascending=False).index[:n].tolist()
                          for k, n in (("target", MAP_ROWS), ("score", MAP_COLS))]
    M = per.pivot(index="target", columns="score", values="interaction").reindex(index=map_rows, columns=map_cols)
    if len(map_rows) > 2 and len(map_cols) > 2:
        from scipy.cluster.hierarchy import leaves_list, linkage
        F = M.fillna(0).to_numpy()
        M = M.iloc[leaves_list(linkage(F, "average")), leaves_list(linkage(F.T, "average"))]
    n_t, n_s = per["target"].nunique(), per["score"].nunique()
    kind = "gene" if what == "genes" else "program"
    shown = None if (len(map_rows), len(map_cols)) == (n_t, n_s) else (
        f"{len(map_rows)} of {n_t} targets, {len(map_cols)} of {n_s} {kind}s:\nthose with the most changes "
        f"at q < {Q}")
    ranked = hits if len(hits) else per
    top = ranked.reindex(ranked["interaction"].abs().sort_values(ascending=False).index).head(8)
    keys = list(zip(top["target"], top["score"]))
    d = tested[np.array([k in set(keys) for k in zip(tested["target"], tested["score"])], dtype=bool)].copy()
    d["pair"] = pd.Categorical(d["target"] + " · " + d["score"].map(_short),
                               [f"{t} · {_short(s)}" for t, s in keys], ordered=True)
    d = d.sort_values("pair")
    d["pair"] = d["pair"].astype(str)
    checks = figures.condition_map(M.to_numpy(), list(M.index), [_short(c) for c in M.columns], d, "pair",
                                   "difference", "condition", use, path,
                                   f"How each target's effect on each {kind} changes from {use[0]} to {use[1]}",
                                   f"Effect in {use[1]} − effect in {use[0]}",
                                   "Score, target − control cells,\nper unit", shown)
    on_units = per["test"].isin(["paired over units", "Welch over units"])
    how = "; ".join(w for w, n in (("each unit's difference tested over units", on_units.any()),
                                   ("one test on cells, each sample in each condition its own baseline",
                                    (~on_units).any())) if n)
    notes = []
    if one_sample:
        notes.append(f"{len(one_sample)} target{'s' if len(one_sample) > 1 else ''} with one sample in a condition, "
                     "whose change is also a difference between samples")
    if not_estimable:
        notes.append(f"{len(not_estimable)} target{'s' if len(not_estimable) > 1 else ''} whose change cannot be "
                     "told from the samples' baselines, not tested")
    return Result("detected" if len(hits) else "not_detected", per,
                  {"conditions": use, "pairs_changed": int(len(hits)), "test": how,
                   "one_sample_in_a_condition": one_sample, "not_estimable": not_estimable,
                   "multiple_testing": {"tests": int(len(per))}},
                  path, checks,
                  f"{len(hits)} target x {kind} effects differ between {use[0]} and {use[1]} at q < {Q} ({how}; "
                  f"{len(per)} tests corrected together)" + "".join(f"; {n}" for n in notes),
                  tables={"by_unit": df}, biology=condition_biology(hits, tested, use))


UNIT_CELLS = 10  # a unit's control cells in a condition, for its pseudobulk sample


def _controls_of_one_kind(screen):
    """The screen with control cells of one kind, and its reference in words (docs/adr/0045, D4): where
    non-targeting and cutting controls are both present, the cutting ones are left out, so a change in the mix of the
    two kinds between conditions is not read as a change (D.cutting_response compares the two kinds)."""
    from .d import CUTTING_KINDS
    if "kind" not in screen.guides:
        return screen, "the control cells"
    kind = dict(zip(screen.guides["guide"].astype(str), screen.guides["kind"].astype(str)))
    k = np.array([kind.get(g, "") for g in screen.top_guide])
    control = screen.label == "control"
    cutting = control & np.isin(k, CUTTING_KINDS)
    if not cutting.any() or not (control & (k == "non_targeting")).any():
        return screen, "the control cells"
    return (screen.subset(cells=~cutting),
            f"the non-targeting control cells ({int(cutting.sum())} cutting control cells left out: two kinds)")


def _control_arms(screen, control):
    """[(the arm in words, its control cells)]: each arm apart where the design matches the reference by an attribute
    (perturbation.reference_matches) and the control cells hold two or more arms (D4); else one, every control cell."""
    import json
    if not screen.reference_matches:
        return [("", control)]
    keys = sorted({k for i in np.flatnonzero(control) for k in screen.arms_of_cells[i]} - {"", "every arm"})
    if len(keys) < 2:
        return [("", control)]
    return [(", ".join(map(str, json.loads(k))) if str(k).startswith("[") else str(k), control & screen.in_arm(k))
            for k in keys]


def _batch_of_units(screen, design):
    """{unit: its batch}: units.batches by unit, or by sample where all of a unit's samples share one batch."""
    v = value(design, "units.batches")
    if not isinstance(v, dict):
        return {}
    by_unit, by_sample = v.get("by_unit") or {}, v.get("by_sample") or {}
    unit, sample = np.asarray(screen.unit).astype(str), np.asarray(screen.sample).astype(str)
    out = {}
    for u in sorted(set(unit)):
        if u in by_unit:
            out[u] = str(by_unit[u])
            continue
        seen = {str(by_sample[s]) for s in set(sample[unit == u]) if s in by_sample}
        if len(seen) == 1:
            out[u] = seen.pop()
    return out


def _units_described(mat, meta):
    """Each unit's log2 counts per million (+1) in each condition, and the difference of the conditions' means
    (second minus first; a single unit's own difference where one unit holds both): no test, so no p or q. Ranked by
    the size of the difference."""
    first = meta.loc[meta["group"] == "FIRST", "sample"].tolist()
    second = meta.loc[meta["group"] == "SECOND", "sample"].tolist()
    t = pd.DataFrame({"gene": np.asarray(mat.index).astype(str)})
    if mat.shape[1] == 0:
        return t.assign(effect=np.nan, statistic=np.nan)
    cpm = np.log2(mat.div(mat.sum(0).clip(lower=1), axis=1) * 1e6 + 1)
    for s in first + second:
        t[f"log2 CPM {s}"] = cpm[s].to_numpy()
    t["effect"] = (cpm[second].mean(1) - cpm[first].mean(1)).to_numpy() if first and second else np.nan
    t["statistic"] = t["effect"]  # what ranks the genes, where a test's statistic would
    return t.sort_values("effect", key=np.abs, ascending=False, na_position="last").reset_index(drop=True)


def controls_by_unit(screen, design, first, second, workdir):
    """The control cells' change from the first condition to the second, on units (docs/adr/0045, D4). A unit is the
    design's replicate; the cells whose unit the build could not tell are left out; each unit's control cells in a
    condition are summed. Units that hold both conditions pair with themselves (~ unit + condition), and the same
    unit is never split into two groups; where none does, the batches the design names block (~ batch + condition)
    where each holds both conditions; else the conditions' units are apart (~ condition). edgeR's quasi-likelihood
    F-test (pertura/r/pseudobulk_conditions.R) runs on the design's replicates, with two complete pairs or two units
    on each side, and raw integer counts. Otherwise each unit's values are listed with no q (_units_described).
    Returns (table, {how, tested, package_calls})."""
    import subprocess
    from .. import envs
    from ..tools.de.pseudobulk import R_DIR, aggregate, r_failure, write_table
    from . import packages
    from .c import R_SLOTS
    keep = ~screen.unassigned()
    units = np.asarray(screen.unit).astype(str)
    mat, meta = aggregate(screen.X, {"FIRST": np.flatnonzero(first & keep), "SECOND": np.flatnonzero(second & keep)},
                          units, screen.genes, UNIT_CELLS)
    sides = {g: set(meta.loc[meta["group"] == g, "mouse"]) for g in ("FIRST", "SECOND")}
    both = sorted(sides["FIRST"] & sides["SECOND"])
    if both:  # the same unit in both conditions: paired by it
        meta = meta[meta["mouse"].isin(both)].reset_index(drop=True)
        meta["pair"] = meta["mouse"]
        layout = "paired by unit (~ unit + condition)"
    else:
        batch = _batch_of_units(screen, design)
        meta["pair"] = [batch.get(u, "") for u in meta["mouse"]]
        held = meta.groupby("pair")["group"].nunique()
        if not (len(held) >= 2 and (meta["pair"] != "").all() and (held == 2).all()):
            meta = meta.drop(columns="pair")
        layout = "blocked by batch (~ batch + condition)" if "pair" in meta else "unpaired (~ condition)"
    mat = mat[list(meta["sample"])]
    try:
        packages.check_layer(screen.X, "raw_counts", "edgeR glmQLFit, glmQLFTest (pseudobulk of raw counts)")
        integers = True
    except ValueError:
        integers = False
    n = meta["group"].value_counts()
    if not screen.independent_units():
        why = "the design names no biological replicate, so its units are technical captures"
    elif both and len(both) < 2:
        why = "one unit holds both conditions: its own difference"
    elif not both and min(len(sides["FIRST"]), len(sides["SECOND"])) < 2:
        why = f"{len(sides['FIRST'])} and {len(sides['SECOND'])} units of {UNIT_CELLS} control cells in the conditions"
    elif not integers:
        why = "the counts are not raw integers, which edgeR needs"
    else:
        why = None
    if why:
        return _units_described(mat, meta), {
            "how": f"descriptive, no q: each unit's summed counts as log2 counts per million (+1), {layout}; {why}",
            "tested": False, "package_calls": []}
    rec = packages.call("edgeR", "glmQLFit, glmQLFTest", data=screen.X, layer="raw_counts",
                        formula="~ pair + group" if "pair" in meta else "~ group",
                        contrast="the second condition's control cells against the first's, each unit a sample",
                        covariates=(["unit"] if both else ["batch"]) if "pair" in meta else [],
                        filter="genes in 10% of samples, then filterByExpr", min_cells_per_sample=UNIT_CELLS)
    base = os.path.join(workdir, "pseudobulk")
    os.makedirs(os.path.join(base, "pb"), exist_ok=True)
    write_table(mat, meta, os.path.join(base, "pb", "conditions"))
    with R_SLOTS:
        p = subprocess.run([envs.rscript(), os.path.join(R_DIR, "pseudobulk_conditions.R")],
                           env=envs.r_env({"PERTURA_EXPR_DIR": base}), capture_output=True, text=True)
    failure = r_failure(p.returncode, p.stderr)
    if failure is not None:
        raise failure
    r = pd.read_csv(os.path.join(base, "r_results", "conditions__edger.tsv.gz"), sep="\t", dtype={"gene": str})
    t = pd.DataFrame({"gene": r["gene"], "effect": r["logFC"], "p": r["p"], "q": bh(r["p"].to_numpy())})
    t["statistic"] = np.sign(t["effect"]) * -np.log10(t["p"].clip(lower=1e-300))
    return t[["gene", "effect", "statistic", "p", "q"]], {
        "how": f"edgeR-QL pseudobulk of {int(n.get('FIRST', 0))} and {int(n.get('SECOND', 0))} units, {layout}",
        "tested": True, "package_calls": [rec]}


def controls_response(screen, design, out_dir, use, test="auto", reference="the control cells"):
    """The control cells' own response to the condition, as published screens describe the controls before the
    targets (docs/adr/0045, D4): the reference first (one kind of control cell, condition_x_perturbation; each arm
    apart, _control_arms), then the unit (controls_by_unit: edgeR on units where the design's replicates allow it,
    else each unit's values with no q). test cells, as chosen: scanpy's Wilcoxon on the cells' log-normalised counts.
    Where the screen holds antibody counts, each protein's mean CLR (Seurat's multimodal normalisation) in each unit,
    with its cells: descriptive, no q, said apart from the genes."""
    from .. import blocks
    from . import charts
    from .c import _wilcoxon_part
    path = os.path.join(out_dir, "figure.png")
    control, cond = screen.reference("control_guides"), np.asarray(screen.condition)
    keep, units = ~screen.unassigned(), np.asarray(screen.unit).astype(str)
    clr = blocks._protein_values(screen, "all") if screen.adata.obsm.get("antibody_counts") is not None else {}
    arms = _control_arms(screen, control)
    parts, prots, said, hows, calls, counts, tested = [], [], [], [], [], {}, False
    for i, (arm, ref) in enumerate(arms):
        first, second = ref & (cond == use[0]), ref & (cond == use[1])
        where = f" ({arm} arm)" if arm else ""
        counts[arm or "every control cell"] = {use[0]: int(first.sum()), use[1]: int(second.sum())}
        if first.sum() < 10 or second.sum() < 10:
            said.append(f"fewer than 10 control cells{where} in {use[0]} ({int(first.sum())}) or {use[1]} "
                        f"({int(second.sum())})")
            continue
        if test == "cells":
            rows = np.flatnonzero(first | second)
            t = _wilcoxon_part(screen.lognorm[rows], np.where(second[rows], "second", "first"), screen.genes,
                               ["second"], "first")
            t = t.rename(columns={"names": "gene", "logfoldchanges": "effect", "scores": "statistic", "pvals": "p",
                                  "pvals_adj": "q"})[["gene", "effect", "statistic", "p", "q"]]
            info = {"how": "scanpy's Wilcoxon on cells, as chosen (test cells): the cells are its observations",
                    "tested": True, "package_calls": []}
        else:
            t, info = controls_by_unit(screen, design, first, second, os.path.join(out_dir, f"arm_{i}"))
        tested |= info["tested"]
        calls += info["package_calls"]
        hows.append(info["how"] + where)
        if arm:
            t.insert(0, "arm", arm)
        parts.append(t)
        cells = f"{int(first.sum())} and {int(second.sum())} cells"
        said.append(f"{int((t['q'] < Q).sum())} genes change at q < {Q}{where} ({cells}; {info['how']})"
                    if info["tested"] else f"each unit's values{where} ({cells}; {info['how']})")
        for c, mask in ((use[0], first), (use[1], second)):
            for u in sorted(set(units[mask & keep])):
                m = mask & keep & (units == u)
                prots += [{"arm": arm, "protein": p, "condition": c, "unit": u, "cells": int(m.sum()),
                           "mean CLR": float(np.mean(v[m]))} for p, v in clr.items()]
    if not parts:
        checks = figures.bars(np.zeros(1), ["none"], path, "Too few control cells in a condition", "cells")
        return Result("not_enough_data", pd.DataFrame(), {"conditions": use, "reference": reference,
                                                          "control_cells": counts}, path, checks, "; ".join(said))
    t = pd.concat(parts, ignore_index=True)
    lines = []
    for arm in (list(dict.fromkeys(t["arm"])) if "arm" in t else [""]):
        sub, where = (t[t["arm"] == arm] if arm else t), (f" ({arm} arm)" if arm else "")
        if "q" in sub and sub["q"].notna().any():
            hit = sub[sub["q"] < Q]
            up = hit[hit["statistic"] > 0].sort_values("statistic", ascending=False)["gene"].head(10).tolist()
            down = hit[hit["statistic"] < 0].sort_values("statistic")["gene"].head(10).tolist()
            lines += ([f"higher in {use[1]}{where}: {', '.join(up)}"] if up else []) + (
                [f"lower in {use[1]}{where}: {', '.join(down)}"] if down else [])
        else:  # ranked by the size of the difference
            up = sub[sub["effect"] > 0]["gene"].head(10).tolist()
            down = sub[sub["effect"] < 0]["gene"].head(10).tolist()
            lines += ([f"largest rises in {use[1]}{where}, descriptive: {', '.join(up)}"] if up else []) + (
                [f"largest falls in {use[1]}{where}, descriptive: {', '.join(down)}"] if down else [])
    tables = {}
    if prots:
        prot = pd.DataFrame(prots)
        prot = prot if (prot["arm"] != "").any() else prot.drop(columns="arm")
        tables["proteins"] = prot
        means = prot.groupby([c for c in ("arm", "protein") if c in prot] + ["condition"])["mean CLR"].mean()
        means = means.unstack("condition")
        if use[0] in means and use[1] in means:
            d = (means[use[1]] - means[use[0]]).dropna()
            d = d.reindex(d.abs().sort_values(ascending=False).index).head(8)
            lines.append("proteins, descriptive (the mean of each unit's mean CLR; no q): " + ", ".join(
                f"{k if isinstance(k, str) else ' '.join(map(str, k))} {v:+.2f}" for k, v in d.items()))
    title = f"Control cells, {use[1]} against {use[0]}"
    if "q" in t and t["q"].notna().any():
        checks = charts.checks(charts.volcano(t[t["q"].notna()], path, title), reference=None, zero=True)
    else:
        checks = charts.checks(charts.ranked_targets(t.dropna(subset=["effect"]).head(30).rename(
            columns={"gene": "target"}), "effect", path, f"log2 CPM, {use[1]} − {use[0]}",
            title + ": each unit's values, descriptive"), zero=True)
    hit = t[t["q"] < Q] if "q" in t else t.iloc[0:0]
    return Result("detected" if len(hit) else "not_detected" if tested else "measured", t,
                  {"conditions": use, "direction": f"{use[1]} against {use[0]}", "reference": reference,
                   "arms": [a for a, _ in arms if a], "control_cells": counts, "test": "; ".join(hows),
                   "package_calls": calls},
                  path, checks,
                  f"in {reference}, {use[1]} against {use[0]}: " + "; ".join(said)
                  + (f"; {prot['protein'].nunique()} proteins: each unit's mean CLR, descriptive, no q" if prots else ""),
                  tables=tables, biology=lines)


def shares_by_condition(screen, out_dir, use, cell_states, reference="the control cells"):
    """Each target's share of each state in each condition against the control cells of its own samples in that
    condition (Fisher's exact test, as C.states_and_composition reads it), the conditions side by side. Each condition
    has its own test and nothing is tested between them: a shift in one condition and none in the other is listed as
    two facts, never as a difference between them (docs/adr/0045, D4)."""
    from ..steps import composition
    path = os.path.join(out_dir, "figure.png")
    if cell_states is None:
        checks = figures.bars(np.zeros(1), ["none"], path, "No states given", "cells")
        return Result("not_enough_data", pd.DataFrame(), {}, path, checks,
                      "what shares needs cell_states, a C.states_and_composition run's table (<run>:cell_states)")
    states = pd.read_csv(cell_states, sep="\t") if isinstance(cell_states, str) else cell_states
    lab = states.set_index(states["barcode"].astype(str))["state"].astype(str)
    lab = lab.reindex(np.asarray(screen.adata.obs_names).astype(str)).fillna("").to_numpy()
    cond, parts = np.asarray(screen.condition), []
    for c in use:
        sub = screen.subset(cells=cond == c)
        per, _, _ = composition.compare(sub, sub.targets(10), "control_guides", lab[cond == c], method="fisher",
                                        workdir=os.path.join(out_dir, "work"))
        parts.append(per[["target", "state", "mean_difference", "q"]].rename(
            columns={"mean_difference": f"difference {c}", "q": f"q {c}"}))
    if any(p.empty for p in parts):
        checks = figures.bars(np.zeros(1), ["none"], path, "A condition with no target to test", "cells")
        return Result("not_enough_data", pd.DataFrame(), {"conditions": use}, path, checks,
                      "a condition holds no target with 10 cells beside 10 control cells")
    t = parts[0].merge(parts[1], on=["target", "state"], how="outer")
    a, b = (t[f"q {use[0]}"] < Q).fillna(False), (t[f"q {use[1]}"] < Q).fillna(False)
    t["changed_in"] = np.select([a & b, a, b], ["both", use[0], use[1]], "neither")
    t["change"] = t[f"difference {use[1]}"] - t[f"difference {use[0]}"]
    lines = []
    for c in use:  # each condition's shifts on their own, the largest first
        hit = t[(t[f"q {c}"] < Q).fillna(False)]
        hit = hit.reindex(hit[f"difference {c}"].abs().sort_values(ascending=False).index)
        lines += [f"in {c}: {r['target']} in {r['state']}, share {r[f'difference {c}']:+.3f} against that "
                  f"condition's control cells (q {r[f'q {c}']:.2g})"
                  for r in hit.head(BIOLOGY_LINES // 2).to_dict("records")]
    M = t.pivot(index="target", columns="state", values="change")
    from . import charts
    checks = charts.checks(charts.heatmap(M.fillna(0).to_numpy(float), list(M.index), list(M.columns), path,
                                          f"Each target's state-share shift, {use[1]} minus {use[0]} (descriptive: "
                                          "not tested between them)", f"Share difference in {use[1]} − in {use[0]}"),
                           zero=True)
    n0, n1 = int(a.sum()), int(b.sum())
    return Result("detected" if n0 or n1 else "not_detected", t,
                  {"conditions": use, "reference": reference, "test": "Fisher's exact test in each condition, against "
                   "the control cells of the target's own samples there; nothing tested between the conditions"},
                  path, checks,
                  f"{n0} target x state shares shift in {use[0]} and {n1} in {use[1]}, each against {reference} of the "
                  f"target's own samples in that condition (Fisher's exact test in each; not tested between them)",
                  biology=lines)


def condition_biology(hits, by_unit, use, most=BIOLOGY_LINES):
    """Each target x program effect that differs between the two conditions, the largest change first: the effect
    in each condition (the mean over units of the target's cells less the control cells'), the change and its q."""
    if not len(hits):
        return []
    mean = by_unit.groupby(["target", "score", "condition"])["difference"].mean()
    top = hits.reindex(hits["interaction"].abs().sort_values(ascending=False).index)
    lines = []
    for r in top.head(most).to_dict("records"):
        v = {c: mean.get((r["target"], r["score"], c), np.nan) for c in use}
        each = ", ".join(f"{c} {v[c]:+.3g}" if np.isfinite(v[c]) else f"{c} not measured by unit" for c in use)
        lines.append(f"{r['target']} on {_short(r['score'])}: {each}; change {r['interaction']:+.3g} "
                     f"(q {r['q']:.2g})")
    if len(hits) > most:
        lines.append(f"{len(hits) - most} more effects differ: the result table holds them")
    return lines


# ---- programs across regulators ----------------------------------------------------------------------------------
def _effects_matrix(effects):
    from ..steps.effects_across_targets import effects_matrix
    t = pd.read_csv(effects, sep="\t") if isinstance(effects, str) else effects
    return effects_matrix(t)


def _modes_of(screen, design, targets):
    gm = label_gene_and_mode(screen, design)
    names = {1: "CRISPRa", -1: "CRISPRi"}
    return {t: names.get(gm[t][1], "all") if t in gm else "all" for t in targets}


@register("E.program_axis", Contract(
    serves="E.program_axis",
    applies_when="each target's effect on several programs (C.programs)",
    needs="C.programs' result table (target, score, effect), labels and each guide's mode",
    returns="for every two programs, over the targets (each mode apart where the screen has two): the Pearson "
            "correlation of the targets' effects on the one with their effects on the other, its p-value and "
            "Benjamini-Hochberg q, the targets counted; an axis is two programs that regulators move in opposite "
            "(or the same) directions",
    fails_when=[("a few strong targets make a correlation alone", "the targets counted, and the scatter drawn"),
                ("programs sharing genes correlate by construction", "the programs' shared top genes, counted")],
    tested_against="synthetic: targets planted to move program P1 up and P2 down together give r below -0.8, "
                   "independent programs give none at q < 0.05",
    choices={"method": ["pearson", "spearman"]},
    inputs={"effects": "C.programs' result table (target, score, effect), or its TSV path (default: the build's "
                       "latest programs or scores run)",
            "top_genes": "{program: [genes]} (C.programs' diagnostics), to count the genes two programs share"}))
def program_axis(screen, design, out_dir, effects=None, method="pearson", top_genes=None):
    from . import charts
    if effects is None:
        raise ValueError("E.program_axis needs effects, C.programs' result table")
    from ..steps import effects_across_targets as eat
    table = pd.read_csv(effects, sep="\t") if isinstance(effects, str) else effects
    M = eat.effects_matrix(table)
    modes = _modes_of(screen, design, M.index)
    df = eat.correlate(table, modes, method, top_genes)
    path = os.path.join(out_dir, "figure.png")
    if df.empty:
        checks = figures.bars(np.zeros(1), ["none"], path, "Fewer than 5 targets or 2 programs", "targets")
        return Result("not_enough_data", df, {}, path, checks, "fewer than 5 targets with effects on 2 programs")
    df = df.sort_values("q").reset_index(drop=True)
    top = df.iloc[0]
    sub = M[[top.program_a, top.program_b]].dropna().reset_index()
    sub["guide_mode"] = sub["target"].map(modes)
    checks = charts.checks(charts.two_scores(sub, top.program_a, top.program_b, path,
                                             "Two programs' effects across the targets",
                                             f"{top.program_a}, effect vs control cells",
                                             f"{top.program_b}, effect vs control cells",
                                             group="guide_mode" if sub["guide_mode"].nunique() > 1 else None), zero=True)
    n = int((df["q"] < 0.05).sum())
    return Result("detected" if n else "not_detected", df,
                  {"pairs": int(len(df)), "at_q_0.05": n,
                   "strongest": top[["guide_mode", "program_a", "program_b", "r", "q"]].to_dict()},
                  path, checks, f"{n} of {len(df)} program pairs correlate over the targets at q < 0.05; the "
                                f"strongest {top.program_a} with {top.program_b} ({top['guide_mode']}, r "
                                f"{top.r:.2f}, {int(top.targets)} targets)")


@register("E.co_functional_sets", Contract(
    serves="E.co_functional_sets",
    applies_when="the regulators of one program (C.programs), and their effects on the others",
    needs="C.programs' result table (target, score, effect), the program and the direction",
    returns="the regulators that move the program in the direction at q < 0.05 (the top ones where the table "
            "has no q; each mode apart where the screen has two), "
            "clustered by the correlation of their effects on the other programs (hierarchical, average linkage), "
            "into sets; per set its members and its mean effect on each program, so regulators that move one "
            "program alike are told apart by what else they move",
    fails_when=[("the number of sets is a cut of the tree", "the cut chosen by the silhouette, and stated"),
                ("a set of a few weak regulators can reflect noise",
                 "each set's members' effects on the program")],
    tested_against="synthetic: two groups of regulators lowering one program, one also raising a second program "
                   "and one a third, fall into two sets",
    choices={"top": [100, 50], "method": ["hierarchical", "hdbscan", "leiden"]},
    inputs={"effects": "C.programs' result table (target, score, effect), or its TSV path (default: the build's "
                       "latest programs or scores run)",
            "program": "the program the regulators move", "direction": "down (default) or up"}))
def co_functional_sets(screen, design, out_dir, effects=None, program=None, direction="down", top=100,
                       method="hierarchical"):
    from ..steps import effects_across_targets as eat
    from . import charts
    if effects is None:
        raise ValueError("E.co_functional_sets needs effects (C.programs' result table)")
    if program is None:
        held = pd.read_csv(effects, sep="\t") if isinstance(effects, str) else effects
        names = sorted(map(str, held["score"].unique())) if "score" in held.columns else []
        raise ValueError("E.co_functional_sets needs program, the one the regulators move: one of "
                         + (", ".join(names[:30]) + (" ..." if len(names) > 30 else "") if names else
                            "the scores of effects"))
    table = pd.read_csv(effects, sep="\t") if isinstance(effects, str) else effects
    M = _effects_matrix(table)
    Q = table.pivot(index="target", columns="score", values="q") if "q" in table else None  # one row per key: above
    modes = _modes_of(screen, design, M.index)
    sets_rows, members, left_out = [], [], []
    for mode in sorted(set(modes.values())):
        # a regulator not measured on a program is left out of the sets, not counted as no effect
        sub = M.loc[[t for t in M.index if modes[t] == mode]].dropna(axis=1, how="all")
        if program not in sub:
            continue
        lead = sub[program].sort_values(ascending=direction == "down")
        lead = lead[(lead < 0) if direction == "down" else (lead > 0)]
        if Q is not None and program in Q:  # the regulators that move it, not merely the top of a list
            lead = lead[Q[program].reindex(lead.index) < 0.05]
        lead = lead.head(top)
        others = [c for c in sub.columns if c != program]
        P, gone = eat.complete_rows(sub.loc[lead.index, others])
        left_out += [t for t in gone if t not in left_out]
        if len(P) < 4 or len(others) < 2:
            continue
        got, best = eat.clusters(P.to_numpy(), list(P.index), method)
        if got is None:
            continue
        labels = np.array([got[t] for t in P.index])
        stable = eat.stability(P.to_numpy(), list(P.index), got, method)
        for k in sorted(set(labels)):
            ts = list(P.index[labels == k])
            prof = sub.loc[ts].mean()
            name = f"{mode} set {k}" if k != -1 else f"{mode} unassigned"
            sets_rows.append({"guide_mode": mode, "set": name, "members": ";".join(ts), "n_targets": len(ts),
                              "silhouette": float(best) if best is not None else np.nan,
                              "stability": stable.get(k), "method": method,
                              **{f"effect_{c}": float(prof[c]) for c in sub.columns}})
            members += [{"guide_mode": mode, "set": name, "target": t} for t in ts]
    df = pd.DataFrame(sets_rows)
    path = os.path.join(out_dir, "figure.png")
    if df.empty:
        checks = figures.bars(np.zeros(1), ["none"], path, "Too few regulators to cluster", "targets")
        return Result("not_enough_data", df, {"left_out": left_out}, path, checks,
                      f"fewer than 4 regulators move {program} {direction} (at q < 0.05 where q is given), or "
                      "fewer than 2 other programs" + (f"; {len(left_out)} regulators not measured on every program "
                                                       "were left out" if left_out else ""))
    mem = pd.DataFrame(members)
    shown = M.loc[mem["target"]].fillna(0.0)
    checks = charts.checks(charts.heatmap(shown.to_numpy(), list(shown.index), list(shown.columns), path,
                                          f"Regulators of {program}, by their effects on the programs",
                                          "Effect vs control cells"), zero=True)
    return Result("measured", df, {"program": program, "direction": direction, "sets": int(len(df)),
                                   "left_out": left_out},
                  path, checks, f"{len(df)} sets among the regulators moving {program} {direction} "
                                f"(clustered by their effects on the other programs)",
                  tables={"members": mem})
