"""The design's checks that block (docs/build/design_confirmation_rebuilt_plan_2026-11-19.md): what the design names
exists in the data, and the design does not contradict itself. Shapes are design_shapes'; nothing here reads a word
for its meaning.

The columns and samples are read from the cell tables as they are now, so a column the agent has just written is seen
without measuring again. The guides are the measurement's library and the tables' guide features. What the
measurement found beside each value (the units the build will count, the cells each guide rule leaves, the expression
groups) is shown to the agent and the user, never blocks: those displays are design_audit.checks' flags.

Each check names the kind of error it stopped in earlier runs (counted in
pertura_trial/notes_and_tools_for_watching_sessions/design_check_problems_classed_by_their_check.py).
"""
import os

from .cell_table import EMPTY, GUIDE, manifest
from .design_gate import GUIDE_ATTRIBUTES, condition_factors, node
from .design_shapes import FROM_REFERENCE, REQUIRED

MAX_VALUES = 200  # a column with more distinct values in a sample is read as holding no levels (a barcode column)
SHOWN = 8         # names listed in a refusal


def tables_now(folder):
    """{samples: [names], columns: {column: {sample: {value: cells}}}, guides: {names}} from the cell tables as they
    are now; empty where the project has none. A column with more than MAX_VALUES values in a sample is listed with
    None for that sample."""
    import anndata as ad
    out = {"samples": [], "columns": {}, "guides": set()}
    entries = manifest(folder).get("samples", {}) if folder else {}
    for name, e in sorted(entries.items()):
        path = os.path.join(folder, e["file"])
        if not os.path.exists(path):
            continue
        a = ad.read_h5ad(path, backed="r")
        try:
            out["samples"].append(name)
            for c in a.obs.columns:
                counts = a.obs[c].astype(str).value_counts()
                out["columns"].setdefault(str(c), {})[name] = (
                    {str(k): int(n) for k, n in counts.items()} if len(counts) <= MAX_VALUES else None)
            if "feature_types" in a.var:
                names = a.var["name"] if "name" in a.var else a.var_names.to_series()
                out["guides"] |= set(names[a.var["feature_types"].astype(str) == GUIDE].astype(str))
        finally:
            a.file.close()
    return out


def _value(design, key):
    return (node(design, key) or {}).get("value")


def _listed(names):
    names = sorted(map(str, names))
    return ", ".join(names[:SHOWN]) + (f" and {len(names) - SHOWN} more" if len(names) > SHOWN else "")


def _values(tables, column):
    """{value: cells} over every sample of a cell-table column, None where a sample holds too many values."""
    out = {}
    for counts in (tables["columns"].get(column) or {}).values():
        if counts is None:
            return None
        for v, n in counts.items():
            if v not in EMPTY:
                out[v] = out.get(v, 0) + n
    return out


def required(design, genes=()):
    """The four keys the gate needs are given, and the organism where the matrix's gene names read as human or mouse:
    the gene sets, regulons, pathway scores and cell cycle need it (stopped in 57 workspaces before a module refused)."""
    from .design_audit.checks import organism_of_genes
    out = [f"{k}: missing. Propose a value (ask the user in the conversation if the files cannot tell)"
           for k in REQUIRED if node(design, k) is None]
    read = organism_of_genes(genes) if node(design, "experiment.organism") is None else None
    if read:
        out.append(f"experiment.organism: missing; the matrix's gene names read as {read}, and the gene sets, regulons, "
                   "pathway scores and cell cycle need it")
    return out


def control_guides_exist(design, library):
    """Each control's guides are the deposit's (stopped: NonTargeting_%d written for 60 control guides)."""
    out = []
    for c in _value(design, "perturbation.controls") or []:
        unknown = [g for g in c.get("guides") or [] if g not in library]
        if library and unknown:
            out.append(f"perturbation.controls: {c.get('name')}: {len(unknown)} of {len(c.get('guides') or [])} guides "
                       f"are not in the deposit's library (such as {_listed(unknown[:3])})")
    return out


