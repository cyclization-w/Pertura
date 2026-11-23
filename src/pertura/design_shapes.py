"""The design's keys, each with one shape (docs/build/design_confirmation_rebuilt_plan_2026-11-19.md).

A shape is a pydantic type. The same type gives the input schema of check_design, which the agent sees on every call,
and the server's validation of what the agent sends, since a client shows a tool's schema to the model but does not
enforce it. The agent sends {dotted key: {value, source, note}}; a value in the wrong shape is refused with the key's
shape, an example in this deposit's names where the key takes them, and nothing is read from the words.

The design file (design.yaml) keeps its layout: each key a node {value, status, source, note} under its section, which
the build and the modules read through design_gate.node.
"""
from typing import Annotated, Literal, Optional, Union

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, TypeAdapter, ValidationError, model_validator

from .contracts import _WithoutTitles
from .design_gate import CONTEXT_VALUES, DELIVERIES, GUIDE_ATTRIBUTES, MODALITIES

CONTROL_KINDS = ("non_targeting", "safe_targeting", "cutting_control", "intergenic", "untransduced",
                 "pooled_perturbations_as_control", "other")
# the guide-calling rules the screen object builds from (pertura.screen_object: perturbation.assignment)
ASSIGNMENT_RULES = ("auto", "deposit", "dominance", "combination", "labels", "per_guide_threshold", "cellranger")
ORGANISMS = ("human", "mouse", "other")
FROM_REFERENCE = "feature_reference"
NOT_RECORDED = "not recorded"


def _lower(v):
    return v.strip().lower() if isinstance(v, str) else v


Name = Annotated[str, Field(min_length=1)]
Names = Annotated[list[Name], Field(min_length=1)]
OneWord = Annotated[str, Field(pattern=r"^[A-Za-z][A-Za-z_-]*$")]
Modality = Annotated[Literal[tuple(MODALITIES)], BeforeValidator(_lower)]


