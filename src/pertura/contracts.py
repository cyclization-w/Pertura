"""Typed contracts at the record's doors (docs/adr/0038, D1): the inputs of the tools that write the record's main
nouns (a finding, a thread's end, a result), as pydantic models (pydantic 2).

Each of these tools' JSON schema is generated from its model (schema), with no titles and its definitions inlined so
the tool list does not grow. The analysis service validates a call with it before the call runs (checked). An error
names the field and what it takes. The record's checks against its state (a referenced finding exists, a finding is
not recorded twice) stay in pertura.record."""
from typing import Annotated, Any, Literal, Optional

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, ValidationError, model_validator
from pydantic.json_schema import GenerateJsonSchema

from .knowledge_store import MOVES


def _id_of(value):
    """A thread or a finding given as its object ({"thread_id": "t12"}): its ID (the lead's review crashed on one,
    docs/build/fair_comparison_problems_seen_while_it_runs.md #21); anything else as it came."""
    if isinstance(value, dict):
        for key in ("thread_id", "finding_id", "id"):
            if isinstance(value.get(key), str):
                return value[key]
    return value


Id = Annotated[str, BeforeValidator(_id_of)]
Text = Annotated[str, Field(min_length=1)]  # stripped first, so a blank string is refused
CLAIM_KINDS = ("gene", "gene_set", "state", "score", "interaction", "custom")


class _Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class RecordFinding(_Contract):
    finding_id: str
    statement: Optional[Text] = None
    run_ids: list[str]
    thread_id: Optional[Id] = None
    target: Optional[str] = None
    targets: Optional[Annotated[list[str], Field(min_length=1, max_length=6)]] = Field(
        None, description="several targets that move the feature the same way: one claim, checked target by target")
    feature: Optional[str] = None
    direction: Optional[Literal["up", "down"]] = None
    kind: Optional[Literal["gene", "gene_set", "state", "score", "interaction", "custom", "description"]] = Field(
        None, description="with a target (or targets), a feature and a direction, the claim's kind, whose checks code "
                          "runs; without them, \"description\", which no check runs on")
    genes: Optional[list[str]] = None
    state_run: Optional[str] = None
    block_call: Optional[str] = None
    population: Optional[str] = Field(None, description="a label for the cells it speaks of; cells restricts its "
                                                        "checks to them")
    cells: Optional[str | dict] = Field(None, description="the cells its checks run on: a kept set's name, or as the "
                                                          "analysis tools take them ({column: value}, {state, run})")
    reference: Optional[Literal["control_guides", "other_targets", "no_guide"]] = None
    effect: Any = None
    figure: Optional[str] = None
    question_id: Optional[str] = None
    prior: Optional[dict] = None
    differs: Optional[dict] = None
    relates: Optional[list[dict]] = None

    @model_validator(mode="after")
    def _a_claim_or_a_description(self):
        """docs/adr/0038, D2: code checks a claim, so a finding gives one or says it is a description; in two
        sessions the agents wrote sentences and the checks ran on 1 finding of 9."""
        if self.target and self.targets:
            raise ValueError("a claim names target (one) or targets (several), not both")
        if self.targets and len(set(self.targets)) < len(self.targets):
            raise ValueError("targets names each target once")
        if (self.target or self.targets) and self.feature and self.direction:
            if self.kind in (None, "description"):
                raise ValueError("a claim (a target or targets, a feature and a direction) names its kind: "
                                 + ", ".join(CLAIM_KINDS))
        elif self.kind != "description" or not self.statement:
            raise ValueError("a finding states a claim that code checks (target, feature, direction and kind) or says "
                             "it is a description (kind: description: a pattern, a count, a technical fact), said in "
                             "a sentence (statement)")
        return self


class ThreadStatus(_Contract):
    thread_id: Id
    status: Literal["open", "followed", "explained", "contradicted", "out of data", "set aside"]
    reason: Text
    finding_ids: list[Id] = Field(default_factory=list, description="explained: the findings that hold the thread's "
                                                                    "result (by default those recorded with its "
                                                                    "thread_id)")
    run_ids: list[str] = Field(default_factory=list)