def target_map_holds(design, library, labels):
    """The target map's guides are the deposit's, each under one target, none under a target and a control
    (stopped: RAF1_1-3, guides the library does not have). 'feature_reference' needs a reference that labels the
    guides; where the deposit has one and Pertura read no label from it, the reader is at fault, and the map can be
    given by guide."""
    value = _value(design, "perturbation.target_map")
    if value is None:
        return [f"perturbation.target_map: missing. Give each target's guides ({{target: [guide names]}}), or "
                f"'{FROM_REFERENCE}' for the feature reference's labels as they are"] if library else []
    if value == FROM_REFERENCE:
        return [] if labels else [f"perturbation.target_map: '{FROM_REFERENCE}', but Pertura read no target label from "
                                  "a feature reference; give each target's guides ({target: [guide names]})"]
    out, seen, twice = [], {}, {}
    controls = {g for c in _value(design, "perturbation.controls") or [] for g in c.get("guides") or []}
    joined = sorted(t for t in value if "+" in t or "|" in t)
    if joined:  # Pertura reads '+' and '|' as a combination of targets
        out.append(f"perturbation.target_map: {len(joined)} targets are named with '+' or '|' (such as {joined[0]!r}), "
                   "which reads as a combination; name a single target without them")
    for t, gs in value.items():
        for g in gs:
            if g in seen and seen[g] != t:
                twice[g] = (seen[g], t)
            seen[g] = t
    if twice:
        g, (a, b) = next(iter(twice.items()))
        out.append(f"perturbation.target_map: {len(twice)} guides are under two targets (such as {g}: {a} and {b})")
    unknown = [g for g in seen if g not in library]
    if library and unknown:
        out.append(f"perturbation.target_map: {len(unknown)} of {len(seen)} guides are not in the deposit's library "
                   f"(such as {_listed(unknown[:3])})")
    both = [g for g in seen if g in controls]
    if both:
        out.append(f"perturbation.target_map: {len(both)} guides are under a target and a control (such as {both[0]})")
    return out


def guide_table_holds(design, library):
    """The guide table names the deposit's guides; a mixed library gives every targeting guide its mode; a target's
    guides share one mode and the attributes the reference matches (stopped: CRISPRa and CRISPRi guides pooled under
    one gene, whose effects cancel); the control guides carry the attributes matched. Elements mixed under a target
    are shown, not blocked: pooling a gene's enhancers can be the authors' choice."""
    out = []
    table = _value(design, "perturbation.guides") or {}
    attrs = {g: dict(a) for g, a in table.items()}
    unknown = sorted(set(attrs) - library) if library else []
    if unknown:
        out.append(f"perturbation.guides: {len(unknown)} guides are not the library's (such as {_listed(unknown[:3])})")
    controls = {g for c in _value(design, "perturbation.controls") or [] for g in c.get("guides") or []}
    if str(_value(design, "perturbation.modality") or "") == "mixed":
        need = sorted(g for g in library - controls if not (attrs.get(g) or {}).get("mode"))
        if need:
            out.append(f"perturbation.modality: mixed, so every targeting guide needs its mode in perturbation.guides; "
                       f"{len(need)} have none (such as {_listed(need[:3])})")
    matches = _value(design, "perturbation.reference_matches") or []
    matches = [matches] if isinstance(matches, str) else list(matches)
    for m in matches:
        lacking = sorted(g for g in controls if not (attrs.get(g) or {}).get(m))
        if lacking:
            out.append(f"perturbation.reference_matches: the control guides need their {m} in perturbation.guides too; "
                       f"{len(lacking)} have none (such as {_listed(lacking[:3])})")
    tmap = _value(design, "perturbation.target_map")
    if isinstance(tmap, dict):
        for m in dict.fromkeys(("mode", *[x for x in matches if x in GUIDE_ATTRIBUTES])):
            mixed = [(t, sorted({str(attrs[g][m]) for g in gs if g not in controls and (attrs.get(g) or {}).get(m)}))
                     for t, gs in tmap.items()]
            mixed = [(t, v) for t, v in mixed if len(v) > 1]
            if mixed:
                t, v = mixed[0]
                out.append(f"perturbation.target_map: {len(mixed)} targets hold guides of different {m} (such as {t}: "
                           f"{' and '.join(v)}); give each {m} its own target (such as '{t}-{v[0]}' and '{t}-{v[1]}')")
    return out


