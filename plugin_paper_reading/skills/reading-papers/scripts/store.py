"""The knowledge base's store (docs/build/the_knowledge_base_from_scratch_2026-11-11.md): one SQLite file with two
tables, papers and results. A paper's reading is one file, rows/<key>/results.json, written by the results reader:
the paper's design and its results, one per claim, each with the list of its kinds of evidence. Code checks every
result before any is written: its quote in the Results, its listed fields in their lists, its panels in their legends,
its images in the paper's list, its links to earlier steps, its methods' reported locations, and no source kept
out. The paper's accepted results then replace its earlier ones in one transaction; the earlier ones go to a dated
export with how they were read.

    python store.py write <library> <key> --model <model>    the paper's results checked and written
    python store.py show <library> <key>                     the paper's rows in the store
"""
import argparse
import datetime
import hashlib
import json
import os
import re
import sqlite3
import sys
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import papers  # noqa: E402

STORE = "knowledge.db"  # in the library; a release copies it into the knowledge base's folder
EXPORTS = "exports"  # in the library: each paper's earlier rows, dated, with how they were read
READING = "results.json"  # in rows/<key>/
BRIEF = "results-reader.md"

# the analysis service's own kinds of analysis (pertura.kinds.KINDS), and four it has no tool for
ANALYSIS = ("guides", "impact", "genes", "scores", "states", "programs", "similarity", "conditions", "combinations",
            "enrichment", "reproducibility", "an independent experiment", "outside data", "a model of the screen",
            "a phenotype", "other")
CHECKS = ("guides agree", "replicates or donors agree", "against control guides", "depth or cell numbers matched",
          "a second readout", "an independent experiment", "other")
UNITS = ("cells", "guides", "targets", "genes", "samples, animals or donors", "pairs", "not stated", "other")
NOT_STATED = "not stated"
RESULT_FIELDS = ("step", "question", "raised_by", "result", "result_quote", "checks_reported", "evidence")
EVIDENCE_FIELDS = ("panels", "evidence", "analysis", "method", "method_place", "compared_with", "unit", "chart",
                   "colour", "colour_named_by", "targets_shown", "image")
LISTED = {"evidence": papers.FIGURE_EVIDENCE, "chart": papers.FIGURE_CHARTS, "colour": papers.FIGURE_COLOURS,
          "colour_named_by": papers.FIGURE_COLOUR_NAMED_BY, "targets_shown": papers.FIGURE_TARGETS, "unit": UNITS}
DESIGN = ("modality", "system", "readout")  # of papers.DESIGN's features, those a query filters by
RESULT_WORDS = (8, 120)  # allow a long source sentence without paraphrasing or truncating it

SCHEMA = """
CREATE TABLE IF NOT EXISTS papers (
    id TEXT PRIMARY KEY, work TEXT NOT NULL, label TEXT NOT NULL, doi TEXT, pmid TEXT, pmcid TEXT,
    modality TEXT, system TEXT, readout TEXT, read_with TEXT NOT NULL, title TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS results (
    paper TEXT NOT NULL REFERENCES papers(id), step INTEGER NOT NULL, question TEXT NOT NULL,
    raised_by TEXT NOT NULL, result TEXT NOT NULL, result_quote TEXT NOT NULL, checks_reported TEXT NOT NULL,
    evidence TEXT NOT NULL, notes TEXT NOT NULL, PRIMARY KEY (paper, step));
"""


def connect(path):
    db = sqlite3.connect(path)
    db.executescript(SCHEMA)
    if "title" not in {r[1] for r in db.execute("PRAGMA table_info(papers)")}:
        db.execute("ALTER TABLE papers ADD COLUMN title TEXT NOT NULL DEFAULT ''")
    return db


def listed(value, values):
    return papers.label_of(str(value)) in values


def text_headings(lib, key, section):
    """The headings of a supplied cut text, as the source-location check compares them."""
    path = os.path.join(lib, "texts", key, section + ".txt")
    text = papers.read_text(path) if os.path.exists(path) else ""
    return [papers.normal(m.group(1)) for m in re.finditer(r"(?m)^#+ (.+)$", text)]


