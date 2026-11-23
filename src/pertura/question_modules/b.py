"""Question B: who was perturbed, and how well."""
import os

import numpy as np
import pandas as pd
import scipy.sparse as sp

from ..most_common import most_common_value
from . import figures
from .contract import REGISTRY, Contract, Result, register
from .data import seed_of, value

MIN_CELLS = 10
ALL_UNITS = "all units"  # B.efficacy's row of a guide over the units holding both sides, pooled
MIN_GROUP = 3  # Seurat's FindMarkers minimum group size (min.cells.group), which Mixscape's marker step uses
# the line of an on-target change: the target gene down by at least 30%, Replogle et al. 2022's CRISPRi filter (or
# up by the same factor under CRISPRa, Pertura's choice); a guide is described by where its change falls against it,
# never judged
EFFICACY_LOG2FC = round(float(-np.log2(0.7)), 3)
NOT_MEASURABLE, TRANSCRIPT_UNCHANGED, TRANSCRIPT_UP = "not measurable", "transcript unchanged", "transcript up"
# the knowledge base's PF7 where a knockout's own gene is read: a worker read FOS's guides raising JUNB, NR4A1 and ID4
# as an artefact because they raised FOS's own transcript (a GSE250558 run); the card sat unread
KNOCKOUT_READING = ("Under knockout a guide's own transcript says little: it can stay, rise or change while the gene "
                    "is cut (it escapes nonsense-mediated decay, the repair is in frame, or its mRNA is misregulated). "
                    "The evidence that a knockout worked is its downstream signature, its guides' agreement and its "
                    "protein where measured (PF7: Dixit et al. 2016, Otto et al. 2023, Tuladhar et al. 2019)")
# what the line can say, in each result's summary (docs/adr/0045, amendment 1): the perturbation's evidence, its
# downstream phenotype and the share of cells that respond are read apart (T3)
LINE_READING = ("the line describes a knockdown (or a rise under CRISPRa) and is no test of whether a guide acts: a "
                "guide short of it is read by its downstream signature and its target's other guides, and the cells "
                "that respond by B.escape")


def line_words(sign, threshold=EFFICACY_LOG2FC):
    """The two sides of the line for a guide that should lower (sign -1) or raise (+1) its target gene, in words:
    (past it, short of it); for a guide whose direction the design does not say, both are that."""
    if sign < 0:
        down = round(100 * (1 - 2 ** -threshold))
        return f"at least {down}% down", f"less than {down}% down"
    if sign > 0:
        up = 2 ** threshold
        return f"at least {up:.2f} times up", f"less than {up:.2f} times up"
    return "no expected direction", "no expected direction"


def modality_of(design, guide):
    """The guide's modality code (crispri, crispra, crispr_ko, ...), or None when the design does not say: its mode
    (perturbation.guides, or an earlier design's perturbation.guide_modes), else the single modality."""
    from ..design_gate import guide_attributes, modality_code
    from ..record import modalities
    mode = guide_attributes(design).get(str(guide), {}).get("mode")
    code = (modality_code(mode) or "other") if mode else None  # a mode given but not known: no expected direction
    if code is None:
        mods = modalities(design)
        code = next(iter(mods)) if len(mods) == 1 else None
    return code


def expected_sign(design, guide):
    """-1 where the guide should lower its target gene (CRISPRi, knockout), +1 where it should raise it (CRISPRa), 0
    when the design does not say."""
    return {"crispri": -1, "crispr_ko": -1, "crispra": 1}.get(modality_of(design, guide), 0)


def _empty(out_dir, title, status, summary, table=None):
    path = os.path.join(out_dir, "figure.png")
    checks = figures.bars(np.zeros(1), ["none"], path, title, "cells")
    return Result(status, table if table is not None else pd.DataFrame(), {}, path, checks, summary)


# ---- guide calls ---------------------------------------------------------------------------------------------------
@register("B.guide_calls_and_their_evidence", Contract(
    serves="B.guide_calls_and_their_evidence",
    applies_when="always",
    needs="the screen object's labels, units and each cell's guides with their UMIs",
    returns="per unit and kind of label (one target, two or more targets, control cells, ambiguous or several, no "
            "guide): the share of the unit's cells, with the median guide UMIs of the unit's single-target cells and "
            "the share of them on 2 UMIs or fewer; cells_per_guide.tsv: each guide's cells (its cell's top guide) per "
            "unit; in captures of pairs, per unit, the single-target cells with their partner guide called and "
            "without it",
    fails_when=[("calls on one or two UMIs may be stray guide RNA",
                 "the share of calls with 2 or fewer UMIs, per unit"),
                ("one sample's ambient guide RNA can make many of its cells ambiguous",
                 "the ambiguous share per unit")],
    tested_against="zebrafish: the cells without a guide and those with several targets or none dominant, "
                   "against an earlier hand count"))
def guide_calls(screen, design, out_dir):
    from .a import kind_of
    df = pd.DataFrame({"unit": screen.unit, "kind": [kind_of(x) for x in screen.label]})
    share = pd.crosstab(df["unit"], df["kind"], normalize="index").stack().rename("share").reset_index()
    single = screen.single()
    rows = []
    for u in screen.units():
        idx = np.flatnonzero(single & (screen.unit == u))
        umis = np.array([sum(x for _, x in screen.guide_lists[i]) for i in idx])
        if len(umis):
            rows.append({"unit": u, "median_umis": float(np.median(umis)),
                         "share_at_most_2": round(float(np.mean(umis <= 2)), 3), "single_target_cells": len(umis)})
    support = pd.DataFrame(rows, columns=["unit", "median_umis", "share_at_most_2", "single_target_cells"])
    per_guide = (pd.DataFrame({"guide": screen.top_guide, "unit": screen.unit, "label": screen.label})
                 [lambda d: d["guide"] != ""].groupby(["guide", "unit"]).size().rename("cells").reset_index())
    path = os.path.join(out_dir, "figure.png")
    checks = figures.dots(share, "kind", "share", "unit", path, "Cells by kind of label, per unit",
                          "Share of the unit's cells", reference=None)
    counts = df["kind"].value_counts().to_dict()
    table = share.merge(support, on="unit", how="left")
    diagnostics = {"cells_by_kind": {k: int(v) for k, v in counts.items()},
                   "share_on_2_umis_or_fewer_by_unit": dict(zip(support["unit"], support["share_at_most_2"]))}
    status = "detected" if counts.get("one target", 0) + counts.get("two or more targets", 0) >= MIN_CELLS else \
        "not_enough_data"
    several = sorted(set(df["unit"][df["kind"] == "two or more targets"]))
    where = f" (in {', '.join(map(str, several[:8]))}{', ...' if len(several) > 8 else ''})" if several else ""
    partner = ""
    if (screen.partner != "").any():  # captures of pairs: a single without its partner may be an uncalled pair
        p = pd.crosstab(screen.unit[screen.partner != ""], screen.partner[screen.partner != ""])
        diagnostics["single_target_cells_in_pair_captures"] = {
            str(u): {f"partner {k}": int(n) for k, n in r.items()} for u, r in p.iterrows()}
        seen, alone = int((screen.partner == "seen").sum()), int((screen.partner == "not seen").sum())
        partner = (f"; in the captures of pairs, {seen + alone} single-target cells: {seen} with their partner guide "
                   f"called, {alone} without")
    return Result(status, table, diagnostics, path, checks,
                  f"{counts.get('one target', 0)} cells with one target, {counts.get('two or more targets', 0)} with "
                  f"two or more{where}, {counts.get('control cells', 0)} control cells, "
                  f"{counts.get('ambiguous or several', 0)} ambiguous or several, {counts.get('no guide', 0)} without "
                  f"a guide{partner}", tables={"cells_per_guide": per_guide})


