"""The design server: the design auditor's tools (docs/build/design_confirmation_rebuilt_plan_2026-11-19.md).

The design auditor (an agent) reads names, metadata and papers, and proposes a design. This server measures what
code can measure, checks the design against the data, and asks the user whether it is right. The agent cannot confirm:
- describe_deposit: the deposit's files and layout, the pairing of matrices with call files, each matrix's feature
  types and the names of its features that are neither genes nor guides, Cell Ranger configurations and other tables
  (sample sheets);
- read_metadata: what the agent reads for meaning: the GEO text (series and samples), the guide names with their
  cells and the feature reference's target labels, a processed object's cell-metadata columns (most frequent values
  with their cell counts), a Cell Ranger configuration by section, and small text files of the deposit (sample
  sheets), in pages;
- measure: the detectors' facts and open questions (pertura.design_audit.audit), kept in the state folder with the
  arrays the displays need (each cell's guides and candidate units, per-guide expression summaries);
- check_design: the design by dotted key, each value in its key's shape (pertura.design_shapes, which is also the
  tool's input schema). A value in another shape is refused with the key's shape. The design then blocks only where
  what it names is not in the data or it contradicts itself (pertura.design_check, reading the cell tables as they are
  now); what the measurement found beside each value comes back for the agent and goes on the user's page, and never
  blocks (design_audit.checks' flags);
- confirm_design: asks the user, through the client's form (MCP elicitation), one question about the whole checked
  design: Right, or Change with their note. Right confirms every key of this design and writes it (design.yaml), which
  the screen's gate reads; Change confirms nothing and returns their note word for word. After the build, the cells'
  quality rule (data.qc) is asked the same way;
- get_design: what is confirmed, what is open, the last check, and the screen object's state;
- build_screen_object: once the design is confirmed, builds the screen object (pertura.screen_object) from it as a
  runner job, and returns its job ID; called again, it reports the job and, when done, the object's summary (cells
  per label, per unit, and its flags).
A client without elicitation cannot confirm: the tool says so, and the operator may supply a confirmed design file
instead (its confirmation records then name who confirmed).

The state folder is written by this server only:
  measurements.json, arrays.npz, proposals/<sha256>.yaml, checks.jsonl, confirmations.jsonl, design.yaml,
  calls.jsonl, outputs.jsonl (each call's result, which the number check of the final report reads), extracted/
  (archives, when measure is asked to extract them), parsed/ (the text matrices, parsed once for the measurement and
  read again by the screen build), screen_object/ and jobs/ (the runner's folder for its build).
    python -m pertura.mcp_server make-design-config <deposit> [--geo SOFT] [--state-dir DIR] > config.json
    python -m pertura.mcp_server serve-design <config.json>
"""
import copy
import hashlib
import inspect
import json
import os
import sys
import threading
import time

from . import coverage, design_check, design_gate, design_shapes
from .limits import CALL_WAIT_MAX
from .design_audit import checks as proposal_checks
from .design_audit.audit import _plain, audit, load_arrays, save_arrays
from .design_audit.readers import (GUIDE, declared_types, matrix_features, open_deposit, read_cellranger_config,
                                   read_feature_reference, read_geo_soft)

SERVER_INFO = {"name": "pertura-design", "version": "0.1.0"}
# how the deposit's reading is checked and changed (docs/adr/0041, step 1b): the agent owns it
READING_DO = ("the deposit was read into cell tables as Pertura's readers propose: check each sample's files and the "
              "notes against the GEO text and the measurement; where one is wrong, rewrite that sample's table "
              "(cell_table.read_10x on other files, cell_table.write with your own code) or take it out "
              "(cell_table.remove with why), then measure with refresh: true")
INSTRUCTIONS = (
    "Pertura's design server, for the design auditor. Call describe_deposit, then measure: Pertura measures what "
    "does not depend on names (structure, counts, balance, sex genes, on-target expression) and lists what only "
    "reading can answer. You read the names, metadata and papers, and give the design with check_design: each key's "
    "value in its shape, with its source. check_design refuses a value in another shape, and blocks only where what "
    "the design names is not in the data or the design contradicts itself; fix those by reading or asking, never by "
    "editing a value to pass. confirm_design then asks the user whether the whole design is right; no effect "
    "analysis runs before they say it is.")


def _obj(props, required=()):
    return {"type": "object", "properties": props, "required": list(required), "additionalProperties": False}


# what check_design and confirm_design do, in the words both servers give the agent
CHECK_WORDS = ("Check the design: design is {dotted key: {value, source, note}}, each value in its key's shape (this "
               "tool's schema). The first call gives every key known; a later call gives only the keys that change "
               "(null removes one), the rest stays as checked last. A value in another shape is refused with the "
               "key's shape. Only what the design names that is not in the data (guides, columns of the cell "
               "tables, samples) or a contradiction (a guide under two targets) blocks; what the measurement found "
               "beside each value comes back in flags, for you and the user, and never blocks. The design is stored.")
CONFIRM_WORDS = ("Ask the user on their client's form whether the checked design is right, as a whole: one answer, "
                 "Right or Change, with their note word for word. Right confirms every key of this design; Change "
                 "confirms nothing and returns their note. After the build, it asks the cells' quality rule the same "
                 "way. Needs a check without problems first.")


TOOLS = [
    {"name": "describe_deposit", "inputSchema": _obj({}),
     "description": "The deposit's files: layout, matrices with their call files, feature references, Cell Ranger "
                    "configurations, other tables (sample sheets), processed objects, archives, GEO metadata; for "
                    "each matrix, its feature types and the names of its features that are neither genes nor "
                    "guides."},
    {"name": "read_metadata",
     "inputSchema": _obj({"item": {"type": "string", "enum": ["geo", "guides", "object", "config", "file"],
                                   "description": "geo: the GEO series and sample text; guides: guide names, their "
                                                  "cells and the feature reference's labels; object: the cell "
                                                  "metadata of a processed object; config: a Cell Ranger multi "
                                                  "config or library CSV, by section; file: a text file"},
                          "path": {"type": "string",
                                   "description": "for 'file', 'config' and 'object': its path in the deposit (an "
                                                  "object or config defaults to the first)"},
                          "offset": {"type": "integer", "minimum": 0,
                                     "description": "first entry, column or line to return (pages)"}}, ("item",)),
     "description": "Text to read for meaning, in pages. item is one of geo, guides, object, config or file: the GEO "
                    "metadata, the guide names (with cells per guide "
                    "and the feature reference's target labels), a processed object's cell-metadata columns (each "
                    "with its most frequent values and their cell counts), a Cell Ranger configuration (sections "
                    "such as libraries and samples), or a small text file of the deposit such as a sample sheet. "
                    "Pertura does not interpret names; you do."},
    {"name": "measure", "inputSchema": _obj({"refresh": {"type": "boolean", "description": "measure again (default: "
                                                                                           "return the stored ones)"}}),
     "description": "Pertura's measurements of the deposit: facts (value, status, source) and the questions the data "
                    "leave open. The first call reads every matrix and can take minutes."},
    {"name": "check_design", "inputSchema": design_shapes.input_schema(),
     "description": CHECK_WORDS},
    {"name": "confirm_design", "inputSchema": _obj({}), "description": CONFIRM_WORDS},
    {"name": "build_screen_object", "inputSchema": _obj({"retry": {"type": "boolean"}}),
     "description": "Once the design is confirmed: build the screen object from it (each cell's guides, call, label, "
                    "unit and condition), as a background job. Called again, it reports the job, and when done the "
                    "object's cells per label and unit and its flags; when it failed, the log's last lines. A failed "
                    "build runs again when the confirmed design changes, or with retry (a failure that was not the "
                    "design's, such as a lost connection)."},
    {"name": "get_design", "inputSchema": _obj({}),
     "description": "The design so far: whether it is confirmed, what is still open, and the last check."},
]
TOOL_NAMES = {t["name"] for t in TOOLS}
ANSWERS = ["confirm", "reject", "unsure"]  # not "correct": an agent read it as "right" and inverted the answer
# "I don't know" goes with Pertura's proposal and is listed as decided for the user (docs/adr/0047, A1)
WITH_THE_PROPOSAL = "I don't know: go with Pertura's proposal"
ANSWER_NAMES = ["Yes, this is right", "No, it is different (say how in the note)", WITH_THE_PROPOSAL]
TITLES = {**{k: title for k, (_, title, _, _) in design_shapes.KEYS.items()},
          "coverage.no_measured_rate": "No measured false-hit rate"}  # the last in earlier designs (the exam's)
