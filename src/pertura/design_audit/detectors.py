"""Detectors of a screen's design: facts with a value, a status and a source, and the questions the data leave open.

The design auditor is an agent: it reads names, metadata and papers, and proposes. These detectors do not guess
meaning from names or from metadata words. They measure what does not depend on naming, check proposals against the
data (checks.py), and ask where only reading can answer:
- feature_classes: features by their declared type; within each non-gene, non-guide type, whether one feature
  dominates each cell (the count pattern of hashtags) or features co-occur (the pattern of surface proteins), and
  among co-occurring features the few that each dominate cells of their own (hashtags among surface proteins);
- guide_names: guide names containing '|' (Cell Ranger's call separator), guides named exactly like genes, and
  whether the call file uses guide names or feature IDs;
- library: the guides, and the feature reference's target labels as they are. Guides labelled 'Non-Targeting',
  Cell Ranger's documented label, are the reported controls. Without labels, the code makes no target map: the
  agent proposes one, which check_design checks;
- moi: guides per cell, and targets per cell when the reference gives a target map;
- units: the candidate units the files show (samples; barcode suffixes of a combined matrix; hashtag groups), their
  balance, and pseudobulk reach given the reported controls (3 units shared with the reference, 10 cells in each);
- guide_capture_by_unit: units that capture far fewer guide UMIs per cell than the others, or whose cells' top guide
  holds a far smaller share of their guide UMIs;
- readouts: matrices that detect far fewer genes than the others (a targeted readout next to whole-transcriptome);
- pooled_donors: cells of both sexes in every unit (XIST against Y-linked genes) means donors pooled within units;
- conditions: every GEO sample characteristic that varies across samples, and samples whose expression splits in
  two clear groups;
- modality: the targets' own expression in the cells carrying their guides against the cells carrying control
  guides, when the reference gives the target map (evidence only; the user confirms). With the agent's map,
  check_design measures the same from the per-guide summaries that measure stores;
- object_columns: in a processed object, the cell-metadata columns whose values are the deposit's guide names, and
  the values in them that are not guides (such as 'WT').
Statuses: observed (from the data), reported (from the deposit's own files or metadata), inferred (from a measured
rule).
"""
import numpy as np
import pandas as pd
import scipy.sparse as sp

from .readers import GUIDE, NON_TARGETING, split_calls

HIGH_MOI = 0.30                       # share of cells with calls whose guides hit 2+ targets past which to ask
ONE_LABEL_SHARE = 0.5                 # a reference label covering more of the targeting guides is not used as a map
TOP_SHARE_MIN_UMIS = 10              # cells with fewer guide UMIs do not count towards the top-guide share
FEMALE = ("XIST", "Xist")
MALE = ("RPS4Y1", "DDX3Y", "UTY", "KDM5D", "EIF1AY", "USP9Y", "ZFY", "Ddx3y", "Eif2s3y", "Kdm5d", "Uty")


def fact(key, value, status, source, note=None):
    f = {"key": key, "value": value, "status": status, "source": source}
    if note:
        f["note"] = note
    return f


def question(key, text, why):
    return {"key": key, "question": text, "why_it_matters": why}


def _dense(x):
    return x.toarray() if sp.issparse(x) else np.asarray(x)


def _records(df):
    """{row: {column: value}} rounded to 2 decimals, with missing values as None (JSON has no NaN)."""
    return {str(i): {c: (None if pd.isna(v) else round(float(v), 2)) for c, v in r.items()}
            for i, r in df.iterrows()}


# 1. feature classes ------------------------------------------------------------------------------------------------
HASHTAG_DOMINANCE = 0.6  # the hashtag pattern: one feature holds 70%+ of the counts in more than this share of cells
MAX_HASHTAGS = 48        # more features than this are not read as one set of hashtags
TOPS_FLOOR = 0.01        # among co-occurring features, a hashtag tops at least this share of the cells,
FOLD = 5                 # holds there at least this many times its counts in the other cells and in each other
                         # hashtag's cells (as each other hashtag does against it),
MOST = 0.5               # and the hashtags together top more than this share of the cells


def dominance(counts):
    """For a cells x features table: the share of cells (with at least 10 counts) in which one feature holds at least
    70% of the counts, and the cells each feature tops (every feature of the table, 0 for those that top none)."""
    H = counts.to_numpy(float)
    t = H.sum(1)
    ok = t >= 10
    if ok.sum() < 50:
        return None, {}
    dom = float(np.mean(H[ok].max(1) / t[ok] >= 0.7))
    top = pd.Series(np.asarray(counts.columns)[H[ok].argmax(1)]).value_counts()
    return dom, {str(c): int(top.get(c, 0)) for c in counts.columns}