# ---- vector structure ----------------------------------------------------------------------------------------------
@register("B.vector_structure", Contract(
    serves="B.vector_structure",
    applies_when="vectors of two or more guides, or two guides per cell by design",
    needs="each cell's guides, and each guide's target (guides.tsv)",
    returns="per target and unit: the share of its single-target cells carrying two or more of its guides",
    fails_when=[("removing cells with two guides would remove a two-guide vector's cells",
                 "this share: high means two guides per vector")],
    tested_against="ILF3: the share of single-target cells that carry both of their target's guides, against an "
                   "earlier hand count"))
def vector_structure(screen, design, out_dir):
    target_of = dict(zip(screen.guides["guide"].astype(str), screen.guides["target"].astype(str)))
    rows = []
    for i in np.flatnonzero(screen.single()):
        own = {g for g, _ in screen.guide_lists[i] if target_of.get(g) == screen.label[i]}
        rows.append({"unit": screen.unit[i], "target": screen.label[i], "two_or_more": len(own) >= 2})
    df = pd.DataFrame(rows)
    if df.empty:
        return _empty(out_dir, "No single-target cells", "not_enough_data", "no single-target cells")
    per = df.groupby(["unit", "target"])["two_or_more"].mean().rename("share").reset_index()
    path = os.path.join(out_dir, "figure.png")
    checks = figures.dots(per, "target", "share", "unit", path,
                          "Single-target cells carrying 2+ of their target's guides, per unit",
                          "Share of cells with 2+ of\ntheir target's guides", reference=None)
    overall = float(df["two_or_more"].mean())
    return Result("measured", per, {"share_overall": round(overall, 3)}, path, checks,
                  f"{overall:.0%} of single-target cells carry two or more of their target's guides")


# ---- efficacy ------------------------------------------------------------------------------------------------------
POWER = 0.8


def detection_power(n_guide, n_reference, p0, sign, alpha=0.05):
    """The power to see the expected change of the share of cells detecting the target gene: a halving (repression, or
    a mode not given), or under activation a rise to twice the share or the share plus 0.1, whichever is larger (at
    most 1: a gene silent in the reference can still be seen to switch on); a one-sided two-proportion z-test at
    alpha, with n_guide cells against n_reference."""
    from scipy.stats import norm
    p1 = min(1.0, max(2 * p0, p0 + 0.1)) if sign > 0 else p0 / 2
    if p1 == p0 or n_guide < 1 or n_reference < 1 or (sign <= 0 and p0 <= 0):
        return 0.0
    pbar = (n_guide * p1 + n_reference * p0) / (n_guide + n_reference)
    null_se = np.sqrt(pbar * (1 - pbar) * (1 / n_guide + 1 / n_reference))
    alt_se = np.sqrt(p1 * (1 - p1) / n_guide + p0 * (1 - p0) / n_reference)
    return float(norm.cdf((abs(p1 - p0) - norm.isf(alpha) * null_se) / max(alt_se, 1e-12)))


def own_gene_effects(screen, min_cells=None, reference="control_guides", method="pooled_cpm"):
    """Per guide and unit, and over all units pooled (the units holding both sides; unit ALL_UNITS): the target gene in
    the single-target cells whose top guide it is, against the reference cells: the log2 fold change of counts per
    million (pooled; steps.on_target), the difference of mean log-normalised expression, the shares detecting it, and
    whether the expected change would be visible (detection_power at least POWER); with method, the change by that
    formula of steps.on_target as well. A row needs min_cells (Pertura's number in effect) of each side's cells where
    it is measured: in a unit for a unit's row, over the units for the pooled row."""
    from .. import policy
    min_cells = policy.get("min_cells") if min_cells is None else min_cells
    from ..steps import on_target
    ref = screen.reference(reference)
    single = screen.single()
    L = screen.lognorm
    rows = []
    for _, r in screen.guides[screen.guides["kind"] == "targeting"].iterrows():
        g, t = str(r["guide"]), str(r["target"])
        own = screen.own_gene_of(t)
        cells = single & (screen.top_guide == g)
        ref_t = screen.reference(reference, target=t) if screen.reference_matches else ref  # its arm's (ADR 0026)
        j = screen.gene_index.get(own) if own else None
        expr = screen.gene(own) if own else None
        ln = np.asarray(L[:, j].todense()).ravel() if j is not None else None
        held = [u for u in screen.units() if (cells & (screen.unit == u)).any() and (ref_t & (screen.unit == u)).any()]
        places = [(u, cells & (screen.unit == u), ref_t & (screen.unit == u)) for u in screen.units()]
        if len(held) > 1:
            places.append((ALL_UNITS, cells & np.isin(screen.unit, held), ref_t & np.isin(screen.unit, held)))
        for u, mt, mr in places:
            if screen.crossed:  # its control cells drawn within its samples' conditions, in its mix
                mt, mr, _ = screen.matched(mt, mr & ~mt, min_cells, seed_of(g))
            if mt.sum() < min_cells or mr.sum() < min_cells:
                continue
            row = {"guide": g, "target": t, "own_gene": own or "", "unit": u, "cells": int(mt.sum()),
                   "reference_cells": int(mr.sum())}
            if expr is None:
                rows.append({**row, "log2fc": np.nan, "lognorm_difference": np.nan, "detected_guide": np.nan,
                             "detected_reference": np.nan, "measurable": False,
                             **({} if method == "pooled_cpm" else {"change": np.nan, "formula": method})})
                continue
            det_r = float((expr[mr] > 0).mean())
            power = detection_power(int(mt.sum()), int(mr.sum()), det_r, expected_sign(screen.design, g))
            more = {} if method == "pooled_cpm" else {
                "change": on_target.change(screen, own, mt, mr, method), "formula": method}
            rows.append({**row, "log2fc": on_target.change(screen, own, mt, mr, "pooled_cpm"), **more,
                         "lognorm_difference": float(ln[mt].mean() - ln[mr].mean()),
                         "detected_guide": float((expr[mt] > 0).mean()), "detected_reference": det_r,
                         "power": round(power, 3), "measurable": power >= POWER})
    return pd.DataFrame(rows)


def place(log2fc, sign, threshold, knockout=False):
    """Where one change falls against the line: (its words, past it or not). A knockout's change short of it is
    'transcript unchanged', or 'transcript up' where it rises by as much the other way."""
    past_words, short_words = line_words(sign, threshold)
    past = bool(sign != 0 and np.sign(log2fc) == sign and abs(log2fc) >= threshold)
    if past or not knockout:
        return (past_words if past else short_words), past
    return (TRANSCRIPT_UP if sign != 0 and np.sign(log2fc) == -sign and abs(log2fc) >= threshold
            else TRANSCRIPT_UNCHANGED), False


def judge_guides(effects, design, threshold):
    """Per guide: its target gene's change in per cent (the median over its measurable units) and where it falls
    against the line (line_words: past it, by at least `threshold` log2 the expected way, or short of it), or not
    measurable (in no unit would the expected change of its detection be visible, or its target gene is not a gene
    of the matrix). A knockout guide short of the line is 'transcript unchanged': a knocked-out gene's transcript
    can stay, as published screens say."""
    out = []
    for g, s in effects.groupby("guide"):
        sign = expected_sign(design, g)
        pooled = s[(s["unit"] == ALL_UNITS) & s["measurable"].astype(bool)]
        ok = s[(s["unit"] != ALL_UNITS) & s["measurable"].astype(bool)]
        if len(pooled):  # the units pooled, where they hold both sides with enough cells
            med, read = float(pooled["log2fc"].iat[0]), "the units pooled"
        elif len(ok):
            med, read = float(ok["log2fc"].median()), "the median over its units"
        else:
            med, read = np.nan, ""
        if np.isfinite(med):
            verdict, past = place(med, sign, threshold, modality_of(design, g) == "crispr_ko")
        else:
            verdict, past = NOT_MEASURABLE, False
        out.append({"guide": g, "target": s["target"].iat[0], "expected": sign, "verdict": verdict,
                    "past_the_line": past, "median_log2fc": med, "read_on": read,
                    "change_percent": round(100 * (2 ** med - 1), 1) if np.isfinite(med) else np.nan,
                    "units": int((s["unit"] != ALL_UNITS).sum())})
    return pd.DataFrame(out)


