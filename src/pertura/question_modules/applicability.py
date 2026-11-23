"""Which modules can run on this screen (the module menu, agreed 2026-11-05): facts read from the screen object's
tables and the confirmed design, and each module's test of them, in the words of its contract's applies_when. Code
says whether a module can run, and what it waits for; whether it should run is the module picker's and the user's.

    f = facts(screen_dir, design, done, state_shifts)   # done: the modules finished on this build
    menu(f)          # [{module, state, reason}]: state "now", "after" (a module it reads first) or "no"

The facts come from cells.tsv.gz, guides.tsv and the design, never from the counts, so a large screen is read in a
second. A new module needs its entry in TESTS (tests/test_module_menu.py fails without one).
"""
import collections
import json
import os

import pandas as pd

from .data import value

MIN_CELLS = 10  # the cells a module's comparison needs on each side, as the modules' own floors ask
CUTTING_KINDS = ("intergenic", "safe_targeting", "cutting_control")  # as D.cutting_response reads them
ACTING_ON_THE_GENE = ("crispri", "crispra", "crispr_ko")


def _modes(design):
    """The modes the screen's guides act by: the design's modality, and each guide's own where modes are mixed."""
    from ..design_gate import MODALITIES, guide_attributes, modality_code
    out = {modality_code(value(design, "perturbation.modality"))}
    out |= {MODALITIES.get(str(a.get("mode", "")).strip().lower()) for a in guide_attributes(design).values()}
    return {m for m in out if m}


def facts(screen_dir, design, done=(), state_shifts=None):
    """The facts each module's test reads. done: the modules finished on this build; state_shifts: the target x
    state pairs C.states_and_composition found changed (None before it ran)."""
    from ..design_gate import condition_names, guide_attributes
    cells = pd.read_csv(os.path.join(screen_dir, "cells.tsv.gz"), sep="\t", keep_default_na=False,
                        usecols=lambda c: c in ("label", "unit", "condition", "control_kind"))
    guides = pd.read_csv(os.path.join(screen_dir, "guides.tsv"), sep="\t", keep_default_na=False)
    lab = cells["label"].astype(str)
    merge = value(design, "units.merge") or {}
    unit = cells["unit"].astype(str).map(lambda u: str(merge.get(u, u)))
    control = lab == "control"
    from ..quality import FAILED
    special = {"control", "none", "multi", "ambiguous", FAILED, "two sets", "incomplete"}
    counts = lab[~lab.isin(special)].value_counts()
    targets = sorted(counts[counts >= MIN_CELLS].index)
    held = guides[(guides["kind"] == "targeting") & (pd.to_numeric(guides["cells"], errors="coerce") > 0)]
    per_target = held.groupby("target")["guide"].nunique() if len(held) else pd.Series(dtype=int)
    kinds = collections.Counter(cells.loc[control, "control_kind"].astype(str)) if "control_kind" in cells else {}
    by_unit = unit[control].value_counts()
    by_condition = cells.loc[control, "condition"].astype(str).value_counts() if "condition" in cells \
        else pd.Series(dtype=int)
    attrs = guide_attributes(design)
    from ..design_gate import MODALITIES
    gene_modes = collections.defaultdict(set)
    for a in attrs.values():
        if a.get("gene") and a.get("mode"):
            gene_modes[a["gene"]].add(MODALITIES.get(str(a["mode"]).strip().lower()))
    return {
        "targets": len(targets), "target_names": targets,
        "targets_with_two_guides": int((per_target >= 2).sum()),
        "control_cells_by_kind": {k: int(v) for k, v in kinds.items() if k},
        "units_with_control_cells": sorted(by_unit[by_unit >= MIN_CELLS].index),
        "conditions_with_control_cells": sorted(c for c, n in by_condition.items() if c and n >= MIN_CELLS),
        "conditions": condition_names(value(design, "experiment.conditions")),
        "modes": sorted(_modes(design)),
        "genes_with_both_modes": sorted(g for g, m in gene_modes.items() if {"crispri", "crispra"} <= m),
        "context": value(design, "experiment.context"), "organism": value(design, "experiment.organism"),
        "joined_labels": sorted({x for x in counts.index if "|" in x or "+" in x}),
        "vectors_of_several_guides": bool(value(design, "perturbation.vectors")),
        "pairs_delivered": "pairs" in json.dumps(value(design, "perturbation.delivery") or ""),
        "done": sorted(set(done)), "state_shifts": state_shifts}


def _tf_targets(f):
    """The targets that are transcription factors with regulons in CollecTRI, for the organism."""
    from .. import gene_sets as gs
    try:
        net = gs.collectri(f["organism"])
    except (OSError, ValueError):
        return []
    sources = {str(s).lower() for s in net["source"]}
    return [t for t in f["target_names"] if t.lower() in sources]


def _after(f, *modules):
    left = [m for m in modules if m not in f["done"]]
    return ("after", f"runs after {' and '.join(left)}") if left else None


def _when(ok, now, no):
    return ("now", now) if ok else ("no", no)


def _dose(f):
    if not set(f["modes"]) & {"crispri", "crispra"}:
        return "no", "neither CRISPRi nor CRISPRa"
    return _after(f, "B.efficacy") or ("now", "CRISPRi or CRISPRa, with each guide's own effect measured")


def _efficacy(f):
    return _when(set(f["modes"]) & set(ACTING_ON_THE_GENE), "its modality acts on the target gene",
                 "no guide acts on its target gene's expression (modality: " + (", ".join(f["modes"]) or "?") + ")")