def hashtags_among(counts):
    """Hashtags among co-occurring features (a few hashtags in a panel of surface proteins), from the counts alone.
    A hashtag tops at least TOPS_FLOOR of the cells (with 10+ counts) and, in the cells it tops, holds FOLD times its
    counts in the other cells (medians, plus one). Hashtags exclude each other: each holds FOLD times its counts in
    each other hashtag's cells, so a protein raised in the cells of some hashtags (an activation marker) is left
    out. The features are taken by that fold, highest first, each where it is apart from those taken before. They
    are hashtags when they are 2 to MAX_HASHTAGS, together top more than MOST of the cells, and one of them holds
    70%+ of their counts in more than HASHTAG_DOMINANCE of the cells, as a group of hashtags does. Returns (the
    features, their measures, with the features left out and the hashtag each is not apart from), or ([], None)."""
    H = counts.to_numpy(float)
    H = H[H.sum(1) >= 10]
    if len(H) < 50:
        return [], None
    top = H.argmax(1)
    names = np.asarray(counts.columns).astype(str)
    fold = {}
    for j in np.flatnonzero(np.bincount(top, minlength=H.shape[1]) >= TOPS_FLOOR * len(H)):
        own = top == j
        if not own.all():
            fold[j] = (np.median(H[own, j]) + 1) / (np.median(H[~own, j]) + 1)

    def held(a, b):  # feature a's median count in the cells feature b tops, plus one
        return np.median(H[top == b, a]) + 1
    picked, left = [], {}
    for j in sorted((j for j, f in fold.items() if f >= FOLD), key=lambda j: -fold[j]):
        near = next((k for k in picked if held(j, j) < FOLD * held(j, k) or held(k, k) < FOLD * held(k, j)), None)
        if near is None:
            picked.append(j)
        else:
            left[str(names[j])] = f"not apart from {names[near]}"
    if not 2 <= len(picked) <= MAX_HASHTAGS:
        return [], None
    picked = sorted(picked)  # in the group's order
    cover = float(np.isin(top, picked).mean())
    dom, _ = dominance(counts.iloc[:, picked])
    if cover <= MOST or dom is None or dom <= HASHTAG_DOMINANCE:
        return [], None
    return [str(names[j]) for j in picked], {
        "they_top_share_of_cells": round(cover, 3), "one_dominates_share_of_cells": round(dom, 3),
        "fold_in_the_cells_each_tops": {str(names[j]): round(float(fold[j]), 1) for j in picked}, "left_out": left}


