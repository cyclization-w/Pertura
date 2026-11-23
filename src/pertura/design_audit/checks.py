"""Checks of a design proposal against the deposit's measurements (pertura.design_audit.audit).

The design auditor (an agent) reads the deposit and proposes a design in pertura.design/v1. This module checks the
proposal. It reads no name for its meaning: it checks that what the proposal names exists in the data, and puts what
the data measured next to each proposed value.
- **Problems** block confirmation: a wrong schema or status; a missing required key; names (guides, samples,
  columns) the data do not have; a target map that puts a guide under two targets, or under a target and a control;
  a unit column the data do not hold.
- **Flags** do not block. They go onto the user's confirmation form, under the value they concern, so the user
  confirms with the measurements in view: pooled donors, unbalanced units, low pseudobulk reach, weak guide capture,
  controls without cells or labelled as targeting, guides in no target and no control, a modality the targets' own
  expression contradicts, varying sample characteristics or expression groups against a single condition, a high
  MOI.
The target map (perturbation.target_map) is the agent's: {target: [guide names]}, or 'feature_reference' to take the
feature reference's labels as they are. With the measurement's arrays (audit.save_arrays), the MOI, the pseudobulk
reach and the on-target expression are measured with the proposed map, controls and units.
"""
import itertools
import json

import numpy as np
import pandas as pd
import scipy.sparse as sp

from ..cell_table import EMPTY
from ..design_gate import (EXTRA, REQUIRED, SCHEMA, UNIT_PARTS, condition_factors, condition_names, modality_code,
                           no_replicate, node, one_word)
from . import detectors as dt
from .readers import NON_TARGETING

STATUSES = ("observed", "reported", "inferred", "proposed", "unresolved")
CONTROL_KINDS = ("non_targeting", "safe_targeting", "cutting_control", "intergenic", "untransduced",
                 "pooled_perturbations_as_control")
OTHER = "other"  # a value outside a fixed list: allowed with a description, shown to the user as a flag
# the guide-calling rules the screen object builds from (pertura.screen_object: perturbation.assignment)
ASSIGNMENT_RULES = ("auto", "deposit", "dominance", "combination", "labels", "per_guide_threshold", "cellranger")
OTHER_MODALITIES = ("base_edit", "other")
VECTOR_SHARE = 0.2      # guides on one vector share at least this share of their cells (Jaccard); other guides less
MIN_VECTOR_CELLS = 20   # a guide needs this many cells before its sharing is judged
FROM_REFERENCE = "feature_reference"
# the ways of telling units apart that the design no longer names (docs/adr/0041, step 4a): a column holds the units
RETIRED_UNIT_KEYS = ("unit_identification", "hashtags", "hashtag_calls")
NODE_FIELDS = ("value", "status", "source", "note", "description", "column")  # what a fact node holds besides its own key
# how a cell column the deposit lacks enters Pertura: the cell tables, written from the notebook
TABLE_ROUTE = ("a column the deposit lacks enters through the project's cell tables: pertura.cell_table.annotate "
               "from the notebook")


def facts_of(design, prefix=""):
    """Every fact node of a design ({value, status, ...}) as (dotted key, node)."""
    out = []
    for k, v in (design or {}).items():
        if k in ("schema", "unresolved", "confirmations", "dataset") or not isinstance(v, dict):
            continue
        key = f"{prefix}{k}"
        if "value" in v or "status" in v:
            out.append((key, v))
        else:
            out += facts_of(v, key + ".")
    return out


def _value(design, key):
    n = node(design, key)
    return None if n is None else n.get("value")


def control_guides(proposal):
    """The proposed control guides (all kinds), in order."""
    out = []
    for c in _value(proposal, "perturbation.controls") or []:
        if isinstance(c, dict) and isinstance(c.get("guides"), list):
            out += [str(g) for g in c["guides"]]
    return out


def _labels(lib):
    """The feature reference's labels as written (older measurements: the map the code measured with)."""
    if lib.get("reference_labels") is not None:
        return {g: ("control" if t == NON_TARGETING else t) for g, t in lib["reference_labels"].items()}
    return lib.get("target_of") or {}


def target_map(proposal, lib):
    """{target: [guides]} of the proposal: its own map, or the feature reference's labels for 'feature_reference'
    (guides labelled 'Non-Targeting' are left out). None when the proposal has no usable map."""
    value = _value(proposal, "perturbation.target_map")
    if value == FROM_REFERENCE:
        out = {}
        for g, t in _labels(lib).items():
            if t != "control":
                out.setdefault(str(t), []).append(str(g))
        return out or None
    if isinstance(value, dict) and value and all(isinstance(v, list) for v in value.values()):
        return {str(t): [str(g) for g in v] for t, v in value.items()}
    return None


def targets_of(proposal, lib):
    """The proposed targets (sorted), for the user's form."""
    m = target_map(proposal, lib) or {}
    ctrl = set(control_guides(proposal))
    return sorted(t for t, gs in m.items() if any(g not in ctrl for g in gs))


def check(proposal, measured, arrays=None, kept=None):
    """{ok, problems, flags: [{key, text}]} for a proposal (a dict) against a measurement (audit's result), and, when
    given, the measurement's arrays (audit.load_arrays) and the folder of the fits it kept (kept)."""
    problems, flags = [], []
    if not isinstance(proposal, dict):
        return {"ok": False, "problems": ["the proposal is not a mapping of design facts"], "flags": []}
    F = {f["key"]: f for f in measured.get("facts", [])}
    lib = measured.get("library") or {}
    if proposal.get("schema") != SCHEMA:
        problems.append(f"schema: {proposal.get('schema')!r}, not {SCHEMA}")
    for k in sorted(str(k) for k in proposal if "." in str(k)):  # development session 3, problem 3
        section, _, fact = k.partition(".")
        problems.append(f"'{k}' is written flat, and flat keys are not read: write it nested, as {section}: "
                        f"{{{fact}: {{value: ..., status: proposed, source: ...}}}}")
    if isinstance(proposal.get("values"), dict):
        problems.append("'values' is not read: write each fact nested under its section (units, perturbation, "
                        "experiment), each a node with value, status and source")
    for key, n in facts_of(proposal):
        st = n.get("status")
        if st == "confirmed":
            problems.append(f"{key}: only the user confirms (confirm_design asks them); mark it proposed, observed, "
                            "reported or inferred")
        elif st not in STATUSES:
            problems.append(f"{key}: status {st!r} is not one of {', '.join(STATUSES)}")
        if st != "unresolved" and not n.get("source"):
            problems.append(f"{key}: no source")
        if st == "inferred" and not n.get("note"):
            problems.append(f"{key}: an inferred fact needs a note with the reasoning")
    # a key written inside another fact's node is never read by the build (units.unit_column under independent_unit)
    known = {k.split(".")[-1] for k in list(REQUIRED) + list(EXTRA)
             + [f"units.{p}" for p in UNIT_PARTS + RETIRED_UNIT_KEYS]}
    known |= {k.split(".")[-1] for k, _ in facts_of(proposal)}
    for key, n in facts_of(proposal):
        for field in n:
            if field not in NODE_FIELDS and field in known:
                problems.append(f"{key}: holds '{field}', which is a key of its own ({key.rsplit('.', 1)[0]}.{field}); "
                                "write it there, with its own value, status and source")
    for key in REQUIRED:
        n = node(proposal, key)
        if n is None:
            problems.append(f"{key}: missing. The user confirms it; propose a value (ask the user in the conversation "
                            "if the files cannot tell)")
        elif n.get("status") == "unresolved":
            problems.append(f"{key}: unresolved. Settle a proposed value first (ask the user in the conversation if "
                            "the files cannot tell); the confirmation form confirms or rejects values")
        elif n.get("value") is None or (n.get("value") in ("", []) and key != "experiment.conditions"):
            problems.append(f"{key}: no value")
    for u in proposal.get("unresolved") or []:
        k = str(u.get("key", "")) if isinstance(u, dict) else ""
        if any(k == r or k.startswith(r + ".") for r in REQUIRED):
            problems.append(f"{k}: still listed as an open question; settle it (or ask the user in the "
                            "conversation) and remove it from unresolved before confirming")
    tmap = _target_map(proposal, lib, problems, flags)
    ctx = _context(arrays, tmap)
    _units(proposal, F, lib, problems, flags, ctx)
    _controls(proposal, lib, problems, flags)
    _modality(proposal, F, lib, problems, flags, ctx, arrays)
    _conditions(proposal, F, lib, problems, flags)
    _columns(proposal, F, problems, flags)
    _moi(proposal, F, flags, ctx)
    _vectors(proposal, lib, problems, flags, arrays, tmap)
    _assignment(proposal, problems)
    if not problems:
        _assignment_left(proposal, lib, flags, arrays, F, kept)
    _quality(proposal, problems)
    _organism(proposal, lib, problems, flags)
    _guides(proposal, lib, problems, flags)
    return {"ok": not problems, "problems": problems, "flags": flags}