def place_fails(place, headings, legends, results_headings=()):
    """Check a supplied Methods/Results heading or figure reference; supplementary notes remain as stated."""
    p = papers.normal(place or "")
    if not p:
        return ["no method_place"]
    if p == NOT_STATED or p.startswith(("supplement", "extended data", "star methods")):
        return []
    if p.startswith("results:"):
        named = p.split(":", 1)[1].strip()
        if any(named and (named in h or h in named) for h in results_headings if h):
            return []
        return [f"method_place {place!r} is not a heading of the paper's Results"]
    panels = papers.panel_tokens(place)
    if panels:
        valid = all((k, n) in legends and (letter == "*" or not legends[k, n]["letters"] or
                                         letter in legends[k, n]["letters"]) for k, n, letter in panels)
        return [] if valid else [f"method_place {place!r} is not in the supplied figure legends"]
    named = p.split(":", 1)[-1].strip() if p.startswith(("methods", "method")) else p
    if any(named and (named in h or h in named) for h in headings if h):
        return []
    return [f"method_place {place!r} is not a heading of the paper's Methods"]


def check_evidence(item, legs, images, pdf):
    """Why one item of a result's evidence list fails: its fields, its lists, its panels in their legends, its
    image."""
    fails = [f"evidence item has no {f}" for f in EVIDENCE_FIELDS if f not in item]
    if fails:
        return fails
    for f, values in LISTED.items():
        if not listed(item[f], values):
            fails.append(f"{f.replace('_', ' ')} {item[f]!r} is not one of the list")
    kinds = item["analysis"] if isinstance(item["analysis"], list) else [item["analysis"]]
    if not kinds or not all(listed(k, ANALYSIS) for k in kinds):
        fails.append(f"analysis {item['analysis']!r} is not one or more of the list")
    if (papers.label_of(item["colour"]) == "one colour") != (item["colour_named_by"] == "not applicable"):
        fails.append("one colour is named nowhere, so its colour_named_by is `not applicable`, and only then")
    tokens = papers.panel_tokens(item["panels"])
    if not tokens:
        fails.append(f"panels {item['panels']!r} name no figure")
    for kind, n, letter in sorted(tokens):
        fig = (kind, n)
        if fig not in legs:
            fails.append(f"no legend of {papers.figure_name(fig)} in the text")
        elif letter != "*" and legs[fig]["letters"] and letter not in legs[fig]["letters"]:
            fails.append(f"panel {letter} is not in the legend of {papers.figure_name(fig)}")
    main = sorted(fig for fig in {(k, n) for k, n, _ in tokens} if fig[0] == "main")
    fails += papers.image_fails(item["image"], main[0] if main else None, images, pdf)
    return fails


def check_results(lib, key, reading):
    """Check claims and evidence separately. A claim needs its source quote and at least one valid evidence item;
    rejected secondary evidence is recorded in notes. Links to rejected claims are removed and also noted."""
    text, legs = papers.source_text(lib, key), papers.paper_legends(lib, key)
    images, pdf = papers.paper_images(lib, key), papers.paper_pdf(lib, key)
    headings, kept = text_headings(lib, key, "methods"), papers.KeptOut.of(lib)
    results_headings = text_headings(lib, key, "results")
    files = text.split(papers.FILE_BREAK)
    at = files[0].find(papers.LEGENDS_HEADING)
    said = files[0][:at] if at >= 0 else files[0]  # the Results text, without the legends
    accepted, failed = {}, []
    for r in reading.get("results", []):
        fails = [f"no {f}" for f in RESULT_FIELDS if f not in r]
        valid_items, bad_items = [], []
        if not fails:
            step = r["step"]
            if not isinstance(step, int) or step < 1:
                fails.append(f"step {step!r} is not a number from 1")
            fails += (papers.quote_fails("result_quote", r["result_quote"], said, RESULT_WORDS)
                      if isinstance(r["result_quote"], str) else ["result_quote must be text"])
            checks = r["checks_reported"]
            if not isinstance(checks, list) or not all(listed(c, CHECKS) for c in checks):
                fails.append(f"checks_reported {checks!r} are not values of the list")
            if not isinstance(r["raised_by"], list) or not all(isinstance(s, int) for s in r["raised_by"]):
                fails.append(f"raised_by {r['raised_by']!r} is not a list of steps")
            elif any(s >= step for s in r["raised_by"]):
                fails.append("raised_by names a step that is not earlier")
            items = r["evidence"] if isinstance(r["evidence"], list) else []
            if not items:
                fails.append("no evidence")
            for i, item in enumerate(items, 1):
                if not isinstance(item, dict):
                    bad_items.append(f"evidence item {i} is not a set of fields")
                    continue
                why = check_evidence(item, legs, images, pdf)
                if "method_place" in item:
                    why += place_fails(item["method_place"], headings, legs, results_headings)
                if why:
                    bad_items += [f"evidence {i}: {message}" for message in why]
                else:
                    valid_items.append(item)
            cited = kept.cited_in(json.dumps(r, ensure_ascii=False))
            if cited:
                fails.append(f"cites sources kept out: {', '.join(cited)}")
            if step in accepted:
                fails.append(f"step {step} is given twice")
            if fails or not valid_items:
                fails += bad_items
        if fails:
            failed.append((r.get("step"), fails))
        else:
            accepted[r["step"]] = dict(r, evidence=valid_items,
                                       notes="excluded " + "; ".join(bad_items) if bad_items else "")
    for step, r in accepted.items():
        lost = [s for s in r["raised_by"] if s not in accepted]
        if lost:
            r["raised_by"] = [s for s in r["raised_by"] if s in accepted]
            r["notes"] = "; ".join(filter(None, [r["notes"],
                f"raised by step {', '.join(map(str, lost))} too, which failed its checks"]))
    return [accepted[s] for s in sorted(accepted)], failed


