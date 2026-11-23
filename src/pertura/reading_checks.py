"""What a screen object read, checked against facts any correctly read screen satisfies (docs/adr/0041, step 1a).
Whoever read the deposit, Pertura's readers or the agent's own code, the result is counted the same way. A check that
does not hold is a fact for the agent and the user to explain; the design may expect it (a sample without a guide
library holds no guides), so none blocks. The build's own flags (guides in no target, units without control cells)
stand beside these.

Each misread these catch went unnoticed or nearly so in a development run:
- library targets without cells: GSE263761's deposit calls made nearly every cell multi-guide, 8 of 135 targets left;
- samples whose cells carry no guide: GSE267740's NTN_1, whose guides were rows of its expression matrix, read
  without guides in two runs;
- samples whose cells mostly carry several targets, and conditions with almost no cell of one target: GSE263761's
  deposit calls again, which left five of nine captures, its 24-hour condition among them, with a few cells of one
  target each, while every target kept cells in the other four;
- samples holding the same cells: GSE250558's two libraries of each capture, read as 12 samples;
- matrices of every droplet: GSE335887's raw matrices beside the filtered ones;
- cells without a unit or a condition the design gives;
- conditions holding one unit: development session 10's hashtags, each one condition's cells, read as donors
  (docs/adr/0041, step 4a: the check holds whatever told the units apart);
- expression features named as the library's guides: until step 6 every module dropped them unsaid, and with them a
  target's own gene where a guide bore its name."""
import pandas as pd

from .design_gate import condition_names
from .design_gate import node as _node

MOSTLY = 0.5  # the share of a sample's cells with guides of several targets past which it is named
ALMOST_NONE = 0.05  # the share of a condition's cells of one target or a control below which it is named


def _some(names, n=4):
    names = [str(x) for x in names]
    return ", ".join(names[:n]) + (f" and {len(names) - n} more" if len(names) > n else "")


def _targets(design, table):
    tmap = (_node(design, "perturbation.target_map") or {}).get("value")
    return set(map(str, tmap)) if isinstance(tmap, dict) else {t for t, k in table.values() if k == "targeting"}


def outside_labels(labels, design, table):
    """The cell labels that are no target of the design and none of the labels that hold no target (NOT_TARGETS);
    a set ("a+b") or a pair ("a|b") is known when each of its parts is."""
    from .question_modules.data import NOT_TARGETS
    known = _targets(design, table) | set(NOT_TARGETS)
    return sorted(x for x in set(map(str, labels)) if not set(x.replace("|", "+").split("+")) <= known)