def _columns(proposal, F, problems, flags):
    """A fact read from a column of the cell metadata (its node's `column`) against that column, unit by unit: a value
    given by unit or by sample ({by_unit: {unit: value}}) must be the value the column gives that unit's cells. A unit
    whose cells hold several values is flagged."""
    measured = (F.get("data.cell_metadata_by_unit") or {}).get("value") or {}
    for key, n in facts_of(proposal):
        col = n.get("column")
        if not col:
            continue
        col = str(col)
        if col not in measured:
            problems.append(f"{key}: read from the column '{col}', which the deposit's cell metadata does not have "
                            "with few enough values to check" + (f" (it has {', '.join(sorted(measured)[:8])})"
                                                                  if measured else "") + f"; {TABLE_ROUTE}")
            continue
        v = n.get("value")
        mapping = (v.get("by_unit") or v.get("by_sample")) if isinstance(v, dict) else None
        if not isinstance(mapping, dict):
            continue
        wrong, mixed, unseen = [], [], []
        for u, said in mapping.items():
            counts = measured[col].get(str(u))
            if not counts:
                unseen.append(str(u))
                continue
            top, n_top = max(counts.items(), key=lambda kv: kv[1])
            share = n_top / sum(counts.values())
            if str(said) != top:
                wrong.append((str(u), str(said), top, share))
            elif share < 0.95:
                mixed.append((str(u), share))
        if wrong:
            u, said, top, share = wrong[0]
            problems.append(f"{key}: {u} is '{said}' in the proposal, but the column '{col}' gives '{top}' for "
                            f"{share:.0%} of its cells" + (f", and {len(wrong) - 1} more units disagree"
                                                           if len(wrong) > 1 else "")
                            + "; read the column for every unit")
        for u, share in mixed[:3]:
            flags.append({"key": key, "text": f"{u}'s cells hold several values of the column '{col}' (the proposed "
                                              f"one for {share:.0%} of them)"})
        if unseen and len(unseen) == len(mapping):
            flags.append({"key": key, "text": f"none of its units ({', '.join(unseen[:4])}) is a unit the "
                                              f"measurement saw, so the column '{col}' could not check them"})


def _guides(proposal, lib, problems, flags=None):
    """perturbation.guides, perturbation.delivery and perturbation.reference_matches (ADR 0026): in a form the build
    can follow; a mixed library gives every targeting guide its mode, and a reference matched on an attribute needs
    the control guides to carry it. An attribute Pertura does not read is kept and not used, and a mode it does not
    know (base editing, CRISPRoff) is kept: the general analyses run on such guides, those that need the mode's
    direction say they do not apply; both are flagged, not refused."""
    from ..design_gate import DELIVERIES, GUIDE_ATTRIBUTES, modality_code
    flags = [] if flags is None else flags
    library = {str(g) for g in lib.get("guides") or []}
    controls = {str(g) for c in (_value(proposal, "perturbation.controls") or []) if isinstance(c, dict)
                for g in c.get("guides") or []}
    table = _value(proposal, "perturbation.guides")
    attrs = {}
    if table is not None:
        if not isinstance(table, dict) or not all(isinstance(a, dict) for a in table.values()):
            problems.append("perturbation.guides: one entry per guide, {guide: {mode, gene, element, vector}}")
        else:
            attrs = {str(g): a for g, a in table.items()}
            unknown = sorted(set(attrs) - library) if library else []
            if unknown:
                problems.append(f"perturbation.guides: {len(unknown)} guides are not the library's (such as "
                                f"{', '.join(unknown[:3])})")
            odd = sorted({str(k) for a in attrs.values() for k in a} - set(GUIDE_ATTRIBUTES))
            if odd:
                flags.append({"key": "perturbation.guides", "text": f"the attributes {', '.join(odd)} are kept and "
                              f"not used; the analyses read {', '.join(GUIDE_ATTRIBUTES)}"})
            modes = sorted({str(a["mode"]) for a in attrs.values() if a.get("mode")
                            and modality_code(a["mode"]) in (None, "other")})
            if modes:
                flags.append({"key": "perturbation.guides", "text": f"the modes {', '.join(modes[:3])} have no "
                              "expected direction on their target gene (CRISPRi and knockout lower it, CRISPRa raises "
                              "it): the general analyses run on their guides, the on-target checks and escape do not"})
    if str(_value(proposal, "perturbation.modality")).strip().lower() == "mixed":
        need = sorted(g for g in library - controls if not (attrs.get(g) or {}).get("mode"))
        if need:
            problems.append(f"perturbation.modality: mixed, so every targeting guide needs its mode in "
                            f"perturbation.guides; {len(need)} have none (such as {', '.join(need[:3])})")
    matches = _value(proposal, "perturbation.reference_matches")
    if matches is not None:
        for m in (matches if isinstance(matches, list) else [matches]):
            if m not in GUIDE_ATTRIBUTES:
                problems.append(f"perturbation.reference_matches: {m!r} is not a guide attribute "
                                f"({', '.join(GUIDE_ATTRIBUTES)})")
                continue
            lacking = sorted(g for g in controls if not (attrs.get(g) or {}).get(m))
            if lacking:
                problems.append(f"perturbation.reference_matches: the control guides need their {m} in "
                                f"perturbation.guides too; {len(lacking)} have none (such as {', '.join(lacking[:3])})")
    # a target's guides share one mode and one element, whatever the reference matches: a target pooling guides of
    # two would be compared with neither arm's controls alone, and opposite effects (CRISPRa against CRISPRi of one
    # gene) would cancel in it
    tmap = target_map(proposal, lib) or {}
    matched = [m for m in (matches if isinstance(matches, list) else [matches] if matches else [])
               if m in GUIDE_ATTRIBUTES]
    for m in dict.fromkeys(("mode", "element", *matched)):
        mixed = []
        for t, gs in tmap.items():
            vals = sorted({str(attrs[g][m]) for g in gs if g not in controls and (attrs.get(g) or {}).get(m)})
            if len(vals) > 1:
                mixed.append((t, vals))
        if mixed:
            t, vals = mixed[0]
            problems.append(f"perturbation.target_map: {len(mixed)} targets hold guides of different {m} (such as "
                            f"{t}: {' and '.join(vals)}); a target's guides share one {m}, so give each {m} its own "
                            f"target (for example '{t}-{vals[0]}' and '{t}-{vals[1]}')")
    delivery = _value(proposal, "perturbation.delivery")
    if delivery is not None:
        by = (delivery.get("by_unit") or delivery.get("by_sample")) if isinstance(delivery, dict) else None
        values = [delivery] if isinstance(delivery, str) else list(by.values()) if isinstance(by, dict) else []
        if not values or any(v not in DELIVERIES for v in values):
            problems.append("perturbation.delivery: singles or pairs, for the whole screen or by unit "
                            "({by_unit: {unit: singles or pairs}})")


