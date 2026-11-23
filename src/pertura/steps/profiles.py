"""Profiles (docs/adr/0031, D1a): each label's expression profile against its reference cells, per sample, written
once; and the replicate agreement read from them.

A profile is the label's cells' mean log-normalised expression minus the mean of its reference cells in the same
sample, over a set of genes:
- detected: genes detected in DETECTION of the sample's reference cells (E.similar_targets);
- well_expressed: genes with WELL_EXPRESSED UMIs per reference cell or more, the genes Replogle et al. 2022
  correlate profiles over (F.guides_agree);
- all: every gene.
The reference is the design's for each label (its arm's control cells where the design matches references, ADR
0026), in the same sample.

The replicate agreement is the field's reproducibility check, run once across the screen before replicates are
pooled: each pair of replicates' profiles of the same targets, correlated (Pearson) over the genes, for every target
together (the screen's r) and for each target.

    by_unit = profiles(screen, labels, genes="detected")          # {unit: DataFrame labels x genes}
    pairs, per_target = replicate_agreement(screen)

A module that pools a label's profiles over samples pools them only within one kind of capture (the delivery, the
batch and the design's other grouping factor), weighted by cells (pooled_within_kinds), and says where two kinds
disagree (kinds_disagree).
"""
import itertools

import numpy as np

from .. import policy
import pandas as pd

DETECTION = 0.1        # a gene detected in this share of the reference cells counts as detected
WELL_EXPRESSED = 0.25  # UMIs per reference cell (Replogle et al. 2022)
MIN_CELLS = 30         # a label's cells in a sample for its profile there (a profile over thousands of genes)
GENE_SETS = ("detected", "well_expressed", "all")


def _genes(screen, reference_cells, genes):
    if genes == "all":
        return np.arange(len(screen.genes))
    if genes == "detected":
        return np.flatnonzero(screen.detected(reference_cells, DETECTION))
    if genes == "well_expressed":
        idx = np.flatnonzero(reference_cells)
        return np.flatnonzero(np.asarray(screen.X[idx].sum(0)).ravel() >= WELL_EXPRESSED * max(len(idx), 1))
    raise ValueError(f"genes is one of {list(GENE_SETS)}, not {genes!r}")


def profiles(screen, labels=None, units=None, genes="detected", min_cells=None, reference="control_guides",
             min_reference=None):
    """{unit: DataFrame (labels x genes)}: each label's profile in each sample where it has min_cells cells and its
    reference min_reference (min_cells where not given). The genes are chosen on the sample's reference cells (the
    control cells; for a label of one arm, its arm's). Where a sample holds several conditions, the reference's mean is
    taken in the label's mix of them (Screen.baseline)."""
    min_cells = policy.get("min_cells_profile") if min_cells is None else min_cells
    min_reference = min_cells if min_reference is None else min_reference
    L = screen.lognorm
    labels = list(labels) if labels is not None else [x for x in screen.targets(min_cells)]
    out = {}
    for u in (units if units is not None else screen.units()):
        here = screen.unit == u
        rows, gene_idx = {}, None
        # the genes are chosen on the control cells of each arm profiled here, kept where every one of them holds
        # them, so profiles of two arms share their genes and a third arm's cells choose none (bug register, BR04)
        arms = {}
        for lab in labels:
            if ((screen.label == lab) & here).any():
                arms.setdefault(screen.arm_of(lab) if reference != "no_guide" else "", screen.reference(
                    reference, target=lab) & here)
        bases = [m for m in arms.values() if m.sum() >= min_reference]
        if not bases:
            continue
        chosen = [set(_genes(screen, m, genes)) for m in bases]
        gene_idx = np.array(sorted(set.intersection(*chosen)), dtype=int)
        Lu = L[:, gene_idx]
        for lab in labels:
            m = (screen.label == lab) & here
            ref = screen.reference(reference, target=lab) & here & ~m
            if m.sum() < min_cells or ref.sum() < min_reference:
                continue
            # the reference read in the label's mix of the unit's conditions, where a unit holds several
            base, m = screen.baseline(Lu, m, ref, min_reference)
            if base is None or m.sum() < min_cells:
                continue
            rows[lab] = np.asarray(Lu[np.flatnonzero(m)].mean(0)).ravel() - base
        if rows:
            out[u] = pd.DataFrame(rows, index=screen.genes[gene_idx]).T
    return out