def feature_classes(var, other_counts=None):
    """var: the deposit's features (name, feature_types). other_counts: cells x non-gene, non-guide features."""
    ft = var["feature_types"].astype(str)
    names = var["name"].astype(str) if "name" in var else pd.Series(var.index, index=var.index)
    facts = [fact("data.feature_types", ft.value_counts().to_dict(), "observed", "features (declared types)")]
    qs, hashtags = [], []
    n_guides = int((ft == GUIDE).sum())
    if n_guides:
        facts.append(fact("perturbation.guide_readout", "direct_capture", "observed",
                          f"{n_guides} features declared '{GUIDE}'"))
    for t in sorted(set(ft) - {GUIDE, "Gene Expression"}):
        group = [n for n in names[ft == t] if other_counts is not None and n in other_counts.columns]
        if not group:
            continue
        dom, top = dominance(other_counts[group])
        # the cells each feature tops, so that a hashtag of few cells (a small animal, an unused tag) is seen, never
        # dropped: which features are the hashtags is the auditor's proposal and the user's confirmation
        if dom is not None and dom > HASHTAG_DOMINANCE and len(group) <= MAX_HASHTAGS:
            hashtags += list(group)
            facts.append(fact(f"data.features.{t}", {"n": len(group), "one_dominates_share_of_cells": round(dom, 3),
                                                     "role": "hashtag-like", "cells_each_tops": top}, "observed",
                              "count pattern", "one feature holds 70%+ of the counts in most cells, as hashtags do; "
                              "cells_each_tops: the cells (with 10+ counts) where each feature is the top one"))
            continue
        # a few hashtags in a panel of surface proteins: measured from the counts, never from the names
        among, how = hashtags_among(other_counts[group]) if dom is not None else ([], None)
        hashtags += among
        value = {"n": len(group), "one_dominates_share_of_cells": None if dom is None else round(dom, 3),
                 "role": "co-expressed (surface proteins?)", "cells_each_tops": top}
        note = "not guides: their counts do not identify perturbations"
        ask = (f"{len(group)} '{t}' features are co-expressed like surface proteins (one holds 70%+ of the counts in "
               f"{0 if dom is None else dom:.0%} of cells). Are some of them hashtags (sample or condition tags)? Which?")
        if among:
            value["role"] = "co-expressed (surface proteins?), with hashtag-like features among them"
            value["hashtag_like"] = {"features": among, **how}
            note += (f"; {len(among)} of them behave like hashtags: each tops cells of its own, where it holds {FOLD} "
                     "times or more its counts in the other cells and in each other one's cells, and one of them "
                     "holds 70%+ of their counts in most cells")
            nearly = "; ".join(f"{k}, {v}" for k, v in list(how["left_out"].items())[:4])
            ask = (f"{len(group)} '{t}' features are co-expressed like surface proteins, and {len(among)} of them behave "
                   f"like hashtags ({', '.join(among[:6])}{', ...' if len(among) > 6 else ''}): they top "
                   f"{how['they_top_share_of_cells']:.0%} of the cells." + (f" Left out: {nearly}." if nearly else "")
                   + " Are they hashtags (sample or condition tags)? Are there others?")
        facts.append(fact(f"data.features.{t}", value, "observed", "count pattern", note))
        qs.append(question(f"data.features.{t}", ask, "hashtags among proteins, or a sample whose hashtags stained "
                                                     "weakly, are invisible to a count test on the whole group"))
    if hashtags:
        qs.append(question("data.hashtags",
                           f"{len(hashtags)} features behave like hashtags ({', '.join(hashtags[:4])}). What do they "
                           "mark: conditions, replicates or donors?",
                           "hashtags are often the only record of conditions or biological replicates"))
    return facts, qs, hashtags


# 2. guide names ----------------------------------------------------------------------------------------------------
def guide_names(guides, gene_names, call_table=None, guide_ids=None):
    """guides: guide names; gene_names: expression feature names; guide_ids: guide feature IDs."""
    facts = []
    sep = [g for g in guides if "|" in g]
    if sep:
        facts.append(fact("data.guide_names.separator", {"n": len(sep), "examples": sep[:3]}, "observed",
                          "guide features", "names contain '|', the separator Cell Ranger puts between calls"))
    genes = set(gene_names)
    like = [g for g in guides if g in genes]
    if like:
        facts.append(fact("data.guide_names.gene_named", like[:10], "observed", "guide and gene features",
                          "guides named exactly like genes: making feature names unique would rename them"))
    if call_table is not None and len(call_table):
        tokens = pd.Series(call_table["feature_call"].astype(str).str.split("|").explode().unique())
        names, ids = set(guides), set(guide_ids or [])
        in_names = float(tokens.isin(names).mean())
        in_ids = float(tokens.isin(ids).mean()) if ids else 0.0
        value = "feature_ids" if in_ids > in_names and in_ids > 0.5 else "names"
        facts.append(fact("data.calls_naming", value, "observed", "call file",
                          f"{in_names:.0%} of call tokens are guide names, {in_ids:.0%} feature IDs"))
    return facts, []


# 3. library --------------------------------------------------------------------------------------------------------
def reference_labels(feature_ref):
    """{guide name: target_gene_name} from a feature reference, as written (empty labels left out)."""
    if feature_ref is None or not {"name", "target_gene_name"} <= set(feature_ref.columns):
        return {}
    ref = feature_ref.dropna(subset=["name"])
    return {str(n): str(t) for n, t in zip(ref["name"], ref["target_gene_name"].astype(str))
            if str(t) not in ("", "nan", "None")}