BY = "the user, through the client's form (MCP elicitation)"
TEXT_SUFFIXES = (".csv", ".tsv", ".txt", ".md", ".json", ".yaml", ".yml", ".soft", ".tab")
PAGE_LINES, PAGE_GUIDES, PAGE_SAMPLES, MAX_CHARS = 300, 500, 100, 60000
PAGE_COLUMNS, TOP_VALUES = 20, 15  # an object's cell-metadata columns per page, and the values shown for each
MAX_OTHER_FEATURES = 200            # names of non-gene, non-guide features listed per type and matrix
MAX_FORM_TARGETS = 60               # targets listed next to the controls on the confirmation form
BATCHES = "units.batches"
# where a proposed value comes from, by its status, in the words the form shows
ORIGINS = {"observed": "measured in the data", "reported": "stated in the files or by you",
           "inferred": "inferred from the data", "proposed": "Pertura's default", "unresolved": "not settled"}
# what each key decides in the analysis, for the user's table
CHANGES = {k: decides for k, (_, _, decides, _) in design_shapes.KEYS.items()}


def _differ_text(fact, shown=4):
    """The characteristics the samples differ in, every one (the user says what each is), each with at most `shown`
    of its levels, said where the user confirms the conditions (docs/adr/0045, D7)."""
    differ = fact.get("value") or {}
    said = "; ".join(f"{k} ({', '.join(map(str, v[:shown]))}{', ...' if len(v) > shown else ''})"
                     for k, v in differ.items())
    return (f"the deposit's samples differ in {said}. For each, say in the note whether it is a condition, a unit, a "
            "batch, or not compared; a condition is read from a cell column")


def _groups_text(fact):
    groups = fact.get("value") or {}
    return (f"the captures or samples fall into {len(groups)} expression groups: "
            + " | ".join(", ".join(str(u).rstrip("_") for u in us) for us in groups.values()))


# measured facts that make a form item a decision of the user's, with the words that say why:
# (the fact's key, the form's key, whether it applies, the words)
MEASURED_FLAGS = (
    ("experiment.expression_groups", "units.batches",
     lambda f: isinstance(f.get("value"), dict) and len(f["value"]) >= 2, _groups_text),
    ("experiment.samples_differ", "experiment.conditions", lambda f: bool(f.get("value")), _differ_text),
    ("perturbation.assignment_source", "perturbation.assignment",
     lambda f: f.get("status") == "inferred",
     lambda f: "the deposit has no guide calls of its own, so Pertura calls the guides: " + str(f.get("source") or "")),
    ("perturbation.ambient_guide_share", "perturbation.assignment",
     lambda f: isinstance(f.get("value"), dict) and f["value"].get("outside_cells") is not None,
     lambda f: f"{f['value']['outside_cells']:.0%} of the guide UMIs lie in barcodes that are not cells: a guide "
               "called on one or two UMIs may be ambient"),
)


def measured_flags(facts, keys):
    """{form key: [words]} for the measured facts that make an item a decision (MEASURED_FLAGS)."""
    out = {}
    for fact_key, key, applies, words in MEASURED_FLAGS:
        f = facts.get(fact_key)
        if key in keys and isinstance(f, dict) and applies(f):
            out.setdefault(key, []).append(words(f))
    return out
UNIT_WORDS = {"none": "none: no separate mice, donors or cultures", "mouse": "each mouse", "mice": "each mouse",
              "donor": "each donor", "culture": "each separate culture"}
MODALITY_WORDS = {"mixed": "several modes, each guide's own", "crispri": "CRISPRi (repression)",
                  "crispra": "CRISPRa (activation)", "crispr_ko": "knockout", "other": "another mode"}
CONTROL_WORDS = {"non_targeting": "non-targeting", "safe_targeting": "safe-targeting", "cutting_control": "cutting",
                 "intergenic": "intergenic", "untransduced": "untransduced",
                 "pooled_perturbations_as_control": "pooled perturbations as"}


class DesignError(Exception):
    pass


MEASURE_WAIT = 20  # seconds a measure call waits for a background measurement before saying it runs
# what to do while the build runs: a call here waits at most CALL_WAIT_MAX seconds, while wait_for_runs waits for the
# build and the client moves it to the background
BUILD_RUNS = ("the build runs in the background, often for many minutes on a large screen: wait_for_runs returns "
              "when it ends; a call here waits at most %d seconds" % CALL_WAIT_MAX)
MAX_MEASURE_WAIT = 120  # a later call waits longer, a third of the time it has run, so the agent asks less often
_MEASURING, _MEASURING_LOCK = {}, threading.Lock()  # state folder -> its background measurement


def make_design_config(deposit, state_dir, geo=None, name=None, go_ahead=True, background=False):
    """go_ahead: whether the form asks for the go-ahead on a screen whose false-hit rate cannot be measured, a
    question of the optional exam (docs/adr/0022, section 4); the analysis path passes False. background: measure
    runs as a runner job (the analysis path), so a large deposit's extraction and reading do not hold the call."""
    cfg = {"deposit": os.path.abspath(deposit), "state_dir": state_dir,
           "name": name or os.path.basename(os.path.abspath(deposit)), "go_ahead": bool(go_ahead),
           "background_measure": bool(background)}
    if geo:
        cfg["geo"] = geo
    return cfg


def _sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def build_sha(path):
    """The design file's hash as the build reads it: without the cells' quality rule (data.qc) and its
    confirmations, which apply when the screen object is read, so a rule confirmed after the build does not build it
    again."""
    import yaml
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    try:
        d = yaml.safe_load(text)
    except yaml.YAMLError:
        return _sha(text)
    if not isinstance(d, dict):
        return _sha(text)
    d = copy.deepcopy(d)
    if isinstance(d.get("data"), dict):
        d["data"].pop("qc", None)
        if not d["data"]:
            d.pop("data")
    if isinstance(d.get("confirmations"), list):
        d["confirmations"] = [c for c in d["confirmations"] if not (isinstance(c, dict) and c.get("key") == "data.qc")]
    return _sha(json.dumps(d, sort_keys=True, default=str))


def _features(sample, declared):
    """A matrix's features (with its extra readouts of the same cells): counts of each type, and the names of the
    features that are neither genes nor guides (hashtags, proteins, other barcodes), by type. Types come from the
    features file, else from the feature reference, else Cell Ranger's default (gene expression)."""
    import pandas as pd
    try:
        parts = [sample] + [dict(e, kind="mtx") for e in sample.get("extra", [])]
        names, types = [], []
        for part in parts:
            n, t = matrix_features(part)
            if t is None:
                ids = pd.read_csv(part["features"], sep="\t", header=None)[0].astype(str).tolist()
                t = [declared.get(i, declared.get(x, "Gene Expression")) for i, x in zip(ids, n)]
            names += n
            types += t
        counts = pd.Series(types).value_counts().to_dict()
        other = {}
        for n, t in zip(names, types):
            if t not in ("Gene Expression", GUIDE):
                other.setdefault(t, []).append(n)
        return counts, {t: v[:MAX_OTHER_FEATURES] + ([f"... and {len(v) - MAX_OTHER_FEATURES} more"]
                                                     if len(v) > MAX_OTHER_FEATURES else [])
                        for t, v in other.items()}
    except Exception as e:  # a description must not fail on one unreadable file
        return {"unreadable": str(e)[:200]}, {}


