"""The survey (docs/adr/0045): the comparisons the confirmed design calls for, each one module run with the field's
defaults, and each comparison's state read from the record's runs. The survey decides what is compared; how (the
reference, the unit, the test) is the modules' own, so an agent's call of the same view compares alike. It lists and
never gates: nothing waits for it. Not the map of the findings (screen_map.py)."""
import json
import os

import pandas as pd

from .design_gate import condition_factors, no_replicate
from .question_modules.data import NOT_TARGETS, value

CUTTING_KINDS = ("intergenic", "safe_targeting", "cutting_control")  # D.cutting_response's kinds of a cutting control
SIGNAL, NOTHING, DESCRIPTIVE = "computed, with a signal", "computed, nothing detected", "computed, descriptive"
NOT_COMPARABLE, RUNNING, FAILED = "not comparable", "queued or running", "failed"
STATES = (SIGNAL, NOTHING, DESCRIPTIVE, NOT_COMPARABLE, RUNNING, FAILED)
FROM_STATUS = {"detected": SIGNAL, "not_detected": NOTHING, "measured": DESCRIPTIVE, "not_enough_data": NOT_COMPARABLE}
ROWS = {"cells_per_target": "Cells per target", "own_transcript": "The target's own transcript",
        "distance": "Distance from the controls", "states": "States and their shares",
        "alike_opposite": "Targets alike and opposite", "baselines": "The samples' baselines",
        "control_kinds": "The two kinds of control", "controls_across": "The control cells across conditions",
        "shifts_per_condition": "Each condition's state shifts",
        "effect_changing": "A target's effect changing between conditions", "proteins": "Proteins",
        "guides_agree": "Each target's guides agreeing", "units_agree": "Each target across samples"}
SOURCE = "survey"  # the source the survey's runs carry, so ADR 0040's first-look sections leave them out
CONTROL_CELLS = 10  # E.condition_x_perturbation's least control cells in a condition; the build's cells, before the
# cells' rule, are as many or more, so fewer here can never be enough after it


def facts(design, screen_dir):
    """What the survey reads from the confirmed design and the screen object: the targets with cells, the samples
    with control cells, the kinds of control, the condition factors (each level's control cells), the antibodies,
    the most guides a target has in the library, and whether the design names an axis for the samples."""
    with open(os.path.join(screen_dir, "screen.json"), encoding="utf-8") as fh:
        meta = json.load(fh)
    try:  # the library's guides: those of a target, not a control's
        guides = pd.read_csv(os.path.join(screen_dir, "guides.tsv"), sep="\t", dtype=str, keep_default_na=False)
        kind = guides["kind"] if "kind" in guides else pd.Series("targeting", index=guides.index)
        own = guides[~kind.isin(("non_targeting",) + CUTTING_KINDS) & ~guides["target"].isin(NOT_TARGETS)]
        per_target = int(own.groupby("target")["guide"].nunique().max()) if len(own) else 0
    except (OSError, ValueError, KeyError):
        per_target = 0
    cells = pd.read_csv(os.path.join(screen_dir, "cells.tsv.gz"), sep="\t", dtype=str, keep_default_na=False,
                        usecols=lambda c: c in ("label", "control_kind", "sample") or c.startswith("condition"))
    control = cells["label"] == "control"
    factors = [f for f in condition_factors(value(design, "experiment.conditions")) if f["by"] == "column"]
    unit = value(design, "units.independent_unit")
    targets = sorted(lab for lab, n in (meta.get("labels") or {}).items()
                     if n and lab not in NOT_TARGETS and "|" not in lab)
    samples = sorted(cells.loc[control, "sample"].unique()) if "sample" in cells else []
    return {"targets": targets, "samples": samples,
            "control_kinds": sorted(set(cells.loc[control, "control_kind"]) - {""}),
            "factors": [{"name": f["name"], "levels": f["levels"], "compared": f["compared"]} for f in factors],
            "antibodies": len(meta.get("antibody_features") or []), "guides_per_target": per_target,
            "axis_for_samples": bool(factors) or not no_replicate(unit),
            "_cells": cells}


def _pairs(f, cells):
    """A factor's comparisons, each of its levels against the first one the design lists (its reference), the other
    factors held, in each layer of the cells (D1, D4): [(held words, the reference's condition, the other's condition,
    why it is not comparable or None)]. A layer without the reference keeps its comparisons, not comparable: another
    level as the reference would be another question. Cells outside the factor (no condition) are left out."""
    name, levels = f["name"], f["levels"]
    cells = cells[cells["condition"] != ""]
    if not levels:
        return []
    ref = levels[0]
    if "condition_" + name not in cells:  # one factor: the cells' condition is its level
        present = [lv for lv in levels if (cells["condition"] == lv).any()]
        why = None if ref in present else f"no cells at {ref}, the reference"
        return [("", ref, lv, why) for lv in present if lv != ref]
    others = [c for c in cells.columns if c.startswith("condition_") and c != "condition_" + name]
    out = []
    for held, part in (cells.groupby(others, sort=False) if others else [((), cells)]):
        held = held if isinstance(held, tuple) else (held,)
        if any(str(v) == "" for v in held):  # cells another factor does not reach
            continue
        named = {r["condition_" + name]: r["condition"]
                 for _, r in part.drop_duplicates(["condition_" + name]).iterrows() if r["condition_" + name] != ""}
        words = ", ".join(f"{c[len('condition_'):]} {v}" for c, v in zip(others, held))
        why = None if ref in named else f"no cells at {ref} within {words}, the reference"
        first = named.get(ref, f"{ref} ({words})")
        out += [(words, first, named[lv], why) for lv in levels if lv in named and lv != ref]
    return out