def library(guides, feature_ref=None):
    """The guide library as the deposit states it: (facts, questions, target_of). target_of maps each guide to its
    reference label, or to 'control' for guides labelled 'Non-Targeting'. It is empty, and the code measures without
    a map, when the reference labels under 90% of the guides, or when one label covers more than half of 20 or more
    targeting guides (GSE319266 labels all 1306 guides 'CD55'): the labels are then reported as they are, and the
    agent is asked for the map."""
    facts = [fact("perturbation.guides", {"n": len(guides), "examples": list(guides[:5])}, "observed",
                  "guide features")]
    qs = []
    lab = reference_labels(feature_ref)
    known = [g for g in guides if g in lab]
    if guides and len(known) >= 0.9 * len(guides):
        target_of = {g: ("control" if lab[g] == NON_TARGETING else lab[g]) for g in known}
        per = pd.Series([t for t in target_of.values() if t != "control"]).value_counts()
        ctrl = [g for g in known if target_of[g] == "control"]
        k = int(pd.Series(per.values).mode().iloc[0]) if len(per) else 0  # the modal number of guides per target
        facts += [fact("perturbation.targets", int(len(per)), "reported", "feature reference (target_gene_name)"),
                  fact("perturbation.guides_per_target", k, "reported", "feature reference (target_gene_name)",
                       None if per.nunique() <= 1 else f"from {int(per.min())} to {int(per.max())}"),
                  fact("perturbation.controls", {"n_guides": len(ctrl), "examples": ctrl[:3]}, "reported",
                       f"feature reference: target_gene_name '{NON_TARGETING}'")]
        if len(known) < len(guides):
            facts.append(fact("perturbation.unlabelled_guides", [g for g in guides if g not in lab][:10], "observed",
                              "guides the feature reference does not label"))
        targeting = int(per.sum()) if len(per) else 0
        if targeting >= 20 and per.iloc[0] > ONE_LABEL_SHARE * targeting:
            facts.append(fact("perturbation.reference_labels", {"guides": len(known), "labels": int(len(per)),
                                                               "largest": {str(per.index[0]): int(per.iloc[0])}},
                              "reported", "feature reference (target_gene_name)",
                              "one label covers most targeting guides; measured without a target map"))
            qs.append(question("perturbation.target_map",
                               f"The feature reference gives {int(per.iloc[0])} of {targeting} targeting guides the "
                               f"same target label ('{per.index[0]}'). Is that each guide's target (guides tiling one "
                               "gene's regions or variants), or a placeholder? Propose perturbation.target_map: which "
                               "target each guide hits, and which guides are controls.",
                               "every target group, the MOI and the reference depend on it"))
            return facts, qs, {}
        qs.append(question("perturbation.controls",
                           f"The feature reference labels {len(ctrl)} guides '{NON_TARGETING}' (e.g. "
                           f"{', '.join(ctrl[:3])}). Are they the controls, and of which kind: non-targeting, or "
                           "cutting (safe-targeting, intergenic)? Is any labelled target a control too (a "
                           "gene-targeting negative control)?",
                           "the reference and every hidden check are built from the controls"))
        return facts, qs, target_of
    qs.append(question("perturbation.target_map",
                       f"No feature reference labels the targets of the {len(guides)} guides. Which target does "
                       "each guide hit, and which guides are controls (propose perturbation.target_map and "
                       "perturbation.controls)?", "every target group and the reference depend on it"))
    return facts, qs, {}


# 4. MOI ------------------------------------------------------------------------------------------------------------
def moi(calls, target_of, n_without):
    """calls: barcode -> [(guide, umis)] for every cell with a call; n_without: cells without any. Targets per cell
    need a target map (target_of); without one, guides per cell stand in."""
    n_guides = np.array([len({g for g, _ in v}) for v in calls.values()])
    if not len(n_guides):
        return [fact("perturbation.moi", "unknown", "observed", "calls", "no cell has a guide call")], [
            question("perturbation.assignment", "No guide calls were found. How were guides assigned to cells?",
                     "without calls or guide UMIs no cell can be assigned")]
    value = {"cells_with_calls": int(len(n_guides)), "cells_without": int(n_without),
             "one_guide": round(float((n_guides == 1).mean()), 3),
             "two_or_more_guides": round(float((n_guides > 1).mean()), 3)}
    if target_of:
        n_targets = np.array([len({target_of.get(g, g) for g, _ in v}) for v in calls.values()])
        multi, what = float((n_targets > 1).mean()), "targets"
        value["two_or_more_targets"] = round(multi, 3)
    else:
        multi, what = value["two_or_more_guides"], "guides (no target map yet)"
    value["basis"] = what
    qs = []
    if multi > HIGH_MOI:
        qs.append(question("perturbation.moi",
                           f"{multi:.0%} of cells with calls carry two or more {what.split(' ')[0]}. Is this a high-MOI "
                           "design (then a high-MOI method), or are ambient guides being called?",
                           "a low-MOI analysis removes these cells; ambient calls mis-assign cells"))
    return [fact("perturbation.moi", value, "observed", "guide calls")], qs