ORGANISMS = ("human", "mouse", "other")


def organism_of_genes(genes, least=500, share=0.9):
    """The organism the matrix's gene names read as, or None: Ensembl gene IDs (ENSG human, ENSMUSG mouse), else
    symbols, upper case for human (GATA1) and a capital then lower case for mouse (Gata1), when at least `share` of
    `least` or more names follow one pattern."""
    names = [str(g) for g in genes]
    ids = [g for g in names if g.startswith("ENS")]
    if len(ids) >= least:
        mouse = sum(g.startswith("ENSMUSG") for g in ids)
        human = sum(g.startswith("ENSG") for g in ids)
        return "mouse" if mouse >= share * len(ids) else "human" if human >= share * len(ids) else None
    symbols = [g for g in names if any(c.isalpha() for c in g) and not g.startswith("ENS")]
    if len(symbols) < least:
        return None
    upper = sum(g == g.upper() for g in symbols)
    capital = sum(g[0].isupper() and any(c.islower() for c in g[1:]) for g in symbols)
    return "human" if upper >= share * len(symbols) else "mouse" if capital >= share * len(symbols) else None


def _organism(proposal, lib, problems, flags):
    """experiment.organism: the gene sets, regulons and pathway scores need it. Asked for when the matrix's gene
    names read as human or mouse; a proposal that disagrees with them is flagged."""
    org = _value(proposal, "experiment.organism")
    read = organism_of_genes(lib.get("genes") or [])
    if org in (None, ""):
        if read:
            problems.append(f"experiment.organism: propose it ({', '.join(ORGANISMS)}); the matrix's gene names "
                            f"read as {read}, and the gene sets, regulons and pathway scores need it")
        return
    if org not in ORGANISMS:
        problems.append(f"experiment.organism: {org!r}; use one of {', '.join(ORGANISMS)}")
    elif read and org != "other" and org != read:
        flags.append({"key": "experiment.organism", "text": f"proposed {org}, but the matrix's gene names read as "
                      f"{read}"})


def _quality(proposal, problems):
    """data.qc, when proposed, is a rule pertura.quality can apply."""
    from .. import quality
    problems += quality.validate((node(proposal, "data.qc") or {}).get("value"))


def _assignment(proposal, problems):
    """perturbation.assignment, when proposed, is a rule the screen object can build from."""
    value = (node(proposal, "perturbation.assignment") or {}).get("value")
    if value is None:
        return
    if isinstance(value, dict) and value.get("rule") == OTHER:
        problems.append("perturbation.assignment: a calling rule Pertura does not have: call the guides with your own "
                        "code, write the calls into the cell tables (pertura.cell_table), then give {rule: deposit}")
    elif not isinstance(value, dict) or value.get("rule") not in ASSIGNMENT_RULES:
        problems.append(f"perturbation.assignment: {value!r} is not a rule; give a mapping with rule one of "
                        f"{', '.join(ASSIGNMENT_RULES)} (for example {{rule: deposit}} for the deposit's own Cell Ranger "
                        "calls), and say what the deposit shows in the source or a note")
    elif value["rule"] == "labels" and not value.get("column"):
        problems.append("perturbation.assignment: the labels rule needs the column that holds the labels")


LEFT_COUNTED = ("deposit", "dominance", "combination", "cellranger")  # the rules counted before the build
SEVERAL = ("multi", "ambiguous", "two sets", "incomplete")
# counted beside the proposed rule, after the deposit's own calls where it has them: Cell Ranger's protospacer calling
# with no floor (releases 7.2 to 8.0) and with its floor of 3 UMIs (8.0.1 on), and the dominance rule
OTHER_RULES = ({"rule": "cellranger", "min_umis": 1}, {"rule": "cellranger", "min_umis": 3},
               {"rule": "dominance", "min_umis": 3, "ratio": 3})
RULE_DEFAULTS = {"cellranger": {"min_umis": 1}, "dominance": {"min_umis": 3, "ratio": 3, "source": "matrix"},
                 "combination": {"min_umis": 2, "min_share": 0.1, "source": "matrix"}}  # as label_cells reads them
OWN_SHIFT = 0.5  # |log2 fold change| of a target's own gene read as a change
WEAK_TOP = 0.5  # a cell whose label's guides hold under this share of its guide UMIs is a weak call: such cells fill
# low-quality states whatever their label, so a rule that keeps more cells is read beside the weak calls it keeps
RULES_DIFFER = (1.05, 0.05)  # the rules leave different cells: one-target cells 5% apart, or weak calls 5 points apart
OWN_DETECTED = 0.05  # a target's own gene is read where at least 5% of the control cells detect it (dt.on_target)


def rule_words(rule):
    """A guide-calling rule in words."""
    r = rule.get("rule")
    if r == "cellranger":
        floor = rule.get("min_umis", 1)
        return "Cell Ranger's protospacer calling" + (f", a floor of {floor} UMIs" if floor > 1 else ", no floor")
    if r == "dominance":
        return (f"dominance ({rule.get('min_umis', 3)} UMIs, {rule.get('ratio', 3)} times the next target"
                + (", from the calls" if rule.get("source") == "calls" else "") + ")")
    if r == "deposit":
        return "the deposit's own calls"
    return str(r)