def summary(design, key, source=True):
    """The proposed value of a key in a few words, with its source unless source is False."""
    v = design_gate.node(design, key) or {}
    value = v.get("value")
    if key == "units.independent_unit":
        col = (design_gate.node(design, "units.unit_column") or {}).get("value")
        n = (design_gate.node(design, "units.n_units") or {}).get("value")
        parts = [str(value)] + ([f"column '{col}'"] if col else ["one per sample"]) + \
            ([f"{n} units"] if n not in (None, "") else [])
        text = ", ".join(parts)
    elif key == "perturbation.controls":
        items = value if isinstance(value, list) else []
        text = "; ".join(f"{c.get('name')}: {c.get('kind')}, {len(c.get('guides') or [])} guides"
                         for c in items if isinstance(c, dict)) or str(value)
    elif key == "experiment.conditions":
        names = design_gate.condition_names(value)
        factors = design_gate.condition_factors(value)
        if len(factors) > 1:  # crossed factors: each with its levels, and the groups the cells fall into
            text = (" x ".join(f"{f['name']} ({', '.join(f['levels'])})" for f in factors)
                    + f"; the groups: {', '.join(names)}")
        else:
            text = ", ".join(names) if names else "one condition"
        text += _columns_said(factors)
    elif key == "experiment.context":
        text = coverage.CONTEXTS.get(str(value), str(value))
    elif key == "perturbation.vectors":
        sizes = sorted({len(gs) for gs in value.values()}) if isinstance(value, dict) else []
        text = (f"{len(value)} vectors of {' or '.join(map(str, sizes))} guides, each counted as one construct "
                f"(e.g. {next(iter(value))}: {', '.join(next(iter(value.values())))})") if sizes else str(value)
    elif key == "coverage.no_measured_rate":
        text = (f"{value.get('control_guides')} control guides with cells, {value.get('guides_per_target')} guides per "
                "target") if isinstance(value, dict) else str(value)
    elif key == "perturbation.guides":  # the guide table in one line (ADR 0026, D1)
        rows = [a for a in value.values() if isinstance(a, dict)] if isinstance(value, dict) else []
        modes = {}
        for a in rows:
            if a.get("mode"):
                modes[str(a["mode"])] = modes.get(str(a["mode"]), 0) + 1
        parts = [f"{len(rows)} guides"] + ([", ".join(f"{n} {m}" for m, n in sorted(modes.items()))] if modes else [])
        genes = sum(1 for a in rows if a.get("gene"))
        if genes:
            parts.append(f"genes for {genes}")
        elements = sorted({str(a["element"]) for a in rows if a.get("element")})
        if elements:
            parts.append("elements: " + ", ".join(elements))
        text = "; ".join(parts)
    elif key == "perturbation.target_map":  # each target with its guides' modes and elements (session 3, problem 6)
        if isinstance(value, dict):
            attrs = design_gate.guide_attributes(design)

            def about(gs):
                kinds = sorted({", ".join(x for x in (attrs.get(g, {}).get("mode"), attrs.get(g, {}).get("element"))
                                          if x) for g in gs} - {""})
                return f"{len(gs)} guides" + (f" ({'; '.join(kinds)})" if kinds else "")
            shown = list(value.items())[:MAX_FORM_TARGETS]
            text = (f"{len(value)} targets: " + "; ".join(f"{t}: {about(gs)}" for t, gs in shown)
                    + (f"; and {len(value) - len(shown)} more" if len(value) > len(shown) else ""))
        else:
            text = "the feature reference's labels" if value == "feature_reference" else str(value)
    elif key == "perturbation.delivery":
        by = (value.get("by_unit") or value.get("by_sample") or {}) if isinstance(value, dict) else {}
        text = (", ".join(f"{u}: {d}" for u, d in sorted(by.items())) if by else str(value))
    elif key == "perturbation.reference_matches":
        names = value if isinstance(value, list) else [value]
        text = "each target compared with the control guides of the same " + ", ".join(map(str, names))
    elif key == "perturbation.assignment":
        rule = value.get("rule") if isinstance(value, dict) else value
        column = value.get("column") if isinstance(value, dict) else None
        text = {"auto": "the deposit's Cell Ranger calls where it has them, else Cell Ranger's own method on the guide "
                        "counts",
                "deposit": "the deposit's own calls", "cellranger": "Cell Ranger's own method on the guide counts",
                "labels": f"the authors' labels in column '{column}'"}.get(str(rule), f"the rule {value}")
    elif key == "data.qc":
        from . import quality
        text = quality.describe(value, (design_gate.node(design, "units.unit_column") or {}).get("value"))
        reasons = value.get("reasons") if isinstance(value, dict) else None
        if isinstance(reasons, dict) and reasons:
            text += "; why: " + "; ".join(f"{k}: {v}" for k, v in reasons.items())
    else:
        text = str(value)
    return text + (f" (source: {v['source']})" if source and v.get("source") else "")


def _columns_said(factors):
    """The columns the conditions are read from, in a few words ('' where none)."""
    cols = [f"'{f['map']}'" for f in factors if f["by"] == "column"]
    return f", from the column{'s' if len(cols) > 1 else ''} {', '.join(cols)}" if cols else ""


def _plain_words(text):
    """Internal names out of a text the user reads on the form: a key's dotted name becomes its title or words."""
    import re
    out = str(text)
    for k, title in sorted(TITLES.items(), key=lambda kv: -len(kv[0])):
        out = out.replace(k, title.lower())
    out = re.sub(r"\b(?:units|perturbation|experiment|coverage|data)\.([a-z_]+)\b",
                 lambda m: m.group(1).replace("_", " "), out)
    return out


def form_value(design, key):
    """A key's proposed value in plain words, for the user's form: no internal names and no sources. The user's page
    shows those, with what Pertura measured."""
    v = design_gate.node(design, key) or {}
    value = v.get("value")
    if key == "units.independent_unit":
        pooled = (design_gate.node(design, "units.pooled_from") or {}).get("value")
        # no replicate the cells tell apart is not no animals or donors (development session 9, problem 3)
        told_none = design_gate.no_replicate(value)
        text = (f"the cells pool {pooled}; they cannot be identified in this deposit (no column names each cell's "
                "animal or donor), so the analysis counts cells"
                if told_none and pooled else UNIT_WORDS.get(str(value).strip().lower(), str(value)))
        col = (design_gate.node(design, "units.unit_column") or {}).get("value")
        text += f"; each cell's unit read from the column '{col}'" if col else "; each sample one unit"
        return text + (f"; each sample's cells pooled from {pooled}" if pooled and not told_none else "")
    if key == "perturbation.controls":
        items = [c for c in (value if isinstance(value, list) else []) if isinstance(c, dict)]
        return "; ".join(f"{c.get('name')}: {len(c.get('guides') or [])} "
                         f"{CONTROL_WORDS.get(str(c.get('kind')), str(c.get('kind')))} guides" for c in items) \
            or "none named"
    if key == "perturbation.modality":
        return MODALITY_WORDS.get(str(value).strip().lower()) or \
            MODALITY_WORDS.get(design_gate.modality_code(value) or "", str(value))
    if key == "experiment.conditions":
        names = design_gate.condition_names(value)
        factors = design_gate.condition_factors(value)
        if len(factors) > 1:  # crossed factors in words: each with its levels
            text = " crossed with ".join(f"{str(f['name']).replace('_', ' ')} ({', '.join(f['levels'])})"
                                         for f in factors)
        else:
            # "no stimulus recorded" was rejected for an infection all the cells shared (a GSE342764 run)
            text = ", ".join(names) if names else ("one condition: every cell had the same treatment and time point, "
                                                   "so no condition is compared")
        if len(names) > 1 and not all(f["compared"] for f in factors):  # GSE267740: separate experiments
            text += "; each read apart and never compared"
        return text + _columns_said(factors)
    if key == BATCHES:
        by = (value.get("by_unit") or value.get("by_sample") or {}) if isinstance(value, dict) else {}
        groups = {}
        for u, b in sorted(by.items()):
            groups.setdefault(str(b), []).append(str(u))
        return "; ".join(f"batch {b}: {', '.join(us)}" for b, us in groups.items()) or str(value)
    if key == "units.capture_factor" and isinstance(value, dict):
        by = value.get("by_unit") or value.get("by_sample") or {}
        levels = {}
        for u, lv in sorted(by.items()):
            levels.setdefault(str(lv), []).append(str(u))
        return f"{value.get('name', 'a factor')}: " + "; ".join(f"{lv}: {', '.join(us)}" for lv, us in levels.items())
    if key == "perturbation.target_map" and isinstance(value, dict):
        return f"{len(value)} targets, such as {', '.join(list(value)[:3])}; the full list is on your page"
    if key == "perturbation.guides" and isinstance(value, dict):
        rows = [a for a in value.values() if isinstance(a, dict)]
        modes = {}
        for a in rows:
            if a.get("mode"):
                modes[str(a["mode"])] = modes.get(str(a["mode"]), 0) + 1
        elements = sorted({str(a["element"]) for a in rows if a.get("element")})
        return (f"{len(rows)} guides" + (": " + ", ".join(f"{n} {m}" for m, n in sorted(modes.items())) if modes
                                         else "") + ("; elements: " + ", ".join(elements) if elements else ""))
    return _plain_words(summary(design, key, source=False))


