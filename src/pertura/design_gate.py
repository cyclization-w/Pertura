"""The design gate (PLAN.md Part 4): no effect analysis runs on a screen until its design is confirmed.

A confirmed design is a pertura.design/v1 file (design.yaml) in which the four keys of REQUIRED have status
'confirmed' and a confirmation record ({key, value, by, date}), and none of them is listed as unresolved. The design
server (pertura.design_service) writes such a file from the user's own answers, given through the client's form
(MCP elicitation). An operator may also supply one, for example for an evaluation screen; its confirmation records
say who confirmed.

The gate also checks that the screen being analysed agrees with the confirmed design:
- the modality;
- units: a screen that treats a column as independent units must use the confirmed independent unit's column;
- controls: two confirmed control kinds need a screen that keeps them apart;
- conditions: a screen holds one condition, and it must be one of the confirmed ones.
status() re-reads the file on every call, so a confirmation made by another process opens the gate at once.
EXTRA keys are asked on the same form and confirmed the same way, but the gate does not need them: the screen build
reads the context; the report states what a sample's cells were pooled from; and, in the optional exam only, the
go-ahead of a screen whose false-hit rate cannot be measured (pertura.coverage).
"""
import copy
import hashlib
import json
import os
import re

SCHEMA = "pertura.design/v1"
REQUIRED = {
    "units.independent_unit": "the biological replicate, one word (mouse, donor, culture, or none)",
    "perturbation.controls": "the control guides and their kind",
    "perturbation.modality": "the modality (knockout, CRISPRi, CRISPRa, base editing or other)",
    "experiment.conditions": "the conditions (each analysed apart)",
}
MODALITIES = {"ko": "crispr_ko", "knockout": "crispr_ko", "crispr_ko": "crispr_ko", "crispr-ko": "crispr_ko",
              "crispri": "crispri", "crispr_i": "crispri", "crispra": "crispra", "crispr_a": "crispra",
              "base_edit": "other", "other": "other",  # other modalities get the same effect analyses (2026-10-07)
              "mixed": "other"}  # several modes in one library: each guide's mode is in perturbation.guides (ADR 0026)
# each guide's attributes (ADR 0026, D1): the mode it acts by, the gene it acts on, the element it targets (a
# promoter, an enhancer), the vector it shares with other guides
GUIDE_ATTRIBUTES = ("mode", "gene", "element", "vector")
DELIVERIES = ("singles", "pairs")
# Keys the form also asks, which the gate does not need but a screen build may (2026-10-07): the context decides
# what a screen with too few control guides is told (pertura.coverage), and such a screen needs the user's go-ahead
EXTRA = {"experiment.context": "the context (in vivo, primary cells in vitro, or a cell line)",
         "experiment.organism": "the organism (human or mouse), for gene sets, regulons and pathway scores",
         "perturbation.guides": "each guide's mode, gene, element or vector, where the target names do not say them",
         "perturbation.target_map": "the targets, each with its guides (and their modes and elements, where given)",
         "perturbation.delivery": "whether a sample or unit delivers single guides or pairs of guides",
         "perturbation.reference_matches": "the guide attributes a target's control cells must share with it",
         "perturbation.vectors": "the guides of one target that share a vector (each vector one construct); pairs "
                                 "of different targets on one vector are perturbation.delivery",
         "units.pooled_from": "what one sample's cells were pooled from before sequencing (for example 5 mice)",
         "units.batches": "which captures or samples ran together in one batch (a day, a run), or not recorded",
         "units.capture_factor": "another factor that groups the captures or samples beside the batches (a cell "
                                 "line, a construct, an effector), with each one's level",
         "perturbation.assignment": "how guides were called in cells: the deposit's calls, Cell Ranger's own method, "
                                    "or the authors' stated rule",
         "coverage.no_measured_rate": "the go-ahead for a screen whose false-hit rate cannot be measured on it",
         "data.qc": "which cells are left out for their quality: filters on the cells' UMIs, genes, mitochondrial or "
                    "ribosomal shares (a fixed threshold, or median absolute deviations within each capture) or on a "
                    "column of one's own, and whether the cells whose unit could not be told are kept "
                    "(pertura.quality)"}
CONTEXT_VALUES = ("in_vivo", "primary_in_vitro", "cell_line")
NO_UNIT = ("none", "", None)
NO_REPLICATE = ("", "none", "cell", "cells")  # the cells are the observations, never the replicate