def _own_genes(arrays, lab, controls, least):
    """The targets' own genes in their cells against the control cells, on the measurement's expression subset
    (audit.own_genes): the targets read, the median log2 fold change, and how many fall and rise by OWN_SHIFT or more.
    None where the measurement kept no own genes, or the rule leaves too few control cells there."""
    if "own_rows" not in arrays:
        return None
    rows = np.asarray(arrays["own_rows"])
    names = np.asarray(arrays["own_genes"]).astype(str)
    E = sp.csr_matrix((arrays["own_data"], arrays["own_indices"], arrays["own_indptr"]),
                      shape=(len(rows), len(names))).tocsc()
    at = np.asarray(lab, dtype=object)[rows]
    ctrl = np.isin(at, list(controls))
    if ctrl.sum() < least:
        return None
    lfc = []
    for j, g in enumerate(names):
        cells = at == g
        col = E[:, j].toarray().ravel()
        if cells.sum() >= least and (col[ctrl] > 0).mean() >= OWN_DETECTED:
            lfc.append(np.log2((col[cells].mean() + 0.01) / (col[ctrl].mean() + 0.01)))
    lfc = np.asarray(lfc)
    return {"targets": int(len(lfc)), "median_log2fc": round(float(np.median(lfc)), 2) if len(lfc) else None,
            "down": int((lfc <= -OWN_SHIFT).sum()), "up": int((lfc >= OWN_SHIFT).sum())}


def _assignment_left(proposal, lib, flags, arrays, F=None, kept=None):
    """What each guide-calling rule leaves, counted on the measurement's guide UMIs before any build: the proposed rule
    first, then the deposit's own calls where it has them and the rules beside it (OTHER_RULES). Each: the cells with
    one target, the control cells, those with several or ambiguous guides and those with none; the fewest and most
    cells with one target over the units; the targets with fewer cells than a test needs; and, on the measurement's
    expression subset, the targets' own genes in their cells against the control cells. The form says the cells each
    rule leaves with one target; the flag beside it, for the agent, the rest. The cellranger rule reads the fit the
    measurement kept (kept: its folder) and fits nothing here; a rule fitted per guide (per_guide_threshold) is counted
    by the build; pertpy's mixture model runs on request (calls.on_request), taking minutes."""
    from .. import policy
    from .. import screen_object as so
    # a design that leaves the rule out is built by the default, so it is counted as that
    named = (node(proposal, "perturbation.assignment") or {}).get("value")
    rule = named or {"rule": "auto"}
    if not (arrays and "umis_indptr" in arrays and isinstance(rule, dict)):
        return
    guides = np.asarray(arrays["guides"]).astype(str)
    n = len(arrays["umis_indptr"]) - 1
    U = sp.csr_matrix((arrays["umis_data"], arrays["umis_indices"], arrays["umis_indptr"]), shape=(n, len(guides)))
    C = sp.csr_matrix((np.ones(len(arrays["calls_indices"])), arrays["calls_indices"], arrays["calls_indptr"]),
                      shape=(n, len(guides)))
    table = so.guide_table(proposal, guides, lib.get("reference_labels"))
    controls = set(so.control_groups(table))
    units = (np.asarray(arrays["unit_samples_names"]).astype(str)[arrays["unit_samples_codes"]]
             if "unit_samples_codes" in arrays else None)
    # each cell's Cell Ranger run, as the measurement fitted them; a measurement from before it kept them has them as
    # its units where its units are the samples
    samples = (list(np.asarray(arrays["cell_samples_names"]).astype(str)[arrays["cell_samples_codes"]])
               if "cell_samples_codes" in arrays else None if units is None else list(units))
    # the deposit's own calls; without call files the measurement's calls are Cell Ranger's method, counted as that
    source = ((F or {}).get("perturbation.assignment_source") or {}).get("status")
    deposit_calls = C.nnz > 0 and source in (None, "observed")
    least = policy.get("min_cells")

    def seen(M):  # per cell [(guide, umis)], as label_cells reads them
        M = sp.csr_matrix(M)
        ip, ix, dx = M.indptr, M.indices, M.data
        return [[(guides[j], int(v)) for j, v in zip(ix[ip[i]:ip[i + 1]], dx[ip[i]:ip[i + 1]])] for i in range(n)]
    umis = seen(U)
    fits = {}  # the measurement's kept fit, read once: its key reads every cell

    def labels(r):  # the rule's label per cell, or why it is not counted here
        if r.get("rule") not in LEFT_COUNTED:
            return None, "counted by the build"
        if r["rule"] == "deposit" or r.get("source") == "calls":
            if not deposit_calls:
                return None, "the deposit holds no calls of its own"
            mine = seen(C if r["rule"] == "deposit" else C.multiply(U))
        else:
            mine = umis
        thr = None
        if r["rule"] == "cellranger":
            if id(mine) not in fits:
                fits[id(mine)] = (None if samples is None else
                                  so.cellranger_thresholds(mine, samples, 1, cache=kept, fit=False))
            thr = fits[id(mine)]
            if thr is None:
                return None, "counted by the build: the measurement kept no fit of these cells"
        return so.label_cells(mine, table, r, groups=samples, cache=kept, fitted=thr)[0], None

    every = sorted({t for t, k in table.values() if k == "targeting"})
    # each cell's guide UMIs summed by label (a target, or a control group): a call's weakness is read on its label's
    # guides, so two guides of one target, or a pair's members, count together
    names = sorted({t for t, _ in table.values()})
    col = {t: j for j, t in enumerate(names)}
    by_label = (U @ sp.csr_matrix((np.ones(len(guides)), (np.arange(len(guides)), [col[table[g][0]] for g in guides])),
                                  shape=(len(guides), len(names)))).tocsr()
    total = np.asarray(U.sum(axis=1)).ravel()

    def weak(raw, rows):  # the cells (rows) whose label's guides hold under WEAK_TOP of their guide UMIs
        parts = [str(raw[i]).split("|") for i in rows]  # a pair's label names both its targets
        own = np.zeros(len(rows))
        for k in range(max(map(len, parts), default=0)):
            j = np.array([col.get(x[k], -1) if k < len(x) else -1 for x in parts])
            own[j >= 0] += np.asarray(by_label[rows[j >= 0], j[j >= 0]]).ravel()
        return own < WEAK_TOP * total[rows]
    # the proposed rule as the build reads it ("auto": the deposit's own calls where it has them, else Cell Ranger's)
    proposed = (({"rule": "deposit"} if deposit_calls else {"rule": "cellranger", "min_umis": 1})
                if rule.get("rule") == "auto" else rule)
    rows, said = [], set()
    for k, r in enumerate([proposed] + ([{"rule": "deposit"}] if deposit_calls else []) + list(OTHER_RULES)):
        key = json.dumps({**RULE_DEFAULTS.get(r.get("rule"), {}), **r}, sort_keys=True)  # a rule counted once
        if key in said:
            continue
        said.add(key)
        lab, why = labels(r)
        row = {"rule": rule_words(r) + (" (proposed)" if k == 0 else "")}
        if lab is None:
            rows.append({**row, "not_counted": why})
            continue
        raw, lab = lab, ["control" if x in controls else x for x in lab]
        kind = np.array(["control" if x == "control" else "none" if x == "none" else "several" if x in SEVERAL
                         else "one" for x in lab])
        row.update({k: int((kind == k).sum()) for k in ("one", "control", "several", "none")})
        kept_cells = np.flatnonzero(np.isin(kind, ("one", "control")))
        if len(kept_cells):
            row["weak_calls"] = round(float(weak(raw, kept_cells).mean()), 3)
        if units is not None and len(set(units)) > 1:
            per = pd.crosstab(pd.Series(units), pd.Series(kind)).reindex(columns=["one"], fill_value=0)["one"]
            row["one_per_unit"] = [int(per.min()), int(per.max())]
            row["fewest_one_in"] = [str(per.idxmin()), int(per.min())]  # the unit a rule leaves fewest
        cells = pd.Series([x for x, k in zip(lab, kind) if k == "one"]).value_counts().reindex(every, fill_value=0)
        row[f"targets_under_{least}_cells"] = int((cells < least).sum())
        mine = _own_genes(arrays, lab, {"control"}, least)
        if mine:
            row["own_genes"] = mine
        rows.append(row)
    counted = [x for x in rows if "one" in x]
    if not counted:
        return
    def beside(x):  # the weak calls among the cells it keeps, and the unit it leaves fewest
        said = ([f"{x['weak_calls']:.0%} of the cells it keeps are weak calls"] if x.get("weak_calls") is not None
                else []) + (
            [f"the fewest in {x['fewest_one_in'][0]}, {x['fewest_one_in'][1]:,}"] if x.get("fewest_one_in") else [])
        return f" ({'; '.join(said)})" if said else ""
    ones = [x["one"] for x in counted]
    weak = [x["weak_calls"] for x in counted if x.get("weak_calls") is not None]
    differ = (max(ones) > RULES_DIFFER[0] * max(min(ones), 1)
              or bool(weak) and max(weak) - min(weak) > RULES_DIFFER[1])
    if named is None and not differ:  # a default with nothing to choose between is not asked (docs/adr/0032, A1, D8)
        return
    flags.append({"key": "perturbation.assignment", "on_the_form": True,
                  "text": f"of {n:,} cells, the cells with one target each rule leaves: "
                          + "; ".join(f"{x['rule']} {x['one']:,}{beside(x)}" for x in counted)
                          + f" (a weak call: its label's guides hold under {WEAK_TOP:.0%} of the cell's guide UMIs)"})
    flags.append({"key": "perturbation.assignment", "rules": rows,
                  "text": "each rule's cells (one target, control, several or ambiguous, none), the share of the "
                          "cells it keeps that are weak calls, the fewest and most with one target over the units and "
                          f"the unit with the fewest, the targets under {least} cells, and the targets' own genes in "
                          "their cells against the control cells (log2 fold change, targets read, down and up by "
                          f"{OWN_SHIFT} or more); pertpy's mixture model is counted on request (calls.on_request)"})


