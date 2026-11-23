"""The three queries of the knowledge base's store (docs/build/the_knowledge_base_from_scratch_2026-11-11.md): how
papers did a kind of analysis, what they did after a result of a kind, and how they drew a kind of evidence. Each
answers with one practice and up to three examples (the paper, its panels, its words); each count is of studies, so a
preprint and its paper, or a paper's several panels, count once; papers of the screen's modality, system and
readout come first among the examples. The store is written by the reading plugin (plugin_paper_reading, store.py).

    query("analysis", "enrichment", design={"modality": "CRISPRi", "readout": "transcriptome"})
    query("proceed", "similarity"); query("figure", "similar targets")
"""
import collections
import itertools
import json
import os
import re
import sqlite3

from . import knowledge
from .literature import kept_out

STORE = "knowledge.db"
EXAMPLES = 3
QUOTE_LENGTH = 300  # characters of a quote in a line for the research lead (batch 4: median 221, p90 373)
QUERIES = ("analysis", "proceed", "figure")
# the kinds of analysis the papers' steps are read as (the reading plugin's vocabulary, the analysis tools' names
# first): a next step's move is one of them (docs/adr/0050, D5)
MOVES = ("guides", "impact", "genes", "scores", "states", "programs", "similarity", "conditions", "combinations",
         "enrichment", "reproducibility", "an independent experiment", "outside data", "a phenotype",
         "a model of the screen", "other")
NEEDS_NEW_DATA = ("an independent experiment", "outside data")  # moves the deposit cannot answer: an option of them
# is shown as needing new data and is never the recommended one (the interactive trial of 2026-11-16)
TECHNICAL = ("guides", "reproducibility")  # kinds that check the screen rather than read its biology


def store_path(path=None):
    """The store's file: the one given, else the knowledge base's."""
    return path or os.path.join(knowledge.folder(), STORE)


CARDS_ONLY = "the hand-written cards alone; the paper store (knowledge.db) was not found"


def results(path=None, excluded=None):
    """Allowed results only; a matching paper withholds every version of its study before aggregation."""
    if excluded and (not isinstance(excluded, dict) or set(excluded) - {"pmids", "dois", "title_words"}
                     or any(v is not None and not isinstance(v, (list, tuple, set)) for v in excluded.values())):
        raise ValueError("literature_excluded takes lists of pmids, dois and title_words")
    p = store_path(path)
    if not os.path.exists(p):
        return []
    db = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
    try:
        blocked = []
        title = "COALESCE(title, '')" if "title" in {r[1] for r in db.execute("PRAGMA table_info(papers)")} else "''"
        if excluded:
            for work, doi, pmid, name in db.execute(f"SELECT work, doi, pmid, {title} FROM papers"):
                if (excluded.get("title_words") and not name) or kept_out(
                        {"doi": doi, "pmid": pmid, "title": name}, excluded):
                    blocked.append(work)
        blocked = sorted(set(blocked))
        where = " WHERE p.work NOT IN (" + ",".join("?" for _ in blocked) + ")" if blocked else ""
        rows = db.execute("SELECT r.paper, p.work, p.label, p.modality, p.system, p.readout, r.step, r.question, r.raised_by, "
                          "r.result, r.result_quote, r.checks_reported, r.evidence, p.doi, p.pmid, r.notes, "
                          f"{title.replace('title', 'p.title')} FROM results r "
                          "JOIN papers p ON p.id = r.paper" + where + " ORDER BY p.label, r.step", blocked).fetchall()
    finally:
        db.close()
    names = ("paper", "work", "label", "modality", "system", "readout", "step", "question", "raised_by", "result", "quote",
             "checks", "evidence", "doi", "pmid", "notes", "title")
    out = []
    for row in rows:
        x = dict(zip(names, row))
        x.update(raised_by=json.loads(x["raised_by"]), checks=json.loads(x["checks"]),
                 evidence=json.loads(x["evidence"]))
        out.append(x)
    return out


def label(value):
    """A listed value, any other (...) as other."""
    return "other" if re.match(r"other\b", str(value).strip()) else str(value).strip()