def vectors_hold(design, library):
    """A vector's guides are the deposit's, each guide on one vector, a vector's guides under one target (stopped:
    delivery pairs of two targets written as vectors)."""
    out, seen = [], {}
    tmap = _value(design, "perturbation.target_map")
    owner = {g: t for t, gs in tmap.items() for g in gs} if isinstance(tmap, dict) else {}
    for v, gs in (_value(design, "perturbation.vectors") or {}).items():
        for g in gs:
            if library and g not in library:
                out.append(f"perturbation.vectors: {v} lists {g}, which is not a guide of the deposit")
            if g in seen:
                out.append(f"perturbation.vectors: {g} is on two vectors ({seen[g]} and {v})")
            seen[g] = v
        owners = sorted({owner[g] for g in gs if g in owner})
        if len(owners) > 1:
            out.append(f"perturbation.vectors: {v} holds guides of different targets ({', '.join(owners)}); pairs of "
                       "targets delivered together are perturbation.delivery")
    return out


def column_exists(tables, column, key, also="", elsewhere=()):
    """A column the design names is in the cell tables of every sample, or in the deposit's cell metadata or
    processed object, which the build also reads (stopped: units 'per mouse' where no column tells the mice apart)."""
    have = tables["columns"].get(column) or {}
    if column in elsewhere and not have:
        return []
    if not tables["samples"]:
        return [f"{key}: the project has no cell tables to read the column '{column}' from"]
    lacking = [s for s in tables["samples"] if s not in have]
    if len(lacking) == len(tables["samples"]):
        return [f"{key}: no column '{column}' in the cell tables (they have {_listed(tables['columns']) or 'none'}); "
                "write it with pertura.cell_table.annotate(cell_tables, column, {sample: values})" + also]
    if lacking:
        return [f"{key}: the column '{column}' is missing from {len(lacking)} samples ({_listed(lacking)})"]
    return []


def units_hold(design, tables, elsewhere=()):
    """The unit column exists; where each sample is one unit, the key is left out."""
    col = _value(design, "units.unit_column")
    out = column_exists(tables, col, "units.unit_column", "; where each sample is one unit, leave units.unit_column "
                        "out", elsewhere) if col else []
    merge = _value(design, "units.merge") or {}
    units = set(_values(tables, col) or {}) if col else set(tables["samples"])
    unknown = [u for u in merge if units and u not in units]
    if unknown:
        out.append(f"units.merge: {_listed(unknown)} are not units of the screen (they are {_listed(units)})")
    return out


def conditions_hold(design, tables, elsewhere=()):
    """Each factor's column exists; its levels are the values it holds, every one; every sample has a level
    (stopped: condition levels for samples that do not exist, samples left without a level)."""
    out = []
    factors = condition_factors(_value(design, "experiment.conditions"))
    for f in factors:
        what = f"experiment.conditions: {f['name']}" if len(factors) > 1 else "experiment.conditions"
        col = f["map"]
        lacking = column_exists(tables, col, what, elsewhere=elsewhere)
        if lacking:
            out += lacking
            continue
        if col not in tables["columns"]:  # read by the build from the deposit's cell metadata: its values are its own
            continue
        held = _values(tables, col)
        if held is None:
            out.append(f"{what}: the column '{col}' holds too many values to be conditions (over {MAX_VALUES} in a "
                       "sample)")
            continue
        extra = sorted(set(held) - set(f["levels"]))
        if extra:
            out.append(f"{what}: the column '{col}' also holds {_listed(extra)}, which are not among its levels")
        absent = sorted(set(f["levels"]) - set(held))
        if absent:
            out.append(f"{what}: {_listed(absent)} are levels, but the column '{col}' holds none (it holds "
                       f"{_listed(held)})")
        empty = [s for s, counts in (tables["columns"].get(col) or {}).items()
                 if counts is not None and not any(v not in EMPTY for v in counts)]
        if empty:
            out.append(f"{what}: {len(empty)} samples have no level in '{col}' ({_listed(empty)}); give their cells "
                       "theirs, or take the samples out of the reading")
    return out