def efficacy_recipe(reference="control_guides"):
    """B.efficacy as blocks (docs/adr/0031, D2): each guide's own gene against its reference cells, per sample
    (pt.on_target: the log2 fold change of pooled counts per million); the module then places each guide against
    the 30% line, which is a rule, not a test."""
    from .. import blocks as pt
    effects = pt.on_target(reference=reference)
    return effects


@register("B.efficacy", Contract(
    serves="B.efficacy",
    applies_when="a modality acting on the target gene (CRISPRi, CRISPRa, knockout)",
    needs="each cell's guides, each guide's target and mode (perturbation.guide_modes where modes are mixed), the "
          "reference cells, units",
    returns="per guide and unit: the target gene's log2 fold change and log-normalised difference against the unit's "
            "reference cells, and its detection; per guide: its change in per cent and where it falls against the "
            "line (at least 30% down under CRISPRi, Replogle et al. 2022's filter; or up by the same factor under "
            "CRISPRa, Pertura's choice), transcript unchanged (knockout) or not measurable",
    fails_when=[("a knockout's mRNA can stay, so its target gene says little",
                 "a knockout guide that does not move it is 'transcript unchanged'; read a downstream signature, "
                 "the guides' agreement (F.guides_agree), the protein or the phenotype, and the edits at the cut "
                 "site where the reads cover it"),
                ("a target gene detected in few cells cannot show a change",
                 "the power to see a halving (a doubling under CRISPRa) of its detection, with the guide's and the "
                 "reference's cells; under 0.8 in every unit is 'not measurable'")],
    tested_against="CRISPRai: each guide's verdict, against an earlier hand analysis of the same guides; a "
                   "target gene the cells do not express reported as not measurable",
    choices={"threshold": [EFFICACY_LOG2FC, 1.0], "reference": ["control_guides", "other_targets"]}))
def efficacy(screen, design, out_dir, threshold=EFFICACY_LOG2FC, reference="control_guides"):
    from .. import blocks as pt
    with pt.inside(screen, out_dir):
        effects = efficacy_recipe(reference)
    eff = effects.table
    if eff.empty:
        from .. import policy
        return _empty(out_dir, "No guide with enough cells", "not_enough_data",
                      f"no guide with {policy.get('min_cells')} cells beside as many reference cells, in a unit or "
                      "over the units pooled")
    verdicts = judge_guides(eff, design, threshold)
    path = os.path.join(out_dir, "figure.png")
    from . import charts
    target_of = dict(zip(eff["guide"], eff["target"]))
    # each guide's change as its verdict reads it: the units pooled, else the median over its units
    own_of = dict(zip(eff["guide"], eff["own_gene"]))
    per_guide = (verdicts[verdicts["median_log2fc"].notna()][["guide", "median_log2fc", "verdict", "past_the_line",
                                                               "change_percent"]]
                 .rename(columns={"median_log2fc": "log2fc"}).reset_index(drop=True))
    per_guide.insert(1, "own_gene", per_guide["guide"].map(own_of))
    # each guide's gene in the control cells of its own arm, as its change is measured (bug register, BR37): where
    # a sample holds several conditions, the control cells drawn in the guide's mix of them
    def baseline(x, g):
        ref = screen.reference(reference, target=target_of[x])
        if screen.crossed:
            cells = screen.single() & (screen.top_guide == x)
            _, ref, _ = screen.matched(cells, ref & ~cells, MIN_CELLS, seed_of(x))
        return float(screen.gene(g)[ref].mean()) if ref.any() else np.nan
    per_guide["baseline"] = [baseline(x, g) if screen.gene(g) is not None else np.nan
                             for x, g in zip(per_guide["guide"], per_guide["own_gene"])]
    # a unit's row: its own change against the line, beside the guide's (the median over its measurable units), so a
    # unit does not read as past the line because its guide is
    table = eff.merge(verdicts[["guide", "verdict", "expected"]].rename(columns={"verdict": "guide_verdict"}),
                      on="guide", how="left")
    table["unit_verdict"] = [place(x, e, threshold, modality_of(design, g) == "crispr_ko")[0] if m else NOT_MEASURABLE
                             for x, e, g, m in zip(table["log2fc"], table["expected"], table["guide"],
                                                   table["measurable"])]
    drawable = per_guide.dropna(subset=["baseline", "log2fc"])
    if drawable[drawable["baseline"] > 0].empty:  # nothing to draw or judge: said, not a crash
        not_genes = sorted({t for t, g in zip(eff["target"], eff["own_gene"]) if not g})
        why = (f"no guide's target is a gene of the matrix (e.g. {', '.join(not_genes[:4])}), so no target gene can "
               "be measured" if not_genes else "no guide's target gene is detected well enough in any unit")
        return _empty(out_dir, "No target gene to measure", "not_enough_data", why, table)
    checks = charts.checks(charts.knockdown_vs_baseline(
        per_guide, path, "Each guide's knockdown of its target gene against the gene's level in the control cells"))
    n = verdicts["verdict"].value_counts().to_dict()
    past = verdicts["past_the_line"].astype(bool)
    measured = verdicts["verdict"] != NOT_MEASURABLE
    knockout = verdicts["guide"].map(lambda g: modality_of(design, g) == "crispr_ko").astype(bool)
    # the line describes a guide's change and never says whether it acts (docs/adr/0045, amendment 1): a measurement;
    # under knockout the biology's first line says what the transcript can show (KNOCKOUT_READING)
    reading = "" if knockout.all() else f"; {LINE_READING}"
    return Result("measured", table, {"verdicts": {k: int(v) for k, v in n.items()},
                                  "past_the_line": verdicts[past]["guide"].tolist(),
                                  "short_of_the_line": verdicts[~past & measured]["guide"].tolist(),
                                  "not_measurable": verdicts[~measured]["guide"].tolist(),
                                  "threshold": threshold, "reference": reference,
                                  "recipe_as_run": pt.recipe_as_run(efficacy_recipe, reference=reference)},
                  path, checks, f"{int(past.sum())} guides change their target gene past the line (at least 30% "
                                f"down, or up by the same factor), {int((~past & measured).sum())} less, "
                                + (f"{n[TRANSCRIPT_UNCHANGED]} of them knockout guides that leave their transcript "
                                   "unchanged, " if n.get(TRANSCRIPT_UNCHANGED) else "")
                                + (f"{n[TRANSCRIPT_UP]} knockout guides that raise it, " if n.get(TRANSCRIPT_UP)
                                   else "")
                                + f"{int((~measured).sum())} not measurable" + reading,
                  tables={"guides": verdicts, "knockdown": per_guide},
                  biology=efficacy_biology(verdicts, any(modality_of(design, g) == "crispr_ko"
                                                         for g in verdicts["guide"])))


def efficacy_biology(verdicts, knockout):
    """Each target's guides by where their own gene falls, the targets with most guides past the line first; under
    knockout, what its own gene can and cannot say (KNOCKOUT_READING)."""
    from .c import BIOLOGY_LINES
    lines = []
    for target, g in verdicts.groupby("target", sort=False):
        said = ", ".join(f"{v} {n}" for v, n in g["verdict"].value_counts().items())
        lines.append((int(g["past_the_line"].astype(bool).sum()), f"{target}: {said}"))
    lines = [w for _, w in sorted(lines, key=lambda x: -x[0])][:BIOLOGY_LINES]
    return ([KNOCKOUT_READING] if knockout else []) + lines


