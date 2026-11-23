"""Question A: what was done."""
import os

import pandas as pd

from . import figures
from .contract import Contract, Result, register
from .data import NOT_TARGETS, Screen

KINDS = ("one target", "two or more targets", "control cells", "ambiguous or several", "no guide", "failed QC")


def kind_of(label):
    """A cell's kind by its label: how many targets it carries (two or more: a pair, or a set delivered together),
    control, ambiguous or several, or no guide."""
    if label == "control":
        return "control cells"
    if label == "none":
        return "no guide"
    if label == "failed QC":
        return "failed QC"
    if label in NOT_TARGETS:
        return "ambiguous or several"
    return "two or more targets" if len(Screen.members(str(label))) > 1 else "one target"


@register("A.what_was_done", Contract(
    serves="A.what_was_done",
    applies_when="always",
    needs="the screen object (cells, labels, units, conditions) and the confirmed design",
    returns="per unit: cells, and cells by kind (one target, two or more targets, control cells, ambiguous or "
            "several, no guide, failed QC); targets with at least 10 cells; the screen object's flags; per capture, "
            "the cells' UMIs, genes detected and mitochondrial and ribosomal shares (median, 5th and 95th "
            "percentiles), the confirmed quality rule and the cells it left out by reason (the table quality)",
    fails_when=[("the units are lanes of one pool, not animals or donors",
                 "the confirmed design's biological replicate (the design gate)"),
                ("a call rule can dilute some samples' groups more than others",
                 "the screen object's shares of cells with several guides or none dominant, by unit")],
    tested_against="seven learning screens: the cells per label, against earlier hand counts of each"))
def design_summary(screen, design, out_dir):
    df = pd.DataFrame({"unit": screen.unit, "kind": [kind_of(x) for x in screen.label]})
    t = pd.crosstab(df["unit"], df["kind"])
    for k in KINDS:
        if k not in t:
            t[k] = 0
    t = t[list(KINDS)]
    t.insert(0, "cells", t.sum(1))
    targets = screen.targets(min_cells=10)
    path = os.path.join(out_dir, "figure.png")
    from .. import quality
    measured = screen.qc_measures if screen.qc_measures is not None else quality.measures(screen)
    checks = figures.cells_and_quality(t, list(t.index), list(KINDS), measured, screen.capture, path,
                                       "Cells by kind in each unit, and their quality in each capture")
    checks.update(reference_shown=True)  # control cells are drawn in every unit
    table = t.reset_index()
    no_unit = int(screen.unassigned().sum())
    diagnostics = {"cells": int(t["cells"].sum()), "units": len(screen.units()), "cells_without_a_unit": no_unit,
                   "targets_with_10_cells": len(targets),
                   "control_cells": int(t["control cells"].sum()), "flags": screen.meta.get("flags", []),
                   "rule": screen.meta.get("rule"), "independent_units": screen.independent_units()}
    from .data import value
    rule = value(design, "data.qc")
    words = quality.describe(rule, value(design, "units.unit_column"))
    qc = quality.by_capture(screen, measured)
    left = {r: int(n) for r, n in pd.Series(screen.qc_reason)[pd.Series(screen.qc_reason) != ""].value_counts().items()}
    diagnostics.update(quality_rule=words, left_out_by_reason=left)
    summary = (f"{diagnostics['cells']} cells in {diagnostics['units']} units; {len(targets)} targets with 10+ "
               f"cells; {diagnostics['control_cells']} control cells; quality: {words}" +
               (f" ({', '.join(f'{n} {r}' for r, n in left.items())})" if left else " (no cell left out)") +
               (f"; {no_unit} cells whose unit could not be told" if no_unit and not left else ""))
    return Result("detected", table, diagnostics, path, checks, summary, tables={"quality": qc})