def one_word(unit):
    """Whether a unit is written as the design asks: one word (mouse, donor, culture, none), what it is and nothing
    else; how the units were pooled or told apart goes in its source and in units.pooled_from."""
    return isinstance(unit, str) and re.fullmatch(r"[A-Za-z][A-Za-z_-]*", unit.strip()) is not None


def no_replicate(unit):
    """Whether a design's independent unit is no biological replicate: none, nothing, or the cell. A lookup of the
    one word check_design asks for (one_word), not a reading of a sentence: a unit of several words is a problem there,
    and is not read here as none."""
    if unit in NO_UNIT:
        return True
    return isinstance(unit, str) and unit.strip().lower() in NO_REPLICATE


NOT_FACTS = ("schema", "unresolved", "confirmations", "dataset")  # a design's entries that hold no facts
BARE_SOURCE = "written without its status: read as a proposed fact, so that the form asks it"


def is_fact(x):
    """Whether x is a fact node ({value, status, ...})."""
    return isinstance(x, dict) and ("value" in x or "status" in x)


def fact_keys():
    """The keys whose node is one fact, whatever its value holds: the required ones, the form's extra ones and the
    parts of the units."""
    return set(REQUIRED) | set(EXTRA) | {f"units.{p}" for p in UNIT_PARTS}


def node(design, key):
    """The fact at a dotted key ({value, status, ...}), or None. A value written bare (a list, a text, or a dict
    without its status at one of fact_keys()) is read as a proposed fact, so that no reader skips it."""
    x = design
    for part in key.split("."):
        if not isinstance(x, dict) or part not in x:
            return None
        x = x[part]
    if x is None:
        return None
    if is_fact(x) or (isinstance(x, dict) and key not in fact_keys()):
        return x
    return {"value": x, "status": "proposed", "source": BARE_SOURCE}


def wrap_bare(design):
    """The design with each value written bare made a proposed fact: a value that is not a dict where a fact
    belongs, inside a section, or a dict without its status at one of fact_keys(). Returns (the design, the keys
    wrapped)."""
    out, wrapped = copy.deepcopy(design or {}), []

    def walk(section, prefix):
        for k, v in list(section.items()):
            key = prefix + k
            if v is None or is_fact(v):
                continue
            if isinstance(v, dict) and key not in fact_keys():
                walk(v, key + ".")
            else:
                section[k] = {"value": v, "status": "proposed", "source": BARE_SOURCE}
                wrapped.append(key)

    for k, v in out.items():
        if k not in NOT_FACTS and isinstance(v, dict) and not is_fact(v):
            walk(v, k + ".")
    return out, wrapped


def canonical(value):
    """A value's canonical text, so a confirmation applies only to the value that was confirmed."""
    return json.dumps(value, sort_keys=True, default=str)


# what one unit is, and the column of the cell tables that holds each cell's unit (none: each sample is one unit)
UNIT_PARTS = ("independent_unit", "unit_column")


def confirmed_value(design, key):
    """What a confirmation of `key` covers. For the units it is the unit and the column that holds it, so neither can
    change after the user confirmed; for the other keys, the fact's value."""
    if key == "units.independent_unit":
        return {p: (node(design, f"units.{p}") or {}).get("value") for p in UNIT_PARTS}
    return (node(design, key) or {}).get("value")


def modality_code(value):
    return MODALITIES.get(str(value).strip().lower()) if value is not None else None


def condition_factors(value):
    """The confirmed conditions as factors: [{name, levels, by, map}], each cell's level read from a column (by
    'column', map the column's name) that the agent writes into the cell tables (cell_table.annotate), or a column of
    the processed object or the deposit's cell metadata. Two forms: {column, names} for one factor, and a list of
    factors, each {name, levels, column} (crossed factors, such as host age and tissue). Conditions named any other
    way (by sample or by unit: the forms retired in docs/adr/0041, step 4b) give their names with by None: they reach
    no cell, and the check names the route. Each form takes "compared": false where the levels are read apart and never
    set against each other (separate experiments in one deposit: GSE267740's two disease models); compared is True
    otherwise."""
    if not value:
        return []
    if isinstance(value, dict) and "column" in value:
        return [{"name": "condition", "levels": [str(x) for x in value.get("names") or []], "by": "column",
                 "map": str(value["column"]), "compared": value.get("compared") is not False}]
    items = value if isinstance(value, list) else [value]
    if all(isinstance(c, dict) and "name" in c and "column" in c for c in items):
        return [{"name": str(c["name"]), "levels": [str(x) for x in c.get("levels") or []], "by": "column",
                 "map": str(c["column"]), "compared": c.get("compared") is not False} for c in items]
    return [{"name": "condition", "levels": _named(value), "by": None, "map": None, "compared": True}]


