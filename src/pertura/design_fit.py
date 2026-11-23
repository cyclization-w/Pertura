"""Which analyses fit a screen (docs/adr/0025, section 2): the confirmed design's facts that choose them, each
threshold named once, and each module's lead test and a finding's checks, chosen from those facts before any result,
with the reason a methods sentence gives.

The tables, one default each; any other of the field's tests is the agent's to name (a module's method or test):
- genes (C.genes, C.within_states, D.cutting_response), per target: with MIN_REPLICATES or more biological
  replicates holding both the target's and the reference cells, edgeR-QL pseudobulk by replicate; otherwise Wilcoxon
  on cells (scanpy), the field's most common test, against the reference cells of the target's own samples in its mix
  where the screen has two or more samples (docs/build/gene_test_calibration_by_design_2026-10-31.md);
- program scores (C.programs), per target: the linear model with the sample where the cells span two or more
  samples or lanes, with the condition where one sample holds several conditions, else Wilcoxon on the scores;
- states (C.states_and_composition, C.cell_cycle), per target: with MIN_REPLICATES or more biological replicates
  holding both sides, propeller (speckle) on the replicates' proportions; otherwise Fisher's exact test on cells
  against the control cells of the same samples.
A finding's checks (the open points' F5): its guides agree (MIN_GUIDES guides with cells); the field's other tests
the design admits for its kind, each beside the control-guide groups through the same test (tests); the calibration
check; halves of the units (a target with one guide, with HALF_UNITS units); the deposit's calls (a finding on the
agent's calling rule). Depth matching and halves of the cells are not among them (the depth-matching tool stays in
the notebook: pt.match_depth)."""
import numpy as np

from . import policy

MIN_REPLICATES = 3   # replicates holding both sides, for a test by replicate (ADR 0022, section 15)
MIN_CELLS = 10       # a side's cells in a replicate for the replicate to count
MIN_GUIDES = 2       # guides with MIN_CELLS cells, for the guides-agree check
HALF_UNITS = 4       # units for halves of the units; with fewer, halves of the cells (in vitro only)
KINDS = ("gene", "gene_set", "state", "score", "interaction", "custom")  # score: a per-cell score the notebook saved


def unit_word(screen):
    """The design's word for one replicate (mouse, donor, ...), else 'sample'."""
    from .design_gate import no_replicate
    from .question_modules.data import value
    v = value(screen.design, "units.independent_unit")
    word = str(v).strip().lower() if not no_replicate(v) else ""
    return word if word and " " not in word else "sample"


def _unit_of_cells(screen):
    """Each cell's unit as integer codes, with the units' names (cached on the screen: the tables ask per target)."""
    import pandas as pd
    c = screen.__dict__.get("_unit_codes")
    if c is None or len(c[0]) != len(screen.unit):
        codes, names = pd.factorize(pd.Series(screen.unit))
        c = screen.__dict__["_unit_codes"] = (codes, list(names))
    return c


def shared_units(screen, target_mask, reference_mask, min_cells=MIN_CELLS):
    """The units holding min_cells or more of both sides' cells. A unit for the cells a sample could not assign
    ('<sample>:unassigned') is no replicate."""
    codes, names = _unit_of_cells(screen)
    a = np.bincount(codes[np.asarray(target_mask, bool)], minlength=len(names))
    b = np.bincount(codes[np.asarray(reference_mask, bool)], minlength=len(names))
    return [str(u) for k, u in enumerate(names)
            if a[k] >= min_cells and b[k] >= min_cells and not str(u).endswith(":unassigned")]


def sides(screen, target, reference="control_guides"):
    """A target's cells and its reference cells."""
    tm = screen.cells_of(target)
    return tm, screen.reference(reference, target=target) & ~tm


def replicates(screen, target, reference="control_guides"):
    """The facts a test by replicate needs: whether the units are biological replicates (the confirmed design),
    the units holding both sides, and the screen's units."""
    tm, rm = sides(screen, target, reference)
    units = shared_units(screen, tm, rm)
    return {"biological": bool(screen.independent_units()), "shared": units, "n": len(units),
            "screen_units": len(screen.units()), "word": unit_word(screen)}


def _plural(word, n):
    return word if n == 1 else (word[:-5] + "mice" if word.endswith("mouse") else word + "s")