# 5. units ----------------------------------------------------------------------------------------------------------
def units(unit, perturbation, label):
    """unit: per-cell candidate unit; perturbation: per-cell target ('control', a target, or '' unassigned). Without
    any control cell (no target map yet), pseudobulk reach is left to check_design, which uses the proposed
    controls."""
    unit = pd.Series(np.asarray(unit, dtype=str))
    pert = pd.Series(np.asarray(perturbation, dtype=str))
    unit, pert = unit[unit != ""], pert[unit != ""]
    sizes = unit.value_counts()
    n = int(len(sizes))
    facts = [fact("units.candidates", {"by": label, "n": n, "cells": sizes.to_dict()}, "observed", label)]
    qs = []
    if n > 1:
        small = sizes[sizes < 0.10 * sizes.median()]
        if len(small):
            facts.append(fact("units.unbalanced", small.to_dict(), "observed", label,
                              "units with under 10% of the median unit's cells"))
    tested = None
    if (pert == "control").any():
        n_ref, tested = pseudobulk_reach(unit, pert)
        from .. import policy
        least, replicates = policy.get("min_cells"), policy.get("min_replicates")
        facts.append(fact("units.pseudobulk_reach", {"units_with_enough_control_cells": n_ref,
                                                     "targets_testable_pooled": round(tested, 3)}, "observed",
                          "cells per unit", f"pseudobulk here needs {replicates} units shared with the reference, "
                                            f"{least} cells in each (pertura.policy)"))
    if n > 1:
        qs.append(question("units.independent_unit",
                           f"Are the {n} {label} independent replicates (mice, donors, separate cultures) or "
                           "technical splits of one pool (lanes, wells)?",
                           "each result's n names the biological replicates (mice, donors) apart from technical "
                           "ones (lanes, wells)"))
    if tested is not None and tested < 0.5:
        qs.append(question("units.pseudobulk",
                           f"With these {label}, pseudobulk can test {tested:.0%} of the targets. Are there other "
                           "samples (hashtag replicates, donors) the files do not show?",
                           "pseudobulk by sample needs several samples that hold both the target's and the control "
                           "cells"))
    return facts, qs, (tested if tested is not None else 0.0)


def pseudobulk_reach(unit, perturbation):
    """(units with at least min_cells control cells, share of targets with min_cells cells in at least min_replicates
    of those units), with pertura.policy's numbers in effect. unit, perturbation: per cell ('control', a target, or
    '')."""
    from .. import policy
    least, replicates = policy.get("min_cells"), policy.get("min_replicates")
    unit = pd.Series(np.asarray(unit, dtype=str))
    pert = pd.Series(np.asarray(perturbation, dtype=str))
    keep = unit != ""
    unit, pert = unit[keep], pert[keep]
    ctrl = pert == "control"
    ref_ok = set(unit[ctrl].value_counts().loc[lambda x: x >= least].index)
    t = pert[(pert != "control") & (pert != "")]
    if not len(t):
        return len(ref_ok), 0.0
    per = pd.crosstab(t, unit[t.index])
    usable = per.ge(least) & pd.DataFrame(np.tile(per.columns.isin(list(ref_ok)), (len(per), 1)),
                                          index=per.index, columns=per.columns)
    return len(ref_ok), float((usable.sum(axis=1) >= replicates).mean())


