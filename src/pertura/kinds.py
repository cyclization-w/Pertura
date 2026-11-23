"""The analyses by the field's kinds (docs/adr/0044, D3): eleven tools, each starting the modules of one kind of
analysis on any slice of the cells. A view picks the module, with the choices and inputs the view presets; the
agent's own choices and inputs override them. A tool shows the options its analyses most need as its own parameters,
each with what it takes as its module's contract says; the others stay in choices and inputs. The modules stay the
engine: a tool's run is a module run, recorded under the module's question."""
from .question_modules import REGISTRY
from .question_modules.data import REFERENCES

# tool: (what it answers, {view: (module, preset)}); a tool's first view is its default
KINDS = {
    "guides": ("Guide calls and their evidence, cells per target, each target's own gene, escape (Mixscape or "
               "Mixscale), dose, clones, the vectors' structure, and the controls that cut against those that do not",
               {"calls": ("B.guide_calls_and_their_evidence", {}), "cells": ("B.abundance", {}),
                "own_gene": ("B.efficacy", {}), "escape": ("B.escape", {}), "dose": ("B.dose", {}),
                "clones": ("B.clonality", {}), "vectors": ("B.vector_structure", {}),
                "cutting": ("D.cutting_response", {})}),
    "impact": ("Which targets change the cells, and how much, against the controls; the response many targets share",
               {"distance": ("C.any_effect", {}), "shared": ("D.common_response", {})}),
    "genes": ("The genes each target changes; named genes read across the targets (genes); the targets' network "
              "(genes targets_own)", {"genes": ("C.genes", {})}),
    "scores": ("Each target's effect on per-cell scores: an MSigDB collection (gene_sets, select), lists of your own "
               "(gene_lists), the control cells' contrast between two levels (contrast), the proteins (gene_sets "
               "proteins), or any per-cell value (cell_values)", {"scores": ("C.programs", {})}),
    "states": ("Cell states with their naming evidence and each target's shares of them; changes within a state; the "
               "cell cycle; a trajectory (trajectory_root)",
               {"states": ("C.states_and_composition", {}), "within": ("C.within_states", {}),
                "cell_cycle": ("C.cell_cycle", {})}),
    "programs": ("Gene programmes found in the cells (cNMF, unless choices gene_sets names another method), with each "
                 "target's usage. Use it when a target's changes spread over many genes that no known set or state "
                 "names, or when targets may share a programme the cells hold",
                 {"programs": ("C.programs", {"choices": {"gene_sets": "cnmf"}})}),
    "similarity": ("Targets alike or opposite, the genes they share and their complexes; sets of targets that act "
                   "together; the regulators along a programme's axis",
                   {"alike": ("E.similar_targets", {}), "sets": ("E.co_functional_sets", {}),
                    "axis": ("E.program_axis", {})}),
    "conditions": ("Effects across conditions, time points, cell types or modes (what: the controls' own response, "
                   "genes, scores or shares; pair: the two conditions)",
                   {"conditions": ("E.condition_x_perturbation", {}), "modes": ("E.opposite_modes", {})}),
    "combinations": ("Pairs of perturbations against their singles. Use it when cells carry two targets (a "
                     "combinatorial design), to ask whether a pair acts beyond its two singles",
                     {"pairs": ("E.combinations", {})}),
    "enrichment": ("What changed genes mean: gene sets in each target's changed genes from a genes run (de; up and "
                   "down apart, or method gsea on its ranking) or in a list; the activity of factors and pathways "
                   "(method ulm with collection collectri or progeny, on a genes run: de); a factor's direct targets",
                   {"meaning": ("G.meaning", {}), "direct_targets": ("G.direct_targets", {})}),
    "reproducibility": ("Whether a result holds: the guides of a target agree, the replicates agree, the calls hold "
                        "with fewer cells",
                        {"guides": ("F.guides_agree", {}), "replicates": ("F.replicates_agree", {}),
                         "fewer_cells": ("C.any_effect", {"inputs": {"fewer_cells": [25, 50, 100, 200]}})}),
}
# the options a tool shows as its own parameters, each a choice or an input of its views' modules: those the
# hand-written notebook cells of four development runs needed, which a tool's description had only named
# (pertura_trial notes, 2026-11-09)
PROMOTED = {
    "genes": ("genes",),
    "scores": ("gene_sets", "gene_lists", "select", "contrast", "proteins", "cell_values"),
    "states": ("trajectory_root",),
    "enrichment": ("method", "collection", "de", "gene_lists"),
    "conditions": ("what", "pair", "genes"),
}
TYPES = {"genes": ["array", "string"], "gene_lists": "object", "select": ["array", "string"], "contrast": "object",
         "proteins": "array", "cell_values": "string", "trajectory_root": ["string", "object"], "de": "string",
         "pair": "array"}  # the JSON types of the promoted inputs; a choice is one of its listed values