class _Shape(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class BySample(_Shape):
    by_sample: dict[Name, Name] = Field(min_length=1)


class ByUnit(_Shape):
    by_unit: dict[Name, Name] = Field(min_length=1)


class Control(_Shape):
    name: Name
    kind: Literal[CONTROL_KINDS]
    guides: Names
    description: Optional[str] = None

    @model_validator(mode="after")
    def _other_described(self):
        if self.kind == "other" and not self.description:
            raise ValueError("a control of kind 'other' needs a description")
        return self


class CaptureFactorBySample(_Shape):
    name: Name
    by_sample: dict[Name, Name] = Field(min_length=1)


class CaptureFactorByUnit(_Shape):
    name: Name
    by_unit: dict[Name, Name] = Field(min_length=1)


class GuideAttributes(BaseModel):
    model_config = ConfigDict(extra="allow", str_strip_whitespace=True)  # other attributes are kept, not read
    mode: Optional[str] = None
    gene: Optional[str] = None
    element: Optional[str] = None
    vector: Optional[str] = None


class DeliveryBySample(_Shape):
    by_sample: dict[Name, Literal[DELIVERIES]] = Field(min_length=1)


class DeliveryByUnit(_Shape):
    by_unit: dict[Name, Literal[DELIVERIES]] = Field(min_length=1)


class Assignment(BaseModel):
    model_config = ConfigDict(extra="allow", str_strip_whitespace=True)  # a rule's other settings are the build's
    rule: Literal[ASSIGNMENT_RULES]
    min_umis: Optional[int] = Field(None, ge=1)
    ratio: Optional[Union[int, float]] = Field(None, gt=0)
    min_share: Optional[float] = Field(None, ge=0, le=1)
    source: Optional[Literal["matrix", "calls"]] = None
    column: Optional[str] = None

    @model_validator(mode="after")
    def _labels_named(self):
        if self.rule == "labels" and not self.column:
            raise ValueError("the labels rule needs the column that holds the labels (column)")
        return self


class OneFactor(_Shape):
    column: Name
    names: Names
    compared: bool = True


class Factor(_Shape):
    name: Name
    levels: Names
    column: Name
    compared: bool = True


Conditions = Union[Annotated[list, Field(max_length=0)], OneFactor, Annotated[list[Factor], Field(min_length=1)]]


def _qc(rule):
    from .quality import validate
    problems = validate(rule)
    if problems:
        raise ValueError("; ".join(problems))
    return rule


# key -> (shape, title on the user's table, what it decides, what the agent gives)
KEYS = {
    "units.independent_unit": (
        OneWord, "Biological replicates", "what counts as an independent sample in the statistics",
        "what one biological replicate is, one word: mouse, donor, culture, or none where the cells cannot be told apart "
        "by their replicate"),
    "units.unit_column": (
        Name, "Unit column", "which cells belong to one replicate",
        "the cell-table column holding each cell's unit (written with pertura.cell_table.annotate(cell_tables, ...)); "
        "leave this key out where each sample is one unit"),
    "units.pooled_from": (
        Name, "Pooled from", "how many animals or donors one sample holds",
        "what one sample's cells were pooled from, in words (for example '5 mice')"),
    "units.merge": (
        dict[Name, Name], "Units merged", "which captured units are one replicate",
        "{captured unit: the replicate it belongs to}, where two units are one replicate (two channels of one mouse)"),
    "units.batches": (
        Union[Literal[NOT_RECORDED], BySample, ByUnit], "Batches", "which cells are compared within one batch",
        "which samples or units ran together: {by_sample: {sample: batch}} or {by_unit: {unit: batch}}, or "
        "'not recorded'"),
    "units.capture_factor": (
        Union[CaptureFactorBySample, CaptureFactorByUnit], "Another factor grouping the captures",
        "which captures are pooled into one profile",
        "a factor other than the batch that groups the samples or units (a cell line, a construct): "
        "{name, by_sample: {sample: level}} or {name, by_unit: {unit: level}}"),
    "perturbation.controls": (
        Annotated[list[Control], Field(min_length=1)], "Controls", "the cells every perturbation is compared with",
        "[{name, kind, guides: [the deposit's guide names]}], kind one of " + ", ".join(CONTROL_KINDS)
        + " ('other' with a description)"),
    "perturbation.modality": (
        Modality, "Modality", "whether a target gene's expression should go down or up",
        "knockout, crispri, crispra, base_edit, other, or mixed (each guide's mode then in perturbation.guides)"),
    "perturbation.target_map": (
        Union[Literal[FROM_REFERENCE], dict[Name, Names]], "Targets", "which guides are pooled as one perturbation",
        "{target: [its guide names]}, or 'feature_reference' for the feature reference's labels as they are"),
    "perturbation.guides": (
        dict[Name, GuideAttributes], "Guides", "which gene each guide should move, and how",
        "{guide: {mode, gene, element, vector}}, where the target names do not say them"),
    "perturbation.delivery": (
        Union[Literal[DELIVERIES], DeliveryBySample, DeliveryByUnit], "Delivery", "which cells are analysed as pairs",
        "singles or pairs, for the whole screen, or {by_sample: {sample: singles or pairs}}"),
    "perturbation.reference_matches": (
        Union[Literal[GUIDE_ATTRIBUTES], Annotated[list[Literal[GUIDE_ATTRIBUTES]], Field(min_length=1)]],
        "Reference per arm", "which control cells each target is compared with",
        "the guide attributes a target's control cells must share with it: " + ", ".join(GUIDE_ATTRIBUTES)),
    "perturbation.vectors": (
        dict[Name, Names], "Vectors", "which guides count as one construct",
        "{vector: [its guides]}, for vectors that carry several guides of one target"),
    "perturbation.sets": (
        Annotated[list[Names], Field(min_length=1)], "Sets", "which targets are called together",
        "[[targets called together]], for combinations of targets in one cell"),
    "perturbation.assignment": (
        Assignment, "Guide calls", "which cells count as carrying a guide",
        "{rule, ...its settings}, rule one of " + ", ".join(ASSIGNMENT_RULES)
        + " (labels needs its column); without it the build uses auto"),
    "perturbation.moi": (
        Annotated[Literal["low", "high"], BeforeValidator(_lower)], "MOI", "how many guides a cell is expected to carry",
        "low or high, where the deposit states it"),
    "experiment.conditions": (
        Conditions, "Conditions",
        "how cells are split: each condition analysed apart, against its own controls, compared only if you ask",
        "[] where every cell had the same treatment and time point; {column, names} for one factor read from a "
        "cell-table column; [{name, levels, column}] for crossed factors; compared: false where the levels are "
        "separate experiments never set against each other"),
    "experiment.context": (
        Literal[CONTEXT_VALUES], "Context", "what a screen with few control guides is told",
        ", ".join(CONTEXT_VALUES)),
    "experiment.organism": (
        Literal[ORGANISMS], "Organism", "the gene sets, regulons and pathway scores used", ", ".join(ORGANISMS)),
    "data.cell_calling": (
        dict, "Cell calling", "which barcodes count as cells", "the build's cell calling settings, where the deposit "
                                                               "holds every droplet"),
    "data.qc": (
        Annotated[dict, BeforeValidator(_qc)], "Quality control", "which cells every analysis leaves out for their "
                                                                  "quality",
        "after the build, from the cells overview: {filters: [{metric, min or max or mads, side}], "
        "keep_cells_without_a_unit, reasons: {filter: why}}"),
}
REQUIRED = ("units.independent_unit", "perturbation.controls", "perturbation.modality", "experiment.conditions")
_ADAPTERS = {k: TypeAdapter(shape) for k, (shape, *_rest) in KEYS.items()}


def _value_schema(key):
    s = _ADAPTERS[key].json_schema(schema_generator=_WithoutTitles, union_format="primitive_type_array")
    s.pop("title", None)
    return s


def input_schema():
    """check_design's input schema: design, {dotted key: {value, source, note}} with each key's value in its shape,
    and why, the source of a value sent bare."""
    keys = {k: {"type": "object", "description": f"{KEYS[k][3]}. It decides {KEYS[k][2]}.",
                "properties": {"value": _value_schema(k), "source": {"type": "string"}, "note": {"type": "string"}},
                "required": ["value", "source"], "additionalProperties": False} for k in KEYS}
    return {"type": "object", "properties": {
        "design": {"type": "object", "properties": keys, "additionalProperties": False,
                   "description": "the design by dotted key, each {value, source, note}: the first call gives every "
                                  "key known; a later call gives only the keys that change (null removes one), the "
                                  "rest stays as checked last"},
        "why": {"type": "string", "description": "the source of any value given bare"}},
        "required": ["design"], "additionalProperties": False}


def example(key, names):
    """An example of a key's shape in this deposit's names (names: {samples: [...], units: [...]}), or None."""
    samples = [str(s) for s in (names or {}).get("samples") or []][:3] or ["<sample>"]
    if key == "units.batches":
        return {"by_sample": {s: "<its batch>" for s in samples}}
    if key == "units.capture_factor":
        return {"name": "<the factor>", "by_sample": {s: "<its level>" for s in samples}}
    if key == "perturbation.delivery":
        return {"by_sample": {s: "singles" for s in samples}}
    if key == "experiment.conditions":
        return {"column": "condition", "names": ["<each level the column holds>"]}
    return None


def _said(error):
    """A pydantic error in a few words: where in the value, and what it takes."""
    first = error.errors()[0]
    where = ".".join(str(x) for x in first.get("loc") or ())
    return (f"at {where}: " if where else "") + str(first.get("msg", "")).replace("Value error, ", "")


def validate(design, names=None, why=None):
    """(nodes {key: {value, source, note}}, problems) for what the agent sent. A value sent bare takes why as its
    source; null removes a key (returned as None)."""
    nodes, problems = {}, []
    if not isinstance(design, dict) or not design:
        return {}, ["design: give {dotted key: {value, source, note}}, for example {\"perturbation.modality\": "
                    "{\"value\": \"crispri\", \"source\": \"the GEO summary\"}}"]
    for key, given in design.items():
        key = str(key)
        if key not in KEYS:
            problems.append(f"{key}: not a key of the design; the keys are {', '.join(KEYS)}")
            continue
        if given is None:
            nodes[key] = None
            continue
        node = dict(given) if isinstance(given, dict) and "value" in given else {"value": given, "source": why}
        odd = sorted(set(node) - {"value", "source", "note"})
        if odd:
            problems.append(f"{key}: {', '.join(odd)} is not part of a key's entry ({{value, source, note}})")
            continue
        if not str(node.get("source") or "").strip():
            problems.append(f"{key}: give its source, where the value comes from (the GEO text, a file, the user, "
                            "or what the data showed)")
            continue
        try:
            node["value"] = _ADAPTERS[key].validate_python(node["value"])
        except ValidationError as e:
            ex = example(key, names)
            problems.append(f"{key}: {_said(e)}. Its shape: {KEYS[key][3]}"
                            + (f"; in this deposit, for example {ex}" if ex else ""))
            continue
        nodes[key] = {k: v for k, v in node.items() if v not in (None, "")}
    return nodes, problems


def plain(value):
    """A validated value as plain data (pydantic models dumped), for the design file."""
    if isinstance(value, BaseModel):
        return value.model_dump(exclude_none=True)
    if isinstance(value, list):
        return [plain(v) for v in value]
    if isinstance(value, dict):
        return {k: plain(v) for k, v in value.items()}
    return value