def design_fails(design):
    return [f"design {f} {design.get(f)!r} is not one of the list" for f in DESIGN
            if not listed(design.get(f, ""), papers.DESIGN[f])]


def read_with(lib, key, model, brief_path):
    """How a paper was read: the brief's sha256, the model, the sha256 of the text and of the images, the date."""
    images = os.path.join(lib, "sources", key, papers.FIGURE_IMAGES)
    return {"brief_sha256": papers.file_sha256(brief_path), "model": model,
            "text_sha256": hashlib.sha256(papers.source_text(lib, key).encode("utf-8")).hexdigest(),
            "images_sha256": papers.file_sha256(images) if os.path.exists(images) else "none",
            "date": datetime.date.today().isoformat()}


def paper_title(lib, key):
    """The source title supports the workspace's existing title-based exclusions."""
    path = os.path.join(lib, "sources", key, "fulltext.xml")
    if os.path.exists(path):
        try:
            title = ET.parse(path).find(".//article-title")
            if title is not None:
                return " ".join(" ".join(title.itertext()).split())
        except (OSError, ET.ParseError):
            pass
    return ""


def load_reading(lib, key):
    """Keep every raw revision before checking it, including an incomplete or malformed reading."""
    meta = papers.paper(papers.papers(lib), key)
    if meta["state"] == "kept out":
        raise papers.LibraryError(f"{key} is kept out: {meta['why']}")
    path = os.path.join(lib, "rows", key, READING)
    if not os.path.exists(path):
        raise papers.LibraryError(f"no reading at {path}")
    with open(path, "rb") as fh:
        raw = fh.read()
    snapshot = os.path.join(lib, "rows", key, "attempts", hashlib.sha256(raw).hexdigest() + ".json")
    os.makedirs(os.path.dirname(snapshot), exist_ok=True)
    try:
        with open(snapshot, "xb") as fh:
            fh.write(raw)
    except FileExistsError:
        pass  # checking the same revision again keeps the original snapshot
    try:
        reading = json.loads(raw)
    except (ValueError, UnicodeError) as e:
        raise papers.LibraryError(f"invalid reading JSON: {e}") from e
    if not isinstance(reading, dict) or not isinstance(reading.get("results"), list) or not all(
            isinstance(r, dict) for r in reading["results"]):
        raise papers.LibraryError("reading must be an object with a results list of objects")
    if reading.get("paper") != meta["label"]:
        raise papers.LibraryError(f"the reading is of {reading.get('paper')!r}, not {meta['label']!r}")
    return meta, reading, snapshot


def check_paper(lib, key):
    """Structured feedback for the orchestrator's bounded reader retries; no database write."""
    _, reading, snapshot = load_reading(lib, key)
    accepted, failed = check_results(lib, key, reading)
    whole = design_fails(reading.get("design") or {})
    if not reading["results"]:
        whole.append("no results were written; finish reading the paper")
    warnings = [{"step": r["step"], "notes": r["notes"]} for r in accepted if r["notes"]]
    return {"paper": key, "status": "retry" if whole or failed or warnings else "ready",
            "snapshot": os.path.relpath(snapshot, lib), "results": len(reading["results"]),
            "accepted_results": len(accepted), "accepted_evidence": sum(len(r["evidence"]) for r in accepted),
            "paper_issues": whole, "issues": [{"step": s, "errors": why} for s, why in failed],
            "warnings": warnings}