def guide_capture_by_unit(G, unit):
    """Per unit, the median guide UMIs per cell, and the median share of a cell's guide UMIs on its top guide over
    cells with at least TOP_SHARE_MIN_UMIS guide UMIs (a cell with one UMI has a share of 1). A unit under 20% of the
    median unit's guide UMIs, or under half its top-guide share, captures guides poorly, and any caller will
    mis-assign its cells (lane A2 of GSE263761: a median of 49 guide UMIs per cell against 704)."""
    M = G.to_numpy(float)
    tot = M.sum(1)
    if M.shape[1]:
        share = np.where(tot >= TOP_SHARE_MIN_UMIS, M.max(1) / np.maximum(tot, 1), np.nan)
    else:
        share = np.full(len(tot), np.nan)
    df = pd.DataFrame({"unit": np.asarray(unit, dtype=str), "guide_umis": tot, "top_share": share})
    g = df.groupby("unit")
    med = pd.DataFrame({"guide_umis": g["guide_umis"].median(), "top_share": g["top_share"].median(),
                        "cells_for_share": g["top_share"].count()})
    med.loc[med["cells_for_share"] < 20, "top_share"] = np.nan  # too few cells to judge the share
    facts = [fact("units.guide_capture", _records(med[["guide_umis", "top_share"]]), "observed",
                  "guide UMIs per cell", f"top_share: median share of the top guide, cells with at least "
                                         f"{TOP_SHARE_MIN_UMIS} guide UMIs")]
    qs = []
    weak = med[(med["guide_umis"] < 0.2 * med["guide_umis"].median()) |
               (med["top_share"] < 0.5 * med["top_share"].median())]
    if len(med) > 1 and len(weak):
        facts.append(fact("units.weak_guide_capture", _records(weak[["guide_umis", "top_share"]]),
                          "observed", "guide UMIs per cell",
                          "under 20% of the median unit's guide UMIs per cell, or under half its top-guide share"))
        qs.append(question("units.weak_guide_capture", f"{', '.join(weak.index)} captured guides poorly (few guide "
                           "UMIs per cell, or a small share on each cell's top guide). Keep their cells, or treat "
                           "their guide calls as unreliable?",
                           "a weak guide library makes callers call ambient guides"))
    return facts, qs


def readouts(genes_detected):
    """genes_detected: per sample, the genes detected in at least 1% of its cells. Samples detecting under a third of
    the others' median look like a targeted readout (e.g. TAP-seq) next to whole-transcriptome matrices."""
    s = pd.Series(genes_detected)
    if len(s) < 2:
        return [], []
    low = s[s < s.median() / 3]
    if not len(low):
        return [fact("data.readouts", "one", "observed", "genes detected per sample")], []
    return [fact("data.readouts", {"targeted_looking": low.to_dict(), "others_median": int(s.median())}, "observed",
                 "genes detected per sample", "some matrices detect far fewer genes: a targeted panel?")], [
        question("data.readouts", f"{len(low)} matrices detect far fewer genes ({', '.join(list(low.index)[:3])}...). "
                 "Are they a targeted readout of the same cells, to be analysed apart?",
                 "two readouts of one sample counted as two samples double the units")]


# 6. pooled donors --------------------------------------------------------------------------------------------------
def pooled_donors(X, gene_names, unit):
    names = np.asarray(gene_names).astype(str)
    X = sp.csr_matrix(X)
    f = np.asarray(X[:, np.flatnonzero(np.isin(names, FEMALE))].sum(1)).ravel()
    m = np.asarray(X[:, np.flatnonzero(np.isin(names, MALE))].sum(1)).ravel()
    sex = pd.Series(np.where((f > 0) & (m == 0), "female", np.where((m > 0) & (f == 0), "male", "")))
    unit = pd.Series(np.asarray(unit, dtype=str))
    called = sex != ""
    if called.mean() < 0.2:
        return [fact("units.donor_sex", "undetermined", "observed", "XIST and Y-linked genes",
                     f"only {called.mean():.0%} of cells show sex genes")], []
    share = sex[called].eq("female").groupby(unit[called]).mean()
    value = {"female_share_by_unit": share.round(2).to_dict(), "cells_assignable": round(float(called.mean()), 3)}
    if ((share > 0.15) & (share < 0.85)).all():
        return [fact("units.pooled_donors", value, "observed", "XIST and Y-linked genes",
                     "both sexes in every unit: donors or mice are pooled within each unit")], [
            question("units.donors", "Every unit holds cells of both sexes. How many donors or mice were pooled, and "
                     f"is there a genotype demultiplexing? (sex genes assign {called.mean():.0%} of cells)",
                     "the pooled mice or donors are the biological replicates; lanes split from the pool are "
                     "technical replicates")]
    note = "units of one sex each: units may be separate donors or mice" if ((share < 0.15) | (share > 0.85)).all() \
        and share.nunique() > 1 else None
    return [fact("units.donor_sex", value, "observed", "XIST and Y-linked genes", note)], []