def counted(pairs):
    """[(value, studies)] of (value, study) pairs, the most studies first."""
    by = collections.defaultdict(set)
    for value, work in pairs:
        by[value].add(work)
    return sorted(((v, len(w)) for v, w in by.items()), key=lambda vw: (-vw[1], vw[0]))


def matches(x, design):
    """How far a paper's design is the screen's: its modality, its system and its readout, one each."""
    return sum(bool((design or {}).get(f)) and x.get(f) == design[f] for f in ("modality", "system", "readout"))


def examples(found, design, k=EXAMPLES):
    """Up to k (result, evidence, previous results), one a study, with their source and stated comparison."""
    seen, out = set(), []
    for x, items, previous in sorted(found, key=lambda f: (_closest_first(design)(f[0]), f[0]["step"])):
        if x["work"] in seen:
            continue
        seen.add(x["work"])
        example = {"paper": x["label"], "step": x["step"], "question": x["question"], "result": x["result"],
                   "quote": x["quote"], "evidence": items}
        if x["doi"] or x["pmid"]:
            example["source"] = (f"https://doi.org/{x['doi']}" if x["doi"] else
                                 f"https://pubmed.ncbi.nlm.nih.gov/{x['pmid']}/")
        if previous:
            example["previous"] = [{"step": p["step"], "result": p["result"], "quote": p["quote"],
                                    "panels": [e["panels"] for e in p["evidence"]]} for p in previous]
        if x["notes"]:
            example["notes"] = x["notes"]
        out.append(example)
        if len(out) == k:
            break
    return out


def kinds_of(item):
    return [label(k) for k in (item["analysis"] if isinstance(item["analysis"], list) else [item["analysis"]])]


def top(pairs, n=3):
    return ", ".join(f"{v} ({k})" for v, k in pairs[:n])


def raising(rows):
    """Each result's previous steps, those its raised_by names, so an example read alone keeps what raised it."""
    by = {(x["paper"], x["step"]): x for x in rows}
    return lambda x: [by[(x["paper"], s)] for s in x["raised_by"] if (x["paper"], s) in by]


def analysis(kind, design=None, path=None, excluded=None):
    """How papers did a kind of analysis: the units they counted in and the checks they reported, with the methods
    of up to three studies in their words, each example with the results that raised it."""
    rows = results(path, excluded)
    previous = raising(rows)
    found = [(x, [item for item in x["evidence"] if kind in kinds_of(item)], previous(x)) for x in rows
             if any(kind in kinds_of(item) for item in x["evidence"])]
    studies = len({x["work"] for x, _, _ in found})
    units = counted((label(item.get("unit", "")), x["work"]) for x, items, _ in found for item in items)
    checks = counted((label(c), x["work"]) for x, _, _ in found for c in x["checks"])
    practice = (f"{studies} studies did {kind}: units reported: {top(units)}; checks reported for these results: "
                f"{top(checks) or 'none reported'}" if studies else f"no matching study in the knowledge base for {kind}")
    return {"query": "analysis", "asked": kind, "studies": studies, "practice": practice,
            "counts": {"unit": units, "checks_reported": checks}, "examples": examples(found, design)}


def proceed(kind, design=None, path=None, excluded=None):
    """What papers did after a result of a kind (an analysis or an evidence): the kinds of analysis of the results
    whose raised_by the paper states names such a result, with up to three of them."""
    rows = results(path, excluded)
    of_kind = {(x["paper"], x["step"]): x for x in rows
               if any(kind in kinds_of(item) or label(item.get("evidence", "")) == kind for item in x["evidence"])}
    found = [(x, x["evidence"], [of_kind[(x["paper"], s)] for s in x["raised_by"] if (x["paper"], s) in of_kind])
             for x in rows
             if any((x["paper"], s) in of_kind for s in x["raised_by"])]
    after = counted((k, x["work"]) for x, _, _ in found for k in {k for item in x["evidence"] for k in kinds_of(item)})
    studies = len({x["work"] for x, _, _ in found})
    practice = (f"after a result of {kind} ({studies} studies), papers went on to: {top(after)}" if after else
                f"no study in the knowledge base states what a result of {kind} raised")
    return {"query": "proceed", "asked": kind, "studies": studies, "practice": practice,
            "counts": {"analysis": after}, "examples": examples(found, design)}