def cooccurrence(arrays):
    """(guide names, cells per guide, cells shared by each pair of guides) from the measured calls."""
    g = np.asarray(arrays["guides"]).astype(str)
    ip, ix = arrays["calls_indptr"], arrays["calls_indices"]
    M = sp.csr_matrix((np.ones(len(ix)), ix, ip), shape=(len(ip) - 1, len(g)))
    M.data[:] = 1
    shared = (M.T @ M).tocsr()
    return g, np.asarray(shared.diagonal()).ravel(), shared


def jaccard(shared, cells, a, b):
    """Cells with both guides over cells with either."""
    both = float(shared[a, b])
    return both / max(cells[a] + cells[b] - both, 1.0)


def vector_map(proposal):
    """guide -> vector of the proposal's perturbation.vectors ({vector: [guides]}), or {} without one."""
    value = (node(proposal, "perturbation.vectors") or {}).get("value")
    if not isinstance(value, dict):
        return {}
    return {str(x): str(v) for v, gs in value.items() if isinstance(gs, list) for x in gs}


def _vectors(proposal, lib, problems, flags, arrays, tmap):
    """Guides that share a vector are one construct. The proposal's vectors are checked against the data: guides
    on one vector are called together in most of their cells, guides on different vectors rarely (Zhou 2023: a median
    47% against 0%). Without a proposed map, guides of one target that share cells like that are flagged."""
    n = node(proposal, "perturbation.vectors")
    value = (n or {}).get("value")
    if n is not None and n.get("status") != "unresolved" and value is not None and not (
            isinstance(value, dict) and value and all(isinstance(v, list) and v for v in value.values())):
        problems.append("perturbation.vectors: give {vector: [its guides]} for vectors that carry several guides")
        return
    vectors = {str(v): [str(x) for x in gs] for v, gs in (value or {}).items()} if isinstance(value, dict) else {}
    library = set(map(str, lib.get("guides") or []))
    seen = {}
    for v, gs in vectors.items():
        for x in gs:
            if library and x not in library:
                problems.append(f"perturbation.vectors: {v} lists {x}, which is not a guide of the deposit")
            if x in seen:
                problems.append(f"perturbation.vectors: {x} is on two vectors ({seen[x]} and {v})")
            seen[x] = v
        if tmap:
            owners = sorted({str(tmap[x]) for x in gs if x in tmap})
            if len(owners) > 1:
                problems.append(f"perturbation.vectors: {v} holds guides of different targets ({', '.join(owners)})")
    if not arrays or "calls_indptr" not in arrays:
        return
    g, cells, shared = cooccurrence(arrays)
    idx = {x: i for i, x in enumerate(g)}
    ok = lambda x: x in idx and cells[idx[x]] >= MIN_VECTOR_CELLS  # noqa: E731
    weak = []
    for v, gs in vectors.items():
        for a, b in itertools.combinations([x for x in gs if ok(x)], 2):
            j = jaccard(shared, cells, idx[a], idx[b])
            if j < VECTOR_SHARE:
                weak.append((v, a, b, j))
    if weak:
        v, a, b, j = weak[0]
        flags.append({"key": "perturbation.vectors", "text": f"{len(weak)} pairs of guides proposed on one vector "
                      f"share under {VECTOR_SHARE:.0%} of their cells (e.g. {v}: {a} and {b}, {j:.0%}); guides on one "
                      "vector are usually called together"})
    if not tmap:
        return
    construct = {x: seen.get(x, x) for x in idx}
    by_owner = {}
    for x, t in tmap.items():
        if ok(x):
            by_owner.setdefault(str(t), []).append(x)
    strong = []
    for t, gs in by_owner.items():
        for a, b in itertools.combinations(sorted(gs), 2):
            if construct[a] != construct[b]:
                j = jaccard(shared, cells, idx[a], idx[b])
                if j >= VECTOR_SHARE:
                    strong.append((t, a, b, j))
    delivered = _value(proposal, "perturbation.delivery")
    if strong and "pairs" in (str(delivered) if delivered is not None else ""):
        strong = []  # a library delivering pairs puts guides on one vector by design (perturbation.delivery)
    if strong:
        t, a, b, j = strong[0]
        owners = len({s[0] for s in strong})
        flags.append({"key": "perturbation.vectors" if vectors else "perturbation.controls",
                      "text": f"in {owners} targets (or the controls), guides on different constructs share at least "
                      f"{VECTOR_SHARE:.0%} of their cells, as guides on one vector do (e.g. {t}: {a} and {b}, {j:.0%}). "
                      "If the library has vectors of several guides, they must be proposed as perturbation.vectors "
                      "({vector: [guides]}): guides that always travel together are not independent replicates"})