def _named(value):
    """The condition names a design gives without a column, in whatever form: a mapping's values, the levels or names
    of a list's entries, or the entries themselves; a word is one name, never its letters."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        named = value.get("levels") or value.get("names") or ([value["name"]] if "name" in value else
                                                               list(value.values()))
    else:
        named = [x for c in value for x in (c.get("levels") or [c.get("name")] if isinstance(c, dict) else [c])]
    return sorted({str(x) for x in named if isinstance(x, (str, int, float))})


def join_levels(levels):
    """Crossed factors' levels as one condition's name, joined by '|' ('aged|tumour'), a '|' or a backslash inside a
    level escaped with a backslash, so two combinations never share a name (bug register, BR12)."""
    return "|".join(str(x).replace("\\", "\\\\").replace("|", "\\|") for x in levels)


def condition_names(value):
    """Names of the confirmed conditions: one factor's levels, or, for crossed factors, every combination of their
    levels, joined by '|' (join_levels); the cells hold those the data have."""
    factors = condition_factors(value)
    if not factors:
        return []
    if len(factors) == 1:
        return factors[0]["levels"]
    import itertools
    return [join_levels(x) for x in itertools.product(*[f["levels"] for f in factors])]


def control_kinds(value):
    items = value if isinstance(value, list) else [value] if value else []
    return sorted({str(c.get("kind")) for c in items if isinstance(c, dict) and c.get("kind")})


def guide_attributes(design):
    """{guide: {attribute: value}} from perturbation.guides (ADR 0026, D1); an earlier design's
    perturbation.guide_modes ({guide: mode}) read as the guides' modes."""
    table = {}
    for g, a in ((node(design, "perturbation.guides") or {}).get("value") or {}).items():
        if isinstance(a, dict):
            table[str(g)] = {k: str(v) for k, v in a.items() if k in GUIDE_ATTRIBUTES and v not in (None, "")}
    for g, m in ((node(design, "perturbation.guide_modes") or {}).get("value") or {}).items():
        table.setdefault(str(g), {}).setdefault("mode", str(m))
    return table


def delivery_of(design, unit=None, sample=None):
    """'pairs' or 'singles' for a unit (or sample) by perturbation.delivery ({by_unit: {unit: ...}} or one value for
    the whole screen); None when the design does not say."""
    v = (node(design, "perturbation.delivery") or {}).get("value")
    if isinstance(v, str):
        return v if v in DELIVERIES else None
    if isinstance(v, dict):
        by = v.get("by_unit") or v.get("by_sample") or {}
        for key in (unit, sample):
            if key is not None and str(key) in by:
                return by[str(key)]
    return None


def load(path):
    import yaml
    with open(path, "rb") as fh:
        raw = fh.read()
    return yaml.safe_load(raw) or {}, hashlib.sha256(raw).hexdigest()


def missing_keys(design, keys=None):
    """{key: why} for the required keys (or the given ones) that are not confirmed."""
    out = {}
    confirmed = {c.get("key"): c for c in design.get("confirmations") or [] if isinstance(c, dict)}
    unresolved = [str(u.get("key", "")) for u in design.get("unresolved") or [] if isinstance(u, dict)]
    for key in (REQUIRED if keys is None else keys):
        n = node(design, key)
        if n is None:
            out[key] = "not in the design"
        elif n.get("status") != "confirmed":
            out[key] = f"status {n.get('status')!r}, not confirmed"
        elif key not in confirmed:
            out[key] = "no confirmation record"
        elif canonical(confirmed[key].get("value")) != canonical(confirmed_value(design, key)):
            out[key] = "the confirmed value differs from the design's value"
        elif any(u == key or u.startswith(key + ".") for u in unresolved):
            out[key] = "also listed as unresolved"
        elif key == "units.independent_unit" and n.get("value") not in NO_UNIT and not one_word(n.get("value")):
            # confirmed before the unit was one word: never read from its sentence, asked again instead
            out[key] = (f"the confirmed unit {n.get('value')!r} is not one word, as the design now asks: propose it as "
                        "one word (mouse, donor, culture, or none), check it and confirm it again")
    return out