def after_results(kinds, design=None, k=EXAMPLES, path=None, excluded=None):
    """What papers did after a result of these kinds, for the research lead at a result's next question: up to k
    papers (proceed's examples over the kinds in turn: each kind's first, then each kind's second, one paper once),
    each a line in the paper's words, with no reader's sentence: its panels, the result that raised the step (its quote), the step's kinds of analysis, what it
    compared and its result (its quote). On 01f3de3 the lead was shown each paper's steps as kinds alone and never
    named one."""
    def cut(q):  # a quote's start, verbatim: one reached 700 characters
        q = str(q or "")
        return q if len(q) <= QUOTE_LENGTH else q[:QUOTE_LENGTH - 3] + "..."

    lines, seen = [], set()
    per_kind = [proceed(kind, design, path, excluded)["examples"] for kind in dict.fromkeys(kinds)]
    for ex in (ex for turn in itertools.zip_longest(*per_kind) for ex in turn if ex):
        if ex["paper"] not in seen:
            seen.add(ex["paper"])
            items = ex["evidence"]
            did = ", ".join(dict.fromkeys(kd for i in items for kd in kinds_of(i))) or "other"
            against = "; ".join(dict.fromkeys(str(i.get("compared_with")) for i in items if i.get("compared_with")))
            raised = cut((ex.get("previous") or [{}])[0].get("quote"))
            lines.append(f"{ex['paper']} {', '.join(i['panels'] for i in items if i.get('panels'))}: after "
                         f"\"{raised}\", it went on to {did}" + (f", against {against}" if against else "")
                         + f": \"{cut(ex['quote'])}\"")
            if len(lines) == k:
                return lines
    return lines


def went_on_to(kinds, move=None, design=None, path=None, excluded=None, k=EXAMPLES):
    """Of the studies with a result of these kinds whose next step the paper states (m), how many went on to a step of
    the kind move (n), with up to k of those steps as references, one a study, each resolving to its row
    (paper:step); without move, every kind they went on to, with its count. For the user's references at a result's
    next steps (docs/adr/0050, D5). Each count is of studies, so a preprint and its paper count once."""
    rows = results(path, excluded)
    kinds = set(kinds or [])
    of_kind = {(x["paper"], x["step"]) for x in rows
               if any(kinds & set(kinds_of(item)) or label(item.get("evidence", "")) in kinds for item in x["evidence"])}
    after = [x for x in rows if any((x["paper"], s) in of_kind for s in x["raised_by"])]
    out = {"m": len({x["work"] for x in after}), "after": sorted(kinds)}
    if move is None:
        return {**out, "moves": counted((kd, x["work"]) for x in after
                                        for kd in {kd for item in x["evidence"] for kd in kinds_of(item)})}
    did = [x for x in after if any(move in kinds_of(item) for item in x["evidence"])]
    references, seen = [], set()
    for x in sorted(did, key=lambda x: (_closest_first(design)(x), x["step"])):
        if x["work"] in seen:
            continue
        seen.add(x["work"])
        references.append({"row": f"{x['paper']}:{x['step']}", "paper": x["label"], "step": x["step"],
                           "panels": ", ".join(str(i["panels"]) for i in x["evidence"] if i.get("panels")),
                           **({"source": f"https://doi.org/{x['doi']}" if x["doi"] else
                               f"https://pubmed.ncbi.nlm.nih.gov/{x['pmid']}/"} if x["doi"] or x["pmid"] else {})})
        if len(references) == k:
            break
    return {**out, "n": len({x["work"] for x in did}), "move": move, "references": references}


def figure(kind, design=None, path=None, excluded=None):
    """Charts for an evidence or analysis kind; a result's several kinds still give at most three examples, each with
    the results that raised it."""
    wanted = {kind} if isinstance(kind, str) else set(kind)

    def fits(item):
        return label(item.get("evidence", "")) in wanted or bool(wanted.intersection(kinds_of(item)))

    rows = results(path, excluded)
    previous = raising(rows)
    found = [(x, [item for item in x["evidence"] if fits(item)], previous(x)) for x in rows
             if any(fits(item) for item in x["evidence"])]
    studies = len({x["work"] for x, _, _ in found})
    charts = counted((label(item.get("chart", "")), x["work"]) for x, items, _ in found for item in items)
    named = counted((item.get("colour_named_by", ""), x["work"]) for x, items, _ in found for item in items
                    if item.get("colour_named_by") != "not applicable")
    shown = counted((item.get("targets_shown", ""), x["work"]) for x, items, _ in found for item in items)
    asked = kind if isinstance(kind, str) else ", ".join(kind)
    practice = (f"{studies} studies drew {asked}: as {top(charts)}; their colours named by {top(named) or '-'}"
                if studies else f"no matching study in the knowledge base for {asked}")
    return {"query": "figure", "asked": kind, "studies": studies, "practice": practice,
            "counts": {"chart": charts, "colour_named_by": named, "targets_shown": shown},
            "examples": examples(found, design)}


