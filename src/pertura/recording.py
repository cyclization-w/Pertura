"""The record kept by code and cheap readers (docs/adr/0051): what a recording holds, and how the analysis service
applies it.

No agent writes the record. After each turn, the plugin's recorder (plugin/status/recorder.py) asks a small model
for a Recording of the turn (`claude -p --json-schema`, with RECORDING_SCHEMA): the claims the agents stated, the
results, the user's decisions, the threads the lead or a worker named, their reviews, and the doubts' answers. The
skeptic, as a reader, returns Doubts. Both go to the project's record/inbox, and the service applies each file before
its next tool call, through its own methods, so every record rule and the claims' checks hold, and one process writes
the record. What a method refuses is kept with its reason and named in the reply."""
import glob
import json
import os
import re
from typing import Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from . import record as rc
from .knowledge_store import MOVES

RECORDER = "recorder"  # the actor of what the recorder applies (record events are checked as the agent's)


class _Item(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Feature(_Item):
    kind: Literal["gene", "gene_set", "score", "state"] = Field(
        description="gene: one gene's expression; gene_set: a named gene set's score; score: a per-cell score the "
                    "notebook saved; state: a cell state's share")
    name: str = Field(description="the gene, the gene set's name, the saved score's name, or the state's name")
    labelling: Optional[str] = Field(None, description="for a state: the run ID of the states run that labelled it")
    genes: Optional[list[str]] = Field(None, description="for a gene_set the agent defined: its genes, as the turn's "
                                                         "text or code lists them")


class Claim(_Item):
    statement: str = Field(description="the claim in the agent's own words, with its numbers")
    targets: list[str] = Field(min_length=1, description="the perturbed targets the claim is about")
    feature: Feature
    direction: Literal["up", "down"] = Field(description="the feature higher (up) or lower (down) in the targets' "
                                                         "cells than in the controls")
    run_ids: list[str] = Field(default_factory=list, description="the run IDs the claim rests on, as the tool "
                                                                 "replies name them (run0083-e96c5f)")
    figure: Optional[str] = Field(None, description="the figure ID it is shown in, where the agent named one")
    replaces: Optional[str] = Field(None, description="a recorded finding's ID, where the agent corrected it")
    cells: Optional[dict[str, Union[str, list[str]]]] = Field(
        None, description="the cells the claim holds in, as {column: value or values} with RECORD's cell_columns "
                          "(for example one sample or condition); none where it holds in all cells")
    population: Optional[str] = Field(None, description="the cells the claim holds in, in the agent's words, where "
                                                        "it names them (one model, one state, one condition)")
    reference: Optional[Literal["control_guides", "other_targets"]] = Field(
        None, description="what the targets' cells were compared against, where the agent's words say: the control "
                          "guides' cells, or every other target's cells")


class NextStep(_Item):
    question: str
    move: Literal[MOVES]
    why: str
    recommended: Optional[bool] = None


class Result(_Item):
    result_id: Optional[str] = Field(None, description="the result's ID where the agent revises one already "
                                                       "proposed (r1); none for a new result")
    title: str = Field(description="its conclusion, in one sentence")
    text: str = Field(description="the agent's paragraph for it")
    claims: list[int] = Field(default_factory=list, description="positions (from 0) of this recording's claims "
                                                                "it rests on")
    finding_ids: list[str] = Field(default_factory=list, description="IDs of findings already recorded it rests on")
    figure: Optional[str] = None
    next_steps: list[NextStep] = Field(default_factory=list, max_length=3)
    raised_by: Optional[str] = Field(None, description="the earlier result (r1) or finding whose reading raised the "
                                                       "question this result answers, as the agent's 'From <id>:' line "
                                                       "or its words name it; none for a question of the user's")
    answers: Optional[str] = Field(None, description="the question it answers, as that line or the agent's words "
                                                     "give it")


class UserDecision(_Item):
    kind: Literal["keep", "drop", "next_step", "enough", "knowledge", "doubt", "deepen", "reread"]
    words: str = Field(description="the user's words, exactly as they wrote them")
    message_uuid: str = Field(description="the uuid of the user's message that holds the words")
    refers_to: Optional[str] = Field(None, description="the result or finding ID, or the next step's rank")


class Thread(_Item):
    about: str = Field(description="what it would test, in a sentence with its numbers")
    raised_by: Literal["a worker", "the user", "the main agent"]
    scope: Optional[Literal["one question", "the whole screen"]] = Field(
        None, description="for the user's question: what they hand over, one question or the whole screen")


class ThreadUpdate(_Item):
    thread_id: str
    status: Literal[rc.THREAD_STATUSES[1:]] = Field(description="followed: the agent takes it; the others end it")
    reason: str


class DoubtAnswer(_Item):
    doubt_id: str
    outcome: Literal[rc.DOUBT_OUTCOMES]
    reason: str
    run_ids: list[str] = Field(default_factory=list)


class Recording(_Item):
    """One turn's recording: only what the text states. An empty list where the turn holds none."""
    claims: list[Claim] = Field(default_factory=list)
    results: list[Result] = Field(default_factory=list)
    user_decisions: list[UserDecision] = Field(default_factory=list)
    threads: list[Thread] = Field(default_factory=list)
    thread_updates: list[ThreadUpdate] = Field(default_factory=list)
    doubt_answers: list[DoubtAnswer] = Field(default_factory=list)


class Doubt(_Item):
    finding_id: str = Field(description="the recorded finding it questions")
    text: str = Field(description="what may be wrong, in a sentence")
    would_change: str = Field(description="the result that would change the judgement")


class Doubts(_Item):
    doubts: list[Doubt] = Field(default_factory=list, description="at most 3, each one that would change how the "
                                                                   "result reads, the weightiest first")
    the_question_lacks: str = Field(default="", description="in one sentence, what the user's question still lacks "
                                                            "after this result; empty if nothing")


MAX_REVIEW_DOUBTS = 3  # a review's doubts are information, the weightiest few (2026-11-18)


RECORDING_SCHEMA, DOUBTS_SCHEMA = Recording.model_json_schema(), Doubts.model_json_schema()
RAISED_BY = {"a worker": "a worker", "the user": "the user", "the main agent": "the main agent"}
RETIRED_FIELDS = ("lead_reviews", "figure_reviews")  # a recording's fields for the retired roles (2026-11-20): an
# earlier recorder still sends them, empty, and they are dropped before the recording is read
RESULT_DECISIONS = {"keep": "confirmed", "drop": "dropped"}


def _range_key(rng):
    return ":".join(str((rng or {}).get(k) or "") for k in ("session", "agent", "first", "last"))


SAVE_TABLE = re.compile(r"save_table\(\s*['\"]([A-Za-z0-9._-]+)['\"]")  # pt.save_table('<name>', ...) in a run's code
DESCRIBED = ("feature", "direction", "kind", "genes", "state_run", "block_call")  # what a description leaves out
_LABELS = {}  # a labelling table's labels, by its path and time


def _labels(path):
    """A labelling table's labels (its column beside the barcode), and the names its run gave them."""
    key = (path, os.path.getmtime(path))
    if key not in _LABELS:
        import pandas as pd
        from .finding_checks import state_names
        t = pd.read_csv(path, sep="\t", dtype=str)
        col = next((c for c in t.columns if c != "barcode"), None)
        _LABELS[key] = (set(t[col].dropna().unique()) if col else set()) | set(state_names(path).values())
    return _LABELS[key]


def _labelling_for(svc, st, state, run_ids):
    """The labelling a state claim reads, by code: the one among its runs that holds the state it names, else the
    project's only labelling where that holds. Two candidates leave it to the agent, since its checks would run on
    the wrong states. (The runs its runs read are not searched: notebook reads are seldom traced, 15% in d2's count.)"""
    def holding(runs):
        out = []
        for r in runs:
            path = svc._run_labelling(r)
            try:
                if path and state in _labels(path):
                    out.append(r)
            except (OSError, ValueError, KeyError):
                continue
        return out
    found = holding(run_ids)
    if found:
        return found[0] if len(found) == 1 else None
    every = [r for r in st.get("runs") or {} if svc._run_labelling(r)]
    return every[0] if len(every) == 1 and holding(every) else None


def _score_table_for(svc, st, name, run_ids):
    """The saved table a score claim reads, by code: the one named like its feature among those its runs saved
    (pt.save_table in their code) or in the notebooks, else the one table its runs saved."""
    if str(name).startswith("table:"):
        return name
    plain = re.sub(r"[^a-z0-9]", "", str(name).lower())
    saved = list(dict.fromkeys(n for r in run_ids for n in SAVE_TABLE.findall(str((st["runs"].get(r) or {})
                                                                                  .get("code") or ""))))
    named = [n for n in saved if re.sub(r"[^a-z0-9]", "", n.lower()) == plain]
    if not named and re.fullmatch(r"[A-Za-z0-9._-]+", str(name)) and glob.glob(
            os.path.join(svc.project, "notebooks", "*", "tables", f"{name}.tsv.gz")):
        named = [name]
    pick = named[0] if named else saved[0] if len(saved) == 1 else None
    return f"table:{pick}" if pick else None


_LIBRARY = {}  # MSigDB collections read for claims, by (collection, organism)
LIBRARY_PREFIXES = {"HALLMARK_": "hallmark", "REACTOME_": "reactome", "GOBP_": "go_bp", "GOCC_": "go_cc",
                    "GOMF_": "go_mf", "WP_": "wikipathways", "KEGG_": "kegg", "BIOCARTA_": "canonical_pathways",
                    "PID_": "canonical_pathways"}


def _plain(name):
    return re.sub(r"[^a-z0-9]", "", str(name).lower())


def _library_set(svc, name):
    """The genes of an MSigDB set named in full (HALLMARK_INTERFERON_ALPHA_RESPONSE) for the confirmed organism, and
    the collection; None where the name is no such set, the organism is not confirmed or the sets are not here."""
    key = re.sub(r"[\s-]+", "_", str(name).strip()).upper()
    collection = next((c for p, c in LIBRARY_PREFIXES.items() if key.startswith(p)), None)
    if collection is None:
        return None
    try:
        organism = svc._organism()
        if (collection, organism) not in _LIBRARY:
            from . import gene_sets as gs
            _LIBRARY[(collection, organism)] = gs.msigdb(collection, organism)
        genes = _LIBRARY[(collection, organism)].get(key)
    except Exception:  # no confirmed organism, or no gene sets on this machine: the claim says what it needs
        return None
    return (list(genes), f"MSigDB {collection} ({organism}): {key}") if genes else None


def _gene_lists(st, run_ids, name):
    """The gene lists of that name (its letters and digits, any case) the given module runs were given (C.programs
    with gene_sets given): [(run, list name, genes)]."""
    from .analysis_service import _module_and_choices
    out = []
    for r in run_ids:
        given = _module_and_choices(st["runs"].get(r) or {})[1]
        for k, v in ((given.get("inputs") or {}).get("gene_lists") or {}).items():
            if isinstance(v, (list, tuple)) and v and _plain(k) == _plain(name):
                out.append((r, str(k), [str(g) for g in v]))
    return out


def _genes_for(svc, st, name, run_ids):
    """The genes of a gene-set claim the recorder gave without them, by code, and where they come from: the list of
    that name its own runs were given, else the record's only list of that name (another module run gave it), else
    the MSigDB set of that full name, or of the full name its G.meaning run tested under its short name, for the
    confirmed organism. None where none is found, and the claim stays a
    description with what it needs. (The graded trial on GSE263761 at 0e8bcbb kept its 12 claims, all on
    HALLMARK_INTERFERON_ALPHA_RESPONSE, without checks; of 63 such claims in the runs of 2026-11-08 to 19, these
    rules give 28 their genes.)"""
    own = _gene_lists(st, run_ids or [], name)
    if own:
        return own[0][2], f"run {own[0][0]}'s gene list {own[0][1]}"
    every = _gene_lists(st, list(st.get("runs") or {}), name)
    if every and len({json.dumps(g) for _, _, g in every}) == 1:
        return every[0][2], f"run {every[0][0]}'s gene list {every[0][1]}, the record's only list of that name"
    return _library_set(svc, _set_tested(svc, st, name, run_ids) or name)


def _bare(name):
    """A set's name without its collection prefix or a trailing '(collection)', as letters and digits in lower case."""
    key = re.sub(r"[\s-]+", "_", re.sub(r"\s*\([^)]*\)\s*$", "", str(name)).strip()).upper()
    prefix = next((p for p in LIBRARY_PREFIXES if key.startswith(p)), "")
    return _plain(key[len(prefix):])


def _set_tested(svc, st, name, run_ids):
    """The full name of the set a G.meaning run among the claim's runs tested, for a claim that names it in short
    ('Oxidative phosphorylation (hallmark)'): the set of its result.tsv whose bare name is the claim's. None unless
    exactly one is."""
    import pandas as pd
    from .analysis_service import _module_and_choices
    want, found = _bare(name), set()
    for r in run_ids or []:
        run = st["runs"].get(r) or {}
        if _module_and_choices(run)[0] != "G.meaning":
            continue
        try:
            sets = pd.read_csv(os.path.join(svc._jobs_root(), str(run.get("job_id") or r), "result.tsv"), sep="\t",
                               usecols=["set"])["set"].astype(str)
        except (OSError, ValueError):  # no table, or one without a 'set' column
            continue
        found |= {s for s in sets if _bare(s) == want}
    return found.pop() if len(found) == 1 else None


def _comparison_for(st, targets, run_ids):
    """The comparison a claim's runs made on its targets, by code, for a claim whose words name neither its cells nor
    its reference: each run of a module that compares against a reference (a reference choice) or was given cells,
    and each pt call of its runs on its targets. Returns (comparison, why it waits): one comparison found, its cells,
    reference and the run or call it comes from, with the call (block_call) where the call holds every target, so
    its test and labelling come from it too; none found, (None, None), the default (every kept cell against the
    control guides); several, (None, the reason its checks wait). In the runs of 2026-11-08 to 19, 28 of 848 claims
    were checked on every cell against the control guides while their run compared one state's cells, a cell set,
    or every other target's cells."""
    from . import question_modules as qm
    from .analysis_service import _module_and_choices
    found = {}
    for r in run_ids or []:
        module, given = _module_and_choices(st["runs"].get(r) or {})
        contract = qm.REGISTRY[module].contract if module in qm.REGISTRY else None
        if contract is None or not ("reference" in contract.choices or given.get("cells")):
            continue
        cells = given.get("cells") or None
        ref = (given.get("choices") or {}).get("reference") or "control_guides"
        found.setdefault((json.dumps(cells, sort_keys=True), ref), {"cells": cells, "reference": ref,
                                                                    "block_call": None, "from": r})
    for cid, call in (st.get("block_calls") or {}).items():
        on = set(call.get("targets") or [])
        if call.get("run_id") not in (run_ids or []) or call.get("check_of") or not set(targets) & on:
            continue
        cells = None if call.get("cells") in (None, "every cell") else call.get("cells")
        ref = call.get("reference") or "control_guides"
        found[(json.dumps(cells, sort_keys=True), ref)] = {
            "cells": cells, "reference": ref, "block_call": cid if set(targets) <= on else None, "from": cid}
    if len(found) > 1:
        return None, ("its runs compared " + "; ".join(f"{v['from']} {v['cells'] or 'every kept cell'} against "
                                                       f"{v['reference']}" for v in found.values())
                      + ", and its words name neither cells nor reference")
    return (next(iter(found.values())) if found else None), None


def _how_to_check(fid, c):
    """What a claim kept as a description needs for its checks to run, as the fixed line code reads."""
    line = (f"Claim {fid}: targets {', '.join(c.targets)}; feature {c.feature.kind} {c.feature.name}; "
            f"direction {c.direction}")
    return {"score": f"a per-cell score is checked: save its table (pt.save_table(name, table), one row per cell with "
                     f"its barcode and its score), then write: {line}; table <name>. An activity read from a genes "
                     f"run (a factor's or a pathway's) has no per-cell score and stays a description",
            "state": f"name the labelling it reads: {line}; labelling <the states run's ID>",
            "gene_set": f"name its genes: {line}; genes <gene, gene, ...>"}.get(c.feature.kind)


def _step(st):
    """The next step the user picked last, while the main agent works on it, until another result is first proposed:
    (its thread, the result it follows); else None."""
    picks = [x for x in st.get("replies") or [] if x.get("kind") == "next_step" and x.get("thread_id")]
    if not picks:
        return None
    last = max(picks, key=lambda x: x.get("seq") or 0)
    for rid, r in (st.get("results") or {}).items():
        if rid != last.get("result_id") and ((r.get("versions") or [r])[0].get("seq") or 0) > (last.get("seq") or 0):
            return None
    return last["thread_id"], last.get("result_id")


def _thread_of(st, run_ids, step=None):
    """A claim's thread, by code: the one its runs name (their thread_id, else a worker notebook's name
    <thread>-<what>), else the step the main agent works on."""
    threads = st.get("threads") or {}
    found = set()
    for r in run_ids or []:
        x = st["runs"].get(r) or {}
        t = x.get("thread_id") or next((t for t in threads if str(x.get("worker") or "").startswith(f"{t}-")), None)
        if t in threads:
            found.add(t)
    if len(found) == 1:
        return found.pop()
    return step[0] if step else None


def _latest_run(st, finding_ids):
    runs = [r for f in finding_ids for r in (st["findings"].get(f) or {}).get("run_ids") or [] if r in st["runs"]]
    return max(runs, key=lambda r: st["runs"][r].get("seq") or 0) if runs else None


def _confirmed(svc):
    try:
        svc._confirmed()
        return True
    except Exception:  # no design yet, or not confirmed
        return False


def _runs_named(known, names):
    """The record's runs a recording names: each as given, or the one run whose ID begins with it ("run0047" for
    "run0047-3fa9c1"); a name no run, or several, answers to is dropped and returned apart."""
    ids, dropped = [], []
    for n in (str(x).strip() for x in names or []):
        hits = [n] if n in known else [k for k in known if k.startswith(n)] if n else []
        (ids if len(hits) == 1 else dropped).append(hits[0] if len(hits) == 1 else n)
    return list(dict.fromkeys(ids)), dropped


FIGURE_REFUSED = ("a result's figure is drawn from the runs", "no figure or run", "give the one this result shows")
SHORT_DOUBT = re.compile(r"^[Dd](\d+)$")
DOUBT_RANGE = re.compile(r"^[Dd]?(\d+)\s*[-–]\s*[Dd]?(\d+)$")


def _doubts_named(doubts, name):
    """The doubts an answer names: the ID itself, a short ID ("d7" for D7), or a range ("D5-D8": D5, D6, D7, D8)."""
    name = str(name).strip()
    if name in doubts:
        return [name]
    m = SHORT_DOUBT.match(name)
    if m and f"D{int(m.group(1))}" in doubts:
        return [f"D{int(m.group(1))}"]
    m = DOUBT_RANGE.match(name)
    if m and int(m.group(1)) <= int(m.group(2)):
        return [f"D{k}" for k in range(int(m.group(1)), int(m.group(2)) + 1) if f"D{k}" in doubts] or [name]
    return [name]


def apply(svc, data, tag):
    """Apply one inbox file (a Recording, or the skeptic's Doubts) through the service's methods. tag names the file
    and prefixes the IDs given here. The recorder's code adds the transcript range the file records (range) and the
    user's messages in it (user_messages, {uuid: text}): a range applied before is refused whole, and a user's
    decision whose words are not in the message it names is refused (the user decides each result and the end).
    Returns what was applied and what was refused, with its reason."""
    applied, refused, notes = [], [], []
    data = dict(data) if isinstance(data, dict) else data
    rng = data.pop("range", None) if isinstance(data, dict) else None
    users = (data.pop("user_messages", None) or {}) if isinstance(data, dict) else {}
    turn_runs = [str(r) for r in (data.pop("turn_runs", None) or [])] if isinstance(data, dict) else []
    links = list(data.pop("from_lines", None) or []) if isinstance(data, dict) else []  # 'From <id>:' lines, by code
    for field in RETIRED_FIELDS if isinstance(data, dict) else ():
        data.pop(field, None)
    if isinstance(data, dict):  # an older recording's parts of the question, which left the loop (2026-11-21)
        for key in ("question_parts", "part_lines"):
            data.pop(key, None)
    choice_lines = list(data.pop("choice_lines", None) or []) if isinstance(data, dict) else []  # 'Choice: ...'
    no_choice = list(data.pop("no_choice_lines", None) or []) if isinstance(data, dict) else []  # 'No choice: ...'
    known = set((svc.record.state().get("runs") or {}))
    turn_runs = [r for r in turn_runs if r in known]  # the turn's runs the record holds
    main = str((rng or {}).get("agent") or "main") == "main"
    seen_path = os.path.join(svc.project, "record", "recorded_ranges.json")  # beside the inbox, never read as a file of it
    if rng:
        import json
        try:
            with open(seen_path, encoding="utf-8") as fh:
                seen = set(json.load(fh))
        except (OSError, ValueError):
            seen = set()
        key = _range_key(rng) + (":doubts" if isinstance(data, dict) and "doubts" in data else "")
        if key in seen:
            return {"applied": [], "refused": [{"what": "the whole file", "why": f"range {key} applied before"}]}
        seen.add(key)
        with open(seen_path + ".tmp", "w", encoding="utf-8") as fh:
            json.dump(sorted(seen), fh)
        os.replace(seen_path + ".tmp", seen_path)

    def one(what, fn, **kw):
        try:
            out = fn(**kw)
            applied.append({"what": what, **({"id": out.get("finding_id") or out.get("result_id") or
                                               out.get("thread_id") or out.get("doubt_id")}
                                              if isinstance(out, dict) else {})})
        except Exception as e:  # each method's own rule refuses it: kept with the reason, never dropped silently
            refused.append({"what": what, "why": str(e)[:300]})

    try:
        if isinstance(data, dict) and "doubts" in data:
            cited = [str(x) for x in data.pop("cited", None) or []]  # the claims the review read, from its code
            data.pop("received", None)  # the counts of what the review was given, kept in the file for the runs' reads
            doubts = Doubts.model_validate(data)
            kept = doubts.doubts[:MAX_REVIEW_DOUBTS]
            if len(doubts.doubts) > len(kept):
                notes.append({"what": "the review", "doubts_dropped": len(doubts.doubts) - len(kept),
                              "why": f"a review raises at most {MAX_REVIEW_DOUBTS} doubts"})
            ids = []
            for d in kept:
                did = svc.new_doubt_id()  # short and run-wide, as the agent answers it (D7)
                before = len(applied)
                one(f"doubt on {d.finding_id}", svc.raise_doubt, doubt_id=did, text=d.text,
                    would_change=d.would_change, finding_id=d.finding_id, opened_by="pertura:skeptic")
                if len(applied) > before:
                    ids.append(did)
            checked = sorted(({d.finding_id for d in kept} | set(cited)) & set(svc.record.state()["findings"]))
            if checked:
                one("the skeptic's check", svc.record_skeptic_check, finding_ids=checked, doubt_ids=ids,
                    question_lacks=doubts.the_question_lacks.strip() or None)
            return {"applied": applied, "refused": refused, **({"notes": notes} if notes else {})}
        rec = Recording.model_validate(data)
    except ValidationError as e:
        return {"applied": [], "refused": [{"what": "the whole file", "why": str(e)[:500]}]}
    st = svc.record.state()
    step = _step(st) if main else None  # the next step the user picked, while the main agent works on it
    claim_ids, counts = [], {"found": 0, "with_checks": 0, "described": 0, "refused": 0}
    for i, c in enumerate(rec.claims, 1):
        counts["found"] += 1
        run_ids, unknown = _runs_named(known, c.run_ids)
        if unknown:
            notes.append({"what": f"claim {i}", "runs_dropped": unknown, "why": "no run of the record, or several, "
                                                                               "answers to these names"})
        if not c.replaces and not run_ids:
            if not turn_runs:
                refused.append({"what": f"claim {i}", "why": "a claim names the runs it rests on"})
                claim_ids.append(None)
                counts["refused"] += 1
                continue
            run_ids = turn_runs  # its words named none the record holds: the runs of the turn that stated it
        feature, state_run = c.feature.name, (c.feature.labelling if c.feature.kind == "state" else None)
        if c.feature.kind == "score":  # filled by code from its runs (revision 3)
            feature = _score_table_for(svc, st, feature, run_ids) or feature
        if c.feature.kind == "state" and not state_run:
            state_run = _labelling_for(svc, st, feature, run_ids)
        genes, by_code = c.feature.genes if c.feature.kind == "gene_set" else None, {}
        if c.feature.kind == "gene_set" and not genes:
            found = _genes_for(svc, st, feature, run_ids)
            if found:
                genes, by_code["genes_from"] = found
        cells, reference, block_call, waits = c.cells or None, c.reference, None, None
        if cells is None and reference is None:  # the comparison its runs made, where its words name none
            taken, waits = _comparison_for(st, c.targets, run_ids)
            if taken:
                cells, reference, block_call = taken["cells"], taken["reference"], taken["block_call"]
                by_code["comparison_from"] = taken["from"]
        kw = dict(statement=c.statement, target=c.targets[0], targets=c.targets if len(c.targets) > 1 else None,
                  feature=feature, direction=c.direction, kind=c.feature.kind, run_ids=run_ids or None, figure=c.figure,
                  state_run=state_run, genes=genes, cells=cells, reference=reference, block_call=block_call)
        fid = c.replaces or f"{tag}-c{i}"
        hint = _how_to_check(fid, c)
        if c.replaces:
            try:
                if waits:  # its runs compared several ways: no comparison is chosen for it
                    raise ValueError(waits)
                svc.reword_finding(finding_id=fid, reason="the agent corrected the recorded claim in its reply", **kw)
                applied.append({"what": f"correction of {fid}", "id": fid})
                counts["with_checks"] += 1
            except Exception as e:  # the finding stays as it was; the reply says what the correction needs
                refused.append({"what": f"correction of {fid}", "why": str(e)[:300],
                                **({"to_check_it": hint} if hint else {})})
                counts["refused"] += 1
            claim_ids.append(fid)
            continue
        kw.update(population=c.population, thread_id=_thread_of(st, run_ids, step))
        try:
            if waits:  # its runs compared several ways: its checks would choose one for it
                raise ValueError(waits)
            svc.record_finding(finding_id=fid, **kw)
            applied.append({"what": f"claim {fid}", "id": fid, **by_code})
            counts["with_checks"] += 1
        except Exception as e:  # its checks cannot start: kept as a description, with what it needs (revision 3)
            try:
                svc.record_finding(finding_id=fid, **{k: v for k, v in kw.items() if k not in DESCRIBED})
                applied.append({"what": f"claim {fid}", "id": fid, "checks": "not started: " + str(e)[:300],
                                **({"to_check_it": hint} if hint else {})})
                counts["described"] += 1
            except Exception as e2:
                refused.append({"what": f"claim {fid}", "why": f"{str(e)[:200]}; as a description: {str(e2)[:200]}"})
                counts["refused"] += 1
                fid = None
        claim_ids.append(fid)
    st = svc.record.state()
    for k, r in enumerate(rec.results):
        rid = r.result_id or f"r{len(st.get('results') or {}) + 1}"
        fids = [claim_ids[j] for j in r.claims if 0 <= j < len(claim_ids) and claim_ids[j]] + list(r.finding_ids)
        raised, answers = r.raised_by, r.answers
        line = links[k] if len(links) == len(rec.results) else links[0] if len(rec.results) == 1 and links else None
        if line:  # the agent's 'From <id>:' line, read by code, over the model's reading
            raised, answers = line.get("raised_by"), line.get("answers") or answers
        if not raised and step and not r.result_id:  # the first result after the user's pick follows its result
            raised = step[1]
            answers = answers or ((st.get("threads") or {}).get(step[0]) or {}).get("about")
        note = {}
        if raised and (raised == rid or raised not in set(st.get("results") or {}) | set(st.get("findings") or {})):
            note["link_dropped"] = f"{raised} is no earlier result or finding"
            raised = None
        if rid in (st.get("results") or {}):  # a new version keeps its findings until a doubt overturns one (problem 2)
            overturned = {d.get("finding_id") for d in st["doubts"].values()
                          if (d.get("answer") or {}).get("outcome") == "overturns"}
            fids = list(dict.fromkeys([f for f in st["results"][rid].get("finding_ids") or [] if f not in overturned]
                                      + fids))
            now = st["results"][rid]
            if now.get("decision") in RESULT_DECISIONS.values() and set(fids) == set(now.get("finding_ids") or []):
                # restated on the same findings: the user's decision stands (effa573: r2 went back to proposed, its
                # paragraph shortened from 147 to 37 words)
                notes.append({"what": f"result {rid}", "why": f"{now['decision']} already; restated on the same "
                                                              "findings, it stays as decided"})
                continue
        kw = dict(result_id=rid, title=r.title, text=r.text, finding_ids=fids, figure=r.figure, raised_by=raised,
                  answers=answers, next_steps=[s.model_dump(exclude_none=True) for s in r.next_steps] or None)

        def attempt(**k):
            try:
                svc.report_result(**k)
                return None
            except Exception as e:  # its rule's reason, for the next attempt or the refusal
                return str(e)

        why = attempt(**kw)
        if why and kw["figure"] and any(s in why for s in FIGURE_REFUSED):  # the figure rule refused the one named:
            note["figure"] = f"{kw['figure']} was not taken ({why[:160]}); code took its findings' latest figure"
            why = attempt(**{**kw, "figure": None})  # code takes its findings' latest figure (problem 3)
        if why and "has its figure" in why and _latest_run(st, fids) is not None:
            run = _latest_run(st, fids)
            why = attempt(**{**kw, "figure": run})  # its latest run stands for its figure until one is saved
            note["figure"] = (f"none saved yet: save one from its findings' runs (pt.save_figure), and the result "
                              f"takes it; until then run {run} stands for it")
        if why:
            refused.append({"what": f"result {rid}", "why": why[:300]})
        else:
            applied.append({"what": f"result {rid}", "id": rid, **note})
        if not r.result_id:
            step = None  # the step ends with the first result proposed after it
        st = svc.record.state()
    designed = _confirmed(svc) if rec.user_decisions else True
    for u in rec.user_decisions:
        if not designed:  # before the design is confirmed, the user's messages answer the design (revision 3)
            refused.append({"what": f"the user's {u.kind}", "why": "a design answer: the design is not confirmed yet"})
            continue
        ref = u.refers_to
        said = str(users.get(u.message_uuid) or "")
        if not u.words.strip() or u.words.strip() not in said:
            refused.append({"what": f"the user's {u.kind}", "why": "its words are not in the user's message "
                                                                  f"{u.message_uuid}"})
            continue
        if u.kind in RESULT_DECISIONS and ref in (st.get("results") or {}):
            one(f"the user's decision on {ref}", svc.report_result, result_id=ref,
                decision=RESULT_DECISIONS[u.kind], user_words=u.words)
        elif u.kind == "next_step":
            one("the user's next step", svc.note_reply, kind="next_step", words=u.words,
                option=int(ref) if ref and str(ref).isdigit() else None)
        elif u.kind in ("enough", "knowledge"):
            one(f"the user's {u.kind}", svc.note_reply, kind=u.kind, words=u.words)
        else:
            one(f"the user's {u.kind} on {ref}", svc.note_reply, kind=u.kind, words=u.words, finding_id=ref)
    for t in rec.threads:
        user = t.raised_by == "the user"  # the user's question, with what they hand over (the survey's scope)
        words = t.about if user else None
        if user and not any(t.about.strip() and t.about.strip() in str(m) for m in users.values()):
            if not (t.scope == "the whole screen" and users and not svc.record.state().get("asked")):
                refused.append({"what": "the user's question", "why": "its words are not in a message of the user's"})
                continue
            # the screen handed over with no question to quote: the messages that hand it over are its words
            # (effa573: "Please analyse it" was refused, so the survey never ran and the review had no question)
            words = "\n".join(str(m) for m in users.values())
        one("a thread", svc.open_thread, about=t.about, raised_by=RAISED_BY[t.raised_by],
            user_words=words, scope=t.scope if user else None)
    for tu in rec.thread_updates:
        one(f"thread {tu.thread_id} {tu.status}", svc.thread_status, thread_id=tu.thread_id, status=tu.status,
            reason=tu.reason)
    doubts = svc.record.state().get("doubts") or {}
    for a in rec.doubt_answers:
        runs, _ = _runs_named(known, a.run_ids)
        for did in _doubts_named(doubts, a.doubt_id):
            one(f"the answer to {did}", svc.answer_doubt, doubt_id=did, outcome=a.outcome, reason=a.reason,
                run_ids=runs or None, limit="design" if a.outcome == rc.LIMIT else None)
            if a.outcome in NARROWING:  # the answer or report follows the finding
                notes.append({"what": f"the answer to {did}", "do": f"update what you wrote that rests on "
                                                                    f"{doubts[did].get('finding_id')}: the answer or "
                                                                    "report"})
    for c in choice_lines if main else []:
        _choice_thread(svc, c, one, refused, notes)
    for c in no_choice if main else []:
        one("the line No choice", svc.record.append, kind="no_choice_said",
            payload={"reason": str((c or {}).get("reason") or "").strip()}, actor=rc.AGENT)
    return {"applied": applied, "refused": refused, "claims": counts, **({"notes": notes} if notes else {})}


def _choice_thread(svc, c, one, refused, notes):
    """A choice the main agent made that could change a reading (its 'Choice:' line) opens a thread a worker takes,
    which tries the other value (docs/adr/0032, D9). The same choice declared again opens none."""
    tries = {k: str(c.get(k) or "").strip() for k in ("what", "value", "other")}
    if not all(tries.values()):
        refused.append({"what": f"the choice {tries['what'] or '(unnamed)'}", "why": "a choice names what it is, its "
                                                                                    "value and the other"})
        return
    if any(th.get("tries") == tries for th in (svc.record.state().get("threads") or {}).values()):
        notes.append({"what": f"the choice {tries['what']}", "why": "its thread is open already"})
        return
    one(f"the choice {tries['what']}", svc.open_thread, raised_by="the main agent", tries=tries,
        about=f"{tries['what']}: {tries['other']} instead of {tries['value']}")


def run_outputs(project, run_ids, code_chars=3000, output_chars=4000):
    """What the skeptic reads of each run a claim rests on: what it did, its code and its output, cut short."""
    st = rc.Record(os.path.join(project, "record")).state()
    out = {}
    for rid in _runs_named(set(st.get("runs") or {}), run_ids)[0]:  # a run named in short, as apply reads it
        r = (st.get("runs") or {}).get(rid)
        if r:
            out[rid] = {"what": r.get("what"), "code": str(r.get("code") or "")[:code_chars],
                        "output": str(r.get("output") or "")[:output_chars]}
    return out


def reader_facts(project, doubt_chars=240):
    """What the reviewer reads beside a result's claims, all facts and no choice: the user's words, the doubts the
    record already holds (each with its answer, so none is raised again), and the screen's targets the user's words
    name (whole words, as written) that no result cites yet."""
    st = rc.Record(os.path.join(project, "record")).state()
    words = [str(q.get("text") or "") for q in st.get("asked") or []]
    words += [str(t.get("about") or "") for t in (st.get("threads") or {}).values() if t.get("raised_by") == "the user"]
    words = list(dict.fromkeys(w for w in words if w.strip()))
    try:
        import pandas as pd
        g = pd.read_csv(os.path.join(project, "design", "screen_object", "guides.tsv"), sep="\t")
        targets = sorted(set(g.loc[g["kind"] == "targeting", "target"].astype(str)))
    except (OSError, ValueError, KeyError):
        targets = []
    said = "\n".join(words)
    named = [t for t in targets if re.search(rf"(?<![A-Za-z0-9_.-]){re.escape(t)}(?![A-Za-z0-9_])", said)]
    fs = st.get("findings") or {}
    cited = {t for r in rc.results_shown(st) for f in r["finding_ids"]
             for t in (fs.get(f) or {}).get("targets") or [(fs.get(f) or {}).get("target")] if t}
    return {"the_users_words": words,
            "doubts_the_record_holds": [{"text": str(d.get("text") or "")[:doubt_chars],
                                         "answer": (d.get("answer") or {}).get("outcome")}
                                        for d in (st.get("doubts") or {}).values()],
            "targets_the_user_named_with_no_result_yet": [t for t in named if t not in cited]}


def checks_of(project, finding_ids, reason_chars=300):
    """What the skeptic reads of each cited claim's checks, as code ran them: {finding: {check: {outcome, reason,
    run_id}}}, an empty dict for a claim recorded with none yet, and none for one not in the record yet. The run's
    words give each target's numbers."""
    st = rc.Record(os.path.join(project, "record")).state()
    out = {}
    for fid in finding_ids:
        f = (st.get("findings") or {}).get(fid)
        out[fid] = None if f is None else {k: {"outcome": c.get("outcome"),
                                               "reason": str(c.get("reason") or "")[:reason_chars],
                                               "run_id": c.get("run_id")}
                                           for k, c in (f.get("checks") or {}).items()}
    return out


def doubt_open(d):
    """Whether a doubt is still open: no answer yet, or its last answer says it is still open (no test closes it
    that way; a test, or a limit stated, does)."""
    return not d.get("answer") or (d.get("answer") or {}).get("outcome") == "still open"


NARROWING = (rc.NARROWED, "weakens", "overturns")  # answers after which what rests on the finding is rewritten


def answers_to_update(st):
    """The doubts answered narrowed, weakens or overturns since the agent last wrote a file or the report's opening
    (a notebook's file, or story_written): what it wrote may rest on the finding as it was."""
    wrote = max([o.get("seq") or 0 for o in (st.get("objects") or {}).values()
                 if o.get("kind") == "file" and o.get("run_id")] + [(st.get("story") or {}).get("seq") or 0])
    return [{"doubt_id": did, "finding_id": d.get("finding_id"), "outcome": d["answer"]["outcome"]}
            for did, d in (st.get("doubts") or {}).items()
            if (d.get("answer") or {}).get("outcome") in NARROWING and (d["answer"].get("seq") or 0) > wrote]


def stop_facts(record_dir, jobs_root=None):
    """What is still open in a record, for the Stop rule: the findings whose checks are queued or running, the doubts
    with no answer, the parts of the user's question still open, the declared choices whose thread has not ended (set
    aside with its reason ends it), and the claims kept as descriptions, with why and what would give them checks. It
    only reads: a checks job's end is read from its folder under the jobs root (default <project>/design/jobs, the
    analysis service's), never logged."""
    from . import runner
    if not os.path.isfile(os.path.join(record_dir, "events.db")):  # never a new record for a wrong path
        raise FileNotFoundError(f"no record at {record_dir}")
    project = os.path.dirname(os.path.abspath(record_dir))
    root = jobs_root or os.path.join(project, "design", "jobs")
    st = rc.Record(record_dir).state()
    started = {}  # a started spec's path -> its run
    for r in (st.get("runs") or {}).values():
        inputs = r.get("inputs") or []
        if str(r.get("what") or "").startswith("checks of finding ") and len(inputs) > 1:
            spec = inputs[1].get("path") if isinstance(inputs[1], dict) else inputs[1]
            started[os.path.abspath(str(spec))] = r
    running = []
    for path in sorted(glob.glob(os.path.join(project, "checks", "*.json"))):
        fid = os.path.basename(path)[:-len(".json")].rsplit("-", 1)[0]
        r = started.get(os.path.abspath(path))
        if r is None:  # queued: no job yet
            busy = True
        elif r.get("state") != "running" or not r.get("job_id"):
            busy = False
        else:
            try:
                busy = runner.status(r["job_id"], root)["state"] in runner.ACTIVE
            except KeyError:  # its folder is not under this root: running, as the record says
                busy = True
        if busy and fid not in running:
            running.append(fid)
    hints = {}  # a claim kept as a description: why, and the line that would give it checks
    for path in glob.glob(os.path.join(record_dir, "inbox", "applied", "*.outcome.json")):
        try:
            with open(path, encoding="utf-8") as fh:
                outcome = json.load(fh)
        except (OSError, ValueError):
            continue
        for a in outcome.get("applied") or []:
            if isinstance(a, dict) and a.get("id") and str(a.get("checks") or "").startswith("not started"):
                hints[a["id"]] = {"why": str(a["checks"])[len("not started: "):],
                                  **({"to_check_it": a["to_check_it"]} if a.get("to_check_it") else {})}
    findings = st.get("findings") or {}
    applying = glob.glob(os.path.join(record_dir, "inbox", "applying", "*.json"))
    return {"inbox": len(inbox_files(project)) + len(applying),  # claims the record has not taken yet
            "checks_running": running,
            "doubts_open": [did for did, d in (st.get("doubts") or {}).items() if doubt_open(d)],
            "answers_to_update": answers_to_update(st),
            "choices_open": [{"thread_id": tid, "what": th["tries"].get("what")}
                             for tid, th in (st.get("threads") or {}).items()
                             if isinstance(th.get("tries"), dict) and th.get("status") not in rc.THREAD_ENDS],
            "described": [{"finding_id": fid, **hints[fid]} for fid in sorted(hints)
                          if fid in findings and not findings[fid].get("feature")],
            "findings": len(findings),
            # a Choice line opened a thread that tries the other value, or a No choice line gave its reason
            "choice_said": any(isinstance(th.get("tries"), dict) for th in (st.get("threads") or {}).values())
                           or bool(st.get("no_choice"))}


def inbox_files(project):
    """The inbox's files not yet applied, in the order they came, the skeptic's after its own turn's file (its name,
    <tag>-z-doubts.json, sorts before <tag>.json, and its doubts were refused with no finding to hold them)."""
    folder = os.path.join(project, "record", "inbox")
    if not os.path.isdir(folder):
        return []
    doubts = "-z-doubts.json"
    names = [n for n in os.listdir(folder) if n.endswith(".json")]
    return [os.path.join(folder, n) for n in sorted(names, key=lambda n: (n[:-len(doubts)], 1) if n.endswith(doubts)
                                                    else (n[:-len(".json")], 0))]


if __name__ == "__main__":  # python -m pertura.recording run-outputs <project> <run_id>...  |  schemas <folder>
    import json
    import sys
    from . import limits
    limits.limit_this_process(1)  # the recorder's readings on the server: niced and pinned, as every process of ours
    if sys.argv[1:2] == ["run-outputs"]:
        print(json.dumps(run_outputs(sys.argv[2], sys.argv[3:]), default=str))
    elif sys.argv[1:2] == ["checks"]:  # python -m pertura.recording checks <project> <finding id>...
        print(json.dumps(checks_of(sys.argv[2], sys.argv[3:]), default=str))
    elif sys.argv[1:2] == ["facts"]:  # python -m pertura.recording facts <project>
        print(json.dumps(reader_facts(sys.argv[2]), default=str))
    elif sys.argv[1:2] == ["stop-facts"]:  # python -m pertura.recording stop-facts <record_dir> [jobs root]
        print(json.dumps(stop_facts(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else None), default=str))
    elif sys.argv[1:2] == ["schemas"]:
        for name, schema in (("recording_schema.json", RECORDING_SCHEMA), ("doubts_schema.json", DOUBTS_SCHEMA)):
            with open(os.path.join(sys.argv[2], name), "w", encoding="utf-8", newline="\n") as fh:
                json.dump(schema, fh, indent=1)
                fh.write("\n")
