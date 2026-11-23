"""Question D: what the perturbations share, and what the reference adds."""
import os

import numpy as np

from .. import policy
import pandas as pd

from . import figures
from .contract import Contract, Result, register

MIN_CELLS = 30  # a profile over thousands of genes needs more cells than one gene's test
REFERENCE_MULTIPLE = 3  # D.common_response's drawn reference: this many times the target's cells


def _mean(L, mask):
    idx = np.flatnonzero(mask)
    return np.asarray(L[idx].sum(0)).ravel() / max(len(idx), 1)


def _corr(a, b):
    if np.std(a) == 0 or np.std(b) == 0:
        return np.nan
    return float(np.corrcoef(a, b)[0, 1])


# ---- a response common to the perturbations -------------------------------------------------------------------------
@register("D.common_response", Contract(
    serves="D.common_response", reference="other_targets",
    applies_when="3 or more targets",
    needs="labels, units, the control cells",
    returns="each target's energy distance from the other targets' cells (C.any_effect's test, pertpy's "
            "energy-distance test, with the other targets as the reference, as five published screens take 'all "
            "other cells'; drawn at random, seeded, to 3 times the target's cells and at least 500 unless "
            "reference_cells is 'all'): a response every perturbation shares sits in that reference too, so the "
            "targets that still differ have effects of their own; read beside C.any_effect against the control cells",
    fails_when=[("a response most perturbations share makes each target differ from the control cells",
                 "this test against the other targets' cells, which hold the shared part"),
                ("with few targets, the other targets' cells are mostly one or two perturbations",
                 "the reference's cells, given per target"),
                ("control guides that skip a step every targeting guide takes (cutting)",
                 "D.cutting_response, where the library has guides that cut where no gene is beside non-targeting "
                 "ones")],
    tested_against="the synthetic screen: a shift planted in every targeting group makes every target differ from "
                   "the control cells; with 20 targets, against the other targets only the target with an effect of "
                   "its own differs, and with 6 every target does, as that target's cells are a fifth of each "
                   "other's reference (tests/test_common_response_and_field_measures.py)",
    choices={"permutations": [1000, 200], "reference_cells": ["drawn", "all"]}))
def common_response(screen, design, out_dir, permutations=1000, seed=0, reference_cells="drawn"):
    from .c import MIN_REFERENCE_DRAW, any_effect
    # every other target's cells make each test's cost grow with the square of the screen's cells; drawn, the
    # reference keeps REFERENCE_MULTIPLE times the target's cells
    drawn = reference_cells == "drawn"
    r = any_effect(screen, design, out_dir, reference="other_targets", permutations=permutations, seed=seed,
                   max_reference=REFERENCE_MULTIPLE if drawn else None)
    if r.status in ("detected", "not_detected"):
        n = r.diagnostics.get("targets_with_an_effect", 0)
        from ..steps.any_effect import CAP
        how = (f"drawn at random (seeded) to {REFERENCE_MULTIPLE} times each target's cells, at least "
               f"{MIN_REFERENCE_DRAW}, then each side to at most {CAP} cells" if drawn else "all of them")
        r.summary = (f"{n} of {len(r.table)} targets differ from the other targets' cells ({how}), which hold any "
                     "response the perturbations share; " + r.summary)
        r.diagnostics["reference_cells"] = reference_cells
    return r