# 7. conditions -----------------------------------------------------------------------------------------------------
def conditions(geo=None, sample_profiles=None):
    """Every GEO sample characteristic that varies across samples (reported), and samples whose expression splits
    in two clear groups (observed)."""
    facts, qs = [], []
    if geo:
        by = {}
        for s in geo["samples"].values():
            for k, v in s["characteristics"].items():
                by.setdefault(k, set()).add(v[0])
        varying = {k: sorted(v) for k, v in by.items() if 1 < len(v) <= 24}
        if varying:
            facts.append(fact("experiment.sample_characteristics", varying, "reported", "GEO samples",
                              "characteristics that vary across samples: conditions, units or library types"))
            qs.append(question("experiment.conditions", f"Which of these sample characteristics are conditions "
                               f"(each analysed apart): {', '.join(varying)}?", "pooling conditions mixes their effects"))
    if sample_profiles is not None and len(sample_profiles) >= 4:
        from scipy.cluster.hierarchy import fcluster, linkage
        from scipy.spatial.distance import squareform
        P = np.log1p(sample_profiles.to_numpy(float))
        with np.errstate(invalid="ignore", divide="ignore"):
            c = np.nan_to_num(np.corrcoef(P - P.mean(0)), nan=0.0)  # a constant profile correlates with nothing
        np.fill_diagonal(c, 1.0)
        lab = fcluster(linkage(squareform(1 - c, checks=False), "average"), 2, "maxclust")
        if len(set(lab)) == 2:
            same = lab[:, None] == lab[None, :]
            off = ~np.eye(len(lab), dtype=bool)
            within, between = float(c[same & off].mean()), float(c[~same].mean())
            if within - between > 0.5:
                groups = {int(g): [str(n) for n, x in zip(sample_profiles.index, lab) if x == g] for g in set(lab)}
                facts.append(fact("experiment.expression_groups", groups, "observed", "sample expression profiles",
                                  f"correlation within groups {within:.2f}, between {between:.2f}"))
                qs.append(question("experiment.expression_groups",
                                   f"The samples split in two expression groups ({groups}). Are these conditions?",
                                   "pooling conditions mixes their effects; each condition is analysed apart"))
    return facts, qs


# 8. modality -------------------------------------------------------------------------------------------------------
def on_target(summary, target_of, all_genes, gene_of=None):
    """The targets' own expression in the cells carrying their guides against the cells carrying control guides (log2
    fold change of mean CP10k), from per-guide summaries of the expression subset (summary: guides, genes, sum,
    detected, cells, basis; audit.guide_expression). The summary counts cells with one called guide, or, when most
    cells carry several, every cell carrying the guide (basis). A target counts when its gene is detected in at least
    5% of the control cells and at least 10 cells carry its guides. The values are reported as they are, with each
    target's log2 fold change: knockdown is what CRISPRi does, activation what CRISPRa does, and a knockout often
    leaves the mRNA (a cut allele still makes it); the user confirms the modality. target_of: guide -> target or
    'control'; gene_of: guide -> its
    gene (perturbation.guides), for targets whose names are not genes (such as GATA1-CRISPRa). Returns (value, targets
    that are not genes of the matrix, targets)."""
    targets = sorted({t for t in target_of.values() if t != "control"})
    known = set(np.asarray(all_genes).astype(str))
    not_genes = [t for t in targets if t not in known]
    if summary is None:
        return "not measured (the library is too large for the per-guide summary)", not_genes, targets
    gi = {g: k for k, g in enumerate(np.asarray(summary["guides"]).astype(str))}
    col = {g: k for k, g in enumerate(np.asarray(summary["genes"]).astype(str))}
    S, D, n = summary["sum"], summary["detected"], np.asarray(summary["cells"], float)
    ctrl = [gi[g] for g, t in target_of.items() if t == "control" and g in gi]
    n_c = n[ctrl].sum() if ctrl else 0
    if not targets or n_c == 0:
        return "not measurable (no control cells)" if targets else "not measurable (no targets)", not_genes, targets
    S_c, D_c = S[ctrl].sum(0), D[ctrl].sum(0)
    by = {}
    for g, t in target_of.items():
        if t != "control" and g in gi:
            by.setdefault(t, []).append(gi[g])
    lfc, mapped = {}, 0
    for t, rows in by.items():
        j = col.get(t)
        if j is None and gene_of:  # the target's gene from its guides' genes, the commonest
            genes = [gene_of[g] for g, x in target_of.items() if x == t and gene_of.get(g)]
            j = col.get(max(set(genes), key=genes.count)) if genes else None
        n_t = n[rows].sum()
        mapped += j is not None
        if j is None or n_t < 10 or D_c[j] < 0.05 * n_c:
            continue
        lfc[t] = float(np.log2((S[rows, j].sum() / n_t + 0.01) / (S_c[j] / n_c + 0.01)))
    if not lfc:
        why = ("no target gene is detected in 5% of control cells" if mapped else
               "no target is a gene of the matrix, and no guide's gene is given in perturbation.guides")
        return f"not measurable ({why})", not_genes, targets
    v = pd.Series(lfc)
    down, up, med = float((v < -0.5).mean()), float((v > 0.5).mean()), float(v.median())
    return ({"targets_measured": len(v), "median_log2fc": round(med, 2), "share_down_past_0.5": round(down, 2),
             "share_up_past_0.5": round(up, 2), "log2fc_by_target": {str(k): round(float(x), 2) for k, x in v.items()},
             "cells": str(summary.get("basis", ""))}, not_genes, targets)