TESTS = {
    "A.what_was_done": lambda f: ("now", "every screen"),
    "B.guide_calls_and_their_evidence": lambda f: ("now", "every screen"),
    "B.abundance": lambda f: ("now", "every screen"),
    "B.efficacy": _efficacy,
    "B.escape": lambda f: _when(set(f["modes"]) & {"crispri", "crispr_ko"}, "CRISPRi or knockout guides",
                                "neither CRISPRi nor knockout"),
    "B.clonality": lambda f: _when(f["context"] == "in_vivo", "in vivo, where few transduced cells can seed a "
                                   "target's cells", f"not in vivo ({f['context'] or 'context not given'})"),
    "B.dose": _dose,
    "B.vector_structure": lambda f: _when(f["vectors_of_several_guides"] or f["pairs_delivered"],
                                          "vectors of several guides, or pairs of guides by design",
                                          "one guide per vector and cell"),
    "C.any_effect": lambda f: ("now", "every screen"),
    "C.genes": lambda f: ("now", "every screen"),
    "C.states_and_composition": lambda f: ("now", "every screen"),
    "C.programs": lambda f: ("now", "every screen"),
    "C.cell_cycle": lambda f: _when(f["organism"] in ("human", "mouse"), f"{f['organism']}: the published phase "
                                    "markers", f"organism {f['organism'] or 'not given'}: no phase markers"),
    "C.within_states": lambda f: _after(f, "C.states_and_composition") or _when(
        bool(f["state_shifts"]), f"{f['state_shifts']} target x state shares changed",
        "no target's share of a state changed"),
    "D.common_response": lambda f: _when(f["targets"] >= 3, f"{f['targets']} targets", f"{f['targets']} targets: "
                                         "3 or more give a common response"),
    "D.cutting_response": lambda f: _when(
        sum(f["control_cells_by_kind"].get(k, 0) for k in CUTTING_KINDS) >= MIN_CELLS
        and f["control_cells_by_kind"].get("non_targeting", 0) >= MIN_CELLS,
        "control guides that cut beside non-targeting ones", "control cells by kind: "
        + (", ".join(f"{k} {n}" for k, n in sorted(f["control_cells_by_kind"].items())) or "none")
        + f"; it needs {MIN_CELLS} cells of each"),
    "E.similar_targets": lambda f: _when(f["targets"] >= 3, f"{f['targets']} targets", f"{f['targets']} targets"),
    "E.combinations": lambda f: _when(f["joined_labels"], f"{len(f['joined_labels'])} pairs or sets",
                                      "no cell carries a pair or a set"),
    "E.condition_x_perturbation": lambda f: _when(
        len(f["conditions_with_control_cells"]) >= 2, "conditions with control cells: "
        + ", ".join(f["conditions_with_control_cells"]), "conditions with control cells: "
        + (", ".join(f["conditions_with_control_cells"]) or "none") + "; it needs two"),
    "E.opposite_modes": lambda f: _when(f["genes_with_both_modes"], f"{len(f['genes_with_both_modes'])} genes with "
                                        "CRISPRa and CRISPRi guides", "no gene has both CRISPRa and CRISPRi guides"),
    "E.co_functional_sets": lambda f: _after(f, "C.programs") or ("now", "the programs ran"),
    "E.program_axis": lambda f: _after(f, "C.programs") or ("now", "the programs ran"),
    "F.guides_agree": lambda f: _when(f["targets_with_two_guides"], f"{f['targets_with_two_guides']} targets with two "
                                      "or more guides holding cells", "no target has two guides holding cells"),
    "F.replicates_agree": lambda f: _when(len(f["units_with_control_cells"]) >= 2, f"{len(f['units_with_control_cells'])}"
                                          " samples or lanes with control cells", "fewer than two samples or lanes "
                                          "hold control cells"),
    "G.meaning": lambda f: _after(f, "C.genes") or ("now", "a genes run to read"),
    "G.direct_targets": lambda f: _after(f, "C.genes") or (
        ("no", f"organism {f['organism'] or 'not given'}: CollecTRI holds human and mouse")
        if f["organism"] not in ("human", "mouse") else
        _when(_tf_targets(f), "targets with CollecTRI regulons: " + ", ".join(_tf_targets(f)[:6]),
              "no target is a transcription factor with a CollecTRI regulon")),
    "A.screen_overview": lambda f: _after(f, "C.genes", "E.similar_targets") or ("now", "the first look's runs to "
                                                                                 "read side by side"),
}


def typical_minutes():
    """Each module's measured run time (analysis/end_to_end/typical_minutes_of_each_module.py over the server's
    runs): {module: {median_minutes, runs, longest_minutes, stopped_or_failed}}."""
    with open(os.path.join(os.path.dirname(__file__), "typical_minutes.json"), encoding="utf-8") as fh:
        return json.load(fh)["modules"]


def menu(f, registry=None):
    """Each registered module with its state (now, after, no) and the reason, in the registry's order."""
    from . import REGISTRY
    registry = REGISTRY if registry is None else registry
    out = []
    for name in registry:
        test = TESTS.get(name)
        state, reason = test(f) if test else ("no", "no test of whether it applies yet")
        out.append({"module": name, "state": state, "reason": reason, "ran": name in f["done"]})
    return out


def facts_words(f):
    """The facts in a few words, for the picker's brief."""
    return {k: v for k, v in f.items() if k not in ("target_names",)} | {
        "targets": f"{f['targets']} with {MIN_CELLS} or more cells"}