def origin(design, key):
    """Where a proposed value comes from, in the form's words, or None."""
    n = design_gate.node(design, key) or {}
    if n.get("status") == "proposed" and not str(n.get("source") or "").startswith("Pertura"):
        return "proposed by the design auditor"  # Pertura's own defaults name Pertura as their source
    return ORIGINS.get(n.get("status"))


def form_details(design, keys, flags):
    """The proposal for the user's page, item by item: the value, where it comes from, what it decides and what the
    check and the measurement said, so that the form itself can be one line per item."""
    return [{"item": TITLES.get(k, k), "proposed": form_value(design, k), "from": origin(design, k),
             "decides": CHANGES.get(k), "measured": [_plain_words(f) for f in flags.get(k) or ()]}
            for k in keys if design_gate.node(design, k) is not None]


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _short(text, n=160):
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[:n - 3].rstrip() + "..."


def keys_of(design):
    """The design's keys in the order of the user's table."""
    return [k for k in TITLES if design_gate.node(design, k) is not None]


def _values_but_qc(design):
    """The design's values by key, the cells' quality rule left out: what a confirmation of the design covers."""
    return {k: design_gate.canonical(n.get("value")) for k, n in proposal_checks.facts_of(design) if k != "data.qc"}


def form_schema(title):
    """The form's one question, with the user's note."""
    return {"type": "object", "properties": {
        "answer": {"type": "string", "title": title, "enum": ["confirm", "change"],
                   "enumNames": ["Right", "Change (say what in the note)"]},
        "note": {"type": "string", "title": "Corrections or comments",
                 "description": "What differs, or anything else Pertura should know"}}, "required": ["answer"]}


AUTO_SOURCE = "Pertura's default: the deposit's calls where it has them, else Cell Ranger's own method"
NO_RULE = ("no rule proposed: every cell with a unit is kept; the cells overview on your page shows their measures "
           "and clusters")
CHANGE_NEXT = ("the user says the design differs, and their note says how. Change the keys it names (check_design "
               "with only those keys), check again, and confirm_design asks them again. No effect analysis runs "
               "before they say the design is right.")


def replace_file(tmp, path, tries=20, wait=0.1):
    """os.replace, tried again for a moment while another process holds the file open: Windows refuses to replace a
    file a reader has open (a notebook's kernel reading the design), where other systems replace it at once."""
    for k in range(tries):
        try:
            return os.replace(tmp, path)
        except PermissionError:
            if k == tries - 1:
                raise
            time.sleep(wait)


def arguments_of(fn):
    """A tool's arguments as an agent passes them: the required ones first, then the optional with their defaults."""
    params = [p for p in inspect.signature(fn).parameters.values() if p.name not in ("self", "elicit")]
    need = [p.name for p in params if p.default is inspect.Parameter.empty]
    may = [f"{p.name}={p.default!r}" for p in params if p.default is not inspect.Parameter.empty]
    return ", ".join(need + may) or "no arguments"