# ---- escape --------------------------------------------------------------------------------------------------------
@register("B.escape", Contract(
    serves="B.escape",
    applies_when="CRISPRi or knockout",
    needs="labels, units, the reference cells, each target's target gene",
    returns="mixscape (the default): each target's cells called perturbed or not perturbed by pertpy's Mixscape "
            "(the perturbation signature within each sample, then its mixture model), per target the share "
            "perturbed, and each cell's class (cell_classes.tsv); mixscale: each cell's score (pertpy's Mixscale), "
            "and per target whether the score follows its target gene's expression (Spearman over its cells, the "
            "gene's mean in each third of the scores: graded.tsv), as screens ask whether downstream effects scale "
            "with the on-target change; own_gene: per perturbation and sample the share of its cells detecting the "
            "target gene, against the control cells'; the package calls",
    fails_when=[("mosaic editing and transcript escape both keep the target gene detected",
                 "this share against the reference; all of a target's cells by default, its perturbed cells as an "
                 "option, each result saying which population it uses"),
                ("perturbation_driven_clusters (Pertura's own method): whether a "
                 "cluster holds its groups in equal representation is a judgement",
                 "the readings of that judgement (rule, factor) and the resolution, run as variants")],
    tested_against="zebrafish: the perturbed calls of the combination labels, against an earlier hand analysis; "
                   "perturbation_driven_clusters: on one development screen, its groups and clusters against those "
                   "an earlier analysis set aside",
    choices={"method": ["mixscape", "mixscale", "own_gene", "perturbation_driven_clusters"],
             "rule": ["chi2", "band", "enriched"],
             "factor": [2.0, 1.5, 3.0], "resolution": [0.8, 0.4, 1.2]},
    inputs={"groups": "perturbation_driven_clusters: the labels clustered and judged",
            "clustered_with": "perturbation_driven_clusters: labels clustered alongside, never judged or set aside "
                              "(the control cells)",
            "covariate_by_unit": "perturbation_driven_clusters: {unit: value}, regressed out before scaling",
            "n_hvg": "variable genes (2000)", "n_pcs": "components computed (30)", "n_use": "components used (10)",
            "n_neighbors": "neighbours (20)", "q": "the chi-square rule's q-value threshold (0.05)",
            "cache": "a dict reused between runs in one process, so readings that change only the judgement or the "
                     "resolution reuse the neighbour graph and clusters"},
    scope={k: ("method", ["perturbation_driven_clusters"])
           for k in ("groups", "clustered_with", "covariate_by_unit", "n_hvg", "n_pcs", "n_use", "n_neighbors", "q",
                     "cache", "rule", "factor", "resolution")}))
def escape(screen, design, out_dir, method="mixscape", rule="chi2", factor=2.0, resolution=0.8, groups=None,
           clustered_with=None, covariate_by_unit=None, n_hvg=2000, n_pcs=30, n_use=10, n_neighbors=20, q=0.05,
           cache=None):
    if not (screen.label == "control").any():  # every method reads a target's cells against control cells
        return _empty(out_dir, "No control cells", "not_enough_data",
                      "no control cells in the screen: each method sets a target's cells against control cells")
    if method in ("mixscape", "mixscale"):
        return escape_by_mixscape(screen, out_dir, method=method)
    if method == "perturbation_driven_clusters":
        return escape_by_clusters(screen, out_dir, groups, clustered_with, covariate_by_unit, rule, factor,
                                  resolution, n_hvg, n_pcs, n_use, n_neighbors, q, cache)
    rows = []
    for lab in screen.perturbations(MIN_CELLS):
        for t in screen.members(lab):
            own = screen.own_gene_of(t)
            if own is None or expected_sign(design, screen.label_guide(t)) >= 0:  # escape is a repression's
                continue
            ref = screen.reference("control_guides", target=t)  # the member's own arm (bug register, BR03)
            expr = screen.gene(own) > 0
            for u in screen.units() + ["all"]:
                where = (screen.unit == u) if u != "all" else np.ones(len(expr), bool)
                mt, mr = screen.cells_of(lab) & where, ref & where
                if screen.crossed:  # the control cells drawn within its samples' conditions, in its mix
                    mt, mr, _ = screen.matched(mt, mr & ~mt, MIN_CELLS, seed_of(lab))
                if mt.sum() < MIN_CELLS or mr.sum() < MIN_CELLS:
                    continue
                rows.append({"perturbation": lab, "own_gene": own, "unit": u, "detected_target": float(expr[mt].mean()),
                             "detected_reference": float(expr[mr].mean()), "cells": int(mt.sum())})
    df = pd.DataFrame(rows)
    if df.empty:
        return _empty(out_dir, "No target with enough cells", "not_enough_data",
                      "no target that should lower its target gene (CRISPRi or knockout) with a target gene and 10 "
                      "cells beside 10 reference cells")
    df["difference"] = df["detected_target"] - df["detected_reference"]
    df["group"] = np.where(df["perturbation"] == df["own_gene"], df["perturbation"],
                           df["perturbation"] + " (" + df["own_gene"] + ")")
    per_unit = df[df["unit"] != "all"]
    path = os.path.join(out_dir, "figure.png")
    checks = figures.dots(per_unit if len(per_unit) else df, "group", "difference", "unit", path,
                          "Target-gene detection: the perturbation's cells minus the reference cells, per unit",
                          "Target gene detected,\nperturbed − reference", reference=0.0, max_groups=80)
    pooled = df[df["unit"] == "all"].set_index("group")
    kept = (pooled["detected_target"] / pooled["detected_reference"].clip(lower=1e-9)) if len(pooled) else pooled
    return Result("measured" if len(pooled) else "not_enough_data", df,
                  {"pooled": pooled[["detected_target", "detected_reference"]].round(3).to_dict("index")},
                  path, checks, f"{len(pooled)} perturbation x target gene pairs; the median keeps "
                                f"{kept.median():.0%} of the control cells' detection of its target gene"
                  if len(pooled) else "no pair")


# ---- dose ----------------------------------------------------------------------------------------------------------
@register("B.dose", Contract(
    serves="B.dose",
    applies_when="CRISPRi or CRISPRa",
    needs="the own-gene effect of every guide (B.efficacy's measurement)",
    returns="per guide: the strength of its own-gene change (median log2 fold change over units) and B.efficacy's "
            "verdict; per target and mode: the strongest and weakest guide's strength and their ratio (no cut-off: "
            "no reviewed screen states a factor between a target's guides), and the targets whose guides' verdicts "
            "differ (one works, another does not)",
    fails_when=[("a target's guides differ in efficacy, so pooling them averages over strong and weak guides",
                 "the strengths and the targets with differing verdicts, listed; analyse them by guide; a finding's "
                 "guides check holds it on at least two guides that agree")],
    tested_against="CRISPRai: the targets whose guides an earlier hand analysis found discordant"))