SLICE = ("cells", "by", "against", "targets")  # every tool's slice of the cells (docs/adr/0044, D2)
HIDDEN_INPUTS = ("targets", "cache")  # given as the slice's targets, or kept inside one process
PROPERTIES = {
    "cells": {"type": ["string", "object"],
              "description": "the cells it reads: a kept cell set's name; {column: value or values} of the cells' own "
                             "columns; or {state: a state or its name, run: a states run (the latest by default)}. "
                             "Kept as a cell set (as save_cells keeps one), so a finding names the same cells; default "
                             "every cell"},
    "by": {"type": "string", "description": "a cell column: the view once per value (each condition, tissue or cell "
                                            "type apart, 2 to 8 values), gathered into one result"},
    "against": {"type": "string", "enum": list(REFERENCES),
                "description": "the cells each target is compared with, where the view compares with a reference: "
                               "control_guides (the default), other_targets or no_guide"},
    "targets": {"type": "array", "items": {"type": "string"},
                "description": "the targets it reads (default: every target with enough cells); the other targets' "
                               "cells are left out of the reading, and the reference stays"},
    "choices": {"type": "object", "description": "the view's other choices, as its line names them; the first value "
                                                 "is the default"},
    "inputs": {"type": "object", "description": "the view's other inputs, as its line names them: a table takes an "
                                                "object's ID (table:<name>, <run>:<table>, a pt call's ID) or a path. "
                                                "read_knowledge with module <its module>: what each one asks"},
    "wait": {"type": "integer", "description": "seconds to wait for the result (default 10, at most 30); a longer run "
                                               "goes on in the background: start the other runs you need, then arm "
                                               "wait_for_runs"},
    "why": {"type": "string", "description": "one line: why this run now"},
    "thread_id": {"type": "string", "description": "the thread it serves"},
    "step": {"type": "string", "description": "kept for records written before the plan was retired; leave it out"},
}


def view_of(tool, view=None):
    """(view, module, preset) of a tool's view, its first where none is named."""
    views = KINDS[tool][1]
    view = view or next(iter(views))
    if view not in views:
        raise ValueError(f"{tool} has no view {view!r}; its views are {', '.join(views)}")
    return (view, *views[view])


def places(module):
    """Every (tool, view) that starts a module."""
    return [(t, v) for t, (_, views) in KINDS.items() for v, (m, _) in views.items() if m == module]


def kind_of(module):
    """(tool, view) that start a module: its view without presets where it has one (C.programs: scores)."""
    places = [(t, v, preset) for t, (_, views) in KINDS.items() for v, (m, preset) in views.items() if m == module]
    if not places:
        raise ValueError(f"no tool starts {module}")
    return min(places, key=lambda p: bool(p[2]))[:2]


def takers(tool, name):
    """The views of a tool whose module takes an option, as a choice or an input."""
    return [v for v, (m, _) in KINDS[tool][1].items()
            if name in REGISTRY[m].contract.choices or name in REGISTRY[m].contract.inputs]


def option(tool, name):
    """A promoted option's schema: its type, and what it takes, as the contract of the first view that takes it
    says (with the views that take it, where not every one does)."""
    views = takers(tool, name)
    c = REGISTRY[KINDS[tool][1][views[0]][0]].contract
    where = "" if len(views) == len(KINDS[tool][1]) else f" (view {' or '.join(views)})"
    if name in c.choices:
        values = list(c.choices[name])
        out = {"description": f"one of {', '.join(map(str, values))}; {values[0]} by default{where}"}
        return {"type": "string", "enum": values, **out} if all(isinstance(v, str) for v in values) else out
    return {**({"type": TYPES[name]} if name in TYPES else {}), "description": c.inputs[name] + where}


def description(tool):
    """What a tool answers, and each view's module with its other choices and inputs as its contract names them (the
    reference and the targets are the slice's against and targets; the promoted options are the tool's parameters)."""
    what, views = KINDS[tool]
    own = set(PROMOTED.get(tool, ()))
    parts = []
    for view, (module, preset) in views.items():
        c = REGISTRY[module].contract
        choices = [f"{k} {'|'.join(map(str, v))}" for k, v in c.choices.items() if k != "reference" and k not in own]
        inputs = [k for k in c.inputs if k not in HIDDEN_INPUTS and k not in own]
        given = {**preset.get("choices", {}), **preset.get("inputs", {})}
        parts.append(f"{view} ({module}" + (f"; choices {', '.join(choices)}" if choices else "")
                     + (f"; inputs {', '.join(inputs)}" if inputs else "")
                     + (f"; given {', '.join(f'{k} {v}' for k, v in given.items())}" if given else "") + ")")
    return f"{what}. Views: " + "; ".join(parts) + "."


def tool(name):
    """A tool's entry in the analysis service's list: its description, and the slice's schema with its views and
    its promoted options."""
    view = {"type": "string", "enum": list(KINDS[name][1]), "description": "which of its analyses (default: the "
                                                                           "first)"}
    own = {k: option(name, k) for k in PROMOTED.get(name, ())}
    return {"name": name, "description": description(name),
            "inputSchema": {"type": "object", "properties": {"view": view, **own, **PROPERTIES}, "required": []}}