class DesignService:
    TOOLS, INSTRUCTIONS, SERVER_INFO = TOOLS, INSTRUCTIONS, SERVER_INFO
    USES_ELICITATION = True

    def __init__(self, config):
        """config: deposit (folder), state_dir, geo (optional GEO family SOFT file), name, operator_design (optional:
        a registered screen's design, confirmed by the operator)."""
        self.cfg = dict(config)
        self.root, self.dir = self.cfg["deposit"], self.cfg["state_dir"]
        os.makedirs(f"{self.dir}/proposals", exist_ok=True)
        self.design_file = f"{self.dir}/design.yaml"

    # ---- calls and bookkeeping
    def call(self, tool, args=None, elicit=None):
        """Run one tool call and append it to calls.jsonl. Returns (ok, result dict). elicit(message, schema) ->
        (action, content) asks the user through the client; None when the client cannot."""
        if tool not in TOOL_NAMES:
            raise DesignError(f"unknown tool {tool}")
        args = dict(args or {})
        fn = getattr(self, tool)
        kw = {"elicit": elicit} if tool == "confirm_design" else {}
        try:
            inspect.signature(fn).bind(**args, **kw)
        except TypeError as e:
            out, ok = {"error": f"bad arguments: {e}. {tool} takes {arguments_of(fn)}"}, False
        else:
            try:
                out, ok = fn(**args, **kw), True
            except DesignError as e:
                out, ok = {"error": str(e)}, False
        rec = {"time": time.strftime("%Y-%m-%dT%H:%M:%S"), "tool": tool, "ok": ok,
               "args_sha256": _sha(json.dumps(args, sort_keys=True, default=str)),
               "result_sha256": _sha(json.dumps(out, sort_keys=True, default=str))}
        self._append("calls.jsonl", rec)
        # what the agent saw, for the number check of its final report (pertura_research.retired.number_check)
        self._append("outputs.jsonl", {"time": rec["time"], "tool": tool, "ok": ok,
                                       "result_sha256": rec["result_sha256"], "result": out})
        return ok, out

    def _append(self, name, rec):
        with open(f"{self.dir}/{name}", "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, default=str) + "\n")

    def _read(self, name):
        path = f"{self.dir}/{name}"
        if not os.path.exists(path):
            return []
        with open(path, encoding="utf-8") as fh:
            return [json.loads(line) for line in fh if line.strip()]

    def _measured(self):
        path = f"{self.dir}/measurements.json"
        if not os.path.exists(path):
            raise DesignError("measure the deposit first (measure)")
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)

    def _proposal(self, sha):
        import yaml
        with open(f"{self.dir}/proposals/{sha}.yaml", encoding="utf-8") as fh:
            return yaml.safe_load(fh)

    # ---- tools
    def _deposit(self):
        """The deposit as the measurement reads it: its folder with the archives extracted before, or its cell tables
        once the deposit has been read into them."""
        return open_deposit(self.root, f"{self.dir}/extracted", extract=False, cell_tables=f"{self.dir}/cell_tables")[0]

    def _rel(self, path):
        """A path as the agent gives it back: below the deposit, or 'extracted/...' and 'cell_tables/...' below the
        project's design folder."""
        if not path:
            return None
        full = os.path.realpath(path)
        for base, prefix in ((self.root, ""), (f"{self.dir}/extracted", "extracted"),
                             (f"{self.dir}/cell_tables", "cell_tables")):
            b = os.path.realpath(base)
            if full == b or full.startswith(b + os.sep):
                r = os.path.relpath(full, b)
                return os.path.join(prefix, r) if prefix else r
        return os.path.basename(path)

    def _resolve(self, path):
        """The file a path names (as _rel gives it): in the deposit, else in its extracted archives or its cell tables
        ('extracted/...', 'cell_tables/...'); None when it is not a file there."""
        parts = path.replace(chr(92), "/").split("/", 1)
        bases = [(self.root, path)]
        if parts[0] in ("extracted", "cell_tables") and len(parts) == 2:
            bases.append((f"{self.dir}/{parts[0]}", parts[1]))
        for base, rest in bases:
            full, b = os.path.realpath(os.path.join(base, rest)), os.path.realpath(base)
            if full.startswith(b + os.sep) and os.path.isfile(full):
                return full
        return None

    def describe_deposit(self):
        d = self._deposit()
        ref = read_feature_reference(d["feature_references"][0]) if d["feature_references"] else None
        declared = declared_types(ref)
        rel = self._rel

        def calls(c):
            if c is None or isinstance(c, str):
                return rel(c)
            return [{"path": rel(x["path"]), **({"barcode_suffix": x["suffix"]} if x.get("suffix") else {})}
                    for x in c]
        samples = []
        for s in d["samples"]:
            types, other = _features(s, declared)
            samples.append({"name": s["name"], "kind": s["kind"], "matrix": rel(s["matrix"]),
                            **({"read_by": s["made_by"]} if s.get("made_by") else {}),
                            **({"extra_readouts": [rel(e["matrix"]) for e in s["extra"]]} if s.get("extra") else {}),
                            "calls": calls(s.get("calls")),
                            **({"calls_pairing": s["calls_pairing"]} if s.get("calls_pairing") else {}),
                            "feature_types": types, "other_features": other})
        from . import cell_table
        read = cell_table.reading(f"{self.dir}/cell_tables")
        return {"deposit": self.cfg.get("name"), "layout": d["layout"], "samples": samples,
                **({"read_into_cell_tables": read} if read else {}),
                "call_files": [rel(c) for c in d["calls"]],
                "feature_references": [rel(f) for f in d["feature_references"]],
                "cellranger_configs": [rel(f) for f in d["configs"]], "tables": [rel(f) for f in d["tables"]],
                "objects": [rel(f) for f in d["objects"]], "archives": [rel(f) for f in d["archives"]],
                "unreadable": [{"file": rel(u["file"]), "error": u["error"]} for u in d.get("unreadable", [])],
                **({"every_droplet": [{"matrix": rel(e["matrix"]), "barcodes_in_one_lane": e["barcodes_in_one_lane"]}
                                      for e in d["every_droplet"]]} if d.get("every_droplet") else {}),
                "geo_metadata": [rel(f) for f in d["geo_soft"]] + ([self.cfg["geo"]] if self.cfg.get("geo") else [])}

    def read_metadata(self, item, path=None, offset=0):
        d = self._deposit()
        if item == "geo":
            soft = self.cfg.get("geo") or (d["geo_soft"][0] if d["geo_soft"] else None)
            if not soft or not os.path.exists(soft):
                raise DesignError("the deposit has no GEO metadata (family SOFT file). Its samples and units come from "
                                  "the matrices and the cell metadata (describe_deposit, measure); ask the user only "
                                  "for what those do not show")
            g = read_geo_soft(soft)
            names = sorted(g["samples"])
            page = names[offset:offset + PAGE_SAMPLES]
            return {"series": g["series"] if offset == 0 else None, "samples_total": len(names),
                    "samples": {k: {"title": g["samples"][k]["title"],
                                    "characteristics": g["samples"][k]["characteristics"],
                                    "description": g["samples"][k]["description"][:3]} for k in page},
                    "next_offset": offset + PAGE_SAMPLES if offset + PAGE_SAMPLES < len(names) else None}
        if item == "guides":
            lib = self._measured()["library"]
            labels = {}
            for ref in d["feature_references"][:1]:
                t = read_feature_reference(ref)
                if "name" in t.columns and "target_gene_name" in t.columns:
                    labels = dict(zip(t["name"].astype(str), t["target_gene_name"].astype(str)))
            guides = lib.get("guides") or sorted(labels)
            page = guides[offset:offset + PAGE_GUIDES]
            return {"guides_total": len(guides),
                    "guides": [{"name": g, "cells": (lib.get("cells_per_guide") or {}).get(g, 0),
                                **({"reference_target": labels[g]} if g in labels else {})} for g in page],
                    "next_offset": offset + PAGE_GUIDES if offset + PAGE_GUIDES < len(guides) else None}
        if item == "object":
            if not d["objects"]:
                raise DesignError("the deposit has no processed object (.h5ad)")
            full = os.path.realpath(os.path.join(self.root, path)) if path else d["objects"][0]
            if full not in [os.path.realpath(o) for o in d["objects"]] and full not in d["objects"]:
                raise DesignError(f"{path} is not a processed object of the deposit (see describe_deposit)")
            import anndata as ad
            a = ad.read_h5ad(full, backed="r")
            try:
                obs, n = a.obs, int(a.n_obs)
            finally:
                a.file.close()
            cols = [str(c) for c in obs.columns]
            out = []
            for c in cols[offset:offset + PAGE_COLUMNS]:
                v = obs[c]
                numeric = str(v.dtype).startswith(("int", "float", "uint"))
                counts = v.astype(str).value_counts()
                out.append({"column": c, "dtype": str(v.dtype), "distinct": int(len(counts)),
                            **({"min": float(v.min()), "max": float(v.max())} if numeric and len(v) else {}),
                            "top_values": {str(k)[:80]: int(x) for k, x in counts.head(TOP_VALUES).items()}})
            return {"object": os.path.relpath(full, self.root), "cells": n, "columns_total": len(cols),
                    "columns": out,
                    "next_offset": offset + PAGE_COLUMNS if offset + PAGE_COLUMNS < len(cols) else None}
        if item == "config":
            if not d["configs"]:
                raise DesignError("the deposit has no Cell Ranger configuration (multi config or library CSV)")
            full = os.path.realpath(os.path.join(self.root, path)) if path else os.path.realpath(d["configs"][0])
            if full not in [os.path.realpath(c) for c in d["configs"]]:
                raise DesignError(f"{path} is not a Cell Ranger configuration of the deposit (see describe_deposit)")
            return {"config": os.path.relpath(full, self.root), "sections": read_cellranger_config(full)}
        if item == "file":
            if not path:
                raise DesignError("give the file's path in the deposit (see describe_deposit)")
            full = self._resolve(path)
            if full is None:
                raise DesignError(f"{path} is not a file of the deposit, its extracted archives or its cell tables")
            if not full[:-3 if full.endswith(".gz") else None].lower().endswith(TEXT_SUFFIXES):
                raise DesignError(f"{path} is not a text file ({', '.join(TEXT_SUFFIXES)}, optionally gzipped)")
            import gzip
            lines, chars, more = [], 0, False
            with (gzip.open(full, "rt", errors="replace") if full.endswith(".gz")
                  else open(full, encoding="utf-8", errors="replace")) as fh:
                for i, line in enumerate(fh):
                    if i < offset:
                        continue
                    if len(lines) >= PAGE_LINES or chars + len(line) > MAX_CHARS:
                        more = True
                        break
                    lines.append(line.rstrip("\n"))
                    chars += len(line)
            return {"path": path, "lines": lines, "next_offset": offset + len(lines) if more else None}
        raise DesignError(f"unknown item {item!r}: geo, guides, object, config or file")

    def measure(self, refresh=False, extract=False):
        """extract: extract the deposit's archives into the state folder first (each into its own folder); ask the
        user before, since it writes a copy of the data. Archives extracted before are always read. In the background
        (the config's background_measure), a new measurement runs in a thread of its own and the call waits for it up
        to MEASURE_WAIT seconds: a small deposit is answered at once, a large one is reported as running, and the next
        call reports it again, or its measurement once done."""
        path = f"{self.dir}/measurements.json"
        if not self.cfg.get("background_measure"):
            if refresh or extract or not os.path.exists(path):
                self._measure_now(extract)
            return self._measurement_answer()
        with _MEASURING_LOCK:
            job = _MEASURING.get(self.dir)
            if job is None or (not job["thread"].is_alive() and job.get("reported")):
                if not (refresh or extract or not os.path.exists(path)):
                    return self._measurement_answer()
                job = {"started": time.strftime("%Y-%m-%dT%H:%M:%S"), "extract": bool(extract), "error": None}

                def run(job=job):
                    try:
                        self._measure_now(extract)
                    except Exception as e:  # reported to the agent on its next call
                        job["error"] = f"{type(e).__name__}: {e}"
                job["thread"] = threading.Thread(target=run, name="pertura-measure", daemon=True)
                job["t0"] = time.monotonic()
                _MEASURING[self.dir] = job
                job["thread"].start()
        first = float(self.cfg.get("measure_wait", MEASURE_WAIT))
        ran = time.monotonic() - job.get("t0", time.monotonic())
        job["thread"].join(min(MAX_MEASURE_WAIT, max(first, ran / 3)))
        if job["thread"].is_alive():
            ran = time.monotonic() - job.get("t0", time.monotonic())
            return {"state": "running", "started": job["started"], "extract": job["extract"],
                    "running_seconds": round(ran),
                    "next": "it runs in the background: wait_for_runs returns when it ends, and measure again "
                            f"waits longer each call, up to {MAX_MEASURE_WAIT} s; extracting and reading a large "
                            "deposit takes minutes"}
        job["reported"] = True
        if job["error"]:
            raise DesignError(f"the measurement failed: {job['error']}")
        return self._measurement_answer()

    def in_background(self):
        """What this design's state runs in the background, so a wait can include it: the measurement (a thread of
        this server) and the screen object's build (a job), each {what, state, started, done}, where done() says
        whether it has ended. Starts nothing."""
        from . import runner
        out = []
        job = _MEASURING.get(self.dir)
        if job is not None and job["thread"].is_alive():
            out.append({"what": "the measurement", "state": "running", "started": job["started"],
                        "done": lambda t=job["thread"]: not t.is_alive()})
        state = f"{self.dir}/screen_object_job.json"
        if os.path.exists(state):
            try:
                with open(state, encoding="utf-8") as fh:
                    jid = json.load(fh)["job_id"]
                root = self.cfg.get("runner_root") or f"{self.dir}/jobs"
                info = runner.status(jid, root)
            except (OSError, ValueError, KeyError):
                info = None
            if info and info["state"] in runner.ACTIVE:
                out.append({"what": "the screen object's build", "state": "running", "job_id": jid,
                            "started": info.get("created"),
                            "done": lambda j=jid, r=root: runner.status(j, r)["state"] not in runner.ACTIVE})
        return out

    def _measure_now(self, extract):
        """Measure the deposit and keep the measurement in the state folder (the text matrices parsed in parallel and
        kept in parsed/ for the screen build)."""
        from . import cell_table, limits
        path = f"{self.dir}/measurements.json"
        t0 = time.time()
        tables = f"{self.dir}/cell_tables"
        if not cell_table.samples(tables):  # docs/adr/0041, step 1b: the deposit read into cell tables as proposed
            dep, _, _ = open_deposit(self.root, f"{self.dir}/extracted", extract=extract)
            ref = read_feature_reference(dep["feature_references"][0]) if dep["feature_references"] else None
            cell_table.propose(dep, tables, declared_types(ref))
        raw = audit(self.root, self.cfg.get("geo"), extract_to=f"{self.dir}/extracted", extract=extract,
                    parsed=f"{self.dir}/parsed", workers=max(1, limits.THREAD_LIMIT - 1),
                    cell_tables=f"{self.dir}/cell_tables")
        save_arrays(raw.get("arrays") or {}, f"{self.dir}/arrays.npz")
        r = _plain({k: raw[k] for k in ("facts", "questions", "library")})
        m = {"facts": r["facts"], "questions": r["questions"], "library": r["library"],
             "seconds": round(time.time() - t0, 1), "time": time.strftime("%Y-%m-%dT%H:%M:%S")}
        with open(path + ".tmp", "w", encoding="utf-8") as fh:
            json.dump(m, fh)
        replace_file(path + ".tmp", path)

    def _measurement_answer(self):
        from . import cell_table
        m = self._measured()
        read = cell_table.reading(f"{self.dir}/cell_tables")
        return {"facts": m["facts"], "questions": m["questions"], "measured": m["time"],
                "library": {"guides": len(m["library"].get("guides", [])),
                            "samples": m["library"].get("samples", []),
                            "hashtag_like_features": m["library"].get("hashtags", [])},
                **({"read_into_cell_tables": {**read, "do": READING_DO}} if read else {})}

    # ---- the design: checked by key, then confirmed as a whole
    def _last_proposal(self):
        """The design checked last, or None."""
        for c in reversed(self._read("checks.jsonl")):
            if c.get("proposal_sha256"):
                return self._proposal(c["proposal_sha256"])
        return None

    def check_design(self, design=None, why=None):
        import yaml
        measured = self._measured()
        tables = design_check.tables_now(f"{self.dir}/cell_tables")
        nodes, problems = design_shapes.validate(design, {"samples": tables["samples"]}, why)
        if problems:  # a value in another shape: nothing else is read, nothing is stored
            self._append("checks.jsonl", {"time": _now(), "proposal_sha256": None, "ok": False, "problems": problems,
                                          "flags": []})
            return {"ok": False, "problems": problems,
                    "next": "send those keys again in their shapes (check_design); the rest stays as checked last"}
        p = copy.deepcopy(self._last_proposal() or {})
        p["schema"] = design_gate.SCHEMA
        for key, n in nodes.items():
            section, _, leaf = key.partition(".")
            x = p.setdefault(section, {})
            if n is None:
                x.pop(leaf, None)
            else:
                x[leaf] = {"value": design_shapes.plain(n["value"]), "status": "proposed", "source": n["source"],
                           **({"note": n["note"]} if n.get("note") else {})}
        p = {k: v for k, v in p.items() if v != {}}  # a section its last key left
        if design_gate.node(p, "perturbation.assignment") is None:  # the build's default, on the user's table
            p.setdefault("perturbation", {})["assignment"] = {"value": {"rule": "auto"}, "status": "proposed",
                                                              "source": AUTO_SOURCE}
        text = yaml.safe_dump(_plain(p), sort_keys=False, allow_unicode=True, width=120)
        sha = _sha(text)
        with open(f"{self.dir}/proposals/{sha}.yaml", "w", encoding="utf-8") as fh:
            fh.write(text)
        problems = design_check.check(p, measured, tables)
        flags = self._displays(p, measured, tables, counts=not problems)
        self._append("checks.jsonl", {"time": _now(), "proposal_sha256": sha, "ok": not problems,
                                      "problems": problems, "flags": flags})
        out = {"proposal_sha256": sha, "ok": not problems, "problems": problems, "flags": flags,
               "next": ("confirm_design: the user answers whether the whole design is right" if not problems else
                        "fix the problems (check_design with the keys that change), then check again")}
        if not problems:  # the user's page shows it before the form: each item, where it comes from, what was measured
            by_key = {}
            for f in flags:
                by_key.setdefault(f["key"], []).append(f["text"])
            out["proposal"] = form_details(p, keys_of(p), by_key)
        return out

    def _displays(self, p, measured, tables, counts=True):
        """What the measurement found beside each value, for the agent and the user, never blocking: design_audit.checks'
        flags (with the cell tables' columns as they are now), the cells each guide rule leaves (counts: once the
        design passes, whatever that module's own problems say), the measured facts that bear on a value, how the
        deposit was read, and after the build the cells' rule counted and the reading checks."""
        from . import cell_table
        cols = tables["columns"]
        lib = {**(measured.get("library") or {}),
               "cell_table_columns": {c: (sorted({v for n in by.values() for v in n}) if None not in by.values() else None)
                                      for c, by in cols.items()},
               "cell_table_columns_by_sample": {c: {s: n for s, n in by.items() if n is not None}
                                                for c, by in cols.items()}}
        arrays = load_arrays(f"{self.dir}/arrays.npz")
        flags = proposal_checks.check(p, {**measured, "library": lib}, arrays, kept=f"{self.dir}/parsed")["flags"]
        facts = {f["key"]: f for f in measured.get("facts", []) if isinstance(f, dict) and f.get("key")}
        if counts and not any(f.get("rules") for f in flags):  # skipped there on a problem it no longer decides
            proposal_checks._assignment_left(p, lib, flags, arrays, facts, f"{self.dir}/parsed")
        for k, words in measured_flags(facts, list(TITLES)).items():
            flags += [{"key": k, "text": w} for w in words]
        by_agent = cell_table.made_by_agent(f"{self.dir}/cell_tables")
        if by_agent:  # the user confirms knowing which samples rest on code the agent wrote
            flags.append({"key": "data.read_by", "text": "read by the agent's own code, not by one of Pertura's "
                          "readers: " + "; ".join(f"{n} ({run})" for n, run in sorted(by_agent.items()))})
        read = cell_table.reading(f"{self.dir}/cell_tables")
        if read:  # how the deposit was read, files to samples (docs/adr/0041, step 1b)
            flags.append({"key": "data.read_by", "text": f"the deposit read as {len(read['samples'])} samples ("
                          + "; ".join(f"{n} from {', '.join(s['from']) or 'the agent'}"
                                      for n, s in list(read["samples"].items())[:6])
                          + (" ..." if len(read["samples"]) > 6 else "") + ")"
                          + ("; " + "; ".join(read["notes"][:6]) if read["notes"] else "")})
        built = self._built_overview()
        rule = (design_gate.node(p, "data.qc") or {}).get("value")
        if rule is not None and built:
            flags.append({"key": "data.qc", "text": self._rule_counted(built, rule)})
        if built:  # the reading checks go with the cells' rule, where the user sees the cells
            try:
                with open(os.path.join(built, "screen.json"), encoding="utf-8") as fh:
                    checks = json.load(fh).get("reading_checks") or []
            except (OSError, ValueError):
                checks = []
            flags += [{"key": "data.qc", "on_the_form": True, "text": f"Read from the deposit: {c['text']}"}
                      for c in checks]
        return flags


    def _built_overview(self):
        """The screen object's folder once it is built with its cells overview (pertura.cells_overview), else None:
        from then on the cells' quality rule is asked."""
        from . import cells_overview
        sd = f"{self.dir}/screen_object"
        ok = os.path.exists(f"{sd}/screen.json") and os.path.exists(f"{sd}/{cells_overview.FOLDER}/measures.tsv.gz")
        return sd if ok else None

    def _rule_counted(self, screen_dir, rule):
        """A proposed quality rule in numbers, for the form: the cells it keeps of those with a unit, by sample, and
        what each filter leaves out; its figure (the cuts drawn, the clusters left out marked) beside the overview."""
        from . import cells_overview
        try:
            t = cells_overview.rule_counts(screen_dir, rule)
            cells_overview.draw(screen_dir, rule, name="figure_proposed")
        except (OSError, ValueError, KeyError) as e:
            return f"the rule could not be counted on the cells overview ({e})"
        total, kept = int(t["cells_with_a_unit"].sum()), int(t["kept"].sum())
        share = (t["kept"] / t["cells_with_a_unit"].clip(lower=1)).to_numpy()
        out = [c for c in t.columns if c.startswith("left out: ")]
        words = (f"the rule keeps {kept:,} of the {total:,} cells with a unit ({kept / max(total, 1):.0%}; by sample "
                 f"{share.min():.0%} to {share.max():.0%})")
        if out:
            words += "; it leaves out " + "; ".join(f"{c[10:]}: {int(t[c].sum()):,}" for c in out)
        return words + f". The figure with its cuts: {cells_overview.FOLDER}/figure_proposed.png"

    def confirm_design(self, elicit=None):
        checks = self._read("checks.jsonl")
        if not checks:
            raise DesignError("check a design first (check_design)")
        last = checks[-1]
        if not last["ok"]:
            raise DesignError("the last check has problems (" + "; ".join(last["problems"][:3]) + "); fix them and "
                              "check again (check_design)")
        sha = last["proposal_sha256"]
        p = self._proposal(sha)
        built = self._built_overview()
        if built and design_gate.node(p, "data.qc") is None:  # asked after the build, a rule or none
            p.setdefault("data", {})["qc"] = {"value": {"filters": []}, "status": "proposed", "source": NO_RULE}
        confirmed = design_gate.load(self.design_file)[0] if os.path.exists(self.design_file) else None
        same = (confirmed is not None and not confirmed.get("change_asked")
                and _values_but_qc(confirmed) == _values_but_qc(p))
        cells = same and built is not None and self._cells_rule_waits(confirmed, p, built)
        if same and not cells:
            return {**self._confirmed_reply(confirmed, p, ""), "note": "this design is confirmed as it stands; "
                                                                     "nothing new to ask"}
        if elicit is None:
            raise DesignError("this client cannot show Pertura's confirmation form (it has no MCP elicitation), so "
                              "the design cannot be confirmed from this session. Tell the user; the operator can "
                              "supply a confirmed design file instead")
        if cells:
            message, title = self._cells_form(p, last), "The cells' quality rule"
        else:
            message, title = self._design_form(p, last), "The design"
        action, content = elicit(message, form_schema(title))
        now = _now()
        content = content if isinstance(content, dict) else {}
        answer, note = content.get("answer"), str(content.get("note") or "").strip()
        self._append("confirmations.jsonl", {"time": now, "action": action, "asked": "cells_rule" if cells else
                                             "design", "answer": answer, "note": note, "by": BY,
                                             "proposal_sha256": sha})
        if action != "accept" or answer not in ("confirm", "change"):
            return {"confirmed": [], "action": action, "note": "the user did not answer the form; nothing was confirmed"}
        if answer == "change":
            if confirmed is not None and not cells:  # the design they said differs: nothing runs on it until Right
                self._write_design({**confirmed, "change_asked": {"note": note, "time": now, "proposal_sha256": sha}})
            return {"confirmed": [], "not_confirmed": {"design": "the user asked for a change"},
                    "design_confirmed": False, "user_note": note, "next": CHANGE_NEXT}
        if cells:
            self._confirm_cells_rule(confirmed, p, sha, note, now)
        else:
            self._write_confirmed(p, sha, note, now)
        return self._confirmed_reply(design_gate.load(self.design_file)[0], p, note)

    def _cells_rule_waits(self, confirmed, p, built):
        """Whether the cells' rule waits on the user: the proposed one is not the one confirmed since this build."""
        from . import cells_overview
        qc = design_gate.node(p, "data.qc")
        if qc is None:
            return False
        if design_gate.missing_keys(confirmed, ["data.qc"]) or design_gate.canonical(
                design_gate.confirmed_value(confirmed, "data.qc")) != design_gate.canonical(qc.get("value")):
            return True
        dates = [c.get("date") for c in confirmed.get("confirmations") or []
                 if isinstance(c, dict) and c.get("key") == "data.qc"]
        return not cells_overview.confirmed_on_this_build(built, dates)

    def _design_form(self, p, last):
        """The form's message for the whole design: one line per key, its value in plain words, where it comes from and
        what was measured beside it; the user's page shows the same with every measurement."""
        by_key = {}
        for f in last.get("flags") or []:
            by_key.setdefault(f["key"], []).append(f["text"])
        keys = keys_of(p)
        lines = []
        for k in keys:
            n = design_gate.node(p, k) or {}
            line = f"- {TITLES.get(k, k)}: {form_value(p, k)}"
            if n.get("source"):
                line += f" (from: {_short(n['source'])})"
            said = [_short(_plain_words(t)) for t in by_key.get(k, [])[:2]]
            if said:
                line += ". Measured: " + "; ".join(said)
            lines.append(line)
        # left out, the modules pool every capture, so the user sees the gap and can answer Change
        lines += [f"- {TITLES[k]}: not given" for k in (BATCHES, "units.capture_factor") if k not in keys]
        other = [_short(_plain_words(t)) for k, ts in by_key.items() if k not in keys for t in ts][:3]
        return ("Pertura: is this design of the screen right? No effect analysis runs until you say it is. Answer "
                "Right, or Change and say in the note what differs; your page shows each line with what Pertura "
                "measured.\n" + "\n".join(lines) + ("\nAlso measured: " + " | ".join(other) if other else ""))

    def _cells_form(self, p, last):
        """The form's message for the cells' quality rule after the build, with its counts and the reading checks."""
        said = [_short(_plain_words(f["text"]), 400) for f in last.get("flags") or [] if f["key"] == "data.qc"]
        return ("Pertura: is this quality rule for the cells right? The analyses start once you say it is. Answer "
                f"Right, or Change and say in the note what differs. The rule: {form_value(p, 'data.qc')}."
                + ("\n" + "\n".join(f"- {s}" for s in said) if said else ""))

    def _write_confirmed(self, p, sha, note, now):
        """design.yaml: this version of the design, every key confirmed by the user's one answer, with their note."""
        d = copy.deepcopy(p)
        d["confirmations"], d["unresolved"] = [], []
        for key, n in proposal_checks.facts_of(d):
            n["status"] = "confirmed"
            d["confirmations"].append({"key": key, "value": design_gate.confirmed_value(d, key), "by": BY, "date": now,
                                       "answer": "confirm", "note": note, "proposal_sha256": sha})
        d["checked_proposal_sha256"] = sha
        self._write_design(d)

    def _confirm_cells_rule(self, confirmed, p, sha, note, now):
        """The confirmed design with the cells' quality rule confirmed, on this build."""
        d = copy.deepcopy(confirmed)
        d.setdefault("data", {})["qc"] = {**design_gate.node(p, "data.qc"), "status": "confirmed"}
        d["confirmations"] = [c for c in d.get("confirmations") or [] if not (isinstance(c, dict)
                                                                             and c.get("key") == "data.qc")]
        d["confirmations"].append({"key": "data.qc", "value": design_gate.confirmed_value(d, "data.qc"), "by": BY,
                                   "date": now, "answer": "confirm", "note": note, "proposal_sha256": sha})
        self._write_design(d)

    def _write_design(self, d):
        import yaml
        with open(self.design_file + ".tmp", "w", encoding="utf-8") as fh:
            yaml.safe_dump(_plain(d), fh, sort_keys=False, allow_unicode=True, width=120)
        replace_file(self.design_file + ".tmp", self.design_file)

    def _confirmed_reply(self, d, p, note):
        """What the user confirmed, in the words of the user's table, and what comes next."""
        st = design_gate.status(self.design_file)
        keys = keys_of(d)
        done = [k for k in keys if design_gate.confirmed(d, k) is not None]
        return {"confirmed": [k for k in design_gate.REQUIRED if k not in st["missing"]],
                "also_confirmed": [k for k in done if k not in design_gate.REQUIRED],
                "not_confirmed": {k: st["missing"][k] for k in st["missing"] if k in design_gate.REQUIRED},
                "confirmed_values": {TITLES.get(k, k): form_value(d, k) for k in done},
                "origins": {TITLES.get(k, k): origin(p, k) for k in done},
                "user_note": note, "design_file": self.design_file, "design_confirmed": not st["missing"],
                "gate": "a screen's gate opens once its design is confirmed and the screen agrees with it (built "
                        "from this design)",
                "next": ("build_screen_object" if not self._built_overview() else
                         "the analyses start once the cells' quality rule is confirmed too")}


    def build_screen_object(self, wait=0, retry=False):
        from . import runner
        operator = self.cfg.get("operator_design")
        path = operator if operator and os.path.exists(operator) else self.design_file
        st = design_gate.status(path)
        if st["missing"]:
            raise DesignError("the design is not confirmed yet (open: " + ", ".join(st["missing"]) + "); confirm it "
                              "first (confirm_design)")
        root = self.cfg.get("runner_root") or f"{self.dir}/jobs"
        os.makedirs(root, exist_ok=True)
        state = f"{self.dir}/screen_object_job.json"
        if os.path.exists(state):
            with open(state, encoding="utf-8") as fh:
                job = json.load(fh)
            if job.get("design_sha256") == build_sha(path):
                if wait:  # the client moves a long call to the background and says when it returns
                    runner.wait(job["job_id"], root, timeout=max(0, min(int(wait), CALL_WAIT_MAX)))
                info = runner.status(job["job_id"], root)
                out = {"job_id": job["job_id"], "state": info["state"],
                       "note": f"the build of the confirmed design as it stands (started {job.get('started', '?')}); "
                               "it is rebuilt only when the confirmed design changes, so a proposed change takes "
                               "effect after the user confirms it (confirm_design)"}
                summary = f"{self.dir}/screen_object/screen.json"
                if info["state"] == "done" and os.path.exists(summary):
                    from . import cells_overview
                    with open(summary, encoding="utf-8") as fh:
                        sm = json.load(fh)
                    out.update({k: sm.get(k) for k in ("cells", "labels", "by_unit", "samples", "flags", "rule")})
                    checks = [c["text"] for c in sm.get("reading_checks") or []]
                    if checks:  # docs/adr/0041, step 1a: what was read, against what any correct reading holds
                        out["reading_checks"] = {
                            "facts": checks,
                            "do": ("say for each whether the design expects it (a sample without a guide library "
                                   "holds no guides); one it does not expect means the deposit was read wrong: "
                                   "correct the design or the reading (check_design) and build again. The user "
                                   "sees them on the form after the build")}
                    # whole paths, which a message to the design auditor passes on as they are
                    so_dir = os.path.abspath(f"{self.dir}/screen_object")
                    out.update({"screen_object": so_dir,
                                "cells_overview": os.path.join(so_dir, cells_overview.FOLDER)})
                elif info["state"] == "failed":
                    out["log_tail"] = info["log_tail"]
                    out["next"] = ("the build failed; the log's last lines say why. Where the design is the cause, "
                                   "revise it (check_design) and let the user confirm it (confirm_design): the build "
                                   "then runs again. retry runs it again on the same design")
                elif info["state"] in runner.ACTIVE:
                    out["next"] = BUILD_RUNS
                if info["state"] not in ("lost", "stopped") and not (retry and info["state"] == "failed"):
                    return out
        cmd = [sys.executable, "-m", "pertura.screen_object", "--root", self.root,
               "--design", path, "--out", f"{self.dir}/screen_object", "--extract-to", f"{self.dir}/extracted",
               "--parsed", f"{self.dir}/parsed", "--cell-tables", f"{self.dir}/cell_tables"]
        jid = runner.start(cmd, "screen-object", root)
        with open(state, "w", encoding="utf-8") as fh:
            json.dump({"job_id": jid, "design_sha256": build_sha(path),
                       "started": time.strftime("%Y-%m-%dT%H:%M:%S")}, fh)
        if wait:
            return self.build_screen_object(wait=wait)
        return {"job_id": jid, "state": "started", "next": BUILD_RUNS}

    def get_design(self):
        """What is confirmed and what is open. A registered screen's design, confirmed by the operator, is the one the
        gate reads, so it is the one reported (found by an analyst agent's session, 2026-10-08: the design server's
        own empty record looked like no design at all)."""
        operator = self.cfg.get("operator_design")
        path = operator if operator and os.path.exists(operator) else self.design_file
        st = design_gate.status(path)
        checks = self._read("checks.jsonl")
        last = checks[-1] if checks else None
        values = {}
        if os.path.exists(path):  # every fact of the design with its value, so a reader need not open the file
            def walk(x, key=""):
                if isinstance(x, dict) and "value" in x and "status" in x:
                    values[key] = x["value"]
                elif isinstance(x, dict):
                    for k, v in x.items():
                        if k not in ("confirmations", "unresolved", "schema"):
                            walk(v, f"{key}.{k}" if key else k)
            walk(design_gate.load(path)[0])
        return {"design_file": path if os.path.exists(path) else None,
                "confirmed_by": "operator" if path == operator else "user",
                "confirmed": [k for k in design_gate.REQUIRED if k not in st["missing"]],
                "values": values,
                "open": st["missing"],
                "last_check": None if last is None else {"proposal_sha256": last["proposal_sha256"], "ok": last["ok"],
                                                         "problems": list(last["problems"]),
                                                         "flags": list(last["flags"]), "time": last["time"]},
                "measured": os.path.exists(f"{self.dir}/measurements.json"),
                "screen_object": os.path.exists(f"{self.dir}/screen_object/screen.json")}