def _target_map(proposal, lib, problems, flags):
    """Checks the proposed target map; returns guide -> target or 'control' (the proposed controls), or None."""
    n = node(proposal, "perturbation.target_map")
    library = [str(g) for g in lib.get("guides") or []]
    if n is None or n.get("status") == "unresolved":
        if library:
            problems.append("perturbation.target_map: missing. Give each target's guides ({target: [guide names]}), "
                            f"or '{FROM_REFERENCE}' to take the feature reference's target labels as they are")
        return None
    value = n.get("value")
    if value == FROM_REFERENCE and not _labels(lib):
        problems.append(f"perturbation.target_map: '{FROM_REFERENCE}', but no feature reference labels the guides; "
                        "give each target's guides ({target: [guide names]})")
        return None
    m = target_map(proposal, lib)
    if m is None:
        problems.append("perturbation.target_map: give each target's guides as {target: [guide names]}, or "
                        f"'{FROM_REFERENCE}'")
        return None
    joined = sorted(t for t in m if "+" in str(t) or "|" in str(t))
    if joined and value != FROM_REFERENCE:  # a combination's name; a single target's name cannot hold them
        problems.append(f"perturbation.target_map: {len(joined)} targets are named with '+' or '|' (e.g. "
                        f"{joined[0]!r}), which Pertura reads as a combination of targets; give each single target a "
                        "name without them (such as 'A_B'), and a combination its members' names joined by '|'")
    known = set(library)
    ctrl = set(control_guides(proposal))
    seen, twice = {}, []
    for t, gs in m.items():
        for g in gs:
            if g in seen and seen[g] != t:
                twice.append(g)
            seen[g] = t
    if twice:
        problems.append(f"perturbation.target_map: {len(set(twice))} guides are under two targets (e.g. "
                        f"{twice[0]}: {seen[twice[0]]} and another)")
    unknown = [g for g in seen if g not in known]
    if known and unknown:
        problems.append(f"perturbation.target_map: {len(unknown)} of {len(seen)} guides are not in the deposit's "
                        f"library (e.g. {', '.join(unknown[:3])})")
    both = [g for g in seen if g in ctrl]
    if both and value != FROM_REFERENCE:
        problems.append(f"perturbation.target_map: {len(both)} guides are under a target and a control (e.g. "
                        f"{both[0]}: {seen[both[0]]})")
    out = {g: t for g, t in seen.items() if g not in ctrl}
    out.update({g: "control" for g in ctrl})
    left = [g for g in library if g not in out]
    if left:
        flags.append({"key": "perturbation.controls", "text": f"{len(left)} of {len(library)} guides are in no target "
                      f"and no control (e.g. {', '.join(left[:3])}); their cells are left out"})
    genes = set(lib.get("genes") or [])
    targets = sorted({t for t in out.values() if t != "control"})
    from ..design_gate import guide_attributes
    attrs = guide_attributes(proposal)
    if genes and targets:
        not_genes = [t for t in targets if t not in genes and not any(attrs.get(g, {}).get("gene") for g in m[t])]
        if not_genes:
            flags.append({"key": "perturbation.modality", "text": f"{len(not_genes)} of {len(targets)} targets are "
                          f"not genes of the matrix (e.g. {', '.join(not_genes[:3])}), and their guides' genes are not "
                          "given: on-target expression is measured for the others, and for these once "
                          "perturbation.guides gives each guide's gene"})
    mixed = {t: sorted({attrs.get(g, {}).get("element", "promoter or not given") for g in gs})
             for t, gs in m.items() if t in targets}
    mixed = {t: e for t, e in mixed.items() if len(e) > 1}
    if mixed:  # development session 3, problem 5: a gene's enhancer guides pooled with its promoter guides
        t0 = next(iter(mixed))
        flags.append({"key": "perturbation.target_map", "text": f"{len(mixed)} targets hold guides of different "
                      f"elements (e.g. {t0}: {', '.join(mixed[t0])}); if each element is a perturbation of its own, "
                      "give it its own target"})
    return out


def _context(arrays, tmap):
    """Per cell, from the arrays and the proposed map: the number of distinct targets among its called guides (a
    control counts as one target), and its target ('control', a target, or '' for none or several)."""
    if not arrays or tmap is None or "calls_indptr" not in arrays:
        return None
    guides = np.asarray(arrays["guides"]).astype(str)
    names = sorted(set(tmap.values()) | {g for g in guides if g not in tmap})
    code = {t: k for k, t in enumerate(names)}
    gcode = np.array([code[tmap.get(g, g)] for g in guides], dtype=np.int64)
    indptr, indices = arrays["calls_indptr"], arrays["calls_indices"]
    n = len(indptr) - 1
    rows = np.repeat(np.arange(n), np.diff(indptr))
    T = sp.csr_matrix((np.ones(len(indices)), (rows, gcode[indices])), shape=(n, len(names)))
    T.data[:] = 1
    n_targets = np.asarray(T.sum(1)).ravel()
    first = np.full(n, -1)
    nz = n_targets == 1
    first[nz] = np.asarray(T[nz].argmax(1)).ravel()
    pert = np.where(nz, np.asarray(names, dtype=object)[np.maximum(first, 0)], "")
    unmapped = {g for g in guides if g not in tmap}
    pert = np.where(np.isin(pert, list(unmapped)), "", pert) if unmapped else pert
    samples = (np.asarray(arrays["unit_samples_names"]).astype(str)[arrays["unit_samples_codes"]]
               if "unit_samples_codes" in arrays else None)  # each cell's sample, where the deposit has several
    return {"n_targets": n_targets, "pert": pert.astype(str), "tmap": tmap, "samples": samples}