def dose(screen, design, out_dir):
    eff = own_gene_effects(screen)
    if eff.empty or not eff["measurable"].any():
        return _empty(out_dir, "No measurable target gene", "not_enough_data", "no guide with a measurable target gene")
    shown = eff[eff["measurable"]]
    per = shown.groupby(["target", "guide"])["log2fc"].median().rename("strength").reset_index()
    per["guide_mode"] = [expected_sign(design, g) for g in per["guide"]]
    verdict = judge_guides(eff, design, EFFICACY_LOG2FC).set_index("guide")["verdict"]
    per["verdict"] = per["guide"].map(verdict)
    per["ratio_strongest_to_weakest"] = per.groupby(["target", "guide_mode"])["strength"].transform(
        lambda v: float(np.abs(v).max() / max(np.abs(v).min(), 1e-9)) if len(v) > 1 else np.nan)
    discordant = []
    for (t, mode), s in per.groupby(["target", "guide_mode"]):
        if len(s) >= 2 and len(set(s["verdict"]) - {"not measurable"}) > 1:
            discordant.append({"target": t, "guide_mode": {1: "activation", -1: "repression"}.get(mode, "unknown"),
                               "guides": dict(zip(s["guide"], np.round(s["strength"].to_numpy(), 3))),
                               "verdicts": dict(zip(s["guide"], s["verdict"]))})
    path = os.path.join(out_dir, "figure.png")
    checks = figures.dots(shown, "guide", "log2fc", "unit", path, "Each guide's own-gene change (its efficacy), per unit",
                          "Target gene, log2 FC vs reference", reference=0.0, max_groups=80)
    return Result("measured", per, {"discordant": discordant}, path, checks,
                  f"{len(discordant)} targets whose guides of one mode differ in B.efficacy's verdict (one works, "
                  "another does not)")


# ---- escape by clusters: the cells no perturbation moved (Pertura's own cell filter) --------------------------------
RULES = ("chi2", "band", "enriched")


def driven_clusters(labels, clusters, groups, q=0.05, rule="chi2", factor=2.0):
    """Per cluster: its counts of each group, and whether it is perturbation-driven, i.e. not in equal representation.
    The judgement is on the groups' cells only (other cells clustered alongside are not counted), each group's share
    of a cluster against its share of all the groups' clustered cells. The judgement has no one numeric rule;
    Pertura's readings:
    - "chi2": a chi-square test of the cluster's counts, Benjamini-Hochberg over the clusters, driven at q < q;
    - "band": in equal representation when every group's share lies within a factor `factor` of its overall share;
    - "enriched": driven when some group's share exceeds its overall share by more than `factor`; a cluster of cells
      no perturbation moved holds every group that has such cells and lacks the strongly perturbed ones."""
    from scipy import stats
    from .c import bh
    if rule not in RULES:
        raise ValueError(f"the rule is one of {', '.join(RULES)}")
    labels, clusters = np.asarray(labels).astype(str), np.asarray(clusters).astype(str)
    overall = pd.Series(labels).value_counts().reindex(groups).fillna(0.0)
    overall = overall / overall.sum()
    present = overall > 0
    rows = []
    for c in sorted(set(clusters)):
        counts = pd.Series(labels[clusters == c]).value_counts().reindex(groups).fillna(0.0)
        n = counts.sum()
        if n == 0:  # only cells outside the groups: nothing to judge or remove
            p, ratio = 1.0, pd.Series(1.0, index=overall.index[present])
        else:
            p = float(stats.chisquare(counts[present], overall[present] * n).pvalue)
            ratio = (counts[present] / n) / overall[present]
        rows.append({"cluster": c, "cells": int(n), **{f"cells {g}": int(counts[g]) for g in groups}, "p": p,
                     "largest enrichment": float(ratio.max()),
                     "largest departure": float(np.inf if ratio.min() == 0 else max(ratio.max(), 1 / ratio.min()))})
    table = pd.DataFrame(rows)
    table["q"] = bh(table["p"].to_numpy())
    if rule == "chi2":
        table["perturbation_driven"] = table["q"] < q
    elif rule == "band":
        table["perturbation_driven"] = table["largest departure"] > factor
    else:
        table["perturbation_driven"] = table["largest enrichment"] > factor
    return table


def neighbour_graph(screen, idx, covariate=None, n_hvg=2000, n_pcs=30, n_use=10, n_neighbors=20, seed=0):
    """Seurat's default workflow on the cells `idx`: log-normalised, variable genes, the covariate regressed out,
    scaled, PCA, a neighbour graph on the first n_use components. Returns the AnnData, ready for clustering."""
    import anndata as ad
    import scanpy as sc
    a = ad.AnnData(screen.X[idx].astype(np.float32))
    a.var_names = [str(g) for g in screen.genes]
    sc.pp.normalize_total(a, target_sum=1e4)
    sc.pp.log1p(a)
    from ..quality import variable_genes
    variable_genes(a, n_top_genes=min(n_hvg, a.n_vars), flavor="seurat")  # receptor segments left out (PF4)
    h = a[:, a.var["highly_variable"].to_numpy()].copy()
    if covariate is not None and np.unique(np.asarray(covariate)[idx]).size > 1:
        h.obs["covariate"] = np.asarray(covariate, float)[idx]
        sc.pp.regress_out(h, ["covariate"])
    sc.pp.scale(h, max_value=10)
    sc.tl.pca(h, n_comps=min(n_pcs, h.n_vars - 1, h.n_obs - 1), random_state=seed)
    sc.pp.neighbors(h, n_neighbors=n_neighbors, n_pcs=min(n_use, h.obsm["X_pca"].shape[1]), random_state=seed)
    return h


def removal(labels, idx, clusters, groups, q=0.05, rule="chi2", factor=2.0):
    """The cells of `groups` among `idx` whose cluster is not perturbation-driven (driven_clusters), as a mask over
    all cells; cells outside the groups are never removed. Returns (the mask, the clusters' table)."""
    labels = np.asarray(labels).astype(str)
    table = driven_clusters(labels[idx], clusters, groups, q, rule, factor)
    driven = set(table.loc[table["perturbation_driven"], "cluster"])
    removed = np.zeros(len(labels), bool)
    removed[idx[~np.isin(clusters, list(driven)) & np.isin(labels[idx], groups)]] = True
    return removed, table


def perturbation_driven(screen, groups, covariate=None, resolution=0.8, n_hvg=2000, n_pcs=30, n_use=10,
                        n_neighbors=20, q=0.05, seed=0, rule="chi2", factor=2.0, clustered_with=(), cache=None):
    """The cell filter: the cells of `groups` (and of `clustered_with`, which shape the clusters but are not judged
    or removed) clustered (neighbour_graph, Leiden), and the groups' cells of clusters that are not
    perturbation-driven marked for removal. A cache (a dict) keeps the neighbour graph and the clusters between calls.
    Returns (a mask of the cells removed, the clusters' table)."""
    from ..states import leiden_states
    groups, extra = list(groups), list(clustered_with or [])
    cache = {} if cache is None else cache
    gkey = ("graph", tuple(groups), tuple(extra), n_hvg, n_pcs, n_use, n_neighbors, seed)
    if gkey not in cache:
        idx = np.flatnonzero(np.isin(screen.label, groups + extra))
        cache[gkey] = (idx, neighbour_graph(screen, idx, covariate, n_hvg, n_pcs, n_use, n_neighbors, seed))
    idx, h = cache[gkey]
    ckey = gkey + (float(resolution),)
    if ckey not in cache:
        cache[ckey] = leiden_states(h, resolution, seed)
    return removal(screen.label, idx, cache[ckey], groups, q, rule, factor)


def escape_recipe(method="mixscape"):
    """B.escape's mixscape and mixscale paths as blocks (docs/adr/0031, D2): each cell's response to its
    perturbation, and the same call on groups of control guides."""
    from .. import blocks as pt
    res = pt.response(method=method)
    try:
        cal = pt.calibrate(res)
    except ValueError as e:  # too few control guides for groups
        cal = str(e)
    return res, cal


REGISTRY["B.escape"].recipe = escape_recipe  # its mixscape and mixscale paths; own_gene and the clusters are its own