def maps_hold(design, tables):
    """A key given by sample or by unit names the screen's samples or units, and the batches give every sample one
    where they are given by sample."""
    out = []
    col = _value(design, "units.unit_column")
    units = set(_values(tables, col) or {}) if col else set(tables["samples"])
    samples = set(tables["samples"])
    for key in ("units.batches", "units.capture_factor", "perturbation.delivery"):
        value = _value(design, key)
        if not isinstance(value, dict):
            continue
        for by, known, what in (("by_sample", samples, "samples"), ("by_unit", units, "units")):
            given = value.get(by)
            if not isinstance(given, dict) or not known:
                continue
            unknown = [x for x in given if x not in known]
            if unknown:
                out.append(f"{key}: {_listed(unknown)} are not {what} of the screen (the {what}: {_listed(known)})")
            elif key == "units.batches" and set(given) != known:
                out.append(f"{key}: {_listed(known - set(given))} have no batch; give every one of the {what} its batch")
    return out


def assignment_holds(design, tables, elsewhere=(), reference_labels=None):
    """The labels rule's column exists, and its labels, a guide's read as its target as the build reads them, give
    cells to the design's targets (stopped: a column of guide names built as 38 labels, none a target, so no claim
    could be checked)."""
    from .screen_object import labels_as_targets
    rule = _value(design, "perturbation.assignment") or {}
    if rule.get("rule") != "labels":
        return []
    out = column_exists(tables, rule["column"], "perturbation.assignment", elsewhere=elsewhere)
    # shortcut: a column only in the deposit's cell metadata is not counted here (the build's reading checks name
    # the targets without cells); count it here once a run confirms such a column
    values = _values(tables, rule["column"])
    tmap = _value(design, "perturbation.target_map")
    targets = (set(map(str, tmap)) if isinstance(tmap, dict)
               else set(map(str, (reference_labels or {}).values())) if tmap == FROM_REFERENCE else set())
    if out or not values or not targets:
        return out
    read = labels_as_targets(design, list(values), reference_labels)
    if not any(set(x.replace("|", "+").split("+")) & targets for x in read):
        shown = ", ".join(f"{v} ({n:,})" for v, n in sorted(values.items(), key=lambda kv: -kv[1])[:SHOWN])
        out.append(f"perturbation.assignment: the labels in column '{rule['column']}' give no cell to any of the "
                   f"design's {len(targets)} targets ({_listed(targets)}): the column holds {shown}. Name the column "
                   "that labels the cells by target, or by the guides of perturbation.target_map")
    return out


def check(design, measured, tables):
    """The problems that block confirmation, for a design (nested, its values validated by design_shapes) against
    the measurement (its library) and the cell tables as they are now (tables_now)."""
    lib = (measured or {}).get("library") or {}
    library = {str(g) for g in lib.get("guides") or []} | set(tables.get("guides") or ())
    labels = lib.get("reference_labels") or lib.get("target_of") or {}
    facts = {f.get("key"): f for f in (measured or {}).get("facts") or [] if isinstance(f, dict)}
    elsewhere = set(((facts.get("data.cell_metadata") or {}).get("value") or {}).get("columns") or []) | set(
        lib.get("object_columns") or [])
    return (required(design, lib.get("genes") or []) + control_guides_exist(design, library)
            + target_map_holds(design, library, labels) + guide_table_holds(design, library)
            + vectors_hold(design, library) + units_hold(design, tables, elsewhere)
            + conditions_hold(design, tables, elsewhere) + maps_hold(design, tables)
            + assignment_holds(design, tables, elsewhere, labels))