def write_paper(lib, key, model, work=None, brief_path=None, db_path=None):
    """One paper's reading checked and written to the store: its accepted results replace its earlier ones in one
    transaction, the earlier ones exported first with how they were read. Returns the lines to show."""
    r, reading, _ = load_reading(lib, key)
    design = reading.get("design") or {}
    whole = design_fails(design)
    accepted, failed = check_results(lib, key, reading)
    out = [f"{key} ({r['label']}): {len(accepted)} of {len(accepted) + len(failed)} results pass their checks"]
    out += [f"  step {s} fails: {why}" for s, fails in failed for why in fails]
    out += [f"  step {x['step']}: {x['notes']}" for x in accepted if x["notes"]]
    if whole or not accepted:
        return out + [f"  not written: {'; '.join(whole) or 'no result passes'}"]
    how = read_with(lib, key, model, brief_path or os.path.join(papers.PLUGIN_FOLDER, "agents", BRIEF))
    db = connect(db_path or os.path.join(lib, STORE))
    try:
        before = db.execute("SELECT * FROM results WHERE paper = ?", (key,)).fetchall()
        if before:  # the earlier rows, with how they were read, kept before they are replaced
            names = [d[0] for d in db.execute("SELECT * FROM results LIMIT 0").description]
            old = db.execute("SELECT read_with FROM papers WHERE id = ?", (key,)).fetchone()
            dest = os.path.join(lib, EXPORTS, f"{key}.{datetime.datetime.now():%Y%m%dT%H%M%S}.json")
            papers.write_text(dest, json.dumps({"read_with": json.loads(old[0]) if old else None,
                                                "results": [dict(zip(names, row)) for row in before]},
                                               indent=1, ensure_ascii=False) + "\n")
            out.append(f"  its earlier {len(before)} results exported to {os.path.relpath(dest, lib)}")
        with db:  # one transaction: all of the paper's rows, or none
            db.execute("DELETE FROM results WHERE paper = ?", (key,))
            db.execute("INSERT OR REPLACE INTO papers "
                       "(id, work, label, doi, pmid, pmcid, modality, system, readout, read_with, title) "
                       "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                       (key, (work or r["doi"] or key).lower(), r["label"], r["doi"], r["pmid"], r["pmcid"],
                        design.get("modality"), design.get("system"), design.get("readout"), json.dumps(how),
                        paper_title(lib, key)))
            db.executemany("INSERT INTO results VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                           [(key, x["step"], x["question"], json.dumps(x["raised_by"]), x["result"],
                             x["result_quote"], json.dumps(x["checks_reported"]),
                             json.dumps(x["evidence"], ensure_ascii=False), x["notes"]) for x in accepted])
    finally:
        db.close()
    return out + [f"  written: {len(accepted)} results, read by {model}"]


def show(lib, key, db_path=None):
    db = connect(db_path or os.path.join(lib, STORE))
    try:
        rows = db.execute("SELECT step, question, raised_by, result, evidence FROM results WHERE paper = ? "
                          "ORDER BY step", (key,)).fetchall()
    finally:
        db.close()
    out = []
    for step, question, raised_by, result, evidence in rows:
        items = json.loads(evidence)
        out.append(f"{step}. {question} (raised by {', '.join(map(str, json.loads(raised_by))) or 'none stated'}): "
                   f"{result}")
        out += [f"   {x['panels']}: {x['evidence']}; {', '.join(x['analysis'])}; {x['chart']}" for x in items]
    return out or [f"{key}: no results in the store"]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="command", required=True)
    a = sub.add_parser("write")
    a.add_argument("library")
    a.add_argument("key")
    a.add_argument("--model", required=True)
    a.add_argument("--work", help="the study's id where its preprint and its paper are both read")
    a = sub.add_parser("show")
    a.add_argument("library")
    a.add_argument("key")
    a = sub.add_parser("check", help="archive the raw reading and return JSON feedback, without writing the store")
    a.add_argument("library")
    a.add_argument("key")
    args = ap.parse_args(argv)
    try:
        if args.command == "check":
            report = check_paper(args.library, args.key)
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 0 if report["status"] == "ready" else 1
        out = (write_paper(args.library, args.key, args.model, args.work) if args.command == "write" else
               show(args.library, args.key))
    except papers.LibraryError as e:
        if args.command == "check":
            print(json.dumps({"paper": args.key, "status": "retry", "paper_issues": [str(e)]},
                             ensure_ascii=False, indent=2))
            return 1
        print(f"error: {e}", file=sys.stderr)
        return 1
    print("\n".join(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