def modality(summary, target_of, all_genes):
    """On-target expression with the feature reference's target map, when it gives one."""
    facts, qs = [], []
    if not target_of:
        facts.append(fact("perturbation.on_target", "needs the target map (check_design measures it with the "
                          "proposed map)", "observed", "expression"))
    else:
        value, not_genes, targets = on_target(summary, target_of, all_genes)
        if targets and len(not_genes) > 0.5 * len(targets):
            facts.append(fact("perturbation.targets_are_genes", False, "observed", "target labels and gene names",
                              f"{len(targets) - len(not_genes)} of {len(targets)} target labels are genes of the "
                              "matrix: regions or variants?"))
            qs.append(question("perturbation.targets_kind", "Most target labels are not genes of the matrix. Are "
                               "they regulatory regions or variants, and which genes should each affect?",
                               "on-target checks need the expected gene for each target"))
        facts.append(fact("perturbation.on_target", value, "inferred", "targets' own expression"))
    qs.append(question("perturbation.modality", "Confirm the modality: knockout, CRISPRi, CRISPRa, base editing, or mixed (guides of different modes in one library: then each guide's mode, in perturbation.guides).",
                       "the gate needs it confirmed"))
    return facts, qs


# 9. processed objects ----------------------------------------------------------------------------------------------
def object_columns(obs, guide_names):
    """The cell-metadata columns whose values are the deposit's guide names (comma-separated lists allowed), and the
    values in them that are not guides."""
    known = set(guide_names)
    facts, qs = [], []
    for c in obs.columns:
        v = obs[c].astype(str)
        uniq = pd.Series(v.unique())
        parts = uniq.str.split(",").explode().str.strip()
        hit = float(parts.isin(known).mean()) if len(parts) else 0.0
        if hit < 0.5:
            continue
        vc = v.value_counts()
        not_guides = {x: int(n) for x, n in vc.items() if not all(p.strip() in known for p in str(x).split(","))}
        multi = float(v.str.contains(",").mean())
        facts.append(fact(f"data.object.guide_column.{c}", {"values_matching_guides": round(hit, 3),
                                                            "cells_with_several": round(multi, 3),
                                                            "values_not_guides": dict(list(not_guides.items())[:5])},
                          "observed", "cell metadata against the guide list"))
        if not_guides:
            qs.append(question(f"data.object.{c}", f"Column '{c}' holds values that are not guides: "
                               f"{dict(list(not_guides.items())[:4])}. What do they mean (no guide, untransduced)?",
                               "read as targets they make fake target groups"))
    if not facts:
        qs.append(question("data.object", "No cell-metadata column holds the deposit's guide names. Which column is "
                           "the per-cell guide assignment?", "without it no cell can be assigned"))
    return facts, qs


def parse_calls(table, guides, guide_ids=None):
    """A call table as barcode -> [(guide, umis)], splitting calls whose names contain '|' and mapping feature IDs
    to names."""
    rename = dict(zip(guide_ids, guides)) if guide_ids is not None else {}
    known = set(guides) | set(rename)
    out = {}
    # pandas 3 keeps missing values as NaN through astype(str): a call table without UMIs (cells_per_protospacer.json)
    # has NaN or None in num_umis, so every value is tested for being text
    for bc, calls, n, umis in zip(table["cell_barcode"].astype(str), table["feature_call"],
                                  table["num_features"].astype(int), table["num_umis"]):
        if not isinstance(calls, str):
            continue
        if not isinstance(umis, str):  # a number (numpy or not), or missing (None, NaN)
            umis = "" if umis is None or (isinstance(umis, float) and umis != umis) else str(umis)
        u = [int(float(x)) for x in umis.split("|")] if umis not in ("None", "nan", "") else [1] * n
        names = calls.split("|") if len(calls.split("|")) == n else split_calls(calls, n, known)
        if names is None or len(names) != len(u):
            continue
        out[bc] = [(rename.get(g, g), x) for g, x in zip(names, u)]
    return out