# ---- the shared response: off the product path ----------------------------------------------------------------------
# No published screen correlates each target's profile with the mean of all targets' profiles, and this one measured
# the shared reference's noise (development session 1, CRISPRai: 0.55 from cell numbers alone); D.common_response
# answers the question by the field's reference instead (docs/build/development_sessions/measures_without_field_basis.md).
def shared_response(screen, design, out_dir, min_cells=None, detection=0.1):
    min_cells = policy.get("min_cells_profile") if min_cells is None else min_cells
    L = screen.lognorm
    control = screen.reference("control_guides")
    rows, null = [], []
    for u in screen.units():
        mr = control & (screen.unit == u)
        if mr.sum() < min_cells:
            continue
        keep = np.flatnonzero(screen.detected(mr, detection))
        Lu = L[:, keep]
        base = _mean(Lu, mr)
        profiles = {t: _mean(Lu, screen.cells_of(t) & (screen.unit == u)) - base
                    for t in screen.targets(min_cells) if (screen.cells_of(t) & (screen.unit == u)).sum() >= min_cells}
        if len(profiles) < 2:
            continue
        P = np.vstack(list(profiles.values()))
        shared = P.mean(0)
        for k, (t, p) in enumerate(profiles.items()):
            others = np.delete(P, k, axis=0).mean(0)
            rows.append({"group": t, "kind": "target", "unit": u, "correlation": _corr(p, shared),
                         "correlation_without_itself": _corr(p, others), "genes": len(keep),
                         "cells": int((screen.cells_of(t) & (screen.unit == u)).sum())})
        for g in sorted(set(screen.top_guide[mr]) - {""}):
            mg = mr & (screen.top_guide == g)
            if mg.sum() < min_cells or (mr & ~mg).sum() < min_cells:
                continue
            p = _mean(Lu, mg) - _mean(Lu, mr & ~mg)
            null.append({"group": g, "kind": "control guide", "unit": u, "correlation": _corr(p, shared),
                         "correlation_without_itself": _corr(p, shared), "genes": len(keep), "cells": int(mg.sum())})
    df = pd.DataFrame(rows + null, columns=["group", "kind", "unit", "correlation", "correlation_without_itself",
                                            "genes", "cells"])
    path = os.path.join(out_dir, "figure.png")
    if not rows:
        checks = figures.bars(np.zeros(1), ["none"], path, "No unit with two targets and control cells", "cells")
        return Result("not_enough_data", df, {}, path, checks,
                      f"no unit with {min_cells} control cells and two targets of {min_cells} cells")
    t = df[df["kind"] == "target"]
    per_unit = t.groupby("unit")["correlation"].median().round(3).to_dict()
    null_med = df[df["kind"] == "control guide"].groupby("unit")["correlation"].median().round(3).to_dict()
    shown = df.assign(group=np.where(df["kind"] == "control guide", df["group"] + " (control)", df["group"]))
    checks = figures.dots(shown, "group", "correlation", "unit", path,
                          "Each profile's correlation with the unit's shared profile, per unit",
                          "Correlation with the shared profile", reference=0.0, max_groups=100)
    # the numbers, without a status: the cut-offs that gave one came from an earlier hand analysis, not a test
    return Result("measured", df,
                  {"median_correlation_by_unit": per_unit, "control_guides_median_by_unit": null_med,
                   "min_cells": min_cells},
                  path, checks, f"median correlation with the shared profile by sample: targets {per_unit}; "
                                f"control guides {null_med or 'none'}")


# ---- cutting response ----------------------------------------------------------------------------------------------
# control guides that act on DNA where no gene is: they cut with Cas9, or bind with a dead Cas9 (CRISPRi, CRISPRa)
CUTTING_KINDS = ("intergenic", "safe_targeting", "cutting_control")


@register("D.cutting_response", Contract(
    serves="D.cutting_response",
    applies_when="control guides that target DNA where no gene is (intergenic, safe-targeting) beside non-targeting "
                 "ones, under any modality",
    needs="counts, labels, units, and control guides that target DNA where no gene is (intergenic, safe-targeting) "
          "beside non-targeting ones",
    returns="the cells of the control guides that target where no gene is against the non-targeting control cells: "
            "their energy "
            "distance (C.any_effect's test, against the control cells of the same samples) and the genes that differ (the test the design chooses, "
            "as C.genes: edgeR-QL pseudobulk by replicate where 3 or more biological replicates hold both, else "
            "scanpy's Wilcoxon on cells; q < 0.05), with the cells of each; the package calls",
    fails_when=[("the two kinds of control sit in different samples", "the energy distance compares within samples; "
                 "the gene test is by replicate where the design has 3 or more biological replicates"),
                ("a few control guides are clones",
                 "B.clonality's top-guide shares of each control group")],
    tested_against="the synthetic screen: a shift planted in the cutting controls is found, and nothing where none "
                   "is planted",
    choices={}))