def _units(proposal, F, lib, problems, flags, ctx=None):
    """The units are a column holding each cell's unit, which the agent writes into the cell tables (or a column of
    the processed object or of the deposit's cell metadata) and the design names (units.unit_column), or the samples
    where it names none; the design says what one unit is (units.independent_unit). Checked: the column exists, the
    units it makes are named on the form, and a number of units the proposal gives matches them. What the build makes
    of them (the cells without a unit, the units of each condition) is in the build's reading checks."""
    unit = _value(proposal, "units.independent_unit")
    col = _value(proposal, "units.unit_column")
    retired = [f"units.{k}" for k in RETIRED_UNIT_KEYS if node(proposal, f"units.{k}") is not None]
    if retired or isinstance(unit, dict):
        problems.append(f"{', '.join(retired) or 'units.independent_unit as a rule'}: not part of the design any "
                        "more. Write each cell's unit into the cell tables (pertura.cell_table.annotate; "
                        "pertura.hashtags.write_into_cell_tables calls each cell's hashtag) and name the column in "
                        "units.unit_column, with how it was made in its source; leave the column out where each "
                        "sample is one unit. units.independent_unit says what one unit is (mouse, donor, culture, or "
                        "none)")
    elif unit not in ("", None) and not one_word(unit):  # code reads the unit as a word, never a sentence's meaning
        problems.append(f"units.independent_unit: '{unit}' is not one word. Write what one unit is as one word "
                        "(mouse, donor, culture, or none where the cells cannot be told apart by their replicate); "
                        "say what a sample's cells were pooled from in units.pooled_from and how the units were told "
                        "apart in the source")
    tabled = _tabled(F, lib)
    if col and (node(proposal, "units.unit_column") or {}).get("status") == "unresolved":
        problems.append(f"units.unit_column: '{col}' is unresolved, and a column the deposit does not hold tells no "
                        f"{unit} apart. Write the column into the cell tables ({TABLE_ROUTE}); where nothing tells "
                        f"the {unit}s apart in this deposit, the replicate is none, with units.pooled_from")
    elif col and col not in tabled:
        problems.append(f"units.unit_column: no column '{col}' in the cell tables, the processed object or the "
                        "deposit's cell metadata" + (f" (they have {', '.join(sorted(tabled)[:20])})" if tabled
                                                     else "") + f"; {TABLE_ROUTE}")
    key = "units.independent_unit"
    made = sorted(units_to_be(proposal, F, lib))
    if made:  # on the form, so the user sees what the build will count (development run on GSE203592, 2026-11-05)
        names = ", ".join(made[:UNITS_NAMED]) + (f" and {len(made) - UNITS_NAMED} more" if len(made) > UNITS_NAMED
                                                 else "")
        from ..design_fit import _plural
        what = ("samples, with no biological replicate (the cells are the observations)" if no_replicate(unit)
                else _plural(str(unit), len(made)))  # not "5 nones"
        flags.append({"key": key, "text": f"the build will count {len(made)} {what}: {names}"
                      + (f", from the column '{col}'" if col else ", one per sample")
                      + (", after units.merge" if _value(proposal, "units.merge") else "")})
        n = _value(proposal, "units.n_units")
        if str(n).isdigit() and int(n) != len(made):
            problems.append(f"units.n_units: {n} {_plural(str(unit), int(n))}, but the units as proposed make "
                            f"{len(made)} ({names}); give the cells a unit column whose values are the units "
                            f"({TABLE_ROUTE}), or correct the number")
    if col:
        shown = column_by_sample(lib, col)
        if shown:
            flags.append({"key": key, "text": f"the column '{col}' in each sample: {shown}"})
        return
    # the samples are the units: what the measurement saw of them
    if "units.pooled_donors" in F:
        share = F["units.pooled_donors"]["value"].get("female_share_by_unit", {})
        flags.append({"key": key, "text": "every sample holds cells of both sexes (XIST against Y-linked genes; "
                      f"female share by unit {share}): each sample pools donors or mice, so the samples may not "
                      f"be independent {unit}s"})
    if "units.unbalanced" in F:
        flags.append({"key": key, "text": f"units with under 10% of the median unit's cells: "
                      f"{F['units.unbalanced']['value']}"})
    if "units.weak_guide_capture" in F:
        flags.append({"key": key, "text": "units that captured guides poorly (few guide UMIs per cell, or a small "
                      f"share on each cell's top guide): {F['units.weak_guide_capture']['value']}"})
    tested, where = None, "with the proposed controls and target map"
    if ctx is not None and ctx.get("samples") is not None:
        _, tested = dt.pseudobulk_reach(ctx["samples"], ctx["pert"])
    elif F.get("units.pseudobulk_reach"):
        tested = F["units.pseudobulk_reach"]["value"].get("targets_testable_pooled")
        where = "with the feature reference's labels"
    if tested is not None and tested < 0.5:
        from .. import policy
        flags.append({"key": key, "text": f"with these units, pseudobulk can test {tested:.0%} of the targets "
                      f"({where}: {policy.get('min_replicates')} units shared with the controls, "
                      f"{policy.get('min_cells')} cells in each); most effects would be tested per cell"})


def _controls(proposal, lib, problems, flags):
    n = node(proposal, "perturbation.controls") or {}
    ctrls = n.get("value")
    if ctrls is None or n.get("status") == "unresolved":
        return
    if not isinstance(ctrls, list) or not ctrls:
        problems.append("perturbation.controls: list each control as {name, kind, guides: [the guide names]}")
        return
    library = set(lib.get("guides") or [])
    cells = lib.get("cells_per_guide") or {}
    listed = []
    for c in ctrls:
        if not isinstance(c, dict):
            problems.append("perturbation.controls: each control is {name, kind, guides}")
            continue
        name = c.get("name", "?")
        if c.get("kind") == OTHER:
            if not c.get("description"):
                problems.append(f"perturbation.controls: {name}: kind 'other' needs a description of the control")
            else:
                flags.append({"key": "perturbation.controls", "text": f"{name}: a kind of control outside Pertura's "
                              f"list ({c['description']}); kept apart from the other controls"})
        elif c.get("kind") not in CONTROL_KINDS:
            problems.append(f"perturbation.controls: {name}: kind {c.get('kind')!r} is not one of "
                            f"{', '.join(CONTROL_KINDS)}, or 'other' with a description")
        guides = c.get("guides")
        if not isinstance(guides, list) or not guides:
            problems.append(f"perturbation.controls: {name}: list its guides by the deposit's guide names")
            continue
        unknown = [g for g in guides if g not in library]
        if library and unknown:
            problems.append(f"perturbation.controls: {name}: {len(unknown)} of {len(guides)} guides are not in the "
                            f"deposit's library (e.g. {', '.join(map(str, unknown[:3]))})")
        if cells:
            empty = [g for g in guides if g in library and cells.get(g, 0) == 0]
            if empty:
                flags.append({"key": "perturbation.controls", "text": f"{name}: {len(empty)} guides have no cell "
                              f"with a call (e.g. {', '.join(empty[:3])})"})
            flags.append({"key": "perturbation.controls", "text": f"{name}: {sum(cells.get(g, 0) for g in guides)} "
                          f"cells carry one of its {len(guides)} guides"})
        listed += guides
    kinds = sorted({str(c.get("kind")) for c in ctrls if isinstance(c, dict)})
    if len(kinds) > 1:
        flags.append({"key": "perturbation.controls", "text": f"{len(kinds)} control kinds ({', '.join(kinds)}): "
                      "they can differ (DNA cutting changes cells), so the screen keeps them apart"})
    target_of = lib.get("target_of") or {}
    if lib.get("targets_source") == "reported":
        labelled = [g for g in listed if target_of.get(g) not in (None, "control")]
        if labelled:
            flags.append({"key": "perturbation.controls", "text": f"the feature reference names a target gene for "
                          f"{len(labelled)} proposed control guides (e.g. {labelled[0]}: {target_of[labelled[0]]})"})


def _modality(proposal, F, lib, problems, flags, ctx, arrays):
    n = node(proposal, "perturbation.modality") or {}
    mod = n.get("value")
    if mod is None or n.get("status") == "unresolved":
        return
    code = modality_code(mod)
    if code is None and str(mod) not in OTHER_MODALITIES:
        problems.append(f"perturbation.modality: {mod!r}; use KO, CRISPRi, CRISPRa, base_edit or other")
        return
    where = "with the proposed target map and controls"
    if ctx is not None:
        summary = None
        if arrays is not None and "expr_sum" in arrays:
            summary = {"guides": arrays["guides"], "genes": arrays["expr_genes"], "sum": arrays["expr_sum"],
                       "detected": arrays["expr_detected"], "cells": arrays["expr_cells"],
                       "basis": str(arrays.get("expr_basis", ""))}
        from ..design_gate import guide_attributes
        gene_of = {g: a["gene"] for g, a in guide_attributes(proposal).items() if a.get("gene")}
        ot, _, _ = dt.on_target(summary, ctx["tmap"], lib.get("genes") or [], gene_of)
    else:
        ot = (F.get("perturbation.on_target") or {}).get("value")
        where = "with the feature reference's labels"
    if isinstance(ot, dict):  # the numbers beside the proposed modality, for the user to read: no verdict
        flags.append({"key": "perturbation.modality", "text": f"the targets' own expression ({where}): median log2 "
                      f"fold change {ot.get('median_log2fc')} over {ot.get('targets_measured')} targets; down by more "
                      f"than 0.5 in {ot.get('share_down_past_0.5', 0):.0%}, up by more than 0.5 in "
                      f"{ot.get('share_up_past_0.5', 0):.0%}"})
    elif isinstance(ot, str) and ctx is not None:
        flags.append({"key": "perturbation.modality", "text": f"on-target expression {where}: {ot}"})