def escape_by_mixscape(screen, out_dir, n_pcs=40, seed=0, method="mixscape"):
    """B.escape by pertpy's Mixscape (Papalexi et al. 2021) or Mixscale (Jiang, Dalgarno et al. 2025), with their
    defaults (pertura.steps.response): on the log-normalised single-target and control cells, the PCA of the top
    2,000 genes by dispersion (scanpy); the perturbation signature (each cell minus its nearest control cells, within
    each sample); then each target's cells called perturbed or not perturbed, or scored. A target with fewer than
    MIN_GROUP cells in a sample is not called in that sample (its marker test cannot run there); those cells are
    listed as not called. The same call on groups of control guides reads each target against no effect. For a
    finding, its perturbed cells are an option beside all of the target's cells (the default): the two populations
    answer different questions, and each result says which it uses."""
    from .. import blocks as pt
    from . import charts
    with pt.inside(screen, out_dir):
        res, cal = escape_recipe(method)
    per, calls = res.table, res.calls
    cells = res.extra["cells"].reset_index().rename(columns={"index": "barcode"})
    summary_cal = cal.extra.get("summary") if isinstance(cal, pt.Result) else None
    col = "share_perturbed" if method == "mixscape" else "median_score"
    zero = bool(summary_cal) and summary_cal.get("groups_95th_percentile") == 0
    words_cal = ((f"; the same call on {summary_cal['groups']} groups of control guides called none of their cells "
                  f"perturbed, and {len(summary_cal['targets_beyond'])} targets have cells it calls" if zero else
                  f"; the same call on {summary_cal['groups']} groups of control guides: their 95th percentile "
                  f"{summary_cal['groups_95th_percentile']:.3g}, {len(summary_cal['targets_beyond'])} targets past it")
                 if summary_cal and summary_cal.get("groups_95th_percentile") is not None else
                 "; the calibration check was not run" + (f" ({cal})" if isinstance(cal, str) else ""))
    path = os.path.join(out_dir, "figure.png")
    line = ((summary_cal["groups_95th_percentile"], f"Control-guide groups, 95th percentile "
             f"({summary_cal['groups']} groups)") if summary_cal and summary_cal.get("groups_95th_percentile")
            is not None else None)
    shown = per.head(80).iloc[::-1]
    if method == "mixscape":
        checks = charts.checks(charts.ranked_targets(shown, col, path, "Share called perturbed (Mixscape)",
                                                     "Each target's share of cells Mixscape calls perturbed",
                                                     control_line=line), reference=None)
        some = int((per["perturbed"] > 0).sum())
        words = f"{some} of {len(per)} targets have cells Mixscape calls perturbed (pertpy's Mixscape)"
        about = {"method": method, "targets_with_perturbed_cells": some}
    else:
        checks = charts.checks(charts.ranked_targets(shown, col, path, "Median Mixscale score",
                                                     "Each target's median perturbation score (Mixscale)",
                                                     control_line=line), reference=None)
        scored = int(per["scored"].sum())
        words = (f"{scored} of {len(per)} targets scored by Mixscale (pertpy's Mixscale; median score of the "
                 f"strongest {per.iloc[0]['median_score']:.2f})" if len(per) else "no target scored")
        about = {"method": method, "targets_scored": scored}
    left = int((cells.get("class", pd.Series(dtype=str)) == "not called").sum()) if method == "mixscape" else \
        int(cells.loc[cells["label"] != "control", "score"].isna().sum())
    graded, lines = graded_by_score(screen, cells) if method == "mixscale" else (pd.DataFrame(), [])
    if len(graded):
        words += f"; {int((graded['q'] < 0.05).sum())} of {len(graded)} targets' scores follow their target gene"
    return Result("measured", per, {**about, "cells_not_called": left, "package_calls": calls,
                                    "calibration": summary_cal or {"not_run": cal if isinstance(cal, str) else
                                                                   "no result"},
                                    "recipe_as_run": pt.recipe_as_run(escape_recipe, method=method)}, path, checks,
                  words + (f"; {left} cells not called (fewer than {MIN_GROUP} of their target's cells, or fewer "
                           "than 20 control cells, in their "
                           "sample)" if left else "") + words_cal,
                  tables={"cell_classes": cells, **({"graded": graded} if len(graded) else {})}, biology=lines)


def graded_by_score(screen, cells, least=20):
    """Whether each target's perturbation follows its strength on its own gene, as published screens ask whether the
    downstream effects scale with the on-target change: within each target's cells, Spearman's correlation of its
    Mixscale score (the downstream signature) with its target gene's log-normalised expression (the on-target change),
    and the gene's mean in each third of the scores. A knockdown that deepens with the score correlates negatively."""
    from scipy import stats
    from .c import BIOLOGY_LINES, bh
    score = cells.set_index(cells["barcode"].astype(str))["score"]
    names = np.asarray(screen.adata.obs_names).astype(str)
    libsize = np.maximum(np.asarray(screen.libsize, dtype=float), 1)
    rows = []
    for t in sorted(set(cells.loc[cells["label"] != "control", "label"].astype(str))):
        own = screen.own_gene_of(t)
        m = screen.cells_of(t)
        s = score.reindex(names[m]).to_numpy(dtype=float)
        if own is None or np.isfinite(s).sum() < least:
            continue
        e = np.log1p(screen.gene(own) * 1e4 / libsize)[m]
        ok = np.isfinite(s)
        rho, p = stats.spearmanr(s[ok], e[ok])
        thirds = pd.qcut(s[ok], 3, labels=False, duplicates="drop")
        means = [float(e[ok][thirds == k].mean()) for k in sorted(set(thirds))]
        rows.append({"target": t, "gene": own, "cells": int(ok.sum()), "rho": float(rho), "p": float(p),
                     "gene_by_score_third": ", ".join(f"{v:.2f}" for v in means)})
    t = pd.DataFrame(rows, columns=["target", "gene", "cells", "rho", "p", "gene_by_score_third"])
    if t.empty:
        return t, []
    t["q"] = bh(t["p"].to_numpy())
    hit = t[t["q"] < 0.05].reindex(t.loc[t["q"] < 0.05, "rho"].abs().sort_values(ascending=False).index)
    lines = [f"{r.target}: the score {'rises' if r.rho < 0 else 'falls'} as {r.gene} falls (rho {r.rho:+.2f}; "
             f"{r.gene} {r.gene_by_score_third} log-normalised by thirds of the score)"
             for r in hit.head(BIOLOGY_LINES).itertuples()]
    return t, lines


def escape_by_clusters(screen, out_dir, groups, clustered_with, by_unit, rule, factor, resolution, n_hvg, n_pcs,
                       n_use, n_neighbors, q, cache):
    """B.escape by clusters: the groups' cells in clusters that hold every group in equal representation (cells no
    perturbation moved), set aside; their barcodes in the table 'removed'."""
    from .c import barcodes, covariate_by_unit
    if not groups:
        raise ValueError("perturbation_driven_clusters needs the groups to cluster")
    cov = covariate_by_unit(screen, by_unit)
    removed, table = perturbation_driven(screen, groups, cov, resolution, n_hvg, n_pcs, n_use, n_neighbors, q,
                                         rule=rule, factor=factor, clustered_with=clustered_with, cache=cache)
    lab = np.asarray(screen.label).astype(str)
    per_group = {g: int((removed & (lab == g)).sum()) for g in groups}
    path = os.path.join(out_dir, "figure.png")
    checks = figures.bars(np.array(list(per_group.values())), list(per_group), path,
                          "Cells set aside: in clusters holding every group in equal representation",
                          "Cells set aside")
    return Result("detected" if removed.any() else "not_detected", table,
                  {"method": "perturbation_driven_clusters", "rule": rule, "factor": factor, "resolution": resolution,
                   "clusters": int(len(table)), "perturbation_driven": int(table["perturbation_driven"].sum()),
                   "cells removed": per_group},
                  path, checks, f"{int(removed.sum())} cells set aside from {len(table)} clusters",
                  tables={"removed": pd.DataFrame({"barcode": barcodes(screen)[removed], "label": lab[removed]})})