def confirmed(design, key):
    """The confirmed value of a key (required or EXTRA), or None when it is not confirmed."""
    return None if missing_keys(design, [key]) else confirmed_value(design, key)


def mismatches(design, screen_design=None, condition=None):
    """Ways the screen being analysed (a pertura.design.Design, and its condition) disagrees with the design."""
    out = []
    if screen_design is not None:
        mod = node(design, "perturbation.modality") or {}
        code = modality_code(mod.get("value"))
        if code is None:
            out.append(f"the confirmed modality {mod.get('value')!r} is not one Pertura knows (knockout, CRISPRi, "
                       "CRISPRa, base_edit or other)")
        elif code != screen_design.modality:
            out.append(f"the screen was built as {screen_design.modality}, but the confirmed modality is "
                       f"{mod.get('value')}")
        unit = (node(design, "units.independent_unit") or {}).get("value")
        column = (node(design, "units.unit_column") or {}).get("value")
        if screen_design.unit_independent:
            if no_replicate(unit):
                out.append(f"the screen treats '{screen_design.unit_col}' as independent units, but the confirmed "
                           "design has no independent unit")
            elif column and str(column) != screen_design.unit_col:
                out.append(f"the screen treats '{screen_design.unit_col}' as independent units, but the confirmed "
                           f"independent unit ({unit}) is in '{column}'")
        kinds = control_kinds((node(design, "perturbation.controls") or {}).get("value"))
        if len(kinds) > 1 and not screen_design.control_kind_col:
            out.append(f"two control kinds were confirmed ({', '.join(kinds)}), but the screen does not keep them "
                       "apart")
    if screen_design is None and condition is None:  # no screen: nothing to agree with
        return out
    names = condition_names((node(design, "experiment.conditions") or {}).get("value"))
    if condition is not None and names and str(condition) not in names:
        out.append(f"the screen holds condition '{condition}', which is not among the confirmed ones "
                   f"({', '.join(names)})")
    if condition is None and len(names) > 1:
        out.append(f"the confirmed design has {len(names)} conditions, but the screen does not say which one it "
                   "holds")
    return out


def status(path, screen_design=None, condition=None):
    """{open, missing: {key: why}, mismatches, file, design_sha256}. Closed when there is no file."""
    if not path or not os.path.exists(path):
        return {"open": False, "missing": {k: "no confirmed design for this screen" for k in REQUIRED},
                "mismatches": [], "file": path or None, "design_sha256": None}
    try:
        design, sha = load(path)
    except Exception as e:  # an unreadable design keeps the gate closed
        return {"open": False, "missing": {k: f"the design file cannot be read ({e})" for k in REQUIRED},
                "mismatches": [], "file": path, "design_sha256": None}
    miss = {"schema": f"schema {design.get('schema')!r}, not {SCHEMA}"} if design.get("schema") != SCHEMA else {}
    if isinstance(design.get("change_asked"), dict):  # the user's latest answer on the form: Change
        note = str(design["change_asked"].get("note") or "").strip()
        miss["design"] = "the user asked for a change" + (f' ("{note}")' if note else "") + "; check it again and ask them"
    miss.update(missing_keys(design))
    mism = mismatches(design, screen_design, condition)
    return {"open": not miss and not mism, "missing": miss, "mismatches": mism, "file": path, "design_sha256": sha}


def refusal(st):
    """The message of a closed gate: what is missing and what disagrees, and how to open it."""
    parts = []
    if st["missing"]:
        parts.append("not confirmed: " + "; ".join(f"{REQUIRED.get(k, k)} ({why})" for k, why in st["missing"].items()))
    if st["mismatches"]:
        parts.append("the screen disagrees with the confirmed design: " + "; ".join(st["mismatches"]))
    return ("the design gate is closed, so no effect analysis can run: " + ". ".join(parts) + ". The user confirms "
            "the design through the design auditor (confirm_design); a screen that disagrees must be rebuilt.")