def gene_lead(screen, target, reference="control_guides"):
    """The lead gene test for one target, from the design: {method, reason, replicates}. Pseudobulk by biological
    replicate where 3 or more hold both sides; otherwise Wilcoxon on cells, the field's most common test, against the
    reference cells of the target's own samples in its mix where the screen has two or more samples: a target whose
    cells sit in other samples than the control cells' would otherwise read the samples' differences as its genes
    (CRISPRai's captures of different libraries: docs/build/gene_test_calibration_by_design_2026-10-31.md). Wilcoxon
    against every reference cell, and a test on cells with the sample as a covariate (Seurat's LR), are the agent's
    to name."""
    r = replicates(screen, target, reference)
    w, n = r["word"], r["n"]
    if r["biological"] and n >= policy.get("min_replicates"):
        return {"method": "edger_ql", "replicates": n,
                "reason": f"pseudobulk by {w} (edgeR-QL), since {n} {_plural(w, n)} hold both the target's and the "
                          f"reference cells"}
    if r["screen_units"] <= 1 and not screen.crossed:
        return {"method": "wilcoxon", "replicates": n,
                "reason": "a test on cells (Wilcoxon, the field's most common), since the screen is one sample, "
                          "without replicates"}
    if r["screen_units"] <= 1:
        why = "the screen is one sample, without replicates"
    elif r["biological"]:
        why = f"only {n} {_plural(w, n)} hold both the target's and the reference cells"
    else:
        why = f"the {r['screen_units']} samples are not biological replicates"
    cells = "control cells" if reference == "control_guides" else "reference cells"
    own = "samples and conditions" if screen.crossed else "samples"
    return {"method": "wilcoxon_same_samples", "replicates": n,
            "reason": f"a test on cells (Wilcoxon, the field's most common) against the {cells} of the target's own "
                      f"{own}, in its mix, since {why}"}


def score_lead(screen, target, reference="control_guides"):
    """The lead program-score test for one target: the linear model with the sample where its cells and the
    reference span two or more samples or lanes, or with the condition where one sample holds several conditions
    (Screen.crossed); else Wilcoxon on the scores."""
    tm, rm = sides(screen, target, reference)
    samples = len(set(screen.unit[tm | rm]))
    both = len(shared_units(screen, tm, rm, min_cells=1))  # the target's effect is estimable within such samples
    if samples > 1 and both >= 1:
        return {"method": "linear_model", "reason": f"the linear model with the sample, since the cells span {samples} "
                                                    f"samples or lanes, {both} of them holding both sides"}
    if samples == 1 and screen.crossed:
        return {"method": "linear_model", "reason": "the linear model with the condition, since the cells are in one "
                                                    "sample that holds several conditions"}
    why = "the cells are in one sample" if samples <= 1 else "no sample holds both the target's and the reference cells"
    return {"method": "wilcoxon", "reason": f"Wilcoxon on the scores, since {why}"}


STATE_LEAD = {"method": "fisher_same_samples",
              "reason": "Fisher's exact test on cells, against the control cells of the same samples"}


def state_lead(screen, target, reference="control_guides"):
    """The lead composition test for one target, from the design: propeller (speckle) on the proportions by
    replicate where 3 or more biological replicates hold both sides, as edgeR-QL leads the gene tests there; else
    Fisher's exact test on cells against the control cells of the same samples (docs/adr/0031, phase 2's notes)."""
    r = replicates(screen, target, reference)
    w, n = r["word"], r["n"]
    if r["biological"] and n >= policy.get("min_replicates"):
        return {"method": "propeller", "replicates": n,
                "reason": f"propeller (speckle) on the proportions by {w}, since {n} {_plural(w, n)} hold both the "
                          f"target's and the reference cells"}
    if r["biological"] and r["screen_units"] > 1:
        why = f"only {n} {_plural(w, n)} hold both the target's and the reference cells"
    elif r["screen_units"] > 1:
        why = f"the {r['screen_units']} samples are not biological replicates"
    else:
        why = "the screen is one sample, without replicates"
    own = "samples and conditions" if screen.crossed else "samples"
    return {"method": "fisher_same_samples", "replicates": n,
            "reason": f"Fisher's exact test on cells, against the control cells of the same {own}, since {why}"}


def depth_ratio(screen, target, reference="control_guides"):
    tm, rm = sides(screen, target, reference)
    if not tm.any() or not rm.any():
        return None
    return float(np.median(screen.libsize[tm]) / max(np.median(screen.libsize[rm]), 1))