# ---- abundance -----------------------------------------------------------------------------------------------------
@register("B.abundance", Contract(
    serves="B.abundance",
    applies_when="always (read against the library's representation when there is one)",
    needs="labels, each cell's strongest guide, the library's guides with their kinds, samples",
    returns="per target: the cells of each of its guides (the library's guides, those without cells as zero; each "
            "confirmed vector of several guides counted as one construct), their median, the control guides' median and range, the log2 ratio of the two medians, the share of control "
            "guides with as many cells, whether it is above every control guide, and the largest share of its "
            "cells held by one guide in one sample (Pertura's measure); per_guide.tsv: cells per guide and sample; "
            "per_sample.tsv: cells per target and sample; per_sample_against_controls.tsv: the same comparison "
            "within each sample, named in the summary where a target is above every control guide there. No "
            "status without a comparison (measured). With a "
            "comparison (treatment samples against control samples, or against the library's counts): MAGeCK's "
            "test (mageck test, normalised to the control guides, as Datlinger et al. 2025 ran it), per target its "
            "log2 fold change and FDR (mageck.tsv)",
    fails_when=[("an expanded clone makes its target look abundant",
                 "each guide's cells against its frequency in the library, as published screens read expansion, "
                 "and B.clonality's top-guide shares"),
                ("control guides that are themselves a few clones make a poor yardstick",
                 "the control guides' range beside their median, and B.clonality's top-guide shares"),
                ("without the library's representation, cell numbers are not fitness",
                 "the library's plasmid or early counts, asked of the user, as MAGeCK's control")],
    tested_against="CAR-T: the targets above every control guide, against an earlier script; the synthetic "
                   "screen's burst of one target; MAGeCK: a planted target ranked first "
                   "(tests/test_question_modules.py)",
    inputs={"treatment": "MAGeCK: the samples tested (such as the tumours)",
            "versus": "MAGeCK: the samples they are compared with (such as the lymph nodes)",
            "library_counts": "MAGeCK: a TSV of guide and count (the plasmid or an early time point), as the control",
            "paired": "MAGeCK: treatment and control samples paired in order (one mouse's tumour and node)"}))
def abundance(screen, design, out_dir, treatment=None, versus=None, library_counts=None, paired=False):
    keep = (screen.single() | (screen.label == "control")) & (screen.top_guide != "")
    g = pd.DataFrame({"guide": screen.top_guide[keep], "label": screen.label[keep], "unit": screen.unit[keep]})
    lib = screen.guides.assign(guide=screen.guides["guide"].astype(str), target=screen.guides["target"].astype(str),
                               kind=screen.guides["kind"].astype(str))
    vectors = value(design, "perturbation.vectors")  # {vector: [guides]}: one construct each, counted as one
    if isinstance(vectors, dict) and vectors:
        of = {str(x): str(v) for v, gs in vectors.items() for x in gs}
        g["guide"] = g["guide"].map(lambda x: of.get(x, x))
        lib = lib.assign(guide=lib["guide"].map(lambda x: of.get(x, x))).drop_duplicates("guide")
    control = set(lib.loc[lib["kind"] != "targeting", "guide"]) | set(g.loc[g["label"] == "control", "guide"])
    by_guide = g.groupby("guide").size()
    lib_guides = lib[["guide", "target", "kind"]].drop_duplicates("guide")
    lib_guides["cells"] = lib_guides["guide"].map(by_guide).fillna(0).astype(int)
    # each guide's target as the screen labels its cells (a gene with CRISPRi and CRISPRa guides is two targets);
    # the library's gene for a guide without cells
    named = g[g["label"] != "control"].groupby("guide")["label"].agg(most_common_value)
    lib_guides["target"] = [named.get(x, tg) for x, tg in zip(lib_guides["guide"], lib_guides["target"])]
    ctrl = lib_guides[lib_guides["guide"].isin(control)]["cells"].to_numpy()
    c_med = float(np.median(ctrl)) if len(ctrl) else np.nan
    per_unit = g.groupby(["guide", "unit"]).size().rename("cells").reset_index()
    per_unit["target"] = per_unit["guide"].map(dict(zip(lib_guides["guide"], lib_guides["target"])))
    rows = []
    for target, gs in lib_guides[~lib_guides["guide"].isin(control)].groupby("target"):
        cells = gs["cells"].to_numpy()
        med = float(np.median(cells))
        burst = per_unit[per_unit["guide"].isin(gs["guide"])]["cells"]
        rows.append({"target": target, "guides": int(len(gs)), "cells": int(cells.sum()),
                     "cells_per_guide": ";".join(f"{x}:{n}" for x, n in zip(gs["guide"], cells)),
                     "median_cells_per_guide": med, "control_median_cells_per_guide": c_med,
                     "log2_vs_control_guides": float(np.log2((med + 1) / (c_med + 1))) if len(ctrl) else np.nan,
                     "control_guides_with_as_many": float(np.mean(ctrl >= med)) if len(ctrl) else np.nan,
                     "above_every_control_guide": bool(len(ctrl) and med > ctrl.max()),
                     "largest_guide_sample_share": round(float(burst.max() / max(cells.sum(), 1)), 3)
                     if len(burst) else 0.0})
    df = pd.DataFrame(rows).sort_values("log2_vs_control_guides", ascending=False) if rows else pd.DataFrame(
        columns=["target"])
    lab = pd.Series(np.where(screen.label == "control", "control cells", screen.label))[
        screen.single() | (screen.label == "control")]
    per_sample = pd.crosstab(lab, pd.Series(screen.unit)[lab.index]).stack().rename("cells").reset_index()
    per_sample.columns = ["target", "unit", "cells"]
    path = os.path.join(out_dir, "figure.png")
    shown = lib_guides.assign(group=np.where(lib_guides["guide"].isin(control), "control guides",
                                             lib_guides["target"]),
                              log2_vs_control_guides=np.log2((lib_guides["cells"] + 1) / (c_med + 1)))
    from . import charts
    checks = charts.checks(charts.guide_abundance(
        shown.rename(columns={"group": "_g"}).assign(target=shown["group"])[["target", "guide", "cells"]], path,
        "Cells per guide, each target against the control guides"))
    above = df[df["above_every_control_guide"]]["target"].tolist() if len(df) else []
    summary = (f"{len(df)} targets; per guide, the control guides' median is {c_med:.0f} cells "
               f"(from {ctrl.min()} to {ctrl.max()}); above every control guide: {', '.join(above) or 'none'}"
               if len(ctrl) else f"{len(df)} targets; no control guides with cells to compare against")
    # within each sample (a mouse, a donor, a hashtag's condition): pooled counts can hide a target that rises in one
    by_sample = _above_controls_in_each_sample(per_unit, lib_guides, control)
    if len(by_sample):
        tops = by_sample[by_sample["above_every_control_guide"]].groupby("unit")["target"].agg(
            lambda s: ", ".join(s[:6]))
        if len(tops):
            summary += "; above every control guide within a sample: " + "; ".join(
                f"{u}: {t}" for u, t in tops.items())
    about = {"control_median_cells_per_guide": c_med, "above_every_control_guide": above,
             "control_guides": int(len(ctrl))}
    tables = {"per_guide": per_unit, "per_sample": per_sample, "per_sample_against_controls": by_sample}
    status = "measured"
    if treatment and (versus or library_counts):
        mg, call = abundance_by_mageck(per_unit, lib_guides, treatment, versus, library_counts, paired,
                                       os.path.join(out_dir, "mageck"))
        tables["mageck"] = mg
        about["package_calls"] = [call]
        hits = mg[mg["fdr"] < 0.05]
        status = "detected" if len(hits) else "not_detected"
        charts.volcano(mg.rename(columns={"target": "gene", "lfc": "effect", "fdr": "q"}),
                       os.path.join(out_dir, "figure_mageck.png"), "Each target's guides in the treatment samples "
                       "against the control samples (MAGeCK)", threshold=0.05)
        summary += (f"; MAGeCK ({','.join(treatment)} against {','.join(versus) if versus else 'the library'}): "
                    f"{len(hits)} targets at FDR < 0.05 ({', '.join(hits['target'].head(8))})")
    return Result(status, df, about, path, checks, summary, tables=tables)