def comparisons(design, screen_dir):
    """The survey's comparisons, in the order they run (D2): first those the agent's first look runs too (the changed
    genes, the targets alike), so its calls take their runs; then the cheaper; the states, what reads them, and the
    proteins last. [{id, row, module, given, scope, reads, takes, not_comparable}]: given is what the module is run
    with ({choices, inputs}); reads names the comparison whose run an input or a reading takes, and takes {input:
    table} the inputs given from that run's tables. Each pair is given, as run_module gives an agent's call without
    one where the design has two conditions, so the two calls are one."""
    fx = facts(design, screen_dir)
    cells = fx.pop("_cells")
    control = cells["label"] == "control"
    two_kinds = "non_targeting" in fx["control_kinds"] and bool(set(fx["control_kinds"]) & set(CUTTING_KINDS))
    out = []

    def add(row, module, given=None, scope="the screen", reads=None, not_comparable=None, key="", takes=None):
        out.append({"id": f"{row}{(':' + key) if key else ''}", "row": row, "module": module, "given": given or {},
                    "scope": scope, "reads": reads, "takes": takes, "not_comparable": not_comparable})

    pairs = []
    for f in fx["factors"]:
        for held, first, other, why in _pairs(f, cells):
            few = [c for c in (first, other) if (control & (cells["condition"] == c)).sum() < CONTROL_CELLS]
            pairs.append({"scope": f"{f['name']}: {other} against {first}" + (f", within {held}" if held else ""),
                          "key": f"{first} vs {other}", "pair": [first, other], "compared": f["compared"],
                          "why": why or (f"fewer than {CONTROL_CELLS} control cells in {', '.join(few)}" if few
                                         else None)})
    def given(p, **choices):
        return {**({"choices": choices} if choices else {}), "inputs": {"pair": p["pair"]}}

    add("cells_per_target", "B.abundance")
    add("own_transcript", "B.efficacy")
    add("distance", "C.genes", key="genes")
    if len(fx["targets"]) >= 3:
        add("alike_opposite", "E.similar_targets")
    if two_kinds:
        add("control_kinds", "D.cutting_response", scope="the cutting controls against the non-targeting controls")
    # each target's support, which the overview sets beside its distance: its guides agreeing, and its profile
    # agreeing between samples (a weak effect that every guide and sample shares is worth following)
    if fx["guides_per_target"] >= 2:
        add("guides_agree", "F.guides_agree", scope=f"up to {fx['guides_per_target']} guides a target")
    if len(fx["samples"]) >= 2:
        add("units_agree", "F.replicates_agree", scope=f"{len(fx['samples'])} samples")
    for p in (p for p in pairs if p["compared"]):  # levels the user keeps apart are never set against each other
        add("controls_across", "E.condition_x_perturbation", given(p, what="controls"), p["scope"], None, p["why"],
            p["key"])
        add("effect_changing", "E.condition_x_perturbation", given(p), p["scope"], None, p["why"], p["key"])
    add("states", "C.states_and_composition")
    if len(fx["samples"]) >= 2:
        add("baselines", None, scope=f"the control cells of {len(fx['samples'])} samples", reads="states")
    for p in pairs:  # each condition's shares on the states run's labels, one labelling for every condition (D4)
        add("shifts_per_condition", "E.condition_x_perturbation", given(p, what="shares"), p["scope"], "states",
            p["why"], p["key"], takes={"cell_states": "cell_states"})
    if fx["antibodies"]:
        add("proteins", "C.programs", {"choices": {"gene_sets": "proteins"}}, scope=f"{fx['antibodies']} antibodies")
    if len(fx["targets"]) >= 3:  # the overview is due once the changed genes and the targets alike have run
        add("distance", "A.screen_overview", key="overview", reads="distance:genes")
    return out


def key(module, given):
    """The reuse key of a module run (D2): the module, and what it was given, with the choices that are the module's
    own defaults left out (the first of each choice's alternatives). The build is matched apart."""
    from .question_modules import REGISTRY
    defaults = {k: v[0] for k, v in REGISTRY[module].contract.choices.items() if v} if module in REGISTRY else {}
    choices = {k: v for k, v in (given.get("choices") or {}).items() if defaults.get(k) != v}
    rest = {k: v for k, v in given.items() if k != "choices" and v}
    return json.dumps([module, {**rest, **({"choices": choices} if choices else {})}], sort_keys=True, default=str)