def guides_with_cells(screen, target, min_cells=MIN_CELLS):
    """The target's own targeting guides that are the top guide of min_cells or more of its cells (a control guide,
    or another target's, is never one of its guides)."""
    tm = screen.cells_of(target)
    g = screen.guides
    of = dict(zip(g["guide"].astype(str), g["target"].astype(str)))
    targeting = set(g.loc[g["kind"].astype(str) == "targeting", "guide"].astype(str))
    own = {of.get(screen.label_guide(target))} | set(screen.members(target))
    mine = {x for x in targeting if of.get(x) in own}
    return [x for x in sorted(set(screen.top_guide[tm]) - {""})
            if x in mine and (tm & (screen.top_guide == x)).sum() >= min_cells]


def lead(kind, screen, target, reference="control_guides"):
    """A finding's lead test by its kind."""
    if kind == "gene":
        return gene_lead(screen, target, reference)
    if kind in ("gene_set", "score"):
        return score_lead(screen, target, reference)
    if kind == "state":
        return state_lead(screen, target, reference)
    return {"method": None, "reason": "not a module's test"}


def checks(kind, screen, target, reference="control_guides", lead_used=None, calling_rule=False):
    """A finding's checks, chosen from the design before any result: [{check, ...}], each either what will run or
    {not_run: why}. lead_used: the lead the finding came from (by default the design's)."""
    if kind in ("interaction", "custom"):
        why = ("a combination is checked by E.combinations' own agreement between units" if kind == "interaction"
               else "the agent's own code: its choices are not declared")
        return [{"check": "all", "not_run": why}]
    used = lead_used or lead(kind, screen, target, reference)
    out = []
    guides = guides_with_cells(screen, target)
    out.append({"check": "guides_agree", "guides": guides} if len(guides) >= MIN_GUIDES else
               {"check": "guides_agree", "not_run": f"{len(guides)} guide with {MIN_CELLS} cells"
                if len(guides) == 1 else f"no guide with {MIN_CELLS} cells"})
    if kind in ("gene", "gene_set", "state", "score"):
        others = field_tests(kind, screen, target, used, reference)
        out.append({"check": "tests", "methods": others} if others else
                   {"check": "tests", "not_run": "the design admits no other of the field's tests for it here"})
    if kind in ("gene", "gene_set", "state", "score"):  # the lead's test on groups of control guides
        out.append({"check": "calibration", "method": used["method"]} if reference == "control_guides" else
                   {"check": "calibration", "not_run": "the reference is not the control cells"})
    if len(guides) < MIN_GUIDES:
        n_units = len(replicates(screen, target, reference)["shared"])
        out.append({"check": "half"} if n_units >= HALF_UNITS else
                   {"check": "half", "not_run": f"{n_units} units hold both sides: halves of the units need "
                                                f"{HALF_UNITS}, and halves of the cells are not separate data"})
    if calling_rule:
        out.append({"check": "deposit_calls"})
    return out


def field_tests(kind, screen, target, lead, reference="control_guides"):
    """The field's tests the design admits for a finding's kind, other than its lead: for a gene, Wilcoxon on cells
    against the control cells of the same samples (two or more samples) and against every control cell, MAST on
    cells, edgeR-QL and DESeq2 pseudobulk where MIN_REPLICATES biological replicates hold both sides; for a state,
    Fisher's exact test on cells against the control cells of the same samples, propeller where MIN_REPLICATES
    replicates hold both sides, the Cochran-Mantel-Haenszel test over 2 or more samples; for a programme's score, the
    linear model with the sample (2 or more samples holding both sides) and Wilcoxon. [{method, label}]."""
    r = replicates(screen, target, reference)
    n, word = r["n"], (r["word"] if r["biological"] else "sample")
    many = n >= policy.get("min_replicates")
    own = "samples and conditions" if screen.crossed else "samples"
    if kind == "gene":
        out = ([("wilcoxon_same_samples", f"Wilcoxon on cells, against the control cells of the same {own}")]
               if r["screen_units"] > 1 or screen.crossed else [])
        out += [("wilcoxon", "Wilcoxon on cells, against every control cell"), ("mast", "MAST on cells")]
        if many:
            out += [("edger_ql", f"edgeR-QL pseudobulk by {word}"), ("pydeseq2", f"DESeq2 pseudobulk by {word}")]
    elif kind == "state":
        out = [("fisher_same_samples", f"Fisher's exact test on cells, against the control cells of the same {own}")]
        if many:
            out.append(("propeller", f"propeller by {word}"))
        if n >= 2:
            out.append(("cmh", f"the Cochran-Mantel-Haenszel test over the {_plural(word, n)}"))
    else:
        out = [("wilcoxon", "Wilcoxon on the scores" + (", against control cells drawn within the samples and "
                                                         "conditions" if screen.crossed else ""))]
        if len(shared_units(screen, *sides(screen, target, reference), min_cells=1)) >= 1 and (
                r["screen_units"] > 1 or screen.crossed):
            out.append(("linear_model", "the linear model with the sample" if r["screen_units"] > 1 else
                        "the linear model with the condition"))
    return [{"method": m, "label": label} for m, label in out if m != lead.get("method")]