def _above_controls_in_each_sample(per_unit, lib_guides, control):
    """Per target and sample (with two samples or more): its guides' median cells (the library's guides, those
    without cells there as zero, as the pooled count has it) against the control guides' median in that sample, the
    log2 ratio, and whether it is above every control guide there with two of its guides or more holding cells (so
    one guide's burst is not named; B.clonality reads bursts); sorted by the ratio."""
    if per_unit["unit"].nunique() < 2:
        return pd.DataFrame(columns=["target", "unit"])
    cells = per_unit.set_index(["guide", "unit"])["cells"]
    rows = []
    for unit in sorted(per_unit["unit"].unique(), key=str):
        ctrl = np.array([cells.get((x, unit), 0) for x in lib_guides["guide"] if x in control])
        if not len(ctrl) or not ctrl.any():
            continue
        for target, gs in lib_guides[~lib_guides["guide"].isin(control)].groupby("target"):
            n = [cells.get((x, unit), 0) for x in gs["guide"]]
            med = float(np.median(n))
            rows.append({"target": target, "unit": unit, "median_cells_per_guide": med,
                         "guides_with_cells": int(sum(1 for v in n if v > 0)),
                         "control_median_cells_per_guide": float(np.median(ctrl)),
                         "log2_vs_control_guides": float(np.log2((med + 1) / (np.median(ctrl) + 1))),
                         "above_every_control_guide": bool(med > ctrl.max() and sum(1 for v in n if v > 0) >= 2)})
    out = pd.DataFrame(rows)
    return out.sort_values("log2_vs_control_guides", ascending=False) if len(out) else pd.DataFrame(
        columns=["target", "unit"])


def abundance_by_mageck(per_unit, lib_guides, treatment, control, library_counts, paired, workdir):
    """MAGeCK's test (mageck test) of each target's guides: cells per guide in the treatment samples against the
    control samples (or the library's counts), normalised to the control guides (--norm-method control). Returns
    (per target: guides, lfc, neg_fdr, pos_fdr, fdr, direction; the package call)."""
    import subprocess
    from .. import envs
    from . import packages
    os.makedirs(workdir, exist_ok=True)
    counts = per_unit.pivot_table(index="guide", columns="unit", values="cells", aggfunc="sum").reindex(
        lib_guides["guide"]).fillna(0).astype(int)
    if library_counts:
        lib = pd.read_csv(library_counts, sep="\t")
        counts["library"] = lib.set_index(lib.columns[0])[lib.columns[1]].reindex(counts.index).fillna(0).astype(int)
    samples = list(treatment) + (list(control) if control else ["library"])
    missing = [s for s in samples if s not in counts.columns]
    if missing:
        raise ValueError(f"MAGeCK: samples {missing} are not among the screen's samples {sorted(counts.columns)}")
    is_control = lib_guides["guide"].isin(controls_of(lib_guides)).to_numpy()
    gene = np.where(is_control, "control", lib_guides["target"].to_numpy())
    table = counts[samples].reset_index()
    table.insert(1, "Gene", gene)
    table.to_csv(os.path.join(workdir, "counts.txt"), sep="\t", index=False)
    lib_guides.loc[is_control, "guide"].to_csv(os.path.join(workdir, "control_guides.txt"), index=False,
                                               header=False)
    cmd = [envs.mageck(), "test", "-k", "counts.txt", "-t", ",".join(treatment),
           "-c", ",".join(control) if control else "library", "-n", "abundance", "--norm-method", "control",
           "--control-sgrna", "control_guides.txt"] + (["--paired"] if paired else [])
    r = subprocess.run(cmd, cwd=workdir, env=envs.mageck_env(), capture_output=True, text=True)
    out = os.path.join(workdir, "abundance.gene_summary.txt")
    if r.returncode != 0 or not os.path.exists(out):
        raise RuntimeError(f"mageck test failed: {(r.stderr or r.stdout)[-1500:]}")
    g = pd.read_csv(out, sep="\t")
    g = g[g["id"] != "control"]
    mg = pd.DataFrame({"target": g["id"].astype(str), "guides": g["num"], "lfc": g["pos|lfc"],
                       "neg_fdr": g["neg|fdr"], "pos_fdr": g["pos|fdr"]})
    mg["fdr"] = mg[["neg_fdr", "pos_fdr"]].min(axis=1)
    mg["direction"] = np.where(mg["pos_fdr"] <= mg["neg_fdr"], "up", "down")
    version = subprocess.run([envs.mageck(), "--version"], env=envs.mageck_env(), capture_output=True,
                             text=True)
    call = packages.call("MAGeCK", "test", contrast=f"{','.join(treatment)} against "
                         f"{','.join(control) if control else 'the library'}", norm_method="control",
                         control_sgrna=int(is_control.sum()), paired=bool(paired),
                         mageck_version=(version.stdout or version.stderr).strip())
    return mg.sort_values("fdr").reset_index(drop=True), call


def controls_of(lib_guides):
    """The library's control guides (any kind but targeting)."""
    return set(lib_guides.loc[lib_guides["kind"] != "targeting", "guide"])


# ---- clonality -----------------------------------------------------------------------------------------------------
@register("B.clonality", Contract(
    serves="B.clonality",
    applies_when="in vivo",
    needs="each cell's guides, labels, control groups and units",
    returns="per target, control group and all control cells, and unit: the share of its cells whose strongest "
            "guide is its most common one (Pertura's measure, which no reviewed screen uses; no cut-off); the "
            "largest share per group",
    fails_when=[("an expanded clone stands in for a perturbation's effect, or for the reference",
                 "these shares beside the guides' representation in the library (B.abundance), as published screens "
                 "read expansion")],
    tested_against="CAR-T: the share of one mouse's control cells that a single guide holds, against an earlier "
                   "hand count"))
def clonality(screen, design, out_dir):
    control = screen.label == "control"
    groups = {f"{g} (control)": control & (screen.control_group == g)
              for g in sorted(set(screen.control_group[control])) if g}
    groups["control cells"] = control
    for t in screen.targets(MIN_CELLS):
        groups[t] = screen.cells_of(t)
    rows = []
    for name, mask in groups.items():
        for u in screen.units():
            m = mask & (screen.unit == u) & (screen.top_guide != "")
            if m.sum() < MIN_CELLS:
                continue
            vc = pd.Series(screen.top_guide[m]).value_counts()
            rows.append({"group": name, "unit": u, "cells": int(m.sum()), "top_guide": str(vc.index[0]),
                         "top_guide_cells": int(vc.iloc[0]), "top_share": round(float(vc.iloc[0] / m.sum()), 3)})
    df = pd.DataFrame(rows)
    if df.empty:
        return _empty(out_dir, "No group with enough cells", "not_enough_data", "no group with 10 cells in a unit")
    path = os.path.join(out_dir, "figure.png")
    checks = figures.dots(df, "group", "top_share", "unit", path,
                          "The share of each group's cells on its most common guide, per unit",
                          "Share of cells on the top guide", reference=None, max_groups=80)
    largest = df.groupby("group")["top_share"].max().sort_values(ascending=False)
    return Result("measured", df, {"largest_top_share": {k: float(v) for k, v in largest.items()}}, path, checks,
                  f"the most common guide holds from {df['top_share'].min():.0%} to {df['top_share'].max():.0%} of a "
                  f"group's cells in a unit (median {df['top_share'].median():.0%}); largest: "
                  + ", ".join(f"{k} {v:.0%}" for k, v in largest.head(3).items()))


REGISTRY["B.efficacy"].recipe = efficacy_recipe