def check(cells, design, table, genes=()):
    """The reading checks on a build's cells (cells.tsv's columns: barcode as sample:barcode, sample, unit, label,
    condition, umis) under its confirmed design; table: guide -> (target, kind), the build's guide table; genes: the
    names of the expression features read. Returns one {check, text, ...} per fact that does not hold, its text in
    plain words; [] when every one holds."""
    from .design_audit import inventory as inv
    from .question_modules.data import UNASSIGNED
    out = []
    labels = set(cells["label"].astype(str))
    targets = _targets(design, table)
    empty = sorted(t for t in targets if t not in labels)
    if empty:
        out.append({"check": "library targets without cells", "targets": empty,
                    "text": f"Library targets without cells: {len(empty)} of {len(targets)} ({_some(empty)})"})
    outside = outside_labels(labels, design, table)
    if outside:
        out.append({"check": "labels outside the design", "labels": outside,
                    "text": f"Cell labels that are no target or control of the design: {len(outside)} "
                            f"({_some(outside)})"})
    named = sorted(set(map(str, genes)) & set(map(str, table)))
    if named:
        out.append({"check": "genes named as the library's guides", "features": named,
                    "text": f"Expression features named as the library's guides: {len(named)} ({_some(named)}); every "
                            "module reads them as genes: guide counts read as genes are retyped as guides "
                            "(cell_table.retype), and genes a guide is named after stay genes"})
    by_sample = dict(tuple(cells.groupby("sample")))
    bare = sorted(s for s, c in by_sample.items() if (c["label"].astype(str) == "none").all())
    if bare:
        out.append({"check": "samples whose cells carry no guide", "samples": bare,
                    "text": f"Samples whose cells carry no guide: {len(bare)} ("
                            + _some(f"{s}, {len(by_sample[s]):,} cells" for s in bare) + "); a sample without a "
                            "guide library, or guides the reading missed"})
    multi = {s: float((c["label"].astype(str) == "multi").mean()) for s, c in by_sample.items()}
    most = sorted((s for s, m in multi.items() if m > MOSTLY), key=lambda s: (-multi[s], s))  # the most first
    if most:
        out.append({"check": "samples whose cells mostly carry several targets", "samples": most,
                    "text": f"Samples whose cells mostly carry guides of several targets: {len(most)} of "
                            f"{len(by_sample)} (" + _some(f"{s}, {multi[s]:.0%} of {len(by_sample[s]):,} cells"
                                                          for s in most)
                            + "); ambient guide reads or the calling rule can make them, and these cells leave "
                            "every comparison: a dominance rule (check_design with changes to perturbation.assignment, "
                            "rule dominance) says what it keeps"})
    barcodes = {s: set(c["barcode"].astype(str).str.split(":", n=1).str[-1]) for s, c in by_sample.items()}
    same, _ = inv.same_cells(barcodes)
    if same:
        pairs = same[0]["value"]
        out.append({"check": "samples holding the same cells", "pairs": [p["samples"] for p in pairs],
                    "text": f"Pairs of samples holding the same cells: {len(pairs)} (" + _some(
                        f"{a} and {b}, {p['shared_barcodes']:,} barcodes in common"
                        for p in pairs for a, b in [p["samples"]]) + "); one capture read twice counts its cells "
                        "and its unit twice"})
    if "umis" in cells:
        droplets, _ = inv.raw_droplets({s: c["umis"].to_numpy() for s, c in by_sample.items()})
        if droplets:
            raw = droplets[0]["value"]
            out.append({"check": "matrices of every droplet", "samples": sorted(raw),
                        "text": f"Samples holding every droplet, not called cells: {len(raw)} (" + _some(
                            f"{s}, {v['barcodes']:,} barcodes, {v['with_100_umis_or_more']:,} with 100 UMIs or more"
                            for s, v in raw.items()) + ")"})
    from .cell_table import EMPTY
    from .question_modules.data import _text  # a missing value as '' (pandas 3's astype(str) keeps NaN)
    unit = pd.Series(_text(cells, "unit"), index=cells.index)
    lost = unit.str.endswith(UNASSIGNED) | unit.isin(EMPTY)
    if lost.any():
        out.append({"check": "cells without a unit", "cells": int(lost.sum()),
                    "text": f"Cells without a unit: {int(lost.sum()):,} (" + _some(
                        f"{s}, {n:,}" for s, n in cells.loc[lost, "sample"].value_counts().items()) + ")"})
    conditions = condition_names((_node(design, "experiment.conditions") or {}).get("value"))
    if len(conditions) > 1 and "condition" in cells:
        # a cell without the level of one of crossed factors has no condition, whatever its combination reads
        none = pd.Series(_text(cells, "condition"), index=cells.index).isin(EMPTY)
        for col in [c for c in cells.columns if str(c).startswith("condition_")]:
            none |= pd.Series(_text(cells, col), index=cells.index).isin(EMPTY)
        if none.any():
            out.append({"check": "cells without a condition", "cells": int(none.sum()),
                        "text": f"Cells without a condition, though the design has {len(conditions)}: "
                                f"{int(none.sum()):,} (" + _some(
                                    f"{s}, {n:,}" for s, n in cells.loc[none, "sample"].value_counts().items()) + ")"})
        from .question_modules.data import NOT_TARGETS
        single = ~cells["label"].astype(str).isin(set(NOT_TARGETS) - {"control"})  # a target, a set or a control
        share = single.groupby(cells["condition"].astype(str)).mean()
        empty = {c: int(single[cells["condition"].astype(str) == c].sum()) for c in share.index
                 if c not in ("", "nan") and share[c] < ALMOST_NONE}
        if empty:
            n = cells["condition"].astype(str).value_counts()
            out.append({"check": "conditions with almost no cell of one target", "conditions": sorted(empty),
                        "text": "Conditions with almost no cell of one target or a control: " + _some(
                            f"{c}, {k:,} of {n[c]:,} cells" for c, k in sorted(empty.items()))
                                + "; nothing in them can be compared"})
        placed = cells.loc[~lost & ~none]
        per = {str(c): sorted(set(map(str, u))) for c, u in placed.groupby(placed["condition"].astype(str))["unit"]}
        one = {c: us[0] for c, us in per.items() if len(us) == 1}
        if one:  # whatever told the units apart, a condition of one unit has no replicate to compare
            out.append({"check": "conditions holding one unit", "conditions": sorted(one),
                        "text": f"Conditions holding one unit: {len(one)} of {len(per)} ("
                                + _some(f"{c}: {u}" for c, u in sorted(one.items())) + "); where each side of a "
                                "comparison is one unit, a difference between the conditions cannot be told from one "
                                "between the units"})
    return out


def samples_read(cells):
    """Each sample's barcodes, those with 100 UMIs or more, and the two samples sharing the most barcodes with the
    share of the smaller one's: shown beside every build, whether or not a check fires (docs/adr/0041, step 6: the
    checks' cut-offs decide a flag, never what is in view)."""
    per = {str(s): {"barcodes": int(len(c)), "with_100_umis_or_more": int((c["umis"] >= 100).sum())}
           for s, c in cells.groupby("sample")} if "umis" in cells else {}
    sets = {str(s): set(c["barcode"].astype(str).str.split(":", n=1).str[-1]) for s, c in cells.groupby("sample")}
    names, best = sorted(sets), None
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            n = len(sets[a] & sets[b])
            share = n / max(min(len(sets[a]), len(sets[b])), 1)
            if best is None or share > best["share_of_the_smaller"]:
                best = {"samples": [a, b], "shared_barcodes": n, "share_of_the_smaller": round(share, 4)}
    return {"by_sample": per, "most_shared": best}