def _moi(proposal, F, flags, ctx):
    if ctx is not None:
        with_calls = ctx["n_targets"] > 0
        multi = float((ctx["n_targets"][with_calls] > 1).mean()) if with_calls.any() else 0.0
        what = "guides of two or more targets (with the proposed map; a control counts as one)"
    else:
        moi = (F.get("perturbation.moi") or {}).get("value")
        if not isinstance(moi, dict):
            return
        multi = moi.get("two_or_more_targets", moi.get("two_or_more_guides"))
        what = ("guides of two or more targets" if "two_or_more_targets" in moi else "two or more guides")
        if multi is None:
            return
    if multi > dt.HIGH_MOI and str(_value(proposal, "perturbation.moi")).lower() == "low":
        flags.append({"key": "perturbation.moi", "text": f"{multi:.0%} of cells with calls carry {what}, against a "
                      "proposed low MOI"})


UNITS_NAMED = 12  # the units the form names by name; beyond, a count


def units_to_be(proposal, F, lib):
    """The units the build will name, as the measurement saw them, each merged as units.merge says: the values of the
    unit column where the proposal names one (an empty set where the measurement did not list them), else the
    samples."""
    col = _value(proposal, "units.unit_column")
    merge = _value(proposal, "units.merge") or {}
    if not col:
        return {str(merge.get(str(x), x)) for x in lib.get("samples") or []}
    values = (lib.get("cell_table_columns") or {}).get(col)
    return {str(merge.get(str(v), v)) for v in values if str(v) not in EMPTY} if values else set()


def _tabled(F, lib):
    """The columns a unit or a condition can be read from, with their values where the measurement listed them: the
    cell tables', the processed object's and the deposit's cell metadata's."""
    return {**{c: None for c in (F.get("data.cell_metadata") or {}).get("value", {}).get("columns") or []},
            **{c: None for c in lib.get("object_columns") or []}, **(lib.get("cell_table_columns") or {})}


def column_by_sample(lib, col, samples=6, values=3):
    """A column's values in each sample, as the measurement counted them in the cell tables, for the form:
    'S1: rest 5,000; S2: stim 4,800'; '' where it did not count them."""
    by = (lib.get("cell_table_columns_by_sample") or {}).get(col) or {}

    def one(s, counts):
        top = sorted(counts.items(), key=lambda kv: -kv[1])
        return f"{s}: " + ", ".join(f"{v if v not in EMPTY else 'no value'} {n:,}" for v, n in top[:values]) + (
            f" and {len(top) - values} more" if len(top) > values else "")
    items = list(by.items())
    return "; ".join(one(s, c) for s, c in items[:samples]) + (f"; and {len(items) - samples} more samples"
                                                              if len(items) > samples else "")


def _conditions(proposal, F, lib, problems, flags):
    """Each cell's condition is read from a column ({column, names}, or [{name, levels, column}] for crossed
    factors), which the agent writes into the cell tables (cell_table.annotate; one value per sample gives all its
    cells that value). Checked: the column exists, the levels are named and hold every value it holds; the form
    shows the column in each sample. Several conditions named without a column reach no cell, and are refused."""
    n = node(proposal, "experiment.conditions") or {}
    value = n.get("value")
    if n.get("status") == "unresolved":
        return
    if isinstance(value, str):  # "none" was read as the conditions e, n and o (a GSE342764 run)
        problems.append(f"experiment.conditions: {value!r} is a word, not one of its forms: [] where every cell had the "
                        "same treatment and time point, {column, names} for one factor, or [{name, levels, column}] "
                        "for crossed factors")
        return
    factors = condition_factors(value)
    tabled = _tabled(F, lib)
    for f in factors:
        what = f"experiment.conditions: {f['name']}" if len(factors) > 1 else "experiment.conditions"
        if f["by"] is None:
            if len(f["levels"]) > 1:
                problems.append(f"{what}: {', '.join(f['levels'][:6])} are named, but no column holds them. Write "
                                "each cell's condition into the cell tables (pertura.cell_table.annotate(cell_tables, "
                                "'condition', {'<sample>': '<its condition>'}): one value per sample gives all its "
                                "cells that value) and give {column, names}, or [{name, levels, column}] for crossed "
                                "factors")
            continue
        col = f["map"]
        if col not in tabled:
            problems.append(f"{what}: no column '{col}' in the cell tables, the processed object or the deposit's "
                            f"cell metadata ({TABLE_ROUTE})")
            continue
        if not f["levels"]:
            problems.append(f"{what}: give the levels the column '{col}' holds ({{column, names}}, or levels for a "
                            "factor)")
        elif tabled.get(col) is not None:
            extra = sorted({str(x) for x in tabled[col]} - set(f["levels"]) - set(EMPTY))
            if extra:
                problems.append(f"{what}: the column '{col}' also holds {', '.join(extra[:5])}, which are not among "
                                "its levels")
        shown = column_by_sample(lib, col)
        if shown:  # the user confirms with the data in view: which samples' cells hold which condition
            flags.append({"key": "experiment.conditions", "text": f"the column '{col}' in each sample: {shown}"})
    names = condition_names(value)
    if len(names) <= 1:
        tags = [str(h) for h in lib.get("hashtags") or []]
        if tags:  # development session 9
            flags.append({"key": "experiment.conditions", "text": f"the measurement found {len(tags)} hashtag-like "
                          f"features ({', '.join(tags[:6])}), and the proposal has one condition: where the hashtags "
                          "mark conditions, each cell's hashtag is written into the cell tables and the conditions "
                          "map by unit over it"})
        chars = F.get("experiment.sample_characteristics")
        if chars:
            flags.append({"key": "experiment.conditions", "text": "GEO sample characteristics vary across samples ("
                          + "; ".join(f"{k}: {', '.join(map(str, v[:4]))}" for k, v in chars["value"].items())
                          + f"), and the proposal has {len(names) or 'one'} condition"})
        groups = F.get("experiment.expression_groups")
        if groups:
            parts = groups["value"].values() if isinstance(groups.get("value"), dict) else []
            said = " | ".join(", ".join(str(u).rstrip("_") for u in us) for us in parts) or str(groups.get("value"))
            flags.append({"key": "experiment.conditions", "text": f"the samples split into two expression groups "
                          f"({said}), and the proposal has {len(names) or 'one'} condition"})