def cutting_response(screen, design, out_dir, permutations=1000, seed=0):
    """Safe-cutting or intergenic control guides against non-targeting ones, for the response to cutting itself
    (Yao et al. 2023; published_screens_review/5)."""
    from . import charts
    from .c import ADJUSTED, GENES_Q, any_effect, genes_by_leads, lead_words, leads_of
    kind = dict(zip(screen.guides["guide"].astype(str), screen.guides["kind"].astype(str)))
    k = np.array([kind.get(g, "") for g in screen.top_guide])
    control = screen.label == "control"
    cutting, plain = control & np.isin(k, CUTTING_KINDS), control & (k == "non_targeting")
    path = os.path.join(out_dir, "figure.png")
    need = 10
    if cutting.sum() < need or plain.sum() < need:
        checks = figures.bars(np.zeros(1), ["none"], path, "No cutting and non-targeting controls", "cells")
        return Result("not_enough_data", pd.DataFrame(), {"cutting_cells": int(cutting.sum()),
                                                         "non_targeting_cells": int(plain.sum())}, path, checks,
                      f"needs control guides that cut and non-targeting ones, {need} cells each")
    s = screen.with_labels(np.where(cutting, "cutting controls", np.where(plain, "control", "none")))
    from ..steps import any_effect as distance
    per, distance_calls, _ = distance.test(s, "control_guides", permutations=permutations, seed=seed)
    leads = leads_of(s, ["cutting controls"], "control_guides")
    table, calls = genes_by_leads(s, leads, "control_guides", os.path.join(out_dir, "genes"))
    changed = table[table["q"] < GENES_Q]
    checks = charts.checks(charts.volcano(table, path, "Cutting control cells against non-targeting control cells",
                                          threshold=GENES_Q))
    dist = per.iloc[0] if len(per) else None
    guides = {"cutting": sorted(set(screen.top_guide[cutting]) - {""}),
              "non_targeting": sorted(set(screen.top_guide[plain]) - {""})}
    differs = dist is not None and float(dist["q"]) < ADJUSTED
    words = (f"cutting control cells ({int(cutting.sum())}, {len(guides['cutting'])} guides) against non-targeting "
             f"control cells ({int(plain.sum())}, {len(guides['non_targeting'])} guides) of the same samples: "
             + (f"energy distance {float(dist['e_distance']):.3f} (p {float(dist['p']):.3g}); " if dist is not None
                else "") + f"{changed['gene'].nunique()} genes differ at q < {GENES_Q} ({lead_words(leads)})")
    return Result("detected" if differs or len(changed) else "not_detected", table,
                  {"guides": guides, "genes_changed": int(changed["gene"].nunique()),
                   "test": leads["cutting controls"],
                   "energy_distance": None if dist is None else dist.to_dict(),
                   "package_calls": calls + distance_calls},
                  path, checks, words, tables={"changed_genes": changed})


# ---- the choice of reference: off the product path ------------------------------------------------------------------
# Correlating profiles taken under two references is Pertura's own construction (no published screen), and it shares
# the shared-reference noise of shared_response above (docs/build/development_sessions/measures_without_field_basis.md).
def the_choice_of_reference(screen, design, out_dir, min_cells=None, detection=0.1):
    min_cells = policy.get("min_cells_profile") if min_cells is None else min_cells
    L = screen.lognorm
    refs = {"control cells": screen.reference("control_guides"), "other targets": screen.reference("other_targets"),
            "no guide": screen.reference("no_guide")}
    rows, shifts = [], []
    for u in screen.units():
        here = {k: m & (screen.unit == u) for k, m in refs.items()}
        here = {k: m for k, m in here.items() if m.sum() >= min_cells}
        if len(here) < 2:
            continue
        union = np.logical_or.reduce(list(here.values()))
        keep = np.flatnonzero(screen.detected(union, detection))
        Lu = L[:, keep]
        means = {k: _mean(Lu, m) for k, m in here.items()}
        targets = [t for t in screen.targets(min_cells) if (screen.cells_of(t) & (screen.unit == u)).sum() >= min_cells]
        prof = {}
        for t in targets:
            mt = screen.cells_of(t) & (screen.unit == u)
            mt_mean = _mean(Lu, mt)
            for k, m in here.items():
                base = _mean(Lu, m & ~mt) if k == "other targets" else means[k]
                prof[(t, k)] = mt_mean - base
        names = sorted(here)
        for i, a in enumerate(names):
            for b in names[i + 1:]:
                for t in targets:
                    rows.append({"target": t, "unit": u, "pair": f"{a} / {b}",
                                 "correlation": _corr(prof[(t, a)], prof[(t, b)])})
                if targets:
                    for x, y in ((a, b), (b, a)):  # x's shift from y, against the targets' mean profile against y
                        mean_against_y = np.mean([prof[(t, y)] for t in targets], axis=0)
                        shifts.append({"unit": u, "reference": x, "against": y,
                                       "correlation_with_targets": _corr(means[x] - means[y], mean_against_y),
                                       "targets": len(targets), "genes": len(keep)})
    df = pd.DataFrame(rows, columns=["target", "unit", "pair", "correlation"])
    sh = pd.DataFrame(shifts, columns=["unit", "reference", "against", "correlation_with_targets", "targets", "genes"])
    path = os.path.join(out_dir, "figure.png")
    if df.empty:
        checks = figures.bars(np.zeros(1), ["none"], path, "Fewer than two references with enough cells", "cells")
        return Result("not_enough_data", df, {}, path, checks,
                      f"no unit with two candidate references of {min_cells} cells and a target of {min_cells}")
    checks = figures.dots(df, "pair", "correlation", "unit", path,
                          "Each target's profile against one reference, correlated with its profile against another",
                          "Correlation of a target's profiles\nagainst the two references", reference=0.0)
    medians = df.groupby("pair")["correlation"].median().round(3).to_dict()
    # the numbers, without a status: the cut-offs at 0.5 that gave one have no published source
    return Result("measured", df, {"median_correlation_by_pair": medians, "min_cells": min_cells},
                  path, checks, f"median agreement by pair of references: {medians}",
                  tables={"reference_shifts": sh})