def halves_are_separate(screen, n_units):
    """Whether two halves of the data are separate data. Halves of the units are (HALF_UNITS units or more). Halves of
    the cells are only in vitro: in vivo a guide's cells are clones, so the halves of one guide share its clones and
    inflate the evidence, while in vitro they behave as independent. A screen whose context is not stated is treated
    as in vivo here."""
    from .question_modules.data import value
    return n_units >= HALF_UNITS or value(screen.design, "experiment.context") in ("primary_in_vitro", "cell_line")


def halves(screen, half, units=None, seed=0):
    """A fixed split of the data in two: the units in two groups where there are HALF_UNITS or more, else the
    cells. Returns the mask of half 1 or 2."""
    units = screen.units() if units is None else units
    if len(units) >= HALF_UNITS:
        rng = np.random.default_rng(seed)
        first = set(rng.permutation(units)[: len(units) // 2])
        a = np.isin(screen.unit, list(first))
    else:
        a = np.random.default_rng(seed).random(len(screen.label)) < 0.5
    return a if half == 1 else ~a


def plan_leads(screen_dir, design, min_cells=MIN_CELLS):
    """The tests the design chooses, for the analysis plan (D1): from the screen object's cell table, without the
    counts. Per kind of analysis, each lead with its reason and the targets it covers, and the facts they rest on."""
    import os
    import anndata as ad
    import pandas as pd
    import scipy.sparse as sp
    from .question_modules.data import Screen
    obs = pd.read_csv(os.path.join(screen_dir, "cells.tsv.gz"), sep="\t", dtype=str, keep_default_na=False)
    a = ad.AnnData(sp.csr_matrix((len(obs), 0)), obs=obs.set_index("barcode"))
    s = Screen(screen_dir, design, adata=a)
    by = {}
    for t in s.targets(min_cells):
        ld = gene_lead(s, t)
        by.setdefault((ld["method"], ld["reason"]), []).append(t)
    genes = [{"test": m, "reason": why, "targets": len(ts)} for (m, why), ts in
             sorted(by.items(), key=lambda kv: -len(kv[1]))]
    by = {}
    for t in s.targets(min_cells):
        ld = state_lead(s, t)
        by.setdefault((ld["method"], ld["reason"]), []).append(t)
    states = [{"test": m, "reason": why, "targets": len(ts)} for (m, why), ts in
              sorted(by.items(), key=lambda kv: -len(kv[1]))]
    return {"genes (C.genes, C.within_states, D.cutting_response)": genes,
            "program scores (C.programs)": "the linear model with the sample where a target's cells and the "
                                               "control cells span 2 or more samples or lanes, with the condition "
                                               "where one sample holds several conditions, else Wilcoxon on the "
                                               "scores",
            "states (C.states_and_composition)": states, "facts": facts(s)}


def facts(screen):
    """The design facts every table reads, for the analysis plan."""
    ctrl = screen.label == "control"
    n_ctrl_guides = len(set(screen.top_guide[ctrl]) - {""})
    units = screen.units()
    return {"biological_replicates": bool(screen.independent_units()), "unit": unit_word(screen),
            "units": len(units),
            "units_with_control_cells": sum(int((ctrl & (screen.unit == u)).sum()) >= MIN_CELLS for u in units),
            "control_guides": n_ctrl_guides, "thresholds": {"replicates": MIN_REPLICATES, "cells": MIN_CELLS,
                                                            "guides": MIN_GUIDES}}