def state_of(comparison, st, result_of, given_of):
    """A comparison's state, its reason and its run: (state, reason, run_id). result_of(run_id) gives a finished
    module run's (status, summary), status None where its result cannot be read; given_of(run) a run's (module,
    given); runs on an earlier build do not count."""
    from . import record as rc
    if comparison["not_comparable"]:
        return NOT_COMPARABLE, comparison["not_comparable"], None
    if comparison["module"] is None:  # a reading of another comparison's run
        return None, None, None
    want = key(comparison["module"], comparison["given"])

    def same(run):
        module, given = given_of(run)
        return module is not None and key(module, given) == want
    runs = [r for r in (st.get("runs") or {}).values() if not rc.on_previous_screen(st, r) and same(r)]
    if not runs:
        return RUNNING, "queued", None
    run = runs[-1]  # the record keeps its runs in the order they started
    if run["state"] == "running":
        return RUNNING, "running", run["run_id"]
    if run["state"] != "done":
        tail = str(run.get("output") or "").strip().splitlines()
        return FAILED, tail[-1] if tail else run["state"], run["run_id"]
    status, summary = result_of(run["run_id"])
    if status is None:
        return FAILED, "its result could not be read", run["run_id"]
    state = FROM_STATUS.get(status, DESCRIPTIVE)
    return state, (summary if state == NOT_COMPARABLE else None), run["run_id"]


def states(comparisons_, st, result_of, given_of):
    """Each comparison with its state, reason and run. A reading takes the state of the run it reads; a comparison
    that takes a table from another's run waits for it, then is matched with that input given; where that run ended
    without a result, the comparison ends too, with the reason (D3)."""
    out, by_id = [], {}

    def put(row):
        by_id[row["id"]] = row
        out.append(row)
    for c in comparisons_:
        if c.get("takes") and not c["not_comparable"]:
            source = by_id.get(c["reads"]) or {}
            if source.get("state") in (FAILED, NOT_COMPARABLE):
                put({**c, "state": source["state"], "run_id": None,
                     "reason": f"the {ROWS[source['row']].lower()} it reads: {source['state']}"
                               + (f" ({source['reason']})" if source.get("reason") else "")})
                continue
            if source.get("state") not in (SIGNAL, NOTHING, DESCRIPTIVE) or not source.get("run_id"):
                put({**c, "state": RUNNING, "reason": f"waits for {c['reads']}", "run_id": None})
                continue
            filled = {k: f"{source['run_id']}:{t}" for k, t in c["takes"].items()}
            c = {**c, "given": {**c["given"], "inputs": {**(c["given"].get("inputs") or {}), **filled}}}
        state, reason, run = state_of(c, st, result_of, given_of)
        if c["module"] is None:
            source = by_id.get(c["reads"]) or {}
            state = DESCRIPTIVE if source.get("state") in (SIGNAL, NOTHING, DESCRIPTIVE) else source.get("state")
            reason, run = source.get("reason"), source.get("run_id")
        put({**c, "state": state or RUNNING, "reason": reason, "run_id": run})
    return out


def complete(rows):
    """The survey is complete when no comparison is queued or running (D3); nothing waits for it."""
    return all(r["state"] != RUNNING for r in rows)


def baselines(cell_states, screen_dir):
    """The samples' baselines (D1): each sample's control cells across the states of one states run (its cell_states
    table, so one labelling of the states for every sample, D4), as shares; descriptive, nothing tested. Returns
    (table: one row per state, a column per sample; summary), or None where the tables cannot be read."""
    try:
        lab = pd.read_csv(cell_states, sep="\t", dtype=str, keep_default_na=False, usecols=["barcode", "state"])
        cells = pd.read_csv(os.path.join(screen_dir, "cells.tsv.gz"), sep="\t", dtype=str, keep_default_na=False,
                            usecols=["barcode", "label", "sample"])
    except (OSError, ValueError):
        return None
    m = cells[cells["label"] == "control"].merge(lab, on="barcode")
    m = m[m["state"] != ""]
    if m.empty:
        return None
    n = m.groupby("sample").size()
    t = m.groupby(["sample", "state"]).size().div(n, level="sample").rename("share").reset_index()
    t = t.pivot(index="state", columns="sample", values="share").fillna(0.0)
    spread = (t.max(axis=1) - t.min(axis=1)).sort_values(ascending=False)
    top = spread.index[0]
    cells_said = (", ".join(f"{s} {int(k)}" for s, k in n.items()) if len(n) <= 6
                  else f"{int(n.min())} to {int(n.max())} each")
    return t.reset_index(), (f"the control cells of {len(n)} samples ({cells_said} cells) across {len(t)} states of "
                             f"one labelling; the widest spread in {top}, a share from {t.loc[top].min():.2f} to "
                             f"{t.loc[top].max():.2f}; descriptive, nothing tested")