ACROSS = "across kinds"  # two labels that share no kind of capture, each pooled over all its captures


def pooled_within_kinds(by_unit, cells, kind_of):
    """Each label's profile pooled within each kind of capture (Screen.capture_kind), weighted by its cells there:
    captures of different kinds (singles and pairs, two batches, two effectors) are never averaged into one profile.
    by_unit: {unit: {label: Series over genes}}; cells: {(unit, label): cells}; kind_of: {unit: kind}. A gene a unit
    lacks counts in the others. Returns ({kind: {label: Series}}, {(kind, label): cells})."""
    parts = {}
    for u, p in by_unit.items():
        for lab, s in p.items():
            parts.setdefault((kind_of.get(u, ""), lab), []).append((s, cells[(u, lab)]))
    out, n = {}, {}
    for (k, lab), items in parts.items():
        df = pd.concat([s for s, _ in items], axis=1)
        w = np.array([c for _, c in items], dtype=float)
        x = df.to_numpy(dtype=float)
        have = np.isfinite(x)
        out.setdefault(k, {})[lab] = pd.Series(np.where(have, x, 0.0) @ w / np.maximum(have @ w, 1e-12),
                                               index=df.index)
        n[(k, lab)] = int(w.sum())
    return out, n


def kinds_disagree(values, past=None):
    """What kinds of capture disagree on, in words: the sign of their values, or the value past the null in one kind
    and not in another (past: {kind: bool}); '' when they agree or there is one kind."""
    v = {k: float(x) for k, x in values.items() if x is not None and np.isfinite(x)}
    if len(v) < 2:
        return ""
    said = []
    if min(v.values()) < 0 < max(v.values()):
        said.append("the sign differs (" + "; ".join(f"{k}: {x:.2f}" for k, x in v.items()) + ")")
    p = {k: bool(b) for k, b in (past or {}).items() if k in v}
    if any(p.values()) and not all(p.values()):
        said.append("past the null in " + ", ".join(k for k, b in p.items() if b) + ", not in "
                    + ", ".join(k for k, b in p.items() if not b))
    return "; ".join(said)


def replicate_agreement(screen, labels=None, units=None, genes="well_expressed", min_cells=None,
                        reference="control_guides"):
    """Each pair of replicates' profiles of the same targets, correlated over the genes both hold: (pairs, per_target).
    pairs: unit_a, unit_b, targets, r (every target's profile together, Pearson), median_target_r; per_target:
    target, unit_a, unit_b, r."""
    min_cells = policy.get("min_cells_profile") if min_cells is None else min_cells
    by_unit = profiles(screen, labels, units, genes, min_cells, reference)
    pair_rows, target_rows = [], []
    for a, b in itertools.combinations(sorted(by_unit), 2):
        A, B = by_unit[a], by_unit[b]
        shared_t = sorted(set(A.index) & set(B.index))
        shared_g = sorted(set(A.columns) & set(B.columns))
        if len(shared_t) < 2 or len(shared_g) < 10:
            continue
        x, y = A.loc[shared_t, shared_g].to_numpy(), B.loc[shared_t, shared_g].to_numpy()
        rs = []
        for i, t in enumerate(shared_t):
            r = _r(x[i], y[i])
            rs.append(r)
            target_rows.append({"target": t, "unit_a": a, "unit_b": b, "r": r, "genes": len(shared_g)})
        pair_rows.append({"unit_a": a, "unit_b": b, "targets": len(shared_t), "genes": len(shared_g),
                          "r": _r(x.ravel(), y.ravel()), "median_target_r": float(np.nanmedian(rs))})
    pairs = pd.DataFrame(pair_rows, columns=["unit_a", "unit_b", "targets", "genes", "r", "median_target_r"])
    per_target = pd.DataFrame(target_rows, columns=["target", "unit_a", "unit_b", "r", "genes"])
    return pairs, per_target


def _r(x, y):
    ok = np.isfinite(x) & np.isfinite(y)
    if ok.sum() < 3 or np.std(x[ok]) == 0 or np.std(y[ok]) == 0:
        return np.nan
    return float(np.corrcoef(x[ok], y[ok])[0, 1])