class NextStep(_Contract):
    """A next step a result offers the user (docs/adr/0050, D5): the question in the agent's words, its move (a kind
    of analysis, in the knowledge base's words) and why; code adds its rank and its references."""
    question: Text
    move: Literal[MOVES] = Field(description="its kind of analysis, as the knowledge base names it")
    why: Text
    recommended: Optional[bool] = None


class ReportResult(_Contract):
    result_id: str
    next_steps: Optional[Annotated[list[NextStep], Field(min_length=1, max_length=3)]] = Field(
        None, description="1 to 3 next steps, in the order you rank them, one of them recommended (the first if none "
                          "says so); code adds what the studies read did after results like it")
    title: Optional[str] = None
    finding_ids: Optional[list[Id]] = None
    figure: Optional[str] = None
    text: Optional[str] = None
    decision: Optional[Literal["confirmed", "dropped"]] = None
    user_words: Optional[str] = None
    reason: Optional[str] = None
    answers: Optional[str] = None
    raised_by: Optional[str] = None
    samples: Optional[str] = None

    @model_validator(mode="after")
    def _one_recommended(self):
        n = sum(bool(s.recommended) for s in self.next_steps or [])
        if n > 1:
            raise ValueError(f"one next step is recommended, not {n}")
        return self


class PriorScreens(_Contract):
    targets: list[str] = Field(min_length=1, max_length=50, description="the targets to look up, by symbol")
    references: Optional[list[Literal["K562_essential", "K562_genome_wide", "RPE1_essential"]]] = Field(
        None, description="the published screens to read (default: all three)")
    neighbours: int = Field(5, ge=1, le=20, description="the nearest perturbations to give for each target")


MODELS = {"record_finding": RecordFinding, "thread_status": ThreadStatus, "report_result": ReportResult,
          "prior_screens": PriorScreens}  # the research lead's review and the advisor's left with them (2026-11-20)


class _WithoutTitles(GenerateJsonSchema):
    def field_title_should_be_set(self, schema):
        return False

    def generate(self, schema, mode="validation"):
        out = super().generate(schema, mode=mode)
        for key in ("title", "description"):  # the tool's own description says what it does
            out.pop(key, None)
        return _inlined(out)


def _inlined(schema):
    """The schema with each $ref replaced by its definition (pydantic keeps them under $defs)."""
    defs = schema.pop("$defs", {})

    def walk(x):
        if isinstance(x, dict):
            ref = x.get("$ref")
            if isinstance(ref, str) and ref.startswith("#/$defs/"):
                d = {k: v for k, v in defs[ref.rsplit("/", 1)[1]].items() if k not in ("title", "description")}
                return walk(d)
            return {k: walk(v) for k, v in x.items()}
        if isinstance(x, list):
            return [walk(v) for v in x]
        return x
    return walk(schema)


def schema(model):
    """A model's JSON schema for a tool's inputSchema: no titles, definitions inlined, an optional value as a type
    array (["string", "null"]) rather than anyOf."""
    return model.model_json_schema(schema_generator=_WithoutTitles, union_format="primitive_type_array")


def checked(tool, args):
    """(the call's arguments as its contract reads them, None), or (None, what is wrong, in words). A tool without a
    contract passes as it came."""
    model = MODELS.get(tool)
    if model is None:
        return args, None
    try:
        return model.model_validate(args).model_dump(exclude_unset=True), None
    except ValidationError as e:
        return None, explain(e, model)


def explain(error, model):
    """A validation error in the words the hand-written errors use: each field, then what it takes."""
    parts = []
    for e in error.errors(include_url=False):
        where = ".".join(str(x) for x in e["loc"]) or "the call"
        if e["type"] == "extra_forbidden":
            parts.append(f"{where}: not an argument of this tool, which takes {', '.join(model.model_fields)}")
        elif e["type"] == "literal_error":
            parts.append(f"{where} is one of {e['ctx']['expected']}")
        else:
            parts.append(f"{where}: {e['msg'].removeprefix('Value error, ')}" if e["loc"] else
                         e["msg"].removeprefix("Value error, "))
    return "bad arguments: " + "; ".join(parts)