def _year(paper):
    """A paper's year, from its label ("PMID37730998 Santinha 2023"), 0 if it names none."""
    m = re.search(r"(\d{4})\s*$", str(paper["label"]))
    return int(m.group(1)) if m else 0


def _closest_first(design):
    """Papers ordered by the design fields they share with the screen, then the newest."""
    return lambda x: (-matches(x, design), -_year(x), x["label"])


def checks_reported(kind, design=None, k=3, path=None, excluded=None):
    """How papers checked a result of a kind: one line a paper, the closest design first, then the newest: the panels
    of its first such result, what it was compared with, its unit, its method and the checks the paper reported."""
    first = {}
    for x in results(path, excluded):
        items = [i for i in x["evidence"] if kind in kinds_of(i)]
        if items and x["work"] not in first:
            first[x["work"]] = (x, items[0])
    out = []
    for x, i in sorted(first.values(), key=lambda xi: _closest_first(design)(xi[0]))[:k]:
        out.append(f"{x['label']} {i.get('panels', '')}: {i.get('compared_with', '')}; unit {i.get('unit', '')}; "
                   f"{i.get('method', '')}; checks reported: {', '.join(label(c) for c in x['checks']) or 'none'}")
    return out


def figure_forms(kinds, design=None, k=4, path=None, excluded=None):
    """The charts papers drew a result of these kinds as, the commonest first, each with one example (the closest
    design, then the newest): its paper and panels, its colour and what names it, and the targets shown."""
    wanted, by = set(kinds), collections.defaultdict(dict)
    for x in results(path, excluded):
        for i in x["evidence"]:
            if (wanted & set(kinds_of(i)) or label(i.get("evidence", "")) in wanted) and                     label(i.get("chart", "")) not in ("", "not stated"):
                by[label(i.get("chart", ""))].setdefault(x["work"], (x, i))
    out = []
    for chart, works in sorted(by.items(), key=lambda cw: (-len(cw[1]), cw[0]))[:k]:
        x, i = min(works.values(), key=lambda xi: _closest_first(design)(xi[0]))
        named = i.get("colour_named_by", "")
        out.append(f"{chart}: {x['label']} {i.get('panels', '')}, {i.get('colour', '')}"
                   + (f", named by {named}" if named and named != "not applicable" else "")
                   + f"; {i.get('targets_shown', '')}")
    return out


def query(name, kind, design=None, path=None, excluded=None):
    """One local lookup; a figure set may supply several kinds internally. Unavailable differs from no match."""
    if name not in QUERIES:
        raise ValueError(f"query: one of {', '.join(QUERIES)}")
    if name == "figure" and isinstance(kind, (list, tuple)) and kind and all(
            isinstance(k, str) and k.strip() for k in kind):
        kind = [k.strip() for k in kind]
    elif isinstance(kind, str) and kind.strip():
        kind = kind.strip()
    else:
        raise ValueError("kind: the analysis kind or kind of evidence to look up")
    empty = {"query": name, "asked": kind, "status": "unavailable", "studies": 0, "counts": {}, "examples": []}
    if not os.path.exists(store_path(path)):
        return {**empty, "practice": "the paper knowledge base is not installed; no papers were queried"}
    try:
        out = {"analysis": analysis, "proceed": proceed, "figure": figure}[name](kind, design, path, excluded)
    except (OSError, sqlite3.Error, ValueError) as e:
        return {**empty, "practice": f"the paper knowledge base could not be read: {e}"}
    return {**out, "status": "found" if out["examples"] else "not found"}
